"""
mesh_finalize.py — Extracted run_code_merge from mesh_loops.py
================================================================
Phases 5-8: Conflict resolution, review-fix, consensus, final approval,
TagSuggester post-processing, and session timeline logging.

Exported as three sub-functions for composability.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from models import PipelineContext, SignalType, MeshSignal, ConsensusResult
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
    generate_failure_report,
)


# ──────────────────────────────────────────────────────────────────────
#  Function 3a: run_code_merge (main orchestrator for Phases 5-8)
# ──────────────────────────────────────────────────────────────────────

def run_code_merge(ctx: PipelineContext) -> PipelineContext:
    """Phases 5-8: Conflict resolution, integration review & fix loop,
    consensus gate, final approval / failure report / suspension,
    and TagSuggester post-processing.

    Returns updated ctx with final_output set to the pipeline result string.
    """
    ctx = _run_conflict_resolution(ctx)
    ctx = _run_review_fix_loop(ctx)
    ctx = _run_consensus_and_finalization(ctx)
    ctx = _run_tagsuggester_post(ctx)
    # Save output snapshot
    _save_output(ctx)
    return ctx


# ── Phase 5: Conflict Resolution ──────────────────────────────────────

def _run_conflict_resolution(ctx: PipelineContext) -> PipelineContext:
    print(f"\n{'='*70}")
    print(f"  Phase 5: Conflict Resolution")
    print(f"{'='*70}")
    ctx.output_parts.append("\n## Phase 5: Conflict Resolution\n")

    ctx.conflict_resolutions = []
    for veto in ctx.all_vetos:
        target = resolve_agent_name(veto["target"])
        from_agent = resolve_agent_name(veto["from"])

        conflict_prompt = (
            f"## VETO Signal\n"
            f"**From:** {ALL_DOMAINS.get(from_agent, {}).get('name', from_agent)}\n"
            f"**Target:** {ALL_DOMAINS.get(target, {}).get('name', target)}\n"
            f"**Reason:** {veto['reason']}\n\n"
            f"## Original Feature Request\n{ctx.user_prompt}\n\n"
            f"## Director's Task Breakdown\n{ctx.director_output}\n\n"
        )

        if veto["task_id"] in ctx.all_results_dict:
            conflict_prompt += (
                f"## Output that triggered VETO\n{ctx.all_results_dict[veto['task_id']]}\n\n"
            )

        conflict_output = call_ollama(
            ALL_DOMAINS["CONF"]["system_prompt"],
            conflict_prompt,
            f"Conflict Resolution: {veto['from']} vs {veto['target']}",
        )
        ctx.conflict_resolutions.append(conflict_output)
        ctx.output_parts.append(
            f"### VETO: {veto['from']} -> {veto['target']}\n{conflict_output}\n"
        )

    for obj in ctx.all_objects:
        target = resolve_agent_name(obj["target"])
        from_agent = resolve_agent_name(obj["from"])

        object_prompt = (
            f"## OBJECT Signal\n"
            f"**From:** {ALL_DOMAINS.get(from_agent, {}).get('name', from_agent)}\n"
            f"**Target:** {ALL_DOMAINS.get(target, {}).get('name', target)}\n"
            f"**Concern:** {obj['concern']}\n\n"
            f"## Original Feature Request\n{ctx.user_prompt}\n\n"
        )

        if obj["task_id"] in ctx.all_results_dict:
            object_prompt += (
                f"## Output that triggered OBJECT\n{ctx.all_results_dict[obj['task_id']]}\n\n"
            )

        object_output = call_ollama(
            ALL_DOMAINS["CONF"]["system_prompt"],
            object_prompt,
            f"Conflict Resolution: {obj['from']} OBJECTS {obj['target']}",
        )
        ctx.conflict_resolutions.append(object_output)
        ctx.output_parts.append(
            f"### OBJECT: {obj['from']} -> {obj['target']}\n{object_output}\n"
        )

    return ctx


# ── Phase 6: Integration Review & Fix Loop ────────────────────────────

def _run_review_fix_loop(ctx: PipelineContext) -> PipelineContext:
    print(f"\n{'='*70}")
    print(f"  Phase 6: Integration Review & Fix Loop")
    print(f"{'='*70}")
    ctx.output_parts.append("\n## Phase 6: Integration Review & Fix Loop\n")

    all_code_str = "\n\n".join(
        [f"### {tid}\n{output}" for tid, output in ctx.all_results_dict.items()]
    )

    print("  [Pre-Flight] Running background compilers...")
    ctx.pre_flight_errors = ""

    # Platform-aware compilation check
    try:
        if sys.platform == "win32":
            cmake_build = subprocess.run(
                ["cmake", "--build", "."],
                capture_output=True, text=True, cwd=ctx.project_root,
                shell=True, timeout=30,
            )
            if cmake_build.returncode != 0:
                err_tail = "\n".join(cmake_build.stderr.splitlines()[-50:])
                ctx.pre_flight_errors += f"\n## C++ Compiler Errors:\n```\n{err_tail}\n```"
        else:
            make_process = subprocess.run(
                ["make", "-j4"], capture_output=True, text=True,
                cwd=ctx.project_root, timeout=30,
            )
            if make_process.returncode != 0:
                err_tail = "\n".join(make_process.stderr.splitlines()[-50:])
                ctx.pre_flight_errors += f"\n## C++ Compiler Errors:\n```\n{err_tail}\n```"
    except subprocess.TimeoutExpired:
        ctx.pre_flight_errors += "\n## Compiler Timeout:\n```\nC++ build timed out after 30s\n```\n"
    except Exception:
        pass

    for lf in ctx.project_root.rglob("*.lua"):
        try:
            lua_proc = subprocess.run(
                ["luac", "-p", str(lf)], capture_output=True, text=True, timeout=30,
            )
            if lua_proc.returncode != 0:
                ctx.pre_flight_errors += (
                    f"\n## Lua Syntax Error in {lf.name}:\n```\n{lua_proc.stderr}\n```"
                )
        except subprocess.TimeoutExpired:
            ctx.pre_flight_errors += (
                f"\n## Lua Syntax Error in {lf.name}:\n```\nluac timed out after 30s\n```\n"
            )
        except Exception:
            pass

    if ctx.pre_flight_errors:
        print("  [Pre-Flight] Syntax errors detected. Forcing Architect Fix Cycle.")
        fix_input = (
            f"Your generated code failed local compilation/syntax checks. "
            f"Fix the following errors:\n{ctx.pre_flight_errors}\n\n"
            f"Previously generated code:\n{all_code_str}"
        )
        all_code_str = call_ollama(
            ARCHITECT_FIX_SYSTEM, fix_input, "Architect Syntax Fix", EXECUTION_MODEL
        )

        # Parse the fixed code blocks and update specific tasks
        for match in re.finditer(
            r"### (task_\d+)\n(.*?)(?=### task_\d+|\Z)", all_code_str, re.DOTALL
        ):
            tid = match.group(1).strip()
            fixed_code = match.group(2).strip()
            if tid in ctx.all_results_dict:
                ctx.all_results_dict[tid] = fixed_code

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
            f"- [{tid}](docs/memory/active_run_ledger.md#{tid}) "
            f"-- use [FETCH:docs/memory/active_run_ledger.md#{tid}]"
        )

    active_ledger_path.parent.mkdir(parents=True, exist_ok=True)
    active_ledger_path.write_text(
        "\n".join(active_ledger_content), encoding="utf-8"
    )
    ctx.active_code_index = (
        "\n".join(active_toc)
        + "\n\n**INSTRUCTIONS:** The generated code is stored in the active ledger. "
        + "You MUST use the `[FETCH:filepath#anchor]` tag to load the code for "
        + "specific tasks to review them against our engine rules."
    )

    ctx.review_output = ""
    ctx.review_verdict = "UNKNOWN"
    ctx.review_cycle = 0

    while ctx.review_cycle < REVIEW_MAX_ITERATIONS:
        ctx.review_cycle += 1
        print(f"\n  [Review-Fix] Cycle {ctx.review_cycle}/{REVIEW_MAX_ITERATIONS}")

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

        # Process Reviewer FETCH signals
        fetch_signals = [
            s for s in extract_signals(ctx.review_output)
            if s["type"] == "FETCH"
        ]
        if fetch_signals:
            print(f"  [Review-Fix] Reviewer fetching indexed memory...")
            fetched_content = handle_fetch_signal(fetch_signals[0]["match"])

            ctx.active_code_index += (
                f"\n\n## Fetched Code Chunk\n{fetched_content}\n"
            )
            ctx.output_parts.append(
                f"*(Reviewer fetched indexed code: {fetch_signals[0]['match']})*\n"
            )
            continue

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

            print(f"  [Review-Fix] Review failed — architect fixing...")
            fix_input = (
                f"## Original Feature Request\n{ctx.user_prompt}\n\n"
                f"## Review Issues (Cycle {ctx.review_cycle})\n{issues_text}\n\n"
                f"{ctx.conflicts_str}"
                f"## Active Code Index\n{ctx.active_code_index}\n\n"
                f"Fix ALL issues listed above. Produce corrected code for all domains. "
                f"Address every issue the Reviewer raised. "
                f"If you believe an issue is a false positive, explain why."
            )
            fix_output = call_ollama(
                ARCHITECT_FIX_SYSTEM, fix_input,
                f"Architect Fix (cycle {ctx.review_cycle})"
            )
            ctx.output_parts.append(
                f"### Architect Fix Cycle {ctx.review_cycle}\n{fix_output}\n"
            )

    # ── Insanity Detector (similarity-based) ──────────────
            normalized = _normalize_fix_fingerprint(fix_input)
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

    return ctx


# ── Phase 7-8: Consensus Gate, Final Approval, Failure Report ────────

def _run_consensus_and_finalization(ctx: PipelineContext) -> PipelineContext:
    # ── Phase 7: Consensus Gate ───────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  Phase 7: Consensus Gate")
    print(f"{'='*70}")
    ctx.output_parts.append("\n## Phase 7: Consensus Gate\n")

    review_passed = (ctx.review_verdict == "PASS")
    has_active_vetos = len(ctx.all_vetos) > 0
    has_recourses = any(
        s["type"] == "RECOURSE"
        for task_list in [t.signals for t in ctx.task_map.values()]
        for s in task_list
    )

    ctx.consensus_checks = {
        "All tasks executed": len(ctx.processed_ids) >= len(ctx.tasks_list),
        "All sub-trees resolved": True,
        "Double-check passed": all(
            not (
                t.double_check
                and t.double_check["unresolved"].strip().lower()
                not in ("none", "n/a", "", "nothing")
            )
            for t in ctx.task_map.values() if t.completed and t.double_check
        ),
        "No active VETOs": not has_active_vetos,
        "Review passed": review_passed,
        "No RECOURSE pending": not has_recourses,
    }

    ctx.all_checks_pass = all(ctx.consensus_checks.values())

    ctx.output_parts.append("### Consensus Checks\n")
    for check, passed in ctx.consensus_checks.items():
        status = "✅" if passed else "❌"
        ctx.output_parts.append(f"- {status} {check}\n")

    # ── Phase 8: Final Approval or Failure Report ─────────────────────────
    if ctx.review_verdict == "BLOCKED":
        _handle_blocked(ctx)
    elif ctx.all_checks_pass:
        _handle_approved(ctx)
    else:
        _handle_failure(ctx)

    # ── Blueprint Step Chaining ───────────────────────────────────────────
    if ctx.all_checks_pass:
        blueprint_path = ctx.project_root / "docs" / "project_blueprint.md"
        if blueprint_path.is_file():
            try:
                bp_content = blueprint_path.read_text(encoding="utf-8")
                next_match = re.search(r"- \[ \] (Task \d+: .+)", bp_content)
                if next_match:
                    next_step = next_match.group(1)
                    ctx.output_parts.append(
                        f"\n### Next Blueprint Step\n"
                        f"**{next_step}** — run with:\n"
                        f"`python pipeline.py \"{next_step}\"`\n"
                    )
                else:
                    ctx.output_parts.append(
                        "\n### Blueprint Complete ✅\n"
                        "All tasks in the blueprint have been executed.\n"
                    )
            except Exception:
                pass

    # ── Session Timeline Log ─────────────────────────────────────────
    agent_list = [
        ALL_DOMAINS.get(resolve_agent_name(t.agent), {}).get("name", t.agent)
        for t in ctx.task_map.values() if t.completed
    ]
    tools_accessed = ", ".join(sorted(set(
        [f"ledger_toc({t.agent})" for t in ctx.task_map.values()] +
        [f"file_context({t.agent})" for t in ctx.task_map.values()] +
        ["director", f"review (x{ctx.review_cycle})"] +
        (["conflict_resolution"] if ctx.all_vetos or ctx.all_objects else [])
    )))

    log_to_session_timeline(
        user_input=ctx.user_prompt,
        agent_assigned=", ".join(agent_list[:5]),
        tools_accessed=tools_accessed[:300],
        final_output=ctx.final_output,
    )

    print(f"\n{'='*70}")
    print(f"  Pipeline Complete — {'APPROVED' if ctx.all_checks_pass else ('SUSPENDED' if ctx.review_verdict == 'BLOCKED' else 'FAILED')}")
    print(f"{'='*70}")

    return ctx


def _handle_blocked(ctx: PipelineContext) -> None:
    print(f"\n{'='*70}")
    print(f"  ⛔ CIRCUIT BREAKER TRIPPED — Suspending Pipeline")
    print(f"{'='*70}")
    ctx.output_parts.append("\n## ⛔ Pipeline Suspended (Circuit Breaker)\n")

    suspend_state = {
        "work_queue": [
            {
                "agent": getattr(t, 'agent', ''),
                "spec": getattr(t, 'spec', ''),
                "parent": getattr(t, 'parent', None),
                "task_id": getattr(t, 'task_id', ''),
                "iteration": getattr(t, 'iteration', 0),
                "completed": getattr(t, 'completed', False),
                "output": getattr(t, 'output', ''),
            }
            for t in ctx.task_map.values()
        ],
        "all_results": ctx.all_results_dict,
        "active_code_index": ctx.active_code_index,
        "director_output": ctx.director_output,
    }

    save_checkpoint(ctx.run_id, "BLOCKED", suspend_state)

    ctx.output_parts.append(
        "⚠️ **CIRCUIT BREAKER TRIPPED** ⚠️\n\n"
        "An infinite loop was detected. The pipeline has been suspended.\n\n"
        "Please manually correct the code below in your editor, then "
        "send me a prompt with your fix to resume.\n\n"
        f"_To resume, use checkpoint ID: `{ctx.run_id}`_\n"
    )
    ctx.output_parts.append(
        f"\n**Suspended code index:**\n\n```\n{ctx.active_code_index[:2000]}\n```\n"
    )
    ctx.final_output = "\n".join(ctx.output_parts)


def _handle_approved(ctx: PipelineContext) -> None:
    print(f"\n{'='*70}")
    print(f"  Phase 8: Final Approval")
    print(f"{'='*70}")
    ctx.output_parts.append("\n## Phase 8: Final Approval\n")

    final_input = (
        f"## Original Feature Request\n{ctx.user_prompt}\n\n"
        f"## Your Task Breakdown\n{ctx.director_output}\n\n"
        f"## Active Code Index\n{ctx.active_code_index}\n\n"
        f"## Integration Review\n{ctx.review_output}\n\n"
        f"Review the complete output. "
        f"State **APPROVED** if everything is satisfactory, "
        f"or **REVISION REQUIRED** with specific changes needed."
    )

    ctx.final_output = call_ollama(
        FINAL_APPROVAL_SYSTEM, final_input,
        "Director (Final Approval)", DIRECTOR_MODEL
    )
    ctx.output_parts.append(ctx.final_output + "\n")

    ctx.output_parts.append("\n---\n## ✅ Pipeline Complete\n")
    ctx.output_parts.append(
        f"**Tasks executed:** {len(ctx.processed_ids)}\n"
    )
    ctx.output_parts.append(
        f"**Review reviews:** {ctx.review_cycle} cycle(s)\n"
    )
    ctx.output_parts.append(
        f"**Review verdict:** {'PASS' if ctx.review_verdict == 'PASS' else 'FAIL'}\n"
    )
    ctx.output_parts.append("**Status:** APPROVED\n")

    if ctx.checkpoint_id:
        ckpt_file = CHECKPOINT_DIR / f"{ctx.checkpoint_id}.json"
        if ckpt_file.is_file():
            ckpt_file.rename(
                CHECKPOINT_DIR / f"{ctx.checkpoint_id}.archived.json"
            )

    ctx.final_output = "\n".join(ctx.output_parts)


def _handle_failure(ctx: PipelineContext) -> None:
    print(f"\n{'='*70}")
    print(f"  Phase 8: Failure Report")
    print(f"{'='*70}")
    ctx.output_parts.append("\n## Phase 8: Failure Report\n")

    failure_report = generate_failure_report(
        ctx.user_prompt, ctx.consensus_checks,
        ctx.all_vetos, ctx.all_objects,
        ctx.all_results_dict, ctx.task_map,
        ctx.director_output,
    )
    ctx.output_parts.append(failure_report + "\n")

    # ── Phase 8b: Lead Producer Scope Post-Mortem ──────────────────────────
    print(f"\n{'='*70}")
    print(f"  Phase 8b: Lead Producer — Scope Post-Mortem")
    print(f"{'='*70}")
    ctx.output_parts.append(
        "\n## Phase 8b: Lead Producer Scope Post-Mortem\n"
    )

    post_mortem_prompt = (
        f"## Original User Prompt\n{ctx.user_prompt}\n\n"
        f"## Director's Task Breakdown\n{ctx.director_output}\n\n"
        f"## Failure Report\n{failure_report}\n\n"
        f"Analyze the failure above. Determine:\n"
        f"1. **TOO_BROAD** — was the original prompt too wide for sub-agents "
        f"(requiring >{SCOPE_FILE_LIMIT} files or >{SCOPE_LINE_LIMIT} lines "
        f"across multiple domains)?\n"
        f"2. **NARROW** — scope was fine, failure was technical "
        f"(model misinterpretation, real code bug, Ollama issue)?\n\n"
        f"If TOO_BROAD, suggest a narrower version of the prompt the user "
        f"could run instead.\n"
        f"If NARROW, recommend the user re-run with more specific constraints "
        f"or check Ollama status.\n\n"
        f"Start with **TOO_BROAD** or **NARROW** on its own line, "
        f"then explain your reasoning."
    )
    post_mortem_output = call_ollama(
        DIRECTOR_SYSTEM,
        post_mortem_prompt,
        "Lead Producer (Scope Post-Mortem)",
        REASONING_MODEL,
    )
    ctx.output_parts.append(post_mortem_output + "\n")

    if ctx.checkpoint_id:
        ckpt_file = CHECKPOINT_DIR / f"{ctx.checkpoint_id}.json"
        if ckpt_file.is_file():
            ckpt_file.rename(
                CHECKPOINT_DIR / f"{ctx.checkpoint_id}.archived.json"
            )

    ctx.final_output = "\n".join(ctx.output_parts)


# ── TagSuggester Post-Processing ──────────────────────────────────────

def _run_tagsuggester_post(ctx: PipelineContext) -> PipelineContext:
    try:
        suggester = TagSuggester()
        tag_suggestions = suggester.analyze(SESSION_TIMELINE_PATH, ctx.run_id)
        if tag_suggestions:
            ctx.output_parts.append("\n## 🏷️ Tag Suggestions (Auto-Detected)\n")
            for tag in tag_suggestions:
                ctx.output_parts.append(f"- {tag}\n")

            checklist_path = (
                ctx.project_root / "docs" / "pipeline_master_checklist.md"
            )
            if checklist_path.is_file():
                try:
                    checklist_content = checklist_path.read_text(encoding="utf-8")
                    tag_section_marker = "### Tag System (Phase 9 — Future)"
                    if tag_section_marker in checklist_content:
                        tag_block = "\n".join(
                            f"  - {tag}" for tag in tag_suggestions
                        )
                        tag_block_full = (
                            f"\n### Tag Suggestions (Run: {ctx.run_id[:20]})\n"
                            f"`{datetime.now().isoformat()}`\n"
                            f"{tag_block}\n"
                        )
                        if "### Tag Suggestions" in checklist_content:
                            checklist_content = re.sub(
                                r"### Tag Suggestions.*?\n(?:  - .*\n)*",
                                tag_block_full,
                                checklist_content,
                            )
                        else:
                            checklist_content += f"\n{tag_block_full}\n"
                        checklist_path.write_text(
                            checklist_content, encoding="utf-8"
                        )
                except Exception as e:
                    print(f"  [TagSuggester] Could not update checklist: {e}")

            ctx.final_output = "\n".join(ctx.output_parts)
            log_to_session_timeline(
                user_input=(
                    f"TagSuggester post-processing (run: {ctx.run_id[:40]})"
                ),
                agent_assigned="TagSuggester",
                tools_accessed=(
                    f"analyze({SESSION_TIMELINE_PATH.name}, {ctx.run_id[:20]}...)"
                ),
                final_output=(
                    "\n".join(tag_suggestions)
                    if tag_suggestions else "No tags suggested"
                ),
            )
            print(f"  [TagSuggester] {len(tag_suggestions)} tag(s) suggested")
    except Exception as e:
        print(f"  [TagSuggester] Error: {e}")
        import traceback
        traceback.print_exc()

    return ctx


# ── Output Saving ─────────────────────────────────────────────────────

def _save_output(ctx: PipelineContext) -> None:
    ctx.final_output = "\n".join(ctx.output_parts)
    output_path = ctx.project_root / f"pipeline_output_{ctx.run_id}.md"
    try:
        output_path.write_text(ctx.final_output, encoding="utf-8")
        print(f"\n  Output saved to: {output_path}")
    except Exception as e:
        print(f"\n  Could not save output: {e}")

    if ctx.snapshot:
        try:
            ctx.snapshot.apply_proposals()
            print(f"  [Snapshot] Applied proposals")
        except Exception as e:
            print(f"  [Snapshot] Apply error: {e}")
