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
    -- [TASK_6_INSERT_HOOK] -- input handling / aiming mechanism setup (use MidwayInput.IsActionDown)
    MidwayPhysics.OnStep(function(dt)
        local MOD = AttractionConstants.modifiers  -- INSIDE callback
    -- [TASK_7_INSERT_HOOK] -- modifier read: AttractionConstants.modifiers every frame, apply ENGINE_MOD_HEAT/LUCK/SLEIGHT_OF_HAND
    -- [TASK_8_INSERT_HOOK] -- gameplay tick & scoring: Engine.AwardTickets(n, label) with Engine.GetStreak() multiplier
    -- [TASK_10_INSERT_HOOK] -- advanced modifier read: AttractionConstants.modifiers every OnStep frame, compute dynamic heat/luck/sleight-of-hand effects per game event
    -- [TASK_11_INSERT_HOOK] -- advanced economy hooks: Engine.AwardTickets or Engine.AwardTokens with streak multiplier on each score event, wire modifier scalars into payout
    end)
end

-- ─── INVARIANT 5: OnUnload ─────────────────────────────
function OnUnload()
    -- [TASK_9_INSERT_HOOK] -- cleanup & diagnostics (DestroyDynamicBody, print stats)
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
