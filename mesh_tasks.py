"""
mesh_tasks.py  Phase 4: Wave-based mesh execution
====================================================
Extracted from mesh_loops.py to keep individual files under 1 000 lines.

run_tasks(ctx) processes the task DAG wave by wave:
  - Pro-mode adversarial TDD (test writer → implementation → tribunal)
  - Math Analyst deterministic sandbox
  - Signal processing (QUERY, DELEGATE, VETO, APPEAL, etc.)
  - Batch compilation / cartridge validation
  - Snapshot persistence

_process_task_signals(ctx, task, work_queue) handles all mesh signals for a
completed task and appends generated sub-tasks to the shared work_queue.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections import deque
from datetime import datetime
from typing import Any, Dict, Set

from _pipeline_helpers import (
    MAX_SUBTASKS_PER_AGENT,
    CODER_MODEL, REVIEWER_MODEL, DIRECTOR_MODEL,
    ALL_DOMAINS,
    resolve_agent_name,
    find_relevant_files, format_file_context,
    call_ollama,
    atomic_write_text,
    execute_task,
    PipelineContext, Task,
)
from pipeline import (
    log_to_session_timeline,
)
from mesh_wave_sorter import sort_tasks_into_waves
from ollama_client import unload_model
from token_budget import TokenBudget


def run_tasks(ctx: PipelineContext) -> PipelineContext:
    """Phase 4: Build DAG from tasks, sort into waves, process wave-by-wave.

    Returns updated ctx with all_results_dict, processed_ids, query_results,
    pending_queries, pending_fetches, and task_map populated.
    """
    _ts = datetime.now().strftime('%H:%M:%S')
    print(f"\n{'='*70}")
    print(f"  [{_ts}] Phase 4: Mesh Execution (Omni-Batch DAG)  {len(ctx.tasks_list)} Task(s)")
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
            target_file=t.get("target_file"),
            anchor_marker=t.get("anchor_marker") or t.get("_anchor_marker"),
        )
        ctx.task_map[task_obj.task_id] = task_obj

    # -- Phase A: Deterministic Skeleton Builder (MUST run BEFORE tasks) --
    # Ensure every .lua target file has a valid canonical skeleton before
    # any SEARCH/REPLACE patch tries to target its anchor markers.
    # This fixes the ordering bug where the skeleton was written AFTER task
    # execution, causing SEARCH blocks to fail against empty/non-existent files.
    print(f"\n{'='*60}")
    print(f"  Phase A: Deterministic Skeleton Builder (pre-task)")
    print(f"{'='*60}")
    _skeleton_written = 0
    for _t in ctx.tasks_list:
        _tf = _t.get("target_file")
        if _tf and _tf.endswith('.lua'):
            from _build_skeleton import ensure_skeleton
            from pathlib import Path as _Path
            _target_path = (ctx.project_root / _tf).resolve()
            if ensure_skeleton(_target_path, _target_path.stem):
                _skeleton_written += 1
    if _skeleton_written:
        print(f"  [Skeleton Builder] ✅ Wrote canonical skeleton for {_skeleton_written} new Lua file(s)")
    else:
        print(f"  [Skeleton Builder] ✓ All target files already have valid skeleton structure")

    # -- Monolithic Collapse Detection ---------------------------------------
    # When ALL tasks target the same single .lua file, the anchor-based
    # per-wave approach creates 17 serial waves with anchor drift and task
    # clobbering.  Instead, collapse into a single monolithic generation call
    # that produces the entire file in one shot, then route through the
    # existing review-fix loop.
    _single_lua_file = _detect_monolithic_lua_candidate(ctx.tasks_list)
    if _single_lua_file:
        print(f"\n  {'='*60}")
        print(f"  [Monolithic Collapse] All tasks target the same file:")
        print(f"    Target: {_single_lua_file}")
        print(f"    Tasks:  {len(ctx.tasks_list)} (collapsing to 1 generation call)")
        print(f"  {'='*60}")
        ctx = _run_monolithic_lua_generation(ctx, _single_lua_file)
        return ctx

    # -- Sort tasks into DAG waves ----------------------------------------
    waves = sort_tasks_into_waves(ctx.tasks_list, ctx=ctx)
    print(f"  [DAG] Sorted {len(ctx.tasks_list)} task(s) into {len(waves)} wave(s):")
    for i, wave in enumerate(waves):
        task_ids = ", ".join(f"Task {t['id']} [{t['domain']}]" for t in wave)
        print(f"    Wave {i+1}: {task_ids}")

    compiled_waves: Set[int] = set()

    def _get_target_model(t_dict: dict) -> str:
        domain = resolve_agent_name(t_dict["domain"])
        if domain in ("REVIEWER", "TRIBUNAL", "CONF"):
            return REVIEWER_MODEL
        # Prefer the live cartridge domain registry model over the kernel CODER_MODEL
        # so per-domain model assignments (e.g. C++ vs Lua) are respected.
        _live_reg = getattr(ctx, 'domain_registry', None) or {}
        return (
            _live_reg.get(domain, {}).get('model')
            or ALL_DOMAINS.get(domain, {}).get('model')
            or CODER_MODEL
        )

    # -- Process each wave ------------------------------------------------
    for wave_idx, wave in enumerate(waves):
        print(f"\n  {'='*60}")
        print(f"  Processing Wave {wave_idx + 1}/{len(waves)} ({len(wave)} task(s))")
        print(f"  {'='*60}")

        optimized_wave = sorted(wave, key=_get_target_model)

        current_active_model = None
        wave_results: Dict[str, str] = {}

        for t in optimized_wave:
            task = ctx.task_map.get(f"task_{t['id']}")
            if task is None or (task.task_id in ctx.processed_ids and not task.is_query):
                continue

            task_model = _get_target_model(t)

            if current_active_model is not None and current_active_model != task_model:
                print(f"  [VRAM Governor] Architecture boundary crossed. Unloading {current_active_model} weights...")
                unload_model(current_active_model)

            current_active_model = task_model

            ollama_params = None
            if task_model == CODER_MODEL:
                ollama_params = {"keep_alive": "15m"}
                print(f"  [VRAM Lock] Setting keep_alive=15m for {task.task_id} ({task.agent})")

            context_extra = ""
            if task.parent and task.parent in ctx.query_results:
                context_extra = f"## Answer from Query\n{ctx.query_results[task.parent]}"
            task.context = context_extra

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

            # -- Directive A: Sibling Context Manifest (Anti-Bloat) ------------
            # B5: For tasks with explicit DependsOn, inject the actual completed
            # output of each dependency (collapsed to a safe budget) so the agent
            # has a real anchor rather than just a title description.
            # For same-wave siblings we keep the manifest-only form (no code) to
            # avoid VRAM overrun from injecting multiple large code blocks.
            _DEP_CODE_BUDGET = 1200  # chars per dependency output, paged if larger
            sibling_parts = []
            dep_code_parts = []
            _task_depends_on = set(t.get("depends_on", []) or [])
            for completed_id, completed_code in ctx.all_results_dict.items():
                if completed_id == task.task_id:
                    continue
                completed_task = ctx.task_map.get(completed_id)
                if completed_task is None:
                    continue
                if completed_task.parent == task.parent:
                    agent_name = ALL_DOMAINS.get(
                        resolve_agent_name(completed_task.agent), {}
                    ).get("name", completed_task.agent)
                    actual_paths = []
                    for path_match in re.finditer(
                        r"(?:###|//|--)\s*(?:File:\s*)?([a-zA-Z0-9_/-]+\.(?:lua|cpp|h|hpp))",
                        completed_code
                    ):
                        actual_paths.append(path_match.group(1).strip())
                    paths_str = ", ".join(sorted(set(actual_paths))) if actual_paths else "no explicit files"
                    sibling_parts.append(
                        f"- {agent_name} ({completed_id}): \"{completed_task.spec}\" (Modified: {paths_str})"
                    )
                    # B5: inject collapsed actual output for declared dependencies
                    dep_raw_id = completed_id.replace("task_", "")
                    if dep_raw_id in _task_depends_on and completed_code and completed_code.strip():
                        _collapsed = TokenBudget._block_aware_collapse(completed_code, _DEP_CODE_BUDGET)
                        dep_code_parts.append(
                            f"## ❮ DEPENDENCY OUTPUT: {completed_id} [{agent_name}] ❯\n"
                            f"(collapsed to {_DEP_CODE_BUDGET} chars  use PAGE_IN for full content)\n"
                            f"```\n{_collapsed}\n```"
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
            if dep_code_parts:
                sibling_context += (
                    "\n\n## ❮ DEPENDENCY CODE SNAPSHOTS ❯\n"
                    "The following are collapsed snapshots of tasks you depend on.\n"
                    "Build on this code  do NOT rewrite it from scratch.\n\n"
                    + "\n\n".join(dep_code_parts)
                )

            # -- Directive B: Pro-Mode Inheritance  cached content ------------
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
                    f" Safe Cache, no disk I/O)\n\n"
                    + "\n".join(cache_blocks)
                )
                print(f"  [Paging Kernel] 📋 Injected {len(_inherited_cache)} cached blocks "
                      f"({total_chars} chars) into '{task.agent}' prompt")

            # -- Pro Mode: Adversarial TDD (per-task) ------------------------------
            _task_id_str = getattr(task, 'task_id', '')
            # pro_mode_always only suppresses the prompt; actual enrollment is
            # still gated by whether the task is in math_heavy_tasks.
            _task_pro_mode = _task_id_str in ctx.math_heavy_tasks
            pro_test_injection = ""
            if _task_pro_mode:
                pro_test_injection, ctx = _run_pro_mode_tdd(
                    task, ctx, _paged_inheritance_note, ollama_params
                )

            # -- Day 6: Math Analyst Deterministic Sandbox ---------------------
            _spec_lower = task.spec.lower()
            _is_binding_task = any(kw in _spec_lower for kw in [
                "bind", "expose", "wrapper", "bridge", "interface", "setup",
                "initialize", "create", "fixture", "game", "ramp", "attraction"
            ])
            _is_pure_math = any(kw in _spec_lower for kw in [
                "solve matrix", "floating-point problem", "pure physics math", "compute impulse vector"
            ])
            if not _is_binding_task and _is_pure_math:
                math_result = _run_math_analyst(task, ctx)
                if math_result:
                    task.context = (task.context or "") + math_result

            # -- Pro Mode: Multi-Draft Generation -----------------------------
            if _task_pro_mode and resolve_agent_name(task.agent) == "PHYS":
                output = _run_tribunal_merge(task, ctx, _paged_inheritance_note, ollama_params)
            else:
                if pro_test_injection:
                    task.context = (task.context or "") + pro_test_injection
                if resolve_agent_name(task.agent) in ("C++", "PHYS") and hasattr(ctx, 'interface_manifest'):
                    task.context = (task.context or "") + getattr(ctx, 'interface_manifest', '')

                # -- Completed-Work Anchor: inject per-task symbol TOC ---------
                # If this task writes to a file that was already approved in a
                # prior blueprint iteration, remind the coder agent of every
                # symbol that is already on disk so it does not re-implement them.
                _approved_snapshots: dict = getattr(ctx, 'completed_file_snapshots', {}) or {}
                if _approved_snapshots and task.target_file:
                    # Normalise path separators for lookup
                    _tf_norm = task.target_file.replace("\\", "/").lstrip("/")
                    _snap_content = None
                    for _snap_path, _snap_body in _approved_snapshots.items():
                        if _snap_path.replace("\\", "/").lstrip("/") == _tf_norm:
                            _snap_content = _snap_body
                            break
                    if _snap_content:
                        try:
                            from mesh_fetches import _extract_symbol_toc as _sym_toc
                            _symbols = _sym_toc(_snap_content, _tf_norm)
                        except Exception:
                            _symbols = []
                        if _symbols:
                            _sym_list = ", ".join(f"`{s}`" for s in _symbols)
                            _anchor = (
                                f"\n\n## ⚠️  ALREADY APPROVED  DO NOT RE-IMPLEMENT\n"
                                f"File `{_tf_norm}` was written and approved in a previous iteration.\n"
                                f"**Symbols already on disk:** {_sym_list}\n"
                                f"Your ONLY job is to APPEND new symbols that are absent from this list.\n"
                                f"Do NOT redefine, replace, or duplicate any symbol above.\n"
                                f"To read a full body: "
                                f"<invoke_kernel><action>PAGE_IN</action>"
                                f"<target>{_tf_norm}</target>"
                                f"<search>SYMBOL_NAME</search></invoke_kernel>\n"
                            )
                        else:
                            # File exists but no extractable symbols (data file etc.)
                            _char_count = len(_snap_content)
                            _anchor = (
                                f"\n\n## ⚠️  ALREADY APPROVED  DO NOT RE-IMPLEMENT\n"
                                f"File `{_tf_norm}` ({_char_count} chars) was written and approved "
                                f"in a previous iteration. Extend it  do NOT replace it.\n"
                            )
                        task.context = (task.context or "") + _anchor

                # -- Shared Integration Schema + Attraction Design Injection --
                # Inject once per task so every agent writes compatible code.
                try:
                    from integration_schema import get_schema_context_block
                    _schema_block = get_schema_context_block(ctx)
                    if _schema_block:
                        task.context = (task.context or "") + "\n\n" + _schema_block
                except Exception:
                    pass
                _design = getattr(ctx, 'attraction_design', None)
                if _design:
                    try:
                        _design_block = _design.to_context_block()
                        if _design_block:
                            task.context = (
                                (task.context or "")
                                + "\n\n## 🏗 Attraction Design Reference\n"
                                + _design_block
                            )
                    except Exception:
                        pass

                # -- Economy Mandate (Blueprint-phase enforcement) -------------
                # Injected here so the agent KNOWS the requirements before it
                # writes a single line  not discovered post-hoc in PhantomAPIGate.
                _scope_mode = getattr(ctx, 'scope_mode', '')
                _econ_hooks = []
                if _design:
                    try:
                        _econ_hooks = list(_design.economy_hooks or [])
                    except Exception:
                        pass
                if _scope_mode in ("NEW_ATTRACTION", "MODIFY_ATTRACTION") or _econ_hooks:
                    task.context = (task.context or "") + (
                        "\n\n## ECONOMY MANDATE (NON-NEGOTIABLE)\n"
                        "Your implementation MUST satisfy ALL of the following or it will be "
                        "rejected by the PhantomAPI Gate:\n"
                        "1. **Modifier consumption**  inside your `OnStep` callback, read "
                        "`AttractionConstants.modifiers` (or individual `ENGINE_MOD_*` globals) "
                        "every frame. NEVER cache modifier values at load time.\n"
                        "   Example: `local MOD = AttractionConstants.modifiers`\n"
                        "2. **Economy hook**  call `Engine.AwardTickets(n, label)` or "
                        "`Engine.AwardTokens(n, label)` on every win or score event.\n"
                        "   Use `Engine.GetStreak()` as a multiplier for ticket payouts.\n"
                        "   Example: `Engine.AwardTickets(score * Engine.GetStreak(), 'WIN')`\n"
                        "Omitting either of these will cause an automatic pipeline failure.\n"
                    )

                output = execute_task(
                    task, ctx.user_prompt, ctx.director_output,
                    ctx.all_results_dict, file_context, ctx.gdd_context,
                    sibling_context=sibling_context,
                    ollama_params=ollama_params,
                )

                # -- VRAM Circuit Breaker --------------------------------------
                from ollama_client import vram_overrun_abort, get_vram_abort_diagnostics
                if vram_overrun_abort():
                    _diag = get_vram_abort_diagnostics()
                    print(f"\n  [VRAM Circuit Breaker] ⛔ VRAM overrun detected in {task.task_id}. "
                          f"Aborting all remaining waves.\n{_diag}")
                    ctx.review_verdict = "BLOCKED"
                    ctx.final_verdict = "VRAM_OVERRUN"
                    ctx.final_output = (
                        f"## 🚨 Pipeline Aborted  VRAM Overrun\n\n"
                        f"Pipeline was aborted because token speed dropped below 2.0 tok/s.\n\n"
                        f"**Triggered in task:** {task.task_id} ({task.agent})\n\n"
                        f"**Diagnostics:**\n{_diag}\n"
                    )
                    return ctx

            ctx.all_results_dict[task.task_id] = output
            ctx.processed_ids.add(task.task_id)
            wave_results[task.task_id] = output

            # -- Register task output into the live integration schema ---------
            # This must run after the output is stored so conflict detection is
            # cumulative across the full wave.  Errors are non-fatal  a conflict
            # just adds a warning to the schema; it will surface in preflight.
            try:
                from integration_schema import update_schema_from_task
                _schema_conflicts = update_schema_from_task(ctx, task.task_id, output)
                if _schema_conflicts:
                    for _sc in _schema_conflicts:
                        print(f"  [IntegrationSchema] ⚠ Handle conflict: {_sc.name} "
                              f"(tasks: {', '.join(_sc.declared_by)})")
            except Exception:
                pass
            # Keep all_results list in sync with all_results_dict.
            _mt_found = False
            for _mt_i, _mt_e in enumerate(ctx.all_results):
                if _mt_e.get("task_id") == task.task_id:
                    ctx.all_results[_mt_i] = {"task_id": task.task_id, "output": output}
                    _mt_found = True
                    break
            if not _mt_found:
                ctx.all_results.append({"task_id": task.task_id, "output": output})

            # Process signals
            _signal_queue: deque = deque()
            _process_task_signals(ctx, task, _signal_queue)
            while _signal_queue:
                _sub = _signal_queue.popleft()
                if _sub.task_id not in ctx.task_map:
                    ctx.task_map[_sub.task_id] = _sub
                optimized_wave.append({
                    "id": _sub.task_id.replace("task_", "").replace("sub_", "").replace("query_", ""),
                    "domain": _sub.agent,
                    "title": _sub.spec,
                    "depends_on": [],
                })

        # -- Batch Compilation / Cartridge Validation ---------------------
        _cartridge_handled_build = False
        _cartridge_build_obj = getattr(ctx, "mounted_cartridge", None)
        if _cartridge_build_obj is not None and hasattr(_cartridge_build_obj, "validate_wave_output"):
            try:
                _build_ok, _build_err = _cartridge_build_obj.validate_wave_output(wave_results, ctx)
                _cartridge_handled_build = True
                if not _build_ok:
                    print(f"  [Cartridge Validator] Wave {wave_idx + 1} validation failed")
                    ctx.pre_flight_errors += f"\n## Wave {wave_idx + 1} Cartridge Errors:\n```\n{_build_err}\n```"
                    for tid in wave_results:
                        ctx.retry_counts[tid] = ctx.retry_counts.get(tid, 0) + 1
                    for tid in wave_results:
                        if ctx.retry_counts.get(tid, 0) >= 3:
                            ctx.review_verdict = "BLOCKED"
                            break
                else:
                    print(f"  [Cartridge Validator] Wave {wave_idx + 1} validated successfully ✅")
            except Exception as _cv_err:
                print(f"  [Cartridge Validator] Error during cartridge validation: {_cv_err}")

        wave_has_cpp = (not _cartridge_handled_build) and any(
            resolve_agent_name(t["domain"]) in ("C++", "PHYS") and
            ("cpp" in ctx.all_results_dict.get(f"task_{t['id']}", "").lower() or
             ".h" in ctx.all_results_dict.get(f"task_{t['id']}", ""))
            for t in wave
        )
        if wave_has_cpp and wave_idx not in compiled_waves:
            _run_cmake_fallback(ctx, wave_idx, wave_results)
            compiled_waves.add(wave_idx)

        if ctx.review_verdict == "BLOCKED":
            break

    return ctx


# -- Private phase helpers ----------------------------------------------------

def _run_pro_mode_tdd(task, ctx: PipelineContext, paged_note: str, ollama_params):
    """Adversarial TDD: write a failing test then return the injection string."""
    print(f"\n  [Pro Mode] Adversarial TDD: Routing {task.task_id} to Lead Architect for test generation...")

    _domain = resolve_agent_name(task.agent)
    _domain_bracket_match = re.search(r'\[([A-Za-z0-9_+#]+)\]', task.spec)
    if _domain_bracket_match:
        _domain = resolve_agent_name(_domain_bracket_match.group(1))
    _alias = getattr(ctx, 'alias_map', {}).get(_domain.upper(), _domain.upper())
    _domain = _alias
    _meta = getattr(ctx, 'domain_metadata_registry', {}).get(_domain)
    if _meta is None:
        print(f"  [Pro Mode] WARNING: No domain metadata for '{_domain}'  pro-mode TDD skipped for this task.")
        ctx.math_heavy_tasks.discard(getattr(task, 'task_id', ''))
        ctx.pro_mode = bool(ctx.math_heavy_tasks)
        return "", ctx

    _lang = _meta["language"]
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
        "math, logic, or behavior described in the task specification.\n"
        "CRITICAL SYNTAX MANDATES:\n"
        "1. Output ONLY perfectly valid, executable source code inside a single code block.\n"
        "2. Ensure every opening parenthesis, bracket, and block has a matching closure.\n"
        "3. Do NOT append loose comma-separated strings outside valid assertion statements.\n"
        "4. Do NOT modify any existing files or write implementation code.\n"
        "The test MUST fail initially due to missing implementation, but MUST compile flawlessly."
    )
    test_writer_prompt = (
        f"## Task Specification\n{task.spec}\n\n"
        f"## User's Feature Request\n{ctx.canonical_request}\n\n"
        f"## Director's Task Breakdown\n{ctx.director_output}\n\n"
        f"{paged_note}"
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
        test_writer_system, test_writer_prompt,
        f"Test Architect ({task.task_id})", REVIEWER_MODEL,
    )
    _code_block_regex = rf"```(?:{_code_block_tag}|C\+\+|cxx)?\s*\n(.*?)```"
    test_match = re.search(_code_block_regex, test_code, re.DOTALL)
    test_body = test_match.group(1).strip() if test_match else test_code.strip()
    test_dir = ctx.project_root / "tests"
    test_dir.mkdir(parents=True, exist_ok=True)
    test_file_path = test_dir / f"test_{task.task_id}{_meta['extension']}"
    atomic_write_text(test_file_path, test_body)
    print(f"  [Pro Mode] Adversarial TDD: Saved test to {test_file_path}")
    # Persist path on the task so fix cycles can re-inject the contract
    task.tdd_test_path = str(test_file_path)

    pro_test_injection = (
        f"\n\n---\n"
        f"## ADVERSARIAL TDD: Failing Unit Test Written by Lead Architect\n"
        f"Here is a failing unit test written by the Lead Architect:\n"
        f"```{_code_block_tag}\n{test_body}\n```\n"
        f"Write the {_lang} implementation to make this test pass. "
        f"You are strictly forbidden from modifying the test file at `{test_file_path}`."
    )
    return pro_test_injection, ctx


def _run_math_analyst(task, ctx: PipelineContext) -> str:
    """Run deterministic Math Analyst subprocess. Returns injected output string or ''."""
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

    from _pipeline_helpers import call_ollama as _call, REASONING_MODEL
    raw_math_code = _call(math_system, math_prompt, f"Math Analyst ({task.task_id})", REASONING_MODEL)
    math_code_match = re.search(r"```python\s*\n(.*?)```", raw_math_code, re.DOTALL)
    math_code = math_code_match.group(1).strip() if math_code_match else raw_math_code.strip()
    atomic_write_text(math_script_path, math_code)

    try:
        proc = subprocess.run(
            [sys.executable, str(math_script_path)],
            capture_output=True, text=True, timeout=15
        )
        if proc.returncode == 0:
            result = (
                f"\n\n## DETERMINISTIC MATH ANALYST OUTPUT\n"
                f"The following guaranteed numerical values were computed natively via `{math_script_path.name}`:\n"
                f"```\n{proc.stdout.strip()}\n```\n"
                f"You MUST utilize these exact floating-point values in your implementation."
            )
            print(f"  [Math Analyst] Guaranteed results computed successfully ✅")
            return result
        else:
            print(f"  [Math Analyst] Execution failed, falling back to probabilistic synthesis: {proc.stderr.strip()[:200]}")
    except Exception as e:
        print(f"  [Math Analyst] Subprocess error: {e}")
    return ""


def _run_tribunal_merge(task, ctx: PipelineContext, paged_note: str, ollama_params) -> str:
    """N-version consensus: generate 3 drafts then merge via Tribunal."""
    from _pipeline_helpers import execute_task as _exec, call_ollama as _call
    print(f"\n  [Pro Mode] N-Version Consensus: Generating 3 drafts for {task.task_id}...")
    temperatures = [0.2, 0.5, 0.8]
    drafts = {}

    _draft_results_snapshot = dict(ctx.all_results_dict)
    _draft_cache_snapshot = dict(getattr(task, 'paged_files_cache', {}))

    file_context = ""
    try:
        files = find_relevant_files(task.spec, task.agent)
        file_context = format_file_context(files, domain_key=task.agent)
    except Exception:
        pass

    for i, temp in enumerate(temperatures):
        draft_label = f"{chr(65+i)} (t={temp})"
        print(f"    Draft {draft_label}...")
        ctx.all_results_dict.clear()
        ctx.all_results_dict.update(_draft_results_snapshot)
        if hasattr(task, 'paged_files_cache'):
            task.paged_files_cache = dict(_draft_cache_snapshot)
        draft_output = _exec(
            task, ctx.user_prompt, ctx.director_output,
            ctx.all_results_dict, file_context, ctx.gdd_context,
            ollama_params={"temperature": temp, **(ollama_params or {})},
        )
        drafts[f"draft_{chr(65+i)}"] = draft_output

    print(f"  [Pro Mode] Tribunal merging 3 drafts for {task.task_id}...")

    _domain_trib = resolve_agent_name(task.agent)
    _dbm = re.search(r'\[([A-Za-z0-9_+#]+)\]', task.spec)
    if _dbm:
        _domain_trib = resolve_agent_name(_dbm.group(1))
    _alias_trib = getattr(ctx, 'alias_map', {}).get(_domain_trib.upper(), _domain_trib.upper())
    _domain_trib = _alias_trib
    _meta_trib = getattr(ctx, 'domain_metadata_registry', {}).get(_domain_trib) or {
        "language": _domain_trib,
        "test_framework": "plain assertions",
        "code_tag": "text",
        "extension": ".txt",
        "assert_examples": "",
    }
    _lang_trib = _meta_trib["language"]
    _domain_mandate_trib = (
        f"[SYSTEM KERNEL MANDATE: You are operating in the strictly enforced "
        f"{_domain_trib} domain. You MUST write your merged code exclusively in "
        f"{_lang_trib}. Do not use C++ if this is a {_lang_trib} task, "
        f"and vice versa.]\n\n"
    )
    tribunal_system = _domain_mandate_trib + (
        "You are the TRIBUNAL ARCHITECT. Evaluate three approaches, discard hallucinations, "
        "and output a single synthesized master solution.\n\n"
        "CRITICAL RULES:\n"
        "1. Identify and discard hallucinated API calls or impossible physics.\n"
        "2. Cross-validate assertions: if only one draft makes a claim, it's likely a hallucination.\n"
        "3. If two drafts agree on an approach, preserve that consensus.\n"
        "4. Output ONLY the merged, synthesized code  no commentary, no evaluation report.\n"
        "5. Use SEARCH/REPLACE diff format if modifying existing files, or full file content for new files."
    )
    tribunal_prompt = (
        f"## Original Task Specification\n{task.spec}\n\n"
        f"## User's Feature Request\n{ctx.canonical_request}\n\n"
        f"## Director's Task Breakdown\n{ctx.director_output}\n\n"
        f"## Draft A (temperature=0.2  conservative)\n{drafts['draft_A'][:2000]}\n\n"
        f"## Draft B (temperature=0.5  balanced)\n{drafts['draft_B'][:2000]}\n\n"
        f"## Draft C (temperature=0.8  creative)\n{drafts['draft_C'][:2000]}\n\n"
        f"{paged_note}"
        f"---\n"
        f"Evaluate the three approaches. Discard hallucinations. "
        f"Cross-validate mathematical assertions. "
        f"Output a single, synthesized master solution file."
    )
    tribunal_output = _call(
        tribunal_system, tribunal_prompt,
        f"Tribunal Merge ({task.task_id})", DIRECTOR_MODEL,
    )
    from _helpers_text import strip_to_code_artifacts
    output = strip_to_code_artifacts(tribunal_output, fallback_truncation=1200)
    print(f"  [Pro Mode] Tribunal merge complete for {task.task_id}")
    return output


def _run_cmake_fallback(ctx: PipelineContext, wave_idx: int, wave_results: dict) -> None:
    """Fallback cmake/make batch compilation for C++ waves when cartridge has no validator."""
    _cmake_cache = ctx.project_root / "CMakeCache.txt"
    _makefile = ctx.project_root / "Makefile"
    _infra_kws = (
        "could not load cache", "no cmake_cache", "run cmake first",
        "cmake error", "cmake warning", "configuring incomplete",
    )
    print(f"  [Batch Compiler] Verifying Wave {wave_idx + 1} C++ output...")
    try:
        if sys.platform == "win32":
            if not _cmake_cache.is_file():
                print(f"  [Batch Compiler] No CMakeCache.txt  skipping wave {wave_idx + 1} build check.")
                return
            cmake_build = subprocess.run(
                ["cmake", "--build", "."],
                capture_output=True, text=True, cwd=ctx.project_root,
                shell=True, timeout=30,
            )
            if cmake_build.returncode != 0:
                err_tail = "\n".join(cmake_build.stderr.splitlines()[-50:])
                if any(kw in err_tail.lower() for kw in _infra_kws):
                    print(f"  [Batch Compiler] cmake infrastructure error suppressed (wave {wave_idx + 1}): {err_tail[:120]}")
                    return
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
        else:
            if not _makefile.is_file():
                print(f"  [Batch Compiler] No Makefile  skipping wave {wave_idx + 1} build check.")
                return
            make_process = subprocess.run(
                ["make", "-j4"], capture_output=True, text=True,
                cwd=ctx.project_root, timeout=30,
            )
            if make_process.returncode != 0:
                err_tail = "\n".join(make_process.stderr.splitlines()[-50:])
                if any(kw in err_tail.lower() for kw in _infra_kws):
                    print(f"  [Batch Compiler] make infrastructure message suppressed (wave {wave_idx + 1}): {err_tail[:120]}")
                    return
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


def _process_task_signals(ctx: PipelineContext, task, work_queue: deque) -> None:
    """Process signals emitted by a completed task."""
    if task.is_query:
        ctx.query_results[task.parent] = task.output
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
                if not (signal.get("content", "") and signal["content"] in v.get("task_id", ""))
            ]

        elif stype == "REJECT":
            ctx.tribunal_verdicts[task.agent] = f"REJECT:{signal['content']}"

        # Directive C: Legacy signal handlers MATH_EVAL, FETCH, READ_OFFLOADED,
        # and EXTRACT_SKELETON have been PURGED. They are entirely superseded
        # by the <invoke_kernel> XML schema in the PagingKernel.

    # -- Double-check unresolved items -----------------------------------------
    if task.double_check and task.double_check["unresolved"]:
        unresolved = task.double_check["unresolved"].strip()
        if unresolved and unresolved.lower() not in ("none", "n/a", "nothing", ""):
            _MAX_DOUBLE_CHECK_ITERS = 2
            _dc_iters = getattr(task, '_double_check_iters', 0)
            if _dc_iters < _MAX_DOUBLE_CHECK_ITERS:
                task._double_check_iters = _dc_iters + 1
                task.completed = False
                work_queue.appendleft(task)

    # -- Snapshot save ----------------------------------------------------------
    if ctx.snapshot:
        try:
            _merged_registry = {**ALL_DOMAINS, **getattr(ctx, 'domain_registry', {})}
            persona = _merged_registry.get(resolve_agent_name(task.agent), {}).get("name", task.agent)
            ctx.snapshot.save_agent_output(persona, len(ctx.processed_ids), task.output)
        except Exception as e:
            print(f"  [Snapshot] Save error: {e}")


# ==============================================================================
#  Monolithic Lua Generation (anti-fragmentation)
# ==============================================================================
# When all 17 tasks target the same single .lua file, the per-task anchor-patch
# approach creates serial waves, anchor drift, and clobbering.  Instead, detect
# this pattern and issue ONE whole-file generation call, then route the result
# through the existing Luacheck and review-fix loop.
#
# Detection: _detect_monolithic_lua_candidate(tasks_list) -> str | None
# Execution: _run_monolithic_lua_generation(ctx, target_file) -> PipelineContext

def _detect_monolithic_lua_candidate(tasks_list: list) -> str | None:
    """Return target_file if ALL tasks write to the same .lua file."""
    target_files = set()
    for t in tasks_list:
        tf = t.get("target_file")
        if tf is None:
            return None
        target_files.add(tf.replace("\\", "/").lower())
    if len(target_files) == 1:
        sole = next(iter(target_files))
        if sole.endswith(".lua"):
            return sole
    return None


def _run_monolithic_lua_generation(ctx: PipelineContext, target_file: str) -> PipelineContext:
    """Single-shot whole-file generation for a Lua attraction.

    Assembles a comprehensive prompt containing:
      - The canonical skeleton (already written by Phase A)
      - All 17 task specs as a consolidated feature list
      - The GDD extract with economy mandates
      - The Attraction Design doc (if available)
      - Bridge contract / Lua rules

    The LLM outputs a COMPLETE replacement file (not SEARCH/REPLACE diffs).
    The output is validated with luac, and the review-fix loop handles errors.
    """
    from _pipeline_helpers import execute_task
    from _build_skeleton import inject_skeleton
    from pathlib import Path

    # -- Ensure skeleton exists --
    _project_root = Path(ctx.project_root)
    _target_abs = (_project_root / target_file).resolve()
    inject_skeleton(_target_abs, _target_abs.stem)
    print(f"  [Monolithic] Skeleton written: {_target_abs}")

    # -- Read current skeleton content --
    _live_content = _target_abs.read_text(encoding="utf-8") if _target_abs.is_file() else ""

    # -- Build consolidated task spec --
    _task_lines = []
    for t in ctx.tasks_list:
        _id = t.get("id", "?")
        _domain = t.get("domain", "Lua")
        _title = t.get("title", "")
        _hooks = t.get("hooks", "None")
        _task_lines.append(f"### Task {_id}: [{_domain}] - {_title}")
        _task_lines.append(f"Hooks: {_hooks}")
        _task_lines.append("")  # blank line separator
    _consolidated_spec = "\n".join(_task_lines)

    # -- Gather GDD context --
    _gdd_block = ctx.gdd_context or ""

    # -- Gather design block --
    _design_block = ""
    _design = getattr(ctx, 'attraction_design', None)
    if _design:
        try:
            _design_block = _design.to_context_block()
        except Exception:
            _design_block = ""

    # -- Gather economy mandate --
    _scope_mode = getattr(ctx, 'scope_mode', '')
    _econ_mandate = ""
    if _scope_mode in ("NEW_ATTRACTION", "MODIFY_ATTRACTION"):
        _econ_mandate = (
            "\n\n## ECONOMY MANDATE (NON-NEGOTIABLE)\n"
            "Your implementation MUST satisfy ALL of the following:\n"
            "1. **Modifier consumption** - inside your OnStep callback, read "
            "AttractionConstants.modifiers every frame. NEVER cache at load time.\n"
            "2. **Economy hook** - call Engine.AwardTickets(n, label) or "
            "Engine.AwardTokens(n, label) on every win/score event.\n"
            "   Use Engine.GetStreak() as a multiplier for ticket payouts.\n"
        )

    # -- Read bridge contract, Lua rules, and canonical skeleton --
    _lua_rules = ""
    _rules_path = _project_root / "docs" / "rules_lua.md"
    if _rules_path.is_file():
        _lua_rules = _rules_path.read_text(encoding="utf-8")[:3000]

    _bridge = ""
    _bridge_path = _project_root / ".." / "midway" / "docs" / "engine_lua_bridge_contract.md"
    if _bridge_path.is_file():
        _bridge = _bridge_path.read_text(encoding="utf-8")[:4000]

    # -- Read the canonical skeleton structure --
    _canonical_skeleton = ""
    try:
        from _build_skeleton import SKELETON_TEMPLATE
        _canonical_skeleton = SKELETON_TEMPLATE
    except Exception:
        _canonical_skeleton = ""

    # Extract approved physics API names from the bridge contract
    _approved_physics_apis = ""
    _approved_api_patterns = [
        r'SpawnStaticBox\b', r'SpawnStaticBoxR\b', r'SpawnStaticSphere\b',
        r'SpawnDynamicSphere\b', r'SpawnDynamicBox\b', r'SpawnDynamicCapsule\b',
        r'DestroyBody\b', r'ApplyImpulse\b', r'GetVelocity\b',
        r'MoveKinematic\b', r'CreatePool\b',
        r'SpawnSharedBooth\b',
        r'OnStep\b', r'AwardTickets\b', r'AwardTokens\b', r'GetStreak\b',
        r'AttractionConstants\.modifiers\b', r'Engine\.\w+\b',
        r'MidwayPhysics\.\w+\b', r'MidwayInput\.\w+\b',
    ]
    _found_apis = set()
    for _pat in _approved_api_patterns:
        _matches = re.findall(_pat, _bridge)
        for _m in _matches:
            _found_apis.add(_m)
    if _found_apis:
        _approved_physics_apis = ", ".join(sorted(_found_apis))

    # -- Assemble the improved monolithic prompt --
    _monolithic_system = (
        "You are a Lua attraction engineer for a game engine. "
        "Your ONLY job is to write a COMPLETE, production-ready Lua file "
        "for a single attraction.  You will receive:\n"
        "1. The current skeleton file (you MUST fill in every anchor hook)\n"
        "2. A consolidated list of ALL tasks that must be implemented\n"
        "3. The GDD extract with economy rules\n"
        "4. The Lua rules and bridge contract\n\n"
        "CRITICAL DO-NOT-USE LIST:\n"
        "You MUST NOT use any of the following APIs, function names, or patterns:\n"
        "- MidwayPhysics.PoolAcquire\n"
        "- MidwayPhysics.PoolReturn\n"
        "- MidwayPhysics.IsSensorTriggered  (use a sensor callback instead)\n"
        "- SkeeballGame (no global game object)\n"
        "- OnPlayerAim, OnPlayerPowerUp, OnThrow, OnCollisionWithTarget\n"
        "- Any function named On* that is NOT OnLoadStatic, OnLoad, or OnUnload\n\n"
        "APPROVED PHYSICS APIS (use ONLY these):\n"
        f"{_approved_physics_apis or 'See the bridge contract in the prompt below'}\n\n"
        "BALL PHYSICS MODEL:\n"
        "- Balls move via physics simulation (gravity, friction, restitution).\n"
        "- The engine handles physics; Lua just sets initial impulses via ApplyImpulse().\n"
        "- Do NOT implement manual velocity arithmetic in Lua.\n"
        "- Do NOT call SetLinearVelocity, SetGravityFactor, or ApplyForce on balls.\n"
        "- Do NOT tick ball positions manually in OnStep; let the physics sim move them.\n\n"
        "SYNTAX & OUTPUT REQUIREMENTS:\n"
        "- The generated file MUST pass `luac -p` syntax check on first try.\n"
        "- Wrap the Lua code inside a single ```lua ... ``` code block.\n"
        "  NO SEARCH/REPLACE blocks, NO explanatory prose outside the fence.\n"
        "- The fence MUST be at the outermost level (no nested fences).\n"
        "- Do NOT include any text before or after the code block.\n\n"
        "CANONICAL SKELETON STRUCTURE (preserve EXACTLY):\n"
        f"{_canonical_skeleton}\n\n"
        "STRUCTURAL MANDATES:\n"
        "- OnLoadStatic() MUST call SpawnSharedBooth() FIRST.\n"
        "- OnLoad() MUST register MidwayPhysics.OnStep(function(dt) ... end).\n"
        "- OnUnload() MUST exist for cleanup (print diagnostics only; pools auto-reclaimed).\n"
        "- OnStep callback MUST read AttractionConstants.modifiers every frame (inner scope).\n"
        "- OnStep callback MUST call Engine.AwardTickets(n, label) with "
        "Engine.GetStreak() multiplier on score events.\n"
        "- Every Lua function must have a matching 'end'.\n"
        "- Every opened table '{' must have a matching '}'.\n"
        "- Do NOT use undefined globals. Every non-builtin must be declared with 'local'.\n"
        "- Global variable SLOT_ID = BOOTH_SLOT_ID or -1 MUST be present at module level.\n"
        "- local CONST = {} MUST be present at module level (constants table placeholder).\n"
        "- Module-level state variables (balls table, remainingBalls, currentScore, "
        "aimAngle, powerLevel) MUST be declared at module scope, not in functions.\n"
        "- 6 balls (remainingBalls = 6), decremented per throw, round ends when count = 0.\n"
    )

    _monolithic_prompt = (
        f"## User's Feature Request\n{ctx.canonical_request}\n\n"
        f"## Consolidated Task List\n{_consolidated_spec}\n\n"
        f"## Game Design Document Extract\n{_gdd_block[:5000]}\n\n"
        f"{_design_block}"
        f"{_econ_mandate}"
        f"\n\n## Engine Bridge Contract\n{_bridge}\n\n"
        f"## Lua Rules\n{_lua_rules}\n\n"
        f"## CURRENT SKELETON FILE: {target_file}\n"
        f"```lua\n{_live_content}\n```\n\n"
        f"---\n"
        f"Write the COMPLETE implementation for `{target_file}`. "
        f"Fill in every anchor hook with working code. "
        f"Output the ENTIRE file inside ONE ```lua code block. "
        f"Do NOT omit OnLoadStatic, OnLoad, or OnUnload. "
        f"Do NOT use SEARCH/REPLACE - output the full file."
    )

    # -- Call the LLM once (not 17 times) --
    _model = CODER_MODEL
    # Increase num_predict for monolithic generation: a full attraction file
    # can exceed the default 4096-token generation limit.
    _mono_params = {"num_predict": 8192}
    print(f"  [Monolithic] Calling {_model} for whole-file generation (num_predict=8192)...")
    raw_output = call_ollama(
        _monolithic_system,
        _monolithic_prompt,
        f"Monolithic Lua ({target_file})",
        _model,
        params=_mono_params,
    )

    # -- Extract code block --
    _code_match = re.search(r"```lua\s*\n(.*?)```", raw_output, re.DOTALL)
    _generated_code = _code_match.group(1).strip() if _code_match else raw_output.strip()

    # -- Write to file --
    _target_abs.write_text(_generated_code, encoding="utf-8")
    print(f"  [Monolithic] Wrote {len(_generated_code)} chars to {target_file}")

    # -- Run luac for syntax check --
    import subprocess
    _luac_proc = subprocess.run(
        ["luac", "-p", str(_target_abs)],
        capture_output=True, text=True, timeout=15,
    )
    if _luac_proc.returncode == 0:
        print(f"  [Monolithic] ✅ luac syntax check passed")
    else:
        _err = _luac_proc.stderr.strip()
        print(f"  [Monolithic] ⚠ luac syntax error: {_err[:200]}")
        ctx.pre_flight_errors += (
            f"\n## Monolithic Generation Syntax Error ({target_file})\n"
            f"```\n{_err}\n```\n"
        )

    # -- Apply deterministic post-processing --
    try:
        from _post_process_lua import post_process_lua_file
        post_process_lua_file(_target_abs)
    except Exception as e:
        print(f"  [Monolithic] Post-process error: {e}")

    # -- Store monolithic target for downstream detection (Bugs B/C) --
    ctx._monolithic_lua_target = target_file

    # -- Populate ctx with a synthetic task result --
    _synthetic_task_id = "task_monolithic"
    ctx.all_results_dict[_synthetic_task_id] = _generated_code
    ctx.processed_ids.add(_synthetic_task_id)
    ctx.all_results.append({
        "task_id": _synthetic_task_id,
        "output": _generated_code,
    })

    # -- Set review context so Phase 5/6 review-fix loop can validate --
    ctx.final_output = _generated_code
    ctx.pre_flight_errors = getattr(ctx, 'pre_flight_errors', "") + (
        f"\n## Monolithic Generation\n"
        f"File: {target_file}\n"
        f"Size: {len(_generated_code)} chars\n"
        f"luac: {'PASS' if _luac_proc.returncode == 0 else 'FAILED'}\n"
    )

    print(f"  [Monolithic] Generation complete. Routing to review-fix loop...")
    return ctx
