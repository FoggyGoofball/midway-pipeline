"""
mesh_finalize.py — Facade for Phases 5-8 (Conflict Resolution, Review,
                     Consensus, Final Approval)
======================================================================
Delegates to three sub-modules:
  - _finalize_conflicts  (Phase 5: Conflict Resolution)
  - _finalize_review     (Phase 6: Integration Review & Fix Loop)
  - _finalize_preflight  (Pre-Flight Checks & Architect Fix)

Also retains Phase 7-8 logic (consensus, final approval, failure report,
TagSuggester post-processing, output saving, flush signal).

Exported:
    finalize_mesh(ctx) -> PipelineContext
    run_code_merge(ctx) -> PipelineContext   (backward compat)
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from models import PipelineContext
from _pipeline_helpers import atomic_write_text, generate_failure_report, trigger_chime
from _domain_sandbox import reject_cross_domain_output
from _finalize_conflicts import _run_conflict_resolution
from _finalize_review import _run_review_fix_loop
from pipeline import (
    PROJECT_ROOT, ALL_DOMAINS,
    DIRECTOR_MODEL, REASONING_MODEL, EXECUTION_MODEL,
    REVIEW_MAX_ITERATIONS,
    DIRECTOR_SYSTEM, FINAL_APPROVAL_SYSTEM,
    SCOPE_FILE_LIMIT, SCOPE_LINE_LIMIT,
    CHECKPOINT_DIR, SESSION_TIMELINE_PATH,
    resolve_agent_name, get_agent_system,
    call_ollama,
    save_checkpoint, load_checkpoint,
    _append_to_ledger,
    log_to_session_timeline,
    TagSuggester, TokenBudget,
)

__all__ = ["finalize_mesh"]


# ──────────────────────────────────────────────────────────────────────
#  Public Entry Points
# ──────────────────────────────────────────────────────────────────────

def finalize_mesh(ctx: PipelineContext) -> PipelineContext:
    """Complete Phases 5-8 finalization pipeline.

    Equivalent to run_code_merge() — delegates to sub-modules for
    conflict resolution, review/fix loop, consensus, and post-processing.
    """
    return run_code_merge(ctx)


def run_code_merge(ctx: PipelineContext) -> PipelineContext:
    """Phases 5-8: Conflict resolution, pre-flight negative intent validation,
    integration review & fix loop, observability pass, consensus gate, final approval,
    and TagSuggester post-processing.

    Returns updated ctx with final_output set to the pipeline result string.
    """
    ctx = _run_conflict_resolution(ctx)
    
    # ── Pre-Flight Negative Intent Guardrail ──
    # Actively scan accumulated code blocks to ensure explicitly blocked concepts
    # did not bleed through prior to entering the review loop.
    req_parts = re.split(r'\[block\]', ctx.user_prompt, maxsplit=1, flags=re.IGNORECASE)
    if len(req_parts) > 1:
        blocked_intent = req_parts[1].strip().lower()
        
        # Comprehensive exclusion set of procedural programming keywords, standard instructional verbs,
        # and target attraction variables that must NEVER be treated as blacklisted entities.
        safe_stopwords = {
            "this", "that", "with", "from", "create", "make", "build", "define", "implement",
            "setup", "system", "class", "classes", "struct", "structs", "file", "files",
            "specific", "basic", "game", "module", "modules", "function", "functions",
            "method", "methods", "variable", "variables", "object", "objects", "data",
            "code", "script", "scripts", "engine", "attraction", "attractions", "skeeball",
            "avoid", "avoiding", "using", "use", "expose", "exposing", "only", "inside",
            "outside", "handled", "handling", "rules", "rule", "settings", "setting",
            "layout", "layouts", "hole", "holes", "scoring", "score", "scores", "points",
            "place", "nor", "are", "not", "does", "doesn", "dont", "exclude", "excluded"
        }
        
        # Dynamically incorporate procedural stopwords from mounted cartridge if available
        try:
            if getattr(ctx, 'mounted_cartridge', None) and getattr(ctx.mounted_cartridge, 'procedural_stopwords', None):
                safe_stopwords.update([w.lower() for w in ctx.mounted_cartridge.procedural_stopwords])
        except Exception:
            pass

        blocked_words = {
            w for w in re.findall(r'\b[a-z]{4,}\b', blocked_intent) 
            if w not in safe_stopwords
        }
        
        for tid, code_text in list(ctx.all_results_dict.items()):
            code_lower = code_text.lower()
            found_violations = [w for w in blocked_words if w in code_lower]
            if found_violations:
                print(f"  [Pre-Flight Guard] ⛔ Scope bleed detected in {tid}. Found explicitly blocked terms: {found_violations}. Stripping contaminated block.")
                ctx.all_results_dict[tid] = f"// [PRE-FLIGHT GUARD] Blocked terms {found_violations} excised from generation."
                
    ctx = _run_review_fix_loop(ctx)
    ctx = _run_observability_pass(ctx)
    ctx = _run_consensus_and_finalization(ctx)
    ctx = _run_tagsuggester_post(ctx)
    # Save output snapshot
    _save_output(ctx)
    return ctx


# ── Phase 6b: Observability Instrumentation Pass ──────────────────────

def _run_observability_pass(ctx: PipelineContext) -> PipelineContext:
    """Phase 6b: Independent Observability Instrumentation Pass.
    
    Processes reviewed code blocks to inject mandatory logging. Enforces
    canonical file-level sandboxing using the original task's domain key
    to physically prevent cross-language hallucinations.
    """
    if ctx.review_verdict != "PASS":
        return ctx  # Skip logging injection if core logic failed review

    print(f"\n{'='*70}")
    print(f"  Phase 6b: Independent Observability Pass")
    print(f"{'='*70}")
    ctx.output_parts.append("\n## Phase 6b: Independent Observability Pass\n")

    obs_system = get_agent_system("OBSERVABILITY")

    for tid, current_code in list(ctx.all_results_dict.items()):
        task_obj = ctx.task_map.get(tid)
        if not task_obj:
            continue

        original_domain_key = resolve_agent_name(task_obj.agent)
        
        # Skip read-only domains
        if original_domain_key in ("DOC", "CONF", "TRIBUNAL", "LIBRARIAN", "REVIEWER", "DIRECTOR"):
            continue

        domain_name = ALL_DOMAINS.get(original_domain_key, {}).get("name", original_domain_key)
        print(f"  [Observability] Instrumenting {domain_name} task ({tid})...")

        obs_input = (
            f"## TARGET DOMAIN: {original_domain_key}\n\n"
            f"Review the following working code block. It has passed core review but lacks complete logging.\n"
            f"Inject mandatory function entry and state logging strictly native to the {original_domain_key} domain.\n"
            f"Do NOT alter existing core logic.\n\n"
            f"```\n{current_code}\n```"
        )

        raw_obs_output = call_ollama(
            obs_system, 
            obs_input, 
            f"Observability Pass ({tid})", 
            EXECUTION_MODEL
        )

        # DIRECTIVE A: Canonical File-Level Sandboxing Enforcement
        # Validate output against the original domain (e.g., 'Lua') rather than 'OBSERVABILITY'
        is_clean, safe_output = reject_cross_domain_output(
            domain_key=original_domain_key,
            output_text=raw_obs_output,
            persona_name=f"Observability Auditor targeting {original_domain_key}"
        )

        if not is_clean:
            print(f"  [Observability] ⛔ Language drift or cross-extension write detected for {tid}. Discarding logs and falling back to safe un-instrumented code.")
            ctx.output_parts.append(f"### Observability Pass ({tid}) — REJECTED (Language Drift)\nFallback to un-instrumented working code retained.\n")
            continue

        # Commit instrumented code
        ctx.all_results_dict[tid] = safe_output
        ctx.output_parts.append(f"### Observability Pass ({tid})\n{safe_output}\n")
        
        # Re-sync active code index
        if f"### [{tid}]" in ctx.active_code_index:
            # Rebuild index text block if necessary to reflect logged state
            pass 

    print("  [Observability] ✓ Instrumentation complete.")
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
                next_match = re.search(r"- \[ \]\s*(Task \d+:\s*.+)", bp_content)
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

    # ── Timeline Archiver: [FLUSH] Signal Detection (Task 11) ────────────
    _handle_flush_signal(ctx)

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

    # ── User-Gated Ledger Save (Task 10) ──────────────────────────
    print("\n" + "=" * 50)
    print("  MEMORY ARCHIVE GATE")
    print("=" * 50)
    save_to_memory = input(
        "  Save architecture to memory? (y/N): "
    ).strip().lower()
    if save_to_memory in ("y", "yes"):
        semantic_tag = input(
            "  Enter semantic tag for this entry "
            "(e.g., 'Plinko jackpot feature'): "
        ).strip()
        if not semantic_tag:
            semantic_tag = f"auto_{ctx.run_id[:8]}"
        ledger_entry = (
            f"### [{semantic_tag}]\n"
            f"**Run ID:** {ctx.run_id}\n"
            f"**Date:** {datetime.now().isoformat()}\n"
            f"**User Prompt:** {ctx.user_prompt}\n"
            f"**Director Decomposition:**\n{ctx.director_output}\n"
            f"**Final Output:**\n{ctx.final_output[:2000]}\n"
        )
        for t in ctx.task_map.values():
            if t.completed:
                agent_name = ALL_DOMAINS.get(
                    resolve_agent_name(t.agent), {}
                ).get("name", t.agent)
                _append_to_ledger(ledger_entry, t.agent, t.spec)
        print(f"  [LedgerWrite] ✓ Saved to ledger with tag: [{semantic_tag}]")
    else:
        print("  [LedgerWrite] ⏭ Skipped (user declined)")

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
                        atomic_write_text(
                            checklist_path, checklist_content
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


# ── Human-in-the-Loop Verification Gate ───────────────────────────────

def enforce_human_approval_gate(staged_changes_summary: str) -> bool:
    """
    Halts pipeline execution to display a summary of proposed file modifications
    and demands explicit human authorization before applying edits to the native tree.
    """
    print("\n" + "=" * 70)
    print("  🛑 ULTIMATE HUMAN-IN-THE-LOOP VERIFICATION GATE")
    print("=" * 70)
    print("The orchestration mesh has proposed the following modifications:")
    print(staged_changes_summary)
    print("-" * 70)

    while True:
        try:
            choice = input("\nAuthorize transferring these staged changes to the native source tree? [y/N]: ").strip().lower()
            if choice in ('y', 'yes'):
                print("  [Verification Gate] ✓ Authorization granted. Committing modifications.")
                return True
            elif choice in ('n', 'no', ''):
                print("  [Verification Gate] ⛔ Authorization denied. Modifications remain quarantined in the staging directory.")
                return False
            else:
                print("  Invalid input. Please enter 'y' to approve or 'n' to reject.")
        except (KeyboardInterrupt, EOFError):
            print("\n  [Verification Gate] ⛔ Pipeline interrupted. Defaulting to safe rejection.")
            return False


def _build_staged_changes_summary(ctx: PipelineContext) -> str:
    """Construct a human-readable summary of all staged file modifications
    from the snapshot manifest for presentation at the verification gate."""
    parts = []
    snap = ctx.snapshot
    if not snap:
        return "  (no snapshot manager — no staged changes detected)"

    manifest = snap._manifest if hasattr(snap, '_manifest') else {}
    proposals = manifest.get("proposals", {})
    if not proposals:
        return "  (no proposals staged)"

    parts.append(f"\n  Run ID: {manifest.get('run_id', snap.run_id)}")
    parts.append(f"  Description: {manifest.get('description', '(none)')}")
    parts.append(f"  Created: {manifest.get('created', '?')}")
    parts.append(f"  Files snapshotted: {len(manifest.get('files_snapshotted', []))}")
    parts.append(f"  Tasks executed: {len(manifest.get('tasks', []))}")
    parts.append("")
    parts.append(f"  Proposed file modifications ({len(proposals)} file(s)):")
    parts.append("")

    for rel_path, entries in proposals.items():
        latest = entries[-1] if entries else {}
        task_id = latest.get("task_id", "?")
        persona = latest.get("persona", "?")
        timestamp = latest.get("timestamp", "?")
        content_hash = latest.get("content_hash", "")[:10] if latest.get("content_hash") else "?"
        parts.append(f"    📄 {rel_path}")
        parts.append(f"       Agent: {persona}  (task {task_id})")
        parts.append(f"       Proposed: {timestamp}  [hash={content_hash}...]")
        parts.append(f"       Revision count: {len(entries)}")

        # Include unified diff if available
        try:
            diff_text = snap.generate_diff(rel_path)
            if diff_text.strip():
                # Truncate long diffs to keep gate readable
                diff_lines = diff_text.split("\n")
                if len(diff_lines) > 30:
                    diff_text = "\n".join(diff_lines[:30]) + "\n       ... (diff truncated)"
                parts.append(f"       Diff preview:")
                for line in diff_text.split("\n"):
                    parts.append(f"       {line}")
        except Exception:
            pass
        parts.append("")

    return "\n".join(parts)


# ── Output Saving ─────────────────────────────────────────────────────

def _save_output(ctx: PipelineContext) -> None:
    from ollama_client import _stream_crashed, _retry_counter

    ctx.final_output = "\n".join(ctx.output_parts)
    output_path = ctx.project_root / f"pipeline_output_{ctx.run_id}.md"

    # ── Directive D: Snapshot rollback on stream crash ────────────────
    if _stream_crashed:
        print(f"\n  [Stream Crash] ⛔ Fatal stream failure detected — rolling back snapshot.")
        if ctx.snapshot:
            try:
                ctx.snapshot.revert_all()
                print(f"  [Snapshot] ✓ Reverted all files to pre-task state")
                _retry_counter["attempt"] = 0
                _retry_counter["temperature"] = 0.5
            except Exception as e:
                print(f"  [Snapshot] Revert error: {e}")
        else:
            print(f"  [Snapshot] ⚠ No snapshot manager available — cannot rollback")
    else:
        try:
            atomic_write_text(output_path, ctx.final_output)
            print(f"\n  Output saved to: {output_path}")
        except Exception as e:
            print(f"\n  Could not save output: {e}")

        if ctx.snapshot:
            # ── Human-in-the-Loop Verification Gate ──────────────────
            staged_summary = _build_staged_changes_summary(ctx)
            if enforce_human_approval_gate(staged_summary):
                try:
                    ctx.snapshot.apply_proposals()
                    print(f"  [Snapshot] Applied proposals")
                except Exception as e:
                    print(f"  [Snapshot] Apply error: {e}")
            else:
                print(f"  [Snapshot] ⏭ Skipped — changes quarantined in staging directory.")



# ── Timeline Archiver: [FLUSH] Signal (Task 11) ─────────────────────────

def _handle_flush_signal(ctx: PipelineContext) -> None:
    """Detect [FLUSH] signal in user prompt. When triggered, summarize the
    last 50 entries of session_timeline.md, inject them into
    architecture_ledger.md, and wipe the timeline."""
    flush_detected = False
    if ctx.user_prompt and "[FLUSH]" in ctx.user_prompt.upper():
        flush_detected = True
    if not flush_detected:
        if hasattr(ctx, 'final_output') and ctx.final_output and "[FLUSH]" in ctx.final_output.upper():
            flush_detected = True

    if not flush_detected:
        return

    print(f"\n{'='*50}")
    print(f"  📊 FLUSH SIGNAL DETECTED — Archiving Timeline")
    print(f"{'='*50}")

    timeline_path = ctx.session_timeline_path if hasattr(ctx, 'session_timeline_path') and ctx.session_timeline_path else SESSION_TIMELINE_PATH
    if not timeline_path:
        timeline_path = ctx.project_root / "docs" / "memory" / "session_timeline.md"

    architecture_ledger_path = ctx.project_root / "docs" / "memory" / "architecture_ledger.md"

    if not timeline_path.is_file():
        print(f"  [FLUSH] ⚠ No session_timeline.md found at {timeline_path}")
        return

    try:
        content = timeline_path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"  [FLUSH] ⚠ Could not read timeline: {e}")
        return

    entries = re.split(r'(?=^##\s*Session\s*Event)', content, flags=re.MULTILINE)
    entries = [e.strip() for e in entries if e.strip() and e.strip() != "# Session Timeline"]

    last_50 = entries[-50:] if len(entries) > 50 else entries

    summary_lines = [
        f"### Timeline Archive — {datetime.now().isoformat()}",
        f"**Trigger:** [FLUSH] signal",
        f"**Entries archived:** {len(last_50)} of {len(entries)} total",
        "",
    ]
    for i, entry in enumerate(last_50):
        preview = entry[:400].replace("```", "'''")
        summary_lines.append(f"**Entry {i+1}:**\n{preview}\n---\n")

    summary_block = "\n".join(summary_lines)

    architecture_ledger_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(architecture_ledger_path, "a", encoding="utf-8") as f:
            f.write("\n" + summary_block + "\n")
        print(f"  [FLUSH] ✓ Archived {len(last_50)} entries to {architecture_ledger_path}")
    except Exception as e:
        print(f"  [FLUSH] ⚠ Could not write to architecture ledger: {e}")
        return

    try:
        atomic_write_text(timeline_path, "# Session Timeline\n\n")
        print(f"  [FLUSH] ✓ Timeline wiped — fresh start")
    except Exception as e:
        print(f"  [FLUSH] ⚠ Could not wipe timeline: {e}")
