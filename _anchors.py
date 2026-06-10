"""
_anchors.py — Canonical attraction scaffold anchors (single source of truth)
=======================================================================

All scaffold anchor definitions live here. Both the blueprint scaffold writer
and the Director monolithic path import from this one constant so they never
drift out of sync.

Each anchor is (insert_bucket, location_key, anchor_text) where:
  - insert_bucket : 'module' | 'onloadstatic' | 'onload' | 'onstep' | 'onunload'
  - location_key  : additional disambiguation (e.g. "pre-registration" for OnLoad)
  - anchor_text   : the exact Lua comment line the agents will SEARCH for
"""

from __future__ import annotations

# 11 canonical anchors (TASK_1..TASK_11) — raised from 9 to cover modifier +
# economy in dedicated tasks per GDD future-attractions pattern.
CANONICAL_ANCHORS: list[tuple[str, str, str]] = [
    # ── Module root ──────────────────────────────────────────────────────────
    ("module", "",
     "-- [TASK_1_INSERT_HOOK] -- module-level state (balls/objects table, globals)"),
    ("module", "",
     "-- [TASK_2_INSERT_HOOK] -- shared constants table (geometry, physics, gameplay values)"),

    # ── OnLoadStatic ─────────────────────────────────────────────────────────
    ("onloadstatic", "",
     "-- [TASK_3_INSERT_HOOK] -- permanent geometry: SpawnSharedBooth() + static physics bodies"),

    # ── OnLoad  (pre-Step registration) ─────────────────────────────────────
    ("onload", "pre-registration",
     "-- [TASK_4_INSERT_HOOK] -- object pool creation (MidwayPhysics.CreatePool with shape/mass/restitution)"),
    ("onload", "pre-registration",
     "-- [TASK_5_INSERT_HOOK] -- round state init (ball counters, timers, round variables)"),
    ("onload", "pre-registration",
     "-- [TASK_6_INSERT_HOOK] -- input handling / aiming mechanism setup"),

    # ── OnStep (inside the callback) ────────────────────────────────────────
    ("onstep", "",
     "-- [TASK_7_INSERT_HOOK] -- modifier read: AttractionConstants.modifiers every frame, apply ENGINE_MOD_HEAT/LUCK/SLEIGHT_OF_HAND"),
    ("onstep", "",
     "-- [TASK_8_INSERT_HOOK] -- gameplay tick & scoring: Engine.AwardTickets(n, label) with Engine.GetStreak() multiplier"),
    ("onstep", "",
     "-- [TASK_10_INSERT_HOOK] -- advanced modifier read: AttractionConstants.modifiers every OnStep frame, compute dynamic heat/luck/sleight-of-hand effects per game event"),
    ("onstep", "",
     "-- [TASK_11_INSERT_HOOK] -- advanced economy hooks: Engine.AwardTickets or Engine.AwardTokens with streak multiplier on each score event, wire modifier scalars into payout"),

    # ── OnUnload ─────────────────────────────────────────────────────────────
    ("onunload", "",
     "-- [TASK_9_INSERT_HOOK] -- cleanup & diagnostics (DestroyDynamicBody, print stats)"),
]

# Pre-built lookup for fast access
_ANCHOR_BY_BUCKET: dict[str, list[tuple[str, str, str]]] = {}
for _bucket, _loc, _text in CANONICAL_ANCHORS:
    _ANCHOR_BY_BUCKET.setdefault(_bucket, []).append((_loc, _text))


def get_anchors_for_bucket(bucket: str) -> list[tuple[str, str, str]]:
    """Return (location, anchor_text) pairs for the given bucket.

    Bucket values: 'module', 'onloadstatic', 'onload', 'onstep', 'onunload'.
    """
    return _ANCHOR_BY_BUCKET.get(bucket, [])


def get_all_anchor_tasks() -> list[tuple[str, str, str, str, str]]:
    """Return (task_id, domain, title, hooks, marker) for the monolithic NARROW path.

    This is the canonical task decomposition matching CANONICAL_ANCHORS above.
    Task IDs are assigned sequentially (1..11) matching the TASK_N markers.
    """
    import re as _re_anchor

    _task_id = 1
    results: list[tuple[str, str, str, str, str]] = []

    for _bucket, _loc, _marker in CANONICAL_ANCHORS:
        _id_str = str(_task_id)
        _hook = "OnLoadStatic" if _bucket == "onloadstatic" else (
                "OnStep" if _bucket == "onstep" else (
                "OnUnload" if _bucket == "onunload" else (
                "OnLoad" if _bucket == "onload" else "")))
        _short = _marker.split("--")[1].strip() if "--" in _marker else _marker
        results.append((_id_str, "Lua", _short, _hook, _marker.strip()))
        _task_id += 1

    return results


def build_lua_skeleton(anchors_list: list[tuple[str, str, str]] | None = None) -> str:
    """Build a complete Lua scaffold file with all anchor markers in the correct
    lifecycle sections.

    Args:
        anchors_list: Override the anchor list (e.g. from blueprint phase).
                      Defaults to CANONICAL_ANCHORS.

    Returns:
        Complete Lua file text with anchor markers placed at the right locations.
    """
    anchors = anchors_list or CANONICAL_ANCHORS

    module_root: list[str] = []
    onload_static: list[str] = []
    onload_pre: list[str] = []
    onstep: list[str] = []
    onunload: list[str] = []

    for _bucket, _loc, _text in anchors:
        if _bucket == "module":
            module_root.append(_text)
        elif _bucket == "onloadstatic":
            onload_static.append(f"    {_text}")
        elif _bucket == "onload":
            onload_pre.append(f"    {_text}")
        elif _bucket == "onstep":
            onstep.append(f"    {_text}")
        elif _bucket == "onunload":
            onunload.append(f"    {_text}")

    return (
        "local balls = {}\n"
        + "\n".join(module_root)
        + ("\n" if module_root else "")
        + "\n"
        + "-- ALL static geometry MUST go here\n"
        + "function OnLoadStatic()\n"
        + "\n".join(onload_static) + "\n"
        + "end\n"
        + "\n"
        + "-- ALL game logic and setup MUST go here\n"
        + "function OnLoad()\n"
        + "\n".join(onload_pre) + "\n"
        + "    MidwayPhysics.OnStep(function(dt)\n"
        + "        local MOD = AttractionConstants.modifiers\n"
        + "\n".join(onstep) + "\n"
        + "    end)\n"
        + "end\n"
        + "\n"
        + "function OnUnload()\n"
        + "\n".join(onunload) + "\n"
        + "end\n"
    )


def verify_and_reinject_anchor(file_content: str, target_anchor: str,
                                anchors_list: list[tuple[str, str, str]] | None = None) -> str:
    """Check if *target_anchor* exists in *file_content*.  If not, re-inject it
    at the correct lifecycle section from the canonical anchor list.

    This is a self-healing mechanism for the SEARCH/REPLACE chain: when a
    previous task consumed an anchor marker without re-inserting it, this
    function locates the nearest remaining anchor in the same bucket and
    inserts the missing marker alongside it.

    Returns the file content with the missing anchor re-injected (or unchanged
    if the anchor was already present).
    """
    if target_anchor in file_content:
        return file_content  # already present, nothing to do

    anchors = anchors_list or CANONICAL_ANCHORS

    # Find the missing anchor's bucket and its insertion order
    missing_bucket = None
    missing_idx = -1
    for _i, (_bucket, _loc, _text) in enumerate(anchors):
        if _text.strip() == target_anchor.strip():
            missing_bucket = _bucket
            missing_idx = _i
            break

    if missing_bucket is None:
        # Unknown anchor -- can't auto-reinject, return as-is
        return file_content

    # Find a sibling anchor in the same bucket that IS still present
    # and insert the missing one nearby.
    if missing_bucket == "module":
        # Insert after "local balls = {}" or at top
        if "local balls = {" in file_content:
            idx = file_content.index("local balls = {")
            eol = file_content.find("\n", idx)
            inject_point = eol + 1
            return file_content[:inject_point] + f"{target_anchor}\n" + file_content[inject_point:]
        else:
            return f"{target_anchor}\n{file_content}"

    def _find_insertion_after_sibling(bucket: str, fallback_hook: str) -> str | None:
        """Find the last remaining anchor in the same bucket and inject after it.
        If none remain, inject right before the hook's 'end' keyword."""
        for _j in range(missing_idx - 1, -1, -1):
            _sib_bucket = anchors[_j][0]
            _sib_text = anchors[_j][2].strip()
            if _sib_bucket == bucket and _sib_text in file_content:
                # Found a surviving sibling -- inject after it
                _inject_after = file_content.index(_sib_text)
                _eol = file_content.find("\n", _inject_after)
                _rejoin = (_eol + 1) if _eol != -1 else len(file_content)
                return file_content[:_rejoin] + f"    {target_anchor}\n" + file_content[_rejoin:]

        # No surviving sibling -- find the hook's 'end' and inject before it
        if fallback_hook:
            _hook_end = file_content.rfind(f"\nend\n", 0, file_content.find(f"\nend\n\nfunction OnUnload"))
            if _hook_end == -1:
                _hook_end = file_content.rfind("\nend")
            if _hook_end != -1:
                _line_start = file_content.rfind("\n", 0, _hook_end) + 1
                return file_content[:_line_start] + f"    {target_anchor}\n" + file_content[_line_start:]

        return None

    hook_map = {
        "onloadstatic": "OnLoadStatic",
        "onload": "OnLoad",
        "onstep": "OnStep",
        "onunload": "OnUnload",
    }
    fallback = hook_map.get(missing_bucket, "")
    result = _find_insertion_after_sibling(missing_bucket, fallback)
    if result is not None:
        return result

    return file_content


def get_anchor_count() -> int:
    """Return the total number of canonical anchors.

    Used by blueprint validation to reject undersized plans
    that would leave some lifecycle markers unfilled.
    """
    return len(CANONICAL_ANCHORS)
