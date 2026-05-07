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
    get_unavailable_domains_text,
    # ── File Hash Locking ────────────────────────────────────────────
    compute_file_hash, save_initial_file_hashes_from_context, verify_file_hashes,
)


# Re-export run_code_merge from mesh_finalize for backward compat
from mesh_finalize import run_code_merge
from _pipeline_helpers import atomic_write_text

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

    blueprint_path = ctx.project_root / "docs" / "project_blueprint.md"

    # Define words that explicitly trigger the Auto-Feeder to pull the next task
    auto_feed_triggers = {"continue", "next", "proceed", "c", "go", "next task"}

    is_auto_feed_request = (
        not ctx.user_prompt
        or ctx.user_prompt.strip().lower() in auto_feed_triggers
    )

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

    if is_auto_feed_request and not blueprint_path.is_file():
        print("  [ERROR] No prompt provided and no blueprint found.")
        ctx.final_output = "Failed to start."
        return ctx

    # ── Phase 1: GDD Librarian (always runs — gathers design context) ──────
    print(f"\n{'='*70}")
    print(f"  Phase 1: GDD Librarian")
    print(f"{'='*70}")
    ctx.output_parts.append("\n## Phase 1: GDD Librarian\n")

    ctx.gdd_context = recursive_librarian(ctx.user_prompt)
    if ctx.gdd_context:
        ctx.output_parts.append(ctx.gdd_context + "\n")
    else:
        ctx.output_parts.append("No relevant GDD sections found.\n")

    # ── Phase 2: Project State & File Context (always runs) ────────────
    print(f"\n{'='*70}")
    print(f"  Phase 2: Project Context")
    print(f"{'='*70}")
    ctx.output_parts.append("\n## Phase 2: Project Context\n")

    ctx.project_state = get_project_state()
    ctx.output_parts.append(ctx.project_state + "\n")

    ctx.structure = curate_project_structure(ctx.user_prompt)
    ctx.output_parts.append(ctx.structure + "\n")

    # ── Auto-Fetch Referenced Files ───────────────────────────────────
    refs = parse_file_references(ctx.user_prompt)
    refs_block = fetch_referenced_files(refs)
    set_referenced_files_cache(refs_block)
    if refs_block:
        ctx.output_parts.append(
            "### Referenced Files (auto-injected)\n" + refs_block + "\n"
        )
        print(f"  [AutoRef] {len(refs)} file reference(s) parsed and cached for all agents")

    # ── Auto-Feeder: extract next task from blueprint (for auto-feed requests) ──
    if is_auto_feed_request:
        if blueprint_path.is_file():
            content = blueprint_path.read_text(encoding="utf-8")
            match = re.search(
                r"^[-\*]?\s*\[ \]\s*(?:Task \d+:\s*)?(.+)",
                content, re.MULTILINE
            )
            if match:
                raw_line = match.group(0)
                task_text = match.group(1)
                ctx.user_prompt = task_text.strip()
                print(f"  [Lead Producer] Auto-feeding next task: {task_text.strip()}")
                new_content = content.replace(raw_line, raw_line.replace("[ ]", "[x]", 1), 1)
                atomic_write_text(blueprint_path, new_content)
            else:
                print("  [Lead Producer] Blueprint complete. Nothing to do.")
                ctx.final_output = "Blueprint complete."
                return ctx

    # ── Phase 0.5: Lead Producer (Scope Gate) — only for fresh prompts ──
    # Runs AFTER GDD/Project State gathering so the model can make informed decisions.
    if not is_auto_feed_request:
        gdd_snippet = ctx.gdd_context[:2000] if ctx.gdd_context else "(no GDD context)"
        state_snippet = ctx.project_state[:2000] if ctx.project_state else "(no project state)"
        if is_read_only_question:
            print(f"\n  [Lead Producer] Prompt looks like a read-only question. "
                  f"Passing through to Phase 1 (Librarian) instead of blueprint generation.")
            print(f"  [Lead Producer] Prompt: {ctx.user_prompt[:80]}")
        else:
            scope_prompt = (
                f"Analyze this prompt: '{ctx.user_prompt}'.\n\n"
                f"## Relevant GDD Context\n{gdd_snippet}\n\n"
                f"## Current Project State\n{state_snippet}\n\n"
                f"Consider whether it requires modifying >{SCOPE_FILE_LIMIT} files or "
                f"writing >{SCOPE_LINE_LIMIT} lines. Think step by step, then conclude "
                f"with [VERDICT: NARROW] or [VERDICT: TOO_BROAD]."
            )
            scope_eval = call_ollama(
                "You are a Lead Producer.", scope_prompt, "Scope Gate", REASONING_MODEL
            )

            verdict_match = re.search(r"\[\s*\*?\*?\s*VERDICT\s*\*?\*?\s*:\s*\*?\*?\s*TOO_BROAD\s*\*?\*?\s*\]", scope_eval, re.IGNORECASE)
            if verdict_match:
                print(f"\n  [Lead Producer] Scope is TOO_BROAD. Generating blueprint...")
                unavailable_text = get_unavailable_domains_text()
                hard_constraints = (
                    f"HARD CONSTRAINTS — Do NOT plan for:\n"
                    f"{unavailable_text}\n\n"
                    f"This is a custom engine using SDL2/OpenGL/Jolt/Box2D/Lua. "
                    f"Never reference Unreal, Unity, Godot, or proprietary engines. "
                    f"If the user asks for features from unavailable domains, "
                    f"substitute with wireframes, debug logging, or standard placeholders."
                )
                blueprint_prompt = (
                    f"Create a step-by-step markdown blueprint to accomplish: "
                    f"{ctx.user_prompt}.\n\n"
                    f"{hard_constraints}\n\n"
                    f"## GDD Context\n"
                    f"{ctx.gdd_context[:3000] if ctx.gdd_context else '(none)'}\n\n"
                    f"## Current Project State\n"
                    f"{ctx.project_state[:2000] if ctx.project_state else '(none)'}\n\n"
                    f"## Unavailable Domains\n"
                    f"{unavailable_text}\n\n"
                    f"## Project Structure\n"
                    f"{ctx.structure[:2000] if ctx.structure else '(none)'}\n\n"
                    f"Base your step-by-step tasks strictly on the provided GDD "
                    f"and current project state. "
                    f"Do NOT include tasks for unavailable domains. "
                    f"Base tasks strictly on the provided GDD and current project state.\n\n"
                    f"Format as a checklist:\n"
                    f"'- [ ] Task 1: ...'"
                )
                blueprint = call_ollama(
                    "You are a Lead Producer.", blueprint_prompt,
                    "Blueprint Generation", REASONING_MODEL
                )
                blueprint_path.parent.mkdir(exist_ok=True)
                atomic_write_text(blueprint_path, blueprint)
                print(f"  [Lead Producer] Saved to docs/project_blueprint.md.")

                # ── Blueprint Gate: user must approve before execution ──
                print(f"\n{'='*50}")
                print(f"  BLUEPRINT GATE — Review the architectural blueprint")
                print(f"  Location: {blueprint_path}")
                print(f"{'='*50}")
                approval = input("  Do you approve this architectural blueprint? [Y/n]: ").strip().lower()
                if approval in ("n", "no"):
                    print(f"\n  [Blueprint Gate] Paused. Edit the blueprint at:")
                    print(f"    {blueprint_path}")
                    input("  Press Enter to continue after editing... ")
                    print(f"  [Blueprint Gate] Continuing with updated blueprint.\n")

                # ── Continuous Execution: extract first task & fall through ──
                content = blueprint_path.read_text(encoding="utf-8")
                first_match = re.search(
                    r"^[-\*]?\s*\[ \]\s*(?:Task \d+:\s*)?(.+)",
                    content, re.MULTILINE
                )
                if first_match:
                    raw_line = first_match.group(0)
                    task_text = first_match.group(1).strip()
                    ctx.user_prompt = task_text
                    new_content = content.replace(raw_line, raw_line.replace("[ ]", "[x]", 1), 1)
                    atomic_write_text(blueprint_path, new_content)
                    print(f"  [Lead Producer] Auto-feeding first task: {task_text}")
                    print(f"  [Lead Producer] Continuing to Phase 3...")
                else:
                    print("  [Lead Producer] Blueprint generated but no tasks found — continuing with original prompt.")

    # ── Phase 3: Director ─────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  Phase 3: Director — Task Decomposition")
    print(f"{'='*70}")
    ctx.output_parts.append("\n## Phase 3: Director — Task Decomposition\n")

    director_prompt = build_director_prompt()
    # ── Director Context: Inject GDD context and project state ──
    gdd_snippet = ctx.gdd_context[:2500] if ctx.gdd_context else "(no GDD context)"
    state_snippet = ctx.project_state[:2000] if ctx.project_state else "(no project state)"
    director_input = (
        f"{director_prompt}\n\n"
        f"## Relevant GDD Context\n{gdd_snippet}\n\n"
        f"## Current Project State\n{state_snippet}\n\n"
        f"---\nUSER REQUEST:\n{ctx.user_prompt}"
    )
    ctx.director_output = call_ollama(
        DIRECTOR_SYSTEM, director_input, "Director", DIRECTOR_MODEL
    )
    ctx.output_parts.append(ctx.director_output + "\n")

    # ── Pro Mode: MATH_HEAVY Gate ──────────────────────────────────────
    if re.search(r"\[\s*\*?\*?\s*MATH_?HEAVY\s*\*?\*?\s*\]", ctx.director_output, re.IGNORECASE):
        print(f"\n{'='*50}")
        print(f"  MATH_HEAVY DETECTED — Complex 3D math / physics request")
        print(f"{'='*50}")
        warning_msg = (
            "It looks like a lot of complex math needs to be calculated. "
            "Should we turn on Pro Mode for rigorous test-driven consensus? [y/N]: "
        )
        user_input = input(f"  {warning_msg}").strip().lower()
        if user_input in ("y", "yes"):
            ctx.pro_mode = True
            print(f"  [Pro Mode] ENABLED — TDD guardrails, multi-draft consensus, and test compilation active.")
        else:
            print(f"  [Pro Mode] Declined — continuing in standard mode.")

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

        # ── Sibling Rolling Context ──────────────────────────────────
        # Aggregate outputs of all previously completed sibling tasks
        # (tasks with the same parent, or top-level tasks from same run).
        sibling_parts = []
        for completed_id, completed_output in ctx.all_results_dict.items():
            # Skip the current task itself and non-sibling tasks
            if completed_id == task.task_id:
                continue
            completed_task = ctx.task_map.get(completed_id)
            if completed_task is None:
                continue
            # Consider siblings: same parent (or both top-level with parent=None)
            same_parent = (completed_task.parent == task.parent)
            if same_parent:
                agent_name = ALL_DOMAINS.get(
                    resolve_agent_name(completed_task.agent), {}
                ).get("name", completed_task.agent)
                sibling_parts.append(
                    f"### Sibling Output: {agent_name} ({completed_id})\n"
                    f"{completed_output[:800]}"
                )
        sibling_context = ""
        if sibling_parts:
            sibling_context = (
                "## Previously Completed Sibling Tasks\n"
                + "\n\n".join(sibling_parts)
            )
            print(f"  [Sibling Context] Injected {len(sibling_parts)} sibling output(s) for {task.task_id}")

        # ── Pro Mode: Adversarial TDD (Test-First Generation) ────────
        # If ctx.pro_mode is active, route the task to the 14B Reviewer/Director
        # model first to write a failing gtest, then inject that test into the
        # Coder's prompt as a "here is the test, make it pass" constraint.
        pro_test_injection = ""
        if ctx.pro_mode:
            print(f"\n  [Pro Mode] Adversarial TDD: Routing {task.task_id} to Lead Architect for test generation...")
            test_writer_system = (
                "You are the Lead Test Architect. Your ONLY job is to write "
                "a C++ Google Test (gtest) unit test that PROVES the expected "
                "math, logic, or behavior described in the task specification. "
                "Output ONLY the gtest source code as a single code block. "
                "Do NOT modify any existing files. Do NOT write implementation code. "
                "The test MUST fail initially (the implementation doesn't exist yet)."
            )
            test_writer_prompt = (
                f"## Task Specification\n{task.spec}\n\n"
                f"## User's Feature Request\n{ctx.user_prompt}\n\n"
                f"## Director's Task Breakdown\n{ctx.director_output}\n\n"
                f"---\n"
                f"Write a C++ Google Test (gtest) that asserts the expected "
                f"behavior, math, or logic for this task. The test should define "
                f"the function signatures and expected values that the implementation "
                f"must satisfy. Output ONLY the gtest code in a ```cpp block. "
                f"Use EXPECT_EQ, EXPECT_NEAR, ASSERT_TRUE, etc. as appropriate. "
                f"If the task involves physics math, include numerical tolerance checks "
                f"with EXPECT_NEAR."
            )
            test_code = call_ollama(
                test_writer_system,
                test_writer_prompt,
                f"Test Architect ({task.task_id})",
                REVIEWER_MODEL,  # 14B model
            )
            # Extract test code from code block
            test_match = re.search(r"```(?:cpp)?\s*\n(.*?)```", test_code, re.DOTALL)
            test_body = test_match.group(1).strip() if test_match else test_code.strip()
            # Save the test file
            test_dir = ctx.project_root / "tests"
            test_dir.mkdir(parents=True, exist_ok=True)
            test_file_path = test_dir / f"test_{task.task_id}.cpp"
            atomic_write_text(test_file_path, test_body)
            print(f"  [Pro Mode] Adversarial TDD: Saved test to {test_file_path}")
            # Build the injection string for the Coder
            pro_test_injection = (
                f"\n\n---\n"
                f"## ADVERSARIAL TDD: Failing Unit Test Written by Lead Architect\n"
                f"Here is a failing unit test written by the Lead Architect:\n"
                f"```cpp\n{test_body}\n```\n"
                f"Write the C++ implementation to make this test pass. "
                f"You are strictly forbidden from modifying the test file at `{test_file_path}`."
            )

        # ── Pro Mode: Multi-Draft Generation (N-Version Consensus) ────
        # If ctx.pro_mode is True and the task is PHYS (complex math/physics),
        # generate 3 drafts at different temperatures, then run them
        # through a Tribunal agent to synthesize a master file.
        if ctx.pro_mode and resolve_agent_name(task.agent) == "PHYS":
            print(f"\n  [Pro Mode] N-Version Consensus: Generating 3 drafts for {task.task_id}...")
        
            temperatures = [0.2, 0.5, 0.8]
            drafts = {}

            for i, temp in enumerate(temperatures):
                draft_label = f"{chr(65+i)} (t={temp})"
                print(f"    Draft {draft_label}...")
                draft_output = execute_task(
                    task, ctx.user_prompt, ctx.director_output,
                    ctx.all_results_dict, file_context, ctx.gdd_context,
                    sibling_context=sibling_context,
                    ollama_params={"temperature": temp},
                )
                drafts[f"draft_{chr(65+i)}"] = draft_output

            # ── Tribunal Merge (Task 9) ──────────────────────────────
            print(f"  [Pro Mode] Tribunal merging 3 drafts for {task.task_id}...")

            tribunal_director_system = (
                "You are the TRIBUNAL ARCHITECT. Your role is to evaluate "
                "three different mathematical/physics approaches to the same "
                "problem, discard hallucinations and incorrect logic, and "
                "output a single, synthesized master solution.\n\n"
                "CRITICAL RULES:\n"
                "1. Identify and discard any hallucinated API calls or impossible physics.\n"
                "2. Cross-validate assertions: if only one draft makes a claim, it's likely a hallucination.\n"
                "3. If two drafts agree on an approach, preserve that consensus.\n"
                "4. Output ONLY the merged, synthesized code — no commentary, no evaluation report.\n"
                "5. Use SEARCH/REPLACE diff format if modifying existing files, or full file content for new files."
            )

            tribunal_prompt = (
                f"## Original Task Specification\n{task.spec}\n\n"
                f"## User's Feature Request\n{ctx.user_prompt}\n\n"
                f"## Director's Task Breakdown\n{ctx.director_output}\n\n"
                f"## Draft A (temperature=0.2 — conservative)\n{drafts['draft_A'][:2000]}\n\n"
                f"## Draft B (temperature=0.5 — balanced)\n{drafts['draft_B'][:2000]}\n\n"
                f"## Draft C (temperature=0.8 — creative)\n{drafts['draft_C'][:2000]}\n\n"
                f"---\n"
                f"Evaluate the three approaches. Discard hallucinations. "
                f"Cross-validate mathematical assertions. "
                f"Output a single, synthesized master solution file."
            )

            tribunal_output = call_ollama(
                tribunal_director_system,
                tribunal_prompt,
                f"Tribunal Merge ({task.task_id})",
                DIRECTOR_MODEL,
            )

            # Save the synthesized master file as the final output
            output = tribunal_output
            print(f"  [Pro Mode] Tribunal merge complete for {task.task_id}")

        else:
            # Standard single-draft execution
            # ── Blind Coder: Inject Adversarial TDD test into coder's prompt ──
            if pro_test_injection:
                task.context = (task.context or "") + pro_test_injection
            output = execute_task(
                task, ctx.user_prompt, ctx.director_output,
                ctx.all_results_dict, file_context, ctx.gdd_context,
                sibling_context=sibling_context,
            )
        


        ctx.all_results_dict[task.task_id] = output
        ctx.processed_ids.add(task.task_id)

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
                # Circuit Breaker: increment retry count for the vetoed task
                veto_target = resolve_agent_name(signal["target"])
                veto_target_tid = f"task_{veto_target}"
                ctx.retry_counts[veto_target_tid] = ctx.retry_counts.get(veto_target_tid, 0) + 1
                print(f"  [Signal] VETO: {task.agent} -> {signal['target']}: {signal['content'][:80]}...")
                print(f"  [Circuit Breaker] {veto_target_tid} retry_count incremented to {ctx.retry_counts[veto_target_tid]} (VETO issued)")
        

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

            elif stype == "APPEAL":
                # Appellate Court Protocol (Task 3): Route appeal to Tribunal
                appeal_target = resolve_agent_name(signal["target"])
                appeal_defense = signal["content"]
                print(f"  [Signal] APPEAL: {task.agent} appeals {appeal_target}'s VETO: {appeal_defense[:80]}...")

                # Find the matching VETO to pair with this appeal
                matched_veto = None
                for v in reversed(ctx.all_vetos):
                    if v["target"] == appeal_target or v["from"] == appeal_target:
                        matched_veto = v
                        break
                if matched_veto is None:
                    # Also check OBJECTs
                    for o in reversed(ctx.all_objects):
                        matched_veto = {"from": o["from"], "target": o["target"],
                                        "reason": o["concern"], "task_id": o.get("task_id", "")}
                        break

                # Store the appeal and target task context for Tribunal resolution
                ctx.pending_appeals.append({
                    "appellant": task.agent,
                    "respondent": appeal_target if matched_veto is None else matched_veto["from"],
                    "defense": appeal_defense,
                    "veto_reason": matched_veto["reason"] if matched_veto else "",
                    "veto_task_id": matched_veto["task_id"] if matched_veto else "",
                    "task_spec": task.spec,
                })

                # Queue a Tribunal agent to blind-review
                tribunal_spec = (
                    f"APPELLATE REVIEW\n"
                    f"Appellant: {task.agent}\n"
                    f"Respondent: {appeal_target if matched_veto is None else matched_veto['from']}\n"
                    f"---\n"
                    f"**Coder's Defense:** {appeal_defense}\n"
                    f"**VETO Justification:** {matched_veto['reason'] if matched_veto else '(from OBJECT)'}\n"
                    f"**Task Spec:** {task.spec}\n"
                    f"---\n"
                    f"As TRIBUNAL agent, perform a blind-review of the dispute. "
                    f"Do NOT consider which agent produced which content. "
                    f"Judge solely on technical merit and feature intent. "
                    f"Issue a binding verdict: [MERGE:Tribunal:<justification>] "
                    f"or [REJECT:Tribunal:<justification>]."
                )
                tribunal_task = Task(
                    agent="TRIBUNAL",
                    spec=tribunal_spec,
                    task_id=f"tribunal_{task.task_id}",
                    parent=task.task_id,
                    is_query=True,
                )
                work_queue.appendleft(tribunal_task)
                ctx.pending_queries[tribunal_task.task_id] = task
                print(f"  [APPEAL] Tribunal task queued for blind-review of {task.agent}'s appeal")

            elif stype == "MERGE":
                # Tribunal MERGE verdict — overrule the VETO, keep the output
                print(f"  [Signal] MERGE (Tribunal verdict — VETO overruled): {signal['content'][:80]}...")
                # Store the verdict so finalizer knows it's binding
                ctx.tribunal_verdicts[task.agent] = f"MERGE:{signal['content']}"

                # Remove the matching VETO from all_vetos since it's been overruled
                # The finalizer will check tribunal_verdicts and skip blocked VETOs
                veto_task_id = signal.get("content", "")
                ctx.all_vetos = [
                    v for v in ctx.all_vetos
                    if not (v.get("task_id") and veto_task_id and
                            veto_task_id in v.get("task_id", ""))
                ]

            elif stype == "REJECT":
                # Tribunal REJECT verdict — uphold the VETO, output must be fixed
                print(f"  [Signal] REJECT (Tribunal verdict — VETO upheld): {signal['content'][:80]}...")
                ctx.tribunal_verdicts[task.agent] = f"REJECT:{signal['content']}"

            elif stype == "MATH_EVAL":
                # Math Oracle: safely execute embedded Python math code via subprocess
                math_code = signal.get("content", "").strip()
                if math_code:
                    print(f"  [MATH_EVAL] Executing: {math_code[:120]}...")
                    try:
                        proc = subprocess.run(
                            ["python", "-c", math_code],
                            capture_output=True, text=True, timeout=15.0,
                        )
                        stdout = proc.stdout.strip()
                        stderr = proc.stderr.strip()
                        if proc.returncode == 0 and stdout:
                            proof_injection = (
                                f"\n## Math Oracle Result\n"
                                f"**Numerical Proof:**\n```\n{stdout}\n```\n"
                            )
                            task.context = (task.context or "") + proof_injection
                            task.completed = False
                            work_queue.appendleft(task)
                            print(f"  [MATH_EVAL] Proof injected into {task.agent}")
                        else:
                            error_msg = (
                                f"\n## Math Oracle Error\n"
                                f"**Execution failed (exit code {proc.returncode}):**\n"
                                f"```\n{stderr or '(no stderr)'}\n```\n"
                            )
                            task.context = (task.context or "") + error_msg
                            task.completed = False
                            work_queue.appendleft(task)
                            print(f"  [MATH_EVAL] Execution failed for {task.agent}: {stderr[:100]}")
                    except subprocess.TimeoutExpired:
                        timeout_msg = (
                            "\n## Math Oracle Error\n"
                            "**Execution timed out after 15s.**\n"
                        )
                        task.context = (task.context or "") + timeout_msg
                        task.completed = False
                        work_queue.appendleft(task)
                        print(f"  [MATH_EVAL] Timeout for {task.agent}")
                    except Exception as e:
                        exc_msg = (
                            f"\n## Math Oracle Error\n"
                            f"**Exception during execution:** {e}\n"
                        )
                        task.context = (task.context or "") + exc_msg
                        task.completed = False
                        work_queue.appendleft(task)
                        print(f"  [MATH_EVAL] Exception for {task.agent}: {e}")

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


