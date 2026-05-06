"""
mesh_loops.py — Extracted iteration loops from run_mesh_pipeline()
==================================================================
Three pure functions: PipelineContext → PipelineContext.

Each function encapsulates one major phase group of the mesh consensus
pipeline, allowing the main orchestrator to thin out to ~800 lines.

All state flows through PipelineContext. No async/await — strictly synchronous.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

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
    TagSuggester, TokenBudget,
    HAS_SNAPSHOT, SnapshotManager,
    generate_failure_report,
)

# Re-export run_code_merge from mesh_finalize for backward compat
from mesh_finalize import run_code_merge

from models import PipelineContext, SignalType, MeshSignal, ConsensusResult


# ──────────────────────────────────────────────────────────────────────
#  Function 1: run_fetches
#  Phases 0.5–3: Lead Producer, GDD Librarian, Project Context, Director
# ──────────────────────────────────────────────────────────────────────

def run_fetches(ctx: PipelineContext) -> PipelineContext:
    """Phase 0.5–3: Scope gate, GDD librarian, file context, director decomposition.

    Returns updated ctx with director_output, gdd_context, project_state, structure,
    and tasks_list populated. Handles resurrection path from BLOCKED checkpoint.
    """
    # ── Resurrection Bypass ──────────────────────────────────────────────────
    # If resuming from a BLOCKED checkpoint, skip Phases 0.5-3 entirely
    # since the director_output, task_map, etc. are already re-hydrated.
    if ctx.resumed_blocked:
        print(f"  [Resurrection Bypass] Skipping Phases 0.5-3. State already re-hydrated from checkpoint.")
        return ctx

    # ── Phase 0.5: Lead Producer (Scope Gate & Auto-Feeder) ───────────────
    blueprint_path = ctx.project_root / "docs" / "project_blueprint.md"

    # Define words that explicitly trigger the Auto-Feeder to pull the next task
    auto_feed_triggers = {"continue", "next", "proceed", "c", "go", "next task"}
    
    is_auto_feed_request = (
        not ctx.user_prompt 
        or ctx.user_prompt.strip().lower() in auto_feed_triggers
    )

    if is_auto_feed_request:
        if blueprint_path.is_file():
            content = blueprint_path.read_text(encoding="utf-8")
            match = re.search(r"- \[ \] (Task \d+: .+)", content)
            if match:
                ctx.user_prompt = match.group(1)
                print(f"  [Lead Producer] Auto-feeding next task: {ctx.user_prompt}")
                new_content = content.replace(f"- [ ] {ctx.user_prompt}", f"- [x] {ctx.user_prompt}", 1)
                blueprint_path.write_text(new_content, encoding="utf-8")
            else:
                print("  [Lead Producer] Blueprint complete. Nothing to do.")
                ctx.final_output = "Blueprint complete."
                return ctx
        else:
            print("  [ERROR] No prompt provided and no blueprint found.")
            ctx.final_output = "Failed to start."
            return ctx
    else:
        # ── Defensive Guard: Detect read-only / informational prompts ──
        # Even if the INFORMATIONAL classifier miscategorized, the Scope Gate
        # should NEVER route a read-only question to the Lead Producer.
        read_only_keywords = [
            "how is", "what is", "explain", "summarize", "status",
            "progress", "tell me about", "describe", "list",
            "show me", "overview", "what are", "how does",
            "what does", "can you tell", "information about",
            "context on", "update on", "report on"
        ]
        prompt_lower = ctx.user_prompt.lower().strip()
        # If the prompt ends with '?' it's a question — never blueprint it
        is_read_only_question = (
            prompt_lower.endswith("?")
            or any(prompt_lower.startswith(kw) for kw in read_only_keywords)
        )
        if is_read_only_question:
            print(f"\n  [Lead Producer] Prompt looks like a read-only question. "
                  f"Passing through to Phase 1 (Librarian) instead of blueprint generation.")
            print(f"  [Lead Producer] Prompt: {ctx.user_prompt[:80]}")
        else:
            scope_prompt = (
                f"Analyze this prompt: '{ctx.user_prompt}'. "
                f"If it requires modifying >{SCOPE_FILE_LIMIT} files or writing "
                f">{SCOPE_LINE_LIMIT} lines, respond strictly with 'TOO_BROAD'. "
                f"Otherwise respond 'NARROW'."
            )
            scope_eval = call_ollama(
                "You are a Lead Producer.", scope_prompt, "Scope Gate", REASONING_MODEL
            )

            if "TOO_BROAD" in scope_eval.upper():
                print(f"\n  [Lead Producer] Scope is TOO BROAD. Generating blueprint...")
                blueprint_prompt = (
                    f"Create a step-by-step markdown blueprint to accomplish: "
                    f"{ctx.user_prompt}. Format as a checklist: "
                    f"'- [ ] Task 1: ...'"
                )
                blueprint = call_ollama(
                    "You are a Lead Producer.", blueprint_prompt,
                    "Blueprint Generation", REASONING_MODEL
                )
                blueprint_path.parent.mkdir(exist_ok=True)
                blueprint_path.write_text(blueprint, encoding="utf-8")
                print(f"  [Lead Producer] Saved to docs/project_blueprint.md.")
                ctx.final_output = "Blueprint created. Run pipeline with no prompt to execute Task 1."
                return ctx

    # ── Phase 1: GDD Librarian ────────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  Phase 1: GDD Librarian")
    print(f"{'='*70}")
    ctx.output_parts.append("\n## Phase 1: GDD Librarian\n")

    ctx.gdd_context = recursive_librarian(ctx.user_prompt)
    if ctx.gdd_context:
        ctx.output_parts.append(ctx.gdd_context + "\n")
    else:
        ctx.output_parts.append("No relevant GDD sections found.\n")

    # ── Phase 2: Project State & File Context ─────────────────────────────
    print(f"\n{'='*70}")
    print(f"  Phase 2: Project Context")
    print(f"{'='*70}")
    ctx.output_parts.append("\n## Phase 2: Project Context\n")

    ctx.project_state = get_project_state()
    ctx.output_parts.append(ctx.project_state + "\n")

    ctx.structure = curate_project_structure(ctx.user_prompt)
    ctx.output_parts.append(ctx.structure + "\n")

    # ── Auto-Fetch Referenced Files ───────────────────────────────────────────
    refs = parse_file_references(ctx.user_prompt)
    refs_block = fetch_referenced_files(refs)
    set_referenced_files_cache(refs_block)
    if refs_block:
        ctx.output_parts.append(
            "### Referenced Files (auto-injected)\n" + refs_block + "\n"
        )
        print(f"  [AutoRef] {len(refs)} file reference(s) parsed and cached for all agents")

    # ── Phase 3: Director ─────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  Phase 3: Director — Task Decomposition")
    print(f"{'='*70}")
    ctx.output_parts.append("\n## Phase 3: Director — Task Decomposition\n")

    director_prompt = build_director_prompt()
    director_input = (
        f"{director_prompt}\n\n---\nUSER REQUEST:\n{ctx.user_prompt}"
    )
    ctx.director_output = call_ollama(
        DIRECTOR_SYSTEM, director_input, "Director", DIRECTOR_MODEL
    )
    ctx.output_parts.append(ctx.director_output + "\n")

    # Parse tasks from Director output
    task_regex = r"### Task (\d+): \[([^\]]+)\] — (.+)"
    ctx.tasks_list = []
    for match in re.finditer(task_regex, ctx.director_output):
        ctx.tasks_list.append({
            "id": match.group(1),
            "domain": match.group(2).strip(),
            "title": match.group(3).strip(),
        })

    if not ctx.tasks_list:
        # Fallback: create a single default task
        ctx.tasks_list.append({"id": "1", "domain": "C++", "title": "Full Implementation"})
        print(f"  [Director] No tasks parsed, created default task")

    print(f"  [Director] Created {len(ctx.tasks_list)} task(s)")

    return ctx


# ──────────────────────────────────────────────────────────────────────
#  Function 2: run_tasks
#  Phase 4: Mesh Execution — work queue processing loop
# ──────────────────────────────────────────────────────────────────────

def run_tasks(ctx: PipelineContext) -> PipelineContext:
    """Phase 4: Build work queue from tasks, process depth-first, handle signals.

    Returns updated ctx with all_results_dict, processed_ids, query_results,
    pending_queries, pending_fetches, and task_map populated.
    """
    # ── Phase 4: Mesh Execution ───────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  Phase 4: Mesh Execution — {len(ctx.tasks_list)} Task(s)")
    print(f"{'='*70}")
    ctx.output_parts.append(
        f"\n## Phase 4: Mesh Execution ({len(ctx.tasks_list)} tasks)\n"
    )

    # Build the work queue
    work_queue = deque()
    ctx.task_map = {}

    for t in ctx.tasks_list:
        task_obj = Task(
            agent=t["domain"],
            spec=t["title"],
            task_id=f"task_{t['id']}",
            parent=None,
        )
        work_queue.append(task_obj)
        ctx.task_map[task_obj.task_id] = task_obj

    # Process work queue (depth-first)
    while work_queue:
        task = work_queue.popleft()

        if task.task_id in ctx.processed_ids and not task.is_query:
            continue

        # Check for query results to inject
        context_extra = ""
        if task.is_query:
            # This is a query being answered — just execute it
            pass
        elif task.parent and task.parent in ctx.query_results:
            context_extra = f"## Answer from Query\n{ctx.query_results[task.parent]}"

        task.context = context_extra

        # Find relevant files for this task (ledger-aware: agent sees own TOC first)
        file_context = ""
        try:
            files = find_relevant_files(task.spec, task.agent)
            file_context = format_file_context(files, domain_key=task.agent)
            # Save originals before Phase 4 modifications
            if ctx.snapshot:
                try:
                    ctx.snapshot.save_originals_from_context(file_context)
                except Exception as e:
                    print(f"  [Snapshot] save_originals_from_context error: {e}")
        except Exception as e:
            print(f"  [FileReader] Error: {e}")

        # Execute the task
        output = execute_task(
            task, ctx.user_prompt, ctx.director_output,
            ctx.all_results_dict, file_context, ctx.gdd_context,
        )

        ctx.all_results_dict[task.task_id] = output
        ctx.processed_ids.add(task.task_id)

        # ── Disk-Write Interceptor: persist domain-agent output to ledger ──
        if not task.is_query and not task.parent:
            try:
                _append_to_ledger(output, task.agent, task.spec)
            except Exception as e:
                print(f"  [LedgerWrite] ⚠ Error appending to ledger: {e}")

        if task.is_query:
            ctx.query_results[task.parent] = output
            print(f"  [Query Tracker] Saved answer for parent task {task.task_id}")

            # If this was a DOC FETCH resolution, re-queue the original agent
            if task.task_id in ctx.pending_fetches:
                original_task = ctx.pending_fetches.pop(task.task_id)
                # Inject the DOC oracle's response as recalled memory
                original_task.context = (original_task.context or "") + "\n" + output
                original_task._fetch_depth = getattr(task, '_fetch_depth', 0)
                original_task.completed = False
                # Re-queue the original task WITHOUT incrementing iteration count
                work_queue.appendleft(original_task)
                print(f"  [FETCH] Injected DOC oracle response into {original_task.agent}, re-queued")

            # Re-queue parent task for QUERY/CONSULT
            if task.task_id in ctx.pending_queries:
                parent_task = ctx.pending_queries.pop(task.task_id)
                parent_task.context = (
                    (parent_task.context or "")
                    + f"\n## Answer from {task.agent}:\n{output}\n"
                )
                parent_task.completed = False
                work_queue.appendleft(parent_task)
                print(f"  [Query Tracker] Injected answer into {parent_task.agent}, re-queued")

        # Process signals from this task
        for signal in task.signals:
            stype = signal["type"]

            if stype == "QUERY":
                # Route query to target agent
                target = resolve_agent_name(signal["target"])
                query_task = Task(
                    agent=target,
                    spec=signal["content"],
                    task_id=f"query_{task.task_id}_{len(ctx.query_results)}",
                    parent=task.task_id,
                    is_query=True,
                )
                work_queue.appendleft(query_task)  # Priority: process before next task
                ctx.pending_queries[query_task.task_id] = task
                print(f"  [Signal] QUERY: {task.agent} -> {target}: {signal['content'][:60]}...")

            elif stype == "DELEGATE":
                # Create sub-task
                target = resolve_agent_name(signal["target"])
                sub_count = sum(1 for t in ctx.task_map.values()
                                if t.parent == task.task_id)
                if sub_count < MAX_SUBTASKS_PER_AGENT:
                    sub_task = Task(
                        agent=target,
                        spec=signal["content"],
                        task_id=f"sub_{task.task_id}_{sub_count + 1}",
                        parent=task.task_id,
                    )
                    work_queue.append(sub_task)
                    ctx.task_map[sub_task.task_id] = sub_task
                    print(f"  [Signal] DELEGATE: {task.agent} -> {target}: {signal['content'][:60]}...")
                else:
                    print(f"  [Signal] DELEGATE SKIPPED: {task.agent} already has {sub_count} sub-tasks (max {MAX_SUBTASKS_PER_AGENT})")

            elif stype == "VETO":
                ctx.all_vetos.append({
                    "from": task.agent,
                    "target": signal["target"],
                    "reason": signal["content"],
                    "task_id": task.task_id,
                })
                print(f"  [Signal] VETO: {task.agent} -> {signal['target']}: {signal['content'][:80]}...")

            elif stype == "OBJECT":
                ctx.all_objects.append({
                    "from": task.agent,
                    "target": signal["target"],
                    "concern": signal["content"],
                    "task_id": task.task_id,
                })
                print(f"  [Signal] OBJECT: {task.agent} -> {signal['target']}: {signal['content'][:80]}...")

            elif stype == "APPROVE":
                ctx.all_approvals_dict[task.agent] = True
                print(f"  [Signal] APPROVE: {task.agent}")

            elif stype == "REVISE":
                target = resolve_agent_name(signal["target"])
                # Re-queue the target task for revision
                revise_task = Task(
                    agent=target,
                    spec=f"Revision requested by {task.agent}: {signal['content']}",
                    task_id=f"revise_{target}_{ctx.consensus_iteration}",
                    parent=task.task_id,
                    iteration=0,
                )
                work_queue.append(revise_task)
                print(f"  [Signal] REVISE: {task.agent} -> {target}: {signal['content'][:60]}...")

            elif stype == "RECOURSE":
                print(f"  [Signal] RECOURSE: {task.agent} -> Director: {signal['content'][:80]}...")
                # Director will handle this in the consensus phase

            elif stype == "CONSULT":
                target = resolve_agent_name(signal["target"])
                consult_task = Task(
                    agent=target,
                    spec=f"Consultation requested by {task.agent}: {signal['content']}",
                    task_id=f"consult_{task.task_id}_{len(ctx.query_results)}",
                    parent=task.task_id,
                    is_query=True,
                )
                work_queue.append(consult_task)
                ctx.pending_queries[consult_task.task_id] = task
                print(f"  [Signal] CONSULT: {task.agent} -> {target}: {signal['content'][:60]}...")

            elif stype == "EXTRACT_SKELETON":
                # Semantic compression: delegate to DOC agent to strip implementation bodies
                block_id = signal.get("content", "")
                if not block_id:
                    print("  [EXTRACT_SKELETON] Empty block_id, skipping")
                    continue
                skeleton_query_spec = (
                    f"Semantic Compression: EXTRACT_SKELETON\n"
                    f"Requesting agent: {task.agent}\n"
                    f"Their current task: {task.spec[:300]}\n"
                    f"Offloaded block_id: {block_id}\n"
                    f"---\n"
                    f"Read the offloaded file corresponding to block_id '{block_id}'. "
                    f"Return ONLY function signatures and class definitions, "
                    f"stripping all implementation bodies. "
                    f"Format as markdown code blocks."
                )
                doc_extract_task = Task(
                    agent="DOC",
                    spec=skeleton_query_spec,
                    task_id=f"doc_extract_{task.task_id}",
                    parent=task.task_id,
                    is_query=True,
                )
                ctx.pending_fetches[doc_extract_task.task_id] = task
                work_queue.appendleft(doc_extract_task)
                print(f"  [Signal] EXTRACT_SKELETON: {task.agent} -> DOC (block_id: {block_id})")

            elif stype == "FETCH":
                # Intercept [FETCH] signal: route through DOC oracle for reasoning.
                fetch_depth = getattr(task, '_fetch_depth', 0)
                if fetch_depth >= 3:
                    max_depth_msg = (
                        "\n## Recalled Memory\n"
                        "**Source:** orchestrator\n"
                        "**Oracle note:** [FETCH ERROR: max recursion depth (3) reached. "
                        "Agent must synthesize from available context.]\n\n"
                    )
                    task.context = (task.context or "") + "\n" + max_depth_msg
                    task.completed = False
                    work_queue.appendleft(task)
                    print(f"  [FETCH] Max depth (3) reached for {task.agent}, returning error")
                    continue

                # Build the DOC oracle query: what memory does the agent need?
                fetch_target = signal.get("content", "")
                doc_query_spec = (
                    f"Memory Oracle: Resolve FETCH request\n"
                    f"Requesting agent: {task.agent}\n"
                    f"Their current task: {task.spec[:300]}\n"
                    f"Their fetch target: {fetch_target}\n"
                    f"---\n"
                    f"As Memory Oracle, evaluate if this fetch target is correct, "
                    f"cross-reference available ledgers, resolve the best section, "
                    f"and return the content formatted as ## Recalled Memory."
                )

                # Queue DOC to resolve the FETCH (NOT the original task yet)
                doc_fetch_task = Task(
                    agent="DOC",
                    spec=doc_query_spec,
                    task_id=f"doc_fetch_{task.task_id}_{fetch_depth}",
                    parent=task.task_id,
                    is_query=True,
                )
                doc_fetch_task._fetch_depth = fetch_depth + 1

                # Store original task — will be re-queued AFTER DOC answers
                ctx.pending_fetches[doc_fetch_task.task_id] = task

                work_queue.appendleft(doc_fetch_task)
                print(f"  [FETCH] Routed to DOC oracle (depth {fetch_depth+1}/3): {fetch_target[:80]}...")

            elif stype == "READ_OFFLOADED":
                block_id = signal.get("content", "")
                if not block_id:
                    print("  [ReadOffloaded] Empty block_id, skipping")
                    continue
                offloaded_content = read_offloaded_file(block_id)
                budget = None  # TokenBudget instance — not tracked in context
                if budget is not None:
                    estimated_cost = len(offloaded_content) // 3
                    available = budget.hard_limit - budget.used
                    if estimated_cost > available:
                        print(f"  [ReadOffloaded] !! Block needs ~{estimated_cost} tokens, "
                              f"only {available} available. Paging out context...")
                        freed = _page_out_context(
                            task.context or "",
                            int((estimated_cost - available) * 3),
                            budget,
                        )
                        if freed > 0:
                            budget.used = max(0, budget.used - freed // 3)
                task.context = (task.context or "") + "\n" + offloaded_content
                task.completed = False
                work_queue.appendleft(task)
                print(f"  [ReadOffloaded] Injected block '{block_id}' into {task.agent}")

        # Check double-check for unresolved items
        if task.double_check and task.double_check["unresolved"]:
            unresolved = task.double_check["unresolved"].strip()
            if unresolved and unresolved.lower() not in ("none", "n/a", "nothing", ""):
                if task.iteration < MAX_ITERATIONS:
                    task.iteration += 1
                    task.completed = False
                    work_queue.appendleft(task)
                    print(f"  [Double-Check] {task.agent} has unresolved items, re-queuing (iter {task.iteration})")

        # Save snapshot after each task
        if ctx.snapshot:
            try:
                persona = ALL_DOMAINS.get(resolve_agent_name(task.agent), {}).get("name", task.agent)
                task_id_num = len(ctx.processed_ids)
                ctx.snapshot.save_agent_output(persona, task_id_num, output)
            except Exception as e:
                print(f"  [Snapshot] Save error: {e}")

    return ctx


# Function 3 is re-exported from mesh_finalize module.
# See the import at the top of this file:
#   from mesh_finalize import run_code_merge


