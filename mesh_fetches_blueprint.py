"""
mesh_fetches_blueprint.py  Blueprint generation and approval phase
===================================================================
Extracted from mesh_fetches.py to keep individual files under 1 000 lines.

Contains:
  - _run_blueprint_phase
"""

from __future__ import annotations

import re
from token_budget import TokenBudget
from _anchors import (
    CANONICAL_ANCHORS,
    build_lua_skeleton,
    get_all_anchor_tasks,
    verify_and_reinject_anchor,
)
from _pipeline_helpers import (
    REASONING_MODEL,
    call_ollama,
    atomic_write_text, trigger_chime,
    PipelineContext,
    AGENT_FILE_TOOLS_PROMPT, handle_file_read, handle_file_list,
    build_blueprint_context_pack,
)
from pipeline import (
    get_unavailable_domains_text,
    parse_file_references, fetch_referenced_files, set_referenced_files_cache,
)
from mesh_fetches_helpers import (
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
        f"HARD CONSTRAINTS  Do NOT plan for:\n"
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

    # Compute a dynamic budget for _ctx_pack so the combined blueprint_prompt
    # fits inside the model's context window without triggering hard truncation.
    # Reserve chars for: system prompt, bridge/scope/API rules appended later,
    # feature request, hard constraints, directives, and tool-result headroom.
    # Remaining budget is split: GDD 35%, state 20%, structure 15%, AST 30%.
    from ollama_client import resolve_ctx_size as _bp_resolve_ctx
    _BP_CTX_CHARS = int(_bp_resolve_ctx(REASONING_MODEL) * 1.5)  # tok -> chars
    _BP_SYSTEM_EST  = 6000   # conservative upper bound for _blueprint_system
    _BP_FIXED_EST   = 1200   # feature request + hard constraints + directives
    _BP_TOOL_HEADROOM = 1200 # headroom for tool-result rounds
    _BP_PACK_BUDGET = max(2000, _BP_CTX_CHARS - _BP_SYSTEM_EST - _BP_FIXED_EST - _BP_TOOL_HEADROOM)
    _BP_GDD_LIM     = int(_BP_PACK_BUDGET * 0.35)
    _BP_STATE_LIM   = int(_BP_PACK_BUDGET * 0.20)
    _BP_STRUCT_LIM  = int(_BP_PACK_BUDGET * 0.15)
    _BP_AST_LIM     = int(_BP_PACK_BUDGET * 0.30)
    _ctx_pack = build_blueprint_context_pack(
        gdd_context=ctx.gdd_context or "",
        project_state=ctx.project_state or "",
        structure=ctx.structure or "",
        project_root=ctx.project_root,
        ast_summary=_scope_ast_section or _ast_summary,
        gdd_limit=max(800, _BP_GDD_LIM),
        state_limit=max(400, _BP_STATE_LIM),
        structure_limit=max(400, _BP_STRUCT_LIM),
        ast_limit=max(600, _BP_AST_LIM),
    )

    # Pull bridge-contract mandate from the class-based cartridge if available.
    _bridge_contract_rule = ""
    if getattr(ctx, '_cartridge_get_project_context', None):
        _bridge_contract_rule = (
            "\n\nENGINE BRIDGE CONTRACT  MANDATORY:\n"
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
    # attractions/<slug>/<slug>.lua  derive it automatically so the user
    # never has to correct the blueprint for a file-layout violation.
    _user_file_constraint_canonical: str = ""
    if _scope_mode == "NEW_ATTRACTION" and _scope_target:
        _attr_slug = re.sub(r'[^\w]+', '_', _scope_target.strip().lower()).strip('_')
        if _attr_slug:
            _user_file_constraint_canonical = f"attractions/{_attr_slug}/{_attr_slug}.lua"
            # Persist to ctx so the scaffold writer below can see it.
            ctx._user_file_constraint_canonical = _user_file_constraint_canonical
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
                    f"\n\nAPPROVED BRIDGE API  use ONLY these exact function names:\n"
                    f"{_lifecycle_line}"
                    f"Physics (static geometry only): {', '.join(f for f in _physics_fns if 'Static' in f)}\n"
                    f"Physics (dynamic/moving bodies): {', '.join(f for f in _physics_fns if 'Dynamic' in f)}\n"
                    f"Physics (other): {', '.join(f for f in _physics_fns if 'Static' not in f and 'Dynamic' not in f)}\n"
                    f"Pools:   {', '.join(_pool_fns)}\n"
                    f"Economy: {', '.join(_econ_fns)}\n"
                    "NEVER invent names not in the lists above.\n"
                    "Static variants (SpawnStaticBox etc.) are for immovable cabinet geometry ONLY  "
                    "NEVER use them for balls, projectiles, or any body that moves.\n"
                    "Do NOT use placeholder namespaces like [MODULE][PhysicsModuleName] "
                    "or [Engine][EngineName]  the real namespaces are MidwayPhysics.* "
                    "and Engine.*\n"
                )
        except Exception:
            pass

    _blueprint_system = (
        "You are a Lead Producer with RESTRICTED access to the project codebase.\n\n"
        "Your role: analyse the project context already provided below, "
        "then produce a precise step-by-step markdown blueprint.\n\n"
        "BLUEPRINT RULES:\n"
        "1. Ground every task in REAL files visible in the AST index  "
        "NEVER invent file paths. If a path is not in the AST index, omit it.\n"
        "2. Use [FILE_READ:<path>, lines N-M] ONLY for files explicitly listed in "
        "the AST index summary below. One read per file  do NOT re-read the same path.\n"
        "3. Do NOT emit [FILE_READ:] or [FILE_LIST:] in your FINAL checklist output. "
        "These signals are for context gathering only and must not appear as tasks.\n"
        "4. Never reference IDE features, GUI tools, images, external URLs, or files "
        "from foreign subsystems (SDL, Box2D, OpenGL setup) unless they are in the AST index.\n"
        "5. Output ONLY the checklist  no prose, no commentary, no file-tool signals.\n"
        "6. Format: '- [ ] Task N: <verb> <specific thing> - <filepath>' on its own line. "
        "The verb MUST be an action word (Implement, Define, Add, Register, Create, Integrate). "
        "NEVER use a bare API function signature as the task name "
        "(e.g. 'SpawnDynamicSphere(...)' is FORBIDDEN as a task title).\n"
        "7. Keep the checklist SHORT and SCOPED: 410 tasks minimum, each targeting a single "
        "concrete gameplay concern (not a single API call).\n"
        "8. ALL gameplay tasks MUST be implemented in Lua attraction scripts. "
        "Do NOT plan any tasks that modify C++ engine files (*.cpp, *.h)  "
        "the bridge contract already exposes all required primitives.\n"
        "9. For NEW_ATTRACTION requests, the FIRST task MUST define ALL four attraction lifecycle "
        "entry points: OnLoadStatic, OnLoad, OnUnload, AND the OnStep callback. "
        "Use ONLY the real hook names: "
        + (", ".join(_bp_lifecycle_hooks) if _bp_lifecycle_hooks else "OnLoadStatic, OnLoad, OnUnload, OnStep")
        + ". "
        "Do NOT invent wrapper functions like OnLoadAttraction() or any other name not in the "
        "approved lifecycle list above. OnStep MUST be registered via "
        "MidwayPhysics.OnStep(function(dt) ... end) inside OnLoad()  "
        "it is NOT an optional extra and MUST appear in the first task.\n"
        "10. Every task MUST describe WHAT gameplay concern it addresses, not just WHICH API "
        "to call. Example  GOOD: 'Implement ball launch on player input using "
        "SpawnDynamicSphere and ApplyImpulse'. BAD: 'SpawnDynamicSphere(lx,ly,lz,radius)'.\n"
        "11. Spawn API semantics  use the CORRECT variant for each body type:\n"
        "    - SpawnStaticBox / SpawnStaticBoxR → ONLY for permanent immovable geometry "
        "(walls, ramps, cabinet surfaces). NEVER use for gameplay objects that move.\n"
        "    - SpawnDynamicSphere / SpawnDynamicBox / SpawnDynamicCapsule → for any body "
        "that moves during gameplay (balls, projectiles, tokens).\n"
        "    - Mixing these up (e.g. using SpawnStaticBox for a ball) is a hard error.\n"
        "12. Gameplay counters, timers, and state (ball count, score, round index) are plain "
        "Lua variables or tables. Do NOT describe them as engine primitives, C++ features, "
        "or inventory systems. Example  CORRECT: 'Track remaining balls with a Lua counter'. "
        "WRONG: 'Create an inventory system for balls'.\n"
        "13. ECONOMY MANDATE (NON-NEGOTIABLE): The blueprint MUST include BOTH of the following "
        "as explicit tasks:\n"
        "    a. A task that reads AttractionConstants.modifiers inside OnStep every frame "
        "(never cache modifier values at load time).\n"
        "    b. A task that calls Engine.AwardTickets(n, label) or Engine.AwardTokens(n, label) "
        "on win/score events, using Engine.GetStreak() as a multiplier.\n"
        "    Omitting either will cause an AUTOMATIC REJECTION of the blueprint.\n"
        "14. CANONICAL STRUCTURAL TEMPLATE  All new attractions MUST follow the exact lifecycle "
        "pattern in attractions/_REFERENCE/REFERENCE.lua:\n"
        "    - OnLoadStatic() → SpawnSharedBooth() first, then permanent geometry\n"
        "    - OnLoad() → create object pools, register MidwayPhysics.OnStep callback\n"
        "    - Inside OnStep: read AttractionConstants.modifiers (never cached at load time), "
        "call Engine.AwardTickets/Tokens on score events\n"
        "    - OnUnload() → print diagnostics only (pools auto-reclaimed by engine)\n"
        "    - All game-specific constants MUST be local tables (never AttractionConstants.booth)\n"
        "    - Every exported hook name must match REFERENCE.lua exactly: OnLoadStatic, OnLoad, OnUnload\n"
        + (
            f"\n## HARD FILE CONSTRAINT  MANDATORY:\n"
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
        + (
            "15. SCAFFOLD-FIRST RULE (CRITICAL  enables iterative SEARCH/REPLACE patching):\n"
            "    - Task 1 for any file MUST output an OVERARCHING STRUCTURAL SCAFFOLD  the "
            "complete .lua file defining EVERY lifecycle entry point (OnLoadStatic, OnLoad, OnStep, "
            "OnUnload) with TODO comment placeholders filling the non-trivial bodies.\n"
            "    - Example placeholders:\n"
            "        -- TODO: Spawn permanent booth geometry\n"
            "        -- TODO: Create object pools for balls/projectiles\n"
            "        -- TODO: Implement input handling\n"
            "        -- TODO: Register scoring hooks\n"
            "    - Task 2+ MUST NOT output full files. They output ONLY SEARCH/REPLACE blocks "
            "that target the TODO comments left by Task 1.\n"
            "    - The scaffold MUST compile/parse without errors  every placeholder sits "
            "inside a valid function body with a minimal return/noop implementation so the "
            "file is structurally complete from task 1 onward.\n"
            "    - The single file constraint (rule 14) interacts with this rule: when all "
            "tasks target ONE file, Task 1 produces the full-file scaffold, "
            "and tasks 2N produce only SEARCH/REPLACE diffs against it.\n"
        )
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
                            header=f"Blueprint tool results overflow  round {_tool_round + 1} ({len(_tool_overflow)} chars)",
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
            )

            from ollama_client import is_fatal_ollama_error as _is_fatal_bp
            if _is_fatal_bp(blueprint):
                print(f"  [Blueprint] ⛔ Ollama error during Blueprint Generation  aborting pipeline.")
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
                # Discard error results  feeding them back causes hallucination spirals
                if "**Error:**" in _result or "File not found" in _result:
                    print(f"  [Blueprint FileTool] FILE_READ (skipped  not found): {_m.group(1)[:80]}")
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
                    print(f"  [Blueprint FileTool] FILE_LIST (skipped  not found): {_m.group(1)[:80]}")
                    continue
                _tool_results.append(_result)
                print(f"  [Blueprint FileTool] FILE_LIST: {_m.group(1)[:80]}")

            if not _tool_results:
                break

            # Replace (not append) tool context each round to prevent context snowballing
            _tool_extra = (
                "\n\n## File Tool Results (injected by orchestrator  these are the ONLY verified paths)\n"
                + "\n".join(_tool_results)
                + "\n\n[SYSTEM KERNEL: Use ONLY the file paths confirmed above. "
                "Do NOT emit any [FILE_READ:] or [FILE_LIST:] signals in your final checklist output. "
                "Produce the final checklist now.]"
            )
        else:
            print(f"  [Lead Producer] ⚠ Tool resolution reached {_MAX_TOOL_ROUNDS} rounds  using last output.")

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

        # ── Anchor-based minimum task count ──────────────────────────────
        # For NEW_ATTRACTION, the scaffold has N deterministic anchors.
        # The blueprint MUST generate at least N tasks (one per anchor)
        # or agents will leave lifecycle markers unfilled.
        _anchor_minimum = 3  # default fallback
        try:
            from _anchors import get_anchor_count
            _anchor_minimum = get_anchor_count()
        except Exception:
            pass
        if _scope_mode == "NEW_ATTRACTION" and _n_tasks < _anchor_minimum:
            _bp_issues.append(
                f"Only {_n_tasks} task(s) for NEW_ATTRACTION  "
                f"minimum {_anchor_minimum} required (one per scaffold anchor). "
                f"There are {_anchor_minimum} deterministic anchor markers "
                f"in the scaffold file that must each be filled by exactly one task. "
                f"Expand the plan to cover every lifecycle phase and gameplay concern."
            )
        elif _n_tasks < 3:
            _bp_issues.append(
                f"Only {_n_tasks} task(s) generated  minimum 3 required for a useful blueprint. "
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

        # Economy mandate check  applies to all attraction scopes
        if _scope_mode in ("NEW_ATTRACTION", "MODIFY_ATTRACTION"):
            _bp_lower = blueprint.lower()
            _has_modifier = any(kw in _bp_lower for kw in (
                "attractionconstants.modifiers", "engine_mod_", "modifier",
            ))
            _has_economy = any(kw in _bp_lower for kw in (
                "awardtickets", "awardtokens", "economy", "tickets", "tokens",
            ))
            if not _has_modifier:
                _bp_issues.append(
                    "ECONOMY VIOLATION: No task reads AttractionConstants.modifiers inside OnStep. "
                    "Add an explicit task: 'Integrate modifier system  read AttractionConstants.modifiers "
                    "every OnStep frame and apply ENGINE_MOD_HEAT/LUCK/SLEIGHT_OF_HAND to gameplay variables'."
                )
            if not _has_economy:
                _bp_issues.append(
                    "ECONOMY VIOLATION: No task calls Engine.AwardTickets or Engine.AwardTokens. "
                    "Add an explicit task: 'Implement economy hooks  call Engine.AwardTickets(n, label) "
                    "with Engine.GetStreak() multiplier on win/score events'."
                )

        # Detect phantom API names in task lines.
        # Only list names that do NOT appear in the bridge contract.
        # Verified against engine_lua_bridge_contract.md  — do NOT add:
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

        # -- Phantom API AUTO-SUBSTITUTION: hard override after repeated failures --
        # If the same phantom API has been rejected 3+ times, the model is stuck
        # in a loop and will never self-correct.  Apply a programmatic rewrite
        # to the blueprint text BEFORE building the retry prompt so the model
        # starts from a corrected draft.
        _phantom_substitutions = {
            "spawndynamicball": "SpawnDynamicSphere",
            "spawndynamicbody": "SpawnDynamicSphere",
            "spawnstaticball": "SpawnStaticSphere",
            "spawnstaticbody": "SpawnStaticBox",
            "spawnstaticplane": "SpawnStaticBox",
            "checkcollision": "IsSensorTriggered",
            "destroyentity": "DestroyBody",
            "releasehandle": "DestroyBody",
            "resetbody": "DestroyBody",
            "getlinearvelocity": "GetVelocity",
        }
        _retry_count = getattr(ctx, '_blueprint_retry_count', 0)
        ctx._blueprint_retry_count = _retry_count + 1
        if _found_phantoms and _retry_count >= 3:
            _applied_sub = False
            for _phantom_lower, _replacement in _phantom_substitutions.items():
                # Find the phantom name in any casing and replace it
                _pattern = re.compile(
                    re.escape(_phantom_lower), re.IGNORECASE
                )
                _before = blueprint
                blueprint = _pattern.sub(_replacement, blueprint)
                if blueprint != _before:
                    _applied_sub = True
                    # Remove the phantom issue since we fixed it programmatically
                    _bp_issues = [
                        i for i in _bp_issues
                        if _phantom_lower not in i.lower()
                    ]
            if _applied_sub:
                print(f"  [Blueprint Validator] 🔧 Auto-substituted {len(_found_phantoms)} phantom API name(s) "
                      f"after {_retry_count} retries — hard override applied.")
                _found_phantoms = []


        # ── File-constraint enforcement ─────────────────────────────────────
        # If the user previously specified a single target file, first attempt
        # a cheap programmatic rewrite for near-match paths (e.g. typos like
        # 'attractions/skeebal/skeeball.lua').  Only paths that differ in more
        # than the directory/filename spelling (i.e. a genuinely different
        # attraction entirely) are escalated to the LLM retry queue.
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
                # Determine the canonical attraction stem for typo detection.
                # e.g. 'attractions/skeeball/skeeball.lua' → stem = 'skeeball'
                _canon_stem = (
                    _user_file_constraint_canonical.rstrip("/")
                    .split("/")[-1]          # filename
                    .replace(".lua", "")
                    .lower()
                )

                def _is_typo_path(bad: str) -> bool:
                    """True when the bad path is just a misspelling of the
                    canonical file  same basename stem (after edit-distance
                    heuristic) or the same stem appears somewhere in the path."""
                    bad_lower = bad.lower()
                    bad_stem = bad_lower.rstrip("/").split("/")[-1].replace(".lua", "")
                    # Accept if stems share ≥ 80 % of characters (Jaccard on chars)
                    s1, s2 = set(_canon_stem), set(bad_stem)
                    jaccard = len(s1 & s2) / max(len(s1 | s2), 1)
                    return jaccard >= 0.8 or _canon_stem in bad_lower

                _typo_paths   = [f for f in _violating if _is_typo_path(f)]
                _foreign_paths = [f for f in _violating if not _is_typo_path(f)]

                if _typo_paths:
                    # Rewrite every typo variant directly in the blueprint string.
                    _rewritten = 0
                    for _bad in set(_typo_paths):
                        blueprint = re.sub(
                            re.escape(_bad),
                            _user_file_constraint_canonical,
                            blueprint,
                            flags=re.IGNORECASE,
                        )
                        _rewritten += 1
                    print(
                        f"  [Blueprint Validator] 🔧 Programmatically corrected "
                        f"{_rewritten} typo path(s) → '{_user_file_constraint_canonical}'"
                    )

                if _foreign_paths:
                    _unique_v = list(dict.fromkeys(_foreign_paths))[:6]
                    _bp_issues.append(
                        f"Blueprint references Lua file(s) other than the required "
                        f"'{_user_file_constraint_canonical}': "
                        + ", ".join(_unique_v)
                        + ". ALL tasks MUST target only the single required file."
                    )

        # -- MAX_RETRIES guard: after 3 retries, force-accept the blueprint --
        # The phi3:14b model physically cannot output 11+ task items reliably,
        # so we break the infinite loop after exhausting retries.
        _MAX_RETRIES = 3
        _retry_count = getattr(ctx, '_blueprint_retry_count', 0)
        if _bp_issues and _retry_count >= _MAX_RETRIES:
            print(f"  [Blueprint Validator] ⚠ {_retry_count} retries exhausted — force-accepting blueprint despite {len(_bp_issues)} issue(s)")
            print(f"  [Blueprint Validator] Issues bypassed: {'; '.join(_bp_issues[:3])}")

            # -- Programmatic injection: add missing mandatory tasks directly --
            # The LLM model physically cannot output 11+ tasks, so we inject
            # economy/modifier tasks into the blueprint text before saving.
            _bp_text_lower = blueprint.lower()
            _injected_count = 0

            # Check for modifier task
            if not any(kw in _bp_text_lower for kw in ("modifier", "attractionconstants", "engine_mod_")):
                _mod_line = (
                    f"- [ ] Integrate modifier system — read AttractionConstants.modifiers every OnStep "
                    f"frame and apply to gameplay variables"
                    + (f" - {_user_file_constraint_canonical}" if _user_file_constraint_canonical else "")
                )
                blueprint += "\n" + _mod_line
                _injected_count += 1
                print(f"  [Blueprint Validator] 🔧 Programmatically injected missing modifier task")

            # Check for economy task
            if not any(kw in _bp_text_lower for kw in ("awardtickets", "awardtokens", "economy", "tickets", "tokens")):
                _eco_line = (
                    f"- [ ] Implement economy hooks — call Engine.AwardTickets(n, label) "
                    f"with Engine.GetStreak() multiplier on win/score events"
                    + (f" - {_user_file_constraint_canonical}" if _user_file_constraint_canonical else "")
                )
                blueprint += "\n" + _eco_line
                _injected_count += 1
                print(f"  [Blueprint Validator] 🔧 Programmatically injected missing economy task")

            if _injected_count:
                print(f"  [Blueprint Validator] 🔧 Injected {_injected_count} mandatory task(s) into blueprint")

            _bp_issues = []  # Clear issues so we fall through to the accept path

        if _bp_issues:
            _issues_text = "\n".join(f"  - {i}" for i in _bp_issues)
            print(f"  [Blueprint Validator] ⚠ Auto-rejecting blueprint  {len(_bp_issues)} structural issue(s):")
            print(_issues_text)
            # Build a correction prompt and loop back for an auto-retry
            _scope_mandate_v = _build_scope_constraint_text(_scope_mode, _scope_target, _scope_refs)
            # Re-inject the user's hard file constraint into the auto-correction prompt
            # so the model cannot drift back to multi-file layouts on auto-retry.
            _fc_rule_v = (
                f"\n## HARD FILE CONSTRAINT (enforced  violations will be auto-rejected):\n"
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
                + f"\n## PREVIOUS DRAFT (contains structural violations  fix ALL issues below):\n"
                f"```\n{blueprint}\n```\n\n"
                f"## STRUCTURAL VIOLATIONS TO FIX (MANDATORY):\n"
                + _issues_text
                + f"\n\nRULES:\n"
                f"1. Fix EVERY violation listed above.\n"
                f"2. Keep all tasks from the previous draft that are still valid.\n"
                f"3. Output ONLY the revised checklist  no prose, no commentary.\n"
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
        print(f"  BLUEPRINT GATE  Review the architectural blueprint")
        print(f"  Location: {blueprint_path}")
        print(f"{'='*50}")
        print()
        print("  ┌─ WHAT IS THE BLUEPRINT GATE? ─────────────────────────────────────┐")
        print("  │ Before writing any code, the pipeline drew up a plan  the        │")
        print("  │ blueprint  describing every task it intends to carry out and      │")
        print("  │ which systems it will touch.                                       │")
        print("  │                                                                   │")
        print("  │ This is your chance to read that plan and catch mistakes BEFORE   │")
        print("  │ any code is generated. Open the file shown above, read it, then   │")
        print("  │ come back here.                                                   │")
        print("  │                                                                   │")
        print("  │  Y   plan looks good, start generating code.                    │")
        print("  │  n   something is wrong; you will be asked what to fix.         │")
        print("  └───────────────────────────────────────────────────────────────────┘")

        from pipeline import AUTO_APPROVE_GATES as _auto_bp_gates
        if _auto_bp_gates:
            print(f"  [Blueprint Gate] Blueprint auto-approved (AUTO_APPROVE_GATES=True).\n")
            break

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
                    f"\n## HARD FILE CONSTRAINT (enforced  violations will be auto-rejected):\n"
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
                    + (f"\n## Project GDD Context (use ONLY this  do not invent content from outside it)\n"
                       f"{_ctx_pack}\n\n" if _ctx_pack else "")
                    + f"\n## PREVIOUS DRAFT (revise this  do NOT discard valid tasks):\n"
                    f"```\n{blueprint}\n```\n\n"
                    f"## USER FEEDBACK ON PREVIOUS DRAFT:\n"
                    f"Problems: {issues}\n"
                    f"Instructions: {suggestions}\n\n"
                    f"RULES:\n"
                    f"1. Fix every problem listed above.\n"
                    f"2. Keep all tasks from the previous draft that are still valid.\n"
                    f"3. Remove or replace any task that references content NOT found in the GDD Context above.\n"
                    f"4. Output ONLY the revised checklist  no prose, no commentary.\n"
                    f"5. Format: '- [ ] Task N: <verb> <specific thing>' on its own line.\n"
                )
                # Also reset tool state so the revised prompt starts clean
                _tool_extra = ""
                _seen_paths = set()
                continue
        else:
            print(f"  [Blueprint Gate] Blueprint approved. Proceeding to execution.\n")
            break

    # ── Scaffold injection before any tasks execute ──
    # Fix 2026-06-04: Uses CANONICAL_ANCHORS from _anchors.py via build_lua_skeleton().
    # This is the SINGLE SOURCE OF TRUTH for anchor markers.  Every anchor is a
    # deterministic SEARCH/REPLACE target that exactly one downstream agent will
    # fill, eliminating "SEARCH block not found" drift from multi-wave rewrites.
    _scaffold_written = False
    _scaffold_target = getattr(ctx, '_user_file_constraint_canonical', '')
    if _scaffold_target:
        _target_path = ctx.project_root / _scaffold_target
        if not _target_path.is_file():
            _target_path.parent.mkdir(parents=True, exist_ok=True)
            _LUA_SKELETON = build_lua_skeleton(CANONICAL_ANCHORS)
            print(f"  [Scaffold] 🔗 Injected {len(CANONICAL_ANCHORS)} task anchor markers into skeleton (all deterministic)")
            atomic_write_text(_target_path, _LUA_SKELETON)
            _scaffold_written = True
            print(f"  [Scaffold] ✅ Wrote canonical Lua skeleton to {_scaffold_target} "
                  f"({len(_LUA_SKELETON.encode('utf-8'))} bytes)")
    if not _scaffold_written:
        # Even without a canonical file constraint, check if any task targets
        # a .lua file that doesn't exist yet and scaffold it.
        _all_tasks = getattr(ctx, '_enriched_blueprint_tasks', [])
        _scaffold_landed = set()
        if _all_tasks:
            for _t in _all_tasks:
                _tf = _t.get('target_file', '') or _t.get('output_file', '') or ''
                if not _tf.endswith('.lua') or _tf in _scaffold_landed:
                    continue
                _scaffold_landed.add(_tf)
                _tp = ctx.project_root / _tf
                if _tp.is_file():
                    continue  # already exists, skip
                _tp.parent.mkdir(parents=True, exist_ok=True)
                _LUA_SKELETON = build_lua_skeleton(CANONICAL_ANCHORS)
                atomic_write_text(_tp, _LUA_SKELETON)
                _scaffold_written = True
                print(f"  [Scaffold] ✅ Wrote canonical Lua skeleton to {_tf} "
                      f"({len(_LUA_SKELETON.encode('utf-8'))} bytes)")


    # ── Continuous Execution: extract first task & fall through ──
    content = blueprint_path.read_text(encoding="utf-8")
    first_match = re.search(
        r"^[-\*]?\s*\[ \]\s*(?:Task \d+:\s*)?(.+)",
        content, re.MULTILINE
    )
    if first_match:
        raw_line = first_match.group(0)
        task_text = first_match.group(1).strip()
        # Persist the raw original request so subsequent blueprint iterations
        # can restore it into <macro_invariants> even after reset_state() clears
        # ctx.user_prompt to "".  Only overwrite if not already set (first pass).
        if not getattr(ctx, '_original_user_prompt', ''):
            ctx._original_user_prompt = ctx.user_prompt
        original_request = ctx._original_user_prompt or ctx.user_prompt
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
        print("  [Lead Producer] Blueprint generated but no tasks found  continuing with original prompt.")

    return ctx

