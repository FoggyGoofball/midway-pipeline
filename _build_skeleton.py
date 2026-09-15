"""
_build_skeleton.py — Phase A: Deterministic Skeleton Builder
=============================================================
Generates a minimal valid .lua file with guaranteed-correct structure.
Zero LLM involvement — pure Python string templates.

Output invariants (exactly one of each, every time):
  1. Slot identity: local SLOT_ID = BOOTH_SLOT_ID or -1
  2. Constants table: local CONST = {}  (placeholder)
  3. OnLoadStatic → SpawnSharedBooth
  4. OnLoad → OnStep registration (with MOD inside callback)
  5. OnUnload

Public API:
    build_skeleton(attraction_name="") -> str
    ensure_skeleton(target_path, attraction_name="") -> bool   # writes if missing
    skeleton_exists(target_path) -> bool
    inject_skeleton(target_path, attraction_name="") -> Path   # always writes
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

# ─── Canonical skeleton template ─────────────────────────────────────────────
# This is the 5-invariant skeleton. Every generated attraction starts from
# exactly this structure. The LLM fills in game-specific logic by replacing
# anchor markers ("-- [TASK_N_INSERT_HOOK]"), not by rewriting the file.

SKELETON_TEMPLATE: str = """\
-- ─── INVARIANT 1: Slot identity ────────────────────────
local SLOT_ID = BOOTH_SLOT_ID or -1

-- ─── INVARIANT 2: Constants table (placeholder values) ─
local CONST = {}  -- FILL: game-specific dimensions/physics

-- ─── MODULE-LEVEL STATE (shared across all lifecycle functions) ──
-- DECLARE all game state variables here so they are accessible from
-- OnLoadStatic, OnLoad, OnStep, and OnUnload.
-- local balls = {}         -- table of ball handles
-- local remainingBalls = 6 -- ball counter
-- local currentScore = 0   -- score accumulator
-- local aimAngle = 0.0     -- player aim direction
-- local powerLevel = 1.0   -- launch power
-- [TASK_1_INSERT_HOOK] -- module-level state (balls/objects table, globals)

-- ─── INVARIANT 3: OnLoadStatic → SpawnSharedBooth ──────
-- ALL static geometry MUST go here
function OnLoadStatic()
    SpawnSharedBooth()
    -- [TASK_3_INSERT_HOOK] -- permanent geometry: SpawnSharedBooth() + static physics bodies
end

-- ─── INVARIANT 4: OnLoad → OnStep registration ─────────
-- ALL game logic and setup MUST go here
function OnLoad()
    -- [TASK_2_INSERT_HOOK] -- shared constants table (geometry, physics, gameplay values)
    -- [TASK_4_INSERT_HOOK] -- object pool creation (MidwayPhysics.CreatePool with shape/mass/restitution)
    -- [TASK_5_INSERT_HOOK] -- round state init (ball counters, timers, round variables)
    -- [TASK_6_INSERT_HOOK] -- input handling / aiming mechanism setup
    MidwayPhysics.OnStep(function(dt)
        local MOD = AttractionConstants.modifiers  -- INSIDE callback
    -- [TASK_7_INSERT_HOOK] -- modifier read: AttractionConstants.modifiers every frame, apply ENGINE_MOD_HEAT/LUCK/SLEIGHT_OF_HAND
    -- [TASK_8_INSERT_HOOK] -- gameplay tick & scoring: Engine.AwardTickets(n, label) with Engine.GetStreak() multiplier
    -- [TASK_9_INSERT_HOOK] -- advanced modifier read: AttractionConstants.modifiers every OnStep frame, compute dynamic heat/luck/sleight-of-hand effects per game event
    -- [TASK_10_INSERT_HOOK] -- advanced economy hooks: Engine.AwardTickets or Engine.AwardTokens with streak multiplier on each score event, wire modifier scalars into payout
    end)
end

-- ─── INVARIANT 5: OnUnload ─────────────────────────────
function OnUnload()
    -- [TASK_11_INSERT_HOOK] -- cleanup & diagnostics (DestroyDynamicBody, print stats)
end
"""


# ─── Anchor markers for validation ──────────────────────────────────────────

_ANCHOR_MARKERS: frozenset[str] = frozenset({
    "-- [TASK_1_INSERT_HOOK]",
    "-- [TASK_2_INSERT_HOOK]",
    "-- [TASK_3_INSERT_HOOK]",
    "-- [TASK_4_INSERT_HOOK]",
    "-- [TASK_5_INSERT_HOOK]",
    "-- [TASK_6_INSERT_HOOK]",
    "-- [TASK_7_INSERT_HOOK]",
    "-- [TASK_8_INSERT_HOOK]",
    "-- [TASK_9_INSERT_HOOK]",
    "-- [TASK_10_INSERT_HOOK]",
    "-- [TASK_11_INSERT_HOOK]",
})

# ─── Invariant validation patterns ──────────────────────────────────────────

_INVARIANT_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("SLOT_ID assignment", re.compile(
        r"local\s+SLOT_ID\s*=\s*BOOTH_SLOT_ID\s+or\s+-?1"
    )),
    ("OnLoadStatic function", re.compile(
        r"function\s+OnLoadStatic\s*\("
    )),
    ("SpawnSharedBooth call", re.compile(
        r"SpawnSharedBooth\s*\("
    )),
    ("OnLoad function", re.compile(
        r"function\s+OnLoad\s*\("
    )),
    ("OnStep registration", re.compile(
        r"MidwayPhysics\.OnStep\s*\("
    )),
    ("MOD inside OnStep callback", re.compile(
        r"local\s+MOD\s*=\s*AttractionConstants\.modifiers"
    )),
    ("OnUnload function", re.compile(
        r"function\s+OnUnload\s*\("
    )),
]


# ─── Public API ──────────────────────────────────────────────────────────────

def build_skeleton(attraction_name: str = "") -> str:
    """Return the canonical 5-invariant skeleton as a string.

    Args:
        attraction_name: Optional name to inject into the header comment.

    Returns:
        Complete Lua source text with all 5 invariants and 9 anchor markers.
    """
    if attraction_name:
        header = (
            f"-- {attraction_name}.lua  —  Auto-generated by _build_skeleton.py\n"
            f"-- EDIT: Replace placeholders with game-specific logic.\n"
            f"-- DO NOT remove lifecycle functions (OnLoadStatic/OnLoad/OnUnload)\n"
            f"-- DO NOT remove SLOT_ID or SpawnSharedBooth()\n"
            f"-- DO NOT cache AttractionConstants.modifiers outside OnStep\n"
            f"\n"
        )
        return header + SKELETON_TEMPLATE
    return SKELETON_TEMPLATE


def skeleton_exists(target_path: Path) -> bool:
    """Check if a file at *target_path* has the canonical skeleton structure.

    Verifies all 5 invariants are present. Does NOT verify anchor markers
    (those may have been consumed by tasks).

    Args:
        target_path: Path to the .lua file to check.

    Returns:
        True if all 5 invariants are present.
    """
    if not target_path.is_file():
        return False

    content = target_path.read_text(encoding="utf-8", errors="replace")
    return all(pattern.search(content) for _, pattern in _INVARIANT_PATTERNS)


def ensure_skeleton(target_path: Path, attraction_name: str = "") -> bool:
    """Write the skeleton to *target_path* ONLY if it doesn't exist or lacks
    the canonical structure.

    Args:
        target_path: Path to write the skeleton to.
        attraction_name: Optional attraction name for the header comment.

    Returns:
        True if the skeleton was written (file was missing or corrupted).
        False if the file already has a valid skeleton structure.
    """
    if skeleton_exists(target_path):
        return False

    content = build_skeleton(attraction_name)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(content, encoding="utf-8")
    print(f"  [Skeleton Builder] ✅ Wrote canonical skeleton to {target_path} ({len(content)} chars)")
    return True


def inject_skeleton(target_path: Path, attraction_name: str = "") -> Path:
    """ALWAYS write the skeleton — overwrite any existing content.

    Unlike ensure_skeleton(), this always writes. Use when you want to
    guarantee a fresh skeleton regardless of current file state.

    Args:
        target_path: Path to write the skeleton to.
        attraction_name: Optional attraction name for the header comment.

    Returns:
        The Path that was written to (same as target_path).
    """
    content = build_skeleton(attraction_name)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(content, encoding="utf-8")
    return target_path


# -- Extra-anchor support (1-task ↔ 1-anchor invariant) -----------------------

_BUCKET_REF_MARKERS: dict[str, str] = {
    "module": "-- [TASK_1_INSERT_HOOK]",
    "onloadstatic": "-- [TASK_3_INSERT_HOOK]",
    "onload": "-- [TASK_6_INSERT_HOOK]",
    "onstep": "-- [TASK_10_INSERT_HOOK]",
    "onunload": "-- [TASK_11_INSERT_HOOK]",
}


def _insert_anchor_into_template(content: str, bucket: str, anchor_text: str) -> str:
    """Insert *anchor_text* into the skeleton at the correct lifecycle bucket,
    immediately after the bucket's canonical reference marker (preserving its
    indentation).  Falls back to appending at end of file when the reference
    marker is absent."""
    _ref = _BUCKET_REF_MARKERS.get(bucket)
    if _ref and _ref in content:
        _idx = content.index(_ref)
        _line_start = content.rfind("\n", 0, _idx) + 1
        _indent = content[_line_start:_idx]
        _eol = content.find("\n", _idx)
        if _eol == -1:
            _eol = len(content)
        _insert_line = _indent + anchor_text + "\n"
        return content[:_eol + 1] + _insert_line + content[_eol + 1:]
    return content.rstrip() + "\n" + anchor_text + "\n"


def build_skeleton_with_anchors(attraction_name: str = "",
                                 extra_anchors: list | None = None) -> str:
    """Build the canonical skeleton and insert any extra anchors at their
    lifecycle buckets so every planned task has a stable SEARCH target."""
    content = build_skeleton(attraction_name)
    for (_bucket, _loc, _text) in (extra_anchors or []):
        content = _insert_anchor_into_template(content, _bucket, _text)
    return content


def write_skeleton_content(target_path: Path, content: str) -> Path:
    """Write arbitrary skeleton content (canonical + extra anchors) to disk."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(content, encoding="utf-8")
    return target_path


def is_skeleton_content(content: str) -> bool:
    """Check if a string of Lua source matches the canonical skeleton.

    Useful for testing and for verifying LLM output hasn't destroyed
    the invariant structure.

    Args:
        content: Lua source text to check.

    Returns:
        True if all 5 invariants are present.
    """
    return all(pattern.search(content) for _, pattern in _INVARIANT_PATTERNS)


def validate_skeleton(content: str) -> list[str]:
    """Return a list of missing invariants (empty list means valid).

    Args:
        content: Lua source text to validate.

    Returns:
        List of invariant names that are missing from the content.
    """
    missing: list[str] = []
    for name, pattern in _INVARIANT_PATTERNS:
        if not pattern.search(content):
            missing.append(name)
    return missing


# ─── Deterministic Module-State Injection ───────────────────────────────────
# The Architect design doc now carries a ``module_state_variables`` array plus
# a ``handles`` array.  This section renders those into Lua ``local``
# declarations and injects them at the absolute module root — BEFORE any
# lifecycle function — so the execution mesh never has to invent or scope a
# shared variable.  This is the deterministic handoff that removes variable
# scoping from the LLM entirely.

_LUA_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Lua 5.4 reserved words — a state var must never collide with these.
_LUA_KEYWORDS = frozenset({
    "and", "break", "do", "else", "elseif", "end", "false", "for", "function",
    "goto", "if", "in", "local", "nil", "not", "or", "repeat", "return",
    "then", "true", "until", "while",
})


def _lua_identifier(name: object) -> str:
    """Return a valid Lua identifier for *name*, sanitizing invalid characters.

    Reserved words are prefixed with ``_`` so an Architect that emits ``end``
    or ``local`` as a state name cannot produce a syntax error.
    """
    raw = str(name or "").strip()
    if not raw:
        return ""
    if not _LUA_IDENT_RE.match(raw):
        cleaned = re.sub(r"[^A-Za-z0-9_]", "_", raw)
        if not cleaned or cleaned[0].isdigit():
            cleaned = "var_" + cleaned
        raw = cleaned
    if raw in _LUA_KEYWORDS:
        return "_" + raw
    return raw


def _coerce_lua_initial(lua_type: str, initial_value: object) -> str:
    """Coerce a JSON initial value into a valid Lua literal string."""
    t = (lua_type or "").strip().lower()

    def _default_for(_t: str) -> str:
        if _t in ("number", "float", "integer", "int", "double"):
            return "0"
        if _t in ("boolean", "bool"):
            return "false"
        if _t == "table":
            return "{}"
        if _t == "string":
            return '""'
        return "nil"

    if initial_value is None:
        return _default_for(t)
    # bool first: isinstance(True, int) is True, so check booleans up front.
    if isinstance(initial_value, bool):
        return "true" if initial_value else "false"
    if isinstance(initial_value, (int, float)):
        if t in ("boolean", "bool"):
            return "true" if initial_value else "false"
        if t == "string":
            return '"' + str(initial_value) + '"'
        return repr(initial_value)
    if isinstance(initial_value, (dict, list)):
        return "{}"
    if isinstance(initial_value, str):
        s = initial_value.strip()
        if s == "":
            return _default_for(t)
        if t == "string":
            return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
        if s in ("true", "false", "nil"):
            return s
        if s == "{}" or s.startswith("{"):
            return s
        try:
            float(s)
            return s
        except ValueError:
            return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return "nil"


def _state_comment_suffix(description: str, owner_task: str) -> str:
    parts = []
    if owner_task:
        parts.append(f"task {owner_task}")
    if description:
        parts.append(str(description).replace("\n", " ").strip())
    if not parts:
        return ""
    text = " ".join(parts).strip()
    return f"  -- {text}" if text else ""


def render_module_state_block(handles: list | None = None,
                              state_variables: list | None = None,
                              pool_keys: list | None = None) -> str:
    """Render module-level ``local`` declarations for handles + primitive state.

    Accepts either dicts or objects exposing ``.name`` / ``.lua_type`` /
    ``.initial_value`` / ``.description`` / ``.owner_task`` attributes.
    For every pooled entity key a dedicated POOL NAME string constant
    (``<key>_pool = "<key>"``) is declared alongside the active handle so
    tasks never conflate the pool name with the live instance handle.

    Returns an empty string when there is nothing to declare.
    """
    lines: list[str] = []
    seen: set[str] = set()

    def _emit(name: str, decl: str) -> None:
        if name and name not in seen:
            seen.add(name)
            lines.append(decl)

    for h in handles or []:
        if isinstance(h, str):
            name = _lua_identifier(h)
        elif isinstance(h, dict):
            name = _lua_identifier(h.get("name"))
        else:
            name = _lua_identifier(getattr(h, "name", None))
        if not name:
            continue
        _emit(name, f"local {name} = nil  -- physics handle (spawned in OnLoadStatic/OnLoad)")

    for pk in pool_keys or []:
        pk_name = _lua_identifier(str(pk))
        if not pk_name:
            continue
        # Pooled entities get a string constant holding the pool NAME so tasks
        # can call PoolAcquire(puck_dynamic_pool, ...) without inventing their
        # own pool name strings. The active instance handle remains the bare
        # handle name (e.g. `puck_dynamic`), NOT `active_puck_dynamic`.
        _emit(f"{pk_name}_pool",
              f'local {pk_name}_pool = "{pk_name}"  -- pool name string (PoolAcquire/PoolReturn)')

    for sv in state_variables or []:
        if isinstance(sv, dict):
            name = _lua_identifier(sv.get("name", ""))
            lua_type = sv.get("lua_type", "number")
            initial = sv.get("initial_value")
            desc = sv.get("description", "")
            owner = sv.get("owner_task", "")
        else:
            name = _lua_identifier(getattr(sv, "name", ""))
            lua_type = getattr(sv, "lua_type", "number")
            initial = getattr(sv, "initial_value", None)
            desc = getattr(sv, "description", "")
            owner = getattr(sv, "owner_task", "")
        if not name:
            continue
        value = _coerce_lua_initial(lua_type, initial)
        _emit(name, f"local {name} = {value}{_state_comment_suffix(desc, owner)}")

    if not lines:
        return ""
    return (
        "-- ─── DETERMINISTIC MODULE STATE (injected by Skeleton Builder) ──\n"
        + "\n".join(lines)
        + "\n"
    )


def _insert_state_block(content: str, block: str) -> str:
    """Insert *block* at the module root of *content*."""
    # Preferred: immediately above the module-state anchor so declarations sit
    # at module root, right where the skeleton's MODULE-LEVEL STATE header points.
    marker = "-- [TASK_1_INSERT_HOOK]"
    idx = content.find(marker)
    if idx != -1:
        line_start = content.rfind("\n", 0, idx) + 1
        return content[:line_start] + block + content[line_start:]

    # Fallback 1: immediately after the constants table declaration.
    marker = "local CONST = {}"
    idx = content.find(marker)
    if idx != -1:
        line_end = content.find("\n", idx)
        if line_end == -1:
            line_end = len(content)
        return content[:line_end + 1] + "\n" + block + content[line_end + 1:]

    # Fallback 2: prepend at the very top.
    return block + "\n" + content


def inject_module_state(target_path: Path,
                        handles: list | None = None,
                        state_variables: list | None = None,
                        pool_keys: list | None = None) -> str:
    """Deterministically inject module-level state declarations into *target_path*.

    Reads the file, renders ``local`` declarations for the given handles,
    primitive state variables, and pooled-entity pool-name string constants,
    inserts them at the module root, writes the file back, and returns the
    final content (so callers can refresh their revert-on-regression baselines).

    Returns the unchanged content (without writing) when there is nothing to
    inject or the file is missing.
    """
    if not target_path.is_file():
        return ""
    block = render_module_state_block(handles, state_variables, pool_keys)
    if not block:
        return target_path.read_text(encoding="utf-8", errors="replace")

    content = target_path.read_text(encoding="utf-8", errors="replace")
    # Avoid double-injection on re-entrant calls (blueprint continuation).
    if block.splitlines()[0] in content:
        return content
    new_content = _insert_state_block(content, block)
    if new_content != content:
        target_path.write_text(new_content, encoding="utf-8")
        print(f"  [Skeleton Builder] 🧬 Injected {len(handles or [])} handle(s) + "
              f"{len(state_variables or [])} state var(s) + "
              f"{len(pool_keys or [])} pool name(s) at module root of {target_path}")
    return new_content


# ─── CLI entry point ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        target = Path(sys.argv[1])
        name = sys.argv[2] if len(sys.argv) > 2 else target.stem
        if ensure_skeleton(target, name):
            print(f"  Written: {target}")
        else:
            print(f"  Already valid skeleton: {target}")
    else:
        # Print skeleton to stdout for inspection/pipe
        print(build_skeleton())
