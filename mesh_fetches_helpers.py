"""
mesh_fetches_helpers.py  Blueprint scope classifier and build helpers
======================================================================
Extracted from mesh_fetches.py to keep individual files under 1 000 lines.

Contains:
  - _classify_blueprint_scope
  - _build_scope_annotated_ast
  - _sanitize_attraction_path
  - _extract_symbol_toc
  - _build_attraction_design_block
  - _build_completed_work_snapshot
  - _build_director_scope_mandate
  - _build_scope_constraint_text
  - _enrich_blueprint_tasks
"""

from __future__ import annotations

import re
from token_budget import TokenBudget
from _pipeline_helpers import (
    REASONING_MODEL,
    call_ollama,
    PipelineContext,
    build_blueprint_context_pack,
)
from pipeline import get_unavailable_domains_text


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
            # NOTE: 'game' intentionally excluded  it is a valid noun that
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
            # noun is found  avoids picking up preamble words like "refer".
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

    # Ambiguous / non-attraction request  no constraint injected.
    return ("GENERAL", "", [])


def _build_scope_annotated_ast(
    scope_mode: str,
    scope_target: str,
    scope_refs: list[str],
    ast_topology,
) -> str:
    """Return a scope-annotated version of the AST summary.

    Files in `scope_refs` are marked [REFERENCE ONLY  do NOT make this a task target].
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
            f"{basename} [REFERENCE ONLY  do NOT make this a task target]",
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
            f"## SCOPE: MODIFY EXISTING ATTRACTION  {target_name}\n"
            f"All tasks MUST target '{target_name}' or shared infrastructure only.\n"
            "Files marked [REFERENCE ONLY] below MUST NOT appear as task targets.\n\n"
        ) + raw_summary
    return raw_summary


def _sanitize_attraction_path(
    path: str,
    canonical: str,
    task_id: str = "",
) -> str:
    """Return the canonical attraction path, remapping any malformed variant.

    Handles the known failure modes seen in live runs:
       Root-level file    : "skeeball.lua"           → canonical
       Flat attractions/  : "attractions/skeeball.lua"→ canonical
       Typo subfolder     : "attractions/skeebal/"  → canonical
       Already correct    : "attractions/skeeball/skeeball.lua" → unchanged

    Emits a one-line warning whenever a remap occurs.
    """
    if not path or not canonical:
        return canonical or path
    norm = path.replace("\\", "/").strip("/")
    if norm == canonical:
        return canonical
    tag = f"Task {task_id}: " if task_id else ""
    print(f"  [PathGuard] ⚠️  {tag}remapping '{norm}' → '{canonical}'")
    return canonical


def _extract_symbol_toc(content: str, rel_path: str) -> list[str]:
    """Return a sorted list of top-level symbol names found in *content*.

    Supports Lua (``function Name``, ``local function Name``,
    ``Name = function``), Python (``def``, ``class``), and C++ (return-type
    ``name(...)`` signatures).  Used by ``_build_completed_work_snapshot`` to
    replace truncated code dumps with a compact, navigable symbol list.
    """
    symbols: list[str] = []
    ext = rel_path.rsplit(".", 1)[-1].lower() if "." in rel_path else ""

    for line in content.splitlines():
        s = line.strip()
        if not s or s.startswith("--") or s.startswith("//") or s.startswith("#"):
            continue
        if ext == "lua":
            m = re.match(
                r'^(?:local\s+)?function\s+([\w:.]+)\s*\('
                r'|^([\w.]+)\s*=\s*function\s*\(',
                s,
            )
            if m:
                symbols.append(m.group(1) or m.group(2))
        elif ext == "py":
            m = re.match(r'^(?:async\s+)?def\s+(\w+)|^class\s+(\w+)', s)
            if m:
                symbols.append(m.group(1) or m.group(2))
        else:
            # C / C++  simple return-type name(...) pattern
            m = re.match(
                r'^(?:static\s+|inline\s+|virtual\s+)?'
                r'(?:void|int|float|double|bool|char|std::\w+|\w+)\s+'
                r'([*&]?\s*\w+)\s*\(',
                s,
            )
            if m:
                symbols.append(m.group(1).strip())

    # Deduplicate preserving order
    seen: set = set()
    result: list[str] = []
    for sym in symbols:
        if sym not in seen:
            seen.add(sym)
            result.append(sym)
    return result


def _build_attraction_design_block(ctx: "PipelineContext") -> str:
    """Return a Director-prompt block containing the AttractionDesign if present.

    Injected into the base_director_input immediately before the USER REQUEST
    line so the Director's task decomposition aligns with the pre-approved design.
    Returns an empty string when no design doc is available.
    """
    design = getattr(ctx, 'attraction_design', None)
    if not design:
        return ""
    block = design.to_context_block()
    if not block.strip():
        return ""
    return (
        "\n\n---\n"
        "## 🏗 PRE-APPROVED ATTRACTION DESIGN DOCUMENT\n"
        "The following design was produced by the pre-decomposition Architect pass.\n"
        "You MUST align every task with this design  use the exact handle names, "
        "lifecycle order, and event flow listed here. Do NOT invent alternatives.\n\n"
        + block
        + "\n---\n\n"
    )


def _build_completed_work_snapshot(ctx: "PipelineContext") -> str:
    """Return a Director-prompt block describing files already written in prior iterations.

    Reads ``ctx.completed_file_snapshots`` (populated by the blueprint loop in
    ``pipeline.py`` before each ``reset_state()`` call).  Each file's content is
    truncated to a safe per-file cap, and the entire block is capped so it never
    dominates the context window.

    The block uses explicit MUST-NOT language so the Director cannot mistake
    already-implemented code for a gap it needs to fill.

    Returns an empty string when no prior iteration has completed (iteration 1).
    """
    snapshots: dict = getattr(ctx, 'completed_file_snapshots', {})
    if not snapshots:
        return ""

    # Symbol-indexed entries are ~150 chars each; raise the total cap accordingly.
    TOTAL_CAP_CHARS = 12000

    # Include the canonical path in the header so the model is reminded of
    # the single shared file it must extend (not replace) this iteration.
    _snap_scope_mode   = getattr(ctx, '_scope_mode',   '')
    _snap_scope_target = getattr(ctx, '_scope_target',  '')
    _canonical_note = ""
    if _snap_scope_mode == "NEW_ATTRACTION" and _snap_scope_target:
        import re as _re_snap
        _snap_slug = _re_snap.sub(r'[^\w]+', '_', _snap_scope_target.strip().lower()).strip('_')
        if _snap_slug:
            _canonical_path = f"attractions/{_snap_slug}/{_snap_slug}.lua"
            _canonical_note = (
                f"The ONLY valid output file for this attraction is: `{_canonical_path}`\n"
                "Every task in this iteration MUST append/extend that file  "
                "do NOT write to any other path.\n"
            )

    lines: list[str] = [
        "\n\n## ⚠️  COMPLETED WORK  FILES ALREADY ON DISK\n"
        "The following files were written and approved in PREVIOUS blueprint iterations.\n"
        "You MUST NOT re-implement, re-stub, or duplicate any function, variable, or block "
        "already present in them.\n"
        "Your tasks for THIS iteration must ONLY implement what is ABSENT from these files.\n"
        "Treat every symbol listed below as DONE AND LOCKED.\n"
        "To read the full body of any symbol, emit:\n"
        "  <invoke_kernel><action>PAGE_IN</action>"
        "<target>PATH</target><search>SYMBOL_NAME</search></invoke_kernel>\n"
        + _canonical_note,
    ]

    total_chars = 0
    for rel_path, content in snapshots.items():
        if total_chars >= TOTAL_CAP_CHARS:
            lines.append(
                f"... [{len(snapshots)} file(s) total  remaining files omitted to stay within context budget]\n"
            )
            break
        # Build a symbol TOC instead of dumping truncated code.
        symbols = _extract_symbol_toc(content, rel_path)
        char_count = len(content)
        if symbols:
            sym_line = ", ".join(f"`{s}`" for s in symbols)
            entry = (
                f"### `{rel_path}` ({char_count} chars)\n"
                f"**Implemented symbols:** {sym_line}\n"
                f"*(PAGE_IN with `<search>SYMBOL_NAME</search>` to read any body)*\n\n"
            )
        else:
            # No extractable symbols (e.g. data file)  show a short head snippet.
            snippet = content[:300] + ("\n" if len(content) > 300 else "")
            ext = rel_path.rsplit(".", 1)[-1] if "." in rel_path else ""
            lang = {"lua": "lua", "cpp": "cpp", "h": "cpp", "hpp": "cpp",
                    "py": "python", "json": "json", "md": "markdown"}.get(ext, "")
            entry = (
                f"### `{rel_path}` ({char_count} chars)\n"
                f"```{lang}\n{snippet}\n```\n\n"
            )
        lines.append(entry)
        total_chars += len(entry)

    lines.append("---\n")
    return "".join(lines)


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
        # Derive the canonical slug path from the scoped attraction name so the
        # Director is bound to the EXACT target instead of a generic example.
        # A hardcoded 'skeeball' example was previously copied verbatim by the
        # model, causing it to decompose the WRONG attraction.
        _slug = re.sub(r'[^\w]+', '_', (scope_target or '').strip().lower()).strip('_')
        _canonical_path = f"attractions/{_slug}/{_slug}.lua" if _slug else ""
        _file_mandate = (
            f"The target attraction is '{scope_target}'. ALL tasks MUST target EXACTLY "
            f"this file: `{_canonical_path}`. "
            f"Do NOT rename it, do NOT use a different slug, and do NOT switch to any "
            f"other attraction  the file path above is authoritative and overrides any "
            f"other attraction name mentioned in the request."
            if _canonical_path else
            "The new attraction file MUST be a NEW file under the `attractions/` directory."
        )
        # Import the canonical anchor tasks for the anchor listing
        try:
            from _anchors import get_all_anchor_tasks as _get_anchors
            _all_tasks = _get_anchors()
            _anchor_list = "\n".join(
                f"  -- {_marker} in {_hooks}()  -- {_title}"
                for _tid, _domain, _title, _hooks, _marker in _all_tasks
            )
            _anchor_count = len(_all_tasks)
        except ImportError:
            _anchor_list = "  (anchor markers omitted — _anchors.py unavailable)"
            _anchor_count = "9"
        parts.append(
            f"## SCOPE MANDATE — NEW ATTRACTION\n"
            f"You are decomposing tasks for a BRAND-NEW attraction file.\n"
            f"{_file_mandate}\n"
            f"You MUST NOT assign any task to an existing attraction file "
            f"({ref_names}{suffix}).\n"
            f"Those files are read-only context — never task targets.\n"
            f"DOMAIN RULE: ALL tasks for a new attraction are [Lua] ONLY. "
            f"Do NOT emit any [C++] tasks. "
            f"The engine bridge already exposes everything you need via MidwayPhysics. "
            f"A missing primitive is NEVER a reason to add a [C++] task here.\n"
            f"\nANCHOR-BASED TASK RULE:\n"
            f"Every task targets exactly ONE anchor comment marker in the file.\n"
            f"The scaffold file already contains {_anchor_count} deterministic comment markers:\n"
            f"{_anchor_list}\n"
            f"Generate exactly ONE Director task per anchor marker.\n"
            f"Each task SEARCHes for its marker line and REPLACES it with code + re-inserted anchor.\n"
            f"Do NOT collapse multiple markers into one task.\n"
            f"Do NOT emit tasks that rewrite the full file — only the anchor's scope.\n"
        )
    elif scope_mode == "MODIFY_ATTRACTION":
        target_name = _os.path.basename(scope_target)
        parts.append(
            f"## SCOPE MANDATE  MODIFY ATTRACTION\n"
            f"All tasks MUST target '{target_name}' "
            f"or shared infrastructure (booth_shared.lua, attraction_constants.lua).\n"
            f"Do NOT assign tasks to any other attraction file  they are read-only context.\n"
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
            f"\n8. SCOPE RULE  NEW ATTRACTION: You are creating a NEW attraction file. "
            f"The new file MUST be placed under the `attractions/` directory "
            f"(e.g. `attractions/skeeball/skeeball.lua`). "
            f"You MUST NOT generate any tasks that modify existing attraction files "
            f"({ref_names}{suffix}). Those files are [REFERENCE ONLY].\n"
        )
    if scope_mode == "MODIFY_ATTRACTION":
        target_name = _os.path.basename(scope_target)
        return (
            f"\n8. SCOPE RULE  MODIFY ATTRACTION: All tasks must target '{target_name}' "
            f"or shared infrastructure (booth_shared.lua, attraction_constants.lua). "
            f"Files annotated [REFERENCE ONLY] in the AST section MUST NOT be task targets.\n"
        )
    return ""


def _enrich_blueprint_tasks(ctx, blueprint_path) -> list:
    """On iteration 1, call the Director once to enrich all flat blueprint items
    with full canonical task headers (domain tags, DEPENDS_ON, Inputs/Outputs/Hooks, File).

    Returns a list of task dicts ready for ctx.tasks_list.
    The Director is instructed to emit exactly ONE canonical header per flat
    blueprint item — no splitting or merging — preserving the original task count.

    Implements a 3-attempt retry loop with count validation to guard against
    enrichment hallucinations (splitting or dropping tasks).
    """
    from _pipeline_helpers import call_ollama, DIRECTOR_SYSTEM, DIRECTOR_MODEL

    bp_text = blueprint_path.read_text(encoding="utf-8").strip()
    flat_tasks = re.findall(r"^[-\*]?\s*\[[ x]\]\s*(?:Task \d+:\s*)?(.+)$", bp_text, re.MULTILINE)
    if not flat_tasks:
        print("  [Blueprint Enricher] No flat tasks found in blueprint — nothing to enrich.")
        return []

    # ── Scope constraint for NEW_ATTRACTION ──────────────────────────────
    _enrich_scope_mode = getattr(ctx, '_scope_mode', 'GENERAL')
    _enrich_scope_target = getattr(ctx, '_scope_target', '')
    _enrich_canonical_file = ""
    _enrich_scope_rule = ""
    _enrich_ref_template = ""

    # Resolve anchor count for NEW_ATTRACTION validation
    _enrich_expected_anchor_count = 0
    if _enrich_scope_mode == "NEW_ATTRACTION" and _enrich_scope_target:
        _enrich_slug = re.sub(r'[^\w]+', '_', _enrich_scope_target.strip().lower()).strip('_')
        if _enrich_slug:
            _enrich_canonical_file = f"attractions/{_enrich_slug}/{_enrich_slug}.lua"
            # Build the anchor list dynamically from _anchors.py
            try:
                from _anchors import get_all_anchor_tasks, get_anchor_count
                _enrich_anchors = get_all_anchor_tasks()
                _enrich_expected_anchor_count = get_anchor_count()
                _anchor_lines = "\n".join(
                    f"  -- {_marker} in {_hooks}()  -- {_title}"
                    for _tid, _domain, _title, _hooks, _marker in _enrich_anchors
                )
                _enrich_anchor_rule = (
                    f"\n\nANCHOR-BASED TASK RULE:\n"
                    f"Every task targets exactly ONE anchor comment marker in the file.\n"
                    f"The scaffold has {_enrich_expected_anchor_count} anchors:\n"
                    f"{_anchor_lines}\n"
                    f"Generate exactly ONE task header per anchor marker.\n"
                    f"Expected task count: {_enrich_expected_anchor_count}.\n"
                    f"Do NOT collapse multiple markers into one task.\n"
                    f"Each task's SEARCH target is its marker line; REPLACE with code + re-inserted anchor.\n"
                )
            except ImportError:
                _enrich_anchor_rule = ""
            _enrich_scope_rule = (
                f"\n## SCOPE CONSTRAINT — NEW ATTRACTION\n"
                f"This blueprint is for a BRAND-NEW attraction file: {_enrich_canonical_file}\n"
                f"ALL tasks MUST use [Lua] domain only  — do NOT emit [C++], [CPP], or [PHYS] tasks.\n"
                f"The engine bridge already exposes all required primitives via MidwayPhysics.* and Engine.*\n"
                f"Every task header's File: field MUST be exactly: {_enrich_canonical_file}\n"
                f"Do NOT reference any other .lua file path.\n"
                f"{_enrich_anchor_rule}"
            )
            _enrich_ref_template = (
                f"\n## CANONICAL STRUCTURAL TEMPLATE (REFERENCE.lua)\n"
                f"Use attractions/_REFERENCE/REFERENCE.lua as the structural invariant guide:\n"
                f"  - OnLoadStatic() → spawn permanent geometry via SpawnStaticBox/SpawnStaticSphere\n"
                f"  - OnLoad() → create object pools, register MidwayPhysics.OnStep callback\n"
                f"  - Inside OnStep: read AttractionConstants.modifiers every frame (never cache at load time),\n"
                f"    call Engine.AwardTickets(n, label) / Engine.AwardTokens(n, label) on score events\n"
                f"  - OnUnload() → cleanup (pools are auto-reclaimed by engine)\n"
                f"  - Do NOT remove any lifecycle function or SpawnSharedBooth() call\n"
                f"  - All game-specific constants MUST be local tables — never AttractionConstants.booth\n"
                f"  - ALL engine API calls are Lua-side: MidwayPhysics.SpawnDynamicSphere, Engine.GetStreak, etc.\n"
                f"  - Every exported hook name must match REFERENCE.lua exactly: OnLoadStatic, OnLoad, OnUnload\n"
            )

    # ── 3-Attempt Retry Loop with Count Validation ───────────────────────
    enriched = []
    _expected_count = len(flat_tasks)
    for _attempt in range(1, 4):
        enrich_prompt = (
            f"You are the DEPENDENCY ANALYZER for an attraction blueprint. "
            f"You have {_expected_count} flat tasks below. "
            f"For EACH one, emit exactly ONE canonical task header "
            f"with domain tag, DEPENDS_ON, Inputs, Outputs, Hooks, and File.\n\n"
            f"CRITICAL RULES:\n"
            f"1. Do NOT split any task into subtasks — 1 item = 1 header.\n"
            f"2. Analyze real ordering: OnLoad hooks must come before OnStep tasks.\n"
            f"   Set DEPENDS_ON to the correct prior task IDs.\n"
            f"3. Assign realistic Input/Output handles.\n"
            f"4. If a task outputs a file ('File: path'), subsequent tasks that read it\n"
            f"   must list it in their DEPENDS_ON.\n"
            f"5. Output ONLY the task headers — no commentary, no summary.\n"
            f"6. You MUST output exactly {_expected_count} task headers — one per flat task.\n"
            f"   Do NOT merge multiple flat tasks into one header, and do NOT split one\n"
            f"   flat task into multiple headers. 1 flat task = 1 header.\n"
            f"{_enrich_scope_rule}"
            f"{_enrich_ref_template}"
            f"FLAT BLUEPRINT TASKS:\n" +
            "\n".join(f"  [{i+1}] {t}" for i, t in enumerate(flat_tasks))
        )

        print(f"  [Blueprint Enricher] Attempt {_attempt}/3: sending {_expected_count} flat task(s)...")
        raw_output = call_ollama(DIRECTOR_SYSTEM, enrich_prompt, "Blueprint Enricher", DIRECTOR_MODEL)

        # Parse the enriched output using the same task_regex the Director uses
        task_regex = r"### Task ([a-zA-Z0-9]+):\s*\[([^\]]+)\]\s*[-—–]\s*(.+?)(?:\s*\(DependsOn:\s*(.+?)\))?\s*$"
        enriched = []
        lines = raw_output.splitlines()
        for idx, line in enumerate(lines):
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

            # Extract Inputs/Outputs/Hooks/File from following lines
            inputs = []
            outputs = []
            hooks = []
            target_file = None
            for offset in range(1, 6):
                scan_idx = idx + offset
                if scan_idx >= len(lines):
                    break
                sl = lines[scan_idx].strip()
                if re.match(r"Inputs\s*:", sl, re.IGNORECASE):
                    raw = re.sub(r"Inputs\s*:\s*", "", sl, flags=re.IGNORECASE)
                    if raw.strip().lower() != "none":
                        inputs = [v.strip() for v in raw.split(",") if v.strip()]
                elif re.match(r"Outputs\s*:", sl, re.IGNORECASE):
                    raw = re.sub(r"Outputs\s*:\s*", "", sl, flags=re.IGNORECASE)
                    if raw.strip().lower() != "none":
                        outputs = [v.strip() for v in raw.split(",") if v.strip()]
                elif re.match(r"Hooks\s*:", sl, re.IGNORECASE):
                    raw = re.sub(r"Hooks\s*:\s*", "", sl, flags=re.IGNORECASE)
                    if raw.strip().lower() != "none":
                        hooks = [v.strip() for v in raw.split(",") if v.strip()]
                elif re.match(r"File\s*:", sl, re.IGNORECASE):
                    raw = re.sub(r"File\s*:\s*", "", sl, flags=re.IGNORECASE)
                    if raw.strip().lower() != "none":
                        target_file = raw.strip()
                if inputs and outputs and hooks and target_file is not None:
                    break

            # -- Canonical target_file backfill (monolithic collapse enabler) --
            # The enricher model frequently emits "Outputs: strongman.lua" instead
            # of a "File: attractions/<name>/<name>.lua" field.  A missing/empty
            # target_file defeats _detect_monolithic_lua_candidate() in mesh_tasks,
            # which then runs N same-file tasks as N full-file generations that
            # clobber each other and get flagged as N separate static-guard
            # artifacts.  Backfill a canonical path so every task declares the
            # same .lua target.
            if not target_file:
                if _enrich_canonical_file:
                    target_file = _enrich_canonical_file
                else:
                    _lua_candidates = [
                        o.strip() for o in (outputs + inputs)
                        if o.strip().lower().endswith(".lua")
                    ]
                    if _lua_candidates:
                        target_file = _lua_candidates[0]

            # -- Fix D: Anchor marker propagation (SEMANTIC matching) ───────────
            # Previous approach used numeric ID matching (task "3" → anchor "3"),
            # but the Blueprint Generator's task ordering rarely matches the
            # canonical anchor ordering.  This caused tasks about "spawning booth
            # geometry" to be anchored to TASK_2_INSERT_HOOK (shared constants)
            # instead of TASK_3_INSERT_HOOK (permanent geometry).
            #
            # Fix: compute keyword overlap between the task title and each
            # anchor's descriptive title.  The best-matching anchor is assigned,
            # with a fallback to numeric ID if no anchor exceeds the threshold.
            _anchor_marker: str | None = None
            if _enrich_scope_mode == "NEW_ATTRACTION":
                try:
                    from _anchors import get_all_anchor_tasks as _get_tasks
                    _all_anchor_tasks = _get_tasks()

                    # Build a list of semantic keywords for each anchor.
                    # These are extracted from the anchor's title text
                    # (e.g. "module-level state (balls/objects table, globals)")
                    # which was written by a human to describe what the anchor is for.
                    _anchor_keywords: list[tuple[str, set[str], str]] = []
                    for _atid, _adom, _atitle, _ahooks, _amarker in _all_anchor_tasks:
                        # Tokenize the anchor title into meaningful keywords
                        _tokens = set(
                            w.lower().rstrip(',)\u201d;:.')
                            for w in _atitle.replace("/", " ").replace("(", " ").replace(")", " ").split()
                            if len(w) >= 4 and w.lower() not in {
                                "with", "that", "this", "from", "your", "have",
                                "will", "using", "need", "should", "only", "into",
                                "also", "more", "every", "each", "call", "per",
                            }
                        )
                        _anchor_keywords.append((_atid, _tokens, _amarker))

                    # Tokenize the current task title
                    _task_tokens = set(
                        w.lower().rstrip(',)\u201d;:.')
                        for w in title.replace("/", " ").replace("(", " ").replace(")", " ").split()
                        if len(w) >= 4 and w.lower() not in {
                            "with", "that", "this", "from", "your", "have",
                            "will", "using", "need", "should", "only", "into",
                            "also", "more", "every", "each", "call", "per",
                        }
                    )

                    # Score each anchor by Jaccard similarity + bonus for exact domain match.
                    # Exact match bonus: if the task mentions a lifecycle hook name
                    # and the anchor targets the same hook, give +0.3.
                    _task_hook_match = (
                        "onloadstatic" if "onloadstatic" in title.lower() else
                        "onload" if "onload" in title.lower() else
                        "onstep" if "onstep" in title.lower() else
                        "onunload" if "onunload" in title.lower() else
                        ""
                    )

                    # -- Negative scoring: prevent generic physics/spawn terms from
                    # matching semantically incompatible lifecycle hooks.
                    # For example, a task about "ball launching" (physics/spawn concern)
                    # should NOT match TASK_6 (input handling) even if both share
                    # the word "launch" or "aim".  We define anchor-to-category mappings
                    # and penalize cross-category matches.
                    _anchor_categories = {
                        # Anchor IDs mapped to their primary concern category
                        "1": "state",      # module-level state
                        "2": "constants",  # shared constants table
                        "3": "geometry",   # permanent geometry / SpawnSharedBooth
                        "4": "pool",       # object pool creation
                        "5": "state",      # round state init
                        "6": "input",      # input handling / aiming
                        "7": "lifecycle",  # OnStep registration
                        "8": "modifier",   # modifier read
                        "9": "economy",    # scoring / award tickets
                        "10": "modifier",  # advanced modifier read
                        "11": "economy",   # advanced economy hooks
                    }
                    _task_categories_hint = set()
                    _title_lower_check = title.lower()
                    if any(w in _title_lower_check for w in ["geometry", "booth", "spawnsharedbooth", "spawnstatic"]):
                        _task_categories_hint.add("geometry")
                    if any(w in _title_lower_check for w in ["pool", "createpool", "objectpool"]):
                        _task_categories_hint.add("pool")
                    if any(w in _title_lower_check for w in ["modifier", "attractionconstants", "engine_mod_"]):
                        _task_categories_hint.add("modifier")
                    if any(w in _title_lower_check for w in ["score", "award", "ticket", "token", "economy"]):
                        _task_categories_hint.add("economy")
                    if any(w in _title_lower_check for w in ["input", "aim", "aiming", "launch", "trigger"]):
                        _task_categories_hint.add("input")
                    if any(w in _title_lower_check for w in ["state", "counter", "round", "track"]):
                        _task_categories_hint.add("state")
                    if any(w in _title_lower_check for w in ["cleanup", "diagnostic", "unload"]):
                        _task_categories_hint.add("cleanup")

                    _best_score = 0.0
                    _best_anchor: str | None = None
                    for _atid, _atokens, _amarker in _anchor_keywords:
                        if not _task_tokens or not _atokens:
                            continue
                        _intersection = _task_tokens & _atokens
                        _union = _task_tokens | _atokens
                        _jaccard = len(_intersection) / max(len(_union), 1)
                        # Domain bonus
                        _domain_bonus = 0.3 if _task_hook_match and (
                            (_task_hook_match == "onload" and _atid in {"4", "5", "6"}) or
                            (_task_hook_match == "onloadstatic" and _atid == "3") or
                            (_task_hook_match == "onstep" and _atid in {"7", "8", "10", "11"}) or
                            (_task_hook_match == "onunload" and _atid == "9")
                        ) else 0.0
                        # Anchor-specific semantic bonuses
                        _bonus = 0.0
                        _title_lower = title.lower()
                        if ("pool" in _title_lower or "createpool" in _title_lower) and "pool" in " ".join(_atokens):
                            _bonus = 0.4
                        elif ("spawnsharedbooth" in _title_lower or "booth" in _title_lower or "geometry" in _title_lower) and "geometry" in " ".join(_atokens):
                            _bonus = 0.4
                        elif ("modifier" in _title_lower or "attractionconstants" in _title_lower) and "modifier" in " ".join(_atokens):
                            _bonus = 0.4
                        elif ("score" in _title_lower or "award" in _title_lower or "ticket" in _title_lower) and "score" in " ".join(_atokens):
                            _bonus = 0.4
                        elif ("cleanup" in _title_lower or "diagnostic" in _title_lower or "unload" in _title_lower) and "cleanup" in " ".join(_atokens):
                            _bonus = 0.4
                        elif ("input" in _title_lower or "aim" in _title_lower or "launch" in _title_lower) and "input" in " ".join(_atokens):
                            _bonus = 0.4

                        # -- Negative penalty for category mismatch --
                        # If the task has a clear category hint and the anchor
                        # belongs to a different category, apply a large penalty.
                        # This prevents "ball launching" (physics/spawn) from
                        # matching TASK_6 (input handling) just because both
                        # contain common generic keywords.
                        _category_penalty = 0.0
                        _anchor_cat = _anchor_categories.get(_atid, "")
                        if _task_categories_hint and _anchor_cat:
                            # If none of the task's hinted categories overlap
                            # with the anchor's category, apply a penalty.
                            _is_compat = any(
                                _cat_hint == _anchor_cat
                                for _cat_hint in _task_categories_hint
                            )
                            if not _is_compat:
                                _category_penalty = -0.5

                        _score = _jaccard + _domain_bonus + _bonus + _category_penalty
                        if _score > _best_score:
                            _best_score = _score
                            _best_anchor = _amarker


                    # Use semantic match if score exceeds threshold; fall back to numeric ID
                    if _best_score >= 0.3 and _best_anchor:
                        _anchor_marker = _best_anchor
                    else:
                        # Fallback: numeric ID matching  (original behavior)
                        for _atid, _adom, _atitle, _ahooks, _amarker in _all_anchor_tasks:
                            if _atid == task_id:
                                _anchor_marker = _amarker
                                break

                except ImportError:
                    pass

            enriched.append({
                "id": task_id,
                "domain": domain,
                "title": title + (f" - {target_file}" if target_file else ""),
                "depends_on": depends_on,
                "inputs": inputs,
                "outputs": outputs,
                "hooks": hooks,
                "target_file": target_file or "",
                "math_heavy": bool(re.search(r"\[MATH[_ ]?HEAVY\]", line, re.IGNORECASE)),
                "anchor_marker": _anchor_marker,
            })

        # ── Count Validation ────────────────────────────────────────────────
        _actual_count = len(enriched)
        if _actual_count == _expected_count:
            print(f"  [Blueprint Enricher] ✓ Attempt {_attempt}: parsed {_actual_count} tasks (matches expected {_expected_count}).")
            break
        else:
            print(f"  [Blueprint Enricher] ⚠ Attempt {_attempt}: parsed {_actual_count} tasks but expected {_expected_count}. Retrying...")
            if _attempt < 3:
                _enrich_scope_rule += (
                    f"\n## PREVIOUS ATTEMPT FAILURE\n"
                    f"Your previous output had {_actual_count} task headers, "
                    f"but there are exactly {_expected_count} flat tasks.\n"
                    f"You MUST output exactly {_expected_count} task headers — one per flat task.\n"
                    f"Do NOT split any task into multiple headers.\n"
                    f"Do NOT merge multiple tasks into one header.\n"
                )
            enriched = []  # reset for retry

    # If all 3 attempts failed, log warning and use whatever we got from the last attempt
    if not enriched:
        print(f"  [Blueprint Enricher] ⚠ All 3 attempts failed to match expected count. Using last attempt's output ({len(enriched)} tasks).")

    print(f"  [Blueprint Enricher] Parsed {len(enriched)} enriched task(s) with dependency metadata.")
    return enriched


