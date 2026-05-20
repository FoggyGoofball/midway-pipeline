"""
_helpers_exec.py — Base Layer & LLM Logic for the mesh consensus pipeline.
Contains: globals/config, intent classification, GDD librarian,
project state, director prompt, task execution.

No async/await — purely synchronous.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from token_budget import TokenBudget


# ── Configuration re-exports (set by pipeline.py at import time) ───────────

PROJECT_ROOT: Path = Path(os.getenv("MIDWAY_PROJECT_ROOT", Path(__file__).resolve().parent.with_name("midway")))
MAX_ITERATIONS = 3
MAX_CONSENSUS_ITERATIONS = 3
MAX_SUBTASKS_PER_AGENT = 5
REVIEW_MAX_ITERATIONS = 3
SCOPE_FILE_LIMIT = 5
SCOPE_LINE_LIMIT = 400
_ALL_DOMAINS = {}  # populated by pipeline.py init


def _init_config(project_root: Path, all_domains: dict, **kwargs):
    """Initialize configuration from pipeline.py module-level constants."""
    global PROJECT_ROOT, _ALL_DOMAINS, MAX_ITERATIONS, MAX_CONSENSUS_ITERATIONS
    global MAX_SUBTASKS_PER_AGENT, REVIEW_MAX_ITERATIONS, SCOPE_FILE_LIMIT, SCOPE_LINE_LIMIT
    PROJECT_ROOT = project_root
    _ALL_DOMAINS = all_domains
    for k, v in kwargs.items():
        if k in globals():
            globals()[k] = v


# ── Intent Classification ──────────────────────────────────────────────────

from _prompts import INTENT_CLASSIFIER_SYSTEM as _INTENT_CLASSIFIER_SYSTEM_BASE
# Use the canonical definition from _prompts; keep the local name for callers.
INTENT_CLASSIFIER_SYSTEM = _INTENT_CLASSIFIER_SYSTEM_BASE


def classify_intent(user_prompt: str, call_ollama_func, director_model: str) -> str:
    """Zero-shot intent classification using the Director model."""
    intent = call_ollama_func(
        INTENT_CLASSIFIER_SYSTEM,
        f"User prompt: '{user_prompt}'\n\n"
        f"Classify as MODIFICATION, INFORMATIONAL, QUERY, or CHAT.",
        "Intent Classifier",
        director_model,
    )
    intent_clean = intent.strip().upper()
    if "CHAT" in intent_clean:
        return "CHAT"
    if "INFORMATIONAL" in intent_clean:
        return "INFORMATIONAL"
    if "QUERY" in intent_clean:
        return "QUERY"
    return "MODIFICATION"


# ── GDD Librarian ──────────────────────────────────────────────────────────

def recursive_librarian(user_prompt: str, extract_gdd_sections_func=None,
                         gdd_section_map=None, keyword_to_section=None) -> str:
    """Query the Librarian for all relevant GDD sections to build full context."""
    if extract_gdd_sections_func is None:
        from context_extractor import extract_project_context as _e
        extract_gdd_sections_func = _e
    # extract_project_context already returns a formatted markdown string
    sections_text = extract_gdd_sections_func(user_prompt)
    if not sections_text:
        return ""

    return sections_text


# ── Project State ──────────────────────────────────────────────────────────

def get_project_state(project_root: Path = None, all_domains: dict = None) -> str:
    """Reads docs/completed_features.md and docs/todo.md for project summary."""
    pr = project_root or PROJECT_ROOT
    domains = all_domains or _ALL_DOMAINS
    lines = ["## Current Project State\n"]
    completed_path = pr / "docs" / "completed_features.md"
    if completed_path.is_file():
        try:
            text = completed_path.read_text(encoding="utf-8", errors="replace")
            done_sections = re.findall(r"### ✅ (.+)", text)
            if done_sections:
                lines.append("### ✅ Implemented Systems")
                for s in done_sections[:15]:
                    lines.append(f"- {s}")
                if len(done_sections) > 15:
                    lines.append(f"- ... ({len(done_sections) - 15} more)")
                lines.append("")
        except Exception:
            pass
    todo_path = pr / "docs" / "todo.md"
    if todo_path.is_file():
        try:
            text = todo_path.read_text(encoding="utf-8", errors="replace")
            lines.append("### Project TODO List (Unimplemented)")
            for line in text.splitlines():
                # Keep headers, table formatting, and non-done items
                if "✅ Done" not in line and line.strip():
                    lines.append(line)
            lines.append("")
        except Exception:
            pass
    lines.append("### 🟢 Available Domains")
    for key, domain in domains.items():
        if domain["ready"]:
            lines.append(f"- {domain['tag']} {domain['description']}")
    lines.append("")
    lines.append("### 🔴 Unavailable Domains")
    for key, domain in domains.items():
        if not domain["ready"]:
            lines.append(f"- {domain['tag']} {domain['description']}")
    lines.append("")
    lines.append("### ❌ Does NOT Exist")
    lines.append("- No networking/multiplayer code at all")
    lines.append("- No Box2D physics integration")
    lines.append("- No audio engine (SoLoud not integrated)")
    lines.append("- No save/load system")
    lines.append("- No boss encounters")
    lines.append("- No prize/augment runtime loading")
    lines.append("- No Barker billboarding system")
    lines.append("")
    return "\n".join(lines)


def get_available_domains_text(all_domains: dict = None) -> str:
    domains = all_domains or _ALL_DOMAINS
    parts = []
    for key, domain in domains.items():
        if domain["ready"]:
            parts.append(f"- {domain['tag']} {domain['description']}")
    return "\n".join(parts)


def get_unavailable_domains_text(all_domains: dict = None) -> str:
    domains = all_domains or _ALL_DOMAINS
    parts = []
    for key, domain in domains.items():
        if not domain["ready"]:
            parts.append(f"- {domain['tag']} {domain['description']}")
    return "\n".join(parts)


# ── Per-Task GDD Distiller ──────────────────────────────────────────────────

_GDD_DISTILL_THRESHOLD: int = 4000   # chars — below this, no model call needed
_GDD_DISTILL_TARGET: int = 3000      # desired output size in chars


def _distill_gdd_for_task(task_spec: str, gdd_text: str,
                          attraction_name: str = "") -> str:
    """Compress an oversized GDD extract to only the content relevant to task_spec.

    Uses the phi3.5 pre-summarizer (fast, 16K context) to produce a tight,
    task-focused slice of the GDD.  Only called when ``gdd_text`` exceeds
    ``_GDD_DISTILL_THRESHOLD`` chars so the fast path (small extracts) has
    zero overhead.

    Args:
        task_spec:       The task's spec string (used as the relevance query).
        gdd_text:        Full GDD extract to compress.
        attraction_name: Optional name of the target attraction (e.g. "skeeball").
                         When provided, the distiller is instructed to discard
                         sections about other named attractions.

    Returns:
        Compressed GDD text, or the original text if compression fails or
        the model is unavailable.
    """
    try:
        from ollama_client import call_ollama as _call_ollama
        from ollama_client import PRE_SUMMARIZER_MODEL as _SUMM_MODEL
    except ImportError:
        return gdd_text

    _attraction_clause = ""
    if attraction_name:
        _attraction_clause = (
            f"The task is implementing the '{attraction_name}' attraction. "
            f"DISCARD any paragraphs that describe a different named attraction or mini-game "
            f"(e.g. Plinko, Ring Toss, Coin Cascade, Slingshot Array) unless they contain "
            f"a mechanic rule that also applies to '{attraction_name}'. "
        )

    system = (
        "You are a precise context distiller. "
        "You will receive a task specification and a large block of game-design document (GDD) text. "
        + _attraction_clause +
        "Extract and return ONLY the paragraphs, rules, values, and examples that are directly "
        f"relevant to implementing the task. Omit unrelated sections entirely. "
        f"Keep output under {_GDD_DISTILL_TARGET} characters. "
        "Output plain text — no commentary, no headings invented by you."
    )
    user = (
        f"## Task Specification\n{task_spec}\n\n"
        f"## GDD Content to Distill\n{gdd_text}"
    )

    result = _call_ollama(system, user, "GDD Distiller", _SUMM_MODEL,
                          skip_pre_summarizer=True)
    if result and len(result.strip()) > 100:
        _label = attraction_name or task_spec[:40]
        print(f"  [GDD Distiller] Compressed {len(gdd_text)} → {len(result)} chars "
              f"for '{_label}'")
        return result.strip()
    return gdd_text


# ── Task Execution ──────────────────────────────────────────────────────────

def execute_task(task, user_prompt: str, director_output: str,
                 all_results: dict, file_context: str, gdd_context: str,
                 sibling_context: str = "",
                 ollama_params: Optional[dict] = None) -> str:
    """Execute a single task by calling the appropriate agent.

    Args:
        sibling_context: Aggregated outputs of previously completed sibling
            tasks (same parent or top-level), so agents see peer work.
        ollama_params: Optional dict of Ollama options (e.g., {"temperature": 0.5}).
    """
    # Local imports to avoid circular dependencies
    from domain_registry import resolve_agent_name, ALL_DOMAINS, get_agent_system
    from file_references import get_referenced_files_cache
    from signals import extract_signals, extract_double_check
    from ledger import ensure_ledger_header
    from models import Task

    agent_key = resolve_agent_name(task.agent)
    domain = ALL_DOMAINS.get(agent_key)

    # Fallback: look up domain from the class-based cartridge registry on _CTX
    if not domain:
        try:
            from pipeline import _CTX
            ctx_dr = getattr(_CTX, 'domain_registry', None) if _CTX else None
            if ctx_dr and agent_key in ctx_dr:
                domain = ctx_dr[agent_key]
        except ImportError:
            pass

    if not domain:
        return f"[ERROR] Unknown agent: {task.agent}"

    preferred_model = domain.get("model", "qwen2.5-coder:7b")
    system = get_agent_system(agent_key)

    # Build context
    context_parts = [
        f"## Original Feature Request\n{user_prompt}",
    ]

    if director_output:
        context_parts.append(f"## Director's Task Breakdown\n{director_output}")

    if file_context:
        context_parts.append(file_context)

    if gdd_context:
        # ── Task-specific GDD re-extraction ────────────────────────────────
        # The global gdd_context blob was built from the high-level user
        # prompt and may not contain the specific rules/values this task
        # needs (e.g. skeeball scoring bands vs. sound cue tables).
        # Re-query context_extractor with the task spec so the agent gets a
        # task-focused slice rather than the head of an unrelated GDD block.
        # Forward scope_mode and attraction_name so the deterministic fast path
        # fires for NEW_ATTRACTION / MODIFY_ATTRACTION tasks.
        _task_spec_query = getattr(task, "spec", "") or user_prompt
        if _task_spec_query and len(_task_spec_query) > 20:
            try:
                from context_extractor import extract_project_context as _epc
                import os as _os_ec, re as _re_ec
                _tf_ec = getattr(task, "target_file", None) or ""
                _attr_ec = _os_ec.path.splitext(_os_ec.path.basename(_tf_ec))[0] if _tf_ec else ""
                _attr_ec = _re_ec.sub(r'[^\w]+', ' ', _attr_ec).strip()
                # Derive scope_mode: if task has a target_file it is always a
                # NEW_ATTRACTION or MODIFY_ATTRACTION context; default GENERAL.
                _scope_ec = "NEW_ATTRACTION" if _attr_ec else "GENERAL"
                _task_gdd = _epc(_task_spec_query,
                                 scope_mode=_scope_ec,
                                 attraction_name=_attr_ec)
                if _task_gdd and len(_task_gdd.strip()) > 100:
                    print(f"  [GDD Re-extract] Task '{getattr(task, 'task_id', '?')}': "
                          f"{len(gdd_context)} → {len(_task_gdd)} chars "
                          f"(task-keyed extract, scope={_scope_ec}, attraction='{_attr_ec}')")
                    gdd_context = _task_gdd
            except Exception:
                pass  # fall through to existing global blob

        # Distill with phi3.5 when still oversized; otherwise block-collapse.
        # Derive the attraction name from task.target_file so the distiller
        # can explicitly discard unrelated attraction modules.
        _GDD_CAP = 3000
        if len(gdd_context) > _GDD_DISTILL_THRESHOLD:
            import os as _os, re as _re
            _tf = getattr(task, "target_file", None) or ""
            _attr_name = _os.path.splitext(_os.path.basename(_tf))[0] if _tf else ""
            # Normalise slug: "skeeball" not "skeeball_lua"
            _attr_name = _re.sub(r'[^\w]+', ' ', _attr_name).strip()
            gdd_context = _distill_gdd_for_task(_task_spec_query, gdd_context,
                                                attraction_name=_attr_name)
        if len(gdd_context) > _GDD_CAP:
            from token_budget import TokenBudget as _TB
            gdd_context = _TB._block_aware_collapse(gdd_context, _GDD_CAP)
        context_parts.append(gdd_context)

    # ── Internal API Ledger: inject live confirmed symbol list ─────────────
    # Prevents downstream agents from hallucinating function names that were
    # never registered, and makes new registrations from prior tasks visible.
    try:
        from ledger import read_internal_api_ledger
        _live_api = read_internal_api_ledger(max_chars=3000)
        if _live_api:
            context_parts.append(_live_api)
    except Exception:
        pass

    # ── Compact Bridge Contract Cheatsheet ────────────────────────────────
    # Injected into EVERY scripter call so the approved API names survive
    # even when context collapses under VRAM pressure.  Hard-capped at 700
    # chars so it cannot itself become a VRAM hazard.
    # Reads directly from the live cartridge on _CTX; degrades gracefully
    # if the cartridge is not mounted (returns empty string, no crash).
    # Edge cases handled:
    #   - _CTX not yet populated (import-time / unit-test context)
    #   - cartridge missing a section key (skips that section)
    #   - bridge contract callable raises (caught, cheatsheet omitted)
    #   - domain is not Lua (cheatsheet injected regardless — never harmful)
    try:
        from pipeline import _CTX as _exec_ctx
        _bc_fn = getattr(_exec_ctx, '_cartridge_build_bridge_contract', None) if _exec_ctx else None
        if callable(_bc_fn):
            _bc = _bc_fn()
            _api_names  = list((_bc.get("midwayphysics_spawn_api") or {}).keys())
            _pool_names  = list((_bc.get("object_pools") or {}).keys())
            _econ_names  = list((_bc.get("economy_api") or {}).keys())
            _subst_guide = (
                "Substitution quick-ref (common wrong → correct):\n"
                "  SpawnDynamicBall → SpawnDynamicSphere(lx,ly,lz,radius[,mass])\n"
                "  RemoveBody/ReleaseHandle/DestroyEntity → DestroyBody(handle)\n"
                "  CheckCollision → IsSensorTriggered(handle) → bool\n"
                "  GetLinearVelocity → GetVelocity(handle) → vx,vy,vz\n"
                "  SetPosition/Teleport → no approved equivalent; use MoveKinematic(h,lx,ly,lz,dt)\n"
                "  ApplyForce/AddForce → ApplyImpulse(handle,ix,iy,iz)\n"
                "  table.clear(t) → for k in pairs(t) do t[k]=nil end  (Lua 5.1 compat)\n"
                "Lifecycle: OnLoadStatic() / OnLoad() / OnUnload() — bare globals, no return.\n"
                "Step: MidwayPhysics.OnStep(function(dt) ... end) — call inside OnLoad.\n"
            )
            if _api_names:
                _cheatsheet = (
                    "## ⚡ Bridge API Cheatsheet (exhaustive — use ONLY these names)\n"
                    + ("Physics: " + ", ".join(_api_names) + "\n" if _api_names else "")
                    + ("Pools:   " + ", ".join(_pool_names) + "\n" if _pool_names else "")
                    + ("Economy: " + ", ".join(_econ_names) + "\n" if _econ_names else "")
                    + _subst_guide
                )
                # Hard cap: truncate to 700 chars from the end so the
                # function names at the top are always preserved.
                if len(_cheatsheet) > 700:
                    _cheatsheet = _cheatsheet[:700]
                context_parts.append(_cheatsheet)
    except Exception:
        pass

    # Auto-Inject Referenced Files
    refs_block = get_referenced_files_cache()
    if refs_block:
        context_parts.append(refs_block)

    # ── Directive A: Stateless Parent Context ──────────────────────────────
    # Parent context is stripped to code artifacts only to prevent linear
    # conversational bloat from bleeding across tasks.
    if task.parent and task.parent in all_results:
        from _helpers_text import strip_to_code_artifacts
        parent_output = all_results[task.parent]
        parent_clean = strip_to_code_artifacts(parent_output, fallback_truncation=800)
        context_parts.append(f"## Parent Task Context (code artifacts only)\n{parent_clean}")

    # ── Directive A: Stateless Sibling Context ─────────────────────────────
    # Already stripped by run_tasks() before being passed as sibling_context.
    # Accept as-is — it has already been code-artifact-sanitized upstream.
    if sibling_context:
        context_parts.append(sibling_context)

    # ── Directive A: Stateless Iteration Output ────────────────────────────
    # Previous iteration output is stripped to code artifacts to prevent the
    # agent's own conversational prose from compounding the context window.
    if task.iteration > 0 and task.output:
        from _helpers_text import strip_to_code_artifacts
        iter_clean = strip_to_code_artifacts(task.output, fallback_truncation=600)
        context_parts.append(f"## Your Previous Output (iteration {task.iteration})\n{iter_clean}")
        if task.iteration >= MAX_ITERATIONS - 1:
            from _prompts import SELF_CORRECT_SYSTEM
            system = SELF_CORRECT_SYSTEM

    # ── Fix D: History truncation guard — cap task.context ────────────
    # task.context grows from query results, pro-test injection, and
    # iteration output. Without a hard cap, it can bloat to 50K+ chars
    # across 3 iterations, directly causing VRAM OOM at <1 tok/s.
    # Uses block-aware collapse so structural blocks (function bodies,
    # markdown sections) are summarised rather than hard-cut.
    _CONTEXT_CHAR_LIMIT: int = 4000
    if task.context:
        if len(task.context) > _CONTEXT_CHAR_LIMIT:
            print(f"  [Context Truncation] task.context was {len(task.context)} chars, "
                  f"collapsing to {_CONTEXT_CHAR_LIMIT} via block-aware paging")
            task.context = TokenBudget._block_aware_collapse(
                task.context, _CONTEXT_CHAR_LIMIT
            )
        context_parts.append(task.context)

    # The task spec
    context_parts.append(f"## Task Specification\n{task.spec}")

    user_message = "\n\n".join(context_parts)

    # ── Fix D: Model-Aware Total user_message char ceiling ──────────
    # Replaced the old 20K-char hard ceiling with a model-aware dynamic
    # limit computed from the model's context window. Uses 65% of the
    # context budget for user content (remaining 35% reserved for system
    # prompt, output tokens, and KV cache overhead).
    #
    # When truncation occurs, the overflow text is preserved in the
    # OffloadStore with a <PAGE_OUT> marker so the LLM can retrieve it
    # on demand. The offload block_id is task_id-specific for retrieval.
    from ollama_client import resolve_ctx_size
    _MODEL_CTX = resolve_ctx_size(preferred_model)
    # 55% of context budget at 3 chars/token (code heuristic, matches estimate_tokens).
    # Using 3 chars/token (not 1.5) prevents the ceiling from being 2× higher than the real
    # token cost, which was the root cause of repeated VRAM overruns.
    # 55% (not 65%) reserves 45% for system prompt, output tokens, and KV cache overhead.
    _TOTAL_MSG_CHAR_LIMIT: int = int(_MODEL_CTX * 3 * 0.55)
    if len(user_message) > _TOTAL_MSG_CHAR_LIMIT:
        print(f"  [Context Truncation] user_message was {len(user_message)} chars, "
              f"truncating to {_TOTAL_MSG_CHAR_LIMIT} chars "
              f"(model ctx={_MODEL_CTX} tok @ 55%, 3 chars/tok)")

        # ── Preserve overflow in OffloadStore before truncating ─────
        # The truncated portion is saved to the OffloadStore under a
        # task-specific block_id so the LLM can <PAGE_IN> it if needed.
        _overflow_text = user_message[_TOTAL_MSG_CHAR_LIMIT:]
        if _overflow_text.strip():
            try:
                from offload_store import get_offload_store
                _store = get_offload_store()
                _overflow_id = f"context_overflow_{task.task_id}"
                _store.store_block(
                    block_id=_overflow_id,
                    header=f"Overflow context for {task.task_id} "
                           f"({len(_overflow_text)} chars truncated)",
                    body_lines=[_overflow_text],
                )
                _overflow_note = (
                    f"\n---\n[📄 Context Overflow Preserved] "
                    f"An additional {len(_overflow_text)} chars of context were truncated "
                    f"to fit within the model's {_MODEL_CTX}-token context window. "
                    f"Use <PAGE_IN> to load the offloaded block:\n"
                    f"`<invoke_kernel><action>PAGE_IN</action>"
                    f"<target>{_overflow_id}</target></invoke_kernel>`\n"
                    f"---\n"
                )
            except Exception:
                _overflow_note = ""
        else:
            _overflow_note = ""

        # Keep the task spec (last appended part) by truncating earlier context
        _spec_marker = f"## Task Specification\n{task.spec}"
        _spec_idx = user_message.find(_spec_marker)
        if _spec_idx > 0:
            _before_spec = user_message[:_spec_idx]
            _allowed = max(0, _TOTAL_MSG_CHAR_LIMIT - len(_spec_marker) - 50)
            _before_spec = _before_spec[:_allowed] + (
                f"\n[... context truncated at {_allowed} chars; "
                f"overflow preserved in OffloadStore as '{_overflow_id}' ...]\n"
            )
            user_message = _before_spec + "\n\n" + _spec_marker + _overflow_note
        else:
            user_message = user_message[:_TOTAL_MSG_CHAR_LIMIT] + (
                f"\n[... total context ceiling reached at {_TOTAL_MSG_CHAR_LIMIT} chars; "
                f"overflow preserved in OffloadStore ...]"
            ) + _overflow_note

    label = f"{domain['name']} (Task {task.task_id})"
    if task.is_query:
        label = f"[QUERY] {domain['name']} -> {task.parent}"

    # ── Directive C: Kernel Interrupt — VRAM critical check ──────────────
    # Before sending the prompt to the LLM, check if the combined token
    # payload exceeds 80% of the model's safe context window.
    # If it does, inject a [SYSTEM KERNEL: VRAM critical] warning into the
    # user message, instructing the agent to <PAGE_OUT> before generating.
    from token_budget import TokenBudget
    vram_warning = TokenBudget.check_vram_critical(system, user_message, preferred_model)
    if vram_warning:
        print(f"  [Kernel Interrupt] ⚠ Prepending VRAM critical warning to '{label}'")
        user_message = vram_warning + "\n\n" + user_message

    # ── Directive A: Hard Context Firewall (Absolute Statelessness) ─────────
    # NO history survives between tasks. A brand new messages array is built
    # fresh for every single task invocation: [System Prompt, User Prompt].
    # No .pop(), no pruning, no accumulation — explicit zero-state per call.
    from ollama_client import (
        call_ollama_with_messages, get_last_paged_cache,
        VramOverrunError, vram_overrun_abort, get_vram_abort_diagnostics,
        is_fatal_ollama_error,
    )
    messages: list = []
    messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user_message})
    try:
        output = call_ollama_with_messages(messages, label, preferred_model, params=ollama_params)
    except VramOverrunError:
        # VRAM overrun — hard abort. Mark task as failed and return a
        # diagnostic message that will propagate up through run_tasks.
        _vram_diag = get_vram_abort_diagnostics()
        _abort_msg = (
            f"\n\n[VRAM OVERRUN — PIPELINE ABORTED]\n"
            f"Task '{task.task_id}' ({task.agent}) was aborted due to VRAM overrun.\n"
            f"The TPS watchdog detected token speed below 2.0 tok/s.\n"
            f"Diagnostics:\n{_vram_diag}\n"
            f"[END OF VRAM ABORT MESSAGE]\n"
        )
        print(_abort_msg, flush=True)
        task.output = _abort_msg
        task.signals = []
        task.double_check = None
        task.completed = True
        return _abort_msg

    # If Ollama returned a fatal error sentinel (timeout, socket drop, OOM)
    # treat the task as failed immediately — do NOT pass the sentinel string
    # downstream as code, and do NOT loop back into another model call.
    if is_fatal_ollama_error(output):
        _fatal_msg = (
            f"[TASK FAILED — OLLAMA ERROR]\n"
            f"Task '{task.task_id}' ({task.agent}) received a fatal error from Ollama:\n"
            f"{output.strip()}\n"
            f"[END OF OLLAMA ERROR]\n"
        )
        print(f"  ⛔ {_fatal_msg.splitlines()[0]}", flush=True)
        task.output = _fatal_msg
        task.signals = []
        task.double_check = None
        task.completed = True
        return _fatal_msg

    # ── Directive A: Capture paged_in_cache for Pro-Mode Inheritance ──
    task.paged_files_cache = get_last_paged_cache()
    if task.paged_files_cache:
        print(f"  [Paging Kernel] 📋 Task '{task.task_id}' paged_files_cache: "
              f"{len(task.paged_files_cache)} files, "
              f"{sum(len(v) for v in task.paged_files_cache.values())} total chars")

    # ── Phase II: MoA Speculative Multi-Draft Synthesis ────────────────
    # If the model output contains both [OPTION_A] and [OPTION_B] markers,
    # parse them into separate candidate buffers, pass through resolve_conflict()
    # for native consensus merging, and commit the deduplicated stream.
    import re as _re
    option_a_match = _re.search(r'\[OPTION_A\]\s*(.*?)\s*\[/OPTION_A\]', output, _re.DOTALL)
    option_b_match = _re.search(r'\[OPTION_B\]\s*(.*?)\s*\[/OPTION_B\]', output, _re.DOTALL)

    if option_a_match and option_b_match:
        option_a_code = option_a_match.group(1).strip()
        option_b_code = option_b_match.group(1).strip()
        print(f"  [MoA Multi-Draft] 📋 Detected dual candidate blocks ({len(option_a_code)} vs {len(option_b_code)} chars)")

        # Invoke Native Consensus API: pipe both candidates through CONF expert
        from _mesh_api import resolve_conflict
        from pipeline import call_ollama as _pipeline_call
        from domain_registry import ALL_DOMAINS as _all_domains

        consensus_result = resolve_conflict(
            option_a_code,
            option_b_code,
            veto_justification="Multi-draft synthesis: merge alternative structural implementations",
            feature_request=user_prompt,
            _call_ollama=_pipeline_call,
            _ALL_DOMAINS=_all_domains,
        )

        merged_code = getattr(consensus_result, 'merged_code', '') or ''
        verdict = getattr(consensus_result, 'verdict', 'COMPROMISE')

        # Commit Deduplicated Stream: apply ledger header and append to ledger
        if merged_code:
            print(f"  [MoA Multi-Draft] ✅ Consensus {verdict}: merged {len(option_a_code)} + {len(option_b_code)} → {len(merged_code)} chars")
            output = ensure_ledger_header(merged_code, task.spec, task.agent)
            from ledger import _append_to_ledger
            _append_to_ledger(
                f"### [MoA Merge: {task.task_id}]\n**Verdict:** {verdict}\n**Merged Output:**\n{merged_code}\n",
                task.agent,
                task.spec,
            )
        else:
            print(f"  [MoA Multi-Draft] ⚠ Consensus returned empty merged_code — using original output")

    # Ledger Guard: auto-fix missing headers
    output = ensure_ledger_header(output, task.spec, task.agent)

    # API Ledger: signatures are written only after preflight validation clears
    # the output (arch-fix and fix-loop paths in _finalize_preflight.py call
    # update_internal_api_ledger on the validated code).  Writing here on raw
    # completion would persist phantom API names into the ledger before guards
    # have had a chance to reject them, poisoning downstream agent prompts.

    task.output = output
    task.signals = extract_signals(output)
    task.double_check = extract_double_check(output)
    task.completed = True

    # Thermal Pacing: Allow Steam Deck APU to dissipate heat
    print(f"  [Thermal Pacing] Cooling down for 2.0s...")
    time.sleep(2.0)

    return output


# ── Cross-Module Compiler Wrapper ──────────────────────────────────────

def compile_project(project_root: Path = None, timeout: int = 30) -> tuple[bool, str]:
    """Run the native project build and return (success, error_text).

    Calls cmake --build on Windows, make -j4 on POSIX.
    Error text is capped at 2,000 chars per Directive E.
    Returns (True, '') on successful compilation.
    """
    pr = project_root or PROJECT_ROOT
    try:
        if sys.platform == "win32":
            proc = subprocess.run(
                ["cmake", "--build", "."],
                capture_output=True, text=True, cwd=pr,
                shell=True, timeout=timeout,
            )
        else:
            proc = subprocess.run(
                ["make", "-j4"],
                capture_output=True, text=True, cwd=pr,
                timeout=timeout,
            )
        if proc.returncode == 0:
            return True, ""
        raw_stderr = (proc.stderr or "")[:2000]
        return False, raw_stderr
    except subprocess.TimeoutExpired:
        return False, f"C++ build timed out after {timeout}s"
    except Exception as e:
        return False, str(e)


# ── Director Prompt ──────────────────────────────────────────────────────

def build_director_prompt(all_domains: dict = None) -> str:
    domains = all_domains or _ALL_DOMAINS
    available = get_available_domains_text(domains)
    unavailable = get_unavailable_domains_text(domains)
    return (
        "Decompose this feature request into 1-5 tasks. "
        "Each task must have a domain tag and a short title.\n\n"
        "IMPORTANT: You may assign as FEW as 1 task or as MANY as 5. "
        "Only create tasks that are absolutely necessary.\n\n"
        "AVAILABLE DOMAINS (use ONLY these):\n"
        f"{available}\n\n"
        "UNAVAILABLE DOMAINS (do NOT use these):\n"
        f"{unavailable}\n\n"
        "RULES:\n"
        "- Do NOT use [NET] — there is no networking code in the project.\n"
        "- Do NOT use [SHADER] — shader effects are not yet implemented.\n"
        "- Do NOT assign [Lua] tasks that write network code.\n"
        "- Only assign a domain if the project actually has code for it.\n\n"
        "Order by dependency: [C++] first, then [PHYS], then [Lua].\n\n"
        "OUTPUT FORMAT (exactly):\n"
        "## Task Breakdown: [Feature Name]\n"
        "### Task 1: [DOMAIN] - [Short Title] (DependsOn: None)\n"
        "### Task 2: [DOMAIN] - [Short Title] (DependsOn: Task 1)\n"
        "### Task 3: [DOMAIN] - [Short Title] (DependsOn: Task 1, Task 2)\n"
        "...\n\n"
        "CRITICAL: Do NOT write any code. Only list tasks.\n\n"
        "MATH SENSOR: If the user's request involves dense 3D math, quaternions, "
        "or complex physics algorithms, you MUST append the exact string [MATH_HEAVY] "
        "to the very end of your output."
    )
