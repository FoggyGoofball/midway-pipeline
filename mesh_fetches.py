"""
mesh_fetches.py — Phase 0.5–3: Scope gate, librarian, context, director
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

    # ── Domain Consultant (Dichotomy Gate) ──────────────────────────────────
    # Scans user prompt for keywords related to dormant/unavailable domains.
    # Keywords are supplied by the mounted cartridge via `dormant_domain_keywords`;
    # if the cartridge does not supply them, the gate is skipped gracefully.
    if not is_read_only_question and not is_auto_feed_request:
        # Read dormant-domain keywords from the mounted cartridge — never hardcode here.
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
            print(f"  🔮 DOMAIN CONSULTANT — Unavailable Domain Gate")
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
                print("  │ Answering NO carries on as-is — the affected domain will simply   │")
                print("  │ be skipped or produce an incomplete result.                       │")
                print("  └───────────────────────────────────────────────────────────────────┘")
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
        f"Registered C++ Implementation Symbols (internal engine layer — NOT gameplay APIs, do NOT reference in feature analysis): {registered_symbols}\n"
        f"{unlogged_manifest}\n"
        f"[SYSTEM KERNEL: Essential documentation and module implementations are available via dynamic paging. "
        f"Use your strict <invoke_kernel> XML tools to pre-mount reference blocks securely.]"
    )

    ctx.output_parts.append("\n## Phase 1 & 2: Autonomic Workspace Discovery\n" + ctx.project_state + "\n")

    # ── Scope classification (shared by blueprint AND director phases) ────────
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

    ctx._scope_mode = _s_mode
    ctx._scope_target = _s_target
    ctx._scope_refs = _s_refs
    print(f"  [Scope Classifier] Mode={_s_mode}"
          + (f", target={_s_target}" if _s_target else "")
          + (f", {len(_s_refs)} reference-only file(s)" if _s_refs else ""))

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
                    "\n\n## BRIDGE CONTRACT — ALREADY IMPLEMENTED\n"
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

    # ── Phase 0.5: Lead Producer (Scope Gate) ───────────────────────────────
    if not is_auto_feed_request:
        gdd_snippet = TokenBudget._block_aware_collapse(ctx.gdd_context, 2500) if ctx.gdd_context else "(no GDD context)"
        state_snippet = TokenBudget._block_aware_collapse(ctx.project_state, 2000) if ctx.project_state else "(no project state)"
        if is_read_only_question:
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
                f"3. Blocked/excluded concepts MUST be skipped entirely — do NOT plan workarounds, "
                f"do NOT factor them into complexity, do NOT mention them as absent systems that need solving. "
                f"Exclusions may appear as explicit [block] tags OR as natural-language phrases in the feature text "
                f"(e.g. 'not in place yet', 'not available', 'no X yet', 'skip', 'ignore'). Both forms are equally binding.\n"
                f"4. SCOPE ANALYSIS CONSTRAINT: Base your analysis ONLY on the feature request text, the GDD context, and the explicitly blocked concepts. "
                f"The C++ symbols in the topology section are opaque internal engine classes — they are NOT gameplay systems, NOT feature requirements, and MUST NOT influence your analysis or verdict.\n"
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
                    REASONING_MODEL
                )

                from ollama_client import is_fatal_ollama_error as _is_fatal_scope
                if _is_fatal_scope(scope_eval):
                    print(f"  [Lead Producer] ⛔ Ollama error during Scope Gate — aborting pipeline.")
                    ctx.final_output = f"Pipeline aborted: Ollama unreachable during Scope Gate. {scope_eval.strip()}"
                    return ctx
                all_verdicts = list(re.finditer(
                    r"^\s*(?:\[\s*)?\*?\*?\s*VERDICT\s*\*?\*?\s*:\s*\*?\*?\s*"
                    r"(?:\[\s*(?:VERDICT\s*:\s*)?)?"
                    r"(TOO_BROAD|NARROW)"
                    r"\s*\*?\*?\s*(?:\])?\s*(?:\])?\s*$",
                    scope_eval,
                    re.IGNORECASE | re.MULTILINE,
                ))
                analysis_end = scope_eval.rfind("</analysis>")

                if all_verdicts and analysis_end != -1 and all_verdicts[-1].start() > analysis_end:
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
            # A2: If the scope classifier already determined this is a NEW_ATTRACTION,
            # a NARROW verdict from the scope gate is almost certainly wrong — a fresh
            # attraction file always warrants a blueprint.  Override to TOO_BROAD.
            if final_verdict == "NARROW" and getattr(ctx, '_scope_mode', 'GENERAL') == "NEW_ATTRACTION":
                print(f"\n  [Lead Producer] Scope override: NARROW → TOO_BROAD "
                      f"(scope classifier identified a NEW_ATTRACTION request).")
                final_verdict = "TOO_BROAD"

            if final_verdict == "TOO_BROAD":
                print(f"\n  [Lead Producer] Evaluated Verdict: TOO_BROAD. Generating architectural blueprint...")
                ctx = _run_blueprint_phase(ctx, blueprint_path, gdd_snippet, state_snippet)
                if getattr(ctx, 'final_output', None) in ("Pipeline abandoned by user at Blueprint Gate.", "Blueprint complete."):
                    return ctx
            elif final_verdict == "NARROW":
                print(f"\n  [Lead Producer] Evaluated Verdict: NARROW. Advancing directly to task decomposition.")
            else:
                print(f"\n  [Lead Producer] WARNING: Failed to extract valid verdict after {max_scope_attempts} attempts. Defaulting to NARROW pass-through.")

    # ── Phase 3: Director ─────────────────────────────────────────────────
    ctx = _run_director_phase(ctx)
    return ctx


# ── Private helpers ──────────────────────────────────────────────────────────

# ── Blueprint scope classifier ───────────────────────────────────────────────

def _classify_blueprint_scope(
    user_prompt: str,
    project_root,
    ast_topology,
) -> tuple[str, str, list[str]]:
    """Classify whether a request targets a NEW attraction or MODIFIES an existing one.

    Returns:
        (mode, target, refs)
        mode   : 'NEW_ATTRACTION' | 'MODIFY_ATTRACTION' | 'GENERAL'
        target : canonical filename stem matched in the AST index (or '' if NEW/GENERAL)
        refs   : list of AST-indexed attraction file paths that are read-only context
    """
    if ast_topology is None:
        return ("GENERAL", "", [])

    # Enumerate all attraction scripts from the AST index.
    all_attractions: list[str] = []
    for path_str in ast_topology.file_index.keys():
        # Normalize to forward slashes for matching.
        norm = path_str.replace("\\", "/")
        if "attractions/" in norm and norm.endswith(".lua"):
            all_attractions.append(path_str)

    prompt_lower = user_prompt.lower()

    import os as _os

    # ── Pass 0: creation-verb detection runs even when the attractions
    # directory is empty (new project, first attraction) so we never bail
    # to GENERAL for a clear "build/create/make X" request. ──────────────
    if not all_attractions:
        _creation_verbs_early = (
            "build", "create", "make", "implement", "write", "add", "develop", "design",
        )
        if any(re.search(r'\b' + v + r'\b', prompt_lower) for v in _creation_verbs_early):
            return ("NEW_ATTRACTION", "", [])
        return ("GENERAL", "", [])

    # ── Pass 1: explicit new-attraction keywords ──────────────────────────
    _new_keywords = (
        "new attraction", "create attraction", "create a new", "add a new attraction",
        "build a new", "make a new", "new game", "new booth", "implement a new",
        "write a new", "scaffold",
    )
    if any(kw in prompt_lower for kw in _new_keywords):
        # Try to extract the attraction name from the prompt even for explicit
        # new-attraction phrasing (e.g. "create a new skeeball attraction").
        _stop_pass1 = {
            "this", "that", "with", "from", "your", "their", "have", "will",
            "using", "make", "want", "need", "should", "only", "into", "also",
            "basic", "simple", "please", "just", "some", "more", "attraction",
            "booth", "game", "new", "create", "build", "add", "implement",
            "write", "scaffold",
        }
        _all_stems_p1 = {
            _os.path.splitext(_os.path.basename(p))[0].lower()
            for p in all_attractions
        }
        _p1_nouns = [
            w for w in re.findall(r'\b[a-z]{4,}\b', prompt_lower)
            if w not in _stop_pass1 and w not in _all_stems_p1
        ]
        _p1_name = _p1_nouns[0] if _p1_nouns else ""
        return ("NEW_ATTRACTION", _p1_name, list(all_attractions))

    # ── Pass 2: existing stem match → MODIFY ─────────────────────────────
    matched_target = ""
    for path_str in all_attractions:
        stem = _os.path.splitext(_os.path.basename(path_str))[0].lower()
        if stem and len(stem) >= 4 and stem in prompt_lower:
            matched_target = path_str
            break

    if matched_target:
        refs = [p for p in all_attractions if p != matched_target]
        return ("MODIFY_ATTRACTION", matched_target, refs)

    # ── Pass 3: creation verb + noun NOT found in any existing stem → NEW ─
    # Catches phrasing like 'build me a basic skeeball game' where 'skeeball'
    # does not yet exist as a file in the AST index.
    _creation_verbs = (
        "build", "create", "make", "implement", "write", "add", "develop", "design",
    )
    _has_creation_verb = any(
        re.search(r'\b' + v + r'\b', prompt_lower) for v in _creation_verbs
    )
    if _has_creation_verb:
        # Extract candidate nouns (words ≥4 chars, not common stopwords)
        _stopwords = {
            "this", "that", "with", "from", "your", "their", "have", "will",
            "using", "make", "want", "need", "should", "only", "into", "also",
            "basic", "simple", "please", "just", "some", "more",
            # NOTE: 'game' intentionally excluded — it is a valid noun that
            # identifies a new attraction type ('skeeball game', 'pinball game').
        }
        _all_stems = {
            _os.path.splitext(_os.path.basename(p))[0].lower()
            for p in all_attractions
        }
        _words = re.findall(r'\b[a-z]{4,}\b', prompt_lower)
        _unknown_nouns = [
            w for w in _words
            if w not in _stopwords and w not in _creation_verbs and w not in _all_stems
        ]
        if _unknown_nouns:
            # Prefer the word that immediately follows a creation verb
            # (e.g. "build me a skeeball game" → "skeeball").
            # Only fall back to _unknown_nouns[0] when no verb-adjacent
            # noun is found — avoids picking up preamble words like "refer".
            _attraction_name = ""
            for _v in _creation_verbs:
                _m = re.search(
                    r'\b' + _v + r'\b\s+(?:(?:me|us|a|an|the|basic|simple|new)\s+)*(\b[a-z]{4,}\b)',
                    prompt_lower,
                )
                if _m and _m.group(1) in _unknown_nouns:
                    _attraction_name = _m.group(1)
                    break
            if not _attraction_name:
                _attraction_name = _unknown_nouns[0]
            return ("NEW_ATTRACTION", _attraction_name, list(all_attractions))

    # Ambiguous / non-attraction request — no constraint injected.
    return ("GENERAL", "", [])


def _build_scope_annotated_ast(
    scope_mode: str,
    scope_target: str,
    scope_refs: list[str],
    ast_topology,
) -> str:
    """Return a scope-annotated version of the AST summary.

    Files in `scope_refs` are marked [REFERENCE ONLY — do NOT make this a task target].
    If scope_mode is GENERAL or ast_topology is None, the raw summary is returned unchanged.
    """
    if ast_topology is None or scope_mode == "GENERAL" or not scope_refs:
        return ast_topology.format_ast_summary() if ast_topology else ""

    raw_summary = ast_topology.format_ast_summary()
    import os as _os
    for ref_path in scope_refs:
        # Match by basename so the annotation survives any path prefix in the summary.
        basename = _os.path.basename(ref_path)
        raw_summary = raw_summary.replace(
            basename,
            f"{basename} [REFERENCE ONLY — do NOT make this a task target]",
        )
    if scope_mode == "NEW_ATTRACTION":
        raw_summary = (
            "## SCOPE: NEW ATTRACTION\n"
            "You are scaffolding a BRAND-NEW attraction file.\n"
            "Files marked [REFERENCE ONLY] below are examples to read for style ONLY.\n"
            "Do NOT generate any tasks that modify those files.\n\n"
        ) + raw_summary
    elif scope_mode == "MODIFY_ATTRACTION":
        target_name = _os.path.basename(scope_target)
        raw_summary = (
            f"## SCOPE: MODIFY EXISTING ATTRACTION — {target_name}\n"
            f"All tasks MUST target '{target_name}' or shared infrastructure only.\n"
            "Files marked [REFERENCE ONLY] below MUST NOT appear as task targets.\n\n"
        ) + raw_summary
    return raw_summary


def _build_director_scope_mandate(ctx: "PipelineContext") -> str:
    """Return scope + bridge-exclusion text for the director prompt.

    Reads the scope classification that was computed once in run_fetches() and
    stored on ctx so both NARROW (director-only) and TOO_BROAD (blueprint →
    director) paths receive identical constraints.
    """
    import os as _os
    scope_mode   = getattr(ctx, '_scope_mode',   'GENERAL')
    scope_target = getattr(ctx, '_scope_target',  '')
    scope_refs   = getattr(ctx, '_scope_refs',    [])
    bridge_excl  = getattr(ctx, '_bridge_exclusion_text', '')

    parts: list[str] = []

    if scope_mode == "NEW_ATTRACTION":
        ref_names = ", ".join(_os.path.basename(p) for p in scope_refs[:6])
        suffix = ", ..." if len(scope_refs) > 6 else ""
        parts.append(
            f"## SCOPE MANDATE — NEW ATTRACTION\n"
            f"You are decomposing tasks for a BRAND-NEW attraction file.\n"
            f"The new file MUST be placed under the `attractions/` directory "
            f"(e.g. `attractions/skeeball/skeeball.lua`).\n"
            f"You MUST NOT assign any task to an existing attraction file "
            f"({ref_names}{suffix}).\n"
            f"Those files are read-only context — never task targets.\n"
            f"DOMAIN RULE: ALL tasks for a new attraction are [Lua] ONLY. "
            f"Do NOT emit any [C++] tasks. "
            f"The engine bridge already exposes everything you need via MidwayPhysics. "
            f"A missing primitive is NEVER a reason to add a [C++] task here.\n"
            f"\nSINGLE-FILE CONSOLIDATION RULE:\n"
            f"All tasks for a new attraction write to the SAME single .lua file. "
            f"Therefore you MUST produce AT MOST 2 tasks:\n"
            f"  Task 1: Write the complete skeleton — OnLoad, OnStep, OnUnload stubs, "
            f"all state variables, the ball/physics setup, and the scoring logic in one file.\n"
            f"  Task 2 (ONLY if genuinely separate): Add a distinct secondary system "
            f"(e.g. economy ticket payout, meta-progression hooks) that cannot fit in Task 1.\n"
            f"Do NOT split a single Lua file into 3-5 tasks. Each task must produce the FULL "
            f"current state of the file, not a fragment. Task 2 MUST build on Task 1's output "
            f"by including all of Task 1's code plus its own additions.\n"
        )
    elif scope_mode == "MODIFY_ATTRACTION":
        target_name = _os.path.basename(scope_target)
        parts.append(
            f"## SCOPE MANDATE — MODIFY ATTRACTION\n"
            f"All tasks MUST target '{target_name}' "
            f"or shared infrastructure (booth_shared.lua, attraction_constants.lua).\n"
            f"Do NOT assign tasks to any other attraction file — they are read-only context.\n"
        )

    if bridge_excl:
        parts.append(bridge_excl)

    return "\n".join(parts) + "\n\n" if parts else ""


def _build_scope_constraint_text(
    scope_mode: str,
    scope_target: str,
    scope_refs: list[str],
) -> str:
    """Return a compact system-prompt rule for the scope mode."""
    import os as _os
    if scope_mode == "NEW_ATTRACTION":
        ref_names = ", ".join(_os.path.basename(p) for p in scope_refs[:6])
        suffix = ", ..." if len(scope_refs) > 6 else ""
        return (
            f"\n8. SCOPE RULE — NEW ATTRACTION: You are creating a NEW attraction file. "
            f"The new file MUST be placed under the `attractions/` directory "
            f"(e.g. `attractions/skeeball/skeeball.lua`). "
            f"You MUST NOT generate any tasks that modify existing attraction files "
            f"({ref_names}{suffix}). Those files are [REFERENCE ONLY].\n"
        )
    if scope_mode == "MODIFY_ATTRACTION":
        target_name = _os.path.basename(scope_target)
        return (
            f"\n8. SCOPE RULE — MODIFY ATTRACTION: All tasks must target '{target_name}' "
            f"or shared infrastructure (booth_shared.lua, attraction_constants.lua). "
            f"Files annotated [REFERENCE ONLY] in the AST section MUST NOT be task targets.\n"
        )
    return ""


def _run_blueprint_phase(ctx: PipelineContext, blueprint_path, gdd_snippet: str, state_snippet: str) -> PipelineContext:
    """Generate, gate, and persist an architectural blueprint (TOO_BROAD path)."""
    unavailable_text = get_unavailable_domains_text()

    cartridge_invariants = ""
    _env_meta: dict = {}
    # Class-based cartridge path (preferred)
    if hasattr(ctx, '_cartridge_environment_metadata') and ctx._cartridge_environment_metadata:
        _env_meta = ctx._cartridge_environment_metadata
    # Data-contract cartridge path (legacy)
    elif hasattr(ctx, 'mounted_cartridge') and ctx.mounted_cartridge:
        try:
            _env_meta = ctx.mounted_cartridge.get_environment_metadata()
        except Exception as e:
            print(f"  [Lead Producer] Metadata extraction error: {e}")
    if _env_meta:
        invariants = [
            f"- {d}: {cfg.get('architectural_invariant', '')}"
            for d, cfg in _env_meta.items() if cfg.get('architectural_invariant')
        ]
        if invariants:
            cartridge_invariants = "\n## Active Cartridge Architectural Invariants\n" + "\n".join(invariants)

    active_stack = getattr(ctx, 'workspace_fingerprint', "a universal source repository")
    hard_constraints = (
        f"HARD CONSTRAINTS — Do NOT plan for:\n"
        f"{unavailable_text}\n"
        f"{cartridge_invariants}\n\n"
        f"You are orchestrating within the strictly fingerprinted ecosystem: {active_stack}. "
        f"Never reference proprietary engines or unverified third-party libraries not present in local dependencies. "
        f"Never plan tasks that violate the Active Cartridge Architectural Invariants. "
        f"If the user asks for features spanning unavailable subsystems, "
        f"substitute with functional stubs, debug logging, or standard placeholders."
    )
    req_parts_bp = re.split(r'\[block\]', ctx.user_prompt, maxsplit=1, flags=re.IGNORECASE)
    pos_request_bp = req_parts_bp[0].strip()
    neg_block_bp = (
        f"\n\n## EXPLICITLY BLOCKED / EXCLUDED CONCEPTS (CRITICAL)\n"
        f"You MUST NOT generate tasks to set up or implement the following:\n{req_parts_bp[1].strip()}"
        if len(req_parts_bp) > 1 else ""
    )

    _ast_summary = ""
    _ast_topology = None
    try:
        from workspace_indexer import WORKSPACE_INDEXER
        _ast_topology = WORKSPACE_INDEXER.scan_project()
        _ast_summary = _ast_topology.format_ast_summary()
        print(f"  [Lead Producer] AST index: {len(_ast_topology.file_index)} files indexed "
              f"({sum(1 for e in _ast_topology.file_index.values() if e.is_large)} large).")
    except Exception as _e:
        print(f"  [Lead Producer] AST index unavailable: {_e}")

    # Read scope classification computed in run_fetches() (shared with director path).
    _scope_mode   = getattr(ctx, '_scope_mode',   'GENERAL')
    _scope_target = getattr(ctx, '_scope_target',  '')
    _scope_refs   = getattr(ctx, '_scope_refs',    [])
    _bridge_already_exists = getattr(ctx, '_bridge_exclusion_text', '')

    _scope_ast_section = _build_scope_annotated_ast(
        _scope_mode, _scope_target, _scope_refs, _ast_topology
    )

    _ctx_pack = build_blueprint_context_pack(
        gdd_context=ctx.gdd_context or "",
        project_state=ctx.project_state or "",
        structure=ctx.structure or "",
        project_root=ctx.project_root,
        ast_summary=_scope_ast_section or _ast_summary,
    )

    # Pull bridge-contract mandate from the class-based cartridge if available.
    _bridge_contract_rule = ""
    if getattr(ctx, '_cartridge_get_project_context', None):
        _bridge_contract_rule = (
            "\n\nENGINE BRIDGE CONTRACT — MANDATORY:\n"
            "This project uses a strict Lua-first attraction architecture. "
            "ALL new gameplay features MUST be implemented as Lua scripts in "
            "the attractions/ directory using the engine bridge API. "
            "You MUST NOT plan tasks that modify Engine.cpp, Engine.h, "
            "PhysicsManager.cpp, DebugRenderer.cpp, or any other core engine "
            "source file UNLESS the required physics primitive is provably "
            "absent from the sol2 bridge contract documented in "
            "docs/engine_lua_bridge_contract.md. "
            "If a required capability already exists in the bridge, route it "
            "through Lua only. Never propose engine modifications as a shortcut."
        )

    # Scope constraint sentence injected into system + user prompt.
    _scope_constraint = _build_scope_constraint_text(_scope_mode, _scope_target, _scope_refs)

    # For NEW_ATTRACTION scope, the canonical Lua path is always
    # attractions/<slug>/<slug>.lua — derive it automatically so the user
    # never has to correct the blueprint for a file-layout violation.
    _user_file_constraint_canonical: str = ""
    if _scope_mode == "NEW_ATTRACTION" and _scope_target:
        _attr_slug = re.sub(r'[^\w]+', '_', _scope_target.strip().lower()).strip('_')
        if _attr_slug:
            _user_file_constraint_canonical = f"attractions/{_attr_slug}/{_attr_slug}.lua"
            print(f"  [Blueprint] 🔒 Auto-enforced file constraint: {_user_file_constraint_canonical}")

    # ── Pull the real approved API list from the cartridge for blueprint grounding ──
    _bp_api_names = ""
    _bp_lifecycle_hooks: list = []
    _build_bridge_bp = getattr(ctx, '_cartridge_get_bridge_contract', None)
    if callable(_build_bridge_bp):
        try:
            _bc_bp = _build_bridge_bp()
            _lifecycle_raw = _bc_bp.get("script_lifecycle") or []
            # script_lifecycle is a list of canonical hook name strings.
            _bp_lifecycle_hooks = [
                h.split("(")[0].split("_dt")[0]  # normalise OnStep_dt -> OnStep
                for h in _lifecycle_raw
            ]
            _physics_fns = list((_bc_bp.get("midwayphysics_spawn_api") or {}).keys())
            _pool_fns    = list((_bc_bp.get("object_pools") or {}).keys())
            _econ_fns    = list((_bc_bp.get("economy_api") or {}).keys())
            _all_fns = _physics_fns + _pool_fns + _econ_fns
            if _all_fns:
                _lifecycle_line = (
                    "LIFECYCLE ENTRY POINTS (use ONLY these exact names, no wrappers):\n"
                    + ", ".join(_bp_lifecycle_hooks or ["OnLoadStatic", "OnLoad", "OnUnload", "OnStep"])
                    + "\n"
                )
                _bp_api_names = (
                    f"\n\nAPPROVED BRIDGE API — use ONLY these exact function names:\n"
                    f"{_lifecycle_line}"
                    f"Physics (static geometry only): {', '.join(f for f in _physics_fns if 'Static' in f)}\n"
                    f"Physics (dynamic/moving bodies): {', '.join(f for f in _physics_fns if 'Dynamic' in f)}\n"
                    f"Physics (other): {', '.join(f for f in _physics_fns if 'Static' not in f and 'Dynamic' not in f)}\n"
                    f"Pools:   {', '.join(_pool_fns)}\n"
                    f"Economy: {', '.join(_econ_fns)}\n"
                    "NEVER invent names not in the lists above.\n"
                    "Static variants (SpawnStaticBox etc.) are for immovable cabinet geometry ONLY — "
                    "NEVER use them for balls, projectiles, or any body that moves.\n"
                    "Do NOT use placeholder namespaces like [MODULE][PhysicsModuleName] "
                    "or [Engine][EngineName] — the real namespaces are MidwayPhysics.* "
                    "and Engine.*\n"
                )
        except Exception:
            pass

    _blueprint_system = (
        "You are a Lead Producer with RESTRICTED access to the project codebase.\n\n"
        "Your role: analyse the project context already provided below, "
        "then produce a precise step-by-step markdown blueprint.\n\n"
        "BLUEPRINT RULES:\n"
        "1. Ground every task in REAL files visible in the AST index — "
        "NEVER invent file paths. If a path is not in the AST index, omit it.\n"
        "2. Use [FILE_READ:<path>, lines N-M] ONLY for files explicitly listed in "
        "the AST index summary below. One read per file — do NOT re-read the same path.\n"
        "3. Do NOT emit [FILE_READ:] or [FILE_LIST:] in your FINAL checklist output. "
        "These signals are for context gathering only and must not appear as tasks.\n"
        "4. Never reference IDE features, GUI tools, images, external URLs, or files "
        "from foreign subsystems (SDL, Box2D, OpenGL setup) unless they are in the AST index.\n"
        "5. Output ONLY the checklist — no prose, no commentary, no file-tool signals.\n"
        "6. Format: '- [ ] Task N: <verb> <specific thing> - <filepath>' on its own line. "
        "The verb MUST be an action word (Implement, Define, Add, Register, Create, Integrate). "
        "NEVER use a bare API function signature as the task name "
        "(e.g. 'SpawnDynamicSphere(...)' is FORBIDDEN as a task title).\n"
        "7. Keep the checklist SHORT and SCOPED: 4–10 tasks minimum, each targeting a single "
        "concrete gameplay concern (not a single API call).\n"
        "8. ALL gameplay tasks MUST be implemented in Lua attraction scripts. "
        "Do NOT plan any tasks that modify C++ engine files (*.cpp, *.h) — "
        "the bridge contract already exposes all required primitives.\n"
        "9. For NEW_ATTRACTION requests, the FIRST task MUST define ALL four attraction lifecycle "
        "entry points: OnLoadStatic, OnLoad, OnUnload, AND the OnStep callback. "
        "Use ONLY the real hook names: "
        + (", ".join(_bp_lifecycle_hooks) if _bp_lifecycle_hooks else "OnLoadStatic, OnLoad, OnUnload, OnStep")
        + ". "
        "Do NOT invent wrapper functions like OnLoadAttraction() or any other name not in the "
        "approved lifecycle list above. OnStep MUST be registered via "
        "MidwayPhysics.OnStep(function(dt) ... end) inside OnLoad() — "
        "it is NOT an optional extra and MUST appear in the first task.\n"
        "10. Every task MUST describe WHAT gameplay concern it addresses, not just WHICH API "
        "to call. Example — GOOD: 'Implement ball launch on player input using "
        "SpawnDynamicSphere and ApplyImpulse'. BAD: 'SpawnDynamicSphere(lx,ly,lz,radius)'.\n"
        "11. Spawn API semantics — use the CORRECT variant for each body type:\n"
        "    - SpawnStaticBox / SpawnStaticBoxR → ONLY for permanent immovable geometry "
        "(walls, ramps, cabinet surfaces). NEVER use for gameplay objects that move.\n"
        "    - SpawnDynamicSphere / SpawnDynamicBox / SpawnDynamicCapsule → for any body "
        "that moves during gameplay (balls, projectiles, tokens).\n"
        "    - Mixing these up (e.g. using SpawnStaticBox for a ball) is a hard error.\n"
        "12. Gameplay counters, timers, and state (ball count, score, round index) are plain "
        "Lua variables or tables. Do NOT describe them as engine primitives, C++ features, "
        "or inventory systems. Example — CORRECT: 'Track remaining balls with a Lua counter'. "
        "WRONG: 'Create an inventory system for balls'.\n"
        + (
            f"\n## HARD FILE CONSTRAINT — MANDATORY:\n"
            f"This is a NEW_ATTRACTION request. ALL Lua code MUST be placed in exactly ONE file:\n"
            f"  {_user_file_constraint_canonical}\n"
            f"Do NOT reference, create, or imply any other .lua file under any circumstance.\n"
            f"Every single task line MUST end with ' - {_user_file_constraint_canonical}'\n"
            if _user_file_constraint_canonical else ""
        )
        + _scope_constraint
        + _bridge_contract_rule
        + _bp_api_names
        + "\n"
        + AGENT_FILE_TOOLS_PROMPT
    )

    blueprint_prompt = (

        f"{pos_request_bp}.\n{neg_block_bp}\n\n"
        f"{hard_constraints}\n\n"
        f"{_ctx_pack}\n\n"
        f"{_bridge_already_exists}\n\n"
        f"## Unavailable Domains\n"
        f"{unavailable_text}\n\n"
        f"CRITICAL DIRECTIVES:\n"
        f"1. You MUST strictly honor any explicit exclusions, omissions, or negative constraints specified in the blocked concepts.\n"
        f"2. Do NOT generate tasks to set up attractions, modules, or features that the user explicitly stated are excluded or blocked.\n"
        f"3. Base your step-by-step tasks strictly on the targeted positive feature requested.\n"
        f"4. Files annotated as [REFERENCE ONLY] in the AST section MUST NOT appear as task targets.\n"
        f"5. Use FILE_READ / FILE_LIST to verify any file path before referencing it in a task.\n\n"
        f"Format as a checklist:\n"
        f"'- [ ] Task 1: ...'"
    )

    while True:
        _MAX_TOOL_ROUNDS = 3
        _tool_extra = ""
        _seen_paths: set = set()  # deduplicate file reads across rounds
        for _tool_round in range(_MAX_TOOL_ROUNDS):
            _round_prompt = blueprint_prompt + _tool_extra
            # Cap the prompt before sending so the pre-summariser never fires
            # on the blueprint call.  When tool results push us over budget,
            # collapse only the tool-results chunk (the tail we just built)
            # rather than letting the summariser eat the actual task list.
            # Overflow is preserved in OffloadStore so the blueprint agent can
            # PAGE_IN any file content it still needs.
            from ollama_client import resolve_ctx_size as _bp_rcz
            _BP_MODEL_CTX = _bp_rcz(REASONING_MODEL)
            _BP_SYSTEM_LEN = len(_blueprint_system)
            _BP_HARD_CAP = max(800, int(_BP_MODEL_CTX * 1.5) - _BP_SYSTEM_LEN - 200)
            if len(_round_prompt) > _BP_HARD_CAP:
                _base_len = len(blueprint_prompt)
                _tool_cap = max(400, _BP_HARD_CAP - _base_len - 200)
                _tool_overflow = _tool_extra[_tool_cap:] if len(_tool_extra) > _tool_cap else ""
                _tool_extra_collapsed = TokenBudget._block_aware_collapse(_tool_extra, _tool_cap)
                if _tool_overflow.strip():
                    try:
                        from offload_store import get_offload_store as _bp_os
                        _bp_store = _bp_os()
                        _bp_oid = f"blueprint_tool_overflow_round{_tool_round}"
                        _bp_store.store_block(
                            block_id=_bp_oid,
                            header=f"Blueprint tool results overflow — round {_tool_round + 1} ({len(_tool_overflow)} chars)",
                            body_lines=[_tool_overflow],
                        )
                        _tool_extra_collapsed += (
                            f"\n[📄 Additional tool results offloaded ({len(_tool_overflow)} chars). "
                            f"Use `<invoke_kernel><action>PAGE_IN</action>"
                            f"<target>{_bp_oid}</target></invoke_kernel>` to retrieve.]\n"
                        )
                    except Exception:
                        pass
                _round_prompt = blueprint_prompt + _tool_extra_collapsed
                print(f"  [Blueprint Context] Tool results collapsed to {_tool_cap} chars to stay under VRAM budget.")

            blueprint = call_ollama(
                _blueprint_system, _round_prompt,
                f"Blueprint Generation (round {_tool_round + 1})",
                REASONING_MODEL,
                skip_pre_summarizer=True,
            )

            from ollama_client import is_fatal_ollama_error as _is_fatal_bp
            if _is_fatal_bp(blueprint):
                print(f"  [Blueprint] ⛔ Ollama error during Blueprint Generation — aborting pipeline.")
                ctx.final_output = f"Pipeline aborted: Ollama unreachable during Blueprint Generation. {blueprint.strip()}"
                return ctx

            _file_read_re = re.compile(r'\[FILE_READ:([^\]]+)\]', re.IGNORECASE)
            _file_list_re = re.compile(r'\[FILE_LIST:([^\]]+)\]', re.IGNORECASE)
            _tool_results: list = []
            for _m in _file_read_re.finditer(blueprint):
                _path_key = _m.group(1).strip()[:120]
                if _path_key in _seen_paths:
                    continue
                _seen_paths.add(_path_key)
                _result = handle_file_read(_m.group(1), project_root=ctx.project_root)
                # Discard error results — feeding them back causes hallucination spirals
                if "**Error:**" in _result or "File not found" in _result:
                    print(f"  [Blueprint FileTool] FILE_READ (skipped — not found): {_m.group(1)[:80]}")
                    continue
                _tool_results.append(_result)
                print(f"  [Blueprint FileTool] FILE_READ: {_m.group(1)[:80]}")
            for _m in _file_list_re.finditer(blueprint):
                _path_key = "LIST:" + _m.group(1).strip()[:120]
                if _path_key in _seen_paths:
                    continue
                _seen_paths.add(_path_key)
                _result = handle_file_list(_m.group(1), project_root=ctx.project_root)
                if "**Error:**" in _result or "not found" in _result.lower():
                    print(f"  [Blueprint FileTool] FILE_LIST (skipped — not found): {_m.group(1)[:80]}")
                    continue
                _tool_results.append(_result)
                print(f"  [Blueprint FileTool] FILE_LIST: {_m.group(1)[:80]}")

            if not _tool_results:
                break

            # Replace (not append) tool context each round to prevent context snowballing
            _tool_extra = (
                "\n\n## File Tool Results (injected by orchestrator — these are the ONLY verified paths)\n"
                + "\n".join(_tool_results)
                + "\n\n[SYSTEM KERNEL: Use ONLY the file paths confirmed above. "
                "Do NOT emit any [FILE_READ:] or [FILE_LIST:] signals in your final checklist output. "
                "Produce the final checklist now.]"
            )
        else:
            print(f"  [Lead Producer] ⚠ Tool resolution reached {_MAX_TOOL_ROUNDS} rounds — using last output.")

        # Strip any unresolved FILE_READ / FILE_LIST signals left in the blueprint.
        # These indicate the model is still exploring rather than producing a final plan.
        _unresolved_re = re.compile(
            r'^[-*]?\s*(?:\[[ x]\]\s*)?.*\[FILE_(?:READ|LIST):[^\]]+\].*$',
            re.IGNORECASE | re.MULTILINE
        )
        _stripped = _unresolved_re.sub('', blueprint).strip()
        # Collapse multiple blank lines left by stripping
        _stripped = re.sub(r'\n{3,}', '\n\n', _stripped)
        if _stripped != blueprint.strip():
            _removed = len(re.findall(_unresolved_re, blueprint))
            print(f"  [Blueprint FileTool] Stripped {_removed} unresolved tool-signal lines from blueprint.")
            blueprint = _stripped

        # ── Blueprint Structural Validator ─────────────────────────────────
        # Auto-reject structurally deficient blueprints before showing the
        # gate, preventing the user from having to manually catch:
        #   - Too few tasks (model bailed out early)
        #   - Tasks that are bare API signatures, not action descriptions
        #   - Missing lifecycle task for NEW_ATTRACTION scope
        #   - Phantom API names in task descriptions
        _bp_issues: list[str] = []

        # Count checkbox tasks
        _task_lines = re.findall(
            r'^[-*]?\s*\[ \]\s*(?:Task\s*\d+[:\.]?\s*)?(.+)',
            blueprint, re.MULTILINE
        )
        _n_tasks = len(_task_lines)
        if _n_tasks < 3:
            _bp_issues.append(
                f"Only {_n_tasks} task(s) generated — minimum 3 required for a useful blueprint. "
                f"Expand the plan to cover all gameplay concerns."
            )

        # Detect bare API signatures used as task names (e.g. "SpawnDynamicSphere(...)")
        _api_sig_re = re.compile(
            r'^[-*]?\s*\[ \]\s*(?:Task\s*\d+[:\.]?\s*)?(\w+\s*\([^)]*\))',
            re.MULTILINE
        )
        _bare_sigs = _api_sig_re.findall(blueprint)
        if _bare_sigs:
            _bp_issues.append(
                f"Tasks are named after bare API signatures instead of gameplay actions: "
                + ", ".join(_bare_sigs[:4])
                + ". Rewrite each as an action description (e.g. 'Implement ball launch')."
            )

        # For NEW_ATTRACTION, require a lifecycle task
        if _scope_mode == "NEW_ATTRACTION":
            _lifecycle_keywords = (
                "onloadattraction", "onloadstatic", "onload", "lifecycle",
                "entry point", "register", "onstep", "onunload",
            )
            _bp_lower = blueprint.lower()
            if not any(kw in _bp_lower for kw in _lifecycle_keywords):
                _bp_issues.append(
                    "No lifecycle task found. The first task MUST define OnLoadAttraction() "
                    "(or OnLoadStatic/OnLoad/OnUnload) entry points and register MidwayPhysics.OnStep."
                )

        # Detect phantom API names in task lines.
        # Only list names that do NOT appear in the bridge contract.
        # Verified against engine_lua_bridge_contract.md — do NOT add:
        #   SetLinearVelocity, SetGravityFactor, SpawnDynamicBox, IsActive
        #   (all are real approved APIs).
        _phantom_apis = {
            "getlinearvelocity",
            "spawnstaticball", "spawndynamicball", "spawndynamicbody",
            "spawnstaticbody", "checkcollision", "destroyentity",
            "releasehandle", "resetbody",
            "spawnstaticplane",
        }
        _bp_lower = blueprint.lower()
        _found_phantoms = [p for p in _phantom_apis if p in _bp_lower]
        if _found_phantoms:
            _bp_issues.append(
                "Phantom API name(s) detected in blueprint: "
                + ", ".join(_found_phantoms)
                + ". Replace with approved bridge API names from the contract above."
            )

        # ── File-constraint enforcement ─────────────────────────────────────
        # If the user previously specified a single target file, reject any
        # blueprint that references a different .lua file.
        if _user_file_constraint_canonical:
            _expected_lower = _user_file_constraint_canonical.lower()
            _other_lua = re.findall(
                r'attractions/[\w/]+\.lua',
                blueprint, re.IGNORECASE
            )
            _violating = [
                f for f in _other_lua
                if f.strip().lower() != _expected_lower
            ]
            if _violating:
                _unique_v = list(dict.fromkeys(_violating))[:6]
                _bp_issues.append(
                    f"Blueprint references Lua file(s) other than the required "
                    f"'{_user_file_constraint_canonical}': "
                    + ", ".join(_unique_v)
                    + ". ALL tasks MUST target only the single required file."
                )

        if _bp_issues:
            _issues_text = "\n".join(f"  - {i}" for i in _bp_issues)
            print(f"  [Blueprint Validator] ⚠ Auto-rejecting blueprint — {len(_bp_issues)} structural issue(s):")
            print(_issues_text)
            # Build a correction prompt and loop back for an auto-retry
            _scope_mandate_v = _build_scope_constraint_text(_scope_mode, _scope_target, _scope_refs)
            # Re-inject the user's hard file constraint into the auto-correction prompt
            # so the model cannot drift back to multi-file layouts on auto-retry.
            _fc_rule_v = (
                f"\n## HARD FILE CONSTRAINT (enforced — violations will be auto-rejected):\n"
                f"ALL Lua code MUST be placed in ONE file ONLY: {_user_file_constraint_canonical}\n"
                f"Do NOT reference, create, or imply any other .lua file.\n"
                f"Every task line MUST end with ' - {_user_file_constraint_canonical}'\n"
            ) if _user_file_constraint_canonical else ""
            blueprint_prompt = (
                f"You are revising an architectural blueprint checklist.\n\n"
                f"## Original Feature Request\n{pos_request_bp}\n"
                + (f"{neg_block_bp}\n" if neg_block_bp else "")
                + _fc_rule_v
                + (_scope_mandate_v + "\n" if _scope_mandate_v else "")
                + (_bridge_already_exists + "\n" if _bridge_already_exists else "")
                + f"\n## PREVIOUS DRAFT (contains structural violations — fix ALL issues below):\n"
                f"```\n{blueprint}\n```\n\n"
                f"## STRUCTURAL VIOLATIONS TO FIX (MANDATORY):\n"
                + _issues_text
                + f"\n\nRULES:\n"
                f"1. Fix EVERY violation listed above.\n"
                f"2. Keep all tasks from the previous draft that are still valid.\n"
                f"3. Output ONLY the revised checklist — no prose, no commentary.\n"
                f"4. Format: '- [ ] Task N: <verb> <gameplay concern> - <filepath>' on its own line.\n"
                f"5. Minimum 4 tasks. First task MUST define the attraction lifecycle for NEW_ATTRACTION scope.\n"
            )
            _tool_extra = ""
            _seen_paths = set()
            continue  # trigger another generation round

        blueprint_path.parent.mkdir(exist_ok=True)
        atomic_write_text(blueprint_path, blueprint)
        print(f"  [Lead Producer] Saved to docs/project_blueprint.md.")

        print(f"\n{'='*50}")
        print(f"  BLUEPRINT GATE — Review the architectural blueprint")
        print(f"  Location: {blueprint_path}")
        print(f"{'='*50}")
        print()
        print("  ┌─ WHAT IS THE BLUEPRINT GATE? ─────────────────────────────────────┐")
        print("  │ Before writing any code, the pipeline drew up a plan — the        │")
        print("  │ blueprint — describing every task it intends to carry out and      │")
        print("  │ which systems it will touch.                                       │")
        print("  │                                                                   │")
        print("  │ This is your chance to read that plan and catch mistakes BEFORE   │")
        print("  │ any code is generated. Open the file shown above, read it, then   │")
        print("  │ come back here.                                                   │")
        print("  │                                                                   │")
        print("  │  Y  — plan looks good, start generating code.                    │")
        print("  │  n  — something is wrong; you will be asked what to fix.         │")
        print("  └───────────────────────────────────────────────────────────────────┘")
        trigger_chime()
        approval = input("  Do you approve this architectural blueprint? [Y/n]: ").strip().lower()

        if approval in ("n", "no"):
            trigger_chime()
            print(f"\n  [Blueprint Gate triage] Blueprint rejected.")
            issues = input("  What are the specific problems or incorrect inclusions? ").strip()
            suggestions = input(
                "  Do you have specific suggestions to address this? "
                "(Type your suggestions, 'retry' to regenerate from scratch, or 'abandon' to abort): "
            ).strip()

            sug_lower = suggestions.lower()
            if sug_lower == "abandon":
                print("  [Blueprint Gate] Abandoning blueprint generation. Aborting pipeline.")
                ctx.final_output = "Pipeline abandoned by user at Blueprint Gate."
                return ctx
            elif sug_lower == "retry":
                print("  [Blueprint Gate] Retrying blueprint generation from scratch based on original constraints...")
                continue
            else:
                print("  [Blueprint Gate] Regenerating blueprint incorporating your feedback...")
                # Extract an explicit file-path constraint from the user's suggestion.
                # If the user named a single target file (e.g. 'attractions/skeeball/skeeball.lua')
                # treat it as a hard mandate, not a hint.
                _file_constraint_re = re.compile(
                    r'(?:^|\s)(attractions/[\w/]+\.lua)',
                    re.IGNORECASE
                )
                _fc_matches = _file_constraint_re.findall(suggestions + " " + issues)
                # Normalise and deduplicate; take only the first match as the canonical target.
                _user_file_constraint: str = _fc_matches[0].strip().lower() if _fc_matches else ""
                if _user_file_constraint:
                    _user_file_constraint_canonical = _fc_matches[0].strip()
                    print(f"  [Blueprint Gate] 🔒 Hard file constraint extracted: {_user_file_constraint_canonical}")
                else:
                    _user_file_constraint_canonical = ""

                _file_constraint_rule = (
                    f"\n## HARD FILE CONSTRAINT (enforced — violations will be auto-rejected):\n"
                    f"ALL Lua code MUST be placed in ONE file ONLY: {_user_file_constraint_canonical}\n"
                    f"Do NOT reference, create, or imply any other .lua file.\n"
                    f"Every task line MUST end with ' - {_user_file_constraint_canonical}'\n"
                ) if _user_file_constraint_canonical else ""

                _scope_mandate = _build_scope_constraint_text(_scope_mode, _scope_target, _scope_refs)
                _bridge_excl_short = _bridge_already_exists or _bridge_contract_rule
                blueprint_prompt = (
                    f"You are revising an architectural blueprint checklist.\n\n"
                    f"## Original Feature Request\n{pos_request_bp}\n"
                    + (f"{neg_block_bp}\n" if neg_block_bp else "")
                    + (_file_constraint_rule)
                    + (_scope_mandate + "\n" if _scope_mandate else "")
                    + (_bridge_excl_short + "\n" if _bridge_excl_short else "")
                    + (f"\n## Project GDD Context (use ONLY this — do not invent content from outside it)\n"
                       f"{_ctx_pack}\n\n" if _ctx_pack else "")
                    + f"\n## PREVIOUS DRAFT (revise this — do NOT discard valid tasks):\n"
                    f"```\n{blueprint}\n```\n\n"
                    f"## USER FEEDBACK ON PREVIOUS DRAFT:\n"
                    f"Problems: {issues}\n"
                    f"Instructions: {suggestions}\n\n"
                    f"RULES:\n"
                    f"1. Fix every problem listed above.\n"
                    f"2. Keep all tasks from the previous draft that are still valid.\n"
                    f"3. Remove or replace any task that references content NOT found in the GDD Context above.\n"
                    f"4. Output ONLY the revised checklist — no prose, no commentary.\n"
                    f"5. Format: '- [ ] Task N: <verb> <specific thing>' on its own line.\n"
                )
                # Also reset tool state so the revised prompt starts clean
                _tool_extra = ""
                _seen_paths = set()
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
        original_request = ctx.user_prompt
        ctx.user_prompt = (
            f"<execution_environment>\n"
            f"  <system_directives>\n"
            f"    You are operating within an isolated expert domain. Focus compilation strictly on the target subtask scope below.\n"
            f"  </system_directives>\n"
            f"  <macro_invariants>\n"
            f"    {original_request.strip()}\n"
            f"  </macro_invariants>\n"
            f"  <target_subtask_scope>\n"
            f"    {task_text.strip()}\n"
            f"  </target_subtask_scope>\n"
            f"</execution_environment>\n\n"
            f"INSTRUCTION: Implement ONLY the functionality defined inside <target_subtask_scope> while adhering strictly to <macro_invariants>."
        )
        new_content = content.replace(raw_line, raw_line.replace("[ ]", "[x]", 1), 1)
        atomic_write_text(blueprint_path, new_content)
        print(f"  [Lead Producer] Auto-feeding first task while preserving block constraints: {task_text}")
        print(f"  [Lead Producer] Continuing to Phase 3...")
    else:
        print("  [Lead Producer] Blueprint generated but no tasks found — continuing with original prompt.")

    return ctx


def _run_director_phase(ctx: PipelineContext) -> PipelineContext:
    """Phase 3: Director task decomposition + interface manifest derivation."""
    _ts = datetime.now().strftime('%H:%M:%S')
    print(f"\n{'='*70}")
    print(f"  [{_ts}] Phase 3: Director — Task Decomposition")
    print(f"{'='*70}")
    ctx.output_parts.append("\n## Phase 3: Director — Task Decomposition\n")

    director_prompt = build_director_prompt()

    # ── Inject cartridge-level Director directives (Lua-first mandate, etc.) ──
    _director_extra_fn = getattr(ctx, '_cartridge_get_director_extra', None)
    if callable(_director_extra_fn):
        try:
            _director_extra = _director_extra_fn()
            if _director_extra:
                director_prompt = director_prompt + "\n\n" + _director_extra
        except Exception:
            pass

    gdd_snippet = TokenBudget._block_aware_collapse(ctx.gdd_context, 3500) if ctx.gdd_context else "(no GDD context)"
    state_snippet = TokenBudget._block_aware_collapse(ctx.project_state, 2000) if ctx.project_state else "(no project state)"

    # ── Inject live bridge contract API surface so the Director cannot invent
    # phantom tasks around non-existent functions. Pull from cartridge if mounted,
    # otherwise leave empty so the kernel stays project-agnostic.
    _bridge_api_snippet = ""
    _build_bridge = getattr(ctx, '_cartridge_build_bridge_contract', None)
    if callable(_build_bridge):
        try:
            _bc = _build_bridge()  # calls cartridge.get_bridge_contract()
            _api_lines = list((_bc.get("midwayphysics_spawn_api") or {}).keys())
            _pool_lines = list((_bc.get("object_pools") or {}).keys())
            _economy_lines = list((_bc.get("economy_api") or {}).keys())
            if _api_lines or _pool_lines:
                _bridge_api_snippet = (
                    "\n\n## Active Bridge Contract — COMPLETE Approved API List\n"
                    "These are ALL currently-exposed functions. This list is exhaustive.\n"
                    "Physics: " + ", ".join(_api_lines) + "\n"
                    "Object pools: " + ", ".join(_pool_lines) + "\n"
                    "Economy: " + ", ".join(_economy_lines) + "\n"
                    "CRITICAL RULES:\n"
                    "1. Lua tasks MUST use ONLY the exact function names listed above.\n"
                    "2. Do NOT invent GetLinearVelocity, SetLinearVelocity, CheckCollision, "
                    "GetEntityHandle, DestroyEntity, ReleaseHandle, SpawnDynamicBody, "
                    "GetBody, SpawnBox, SpawnCircle, or any other name not in this list.\n"
                    "3. If a needed primitive appears absent from this list, you MUST verify "
                    "it is truly missing (not just differently named) before planning C++ work. "
                    "A phantom naming gap is NOT a valid reason to add C++ tasks. "
                    "Prefer a pure-Lua solution unless the primitive is provably absent from "
                    "MidwayPhysics.h. Do NOT re-register or re-expose any function already on this list."
                )
        except Exception:
            pass

    base_director_input = (
        f"{director_prompt}\n\n"
        f"## Relevant GDD Context\n{gdd_snippet}\n\n"
        f"## Current Project State\n{state_snippet}"
        f"{_bridge_api_snippet}\n\n"
        + _build_director_scope_mandate(ctx)
        + f"---\nUSER REQUEST:\n{ctx.user_prompt}"
    )

    task_regex = r"### Task ([a-zA-Z0-9]+):\s*\[([^\]]+)\]\s*[-—–]\s*(.+?)(?:\s*\(DependsOn:\s*(.+?)\))?\s*$"
    ctx.tasks_list = []

    # Derive the canonical Lua path for NEW_ATTRACTION scope so the per-task
    # target_file extraction below can fall back to it when the director omits
    # an explicit path in the task title.
    _user_file_constraint_canonical: str = ""
    _d_scope_mode  = getattr(ctx, '_scope_mode', 'GENERAL')
    _d_scope_target = getattr(ctx, '_scope_target', '')
    if _d_scope_mode == "NEW_ATTRACTION" and _d_scope_target:
        _d_slug = re.sub(r'[^\w]+', '_', _d_scope_target.strip().lower()).strip('_')
        if _d_slug:
            _user_file_constraint_canonical = f"attractions/{_d_slug}/{_d_slug}.lua"

    max_parsing_attempts = 3
    current_director_input = base_director_input

    for attempt in range(1, max_parsing_attempts + 1):
        if attempt > 1:
            print(f"  [Director] Task parsing failed (missing brackets/alignment). Initiating autonomic retry {attempt}/{max_parsing_attempts}...")

        ctx.director_output = call_ollama(
            DIRECTOR_SYSTEM, current_director_input, f"Director (Attempt {attempt})", DIRECTOR_MODEL
        )

        from ollama_client import is_fatal_ollama_error as _is_fatal_dir
        if _is_fatal_dir(ctx.director_output):
            print(f"  [Director] ⛔ Ollama error during Director — aborting pipeline.")
            ctx.final_output = f"Pipeline aborted: Ollama unreachable during Director. {ctx.director_output.strip()}"
            return ctx

        # ── Parse tasks, detecting per-task [MATH_HEAVY] annotations ────────
        # Accepts both [MATH_HEAVY] and [MATH HEAVY] (spaced form).
        _math_heavy_re = re.compile(r"\[\s*\*?\*?\s*MATH[_ ]?HEAVY\s*\*?\*?\s*\]", re.IGNORECASE)
        ctx.tasks_list = []
        lines = ctx.director_output.splitlines()
        for line_idx, line in enumerate(lines):
            match = re.match(task_regex, line.strip())
            if not match:
                continue
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
            # Check if [MATH_HEAVY] appears on the task line itself, or within
            # the next 3 non-empty lines (covers blank-line-separated annotations).
            is_math_heavy = bool(_math_heavy_re.search(line))
            if not is_math_heavy:
                _lookahead_remaining = 3
                _scan_idx = line_idx + 1
                while _lookahead_remaining > 0 and _scan_idx < len(lines):
                    _scan_line = lines[_scan_idx].strip()
                    if _scan_line:
                        if _math_heavy_re.search(_scan_line):
                            is_math_heavy = True
                        _lookahead_remaining -= 1
                    _scan_idx += 1
                    if is_math_heavy:
                        break
            # ── Extract target_file from task title ──────────────────────────
            # Director often emits paths in titles:
            #   "Implement ball count - attractions/skeeball.lua"
            #   "Register OnLoad() function … - attractions/skeebal/skeeball.lua"
            # Capture any "word/word.ext" segment as the target file.
            _FILE_IN_TITLE_RE = re.compile(
                r'(?:^|\s|-)([a-zA-Z][\w/]*\.(?:lua|cpp|h|hpp|c))\b'
            )
            _title_file_match = _FILE_IN_TITLE_RE.search(title)
            _task_target_file: str | None = None
            if _title_file_match:
                _task_target_file = _title_file_match.group(1).strip()
            elif _user_file_constraint_canonical:
                # Fallback: use the canonical slug path for NEW_ATTRACTION scope
                _task_target_file = _user_file_constraint_canonical
            ctx.tasks_list.append({
                "id": task_id,
                "domain": domain,
                "title": title,
                "depends_on": depends_on,
                "math_heavy": is_math_heavy,
                "target_file": _task_target_file,
            })

        # ── Orphan-tag pass: a bare [MATH_HEAVY] placed outside any task block
        # (e.g. at the end of the director output) should flag ALL tasks, because
        # the director intends the whole solution to use pro mode.
        _director_has_global_math_heavy = bool(_math_heavy_re.search(ctx.director_output))
        if _director_has_global_math_heavy and ctx.tasks_list:
            _any_flagged = any(t.get("math_heavy") for t in ctx.tasks_list)
            if not _any_flagged:
                # No per-task tag was found, but the tag exists in the output → flag all tasks
                for _t in ctx.tasks_list:
                    _t["math_heavy"] = True

        # ── Pro Mode: per-task MATH_HEAVY gate ──────────────────────────────
        _math_heavy_ids = [t["id"] for t in ctx.tasks_list if t.get("math_heavy")]
        if _math_heavy_ids and not ctx.pro_mode_always:
            print(f"\n{'='*50}")
            print(f"  MATH_HEAVY DETECTED — Tasks flagged: {', '.join(_math_heavy_ids)}")
            print(f"{'='*50}")
            from _pipeline_helpers import trigger_chime as _chime
            _chime()
            user_input = input(
                f"  Task(s) {', '.join(_math_heavy_ids)} require complex 3D math / physics.\n"
                "  Enable Pro Mode (TDD guardrails, multi-draft consensus) for these tasks?\n"
                "  [y]es / [n]o / [a]lways (auto-enable for all math-heavy tasks this session): "
            ).strip().lower()
            if user_input in ("a", "always"):
                ctx.pro_mode_always = True
                ctx.math_heavy_tasks.update(_math_heavy_ids)
                print(f"  [Pro Mode] ALWAYS — will auto-enable for all math-heavy tasks this session.")
            elif user_input in ("y", "yes"):
                ctx.math_heavy_tasks.update(_math_heavy_ids)
                print(f"  [Pro Mode] ENABLED for task(s): {', '.join(_math_heavy_ids)}.")
            else:
                print(f"  [Pro Mode] Declined — task(s) {', '.join(_math_heavy_ids)} will run in standard mode.")
        elif _math_heavy_ids and ctx.pro_mode_always:
            ctx.math_heavy_tasks.update(_math_heavy_ids)
            print(f"  [Pro Mode] Auto-enabled (always) for task(s): {', '.join(_math_heavy_ids)}.")

        # Keep legacy ctx.pro_mode in sync so any existing callers remain safe
        ctx.pro_mode = bool(ctx.math_heavy_tasks)

        if ctx.tasks_list:
            # ── A3: Hard-drop ALL C++ tasks for NEW_ATTRACTION scope ──────────
            # New attractions are pure Lua. Any C++ task means the director
            # hallucinated bridge work that already exists. Drop unconditionally.
            _scope_is_new = getattr(ctx, '_scope_mode', 'GENERAL') == "NEW_ATTRACTION"
            if _scope_is_new:
                _before = len(ctx.tasks_list)
                ctx.tasks_list = [
                    _t for _t in ctx.tasks_list
                    if _t.get("domain", "").upper() not in ("C++", "CPP", "PHYS")
                ]
                _dropped = _before - len(ctx.tasks_list)
                if _dropped:
                    print(f"  [Director Guard] ⚠ Dropped {_dropped} C++ task(s) — "
                          f"NEW_ATTRACTION scope requires Lua-only output.")
                    # Rechain: remove any depends_on IDs that no longer exist
                    # so the DAG doesn't carry ghost dependencies that stall
                    # later tasks waiting on a task that was just evicted.
                    _surviving_ids = {_t["id"] for _t in ctx.tasks_list}
                    for _t in ctx.tasks_list:
                        _before_deps = _t["depends_on"]
                        _t["depends_on"] = [_d for _d in _before_deps if _d in _surviving_ids]
                        _ghost = set(_before_deps) - _surviving_ids
                        if _ghost:
                            print(f"  [Director Guard] ↳ Task {_t['id']}: removed ghost depends_on "
                                  f"{sorted(_ghost)} (task(s) were dropped).")

            # ── A4: Reject C++ tasks that duplicate existing bridge functions ─
            _excl_set = getattr(ctx, '_bridge_exclusion_set', set())
            _filtered: list = []
            for _t in ctx.tasks_list:
                _domain_upper = _t.get("domain", "").upper()
                _title_lower = _t.get("title", "").lower()
                # Drop hedged / conditional C++ tasks — they always produce delegates.
                # Patterns: 'if necessary', 'if needed', 'if required', 'if applicable'.
                _HEDGE_RE = re.compile(
                    r'\bif\s+(necessary|needed|required|applicable|warranted)\b',
                    re.IGNORECASE,
                )
                if _domain_upper in ("C++", "CPP", "PHYS") and _HEDGE_RE.search(_title_lower):
                    print(
                        f"  [Director Guard] ⚠ Dropping Task {_t['id']} [{_t['domain']}] "
                        f"'{_t['title']}' — conditional/hedged task always produces a delegate."
                    )
                    continue
                # Drop C++ tasks whose title matches an already-bridged function.
                if _excl_set and _domain_upper in ("C++", "CPP", "PHYS"):
                    _matched_fn = next(
                        (fn for fn in _excl_set if fn and fn in _title_lower), None
                    )
                    if _matched_fn:
                        print(
                            f"  [Director Guard] ⚠ Dropping Task {_t['id']} [{_t['domain']}] "
                            f"'{_t['title']}' — '{_matched_fn}' already in bridge contract."
                        )
                        continue
                _filtered.append(_t)
            if len(_filtered) < len(ctx.tasks_list):
                ctx.tasks_list = _filtered
                print(f"  [Director Guard] {len(ctx.tasks_list)} task(s) remain after guard pass.")
            break
        else:
            syntax_feedback = (
                f"\n\n[SYSTEM KERNEL ERROR: Your previous output failed to match the mandatory parsing schema entirely. "
                f"No tasks could be extracted because you omitted literal square brackets around the domain tags or dropped the header alignment. "
                f"You MUST self-correct immediately. Wrap the domain in brackets. Example: '### Task 1: [C++] - Integrate Gameplay Logic (DependsOn: None)'. "
                f"Do NOT output loose prose.]"
            )
            current_director_input = base_director_input + f"\n\n## PREVIOUS REJECTED OUTPUT:\n{ctx.director_output}\n" + syntax_feedback

    # ── Directive B: Interface Manifest (Anti-Hallucination Contract) ────────
    _cartridge_for_manifest = getattr(ctx, "mounted_cartridge", None)
    _OVERARCHING_ATTRACTIONS: set = set()
    if _cartridge_for_manifest is not None and hasattr(_cartridge_for_manifest, "overarching_entity_keywords"):
        _OVERARCHING_ATTRACTIONS = set(_cartridge_for_manifest.overarching_entity_keywords or [])

    def _derive_class_name(title_str: str) -> str:
        procedural_filler = {
            "create", "implement", "initialize", "setup", "define", "expose",
            "load", "integrate", "add", "build", "make", "update", "refactor",
            "write", "test", "for", "the", "a", "an", "to", "into", "from",
            "via", "using", "with", "and", "or", "basic", "game", "system",
            "module", "feature", "request", "want", "you", "information", "active",
            "subtask", "overarching", "context", "constraints", "original",
        }
        pos_title = re.split(r'\[block\]|Overarching Context', title_str, maxsplit=1, flags=re.IGNORECASE)[0]
        clean_str = re.sub(r'[^a-zA-Z0-9\s]', ' ', pos_title)
        tokens = clean_str.split()
        filtered = [t for t in tokens if t.lower() not in procedural_filler and len(t) > 1]
        if filtered:
            remaining_lower = {t.lower() for t in filtered}
            if remaining_lower.issubset(_OVERARCHING_ATTRACTIONS):
                return "GenericPhysicsPrimitives"
        if not filtered:
            return "GenericPhysicsPrimitives"
        capped = filtered[:3]
        pascal_name = "".join(t.capitalize() for t in capped)
        if pascal_name[0].isdigit():
            pascal_name = "Module" + pascal_name
        return pascal_name[:40]

    _target_title = ctx.user_prompt if hasattr(ctx, 'user_prompt') and ctx.user_prompt else ""
    if hasattr(ctx, 'tasks_list') and ctx.tasks_list:
        _target_title = ctx.tasks_list[0].get("title", _target_title)

    _manifest_domains = set(getattr(ctx, 'domain_metadata_registry', {}).keys()) or {"C++", "PHYS"}
    _has_code_domain = any(
        resolve_agent_name(t.get("domain", "")) in _manifest_domains
        for t in (ctx.tasks_list or [])
    )
    if _has_code_domain:
        _expected_target_name = _derive_class_name(_target_title)
        ctx.interface_manifest = (
            f"\n[SYSTEM KERNEL CONTRACT: Both the Test Suite and the Implementation MUST strictly "
            f"utilize the class name '{_expected_target_name}'. Do not invent alternative class names.]\n"
        )
    else:
        ctx.interface_manifest = ""

    ctx.output_parts.append(ctx.director_output + "\n")

    if not ctx.tasks_list:
        ctx.tasks_list.append({"id": "1", "domain": "C++", "title": "Full Implementation", "depends_on": []})
        print(f"  [Director] CRITICAL ERROR: Maximum parsing recovery retries exceeded. Forced default fallback.")

    print(f"  [Director] Created {len(ctx.tasks_list)} task(s)")
    return ctx
