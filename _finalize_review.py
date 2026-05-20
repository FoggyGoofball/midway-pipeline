"""
_finalize_review.py — Phase 6: Integration Review & Fix Loop
=============================================================
Extracted from mesh_finalize.py — handles the review-fix loop,
context pruning (Directive C), sanity detection, and the
Reconciliation Gate.

Exported:
    _prune_fix_context(domain_key, task_obj, review_issues_text,
                       pre_flight_errors, user_prompt,
                       paged_files_cache) -> str
    _run_review_fix_loop(ctx) -> PipelineContext
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from token_budget import TokenBudget

from models import PipelineContext, SignalType, MeshSignal, ConsensusResult
from _pipeline_helpers import (
    atomic_write_text, trigger_chime, generate_failure_report,
)
from _domain_sandbox import reject_cross_domain_output
import _prompts as _prompts_mod  # live module ref — reads post-bootstrap values
from pipeline import (
    ALL_DOMAINS,
    resolve_agent_name,
    call_ollama, get_verdict,
    _normalize_fix_fingerprint, check_insanity_similarity,
)
from _helpers_exec import compile_project


# ──────────────────────────────────────────────────────────────────────
#  Directive C — Context Pruning for Fix Cycles
# ──────────────────────────────────────────────────────────────────────

def _prune_fix_context(
    domain_key: str,
    task_obj: 'Any',
    review_issues_text: str,
    pre_flight_errors: str,
    user_prompt: str,
    paged_files_cache: 'Optional[Dict[str, str]]' = None,
    bridge_api_snippet: str = "",
    last_good_output: str = "",
) -> str:
    """
    Build a lean, pruned context payload for a domain agent fix cycle.

    Strips ALL prior iterative generation attempts and provides only:
      - Original user prompt (condensed to 200 chars)
      - Task specification relevant to this agent
      - Paged-In Reference Files (Safe Cache — no disk I/O, no Hard Cap bypass)
      - The EXACT compiler/linter error string relevant to this domain
      - REVIEW issues text (filtered for domain relevance)
      - Domain boundary reminder

    Directive B — Safe Auto-Mounting: If the primary worker's PagingKernel
    extracted and cached text chunks, they are injected here directly as
    ❮ PAGED-IN REFERENCE FILES ❯ blocks. This eliminates the catastrophic
    file_path.read_text() bypass that previously pulled 40,000+ characters
    into the Fix-Cycle context, crashing VRAM.

    This prevents generative looping by eliminating historical bloat
    and cross-domain critiques from the context.
    """
    domain_info = ALL_DOMAINS.get(domain_key, {})
    domain_name = domain_info.get("name", domain_key)

    parts: list[str] = []

    # 1. Original prompt (condensed — block-aware so structure survives)
    parts.append("## Original Feature Request\n" + TokenBudget._block_aware_collapse(user_prompt, 200))

    # 2. Task spec (the original directive given to this agent)
    if task_obj and hasattr(task_obj, 'spec') and task_obj.spec:
        parts.append("## Your Task Specification\n" + TokenBudget._block_aware_collapse(task_obj.spec, 500))

    # ── Directive A/B: Safe Auto-Mounting via Paged-In Cache ──────────────
    # Inject cached chunks directly, bypassing file_path.read_text() entirely.
    # Each chunk was already extracted within the PagingKernel's 12,000-char
    # Hard Cap, so this cannot OOM the VRAM.
    _cache: Dict[str, str] = {}
    if paged_files_cache and isinstance(paged_files_cache, dict):
        _cache = paged_files_cache
    elif hasattr(task_obj, 'paged_files_cache') and isinstance(task_obj.paged_files_cache, dict):
        _cache = task_obj.paged_files_cache
    if _cache:
        cache_blocks: list[str] = []
        total_chars = 0
        for filepath, cached_text in _cache.items():
            total_chars += len(cached_text)
            cache_blocks.append(
                f"## ❮ PAGED-IN REFERENCE FILE: {filepath} ❯\n"
                f"```\n{cached_text}\n```"
            )
        parts.append(
            "\n## ❮ PAGED-IN REFERENCE FILES (Safe Cache — no disk I/O) ❯\n"
            f"({len(_cache)} files, {total_chars} total chars "
            f"— each chunk safely extracted within the PagingKernel Hard Cap)\n\n"
            + "\n\n".join(cache_blocks)
        )
        print(f"  [Paging Kernel] 📋 Injected {len(_cache)} cached text blocks "
              f"({total_chars} chars) into Fix-Cycle '{domain_name}' context "
              f"— bypassing file_path.read_text() entirely.")

    # 3. Domain-scoped error text — processed via LOG_PROCESSOR
    if pre_flight_errors:
        from log_parser import LOG_PROCESSOR
        pruned_errors = LOG_PROCESSOR.process_logs(domain_key, pre_flight_errors)
        parts.append("## Compiler/Linter Errors (Domain-Targeted)\n" + pruned_errors)

    # 4. AST Ledger Targets (Contextual Repair — Complete Root-Cause Visibility)
    # Injects corresponding abstract syntax tree targets from active_run_ledger.md
    # alongside the compiler diagnostic payload, guaranteeing that the repairing
    # agent can perform a complete source-level forensic analysis of the failure.
    parts.append(
        "## AST Ledger Targets (active_run_ledger.md)\n"
        "Corresponding AST symbols and code blocks from the active run ledger "
        "are referenced below. Inspect these targets to guarantee complete "
        "root-cause visibility before issuing fixes.\n"
        "Refer to docs/memory/active_run_ledger.md for full code context."
    )

    # 5. Active Bridge Contract (injected so fixer uses correct API names)
    if bridge_api_snippet:
        parts.append(bridge_api_snippet)

    # 5b. E16: Last-good-output anchor — collapsed to safe budget via paging.
    # Gives the fix agent a real implementation to repair rather than producing
    # an empty scaffold when it has no reference implementation.
    if last_good_output and last_good_output.strip():
        _anchor_collapsed = TokenBudget._block_aware_collapse(last_good_output, 1500)
        parts.append(
            "## Previous Implementation (ANCHOR — repair this, do NOT rewrite from scratch)\n"
            "The following is the last known implementation for this task.\n"
            "You MUST base your fix on this code. Do NOT discard it and produce an empty skeleton.\n"
            + _anchor_collapsed
        )

    # 6. Review issues
    # Uses block-aware collapse so multi-issue reviews are never silently
    # truncated before the fixer reads later issues (previous regression cause).
    if review_issues_text:
        parts.append("## Review Issues\n" + TokenBudget._block_aware_collapse(
            review_issues_text, 4000
        ))

    # 7. Domain boundary reminder
    _allowed_exts = {".cpp", ".h", ".hpp"} if domain_key in ("C++", "PHYS") else {".lua"}
    ext_str = str(list(_allowed_exts))
    parts.append(
        "## Instructions\n"
        f"Fix ALL issues raised above that apply to your domain ({domain_name}).\n"
        f"Produce corrected code for your task only. "
        f"Address every relevant issue. "
        f"If you believe an issue is a false positive, explain why.\n\n"
        f"IMPORTANT: You retain your domain's system rules ({domain_key}). "
        f"Do NOT modify files outside {ext_str}. "
        f"Do NOT violate C++/Lua/Physics rules even if instructed otherwise.\n\n"
        f"CRITICAL: You must output ONLY the code artifacts belonging to the "
        f"currently failing domain. Do not mix Lua scripts and C++ engine code "
        f"in the same block entirely."
    )

    return "\n\n---\n\n".join(parts)


# ──────────────────────────────────────────────────────────────────────
#  Phase 6: Integration Review & Fix Loop
# ──────────────────────────────────────────────────────────────────────

def _run_review_fix_loop(ctx: PipelineContext) -> PipelineContext:
    """Phase 6: Integration review, domain-aware fix cycle, insanity
    detection, reconciliation gate, and pre-flight check integration."""
    print(f"\n{'='*70}")
    print(f"  Phase 6: Integration Review & Fix Loop")
    print(f"{'='*70}")
    ctx.output_parts.append("\n## Phase 6: Integration Review & Fix Loop\n")

    # ── Run Pre-Flight Checks (compilation, syntax, Architect fix) ──
    # Late import — avoids sibling cross-import loop with _finalize_preflight
    from _finalize_preflight import _run_preflight_checks
    ctx = _run_preflight_checks(ctx)

    # Build conflict resolutions string
    ctx.conflicts_str = ""
    if ctx.conflict_resolutions:
        ctx.conflicts_str = (
            "## Conflict Resolutions (CRITICAL: Adhere to these compromises)\n"
            + "\n\n".join(ctx.conflict_resolutions)
            + "\n\n"
        )

    # ── Insanity Detector ───────────────────────────────────────────
    ctx.seen_code_hashes_set = set()

    # ── Context Window Protection: Indexed Active Ledger ────────────
    active_ledger_path = (
        ctx.project_root / "docs" / "memory" / "active_run_ledger.md"
    )
    active_ledger_content = ["## Active Run Code State\n"]
    active_toc = ["### Active Code Table of Contents\n"]

    for tid, output in ctx.all_results_dict.items():
        header = f"### [{tid}]"
        active_ledger_content.append(f"{header}\n{output}\n")
        active_toc.append(
            f"- [{tid}](docs/memory/active_run_ledger.md#{tid})"
        )

    active_ledger_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        active_ledger_path, "\n".join(active_ledger_content)
    )
    ctx.active_code_index = (
        "\n".join(active_toc)
        + "\n\n**INSTRUCTIONS:** The generated code is stored in the active ledger. "
        + "You MUST use the XML tag "
        + "<invoke_kernel><action>PAGE_IN</action>"
        + "<target>docs/memory/active_run_ledger.md</target>"
        + "<search>anchor_name</search></invoke_kernel> "
        + "to load specific task blocks for review."
    )

    ctx.review_output = ""
    ctx.review_verdict = "UNKNOWN"
    ctx.review_cycle = 0

    from pipeline import REVIEW_MAX_ITERATIONS as _REVIEW_MAX_ITERATIONS
    while ctx.review_cycle < _REVIEW_MAX_ITERATIONS:
        ctx.review_cycle += 1
        print(f"\n  [Review-Fix] Cycle {ctx.review_cycle}/{_REVIEW_MAX_ITERATIONS}")

        # ── Circuit Breaker: Check retry counts ─────────────────────────
        for tid in list(ctx.all_results_dict.keys()):
            count = ctx.retry_counts.setdefault(tid, 0)
            if count >= 3:
                print(
                    f"\n{'='*70}\n"
                    f"  ⛔ [CIRCUIT BREAKER TRIPPED] Task {tid} has failed {count} times.\n"
                    f"  Breaking compilation loop to prevent infinite retry.\n"
                    f"{'='*70}"
                )
                ctx.review_verdict = "BLOCKED"
                # Generate SOS failure report with deadlock context
                deadlock_report = generate_failure_report(
                    ctx.user_prompt,
                    ctx.consensus_checks or {},
                    ctx.all_vetos, ctx.all_objects,
                    ctx.all_results_dict, ctx.task_map,
                    ctx.director_output,
                )
                print(f"\n{'='*70}")
                print(f"  📋 [Circuit Breaker] Failure report generated:")
                print(f"  Deadlock Context: Compiler Loop Exhausted (task {tid} failed {count}x)")
                print(f"{'='*70}")
                print(deadlock_report)
                break
        if ctx.review_verdict == "BLOCKED":
            break

        cycle_label = f"Integration Review (cycle {ctx.review_cycle})"

        # Dynamically extract negative intent to insulate the Reviewer against double-binds
        neg_guard_str = ""
        req_parts_rev = re.split(r'\[block\]', ctx.user_prompt, maxsplit=1, flags=re.IGNORECASE)
        if len(req_parts_rev) > 1:
            neg_guard_str = (
                f"\n\n## NEGATIVE INTENT OVERRIDE, AGENTIC EXTENSIBILITY & LIFECYCLE MANDATE\n"
                f"The user explicitly commanded the following avoidance constraints:\n"
                f"\"{req_parts_rev[1].strip()}\"\n"
                f"CRITICAL EVALUATION GUIDANCE:\n"
                f"1. AVOIDANCE EVALUATION: You MUST NOT penalize agents or issue a FAIL verdict for omitting explicitly blocked C++ classes, structs, or files. Treat successful avoidance as a PASS, and emit exactly `- None` under `### Issues` if no other defects exist.\n"
                f"2. CONSTRUCTIVE PRIMITIVE EXTENSIBILITY: If an attraction requires complex physical shapes (e.g., curved ramps, flared rings) that cannot be modeled by simple boxes or cylinders, agents must NEVER invent new C++ classes. Instead, they MUST achieve extensibility via two native pathways:\n"
                f"   A. ASSET MOUNTING: Use `MidwayPhysics.SpawnStaticMesh` or `SpawnDynamicMesh` to delegate custom collision geometry to external 3D asset models.\n"
                f"   B. LUA COMPOSITION: Group multiple foundational primitives inside custom Lua factory tables to construct reusable prefabs entirely at runtime.\n"
                f"3. MANDATORY LUA LIFECYCLE AUDIT: You MUST actively audit Lua attraction scripts for strict event-driven lifecycle compliance. If the script fails to implement `OnLoadStatic()` (invoking `SpawnSharedBooth()`), `OnLoad()` (spawning bodies and registering `MidwayPhysics.OnStep`), or polls modifiers at load time rather than live every frame inside `OnStep(dt)`, you MUST issue an immediate FAIL.\n"
            )

        # ── Review Input: Inline actual code, not just a TOC ──────────────
        # Giving the reviewer only a PAGE_IN index causes it to hallucinate
        # violations from task titles. Inline the real task outputs so it can
        # only flag issues that actually appear in the code.
        #
        # BUDGET STRATEGY: Measure the real non-code overhead first, then
        # divide the remaining space evenly across tasks.  This prevents
        # tail tasks from being silently dropped because the overhead estimate
        # was wrong — which caused the context-collapse failure seen in earlier
        # runs where only task_2 reached the reviewer.
        #
        # Hard cap is set to leave a 1 500-char safety margin below the 16 384
        # absolute truncation limit so the REVIEW_PROMPT and bridge snippet
        # always survive at the end of the packet.
        # Model-aware review hard cap: 55% of context window at 3 chars/token,
        # matching the executor ceiling in _helpers_exec.py so neither path
        # exceeds VRAM before the VRAM Guard fires.
        try:
            from ollama_client import resolve_ctx_size as _rcz
            _review_model = getattr(ctx, 'reviewer_model', 'phi3:14b')
            _REVIEW_CTX = _rcz(_review_model)
        except Exception:
            _REVIEW_CTX = 8192
        _REVIEW_HARD_CAP = int(_REVIEW_CTX * 3 * 0.55)
        _MAX_TASKS_INLINE = 12
        _task_items = list(ctx.all_results_dict.items())[:_MAX_TASKS_INLINE]

        # Defined early so its length is available for the overhead calculation below.
        _visibility_mandate = (
            "## ⚠ REVIEWER SCOPE CONSTRAINT (MANDATORY)\n"
            "You MAY ONLY flag issues that are **visibly present** in the code blocks shown "
            "in the 'Generated Code' section above.\n"
            "You MUST NOT:\n"
            "- Fail a task because a file that was NOT shown is absent or incomplete.\n"
            "- Speculate about code you cannot see.\n"
            "- Invent violations from task titles or Director descriptions alone.\n"
            "- Issue a FAIL verdict for features blocked by an explicit [block] directive.\n"
            "If a task's code block is absent from this review packet, treat it as OUT-OF-SCOPE "
            "for this cycle and do NOT mention it under Issues.\n"
        )

        # Build the non-code frame first so we know its real size.
        _frame_str = (
            f"## Original Feature Request\n{ctx.user_prompt}\n\n"
            f"## Director's Task Breakdown\n{ctx.director_output}\n\n"
            f"{ctx.conflicts_str}"
        )
        _frame_overhead = len(_frame_str) + len(_visibility_mandate) + len(neg_guard_str)
        # Reserve 3 500 chars for bridge snippet + REVIEW_PROMPT at the tail.
        _TAIL_RESERVE = 3500
        _code_budget_total = max(2000, _REVIEW_HARD_CAP - _frame_overhead - _TAIL_RESERVE)
        _task_code_budget = max(400, _code_budget_total // max(1, len(_task_items)))

        inline_code_blocks: list[str] = []
        for _tid, _out in _task_items:
            _task_obj = ctx.task_map.get(_tid)
            _domain = (_task_obj.agent if _task_obj and getattr(_task_obj, 'agent', None) else "?")
            if len(_out) > _task_code_budget:
                _snippet_overflow = _out[_task_code_budget:]
                _snippet = _out[:_task_code_budget]
                # Preserve overflow in OffloadStore so reviewer can PAGE_IN if it
                # needs to see the full output rather than silently losing it.
                try:
                    from offload_store import get_offload_store as _rv_os
                    _rv_store = _rv_os()
                    _rv_oid = f"review_overflow_{_tid}"
                    _rv_store.store_block(
                        block_id=_rv_oid,
                        header=f"Review overflow for {_tid} ({len(_snippet_overflow)} chars truncated)",
                        body_lines=[_snippet_overflow],
                    )
                    _snippet += (
                        f"\n[📄 {len(_snippet_overflow)} chars truncated — "
                        f"use `<invoke_kernel><action>PAGE_IN</action>"
                        f"<target>{_rv_oid}</target></invoke_kernel>` to retrieve full output.]"
                    )
                except Exception:
                    _snippet += "\n…[truncated]"
            else:
                _snippet = _out
            inline_code_blocks.append(f"### [{_tid}] [{_domain}]\n{_snippet}")

        inline_code_str = "\n\n".join(inline_code_blocks)
        _tasks_in_packet = len(_task_items)
        _tasks_total = len(ctx.all_results_dict)
        if _tasks_in_packet < _tasks_total:
            print(f"  [Review Budget] ⚠ Only {_tasks_in_packet}/{_tasks_total} tasks fit inline "
                  f"(budget {_task_code_budget} chars/task, frame {_frame_overhead} chars overhead).")
        else:
            print(f"  [Review Budget] ✅ All {_tasks_in_packet} task(s) inlined "
                  f"({_task_code_budget} chars/task budget).")
        # Inject pre-flight errors as a hard preamble so the reviewer cannot
        # issue PASS while known static violations are still present.
        _preflight_preamble = ""
        if ctx.pre_flight_errors and ctx.pre_flight_errors.strip():
            _pf_collapsed = TokenBudget._block_aware_collapse(ctx.pre_flight_errors, 1500)
            _preflight_preamble = (
                "## ⛔ PRE-FLIGHT VIOLATIONS — PASS IS FORBIDDEN UNTIL THESE ARE RESOLVED\n"
                "The automated pre-flight checker found the following violations in the code below.\n"
                "You MUST issue [VERDICT: FAIL] and list every unresolved violation under Issues.\n"
                "You MAY issue [VERDICT: PASS] ONLY if you can confirm each violation listed here "
                "has been corrected in the code shown.\n\n"
                + _pf_collapsed
                + "\n"
            )

        review_input_raw = (
            f"{_preflight_preamble}"
            f"## Original Feature Request\n{ctx.user_prompt}\n\n"
            f"## Director's Task Breakdown\n{ctx.director_output}\n\n"
            f"{ctx.conflicts_str}"
            f"## Generated Code (review ONLY what is shown below — do NOT invent issues)\n"
            f"{inline_code_str}\n"
            f"{_visibility_mandate}\n"
            f"{neg_guard_str}\n\n"
        )

        # ── Inject bridge contract so reviewer can flag phantom APIs ────────
        # Without this, the reviewer sees phantom names like GetPrizeValue()
        # and cannot distinguish them from real bridge exports.
        # NOTE: _build_bridge_fn() returns a dict — it must be rendered to a
        # human-readable string before slicing or it raises TypeError and the
        # snippet is silently dropped (the previous regression cause).
        _review_bridge_snippet = ""
        _build_bridge_fn = getattr(ctx, '_cartridge_build_bridge_contract', None)
        if callable(_build_bridge_fn):
            try:
                _bc_rev = _build_bridge_fn()
                if _bc_rev and isinstance(_bc_rev, dict):
                    _bc_lines: list[str] = []
                    for _section, _entries in _bc_rev.items():
                        if isinstance(_entries, dict):
                            _bc_lines.append(f"### {_section}")
                            for _name, _desc in _entries.items():
                                _bc_lines.append(
                                    f"  - {_name}: {_desc}"
                                    if isinstance(_desc, str)
                                    else f"  - {_name}"
                                )
                        elif isinstance(_entries, list):
                            _bc_lines.append(f"### {_section}")
                            for _item in _entries:
                                _bc_lines.append(f"  - {_item}")
                    _bc_str = "\n".join(_bc_lines)[:3000]
                    _review_bridge_snippet = (
                        "## Active Bridge Contract — APPROVED APIs (exhaustive list)\n"
                        "Any Lua call that is NOT on this list is a phantom API and MUST be flagged as a FAIL.\n"
                        f"{_bc_str}\n\n"
                    )
                    print(f"  [Bridge Contract] ✅ Injected {len(_bc_str)} chars of approved API list into reviewer context.")
            except Exception as _bc_err:
                print(f"  [Bridge Contract] ⚠ Failed to render bridge contract for reviewer: {_bc_err}")

        # Append tail (bridge + review prompt) AFTER the code body so it always
        # survives.  D14: When truncation is necessary, collapse the frame prose
        # sections first and protect the task code blocks — the reviewer must see
        # real code or it cannot produce a meaningful verdict.
        review_input_full = review_input_raw + _review_bridge_snippet + _prompts_mod.REVIEW_PROMPT
        if len(review_input_full) > 16000:
            _excess = len(review_input_full) - 16000
            print(f"  [VRAM Guard] Review input oversized ({len(review_input_full)} chars) — "
                  f"collapsing {_excess} chars from code body via block-aware paging (tail preserved).")
            _tail = _review_bridge_snippet + _prompts_mod.REVIEW_PROMPT
            # D14: collapse the frame (prose) section first, keeping the task code blocks intact.
            _frame_cap = max(800, len(_frame_str) - _excess - 200)
            _collapsed_frame = TokenBudget._block_aware_collapse(_frame_str, _frame_cap)
            _rebuilt_body = (
                _collapsed_frame
                + f"\n\n[SYSTEM KERNEL: Feature request / director sections collapsed to {_frame_cap} chars "
                f"to protect code blocks below. Bridge contract and review instructions are complete.]\n\n"
                + f"## Generated Code (review ONLY what is shown below — do NOT invent issues)\n"
                + inline_code_str
                + f"\n{_visibility_mandate}\n{neg_guard_str}\n\n"
            )
            # If still oversized after frame collapse, fall back to body collapse preserving tail.
            if len(_rebuilt_body) + len(_tail) > 16000:
                _body_cap = 16000 - len(_tail) - 80
                _rebuilt_body = TokenBudget._block_aware_collapse(_rebuilt_body, _body_cap)
                _rebuilt_body += (
                    "\n\n[SYSTEM KERNEL: Code body further collapsed via block-aware paging. "
                    "Bridge contract and review instructions below are complete.]\n\n"
                )
            review_input = _rebuilt_body + _tail
        else:
            review_input = review_input_full


        # ── Physical Compilation Gate Override ──
        # Skip entirely when no configured build tree exists — running cmake
        # without a cache produces infrastructure noise ("could not load cache")
        # that poisons every fix cycle with meaningless errors.
        _cmake_cache = ctx.project_root / "CMakeCache.txt"
        _infra_noise_re = re.compile(
            r"(could not load cache|no such file or directory.*cmake"
            r"|cmake.*error.*cache|error opening.*cmakecache)",
            re.IGNORECASE,
        )
        if not _cmake_cache.is_file():
            compile_success = True   # treat as pass — nothing to compile yet
            compile_stderr = ""
            print("  [Compile Gate] No CMakeCache.txt — skipping physical compilation check.")
        else:
            compile_success, compile_stderr = compile_project(ctx.project_root)
            # Strip pure cmake-infrastructure lines so only real compiler
            # diagnostics reach the fix agents.
            if compile_stderr and _infra_noise_re.search(compile_stderr):
                clean_lines = [
                    ln for ln in compile_stderr.splitlines()
                    if not _infra_noise_re.search(ln)
                ]
                compile_stderr = "\n".join(clean_lines).strip()
                if not compile_stderr:
                    compile_success = True  # all lines were infra noise

        if not compile_success and compile_stderr:
            ctx.pre_flight_errors = (
                ctx.pre_flight_errors
                + f"\n## Mandatory Compiler Fix Required (truncated to 2,000 chars):\n```\n{compile_stderr[:2000]}\n```"
            )

        if not compile_success:
            print("  [Circuit Breaker] ⛔ Physical compilation failed. Overriding LLM review hallucination.")
            ctx.review_output = "### Verdict\n[VERDICT: FAIL]\n### Issues\nPhysical compilation failed. See compiler logs."
            ctx.review_verdict = "FAIL"
        else:
            from pipeline import REVIEWER_MODEL as _reviewer_model
            ctx.review_output = call_ollama(
                _prompts_mod.REVIEW_SYSTEM, review_input, cycle_label, _reviewer_model,
                skip_pre_summarizer=True
            )
            from ollama_client import is_fatal_ollama_error as _is_fatal_rev
            if _is_fatal_rev(ctx.review_output):
                print(f"  [Review-Fix] ⛔ Ollama error during review — aborting review loop.")
                ctx.review_verdict = "BLOCKED"
                break
            ctx.review_verdict = get_verdict(ctx.review_output)

        ctx.output_parts.append(
            f"### Review Cycle {ctx.review_cycle}\n{ctx.review_output}\n"
        )

        print(f"  [Review-Fix] Verdict: {ctx.review_verdict}")

        if ctx.review_verdict == "PASS":
            # FM3: Hard-gate PASS against open preflight errors.
            # The LLM can emit PASS even when the preflight preamble lists
            # violations — treat any open errors as an automatic FAIL so
            # the fix loop actually runs.
            _open_pf = (ctx.pre_flight_errors or "").strip()
            if _open_pf:
                print(f"  [Review-Fix] ⛔ Reviewer emitted PASS but preflight errors are still open "
                      f"— overriding to FAIL (cycle {ctx.review_cycle}).")
                ctx.review_verdict = "FAIL"
                ctx.review_output = (
                    ctx.review_output
                    + "\n\n[SYSTEM KERNEL: PASS overridden to FAIL — open pre-flight violations "
                    "must be resolved before this run can be approved.]"
                )
            else:
                print(f"  [Review-Fix] Passed on cycle {ctx.review_cycle}")
                break

        # D13/D15: NO_VERDICT means the reviewer output contained no verdict line.
        # Give it one targeted re-prompt before treating as FAIL, so a single
        # formatting slip doesn't burn a full fix cycle unnecessarily.
        if ctx.review_verdict == "UNKNOWN":
            ctx.review_verdict = "NO_VERDICT"
            print(f"  [Review-Fix] ⚠ NO_VERDICT — reviewer produced no verdict line. "
                  f"Issuing one verdict re-prompt before fix routing (cycle {ctx.review_cycle}).")
            _pf_reminder = ""
            if ctx.pre_flight_errors and ctx.pre_flight_errors.strip():
                _pf_collapsed = TokenBudget._block_aware_collapse(ctx.pre_flight_errors, 800)
                _pf_reminder = (
                    "\n\n## ⚠ OPEN PRE-FLIGHT VIOLATIONS — PASS IS FORBIDDEN\n"
                    + _pf_collapsed
                    + "\n"
                )
            _verdict_nudge = (
                ctx.review_output
                + _pf_reminder
                + "\n\n[SYSTEM KERNEL: Your review above contains no verdict line. "
                "You MUST append exactly one of the following on its own line now:\n"
                "[VERDICT: PASS]\n[VERDICT: FAIL]\n"
                "If any pre-flight violations are listed above, you MUST emit [VERDICT: FAIL].\n"
                "No other text on that line. Do NOT repeat your review.]"
            )
            from pipeline import REVIEWER_MODEL as _rv_model2
            _retry_out = call_ollama(
                _prompts_mod.REVIEW_SYSTEM, _verdict_nudge,
                f"Review Verdict Re-prompt (cycle {ctx.review_cycle})", _rv_model2,
                skip_pre_summarizer=True,
            )
            from ollama_client import is_fatal_ollama_error as _is_fatal_vn
            if _is_fatal_vn(_retry_out):
                print(f"  [Review-Fix] ⛔ Ollama error during verdict re-prompt — aborting review loop.")
                ctx.review_verdict = "BLOCKED"
                break
            _retry_verdict = get_verdict(_retry_out)
            if _retry_verdict in ("PASS", "FAIL"):
                ctx.review_verdict = _retry_verdict
                ctx.review_output = _retry_out
                print(f"  [Review-Fix] Re-prompt resolved to {ctx.review_verdict}.")
                if ctx.review_verdict == "PASS":
                    # FM3 (re-prompt path): same hard-gate as the primary PASS check.
                    # The re-prompt model can emit PASS while static guards are still
                    # open — treat open preflight errors as an automatic FAIL override.
                    _open_pf_rp = (ctx.pre_flight_errors or "").strip()
                    if _open_pf_rp:
                        print(f"  [Review-Fix] ⛔ Re-prompt PASS overridden — preflight errors still open "
                              f"(cycle {ctx.review_cycle}).")
                        ctx.review_verdict = "FAIL"
                        ctx.review_output = (
                            ctx.review_output
                            + "\n\n[SYSTEM KERNEL: PASS overridden to FAIL — open pre-flight violations "
                            "must be resolved before this run can be approved.]"
                        )
                    else:
                        break
            else:
                print(f"  [Review-Fix] Re-prompt still produced no verdict — treating as FAIL.")
                ctx.review_verdict = "FAIL"

        if ctx.review_verdict == "FAIL" and ctx.review_cycle < _REVIEW_MAX_ITERATIONS:
            issues_match = re.search(
                r"### Issues\s*\n(.*?)(?=###|\Z)",
                ctx.review_output, re.DOTALL,
            )
            issues_text = (
                issues_match.group(1).strip()
                if issues_match else ctx.review_output[:1000]
            )

            print(f"  [Review-Fix] Review failed — routing critiques to original domain agents...")
            ctx.output_parts.append(
                f"### Domain Agent Fix Cycle {ctx.review_cycle}\n"
            )

            # ── Domain-Aware Fix Loop ─────────────────────────────
            # Instead of using a generic ARCHITECT_FIX_SYSTEM, iterate over
            # failing task IDs and route the Reviewer's critique back to the
            # *original domain agent* so it retains its strict C++/Lua rules.
            #
            # Fix #4: Only route tasks that have OPEN preflight errors.
            # Using `tid in ctx.review_output` is too broad — it matches any task
            # ID mentioned in passing and routes clean tasks through fix agents,
            # burning VRAM cycles and introducing noise that can break passing code.
            # Strategy: first try to build a set of tids with explicit preflight
            # errors; fall back to review-mentioned tids only when that set is empty.
            _pf_error_tids: set = set()
            if ctx.pre_flight_errors:
                for _pf_tid in ctx.all_results_dict:
                    # Preflight errors are headed "— Task <tid> [" so a simple
                    # contains check on the error string is safe and deterministic.
                    if f"Task {_pf_tid}" in ctx.pre_flight_errors or \
                       f"Task {_pf_tid.replace('_', ' ')}" in ctx.pre_flight_errors:
                        _pf_error_tids.add(_pf_tid)

            task_ids_in_review: set = set()
            if _pf_error_tids:
                # Prefer the deterministic preflight-based set.
                task_ids_in_review = _pf_error_tids
            else:
                # No explicit preflight hits — fall back to reviewer-mentioned tasks.
                for tid, _ in ctx.all_results_dict.items():
                    if tid in ctx.review_output or tid.replace("_", " ") in ctx.review_output:
                        task_ids_in_review.add(tid)

            if not task_ids_in_review:
                # Last resort: use all tasks
                task_ids_in_review = set(ctx.all_results_dict.keys())

            domain_fix_outputs = {}
            # Snapshot results BEFORE any fix writes so the post-fix revert has a
            # clean previous-cycle value to fall back to (not the just-written bad one).
            _pre_fix_snapshot = dict(ctx.all_results_dict)
            for tid in sorted(task_ids_in_review):
                task_obj = ctx.task_map.get(tid)
                if task_obj is None:
                    continue

                original_agent_key = resolve_agent_name(task_obj.agent)

                # Use a compact, repair-specific system prompt — NOT the full
                # get_agent_system() which adds mesh/ledger/virtual-memory protocols
                # (~11.8 k chars) and collapses the user payload to ~469 chars.
                _base_review_fix_system = _prompts_mod.ARCHITECT_FIX_SYSTEM
                _rv_domain_prohibitions = ""
                try:
                    _cart_rv = getattr(ctx, 'mounted_cartridge', None)
                    if _cart_rv:
                        _cart_rv_domain = _cart_rv.domains.get(original_agent_key)
                        if _cart_rv_domain:
                            _sp_rv = _cart_rv_domain.system_prompt or ""
                            _rv_domain_prohibitions = "\n\n## Domain Rules (summary)\n" + _sp_rv[:1800]
                    if not _rv_domain_prohibitions:
                        _live_reg_rv = getattr(ctx, 'domain_registry', None) or {}
                        _dreg_rv = _live_reg_rv.get(original_agent_key, {})
                        if isinstance(_dreg_rv, dict) and _dreg_rv.get('system_prompt'):
                            _rv_domain_prohibitions = "\n\n## Domain Rules (summary)\n" + _dreg_rv['system_prompt'][:1800]
                except Exception:
                    pass
                original_agent_system = _base_review_fix_system + _rv_domain_prohibitions

                domain_name = ALL_DOMAINS.get(original_agent_key, {}).get("name", original_agent_key)
                print(f"    Routing critique for {tid} to {domain_name}")

                # ── Directive C: Context-Pruned Fix Payload ──────────────
                # Uses _prune_fix_context() to strip iterative history,
                # provide only current file state + exact domain-relevant errors.
                # Directive B — Safe Auto-Mounting: Pass paged_files_cache so
                # cached text chunks are injected directly, bypassing the
                # catastrophic file_path.read_text() that would pull 40k+ chars.
                # Build bridge contract snippet for fix context so agents
                # use correct API names instead of hallucinating variants.
                _fix_bridge_snippet = ""
                _build_bridge_fn = getattr(ctx, '_cartridge_build_bridge_contract', None)
                if callable(_build_bridge_fn):
                    try:
                        _bc_fix = _build_bridge_fn()
                        _api_fix = list((_bc_fix.get("midwayphysics_spawn_api") or {}).keys())
                        _pool_fix = list((_bc_fix.get("object_pools") or {}).keys())
                        _econ_fix = list((_bc_fix.get("economy_api") or {}).keys())
                        if _api_fix:
                            _fix_bridge_snippet = (
                                "## Active Bridge Contract — APPROVED APIs (exhaustive list)\n"
                                "Use ONLY these exact function names. Any other name is a phantom API.\n"
                                "Physics: " + ", ".join(_api_fix) + "\n"
                                "Pools: " + ", ".join(_pool_fix) + "\n"
                                "Economy: " + ", ".join(_econ_fix) + "\n"
                                "IMPORTANT: SpawnDynamicBall does NOT exist. "
                                "Use SpawnDynamicSphere(lx, ly, lz, radius) for ball-shaped objects.\n"
                                "IMPORTANT: SpawnDynamicBody, ReleaseHandle, RemoveBody, CheckCollision, "
                                "GetLinearVelocity do NOT exist. Use the names listed above only."
                            )
                    except Exception:
                        pass

                agent_fix_input = _prune_fix_context(
                    domain_key=original_agent_key,
                    task_obj=task_obj,
                    review_issues_text=issues_text,
                    pre_flight_errors=ctx.pre_flight_errors,
                    user_prompt=ctx.user_prompt,
                    paged_files_cache=getattr(task_obj, 'paged_files_cache', None),
                    bridge_api_snippet=_fix_bridge_snippet,
                    last_good_output=ctx.all_results_dict.get(tid, ""),
                )

                # Prefer live cartridge domain_registry (populated by mount_cartridge)
                # over the kernel-only ALL_DOMAINS which omits C++/Lua/PHYS entries.
                _live_registry = getattr(ctx, 'domain_registry', None) or {}
                fix_model = (
                    _live_registry.get(original_agent_key, {}).get('model')
                    or ALL_DOMAINS.get(original_agent_key, {}).get('model')
                    or __import__('pipeline').EXECUTION_MODEL
                )
                agent_fix_output = call_ollama(
                    original_agent_system, agent_fix_input,
                    f"{domain_name} (Fix cycle {ctx.review_cycle})",
                    fix_model,
                    skip_pre_summarizer=True,
                )

                from ollama_client import is_fatal_ollama_error as _is_fatal_fix
                if _is_fatal_fix(agent_fix_output):
                    print(f"  [Review-Fix] ⛔ Ollama error during fix for {tid} — skipping, retaining previous output.")
                    continue

                # ── Directive A: Sandbox validation ──────────────────────
                # Reject output if it attempts cross-domain file writes.
                is_clean, safe_output = reject_cross_domain_output(
                    domain_key=original_agent_key,
                    output_text=agent_fix_output,
                    persona_name=domain_name,
                )
                if not is_clean:
                    print(f"  [SANDBOX] ⛔ {domain_name} ({tid}) output rejected — "
                          f"cross-domain file write detected. Using truncated safe stub.")
                    agent_fix_output = safe_output

                # ── Phase III: LangGraph AST Patch State Reducer ──────────────
                # Extract AST_PATCH signals from agent output, validate them,
                # and apply as state-reducing merge operations. Malformed patches
                # are routed through a fast-path micro-model syntax repair pass.
                from signals import AST_PATCH_BLOCK_PATTERN
                ast_patches = list(re.finditer(AST_PATCH_BLOCK_PATTERN, agent_fix_output, re.DOTALL))
                if ast_patches:
                    for p_match in ast_patches:
                        target_path = p_match.group(1).strip()
                        patch_content = p_match.group(2).strip()
                        if target_path and patch_content:
                            # Validate: ensure path has valid extension
                            valid_extensions = (".cpp", ".h", ".hpp", ".lua", ".py", ".json", ".md")
                            if any(target_path.endswith(ext) for ext in valid_extensions):
                                ctx.pending_patches.append({
                                    "task_id": tid,
                                    "domain": original_agent_key,
                                    "target_path": target_path,
                                    "content": patch_content,
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                })
                                print(f"  [AST Reducer] ✅ Queued patch for {target_path} ({tid})")
                            else:
                                # Malformed — route through fast-path syntax repair
                                print(f"  [AST Reducer] ⚠ Malformed patch path '{target_path}' — routing to syntax repair")
                                from pipeline import EXECUTION_MODEL as _syntax_model, SYNTAX_GATE_MODEL as _repair_model
                                repair_model = _repair_model or "qwen2.5-coder:1.5b"
                                repair_prompt = (
                                    f"Repair the following AST patch targeting invalid path '{target_path}'. "
                                    f"Extract the correct target file path and clean up the patch content. "
                                    f"Output as: [AST_PATCH:corrected/path]```code```[/AST_PATCH]\n\n"
                                    f"Raw patch:\n{target_path}\n---\n{patch_content}"
                                )
                                from _pipeline_helpers import call_ollama as _pipeline_call_ollama
                                repair_output = _pipeline_call_ollama(
                                    "You are a syntax repair micro-model. Fix malformed AST patches.",
                                    repair_prompt,
                                    f"AST Syntax Repair ({tid})",
                                    repair_model,
                                )
                                # Re-parse repaired output
                                repair_match = re.search(AST_PATCH_BLOCK_PATTERN, repair_output, re.DOTALL)
                                if repair_match:
                                    repaired_path = repair_match.group(1).strip()
                                    repaired_content = repair_match.group(2).strip()
                                    if repaired_path and repaired_content:
                                        ctx.pending_patches.append({
                                            "task_id": tid,
                                            "domain": original_agent_key,
                                            "target_path": repaired_path,
                                            "content": repaired_content,
                                            "timestamp": datetime.now(timezone.utc).isoformat(),
                                            "repaired": True,
                                        })
                                        print(f"  [AST Reducer] 🔧 Repaired patch for {repaired_path} ({tid})")
                                else:
                                    print(f"  [AST Reducer] ⛔ Could not repair malformed AST patch for {tid}")
                    # After collecting all patches, apply state reducer: merge patches into results dict.
                    # Group patches by (domain, target_path) so earlier valid patches from one domain
                    # are not silently overwritten by a later patch from a different domain targeting
                    # the same file — each domain owns its own file namespace.
                    latest_patches = {}
                    for patch in ctx.pending_patches:
                        tpath = patch["target_path"]
                        patch_domain = patch.get("domain", "")
                        # Key is (domain, path): a C++ patch and a Lua patch to the same file are kept separately
                        merge_key = (patch_domain, tpath)
                        latest_patches[merge_key] = patch  # last one per (domain, path) wins
                    
                    # Apply state-reduced patches directly to all_results_dict
                    for _merge_key, patch in latest_patches.items():
                        tpath = patch["target_path"]
                        content = patch["content"]
                        # Tag the output with AST patch metadata
                        tagged_output = (
                            f"### AST Patch: {tpath}\n"
                            f"**Domain:** {patch.get('domain', '?')}\n"
                            f"**Task:** {patch.get('task_id', '?')}\n"
                            f"**Status:** {'Repaired' if patch.get('repaired') else 'Clean'}\n\n"
                            f"```\n{content}\n```"
                        )
                        # Inject into the dict under the original task's key
                        ctx.all_results_dict[f"{patch['task_id']}_ast_{tpath.replace('/', '_')}"] = tagged_output

                domain_fix_outputs[tid] = agent_fix_output
                ctx.output_parts.append(
                    f"### {domain_name} Fix ({tid})\n{agent_fix_output}\n"
                )

                # Update the result in-place
                ctx.all_results_dict[tid] = agent_fix_output

            # Build a combined fix output for backward compat
            fix_output = "\n\n".join(
                f"### {tid}\n{output}"
                for tid, output in domain_fix_outputs.items()
            )

            # ── Post-Fix Validation: strip tasks whose fix output is still empty ──
            # A fix that consists solely of [DELEGATE], [QUERY:DOC], or prose with
            # no code block must be zeroed out before the next review cycle.  If we
            # let them through, the reviewer sees a task with no code and issues a
            # spurious FAIL that is structurally identical to the previous cycle,
            # tripping the insanity detector or burning the last review iteration.
            import re as _re_postfix
            _code_fence_re = _re_postfix.compile(r"```", _re_postfix.MULTILINE)
            _delegate_only_re = _re_postfix.compile(
                r"^\s*(\[DELEGATE[:\]].{0,120}|\[QUERY:DOC.{0,120}|\[REVISE.{0,80})\s*$",
                _re_postfix.IGNORECASE | _re_postfix.MULTILINE,
            )
            for _ftid, _fout in list(domain_fix_outputs.items()):
                _has_code = bool(_code_fence_re.search(_fout))
                _is_delegate = bool(_delegate_only_re.search(_fout)) and not _has_code
                if _is_delegate or (not _has_code and len(_fout.strip()) < 120):
                    print(f"  [Post-Fix] ⚠ {_ftid} fix output is delegation/empty — retaining previous result.")
                    # Revert to the pre-fix snapshot so the reviewer sees the last real code
                    # rather than prose-only output that guarantees another FAIL.
                    # NOTE: _pre_fix_snapshot was captured before the domain fix loop above.
                    if _ftid in _pre_fix_snapshot:
                        ctx.all_results_dict[_ftid] = _pre_fix_snapshot[_ftid]

            # ── Post-Fix Static Guard Refresh ─────────────────────
            # Re-run the lightweight static pattern guards against the newly
            # written code so ctx.pre_flight_errors reflects the CURRENT state
            # of all_results_dict.  Without this, stale errors from the original
            # code keep the PASS-override gate firing indefinitely, causing the
            # loop to exhaust all cycles and suspend at the tribunal gate even
            # when every real violation has already been corrected by a fix agent.
            try:
                from _finalize_preflight import (
                    _inject_empty_output_errors,
                    _inject_static_pattern_errors,
                    _flush_results_to_workspace,
                )
                _flush_results_to_workspace(ctx)
                ctx.pre_flight_errors = ""
                _inject_empty_output_errors(ctx)
                _inject_static_pattern_errors(ctx)
                if ctx.pre_flight_errors.strip():
                    print(f"  [Post-Fix Preflight] ⚠ Static guard still open after fix cycle "
                          f"{ctx.review_cycle} — routing next review cycle with updated errors.")
                else:
                    print(f"  [Post-Fix Preflight] ✅ All static guards clear after fix cycle "
                          f"{ctx.review_cycle}.")
            except Exception as _pf_refresh_err:
                print(f"  [Post-Fix Preflight] ⚠ Guard refresh failed ({_pf_refresh_err}) — "
                      f"retaining previous pre_flight_errors state.")

            # ── Insanity Detector (similarity-based) ──────────────
            normalized = _normalize_fix_fingerprint(issues_text + ctx.conflicts_str)
            if check_insanity_similarity(normalized, ctx.seen_code_hashes_set, threshold=0.95):
                print(
                    f"\n  [Insanity Detector] ⛔ Infinite fix loop detected! "
                    f"Similar input >95% matches previous cycle — circuit breaker tripped."
                )
                ctx.review_verdict = "BLOCKED"
                break
            ctx.seen_code_hashes_set.add(normalized)
            continue

        break

    # ── Reconciliation Gate (Active Rule Auditor) ─────────────────────────
    # If the Tribunal/Reviewer struggled to reach consensus, trigger an audit
    # to cross-reference ledgers for conflicting rules.
    if ctx.review_verdict != "PASS" and ctx.review_cycle >= _REVIEW_MAX_ITERATIONS:
        print(f"\n{'='*50}")
        print(f"  🔍 RECONCILIATION GATE — Active Rule Auditor")
        print(f"{'='*50}")
        print(f"  Tribunal struggled to reach consensus after {ctx.review_cycle} cycles.")
        # When running as a stream server there is no TTY, so input() would block
        # forever or raise an EOFError and crash the worker thread.  Detect this
        # and fall through to a clean FAIL result instead of raising an exception.
        _has_tty = hasattr(sys.stdin, 'isatty') and sys.stdin.isatty()
        if _has_tty:
            trigger_chime()
            try:
                _audit_choice = input(
                    "  [Reconciliation] Trigger Active Rule Auditor? (Y/n): "
                ).strip().lower()
            except (EOFError, KeyboardInterrupt):
                _audit_choice = "n"
            if _audit_choice not in ("n", "no"):
                print("  [Reconciliation] Auditor triggered — review open preflight errors above.")
        else:
            print(
                "  [Reconciliation] ⚠ No TTY detected (running as server). "
                "Pipeline cannot interactively trigger the Auditor. "
                "Returning FAIL result for client reporting."
            )
        ctx.review_verdict = "FAIL"
        ctx.output_parts.append(
            "\n## ❌ Pipeline Failed — Review did not converge\n"
            f"Tribunal reached max cycles ({ctx.review_cycle}) without consensus.\n"
            "Open preflight errors were still present at cycle limit. "
            "Review the static guard output above and re-run with a more specific task description.\n"
        )

    return ctx
