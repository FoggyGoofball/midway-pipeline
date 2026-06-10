"""
mesh_fetches_director.py - Phase 3 Director task decomposition
==============================================================
Extracted from mesh_fetches.py to keep individual files under 1 000 lines.

Contains:
  - _run_director_phase
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
    call_ollama,
    build_director_prompt,
    PipelineContext,
)
from pipeline import get_unavailable_domains_text
from _anchors import CANONICAL_ANCHORS
from mesh_fetches_helpers import (
    _build_completed_work_snapshot,
    _build_director_scope_mandate,
    _build_attraction_design_block,
    _sanitize_attraction_path,
)


def _run_director_phase(ctx: PipelineContext) -> PipelineContext:
    """Phase 3: Director task decomposition + interface manifest derivation."""
    _ts = datetime.now().strftime('%H:%M:%S')
    print(f"\n{'='*70}")
    print(f"  [{_ts}] Phase 3: Director - Task Decomposition")
    print(f"{'='*70}")
    ctx.output_parts.append("\n## Phase 3: Director - Task Decomposition\n")

    # ═══════════════════════════════════════════════════════════════════════
    # MONOLITHIC MODE — REMOVED 2026-06-04
    # The NARROW+NEW_ATTRACTION monolithic bypass has been eliminated.
    # ALL new attraction requests now go through the blueprint phase which
    # produces the full task decomposition via _enrich_blueprint_tasks.
    # The old code path (9 hardcoded anchors) was insufficient — the GDD
    # requires 12+ tasks per attraction. Blueprint + enrichment is the
    # canonical path.
    # ═══════════════════════════════════════════════════════════════════════

    director_prompt = build_director_prompt(user_prompt=ctx.user_prompt, project_root=ctx.project_root)

    # -- Inject cartridge-level Director directives (Lua-first mandate, etc.) --
    _director_extra_fn = getattr(ctx, '_cartridge_get_director_extra', None)
    if callable(_director_extra_fn):
        try:
            _director_extra = _director_extra_fn()
            if _director_extra:
                director_prompt = director_prompt + "\n\n" + _director_extra
        except Exception:
            pass

    # Keep snippets tight so the Director payload stays well under the
    # pre-summarizer threshold - the blueprint constraint block is the
    # important content; GDD/state are background only.
    gdd_snippet = TokenBudget._block_aware_collapse(ctx.gdd_context, 800) if ctx.gdd_context else "(no GDD context)"
    state_snippet = TokenBudget._block_aware_collapse(ctx.project_state, 600) if ctx.project_state else "(no project state)"


    # -- Inject live bridge contract API surface so the Director cannot invent
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
                    "\n\n## Active Bridge Contract - COMPLETE Approved API List\n"
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

    # -- Inject the approved blueprint checklist so the Director is bound to it.
    # This is the single most important context block - it must survive the
    # pre-summarizer unchanged, so it is wrapped in a labelled HARD CONSTRAINT
    # block rather than buried in prose that the compressor may discard.
    _blueprint_path_for_director = ctx.project_root / "docs" / "project_blueprint.md"
    _blueprint_constraint_block = ""
    try:
        _bp_text = _blueprint_path_for_director.read_text(encoding="utf-8").strip()
        if _bp_text:
            _blueprint_constraint_block = (
                "\n\n## APPROVED BLUEPRINT - MANDATORY TASK LIST (DO NOT OMIT ANY ITEM)\n"
                "The following checklist was reviewed and approved. "
                "You MUST generate exactly one Director task for every unchecked item below. "
                "Do NOT collapse, merge, or drop any item:\n"
                + _bp_text
                + "\n"
            )
    except Exception:
        pass

    base_director_input = (
        f"{director_prompt}\n\n"
        f"## Relevant GDD Context\n{gdd_snippet}\n\n"
        f"## Current Project State\n{state_snippet}"
        f"{_bridge_api_snippet}\n\n"
        + _build_completed_work_snapshot(ctx)
        + _build_director_scope_mandate(ctx)
        + _build_attraction_design_block(ctx)
        + _blueprint_constraint_block
        + f"---\nUSER REQUEST:\n{ctx.user_prompt}"
    )

    task_regex = r"### Task ([a-zA-Z0-9]+):\s*\[([^\]]+)\]\s*[-]\s*(.+?)(?:\s*\(DependsOn:\s*(.+?)\))?\s*$"

    def _parse_task_meta_field(all_lines: list, header_idx: int, field_name: str) -> list:
        """Parse a metadata field from the lines following a Director task header.

        Looks for a line matching '<Field>: <value>' within the 5 lines after
        header_idx.  Splits the value on commas, strips whitespace, and removes
        the sentinel value 'None' / 'none'.
        Returns a list of strings, or [] when the field is absent or empty.
        """
        import re as _re_meta
        _field_re = _re_meta.compile(
            rf"^{re.escape(field_name)}\s*:\s*(.+)$", _re_meta.IGNORECASE
        )
        for offset in range(1, 6):
            idx = header_idx + offset
            if idx >= len(all_lines):
                break
            stripped = all_lines[idx].strip()
            if not stripped:
                continue
            # Stop scanning if we've hit another task header
            if stripped.startswith("###"):
                break
            m = _field_re.match(stripped)
            if m:
                raw = m.group(1).strip()
                if raw.lower() == "none":
                    return []
                return [v.strip() for v in raw.split(",") if v.strip() and v.strip().lower() != "none"]
        return []


    # Lenient form: "### Task 1: Lua - Title" or "### Task 1: [Lua] Title" (missing dash)
    # Normalised to the strict form before matching so parse retries are not wasted on formatting.
    _task_norm_re = re.compile(
        r"^(###\s*Task\s+[a-zA-Z0-9]+\s*:\s*)([a-zA-Z0-9 /+#]+?)\s*[-]\s*(.+)$"
    )

    def _normalise_task_header(raw_line: str) -> str:
        """If *raw_line* is a director task header without bracket-wrapped domain,
        rewrite it so the strict ``task_regex`` can match it."""
        stripped = raw_line.strip()
        # Already has brackets - nothing to do.
        if re.search(r"\[[^\]]+\]", stripped):
            return raw_line
        m = _task_norm_re.match(stripped)
        if m:
            prefix, domain, rest = m.group(1), m.group(2).strip(), m.group(3).strip()
            return f"{prefix}[{domain}] - {rest}"
        return raw_line
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

        # skip_pre_summarizer=True: the blueprint constraint block injected above
        # is the authoritative task list - compressing it is the root cause of
        # tasks being dropped. Snippets are already pre-compacted above.
        ctx.director_output = call_ollama(
            DIRECTOR_SYSTEM, current_director_input, f"Director (Attempt {attempt})", DIRECTOR_MODEL,
            skip_pre_summarizer=True,
        )

        from ollama_client import is_fatal_ollama_error as _is_fatal_dir
        if _is_fatal_dir(ctx.director_output):
            print(f"  [Director] Ollama error during Director - aborting pipeline.")
            ctx.final_output = f"Pipeline aborted: Ollama unreachable during Director. {ctx.director_output.strip()}"
            return ctx

        # -- Parse tasks, detecting per-task [MATH_HEAVY] annotations ----------
        # Accepts both [MATH_HEAVY] and [MATH HEAVY] (spaced form).
        _math_heavy_re = re.compile(r"\[\s*\*?\*?\s*MATH[_ ]?HEAVY\s*\*?\*?\s*\]", re.IGNORECASE)
        ctx.tasks_list = []
        lines = ctx.director_output.splitlines()
        for line_idx, line in enumerate(lines):
            match = re.match(task_regex, _normalise_task_header(line).strip())
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
                # Only flag if the immediately following non-empty line consists
                # solely of the [MATH_HEAVY] tag - no prose mixed in.
                _scan_idx = line_idx + 1
                while _scan_idx < len(lines):
                    _scan_line = lines[_scan_idx].strip()
                    if _scan_line:
                        # Accept only a line whose entire content IS the tag
                        if _math_heavy_re.fullmatch(_scan_line):
                            is_math_heavy = True
                        break  # stop at first non-empty line regardless
                    _scan_idx += 1
            # -- Extract target_file from task title ---------------------------
            # Director often emits paths in titles:
            #   "Implement ball count - attractions/skeeball.lua"
            #   "Register OnLoad() function ... - attractions/skeebal/skeeball.lua"
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
            # -- NEW_ATTRACTION hard-override: ALL tasks MUST target the single
            # shared canonical file.  The Director is not trusted to emit the
            # correct path (it may spell it wrong, omit the subfolder, or place
            # it at the repo root).  Override unconditionally so every task in
            # this wave extends the same file.
            if _d_scope_mode == "NEW_ATTRACTION" and _user_file_constraint_canonical:
                _task_target_file = _sanitize_attraction_path(
                    _task_target_file or "", _user_file_constraint_canonical, task_id
                )
            ctx.tasks_list.append({
                "id": task_id,
                "domain": domain,
                "title": title,
                "depends_on": depends_on,
                "math_heavy": is_math_heavy,
                "target_file": _task_target_file,
                # Interface-contract fields from the new Director output format.
                # Parsed from the lines that immediately follow the task header.
                # Safe to leave empty if the Director omitted them.
                "inputs": _parse_task_meta_field(lines, line_idx, "Inputs"),
                "outputs": _parse_task_meta_field(lines, line_idx, "Outputs"),
                "hooks": _parse_task_meta_field(lines, line_idx, "Hooks"),
            })

        # Orphan-tag broadcast intentionally removed: a bare [MATH_HEAVY] that
        # appears anywhere in the director output but is NOT attached to a
        # specific task line must NOT flood all tasks with Pro Mode.  The
        # Director is expected to place the tag on the exact task line(s) that
        # require heavy numerical computation.

        # -- Pro Mode: per-task MATH_HEAVY gate --------------------------------
        _math_heavy_ids = [t["id"] for t in ctx.tasks_list if t.get("math_heavy")]
        if _math_heavy_ids and not ctx.pro_mode_always:
            print(f"\n{'='*50}")
            print(f"  MATH_HEAVY DETECTED - Tasks flagged: {', '.join(_math_heavy_ids)}")
            print(f"{'='*50}")
            import sys as _sys
            from pipeline import AUTO_APPROVE_GATES as _auto_pro
            if not _sys.stdin.isatty() or _auto_pro:
                # Non-interactive / headless run OR auto-approve - enable pro mode automatically
                user_input = "y"
                _reason = "AUTO_APPROVE_GATES=True" if _auto_pro else "non-interactive session"
                print(f"  [Pro Mode] Auto-enabling pro mode ({_reason}).")
            else:
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
                print(f"  [Pro Mode] ALWAYS - will auto-enable for all math-heavy tasks this session.")
            elif user_input in ("y", "yes"):
                ctx.math_heavy_tasks.update(_math_heavy_ids)
                print(f"  [Pro Mode] ENABLED for task(s): {', '.join(_math_heavy_ids)}.")
            else:
                print(f"  [Pro Mode] Declined - task(s) {', '.join(_math_heavy_ids)} will run in standard mode.")
        elif _math_heavy_ids and ctx.pro_mode_always:
            ctx.math_heavy_tasks.update(_math_heavy_ids)
            print(f"  [Pro Mode] Auto-enabled (always) for task(s): {', '.join(_math_heavy_ids)}.")

        # Keep legacy ctx.pro_mode in sync so any existing callers remain safe
        ctx.pro_mode = bool(ctx.math_heavy_tasks)

        if ctx.tasks_list:
            # -- A3: Hard-drop ALL C++ tasks for NEW_ATTRACTION scope -----------
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
                    print(f"  [Director Guard] Dropped {_dropped} C++ task(s) - "
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
                            print(f"  [Director Guard] -> Task {_t['id']}: removed ghost depends_on "
                                  f"{sorted(_ghost)} (task(s) were dropped).")

            # -- A4: Reject C++ tasks that duplicate existing bridge functions --
            _excl_set = getattr(ctx, '_bridge_exclusion_set', set())
            _filtered: list = []
            for _t in ctx.tasks_list:
                _domain_upper = _t.get("domain", "").upper()
                _title_lower = _t.get("title", "").lower()
                # Drop hedged / conditional C++ tasks - they always produce delegates.
                # Patterns: 'if necessary', 'if needed', 'if required', 'if applicable'.
                _HEDGE_RE = re.compile(
                    r'\bif\s+(necessary|needed|required|applicable|warranted)\b',
                    re.IGNORECASE,
                )
                if _domain_upper in ("C++", "CPP", "PHYS") and _HEDGE_RE.search(_title_lower):
                    print(
                        f"  [Director Guard] Dropping Task {_t['id']} [{_t['domain']}] "
                        f"'{_t['title']}' - conditional/hedged task always produces a delegate."
                    )
                    continue
                # Drop C++ tasks whose title matches an already-bridged function.
                if _excl_set and _domain_upper in ("C++", "CPP", "PHYS"):
                    _matched_fn = next(
                        (fn for fn in _excl_set if fn and fn in _title_lower), None
                    )
                    if _matched_fn:
                        print(
                            f"  [Director Guard] Dropping Task {_t['id']} [{_t['domain']}] "
                            f"'{_t['title']}' - '{_matched_fn}' already in bridge contract."
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

    # -- Directive B: Interface Manifest (Anti-Hallucination Contract) ---------
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

    # -- Mandatory-task injection: ensure economy/modifier tasks always exist ---
    # The pre-summarizer can compress the blueprint context, causing the Director
    # to omit mandatory tasks. Programmatically append any that are missing so
    # downstream Lua agents always receive them - no extra LLM pass needed.
    if _d_scope_mode in ("NEW_ATTRACTION", "MODIFY_ATTRACTION") and ctx.tasks_list:
        _task_titles_lower = " ".join(t.get("title", "") for t in ctx.tasks_list).lower()
        _canonical_file = _user_file_constraint_canonical or ""
        _next_id = str(len(ctx.tasks_list) + 1)
        _last_id = ctx.tasks_list[-1].get("id", _next_id)

        if not any(kw in _task_titles_lower for kw in ("modifier", "attractionconstants", "engine_mod_")):
            _mod_task = {
                "id": _next_id,
                "domain": "Lua",
                "title": (
                    "Integrate modifier system - read AttractionConstants.modifiers every OnStep "
                    "frame and apply ENGINE_MOD_HEAT/LUCK/SLEIGHT_OF_HAND to gameplay variables"
                    + (f" - {_canonical_file}" if _canonical_file else "")
                ),
                "depends_on": [_last_id],
                "target_file": _canonical_file,
                "hooks": ["OnStep"],
            }
            ctx.tasks_list.append(_mod_task)
            _next_id = str(int(_next_id) + 1)
            _last_id = _mod_task["id"]
            print(
                f"  [Director Guard] Injected mandatory modifier task "
                f"(task_{_mod_task['id']}) - was absent from Director output."
            )

        if not any(kw in _task_titles_lower for kw in ("awardtickets", "awardtokens", "economy", "tickets", "tokens")):
            _eco_task = {
                "id": _next_id,
                "domain": "Lua",
                "title": (
                    "Implement economy hooks - call Engine.AwardTickets(n, label) "
                    "with Engine.GetStreak() multiplier on win/score events"
                    + (f" - {_canonical_file}" if _canonical_file else "")
                ),
                "depends_on": [_last_id],
                "target_file": _canonical_file,
                "hooks": ["OnStep"],
            }
            ctx.tasks_list.append(_eco_task)
            print(
                f"  [Director Guard] Injected mandatory economy task "
                f"(task_{_eco_task['id']}) - was absent from Director output."
            )

    # =====================================================================
    # Step 1: Enforce Linear File Dependency (File-Linearization Pass)
    # =====================================================================
    # Tasks that share a "target_file" value must run sequentially, not in
    # parallel. This pass groups tasks by target_file and chains them via
    # DependsOn so that Task N depends on Task N-1 within each file group.
    #
    # After linearization, marks ctx as dirty so the wave sorter re-sorts
    # waves regardless of original depends_on ordering.
    #
    # Tasks with target_file=None or "" are left at their original dep order.
    _file_groups: dict[str, list] = {}  # target_file -> list of task dicts
    _ungrouped: list = []               # tasks with no target_file
    for _t in ctx.tasks_list:
        _tf = _t.get("target_file") or ""
        if _tf:
            _file_groups.setdefault(_tf, []).append(_t)
        else:
            _ungrouped.append(_t)

    _rechained: list = []
    _file_chain_counts: dict = {}
    _linearizer_dirty = False  # True if any depends_on was overwritten
    for _tf, _group in _file_groups.items():
        # Sort each group by original id order so chaining is deterministic
        _group.sort(key=lambda x: int(x["id"]) if x["id"].isdigit() else x["id"])
        _prev_id = None
        for _i, _t in enumerate(_group):
            if _i == 0:
                # First task in the file chain: keep its original depends_on
                # (may reference tasks in other files or be root)
                pass
            else:
                # Force dependency on the immediately previous task in THIS file
                _original_dep = _t.get("depends_on", [])
                if _original_dep != [_prev_id]:
                    _t["depends_on"] = [_prev_id]
                    _linearizer_dirty = True
                _file_chain_counts[_tf] = _file_chain_counts.get(_tf, 0) + 1
            _prev_id = _t["id"]
            _rechained.append(_t)

    if _file_chain_counts:
        for _tf, _count in _file_chain_counts.items():
            print(f"  [File-Linearizer] Chained {_count} task(s) sequentially for '{_tf}'")

    # Append ungrouped tasks in their original order
    _rechained.extend(_ungrouped)
    ctx.tasks_list = _rechained

    # ── Flag dirty so wave sorter re-sorts ───────────────────────────────
    if _linearizer_dirty:
        ctx._tasks_dirty = True
        print("  [File-Linearizer] ⚠ Flagged task list as dirty  wave sorter will re-sort.")

    print(f"  [Director] Created {len(ctx.tasks_list)} task(s)")
    return ctx
