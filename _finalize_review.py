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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from models import PipelineContext, SignalType, MeshSignal, ConsensusResult
from _pipeline_helpers import atomic_write_text, trigger_chime, generate_failure_report, verify_file_hashes, get_normalized_syntax
from _domain_sandbox import reject_cross_domain_output
from _finalize_preflight import _run_preflight_checks
from pipeline import (
    _CTX, PROJECT_ROOT, MEMORY_DIR, OLLAMA_HOST, MODEL, MAX_ITERATIONS,
    MAX_SUBTASKS_PER_AGENT, REVIEW_MAX_ITERATIONS, MAX_CONSENSUS_ITERATIONS,
    CODER_MODEL, REVIEWER_MODEL, DIRECTOR_MODEL, EXECUTION_MODEL,
    REASONING_MODEL, SYNTAX_GATE_MODEL, CHAT_MODEL,
    ALL_DOMAINS, AGENT_ALIAS_MAP, REASONING_GATE_DOMAINS,
    REASONING_GATE_SYSTEM, SELF_CORRECT_SYSTEM, ARCHITECT_FIX_SYSTEM,
    DIRECTOR_SYSTEM, REVIEW_SYSTEM, REVIEW_PROMPT, CHAT_SYSTEM,
    FINAL_APPROVAL_SYSTEM, DIAGNOSTIC_ORACLE_SYSTEM, INTENT_ROUTER_SYSTEM,
    LIBRARIAN_SYSTEM,
    SCOPE_FILE_LIMIT, SCOPE_LINE_LIMIT,
    CHECKPOINT_DIR, SESSION_TIMELINE_PATH,
    LEDGER_MEMORY_RULE, MESH_AGENT_SYSTEM_EXTENSION,
    Task, resolve_agent_name, get_agent_system,
    execute_task, find_relevant_files, format_file_context,
    classify_intent, is_likely_chat, recursive_librarian,
    get_project_state, curate_project_structure,
    call_ollama, call_ollama_streamed,
    extract_signals, parse_signal, extract_double_check, get_verdict,
    build_director_prompt, build_anchor_toc, get_offload_store,
    parse_file_references, fetch_referenced_files, set_referenced_files_cache,
    save_checkpoint, load_checkpoint,
    handle_fetch_signal, read_offloaded_file, handle_read_offloaded_signal,
    _append_to_ledger, _page_out_context, _normalize_fix_fingerprint,
    log_to_session_timeline, search_memory, ledger_toc,
    check_insanity_similarity,
    TagSuggester, TokenBudget,
    HAS_SNAPSHOT, SnapshotManager,
    PROJECT_ROOT as PIPELINE_PROJECT_ROOT,
)


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

    # 1. Original prompt (condensed to first 200 chars)
    parts.append("## Original Feature Request\n" + user_prompt[:200])

    # 2. Task spec (the original directive given to this agent)
    if task_obj and hasattr(task_obj, 'spec') and task_obj.spec:
        parts.append("## Your Task Specification\n" + task_obj.spec[:500])

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

    # 4. Review issues (heuristic domain filter)
    if review_issues_text:
        domain_keywords = []
        if domain_key == "C++":
            domain_keywords = [
                "c++", "cpp", "engine", "class", "struct",
                "header", "include", "namespace", "template",
                "std::", "virtual", "override", "constexpr",
            ]
        elif domain_key == "PHYS":
            domain_keywords = [
                "physics", "jolt", "box2d", "collision",
                "rigidbody", "body", "joint", "constraint",
                "teleport", "kinematic",
            ]
        elif domain_key == "Lua":
            domain_keywords = [
                "lua", "script", "sol2", "bridge", "onload",
                "onstep", "onunload", "register",
            ]

        lines = review_issues_text.split("\n")
        relevant_lines = [line for line in lines
                          if any(kw in line.lower() for kw in domain_keywords)]

        if relevant_lines:
            parts.append(
                "## Review Issues (Domain-Relevant)\n"
                + "\n".join(relevant_lines[:20])
            )
        else:
            parts.append("## Review Issues\n" + review_issues_text[:300])

    # 5. Domain boundary reminder
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

    while ctx.review_cycle < REVIEW_MAX_ITERATIONS:
        ctx.review_cycle += 1
        print(f"\n  [Review-Fix] Cycle {ctx.review_cycle}/{REVIEW_MAX_ITERATIONS}")

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

        review_input = (
            f"## Original Feature Request\n{ctx.user_prompt}\n\n"
            f"## Director's Task Breakdown\n{ctx.director_output}\n\n"
            f"{ctx.conflicts_str}"
            f"## Active Code Index\n{ctx.active_code_index}\n\n"
            f"{REVIEW_PROMPT}"
        )

        ctx.review_output = call_ollama(
            REVIEW_SYSTEM, review_input, cycle_label
        )

        # NOTE: XML PAGE_IN requests emitted by the Reviewer are now
        # intercepted and resolved mid-stream by the PagingController
        # in ollama_client.py — no dedicated fetch signal loop needed.

        ctx.review_verdict = get_verdict(ctx.review_output)

        ctx.output_parts.append(
            f"### Review Cycle {ctx.review_cycle}\n{ctx.review_output}\n"
        )

        print(f"  [Review-Fix] Verdict: {ctx.review_verdict}")

        if ctx.review_verdict == "PASS":
            print(f"  [Review-Fix] Passed on cycle {ctx.review_cycle}")
            break

        if ctx.review_verdict == "FAIL" and ctx.review_cycle < REVIEW_MAX_ITERATIONS:
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
            task_ids_in_review = set()
            for tid, _ in ctx.all_results_dict.items():
                if tid in ctx.review_output or tid.replace("_", " ") in ctx.review_output:
                    task_ids_in_review.add(tid)

            if not task_ids_in_review:
                # Fallback: use all tasks
                task_ids_in_review = set(ctx.all_results_dict.keys())

            domain_fix_outputs = {}
            for tid in sorted(task_ids_in_review):
                task_obj = ctx.task_map.get(tid)
                if task_obj is None:
                    continue

                original_agent_key = resolve_agent_name(task_obj.agent)
                original_agent_system = get_agent_system(original_agent_key)

                if not original_agent_system:
                    original_agent_system = ARCHITECT_FIX_SYSTEM

                domain_name = ALL_DOMAINS.get(original_agent_key, {}).get("name", original_agent_key)
                print(f"    Routing critique for {tid} to {domain_name}")

                # ── Directive C: Context-Pruned Fix Payload ──────────────
                # Uses _prune_fix_context() to strip iterative history,
                # provide only current file state + exact domain-relevant errors.
                # Directive B — Safe Auto-Mounting: Pass paged_files_cache so
                # cached text chunks are injected directly, bypassing the
                # catastrophic file_path.read_text() that would pull 40k+ chars.
                agent_fix_input = _prune_fix_context(
                    domain_key=original_agent_key,
                    task_obj=task_obj,
                    review_issues_text=issues_text,
                    pre_flight_errors=ctx.pre_flight_errors,
                    user_prompt=ctx.user_prompt,
                    paged_files_cache=getattr(task_obj, 'paged_files_cache', None),
                )

                fix_model = ALL_DOMAINS.get(original_agent_key, {}).get(
                    "model", EXECUTION_MODEL
                )
                agent_fix_output = call_ollama(
                    original_agent_system, agent_fix_input,
                    f"{domain_name} (Fix cycle {ctx.review_cycle})",
                    fix_model,
                )

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
    if ctx.review_verdict != "PASS" and ctx.review_cycle >= REVIEW_MAX_ITERATIONS:
        print(f"\n{'='*50}")
        print(f"  🔍 RECONCILIATION GATE — Active Rule Auditor")
        print(f"{'='*50}")
        print(f"  Tribunal struggled to reach consensus after {ctx.review_cycle} cycles.")
        trigger_chime()
        raise Exception(
            "\n[INTERACTIVE GATE] Tribunal struggles to reach consensus. PIPELINE SUSPENDED. Please reply to this prompt with 'Y' to trigger the Auditor, or 'N' to abort."
        )

    return ctx
