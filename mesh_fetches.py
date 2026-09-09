"""
mesh_fetches.py  Phase 0.53: Scope gate, librarian, context, director
========================================================================
Extracted from mesh_loops.py to keep individual files under 1 000 lines.

run_fetches(ctx) handles:
  - Resurrection bypass (BLOCKED checkpoint resume)
  - Domain Consultant / Unavailable Domain Gate
  - Workspace discovery & structure curation
  - Auto-feeder (blueprint-driven continuous execution)
  - Lead Producer / Scope Gate (NARROW vs TOO_BROAD)
  - Blueprint generation & approval
  - Phase 3: Director task decomposition
  - Interface Manifest derivation
"""

from __future__ import annotations

import re
from datetime import datetime
from token_budget import TokenBudget
from _pipeline_helpers import (
    REASONING_MODEL, DIRECTOR_MODEL,
    ALL_DOMAINS, AGENT_ALIAS_MAP,
    DIRECTOR_SYSTEM,
    resolve_agent_name,
    curate_project_structure,
    call_ollama,
    build_director_prompt,
    atomic_write_text, trigger_chime,
    PipelineContext,
    AGENT_FILE_TOOLS_PROMPT, handle_file_read, handle_file_list,
    build_blueprint_context_pack,
)
from pipeline import (
    get_unavailable_domains_text,
    parse_file_references, fetch_referenced_files, set_referenced_files_cache,
)
# Sub-module re-exports so callers that import directly from mesh_fetches still work.
from mesh_fetches_helpers import (  # noqa: F401
    _classify_blueprint_scope,
    _build_scope_annotated_ast,
    _sanitize_attraction_path,
    _extract_symbol_toc,
    _build_attraction_design_block,
    _build_completed_work_snapshot,
    _build_director_scope_mandate,
    _build_scope_constraint_text,
    _enrich_blueprint_tasks,
)
from mesh_fetches_blueprint import _run_blueprint_phase  # noqa: F401
from mesh_fetches_director import _run_director_phase  # noqa: F401


def run_fetches(ctx: PipelineContext) -> PipelineContext:
    """Phase 0.53: Scope gate, GDD librarian, file context, director decomposition.

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
    # If the prompt ends with '?' it's a question  never blueprint it
    is_read_only_question = (
        prompt_lower.endswith("?")
        or any(prompt_lower.startswith(kw) for kw in read_only_keywords)
    )

    if is_auto_feed_request and not blueprint_path.is_file():
        print("  [ERROR] No prompt provided and no blueprint found.")
        ctx.final_output = "Failed to start."
        return ctx

    # ── Domain Consultant (Dichotomy Gate) ──────────────────────────────────
    # Scans user prompt for keywords related to dormant/unavailable domains.
    # Keywords are supplied by the mounted cartridge via `dormant_domain_keywords`;
    # if the cartridge does not supply them, the gate is skipped gracefully.
    if not is_read_only_question and not is_auto_feed_request:
        # Read dormant-domain keywords from the mounted cartridge  never hardcode here.
        _cartridge = getattr(ctx, "mounted_cartridge", None)
        dormant_domain_keywords: dict = {}
        if _cartridge is not None and hasattr(_cartridge, "dormant_domain_keywords"):
            dormant_domain_keywords = _cartridge.dormant_domain_keywords or {}
        elif hasattr(ctx, "_cartridge_class") and hasattr(ctx._cartridge_class, "get_dormant_domain_keywords"):
            dormant_domain_keywords = ctx._cartridge_class.get_dormant_domain_keywords() or {}
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
            print(f"  🔮 DOMAIN CONSULTANT  Unavailable Domain Gate")
            print(f"{'='*50}")
            print(f"  Your prompt references features from unavailable domain(s):")
            for d in sorted(set(found_dormant_domains)):
                print(f"    - {d}")

            prompt_lower_check = ctx.user_prompt.lower()
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
                print()
                print("  ┌─ DOMAIN CONSULTANT GATE ──────────────────────────────────────────┐")
                print("  │ The feature you requested touches a code domain (e.g. a C++       │")
                print("  │ physics system or external service) that this pipeline cannot      │")
                print("  │ write real code for right now.                                    │")
                print("  │                                                                   │")
                print("  │ Answering YES tells the pipeline to generate a safe placeholder   │")
                print("  │ (stub/wireframe) with TODO markers so you can fill it in later.   │")
                print("  │ Answering NO carries on as-is  the affected domain will simply   │")
                print("  │ be skipped or produce an incomplete result.                       │")
                print("  └───────────────────────────────────────────────────────────────────┘")
                from pipeline import AUTO_APPROVE_GATES as _auto_wf
                if _auto_wf:
                    wireframe_choice = "n"
                    print(f"  [Domain Consultant] Gate auto-skipped (AUTO_APPROVE_GATES=True)  continuing as-is.")
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
                    print(f"  [Domain Consultant] Wireframe mode activated  placeholder instruction appended.")
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

    registered_symbols = ", ".join(sorted(active_topology.classes)) if active_topology.classes else "None Detected"
    print(f"  [Librarian] Verified active C++ AST symbols: {len(active_topology.classes)}")

    unlogged_manifest = ""
    if active_topology.uninstrumented_files:
        _files_str = "\n".join(f"  - {f}" for f in active_topology.uninstrumented_files[:10])
        unlogged_manifest = (
            f"\n[SYSTEM KERNEL AUDIT: The following active files currently lack observability instrumentation. "
            f"If your SEARCH/REPLACE blocks touch these modules, you MUST inject valid logging calls.]\n{_files_str}\n"
        )
        print(f"  [Librarian] Flagged {len(active_topology.uninstrumented_files)} un-instrumented files for pre-flight safety.")

    ctx.project_state = (
        f"## Active Repository Topology\n"
        f"Registered C++ Implementation Symbols (internal engine layer  NOT gameplay APIs, do NOT reference in feature analysis): {registered_symbols}\n"
        f"{unlogged_manifest}\n"
        f"[SYSTEM KERNEL: Essential documentation and module implementations are available via dynamic paging. "
        f"Use your strict <invoke_kernel> XML tools to pre-mount reference blocks securely.]"
    )

    ctx.output_parts.append("\n## Phase 1 & 2: Autonomic Workspace Discovery\n" + ctx.project_state + "\n")

    # ── Scope classification (shared by blueprint AND director phases) ────────
    # On blueprint continuation iterations the user_prompt has already been
    # replaced by the next task description (e.g. "Implement ball launch").
    # Re-classifying against that text always returns GENERAL, which drops the
    # A3 C++/PHYS guard and allows the director to emit out-of-scope tasks.
    # Solution: when a non-GENERAL scope is already pinned on ctx from the
    # first iteration, skip re-classification and reuse the existing values.
    _existing_scope = getattr(ctx, '_scope_mode', 'GENERAL')
    if is_auto_feed_request and _existing_scope != 'GENERAL':
        _s_mode   = _existing_scope
        _s_target = getattr(ctx, '_scope_target', '')
        _s_refs   = getattr(ctx, '_scope_refs',   [])
        print(f"  [Scope Classifier] Mode={_s_mode} (inherited from blueprint session)"
              + (f", target={_s_target}" if _s_target else ""))
    else:
        # Computed once here so both NARROW (director-only) and TOO_BROAD (blueprint
        # → director) paths receive identical scope constraints.
        _s_mode, _s_target, _s_refs = _classify_blueprint_scope(
            ctx.user_prompt, ctx.project_root, active_topology
        )
        # When the classifier returns NEW_ATTRACTION with an empty target name
        # (e.g. Pass 0 fires because the attractions dir was empty), try to
        # recover the attraction name directly from the user prompt so that
        # downstream scoped extraction and the GDD re-extract can use it.
        if _s_mode == "NEW_ATTRACTION" and not _s_target:
            import re as _re_scope
            _creation_verbs_scope = (
                "build", "create", "make", "implement", "write", "add", "develop", "design",
            )
            _stop_scope = {
                "this", "that", "with", "from", "your", "their", "have", "will",
                "using", "make", "want", "need", "should", "only", "into", "also",
                "basic", "simple", "please", "just", "some", "more",
            }
            _pl = ctx.user_prompt.lower()
            for _v in _creation_verbs_scope:
                _m = _re_scope.search(
                    r'\b' + _v + r'\b\s+(?:(?:me|us|a|an|the|basic|simple|new)\s+)*(\b[a-z]{4,}\b)',
                    _pl,
                )
                if _m and _m.group(1) not in _stop_scope:
                    _s_target = _m.group(1)
                    break
            if not _s_target:
                _words = _re_scope.findall(r'\b[a-z]{4,}\b', _pl)
                _s_target = next((w for w in _words if w not in _stop_scope
                                   and not any(_re_scope.search(r'\b' + v + r'\b', w)
                                               for v in _creation_verbs_scope)), "")
        print(f"  [Scope Classifier] Mode={_s_mode}"
              + (f", target={_s_target}" if _s_target else "")
              + (f", {len(_s_refs)} reference-only file(s)" if _s_refs else ""))

    ctx._scope_mode   = _s_mode
    ctx._scope_target = _s_target
    ctx._scope_refs   = _s_refs

    # ── Bridge exclusion list (shared) ───────────────────────────────────────
    # A3: Use the Lua-callable form of each name so the director recognises
    # Engine.GetStreak etc. as already live rather than bare dict keys.
    ctx._bridge_exclusion_text = ""
    ctx._bridge_exclusion_set: set = set()  # used by A4 task validator
    try:
        _build_bridge_excl = getattr(ctx, '_cartridge_build_bridge_contract', None)
        if callable(_build_bridge_excl):
            _bc_excl = _build_bridge_excl()
            _excl_lines: list[str] = []
            # Section → Lua prefix mapping; unmapped sections use bare name.
            _section_prefix = {
                "midwayphysics_spawn_api": "MidwayPhysics",
                "object_pools": "MidwayPhysics",
                "economy_api": "Engine",
                "globals_injected": None,   # bare globals, not callable
                "modifier_globals": None,
                "script_lifecycle": None,
                "load_order": None,
                "win_banners": None,
            }
            for _sec, _sv in _bc_excl.items():
                _pfx = _section_prefix.get(_sec, "")
                if _pfx is None:
                    continue  # skip non-callable sections
                if isinstance(_sv, dict):
                    for _fn in _sv.keys():
                        _lua_name = f"{_pfx}.{_fn}" if _pfx else _fn
                        _excl_lines.append(_lua_name)
                        ctx._bridge_exclusion_set.add(_fn.lower())
                        ctx._bridge_exclusion_set.add(_lua_name.lower())
                elif isinstance(_sv, list):
                    for _item in _sv:
                        _fn = str(_item).split("/")[0].split("(")[0].strip()
                        if _fn:
                            _lua_name = f"{_pfx}.{_fn}" if _pfx else _fn
                            _excl_lines.append(_lua_name)
                            ctx._bridge_exclusion_set.add(_fn.lower())
                            ctx._bridge_exclusion_set.add(_lua_name.lower())
            if _excl_lines:
                ctx._bridge_exclusion_text = (
                    "\n\n## BRIDGE CONTRACT  ALREADY IMPLEMENTED\n"
                    "The following Lua-callable names are ALREADY exposed via the sol2 bridge.\n"
                    "Do NOT plan any C++ task to add or re-expose these:\n"
                    + "\n".join(f"  - {fn}" for fn in _excl_lines)
                    + "\nOnly plan a C++ bridge task for a primitive provably absent from this list."
                )
    except Exception as _exc_e:
        print(f"  [Scope Classifier] Bridge exclusion list error: {_exc_e}")

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

    # ── Generic Documentation Context Extraction via Mounted Cartridge ─────
    if not ctx.gdd_context:
        try:
            # Prefer the scoped variant when we already know the attraction.
            _s_mode = getattr(ctx, '_scope_mode', 'GENERAL')
            _s_target = getattr(ctx, '_scope_target', '') or ''
            _get_ctx_scoped = getattr(ctx, '_cartridge_get_project_context_scoped', None)
            _get_ctx_fn = getattr(ctx, '_cartridge_get_project_context', None)
            if _get_ctx_scoped is not None:
                ctx.gdd_context = _get_ctx_scoped(ctx.user_prompt,
                                                   scope_mode=_s_mode,
                                                   attraction_name=_s_target)
                if ctx.gdd_context:
                    print(f"  [Kernel] Cartridge provided scoped context "
                          f"(scope={_s_mode}, attraction='{_s_target}', "
                          f"{len(ctx.gdd_context)} chars)")
                else:
                    print(f"  [Kernel] Cartridge returned no relevant project context")
            elif _get_ctx_fn is not None:
                ctx.gdd_context = _get_ctx_fn(ctx.user_prompt)
                if ctx.gdd_context:
                    print(f"  [Kernel] Cartridge provided context ({len(ctx.gdd_context)} chars)")
                else:
                    print(f"  [Kernel] Cartridge returned no relevant project context")
            else:
                print(f"  [Kernel] No mounted cartridge with get_project_context; skipping context extraction")
        except Exception as e:
            print(f"  [Kernel] Cartridge context extraction error: {e}")

    # ── Bridge Contract Injection into Agent Context ──────────────────────
    # Append the exhaustive approved API list to gdd_context so that ALL
    # domain agents (Lua, C++, etc.) during execution AND fix cycles see
    # the correct function names.  This prevents phantom API hallucinations
    # such as SpawnDynamicBall that arise when agents only read the system
    # prompt's general prohibition without an explicit approved name list.
    try:
        _build_bridge_fn = getattr(ctx, '_cartridge_build_bridge_contract', None)
        if callable(_build_bridge_fn):
            _bc_exec = _build_bridge_fn()
            _api_exec = list((_bc_exec.get("midwayphysics_spawn_api") or {}).keys())
            _pool_exec = list((_bc_exec.get("object_pools") or {}).keys())
            _econ_exec = list((_bc_exec.get("economy_api") or {}).keys())
            if _api_exec:
                _bridge_exec_snippet = (
                    "\n\n## Active Bridge Contract \u2014 ALL Approved APIs (exhaustive)\n"
                    "You MUST use ONLY these exact function names. Any other name is a phantom.\n"
                    "Physics: " + ", ".join(_api_exec) + "\n"
                    "Object Pools: " + ", ".join(_pool_exec) + "\n"
                    "Economy: " + ", ".join(_econ_exec) + "\n"
                    "CRITICAL SUBSTITUTION GUIDE:\n"
                    "  SpawnDynamicBall \u2192 SpawnDynamicSphere(lx, ly, lz, radius)\n"
                    "  SpawnDynamicBody / SpawnStaticBody (generic) \u2192 use typed variants above\n"
                    "  RemoveBody / ReleaseHandle / DestroyEntity \u2192 DestroyBody(handle)\n"
                    "  CheckCollision \u2192 IsSensorTriggered(handle)\n"
                    "  GetLinearVelocity \u2192 GetVelocity(handle)\n"
                    "  MoveKinematic(handle, vec) \u2192 MoveKinematic(handle, lx, ly, lz, dt)\n"
                    "  sol.on_load/on_step/on_unload/set_function \u2192 define bare global functions\n"
                )
                ctx.gdd_context = (ctx.gdd_context or "") + _bridge_exec_snippet
                print(f"  [Kernel] Bridge contract appended to agent context "
                      f"({len(_bridge_exec_snippet)} chars)")
    except Exception as _e:
        print(f"  [Kernel] Bridge contract injection error: {_e}")

    # ── Auto-Feeder ──────────────────────────────────────────────────────
    if is_auto_feed_request:
        # Option C: Bulk-Enriched Path  consume next pre-enriched task header
        # directly from ctx._enriched_blueprint_tasks without calling the
        # Director LLM.  Also advance the blueprint file's [ ] → [x] so the
        # visual state stays in sync.
        _enriched_tasks = getattr(ctx, '_enriched_blueprint_tasks', [])
        if _enriched_tasks:
            _next_task = _enriched_tasks.pop(0)
            # ── A3 Guard: Override C++/PHYS domains to Lua for NEW_ATTRACTION ─
            # The Blueprint Enricher may assign [C++] domain to tasks even though
            # the scope mandate says Lua-only. Override the domain unconditionally.
            _scope_is_new = getattr(ctx, '_scope_mode', 'GENERAL') == "NEW_ATTRACTION"
            if _scope_is_new and _next_task.get("domain", "").upper() in ("C++", "CPP", "PHYS"):
                _old_domain = _next_task["domain"]
                _next_task["domain"] = "Lua"
                print(f"  [Auto-Feeder] 🔧 Overrode task {_next_task['id']} domain from [{_old_domain}] → [Lua] (NEW_ATTRACTION scope)")
            ctx.tasks_list = [_next_task]
            _task_title = _next_task.get("title", "").split(" - ")[0]
            _orig_req = getattr(ctx, '_original_user_prompt', '') or _task_title
            ctx.user_prompt = (
                f"<execution_environment>\n"
                f"  <system_directives>\n"
                f"    You are operating within an isolated expert domain. Focus compilation strictly on the target subtask scope below.\n"
                f"  </system_directives>\n"
                f"  <macro_invariants>\n"
                f"    {_orig_req.strip()}\n"
                f"  </macro_invariants>\n"
                f"  <target_subtask_scope>\n"
                f"    {_task_title}\n"
                f"  </target_subtask_scope>\n"
                f"</execution_environment>\n\n"
                f"INSTRUCTION: Implement ONLY the functionality defined inside <target_subtask_scope> while adhering strictly to <macro_invariants>."
            )
            # Advance the blueprint file so the visual checkbox state matches.
            if blueprint_path.is_file():
                _bp_content = blueprint_path.read_text(encoding="utf-8")
                _bp_match = re.search(r"^[-\*]?\s*\[ \]\s*(?:Task \d+:\s*)?(.+)", _bp_content, re.MULTILINE)
                if _bp_match:
                    _bp_raw = _bp_match.group(0)
                    _bp_content_new = _bp_content.replace(_bp_raw, _bp_raw.replace("[ ]", "[x]", 1), 1)
                    atomic_write_text(blueprint_path, _bp_content_new)
            # Signal the pipeline loop to continue if more enriched tasks remain.
            if _enriched_tasks:
                ctx._blueprint_continue = True
            print(f"  [Auto-Feeder] Consumed enriched task {_next_task['id']}: {_task_title}")
            return ctx

        # Fallback (no enriched tasks): advance blueprint normally
        if blueprint_path.is_file():
            content = blueprint_path.read_text(encoding="utf-8")
            match = re.search(
                r"^[-\*]?\s*\[ \]\s*(?:Task \d+:\s*)?(.+)",
                content, re.MULTILINE
            )
            if match:
                raw_line = match.group(0)
                task_text = match.group(1).strip()
                # Use the persisted original request for <macro_invariants> so
                # every continuation task agent sees the full feature context,
                # not just the bare task title.
                _orig_req = getattr(ctx, '_original_user_prompt', '') or task_text
                ctx.user_prompt = (
                    f"<execution_environment>\n"
                    f"  <system_directives>\n"
                    f"    You are operating within an isolated expert domain. Focus compilation strictly on the target subtask scope below.\n"
                    f"  </system_directives>\n"
                    f"  <macro_invariants>\n"
                    f"    {_orig_req.strip()}\n"
                    f"  </macro_invariants>\n"
                    f"  <target_subtask_scope>\n"
                    f"    {task_text}\n"
                    f"  </target_subtask_scope>\n"
                    f"</execution_environment>\n\n"
                    f"INSTRUCTION: Implement ONLY the functionality defined inside <target_subtask_scope> while adhering strictly to <macro_invariants>."
                )
                print(f"  [Lead Producer] Auto-feeding next task: {task_text}")
                new_content = content.replace(raw_line, raw_line.replace("[ ]", "[x]", 1), 1)
                atomic_write_text(blueprint_path, new_content)
            else:
                print("  [Lead Producer] Blueprint complete. Nothing to do.")
                ctx.final_output = "Blueprint complete."
                return ctx

    # ── Phase 0.5: Lead Producer (Scope Gate) ───────────────────────────────
    if not is_auto_feed_request:
        gdd_snippet = TokenBudget._block_aware_collapse(ctx.gdd_context, 2500) if ctx.gdd_context else "(no GDD context)"
        state_snippet = TokenBudget._block_aware_collapse(ctx.project_state, 2000) if ctx.project_state else "(no project state)"

        # ── Pre-Blueprint Clarification Gate ─────────────────────────────────
        # If the feature request is too vague to blueprint safely, present the
        # user with concrete implementation options and require a choice.  Runs
        # on DIRECTOR_MODEL (llama3.1:8b), which is still resident from intent
        # classification, so this adds latency but NO extra model reload.
        from pipeline import CLARIFY_VAGUE_REQUESTS as _clarify_enabled
        if _clarify_enabled:
            from clarification_gate import run_clarification_gate
            if not run_clarification_gate(ctx, gdd_snippet, state_snippet):
                return ctx

        if getattr(ctx, '_scope_mode', 'GENERAL') == "NEW_ATTRACTION":
            # NEW_ATTRACTION ALWAYS routes to the blueprint phase regardless of
            # the scope-gate verdict (TOO_BROAD / NARROW / None all force
            # blueprint in the routing below), so the LLM scope gate is pure
            # wasted latency for these requests.  Skip it and go straight to
            # the blueprint phase.
            print(f"\n  [Lead Producer] Scope is NEW_ATTRACTION — scope gate skipped "
                  f"(blueprint is mandatory for new attractions).")
            ctx = _run_blueprint_phase(ctx, blueprint_path, gdd_snippet, state_snippet)
            if getattr(ctx, 'final_output', None) in ("Pipeline abandoned by user at Blueprint Gate.", "Blueprint complete."):
                return ctx
        elif is_read_only_question:
            print(f"\n  [Lead Producer] Prompt looks like a read-only question. "
                  f"Passing through to Phase 1 (Librarian) instead of blueprint generation.")
            print(f"  [Lead Producer] Prompt: {ctx.user_prompt[:80]}")
        else:
            glossary_map = getattr(ctx, 'active_glossary_registry', {}) or {}
            definitions_str = "\n".join(f"  - '{k}': {v}" for k, v in glossary_map.items())

            req_parts = re.split(r'\[block\]', ctx.user_prompt, maxsplit=1, flags=re.IGNORECASE)
            pos_request = req_parts[0].strip()
            neg_block_str = (
                f"\n\n## EXPLICITLY BLOCKED / EXCLUDED CONCEPTS\n"
                f"The following elements MUST NOT be implemented or planned for:\n{req_parts[1].strip()}"
                if len(req_parts) > 1 else ""
            )

            base_scope_prompt = (
                f"Analyze this feature request: '{pos_request}'.{neg_block_str}\n\n"
                f"## Local Repository Terminology Registry\n"
                f"Interpret all acronyms strictly according to the following active mappings:\n"
                f"{definitions_str}\n\n"
                f"## Relevant Source Documentation Context\n{gdd_snippet}\n\n"
                f"## Current Project State\n{state_snippet}\n\n"
                f"CRITICAL DIRECTIVES:\n"
                f"1. Resolve acronyms exclusively via the Terminology Registry. Do NOT hallucinate external engine terminology.\n"
                f"2. MANDATORY CHAIN-OF-THOUGHT: You MUST compute your workload analysis step by step inside an explicit structural scratchpad BEFORE deciding the routing path.\n"
                f"3. Blocked/excluded concepts MUST be skipped entirely  do NOT plan workarounds, "
                f"do NOT factor them into complexity, do NOT mention them as absent systems that need solving. "
                f"Exclusions may appear as explicit [block] tags OR as natural-language phrases in the feature text "
                f"(e.g. 'not in place yet', 'not available', 'no X yet', 'skip', 'ignore'). Both forms are equally binding.\n"
                f"4. SCOPE ANALYSIS CONSTRAINT: Base your analysis ONLY on the feature request text, the GDD context, and the explicitly blocked concepts. "
                f"The C++ symbols in the topology section are opaque internal engine classes  they are NOT gameplay systems, NOT feature requirements, and MUST NOT influence your analysis or verdict.\n"
                f"5. FORMATTING MANDATE: You MUST format your output exactly as follows:\n"
                f"<analysis>\n"
                f"Step 0: [Scan the raw feature text for ANY phrase indicating something is unavailable, not yet built, skipped, or out of scope "
                f"(e.g. 'not in place yet', 'not available', 'skip', 'ignore', 'no X', 'without X'). "
                f"List every such item here as EXCLUDED. If the EXPLICITLY BLOCKED section is present, add those too. "
                f"These items are now off-limits for the rest of your analysis.]\n"
                f"Step 1: [Identify what the feature request is actually asking to BUILD, ignoring everything listed as excluded in Step 0]\n"
                f"Step 2: [List only the required gameplay systems from Step 1. Confirm excluded items from Step 0 are absent from this list.]\n"
                f"Step 3: [Assess implementation complexity purely on the required systems from Step 2. Excluded concepts contribute ZERO complexity.]\n"
                f"</analysis>\n"
                f"[VERDICT: <NARROW or TOO_BROAD>]"
            )

            scope_system_persona = (
                "You are the Lead Producer orchestration gate. "
                "CRITICAL: You are strictly FORBIDDEN from front-loading your decision. "
                "You MUST compute your workload evaluation step by step inside explicit <analysis> ... </analysis> tags FIRST. "
                "Output exactly ONE literal bracketed verdict tag on its own line strictly AFTER the closing </analysis> tag. "
                "Valid options are ONLY [VERDICT: NARROW] or [VERDICT: TOO_BROAD]."
            )

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
                    REASONING_MODEL,
                    params={"num_predict": 1024},
                )

                from ollama_client import is_fatal_ollama_error as _is_fatal_scope
                if _is_fatal_scope(scope_eval):
                    print(f"  [Lead Producer] ⛔ Ollama error during Scope Gate  aborting pipeline.")
                    ctx.final_output = f"Pipeline aborted: Ollama unreachable during Scope Gate. {scope_eval.strip()}"
                    return ctx
                all_verdicts = list(re.finditer(
                    r"^\s*"
                    r"(?:\[\s*)?"                          # optional opening [
                    r"(?:\*{0,2}\s*VERDICT\s*:\s*){1,4}"  # 1-4 repetitions of VERDICT:
                    r"\*{0,2}\s*"
                    r"(?:\[\s*)?"                          # optional bracket around value
                    r"\*{0,2}\s*"
                    r"(TOO_BROAD|NARROW)"
                    r"\s*\*{0,2}"
                    r"(?:\s*\]){0,2}\s*$",                # optional closing brackets (inner + outer)
                    scope_eval,
                    re.IGNORECASE | re.MULTILINE | re.VERBOSE,
                ))
                if all_verdicts:
                    # Accept the verdict wherever the model placed it.  Requiring
                    # it strictly AFTER the </analysis> tag made verbose models
                    # retry pointlessly when they emitted a readable verdict
                    # earlier in the output.
                    final_verdict = all_verdicts[-1].group(1).upper()
                    break
                else:
                    feedback = (
                        "\n\n[SYSTEM KERNEL ERROR: Your previous output failed to comply with the mandatory Chain-of-Thought sequence. "
                        "You either omitted the <analysis> ... </analysis> scratchpad entirely, or emitted the verdict tag prematurely before concluding your reasoning. "
                        "You MUST self-correct: compute your analysis inside <analysis> ... </analysis> FIRST, and place exactly [VERDICT: NARROW] or [VERDICT: TOO_BROAD] strictly AFTER the closing </analysis> tag.]"
                    )
                    current_scope_prompt = base_scope_prompt + f"\n\n## PREVIOUS REJECTED ANALYSIS:\n{scope_eval}\n" + feedback

            # ── Deterministic Path Routing ────────────────────────────────────
            # FIX 2026-06-04: Force blueprint for ALL NEW_ATTRACTION requests.
            # Previously the NARROW+NEW_ATTRACTION bypass skipped the blueprint
            # phase entirely, using a monolithic single-file path that produced
            # only 9 anchor-based tasks. This was insufficient — the GDD
            # Future Attractions section specifies 12+ tasks per attraction.
            # The blueprint phase now always runs for NEW_ATTRACTION, and the
            # _enrich_blueprint_tasks step ensures the Director decomposes the
            # full workload into the correct number of tasks.
            #
            # The old NARROW → monolithic path (ctx._scope_narrow_single_file)
            # is REMOVED. All NEW_ATTRACTION requests go through blueprint.
            #
            # Edge cases:
            #   - None (exhausted retries) + NEW_ATTRACTION → fallback to blueprint
            if final_verdict is None and getattr(ctx, '_scope_mode', 'GENERAL') == "NEW_ATTRACTION":
                print(f"\n  [Lead Producer] Scope gate exhausted retries  — "
                      f"NEW_ATTRACTION requires fallback to blueprint.")
                final_verdict = "TOO_BROAD"


            if final_verdict == "TOO_BROAD":
                print(f"\n  [Lead Producer] Evaluated Verdict: TOO_BROAD. Generating architectural blueprint...")
                ctx = _run_blueprint_phase(ctx, blueprint_path, gdd_snippet, state_snippet)
                if getattr(ctx, 'final_output', None) in ("Pipeline abandoned by user at Blueprint Gate.", "Blueprint complete."):
                    return ctx
            elif final_verdict == "NARROW":
                _scope_is_new = getattr(ctx, '_scope_mode', 'GENERAL') == "NEW_ATTRACTION"
                if _scope_is_new:
                    print(f"\n  [Lead Producer] Evaluated Verdict: NARROW but scope is NEW_ATTRACTION — "
                          f"forcing blueprint phase (GDD requires 12+ tasks).")
                    ctx = _run_blueprint_phase(ctx, blueprint_path, gdd_snippet, state_snippet)
                    if getattr(ctx, 'final_output', None) in ("Pipeline abandoned by user at Blueprint Gate.", "Blueprint complete."):
                        return ctx
                else:
                    print(f"\n  [Lead Producer] Evaluated Verdict: NARROW. Advancing directly to task decomposition.")
            else:
                _scope_is_new = getattr(ctx, '_scope_mode', 'GENERAL') == "NEW_ATTRACTION"
                if _scope_is_new:
                    print(f"\n  [Lead Producer] WARNING: Failed to extract valid verdict after {max_scope_attempts} attempts. "
                          f"NEW_ATTRACTION forces blueprint fallback.")
                    ctx = _run_blueprint_phase(ctx, blueprint_path, gdd_snippet, state_snippet)
                    if getattr(ctx, 'final_output', None) in ("Pipeline abandoned by user at Blueprint Gate.", "Blueprint complete."):
                        return ctx
                else:
                    print(f"\n  [Lead Producer] WARNING: Failed to extract valid verdict after {max_scope_attempts} attempts. Defaulting to NARROW pass-through.")

    # ── Phase 3: Director ─────────────────────────────────────────────────
    ctx = _run_director_phase(ctx)
    return ctx

