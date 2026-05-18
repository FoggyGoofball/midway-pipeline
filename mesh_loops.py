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
from _pipeline_helpers import search_memory

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
    find_relevant_files, format_file_context,
    classify_intent, is_likely_chat, recursive_librarian,
    get_project_state, curate_project_structure,
    call_ollama, call_ollama_streamed,
    extract_signals, parse_signal, extract_double_check, get_verdict,
    build_director_prompt, build_anchor_toc, get_offload_store,
    parse_file_references, fetch_referenced_files, set_referenced_files_cache,
    save_checkpoint, load_checkpoint,
    handle_fetch_signal, read_offloaded_file, handle_read_offloaded_signal,
    _append_to_ledger, _page_out_context, _normalize_fix_fingerprint,
    log_to_session_timeline, ledger_toc,
    TagSuggester, TokenBudget,
    HAS_SNAPSHOT, SnapshotManager,
    get_unavailable_domains_text,
)


# Re-export run_code_merge from mesh_finalize for backward compat
from mesh_finalize import run_code_merge
from _pipeline_helpers import (
    atomic_write_text, trigger_chime,
    compute_file_hash, save_initial_file_hashes_from_context, verify_file_hashes,
    execute_task, generate_failure_report,
)
from ollama_client import unload_model
from auditor import harvest_approved_tags, scan_for_conflicts, generate_audit_report

from models import PipelineContext, SignalType, MeshSignal, ConsensusResult


# ── DAG Wave Sorter (Topological Sort) ────────────────────────────────────

def sort_tasks_into_waves(tasks_list: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """Topological sort: group tasks into waves of independent tasks.

    Each task dict may have a 'depends_on' key (list of task IDs it depends on).
    Groups tasks into waves where all tasks in a wave can run in parallel.

    Args:
        tasks_list: List of task dicts with 'id', 'domain', 'title', and optional 'depends_on'.

    Returns:
        List of waves, where each wave is a list of task dicts that can run in parallel.
    """
    # Build dependency graph
    task_ids = {t["id"] for t in tasks_list}
    remaining = set(task_ids)
    waves: List[List[Dict[str, Any]]] = []

    # Map id -> task
    task_map = {t["id"]: t for t in tasks_list}

    # Track completed tasks
    completed: Set[str] = set()

    while remaining:
        wave = []
        for tid in sorted(remaining):
            task = task_map[tid]
            deps = task.get("depends_on", [])
            # A dependency is satisfied if it's in completed or doesn't exist in tasks
            satisfied = all(d in completed or d not in task_ids for d in deps)
            if satisfied:
                wave.append(task)

        if not wave:
            # Circular dependency or all remaining tasks depend on each other
            # Fallback: add all remaining as a single wave
            wave = [task_map[tid] for tid in sorted(remaining)]
            waves.append(wave)
            break

        waves.append(wave)
        for task in wave:
            remaining.discard(task["id"])
            completed.add(task["id"])

    return waves


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

    # ── Domain Consultant (Dichotomy Gate / "Neon Arcade" Gate) ──────────
    # Scans user prompt for keywords related to dormant/unavailable domains.
    # If a dichotomy is found, chime and prompt the user for wireframe mode.
    if not is_read_only_question and not is_auto_feed_request:
        # Define keywords that trigger the domain consultant
        dormant_domain_keywords = {
            "shader": ["shader", "glsl", "bloom", "particle", "karmic", "temporal", "fragment", "vertex"],
            "neon": ["neon", "neon glow", "neon lighting", "neon sign"],
            "multiplayer": ["multiplayer", "networking", "network", "online", "pvp", "co-op", "multiplayer"],
            "audio": ["audio", "sound", "music", "sfx", "soloud", "beep", "chime", "sound effect"],
            "save": ["save", "load", "persist", "save/load", "checkpoint", "persistence"],
        }
        unavailable_text = get_unavailable_domains_text()
        unavailable_lower = unavailable_text.lower()
        found_dormant_domains = []
        prompt_lower_check = ctx.user_prompt.lower()
        for domain_name, keywords in dormant_domain_keywords.items():
            for kw in keywords:
                if kw in prompt_lower_check and domain_name in unavailable_lower:
                    found_dormant_domains.append(domain_name)
                    break
        if found_dormant_domains:
            trigger_chime()
            print(f"\n{'='*50}")
            print(f"  🔮 DOMAIN CONSULTANT — Neon Arcade Gate")
            print(f"{'='*50}")
            print(f"  Your prompt references features from unavailable domain(s):")
            for d in sorted(set(found_dormant_domains)):
                print(f"    - {d}")
            
            prompt_lower_check = ctx.user_prompt.lower()
            # Rigorous block and semantic mitigation check
            exclusion_patterns = [
                "ignore", "wireframe", "stub", "placeholder", "skip",
                "not in place", "exclude", "without", "omit", "nor are", "disabled",
                "[block]"
            ]
            already_mitigated = any(kw in prompt_lower_check for kw in exclusion_patterns)
            
            if already_mitigated:
                print("  [Domain Consultant] Automated exclusion/stub intent detected via [block] or keywords. Bypassing interactive prompt.")
                if "[ARCHITECT'S NOTE" not in ctx.user_prompt:
                    ctx.user_prompt += (
                        "\n\n[ARCHITECT'S NOTE: The requested feature explicitly excludes or stubs unavailable domains. "
                        "Implement a functional wireframe, debug placeholder, or safe stub implementation as requested.]"
                    )
            else:
                wireframe_choice = input(
                    "  You asked for a feature relying on an unavailable domain. "
                    "Implement a functional wireframe/debug placeholder instead? [y/N]: "
                ).strip().lower()
                if wireframe_choice in ("y", "yes"):
                    ctx.user_prompt += (
                        "\n\n[ARCHITECT'S NOTE: The following feature relies on an unavailable domain. "
                        "Implement a functional wireframe, debug placeholder, or stub implementation "
                        "that can be easily replaced when the domain becomes available. "
                        "Use TODO comments to mark what needs to be filled in later.]"
                    )
                    print(f"  [Domain Consultant] Wireframe mode activated — placeholder instruction appended.")
                else:
                    print(f"  [Domain Consultant] Declined. Continuing with original prompt.")

    # ── Phase 1 & 2: Autonomic Workspace Discovery & Injection ────────────
    _ts_index = datetime.now().strftime('%H:%M:%S')
    print(f"\n{'='*70}")
    print(f"  [{_ts_index}] Phase 1 & 2: Autonomic Workspace Discovery")
    print(f"{'='*70}")

    from workspace_indexer import WORKSPACE_INDEXER

    print(f"  [Librarian] Executing programmatic repository indexing...")
    active_topology = WORKSPACE_INDEXER.scan_project()

    # 1. Compile verified structural symbols to permanently suppress naming hallucinations
    registered_symbols = ", ".join(sorted(active_topology.classes)) if active_topology.classes else "None Detected"
    print(f"  [Librarian] Verified active C++ AST symbols: {len(active_topology.classes)}")

    # 2. Programmatically generate proactive observability warnings
    unlogged_manifest = ""
    if active_topology.uninstrumented_files:
        _files_str = "\n".join(f"  - {f}" for f in active_topology.uninstrumented_files[:10])
        unlogged_manifest = (
            f"\n[SYSTEM KERNEL AUDIT: The following active files currently lack observability instrumentation. "
            f"If your SEARCH/REPLACE blocks touch these modules, you MUST inject valid logging calls.]\n{_files_str}\n"
        )
        print(f"  [Librarian] Flagged {len(active_topology.uninstrumented_files)} un-instrumented files for pre-flight safety.")

    # 3. Inject highly concentrated context manifest
    ctx.project_state = (
        f"## Active Repository Topology\n"
        f"Registered C++ Symbols: {registered_symbols}\n"
        f"{unlogged_manifest}\n"
        f"[SYSTEM KERNEL: Essential documentation and module implementations are available via dynamic paging. "
        f"Use your strict <invoke_kernel> XML tools to pre-mount reference blocks securely.]"
    )

    ctx.output_parts.append("\n## Phase 1 & 2: Autonomic Workspace Discovery\n" + ctx.project_state + "\n")

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
            # 1. Dynamically load acronym glossary mappings from the active context or cartridge
            glossary_map = getattr(ctx, 'active_glossary_registry', {})
            # Provide a safe fallback ensuring current ecosystem acronyms are resolved without immutable hardcoding
            if not glossary_map:
                glossary_map = {
                    "GDD": "Game Design Document",
                    "AST": "Abstract Syntax Tree"
                }
            
            definitions_str = "\n".join(f"  - '{k}': {v}" for k, v in glossary_map.items())
            
            # Explicitly parse positive request vs [block] negative constraints
            req_parts = re.split(r'\[block\]', ctx.user_prompt, maxsplit=1, flags=re.IGNORECASE)
            pos_request = req_parts[0].strip()
            neg_block_str = f"\n\n## EXPLICITLY BLOCKED / EXCLUDED CONCEPTS\nThe following elements MUST NOT be implemented or planned for:\n{req_parts[1].strip()}" if len(req_parts) > 1 else ""
            
            base_scope_prompt = (
                f"Analyze this feature request: '{pos_request}'.{neg_block_str}\n\n"
                f"## Local Repository Terminology Registry\n"
                f"Interpret all acronyms strictly according to the following active mappings:\n"
                f"{definitions_str}\n\n"
                f"## Relevant Source Documentation Context\n{gdd_snippet}\n\n"
                f"## Current Project State\n{state_snippet}\n\n"
                f"CRITICAL DIRECTIVES:\n"
                f"1. Resolve acronyms exclusively via the Terminology Registry. Do NOT hallucinate external engine terminology (e.g., Godot).\n"
                f"2. You MUST evaluate workload step by step strictly based on active topology without conversational filler.\n"
                f"3. Strictly honor all explicitly blocked or excluded concepts.\n"
                f"4. Conclude with exactly ONE absolute verdict tag on its own line: [VERDICT: NARROW] or [VERDICT: TOO_BROAD]. Intermediate tags are invalid."
            )
            
            scope_system_persona = (
                "You are the Lead Producer orchestration gate. "
                "CRITICAL: Enforce strict, professional step-by-step workload evaluation. "
                "You MUST conclude your output with a literal bracketed verdict tag on its own line. "
                "Valid options are ONLY [VERDICT: NARROW] or [VERDICT: TOO_BROAD]."
            )
            
            # ── Autonomic Feedback-Driven Retry Loop ──────────────────────────
            max_scope_attempts = 3
            current_scope_prompt = base_scope_prompt
            final_verdict = None
            scope_eval = ""

            for attempt in range(1, max_scope_attempts + 1):
                if attempt > 1:
                    print(f"  [Lead Producer] Verdict grammar missing or invalid. Initiating autonomic retry {attempt}/{max_scope_attempts}...")
                
                scope_eval = call_ollama(
                    scope_system_persona, 
                    current_scope_prompt, 
                    f"Scope Gate (Attempt {attempt})", 
                    REASONING_MODEL
                )

                # 2. Extract ALL explicit verdict tags to entirely prevent echoed prompt instructions from hijacking routing
                all_verdicts = re.findall(r"\[\s*\*?\*?\s*VERDICT\s*\*?\*?\s*:\s*\*?\*?\s*(TOO_BROAD|NARROW)\s*\*?\*?\s*\]", scope_eval, re.IGNORECASE)

                if all_verdicts:
                    # Deterministically anchor to the LAST explicit verdict emitted in the conclusion
                    final_verdict = all_verdicts[-1].upper()
                    break
                else:
                    feedback = (
                        "\n\n[SYSTEM KERNEL ERROR: Your previous output failed to append a valid binary verdict tag. "
                        "Intermediate verdicts are invalid. You MUST self-correct and conclude your analysis "
                        "with exactly [VERDICT: NARROW] or [VERDICT: TOO_BROAD] on its own line. Do not omit square brackets.]"
                    )
                    current_scope_prompt = base_scope_prompt + f"\n\n## PREVIOUS REJECTED ANALYSIS:\n{scope_eval}\n" + feedback

            # ── Deterministic Path Routing & Observability ────────────────────
            if final_verdict == "TOO_BROAD":
                print(f"\n  [Lead Producer] Evaluated Verdict: TOO_BROAD. Generating architectural blueprint...")
                unavailable_text = get_unavailable_domains_text()
                active_stack = getattr(ctx, 'workspace_fingerprint', "a universal source repository")
                hard_constraints = (
                    f"HARD CONSTRAINTS — Do NOT plan for:\n"
                    f"{unavailable_text}\n\n"
                    f"You are orchestrating within the strictly fingerprinted ecosystem: {active_stack}. "
                    f"Never reference proprietary engines or unverified third-party libraries not present in local dependencies. "
                    f"If the user asks for features spanning unavailable subsystems, "
                    f"substitute with functional stubs, debug logging, or standard placeholders."
                )
                req_parts_bp = re.split(r'\[block\]', ctx.user_prompt, maxsplit=1, flags=re.IGNORECASE)
                pos_request_bp = req_parts_bp[0].strip()
                neg_block_bp = f"\n\n## EXPLICITLY BLOCKED / EXCLUDED CONCEPTS (CRITICAL)\nYou MUST NOT generate tasks to set up or implement the following:\n{req_parts_bp[1].strip()}" if len(req_parts_bp) > 1 else ""

                blueprint_prompt = (
                    f"Create a step-by-step markdown blueprint to accomplish the positive intent: "
                    f"{pos_request_bp}.\n{neg_block_bp}\n\n"
                    f"{hard_constraints}\n\n"
                    f"## GDD Context\n"
                    f"{ctx.gdd_context[:3000] if ctx.gdd_context else '(none)'}\n\n"
                    f"## Current Project State\n"
                    f"{ctx.project_state[:2000] if ctx.project_state else '(none)'}\n\n"
                    f"## Unavailable Domains\n"
                    f"{unavailable_text}\n\n"
                    f"## Project Structure\n"
                    f"{ctx.structure[:2000] if ctx.structure else '(none)'}\n\n"
                    f"CRITICAL DIRECTIVES:\n"
                    f"1. You MUST strictly honor any explicit exclusions, omissions, or negative constraints specified in the blocked concepts.\n"
                    f"2. Do NOT generate tasks to set up attractions, modules, or features that the user explicitly stated are excluded or blocked, even if they appear in the GDD Context.\n"
                    f"3. Base your step-by-step tasks strictly on the targeted positive feature requested.\n\n"
                    f"Format as a checklist:\n"
                    f"'- [ ] Task 1: ...'"
                )
                
                # Dynamic retry loop for blueprint generation triage
                while True:
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
                    trigger_chime()
                    approval = input("  Do you approve this architectural blueprint? [Y/n]: ").strip().lower()
                    
                    if approval in ("n", "no"):
                        trigger_chime()
                        print(f"\n  [Blueprint Gate triage] Blueprint rejected.")
                        issues = input("  What are the specific problems or incorrect inclusions? ").strip()
                        suggestions = input("  Do you have specific suggestions to address this? (Type your suggestions, 'retry' to regenerate from scratch, or 'abandon' to abort): ").strip()
                        
                        sug_lower = suggestions.lower()
                        if sug_lower == "abandon":
                            print("  [Blueprint Gate] Abandoning blueprint generation. Aborting pipeline.")
                            ctx.final_output = "Pipeline abandoned by user at Blueprint Gate."
                            return ctx
                        elif sug_lower == "retry":
                            print("  [Blueprint Gate] Retrying blueprint generation from scratch based on original constraints...")
                            continue
                        else:
                            # Feed the issues and suggestions back into the prompt to generate an aligned blueprint
                            print("  [Blueprint Gate] Regenerating blueprint incorporating your feedback...")
                            triage_feedback = f"\n\n## USER TRIAGE FEEDBACK ON PREVIOUS DRAFT:\nIssues Identified: {issues}\nUser Suggestions/Mandates: {suggestions}\nCRITICAL: Adjust the checklist strictly to reflect this feedback."
                            blueprint_prompt += triage_feedback
                            continue
                    else:
                        print(f"  [Blueprint Gate] Blueprint approved. Proceeding to execution.\n")
                        break

                # ── Continuous Execution: extract first task & fall through ──
                content = blueprint_path.read_text(encoding="utf-8")
                first_match = re.search(
                    r"^[-\*]?\s*\[ \]\s*(?:Task \d+:\s*)?(.+)",
                    content, re.MULTILINE
                )
                if first_match:
                    raw_line = first_match.group(0)
                    task_text = first_match.group(1).strip()
                    # Wrap the active subtask while preserving the full positive intent and explicit [block] exclusions
                    original_request = ctx.user_prompt
                    ctx.user_prompt = f"Active Subtask: {task_text}\n\nOverarching Context & Constraints from original request:\n{original_request}"
                    new_content = content.replace(raw_line, raw_line.replace("[ ]", "[x]", 1), 1)
                    atomic_write_text(blueprint_path, new_content)
                    print(f"  [Lead Producer] Auto-feeding first task while preserving block constraints: {task_text}")
                    print(f"  [Lead Producer] Continuing to Phase 3...")
                else:
                    print("  [Lead Producer] Blueprint generated but no tasks found — continuing with original prompt.")
            elif final_verdict == "NARROW":
                print(f"\n  [Lead Producer] Evaluated Verdict: NARROW. Advancing directly to task decomposition.")
            else:
                print(f"\n  [Lead Producer] WARNING: Failed to extract valid verdict after {max_scope_attempts} attempts. Defaulting to NARROW pass-through.")

    # ── Phase 3: Director ─────────────────────────────────────────────────
    _ts = datetime.now().strftime('%H:%M:%S')
    print(f"\n{'='*70}")
    print(f"  [{_ts}] Phase 3: Director — Task Decomposition")
    print(f"{'='*70}")
    ctx.output_parts.append("\n## Phase 3: Director — Task Decomposition\n")

    director_prompt = build_director_prompt()
    # ── Director Context: Inject GDD context and project state ──
    gdd_snippet = ctx.gdd_context[:2500] if ctx.gdd_context else "(no GDD context)"
    state_snippet = ctx.project_state[:2000] if ctx.project_state else "(no project state)"
    
    base_director_input = (
        f"{director_prompt}\n\n"
        f"## Relevant GDD Context\n{gdd_snippet}\n\n"
        f"## Current Project State\n{state_snippet}\n\n"
        f"---\nUSER REQUEST:\n{ctx.user_prompt}"
    )

    # Highly robust regex capturing the strict task layout
    task_regex = r"### Task ([a-zA-Z0-9]+):\s*\[([^\]]+)\]\s*[-—–]\s*(.+?)(?:\s*\(DependsOn:\s*(.+?)\))?\s*$"
    ctx.tasks_list = []

    # ── Autonomic Feedback-Driven Retry Loop ──────────────────────────────
    max_parsing_attempts = 3
    current_director_input = base_director_input

    for attempt in range(1, max_parsing_attempts + 1):
        if attempt > 1:
            print(f"  [Director] Task parsing failed (missing brackets/alignment). Initiating autonomic retry {attempt}/{max_parsing_attempts}...")

        ctx.director_output = call_ollama(
            DIRECTOR_SYSTEM, current_director_input, f"Director (Attempt {attempt})", DIRECTOR_MODEL
        )

        # ── Pro Mode: MATH_HEAVY Gate ─────────────────────────────────────
        if re.search(r"\[\s*\*?\*?\s*MATH_?HEAVY\s*\*?\*?\s*\]", ctx.director_output, re.IGNORECASE):
            print(f"\n{'='*50}")
            print(f"  MATH_HEAVY DETECTED — Complex 3D math / physics request")
            print(f"{'='*50}")
            warning_msg = (
                "It looks like a lot of complex math needs to be calculated. "
                "Should we turn on Pro Mode for rigorous test-driven consensus? [y/N]: "
            )
            trigger_chime()
            user_input = input(f"  {warning_msg}").strip().lower()
            if user_input in ("y", "yes"):
                ctx.pro_mode = True
                print(f"  [Pro Mode] ENABLED — TDD guardrails, multi-draft consensus, and test compilation active.")
            else:
                print(f"  [Pro Mode] Declined — continuing in standard mode.")

        # Attempt surgical extraction
        ctx.tasks_list = []
        for match in re.finditer(task_regex, ctx.director_output, re.MULTILINE):
            task_id = match.group(1)
            domain = match.group(2).strip()
            title = match.group(3).strip()
            depends_on_str = match.group(4)
            depends_on = []
            if depends_on_str and depends_on_str.strip().lower() != "none":
                for dep in re.split(r',\s*', depends_on_str.strip()):
                    dep_match = re.search(r'Task\s*([a-zA-Z0-9]+)', dep, re.IGNORECASE)
                    if dep_match:
                        depends_on.append(dep_match.group(1))
            ctx.tasks_list.append({
                "id": task_id,
                "domain": domain,
                "title": title,
                "depends_on": depends_on,
            })

        # Evaluate extraction validity
        if ctx.tasks_list:
            # Graph successfully parsed
            break
        else:
            # Intercept malformed string and dynamically build targeted syntax feedback
            syntax_feedback = (
                f"\n\n[SYSTEM KERNEL ERROR: Your previous output failed to match the mandatory parsing schema entirely. "
                f"No tasks could be extracted because you omitted literal square brackets around the domain tags or dropped the header alignment. "
                f"You MUST self-correct immediately. Wrap the domain in brackets. Example: '### Task 1: [C++] - Integrate Gameplay Logic (DependsOn: None)'. "
                f"Do NOT output loose prose.]"
            )
            current_director_input = base_director_input + f"\n\n## PREVIOUS REJECTED OUTPUT:\n{ctx.director_output}\n" + syntax_feedback

    # ── Directive B: Interface Manifest (Anti-Hallucination Contract) ─────
    # Deterministically extract substantive nouns to guarantee valid C++ identifiers,
    # utilizing a refined taxonomic filter that excises imperative action verbs
    # and prepositions while strictly protecting valid structural class suffixes.
    def _derive_cpp_class_name(title_str: str) -> str:
        procedural_filler = {
            "create", "implement", "initialize", "setup", "define", "expose", 
            "load", "integrate", "add", "build", "make", "update", "refactor",
            "write", "test", "for", "the", "a", "an", "to", "into", "from", 
            "via", "using", "with", "and", "or", "basic", "game", "system",
            "module", "feature", "request", "want", "you", "information", "active",
            "subtask", "overarching", "context", "constraints", "original"
        }
        # Safely extract only the positive intent before deriving identifier names
        pos_title = re.split(r'\[block\]|Overarching Context', title_str, maxsplit=1, flags=re.IGNORECASE)[0]
        clean_str = re.sub(r'[^a-zA-Z0-9\s]', ' ', pos_title)
        tokens = clean_str.split()
        substantive = [t for t in tokens if t.lower() not in procedural_filler and len(t) > 1]
        
        if not substantive:
            return getattr(ctx, 'default_module_name', "UniversalOrchestrationModule")
            
        # Rigorously cap to a maximum of 3 concise substantive nouns to ensure clean, valid identifiers
        capped_substantive = substantive[:3]
        pascal_name = "".join(t.capitalize() for t in capped_substantive)
        if pascal_name[0].isdigit():
            pascal_name = "Module" + pascal_name
            
        return pascal_name[:40]  # Hard safety ceiling

    _target_title = ""
    if hasattr(ctx, 'user_prompt') and ctx.user_prompt:
        _target_title = ctx.user_prompt
        
    if hasattr(ctx, 'tasks_list') and ctx.tasks_list and len(ctx.tasks_list) > 0:
        # Prefer the explicit title from the first decomposed task if available
        _target_title = ctx.tasks_list[0].get("title", _target_title)

    _expected_target_name = _derive_cpp_class_name(_target_title)

    ctx.interface_manifest = (
        f"\n[SYSTEM KERNEL CONTRACT: Both the Test Suite and the Implementation MUST strictly "
        f"utilize the class name '{_expected_target_name}'. Do not invent alternative class names.]\n"
    )

    # Commit authoritative parsed output to session trail
    ctx.output_parts.append(ctx.director_output + "\n")

    # Final circuit breaker (Triggered only if 3 explicit corrective cycles fail)
    if not ctx.tasks_list:
        ctx.tasks_list.append({"id": "1", "domain": "C++", "title": "Full Implementation", "depends_on": []})
        print(f"  [Director] CRITICAL ERROR: Maximum parsing recovery retries exceeded. Forced default fallback.")

    print(f"  [Director] Created {len(ctx.tasks_list)} task(s)")

    return ctx


# ──────────────────────────────────────────────────────────────────────
#  Function 2: run_tasks
#  Phase 4: Mesh Execution — DAG-based wave processing loop
# ──────────────────────────────────────────────────────────────────────

def run_tasks(ctx: PipelineContext) -> PipelineContext:
    """Phase 4: Build DAG from tasks, sort into waves, process wave-by-wave.

    Returns updated ctx with all_results_dict, processed_ids, query_results,
    pending_queries, pending_fetches, and task_map populated.
    """
    # ── Phase 4: Mesh Execution ───────────────────────────────────────────
    _ts = datetime.now().strftime('%H:%M:%S')
    print(f"\n{'='*70}")
    print(f"  [{_ts}] Phase 4: Mesh Execution (Omni-Batch DAG) — {len(ctx.tasks_list)} Task(s)")
    print(f"{'='*70}")
    ctx.output_parts.append(
        f"\n## Phase 4: Mesh Execution ({len(ctx.tasks_list)} tasks)\n"
    )

    # Build task map
    ctx.task_map = {}

    for t in ctx.tasks_list:
        task_obj = Task(
            agent=t["domain"],
            spec=t["title"],
            task_id=f"task_{t['id']}",
            parent=None,
        )
        ctx.task_map[task_obj.task_id] = task_obj

    # ── Sort tasks into DAG waves ────────────────────────────────────────
    waves = sort_tasks_into_waves(ctx.tasks_list)
    print(f"  [DAG] Sorted {len(ctx.tasks_list)} task(s) into {len(waves)} wave(s):")
    for i, wave in enumerate(waves):
        task_ids = ", ".join(f"Task {t['id']} [{t['domain']}]" for t in wave)
        print(f"    Wave {i+1}: {task_ids}")

    # Track compiled waves for batch circuit breaker
    compiled_waves: Set[int] = set()

    # ── Process each wave ────────────────────────────────────────────────
    for wave_idx, wave in enumerate(waves):
        print(f"\n  {'='*60}")
        print(f"  Processing Wave {wave_idx + 1}/{len(waves)} ({len(wave)} task(s))")
        print(f"  {'='*60}")

        # Determine if this wave uses 7B generation (typically C++, Lua tasks)
        # or 14B reviewer/tribunal tasks
        uses_7b = any(
            resolve_agent_name(t["domain"]) not in ("REVIEWER", "TRIBUNAL", "CONF", "LIBRARIAN")
            for t in wave
        )
        uses_14b = any(
            resolve_agent_name(t["domain"]) in ("REVIEWER", "TRIBUNAL", "CONF")
            for t in wave
        )

        # ── VRAM Flush: Before swapping from 7B to 14B model ─────────
        if uses_14b and not uses_7b:
            print(f"  [VRAM Governor] Wave {wave_idx + 1} uses 14B model — flushing 7B from VRAM...")
            unload_model(CODER_MODEL)

        # ── Execute tasks in this wave (can be parallel in theory) ──
        wave_results = {}
        for t in wave:
            task = ctx.task_map.get(f"task_{t['id']}")
            if task is None:
                continue

            if task.task_id in ctx.processed_ids and not task.is_query:
                continue

            # ── VRAM Lock: For 7B generation, set keep_alive to 15m ──
            ollama_params = None
            if uses_7b and resolve_agent_name(task.agent) not in ("REVIEWER", "TRIBUNAL", "CONF"):
                ollama_params = {"keep_alive": "15m"}
                print(f"  [VRAM Lock] Setting keep_alive=15m for {task.task_id} ({task.agent})")

            # Check for query results to inject
            context_extra = ""
            if task.parent and task.parent in ctx.query_results:
                context_extra = f"## Answer from Query\n{ctx.query_results[task.parent]}"

            task.context = context_extra

            # Find relevant files for this task
            file_context = ""
            try:
                files = find_relevant_files(task.spec, task.agent)
                file_context = format_file_context(files, domain_key=task.agent)
                if ctx.snapshot:
                    try:
                        ctx.snapshot.save_originals_from_context(file_context)
                    except Exception as e:
                        print(f"  [Snapshot] save_originals_from_context error: {e}")
            except Exception as e:
                print(f"  [FileReader] Error: {e}")

            # ── Directive A: Sibling Context Manifest (Anti-Bloat) ───
            # Sibling output code is NOT dumped into the next agent's prompt.
            # Instead, a lightweight manifest string is emitted that lists
            # completed sibling tasks by name/spec. The receiving agent must
            # use <PAGE_IN> if it needs the actual code. This eliminates
            # 3,000+ tokens of raw sibling code bloat per wave.
            sibling_parts = []
            for completed_id, completed_code in ctx.all_results_dict.items():
                if completed_id == task.task_id:
                    continue
                completed_task = ctx.task_map.get(completed_id)
                if completed_task is None:
                    continue
                same_parent = (completed_task.parent == task.parent)
                if same_parent:
                    agent_name = ALL_DOMAINS.get(
                        resolve_agent_name(completed_task.agent), {}
                    ).get("name", completed_task.agent)

                    # Dynamically extract actual, real relative paths modified by the sibling task
                    actual_paths = []
                    for path_match in re.finditer(r"(?:###|//|--)\s*(?:File:\s*)?([a-zA-Z0-9_/-]+\.(?:lua|cpp|h|hpp))", completed_code):
                        actual_paths.append(path_match.group(1).strip())

                    paths_str = ", ".join(sorted(set(actual_paths))) if actual_paths else "no explicit files"
                    sibling_parts.append(
                        f"- {agent_name} ({completed_id}): \"{completed_task.spec}\" (Modified: {paths_str})"
                    )
            sibling_context = ""
            if sibling_parts:
                sibling_context = (
                    "[SYSTEM KERNEL: The following sibling task(s) have completed "
                    "in this execution wave:\n"
                    + "\n".join(sibling_parts)
                    + "\n\n"
                    "CRITICAL: If you require the code from any of these tasks, you MUST use "
                    "the exact, real file paths listed above in your paging tags (e.g., "
                    "<invoke_kernel><action>PAGE_IN</action><target>src/attractions/example.lua</target></invoke_kernel>). "
                    "Do NOT use literal placeholder strings, and do NOT attempt to reconstruct sibling code from this manifest.]"
                )

            # ── Directive B: Pro-Mode Inheritance — cached content ──
            # Inherits actual cached text chunks (not filenames), so sub-agents
            # receive the safely extracted content directly — no disk I/O, no
            # 12k Hard Cap bypass, no hallucination from "notes".
            _inherited_cache: Dict[str, str] = getattr(task, 'paged_files_cache', {}) or {}
            _paged_inheritance_note = ""
            if _inherited_cache:
                cache_blocks = []
                total_chars = 0
                for filepath, cached_text in _inherited_cache.items():
                    total_chars += len(cached_text)
                    cache_blocks.append(
                        f"## ❮ PAGED-IN REFERENCE FILE: {filepath} ❯\n"
                        f"```\n{cached_text}\n```\n"
                    )
                _paged_inheritance_note = (
                    "\n\n## ❮ PAGED-IN REFERENCE FILES (inherited from primary worker) ❯\n"
                    f"({len(_inherited_cache)} files, {total_chars} total chars "
                    f"— Safe Cache, no disk I/O)\n\n"
                    + "\n".join(cache_blocks)
                )
                print(f"  [Paging Kernel] 📋 Injected {len(_inherited_cache)} cached blocks "
                      f"({total_chars} chars) into '{task.agent}' prompt")

            # ── Pro Mode: Adversarial TDD ────────────────────────────
            pro_test_injection = ""
            if ctx.pro_mode:
                print(f"\n  [Pro Mode] Adversarial TDD: Routing {task.task_id} to Lead Architect for test generation...")

                # ── Directive B: Dynamic Cartridge-Driven Domain Enforcement ──
                # Extract the language domain from the primary worker's task so
                # the Test Architect writes tests in the correct language (e.g.
                # Lua Busted tests instead of C++ Google Tests for a [Lua] task).
                # Now reads from the cartridge's domain_metadata_registry instead
                # of a hardcoded _DOMAIN_LANG_MAP dictionary.
                _domain = resolve_agent_name(task.agent)
                _domain_bracket_match = re.search(r'\[([A-Za-z0-9_+#]+)\]', task.spec)
                if _domain_bracket_match:
                    _domain = resolve_agent_name(_domain_bracket_match.group(1))
                _alias = getattr(ctx, 'alias_map', {}).get(_domain.upper(), _domain.upper())
                _domain = _alias
                _meta = getattr(ctx, 'domain_metadata_registry', {}).get(
                    _domain, getattr(ctx, 'domain_metadata_registry', {}).get("C++", {
                        "language": "C++",
                        "test_framework": "C++ Google Test (gtest)",
                        "code_tag": "cpp",
                        "extension": ".cpp",
                        "assert_examples": "Use EXPECT_EQ, EXPECT_NEAR, ASSERT_TRUE, etc. as appropriate."
                    })
                )
                _lang = _meta["language"]
                _is_lua = (_lang == "Lua")
                _test_framework = _meta["test_framework"]
                _code_block_tag = _meta["code_tag"]
                _assert_examples = _meta["assert_examples"]
                _domain_mandate = (
                    f"[SYSTEM KERNEL MANDATE: You are operating in the strictly enforced "
                    f"{_domain} domain. You MUST write your tests exclusively in "
                    f"{_lang}. Do not use C++ if this is a {_lang} task, and vice versa. "
                    f"Output ONLY source code for {_test_framework}.]\n\n"
                )
                _manifest_part = getattr(ctx, 'interface_manifest', '') if _domain in ("C++", "PHYS") else ""
                test_writer_system = _domain_mandate + _manifest_part + (
                    "You are the Lead Test Architect. Your ONLY job is to write "
                    f"a {_test_framework} unit test that PROVES the expected "
                    "math, logic, or behavior described in the task specification. "
                    f"Output ONLY the {_test_framework} source code as a single code block. "
                    "Do NOT modify any existing files. Do NOT write implementation code. "
                    "The test MUST fail initially (the implementation doesn't exist yet)."
                )
                test_writer_prompt = (
                    f"## Task Specification\n{task.spec}\n\n"
                    f"## User's Feature Request\n{ctx.user_prompt}\n\n"
                    f"## Director's Task Breakdown\n{ctx.director_output}\n\n"
                    f"{_paged_inheritance_note}"
                    f"---\n"
                    f"Write a {_test_framework} that asserts the expected "
                    f"behavior, math, or logic for this task. The test should define "
                    f"the function signatures and expected values that the implementation "
                    f"must satisfy. Output ONLY the {_test_framework} code in a "
                    f"```{_code_block_tag} block. "
                    f"{_assert_examples} "
                    f"If the task involves physics math, include numerical tolerance checks "
                    f"with EXPECT_NEAR."
                )
                test_code = call_ollama(
                    test_writer_system,
                    test_writer_prompt,
                    f"Test Architect ({task.task_id})",
                    REVIEWER_MODEL,
                )
                # ── Directive B: Dynamic code block extraction ────────────
                # Match the language-specific fence that the Test Architect was
                # instructed to use (lua for Lua tasks, cpp/C++/cxx for C++).
                _code_block_regex = (
                    rf"```(?:{_code_block_tag}|C\+\+|cxx)?\s*\n(.*?)```"
                )
                test_match = re.search(_code_block_regex, test_code, re.DOTALL)
                test_body = test_match.group(1).strip() if test_match else test_code.strip()
                test_dir = ctx.project_root / "tests"
                test_dir.mkdir(parents=True, exist_ok=True)
                _test_ext = _meta["extension"]
                test_file_path = test_dir / f"test_{task.task_id}{_test_ext}"
                atomic_write_text(test_file_path, test_body)
                print(f"  [Pro Mode] Adversarial TDD: Saved test to {test_file_path}")
                _injection_code_tags = _code_block_tag
                pro_test_injection = (
                    f"\n\n---\n"
                    f"## ADVERSARIAL TDD: Failing Unit Test Written by Lead Architect\n"
                    f"Here is a failing unit test written by the Lead Architect:\n"
                    f"```{_injection_code_tags}\n{test_body}\n```\n"
                    f"Write the {_lang} implementation to make this test pass. "
                    f"You are strictly forbidden from modifying the test file at `{test_file_path}`."
                )

            # ── Day 6: Math Analyst Deterministic Sandbox ────────────
            math_analyst_output = ""
            _spec_lower = task.spec.lower()
            _is_binding_task = any(kw in _spec_lower for kw in ["bind", "expose", "wrapper", "bridge", "interface", "setup", "initialize", "create", "fixture", "game", "ramp", "attraction"])
            _is_pure_math = any(kw in _spec_lower for kw in ["solve matrix", "floating-point problem", "pure physics math", "compute impulse vector"])
            
            if not _is_binding_task and _is_pure_math:
                print(f"\n  [Math Analyst] Intercepting mathematical task {task.task_id} for deterministic execution...")
                math_script_dir = ctx.project_root / "global_cache" / "math_analyst"
                math_script_dir.mkdir(parents=True, exist_ok=True)
                math_script_path = math_script_dir / f"solver_{task.task_id}.py"
                
                math_system = (
                    "You are the Math Analyst. Your ONLY job is to write a standalone, deterministic Python script "
                    "using numpy or scipy to solve the exact floating-point matrix, vector, or physics impulse problem described. "
                    "Output ONLY executable Python code inside a ```python block. Use print() to output the final numerical results."
                )
                math_prompt = f"Solve the following mathematical/physics problem deterministically:\n{task.spec}"
                
                raw_math_code = call_ollama(math_system, math_prompt, f"Math Analyst ({task.task_id})", REASONING_MODEL)
                math_code_match = re.search(r"```python\s*\n(.*?)```", raw_math_code, re.DOTALL)
                math_code = math_code_match.group(1).strip() if math_code_match else raw_math_code.strip()
                
                atomic_write_text(math_script_path, math_code)
                
                try:
                    proc = subprocess.run([sys.executable, str(math_script_path)], capture_output=True, text=True, timeout=15)
                    if proc.returncode == 0:
                        math_analyst_output = (
                            f"\n\n## DETERMINISTIC MATH ANALYST OUTPUT\n"
                            f"The following guaranteed numerical values were computed natively via `{math_script_path.name}`:\n"
                            f"```\n{proc.stdout.strip()}\n```\n"
                            f"You MUST utilize these exact floating-point values in your implementation."
                        )
                        print(f"  [Math Analyst] Guaranteed results computed successfully ✅")
                    else:
                        print(f"  [Math Analyst] Execution failed, falling back to probabilistic synthesis: {proc.stderr.strip()[:200]}")
                except Exception as e:
                    print(f"  [Math Analyst] Subprocess error: {e}")
                
                if math_analyst_output:
                    task.context = (task.context or "") + math_analyst_output

            # ── Pro Mode: Multi-Draft Generation ─────────────────────
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
                        ollama_params={"temperature": temp, **(ollama_params or {})},
                    )
                    drafts[f"draft_{chr(65+i)}"] = draft_output

                print(f"  [Pro Mode] Tribunal merging 3 drafts for {task.task_id}...")

                # ── Directive B: Cartridge-Driven Domain Enforcement for Tribunal Merge ──
                # Extract the language domain from the primary task so the Tribunal
                # synthesizes code in the correct language.
                # Now reads from the cartridge's domain_metadata_registry instead
                # of a hardcoded _DOMAIN_LANG_MAP dictionary.
                _domain_trib = resolve_agent_name(task.agent)
                _domain_bracket_match_trib = re.search(r'\[([A-Za-z0-9_+#]+)\]', task.spec)
                if _domain_bracket_match_trib:
                    _domain_trib = resolve_agent_name(_domain_bracket_match_trib.group(1))
                _alias_trib = getattr(ctx, 'alias_map', {}).get(_domain_trib.upper(), _domain_trib.upper())
                _domain_trib = _alias_trib
                _meta_trib = getattr(ctx, 'domain_metadata_registry', {}).get(
                    _domain_trib, getattr(ctx, 'domain_metadata_registry', {}).get("C++", {
                        "language": "C++",
                        "test_framework": "C++ Google Test (gtest)",
                        "code_tag": "cpp",
                        "extension": ".cpp",
                        "assert_examples": "Use EXPECT_EQ, EXPECT_NEAR, ASSERT_TRUE, etc. as appropriate."
                    })
                )
                _lang_trib = _meta_trib["language"]
                _domain_mandate_trib = (
                    f"[SYSTEM KERNEL MANDATE: You are operating in the strictly enforced "
                    f"{_domain_trib} domain. You MUST write your merged code exclusively in "
                    f"{_lang_trib}. Do not use C++ if this is a {_lang_trib} task, "
                    f"and vice versa.]\n\n"
                )
                tribunal_director_system = _domain_mandate_trib + (
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
                    f"{_paged_inheritance_note}"
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
                # ── Directive B: Pro-Mode Artifact Extraction ──────────
                # Strip conversational prose from tribunal output, keeping
                # only the synthesized code blocks. This prevents the tribunal's
                # evaluation commentary from bleeding into the global state.
                from _helpers_text import strip_to_code_artifacts
                output = strip_to_code_artifacts(tribunal_output, fallback_truncation=1200)
                print(f"  [Pro Mode] Tribunal merge complete for {task.task_id}")
                print(f"  [Pro Mode] Artifact extraction: stripped tribunal commentary, "
                      f"keeping {len(output)} chars of code artifacts")
            else:
                # Standard single-draft execution
                if pro_test_injection:
                    task.context = (task.context or "") + pro_test_injection
                # ── Directive B: Inject Interface Manifest strictly for C++ domains ──
                if resolve_agent_name(task.agent) in ("C++", "PHYS") and hasattr(ctx, 'interface_manifest'):
                    task.context = (task.context or "") + getattr(ctx, 'interface_manifest', '')
                output = execute_task(
                    task, ctx.user_prompt, ctx.director_output,
                    ctx.all_results_dict, file_context, ctx.gdd_context,
                    sibling_context=sibling_context,
                    ollama_params=ollama_params,
                )

            ctx.all_results_dict[task.task_id] = output
            ctx.processed_ids.add(task.task_id)
            wave_results[task.task_id] = output

            # Process signals
            _process_task_signals(ctx, task, work_queue := deque())

        # ── Batch Compilation: Verify entire wave of generated C++ ──
        # Check if this wave generated any .cpp/.h code and compile it
        wave_has_cpp = any(
            resolve_agent_name(t["domain"]) in ("C++", "PHYS") and
            ("cpp" in ctx.all_results_dict.get(f"task_{t['id']}", "").lower() or
             ".h" in ctx.all_results_dict.get(f"task_{t['id']}", ""))
            for t in wave
        )
        if wave_has_cpp and wave_idx not in compiled_waves:
            print(f"  [Batch Compiler] Verifying Wave {wave_idx + 1} C++ output...")
            try:
                if sys.platform == "win32":
                    cmake_build = subprocess.run(
                        ["cmake", "--build", "."],
                        capture_output=True, text=True, cwd=ctx.project_root,
                        shell=True, timeout=30,
                    )
                    if cmake_build.returncode != 0:
                        err_tail = "\n".join(cmake_build.stderr.splitlines()[-50:])
                        print(f"  [Circuit Breaker] Wave {wave_idx + 1} build failure detected")
                        ctx.pre_flight_errors += f"\n## Wave {wave_idx + 1} Compiler Errors:\n```\n{err_tail}\n```"
                        # Circuit Breaker: increment retry for entire wave
                        for tid in wave_results:
                            ctx.retry_counts[tid] = ctx.retry_counts.get(tid, 0) + 1
                            print(f"  [Circuit Breaker] {tid} retry_count incremented (wave build failure)")
                        # Check if any task hit retry limit
                        for tid in wave_results:
                            if ctx.retry_counts.get(tid, 0) >= 3:
                                print(f"\n  [Circuit Breaker] ⛔ Wave {wave_idx + 1} — task {tid} hit retry limit!")
                                ctx.review_verdict = "BLOCKED"
                                break
                    else:
                        print(f"  [Batch Compiler] Wave {wave_idx + 1} compiles successfully ✅")
                else:
                    make_process = subprocess.run(
                        ["make", "-j4"], capture_output=True, text=True,
                        cwd=ctx.project_root, timeout=30,
                    )
                    if make_process.returncode != 0:
                        err_tail = "\n".join(make_process.stderr.splitlines()[-50:])
                        print(f"  [Circuit Breaker] Wave {wave_idx + 1} build failure detected")
                        ctx.pre_flight_errors += f"\n## Wave {wave_idx + 1} Compiler Errors:\n```\n{err_tail}\n```"
                        for tid in wave_results:
                            ctx.retry_counts[tid] = ctx.retry_counts.get(tid, 0) + 1
                        for tid in wave_results:
                            if ctx.retry_counts.get(tid, 0) >= 3:
                                ctx.review_verdict = "BLOCKED"
                                break
                    else:
                        print(f"  [Batch Compiler] Wave {wave_idx + 1} compiles successfully ✅")
            except subprocess.TimeoutExpired:
                print(f"  [Circuit Breaker] Wave {wave_idx + 1} build timed out")
            except Exception:
                pass
            compiled_waves.add(wave_idx)

            if ctx.review_verdict == "BLOCKED":
                break

    return ctx


def _process_task_signals(ctx: PipelineContext, task, work_queue: deque) -> None:
    """Process signals emitted by a completed task. (Extracted for readability.)"""
    if task.is_query:
        ctx.query_results[task.parent] = task.output
        # Re-queue original task if this resolved a FETCH or QUERY
        if task.task_id in ctx.pending_fetches:
            original_task = ctx.pending_fetches.pop(task.task_id)
            original_task.context = (original_task.context or "") + "\n" + task.output
            original_task._fetch_depth = getattr(task, '_fetch_depth', 0)
            original_task.completed = False
            work_queue.appendleft(original_task)
        if task.task_id in ctx.pending_queries:
            parent_task = ctx.pending_queries.pop(task.task_id)
            parent_task.context = (
                (parent_task.context or "")
                + f"\n## Answer from {task.agent}:\n{task.output}\n"
            )
            parent_task.completed = False
            work_queue.appendleft(parent_task)

    for signal in task.signals:
        stype = signal["type"]
        if stype == "QUERY":
            target = resolve_agent_name(signal["target"])
            query_task = Task(
                agent=target, spec=signal["content"],
                task_id=f"query_{task.task_id}_{len(ctx.query_results)}",
                parent=task.task_id, is_query=True,
            )
            work_queue.appendleft(query_task)
            ctx.pending_queries[query_task.task_id] = task

        elif stype == "DELEGATE":
            target = resolve_agent_name(signal["target"])
            sub_count = sum(1 for t in ctx.task_map.values() if t.parent == task.task_id)
            if sub_count < MAX_SUBTASKS_PER_AGENT:
                sub_task = Task(
                    agent=target, spec=signal["content"],
                    task_id=f"sub_{task.task_id}_{sub_count + 1}",
                    parent=task.task_id,
                )
                work_queue.append(sub_task)
                ctx.task_map[sub_task.task_id] = sub_task

        elif stype == "VETO":
            ctx.all_vetos.append({
                "from": task.agent, "target": signal["target"],
                "reason": signal["content"], "task_id": task.task_id,
            })
            veto_target = resolve_agent_name(signal["target"])
            veto_target_tid = f"task_{veto_target}"
            ctx.retry_counts[veto_target_tid] = ctx.retry_counts.get(veto_target_tid, 0) + 1

        elif stype == "OBJECT":
            ctx.all_objects.append({
                "from": task.agent, "target": signal["target"],
                "concern": signal["content"], "task_id": task.task_id,
            })

        elif stype == "APPROVE":
            ctx.all_approvals_dict[task.agent] = True

        elif stype == "REVISE":
            target = resolve_agent_name(signal["target"])
            revise_task = Task(
                agent=target,
                spec=f"Revision requested by {task.agent}: {signal['content']}",
                task_id=f"revise_{target}_{ctx.consensus_iteration}",
                parent=task.task_id, iteration=0,
            )
            work_queue.append(revise_task)

        elif stype == "RECOURSE":
            pass  # handled in consensus phase

        elif stype == "CONSULT":
            target = resolve_agent_name(signal["target"])
            consult_task = Task(
                agent=target,
                spec=f"Consultation requested by {task.agent}: {signal['content']}",
                task_id=f"consult_{task.task_id}_{len(ctx.query_results)}",
                parent=task.task_id, is_query=True,
            )
            work_queue.append(consult_task)
            ctx.pending_queries[consult_task.task_id] = task

        elif stype == "APPEAL":
            appeal_target = resolve_agent_name(signal["target"])
            appeal_defense = signal["content"]
            matched_veto = None
            for v in reversed(ctx.all_vetos):
                if v["target"] == appeal_target or v["from"] == appeal_target:
                    matched_veto = v
                    break
            ctx.pending_appeals.append({
                "appellant": task.agent,
                "respondent": appeal_target if matched_veto is None else matched_veto["from"],
                "defense": appeal_defense,
                "veto_reason": matched_veto["reason"] if matched_veto else "",
                "veto_task_id": matched_veto["task_id"] if matched_veto else "",
                "task_spec": task.spec,
            })
            tribunal_spec = (
                f"APPELLATE REVIEW\n"
                f"Appellant: {task.agent}\n"
                f"Respondent: {appeal_target if matched_veto is None else matched_veto['from']}\n---\n"
                f"**Coder's Defense:** {appeal_defense}\n"
                f"**VETO Justification:** {matched_veto['reason'] if matched_veto else '(from OBJECT)'}\n"
                f"**Task Spec:** {task.spec}\n---\n"
                f"As TRIBUNAL agent, perform a blind-review. "
                f"Issue [MERGE:Tribunal:<justification>] or [REJECT:Tribunal:<justification>]."
            )
            tribunal_task = Task(
                agent="TRIBUNAL", spec=tribunal_spec,
                task_id=f"tribunal_{task.task_id}",
                parent=task.task_id, is_query=True,
            )
            work_queue.appendleft(tribunal_task)
            ctx.pending_queries[tribunal_task.task_id] = task

        elif stype == "MERGE":
            ctx.tribunal_verdicts[task.agent] = f"MERGE:{signal['content']}"
            ctx.all_vetos = [
                v for v in ctx.all_vetos
                if not (signal.get("content", "") and
                        signal["content"] in v.get("task_id", ""))
            ]

        elif stype == "REJECT":
            ctx.tribunal_verdicts[task.agent] = f"REJECT:{signal['content']}"

        # Directive C: Legacy signal handlers MATH_EVAL, FETCH, READ_OFFLOADED,
        # and EXTRACT_SKELETON have been PURGED. They are entirely superseded
        # by the <invoke_kernel> XML schema in the PagingKernel, which handles
        # all file access, math evaluation, and offload operations safely
        # within the 12,000-character Hard Cap.

    # Check double-check for unresolved items
    if task.double_check and task.double_check["unresolved"]:
        unresolved = task.double_check["unresolved"].strip()
        if unresolved and unresolved.lower() not in ("none", "n/a", "nothing", ""):
            if task.iteration < MAX_ITERATIONS:
                task.iteration += 1
                task.completed = False
                work_queue.appendleft(task)

    # Save snapshot after each task
    if ctx.snapshot:
        try:
            persona = ALL_DOMAINS.get(resolve_agent_name(task.agent), {}).get("name", task.agent)
            ctx.snapshot.save_agent_output(persona, len(ctx.processed_ids), task.output)
        except Exception as e:
            print(f"  [Snapshot] Save error: {e}")


# Function 3 is re-exported from mesh_finalize module.
# See the import at the top of this file:
#   from mesh_finalize import run_code_merge
