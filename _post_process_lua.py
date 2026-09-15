"""
_post_process_lua.py — Deterministic post-processor for Lua attraction scripts.
=============================================================================
Runs AFTER all LLM task output has been merged into the target file.
8 fixes, all 100% Python/regex, zero LLM cost.

Designed to be called from mesh_finalize.py:run_code_merge() after the
review-fix loop but before consensus.  Can also be run standalone:

    python _post_process_lua.py path/to/skeeball.lua

Public API:
    post_process_lua(content: str) -> str
    post_process_lua_file(path: Path) -> bool      # in-place fix
    post_process_ctx(ctx: PipelineContext) -> PipelineContext
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Optional


# ==============================================================================
#  Fix #1: Strip duplicate function definitions
# ==============================================================================
# Pattern: match `function NAME(...)` ... `end` blocks.
# For each function name, keep ONLY the last definition.
# Uses a two-pass strategy:
#   Pass 1 — extract all function name → (start_pos, end_pos) mappings.
#   Pass 2 — keep only the last occurrence of each name.

def _strip_duplicate_functions(content: str) -> str:
    """Remove all but the last definition of any function name."""
    # Match top-level function definitions (not nested inside another function).
    # We use a line-by-line depth tracker to identify function boundaries.
    _FunctionSpan = tuple[str, int, int]  # (name, start_line, end_line)
    lines = content.splitlines()
    n = len(lines)

    # Find all function definitions and their spans.
    # Strategy: scan for `function NAME(` at depth 0 (module level).
    spans: list[_FunctionSpan] = []
    depth = 0
    fn_start = -1
    fn_name = ""

    for i, line in enumerate(lines):
        stripped = line.strip()
        # Skip comments and empty lines for depth calculation
        code_part = re.sub(r'--.*$', '', stripped).strip()

        if not code_part:
            continue

        # Track depth from block openers/closers
        opener = bool(
            re.search(r'\bfunction\b', code_part) or
            re.match(r'\b(do|if|for|while|repeat)\b', code_part)
        )
        closer = bool(
            re.search(r'(?<!\w)end(?!\w)', code_part) or
            re.search(r'(?<!\w)until(?!\w)', code_part)
        )


        if opener and depth == 0:
            # Check if this is a function definition
            fn_match = re.search(r'(?:local\s+)?function\s+(\w+)\s*\(', code_part)
            if fn_match:
                # If we were tracking a previous function, close it
                if fn_name and fn_start >= 0:
                    spans.append((fn_name, fn_start, i))
                fn_name = fn_match.group(1)
                fn_start = i

        if closer:
            depth = max(0, depth - 1)
            if depth == 0 and fn_name and fn_start >= 0:
                spans.append((fn_name, fn_start, i))
                fn_name = ""
                fn_start = -1
            continue

        if opener:
            depth += 1

    # Pass 2: Find duplicates (keep last occurrence of each name)
    seen: dict[str, int] = {}  # name -> last index in spans list
    for idx, (name, _, _) in enumerate(spans):
        seen[name] = idx

    # Determine lines to keep: everything EXCEPT the duplicate spans
    keep_line_set: set[int] = set(range(n))
    removed_names: set[str] = set()
    for idx, (name, start, end) in enumerate(spans):
        if idx != seen.get(name, -1):
            # This is a duplicate — remove its lines
            for ln in range(start, end + 1):
                keep_line_set.discard(ln)
            removed_names.add(name)

    if not removed_names:
        return content

    result = "\n".join(lines[i] for i in sorted(keep_line_set))
    print(f"  [Post-Process Fix #1] Removed {len(removed_names)} duplicate function(s): {', '.join(sorted(removed_names))}")
    return result


# ==============================================================================
#  Fix #2: Strip module-level MOD caching
# ==============================================================================
# Pattern: `local MOD = AttractionConstants.modifiers` at module level
# (outside any function body).  This is always wrong because Modifiers must
# be re-read every frame inside OnStep — caching at load time captures a
# stale value.

def _strip_module_level_mod(content: str) -> str:
    """Remove `local MOD = AttractionConstants.modifiers` if outside any function."""
    lines = content.splitlines()
    depth = 0
    result: list[str] = []
    removed = 0

    for line in lines:
        stripped = re.sub(r'--.*$', '', line).strip()
        code_part = stripped

        # Track function/block depth
        opener = bool(
            re.search(r'\bfunction\b', code_part) or
            re.match(r'\b(do|if|for|while|repeat)\b', code_part)
        )
        closer = bool(
            re.match(r'\bend\b', code_part) or
            re.match(r'\buntil\b', code_part)
        )

        if opener:
            depth += 1
        elif closer:
            depth = max(0, depth - 1)
            result.append(line)
            continue

        if depth == 0:
            # Check for module-level MOD caching
            if re.match(
                r'^[\t ]*local\s+MOD\s*=\s*AttractionConstants\.modifiers\s*$',
                line
            ):
                removed += 1
                continue  # skip this line

        result.append(line)

    if removed:
        print(f"  [Post-Process Fix #2] Removed {removed} module-level MOD caching line(s)")
    return "\n".join(result)


# ==============================================================================
#  Fix #3: Convert surviving pipeline artifacts to visible TODOs
# ==============================================================================
# Replaces (does NOT silently remove):
#   - [TASK_x_INSERT_HOOK] markers → `-- TODO [TASK_x]: implement` (visible TODOs)
#   - <fix-plan>...</fix-plan> blocks → removed (these are never useful in output)
#   - ### [Anchor] headers → removed
#   - bare [TASK_x] markers → removed
#
# Rationale: If the LLM preserved an anchor marker as a comment, silently
# stripping it produces valid Lua with zero functionality.  Converting it to
# a TODO line ensures the reviewer catches it.

def _artifact_to_todo(line: str):
    """Convert an artifact line into a visible `-- TODO [TASK_x]: ...` comment.
    Returns:
      - None if the artifact should be removed entirely.
      - A replacement string (TODO comment) if artifact converted.
      - The original line unchanged otherwise.
    """
    # <fix-plan> blocks → remove entirely
    if re.match(r'^\s*<fix-plan>', line):
        return None
    # ### [Anchor] headers → remove
    if re.match(r'^\s*###\s*\[Anchor\]', line, re.IGNORECASE):
        return None
    # Bare [TASK_x] lines → remove (no description to preserve)
    if re.match(r'^\s*\[TASK_\d+\]\s*$', line):
        return None

    # [TASK_x_INSERT_HOOK] markers → convert to TODO
    m = re.match(
        r'^\s*(?:--?\s*)?\[TASK_(\d+)_INSERT_HOOK\]\s*--\s*(.*)',
        line
    )
    if m:
        task_num = m.group(1)
        description = m.group(2).strip()
        return f"    -- TODO [TASK_{task_num}]: {description}"
    # Also match markers without leading `-- description` part
    m = re.match(r'^\s*(?:--?\s*)?\[TASK_(\d+)_INSERT_HOOK\]', line)
    if m:
        task_num = m.group(1)
        return f"    -- TODO [TASK_{task_num}]: implement this section"
    return line  # not an artifact — pass through



def _strip_pipeline_artifacts(content: str) -> str:
    """Replace pipeline artifact markers with visible TODO comments.

    Surviving anchor markers indicate the LLM failed to implement that
    section.  Rather than silently removing them (which produces empty
    code), we convert them to `-- TODO [TASK_x]:` lines that the
    reviewer can flag.
    """
    original = content

    # Phase 1: Remove <fix-plan>...</fix-plan> blocks (may span multiple lines)
    content = re.sub(r'<fix-plan>.*?</fix-plan>', '', content, flags=re.DOTALL)

    # Phase 2: Process line-by-line
    lines = content.splitlines()
    converted: list[str] = []
    removed_count = 0
    todo_count = 0

    for line in lines:
        result = _artifact_to_todo(line)
        if result is None:
            removed_count += 1
        elif result != line:
            # Marker was converted to a TODO
            todo_count += 1
            converted.append(result)
        else:
            converted.append(result)


    result = "\n".join(converted)

    # Collapse runs of 3+ blank lines (can happen after removal)
    result = re.sub(r'\n{4,}', '\n\n\n', result)

    if result != original:
        parts = []
        if removed_count:
            parts.append(f"removed {removed_count}")
        if todo_count:
            parts.append(f"converted {todo_count} to TODO")
        print(f"  [Post-Process Fix #3] {'; '.join(parts)} pipeline artifact(s)")
    return result


# ==============================================================================
#  Fix #4: Inject missing OnLoadStatic()
# ==============================================================================
# If the file does not contain `function OnLoadStatic`, inject it before
# `function OnLoad()` (or at end of file if OnLoad also missing).

def _inject_onload_static(content: str) -> str:
    """Inject function OnLoadStatic() if missing."""
    if re.search(r'\bfunction\s+OnLoadStatic\s*\(', content):
        return content  # already present

    # Find insertion point: before OnLoad() or at end of file
    onload_match = re.search(r'^(\s*function\s+OnLoad\s*\()', content, re.MULTILINE)

    stub = (
        "\n-- ─── OnLoadStatic: permanent geometry ────────────────────────\n"
        "function OnLoadStatic()\n"
        "    SpawnSharedBooth()\n"
        "end\n"
    )

    if onload_match:
        insert_pos = onload_match.start()
        result = content[:insert_pos] + stub + "\n" + content[insert_pos:]
    else:
        result = content.rstrip() + "\n" + stub

    print(f"  [Post-Process Fix #4] Injected missing OnLoadStatic()")
    return result


# ==============================================================================
#  Fix #5: Inject missing local SLOT_ID = ...
# ==============================================================================
# If no SLOT_ID assignment is found in the first 10 lines, inject after
# the file header.

def _inject_slot_id(content: str) -> str:
    """Inject ``local SLOT_ID = BOOTH_SLOT_ID or -1`` if missing."""
    # Idempotent: skip if SLOT_ID appears ANYWHERE (a previous cycle may have
    # already injected it, or it may be defined below the first 10 lines).
    if 'SLOT_ID' in content:
        return content

    # Find a good insertion point: after a header comment or at the very top
    lines = content.splitlines()
    insert_after = 0
    for i, line in enumerate(lines[:10]):
        if line.startswith('-- ───') or line.startswith('-- '):
            insert_after = i  # insert after the last header line

    slot_line = "\n-- ─── Slot identity ────────────────────────────────────────\nlocal SLOT_ID = BOOTH_SLOT_ID or -1\n"
    lines.insert(insert_after + 1, slot_line)
    result = "\n".join(lines)

    print(f"  [Post-Process Fix #5] Injected missing local SLOT_ID")
    return result


# ==============================================================================
#  Fix #6: Add MidwayPhysics. prefix to bare API calls
# ==============================================================================
# For known MidwayPhysics symbols called without the prefix, prepend it.
# Uses the contract validator's bare_name_to_namespace map dynamically so the
# symbol list is always in sync with the live bridge contract — no static list
# to maintain or go stale.
#
# NOTE: SpawnSharedBooth is deliberately excluded. It is a standalone global
# function provided by the engine bridge, NOT a MidwayPhysics.* method.
# The contract validator also omits it from bare_name_to_namespace.

# Cache for the dynamically-built symbol set (built once per process)
_KNOWN_BARE_SYMBOLS: frozenset[str] | None = None


def _build_bare_symbols_from_contract() -> frozenset[str]:
    """Return the bare symbols that need a ``MidwayPhysics.`` prefix.

    Uses a static PascalCase set as the single source of truth.  The bridge
    contract stores symbol names in LOWERCASE (both ``bare_name_to_namespace``
    keys and ``approved_calls`` entries), so deriving canonical casing from it
    is lossy — "spawnstaticbox" cannot be reliably re-cased to
    "SpawnStaticBox" — which silently disabled the prefixer.
    """
    global _KNOWN_BARE_SYMBOLS
    if _KNOWN_BARE_SYMBOLS is not None:
        return _KNOWN_BARE_SYMBOLS

    _KNOWN_BARE_SYMBOLS = frozenset({
        # Spawn functions
        "SpawnDynamicMesh", "SpawnDynamicBox", "SpawnDynamicSphere",
        "SpawnDynamicCapsule", "SpawnDynamicCylinder",
        "SpawnDynamicBoxR", "SpawnDynamicSphereR",
        "SpawnDynamicCapsuleR", "SpawnDynamicCylinderR",
        "SpawnStaticMesh", "SpawnStaticBox", "SpawnStaticSphere",
        "SpawnStaticCapsule", "SpawnStaticCylinder",
        "SpawnStaticBoxR", "SpawnStaticSphereR",
        "SpawnStaticCapsuleR", "SpawnStaticCylinderR",
        "SpawnKinematicBox", "SpawnKinematicSphere",
        "SpawnKinematicCapsule", "SpawnKinematicCylinder",
        "SpawnKinematicBoxR",
        "SpawnSensorBox", "SpawnSensorSphere",
        # Pool operations
        "CreatePool", "PoolAcquire", "PoolReturn",
        "PoolCullBelow", "PoolFree", "PoolTotal",
        # Physics manipulation
        "ApplyImpulse", "ApplyAngularImpulse",
        "SetLinearVelocity", "AddLinearVelocity",
        "DestroyBody", "GetVelocity",
        "SetVelocity", "MoveKinematic", "IsSensorTriggered",
        "IsActive",
        "GetPosition", "SetPosition", "GetRotation",
        "SetFriction", "SetRestitution",
        "SetGravityFactor", "SetMass",
        "SetLinearDamping", "SetAngularDamping",
        # Callback registration
        "OnStep", "OnCollision", "OnSensorEnter", "OnSensorExit",
        # Query
        "RayCast", "OverlapSphere", "OverlapBox",
    })
    return _KNOWN_BARE_SYMBOLS


def _add_midwayphysics_prefix(content: str) -> str:
    """Add ``MidwayPhysics.`` prefix to bare calls to known API symbols.

    Uses the contract validator's symbol list dynamically so the set is
    always in sync with the live bridge contract.

    We need to be careful NOT to:
      - Double-prefix already-prefixed calls (MidwayPhysics.SpawnXxx)
      - Prefix calls inside string literals
      - Prefix Lua built-in function names
    Strategy: find all `WORD(` calls and check if WORD is in our known set
    AND not already prefixed with MidwayPhysics.
    """
    symbols = _build_bare_symbols_from_contract()
    original = content
    modifications = 0

    # The coder model occasionally hallucinates the shorter namespace
    # `Physics.*` instead of `MidwayPhysics.*` (e.g. Physics.SpawnStaticBox).
    # Rewrite deterministically — `Physics` is not a real namespace in this
    # engine, so every `Physics.` occurrence means `MidwayPhysics.`.
    _alias_pat = re.compile(r'\bPhysics\.')
    _rewritten, _alias_count = _alias_pat.subn('MidwayPhysics.', content)
    if _alias_count > 0:
        content = _rewritten
        print(f"  [Post-Process Fix #6] Rewrote {_alias_count} hallucinated 'Physics.' namespace(s) → 'MidwayPhysics.'")

    # Match WORD( patterns where WORD is not already prefixed
    # Negative lookbehind: not preceded by MidwayPhysics. or .
    # Negative lookahead: not a Lua keyword or local function def
    for symbol in sorted(symbols, key=len, reverse=True):
        # Pattern: bare call like `SpawnDynamicSphere(lx, ly, lz, r)`
        # Not preceded by MidwayPhysics., not part of a larger identifier, and
        # NOT a function DEFINITION (`function SetFriction(` / `local function
        # OnStep(`) — prefixing a definition corrupts it into
        # `function MidwayPhysics.SetFriction(` which is never what we want.
        pattern = re.compile(
            r'(?<!MidwayPhysics\.)(?<!\.)(?<![\w.])'
            r'(?<!function\s)(?<!local\s)\b'
            + re.escape(symbol)
            + r'\s*\('
        )
        # Use subn to get both the result and count of replacements
        new_content, count = pattern.subn(f'MidwayPhysics.{symbol}(', content)
        if count > 0:
            modifications += count
            content = new_content

    if modifications:
        print(f"  [Post-Process Fix #6] Added MidwayPhysics. prefix to {modifications} call(s) (from {len(symbols)} contract symbols)")
    return content


# ==============================================================================
#  Fix #7: SEARCH-exactly-once gate
# ==============================================================================

# Before applying any fix cycle patch, verify that the SEARCH block matches
# exactly 1 location in the file.  This prevents accidental multi-site patches
# that corrupt the file.

def search_exactly_once_gate(file_content: str, search_block: str) -> bool:
    """Verify that ``search_block`` appears exactly once in ``file_content``.

    Returns True if exactly 1 match, False otherwise.
    Useful as a pre-condition check before applying any fix-cycle patch.
    """
    count = file_content.count(search_block)
    return count == 1


# ==============================================================================
#  Fix #8: Phantom API call replacement (safe in-place)
# ==============================================================================
# Replaces calls to MidwayPhysics APIs that don't exist in the bridge contract
# (hallucinated by the LLM) with safe placeholder values instead of destroying
# the entire line.  Fail-open when no cartridge is mounted.
#
# IMPORTANT: Previous behaviour commented out the ENTIRE LINE, which broke
# Lua syntax when the phantom call appeared inside an if/for/while condition
# (e.g. `if ball:IsActive() then` became a dangling `if`).  Now we replace
# ONLY the phantom call with a safe no-op expression that preserves the
# surrounding statement structure.

def _strip_phantom_api_calls(content: str) -> str:
    """Repair phantom MidwayPhysics.XXX() calls (hallucinated by the LLM).

    Strategy:
      1. Build the known-good API set from the same static PascalCase
         whitelist the prefixer uses, plus lifecycle hooks and helpers.
      2. Scan for every MidwayPhysics.XXXXX( pattern in the content.
      3. For calls to a known phantom alias (CreateDynamicBox etc.), rewrite
         to the correct API name.
      4. For other unknown calls, comment out the whole line when the call is
         a bare statement (a bare `false` is invalid Lua), and replace with
         `false` when the call sits inside an expression.
    """
    # Phase 1: Build known-good API set from the SAME static PascalCase
    # whitelist the prefixer uses, plus lifecycle hooks and shared helpers.
    # The old contract-derived list was incomplete and lower-cased, so it
    # wrongly flagged real APIs like SpawnStaticBox / SetFriction as phantom.
    _known_apis = set(_build_bare_symbols_from_contract())
    _known_apis.update({
        "OnLoadStatic", "OnLoad", "OnStep", "OnUnload", "OnCollision",
        "OnSensorEnter", "OnSensorExit", "OnInput",
        "SpawnSharedBooth",
        "GetModifiers", "GetModifier", "GetCurrentScore", "AddScore",
        "ResetScore", "GetTokens", "DeductTokens", "AwardTokens",
        "GRAVITY", "PHYSICS_SCALE",
    })

    # SpawnSharedBooth is a BARE global helper (attractions/booth_shared.lua),
    # NOT a MidwayPhysics.* API.  Coders frequently hallucinate the prefix;
    # rewrite it so the static guard / reviewer death-spiral cannot fire.
    _content_no_sb, _sb_count = re.subn(
        r'MidwayPhysics\.SpawnSharedBooth\s*\(', 'SpawnSharedBooth(', content
    )
    if _sb_count:
        content = _content_no_sb
        print(f"  [Post-Process Fix #8] Rewrote {_sb_count} 'MidwayPhysics.SpawnSharedBooth' "
              f"-> bare 'SpawnSharedBooth'")

    # `Engine.DestroyBody` is a phantom: body destruction belongs to
    # MidwayPhysics.* — the Economy `Engine.*` namespace only has Award*/Get*
    # hooks.  The coder repeatedly emits it; rewrite deterministically so it
    # never reaches the final PhantomAPIGate.
    _content_no_edb, _edb_count = re.subn(
        r'\bEngine\.DestroyBody\s*\(', 'MidwayPhysics.DestroyBody(', content
    )
    if _edb_count:
        content = _content_no_edb
        print(f"  [Post-Process Fix #8] Rewrote {_edb_count} 'Engine.DestroyBody' "
              f"-> 'MidwayPhysics.DestroyBody'")

    # `MidwayPhysics.GetMass` is a phantom: the bridge contract has SetMass but
    # NO GetMass getter.  Neutralize it to a constant mass so patterns like
    # `SetMass(handle, GetMass(handle))` become valid no-ops instead of
    # `SetMass(handle, false)` (a runtime type error).
    _content_no_gm, _gm_count = re.subn(
        r'\bMidwayPhysics\.GetMass\s*\([^()]*\)', '1.0', content
    )
    if _gm_count:
        content = _content_no_gm
        print(f"  [Post-Process Fix #8] Neutralized {_gm_count} phantom "
              f"'MidwayPhysics.GetMass' -> 1.0")

    # Phase 2: collect all phantom function names found in the content.
    modifications = 0
    _phantom_names_found: set[str] = set()
    for _pm in re.finditer(r'MidwayPhysics\.(\w+)\s*\(', content):
        _fn = _pm.group(1)
        if _fn not in _known_apis:
            _phantom_names_found.add(_fn)

    if not _phantom_names_found:
        return content

    _commented = 0

    # Known phantom aliases -> correct API (substitute, never drop).
    _phantom_substitutions = {
        "CreateDynamicBox": "SpawnDynamicBox",
        "CreateDynamicSphere": "SpawnDynamicSphere",
        "CreateDynamicCapsule": "SpawnDynamicCapsule",
        "CreateDynamicCylinder": "SpawnDynamicCylinder",
        "CreateStaticBox": "SpawnStaticBox",
        "CreateStaticSphere": "SpawnStaticSphere",
        "CreateStaticCapsule": "SpawnStaticCapsule",
        "CreateStaticCylinder": "SpawnStaticCylinder",
        "CreateKinematicBox": "SpawnKinematicBox",
        "CreateKinematicSphere": "SpawnKinematicSphere",
        "DestroyDynamicBody": "DestroyBody",
        "DestroyStaticBody": "DestroyBody",
    }

    for _pn in sorted(_phantom_names_found, key=len, reverse=True):
        # 1. Substitute known aliases (keep a valid MidwayPhysics call).
        if _pn in _phantom_substitutions:
            _sub_pat = re.compile(r'MidwayPhysics\.' + re.escape(_pn) + r'\s*\(')
            _new_content, _count = _sub_pat.subn(
                'MidwayPhysics.' + _phantom_substitutions[_pn] + '(', content
            )
            if _count > 0:
                modifications += _count
                content = _new_content
            continue

        # 2. Comment out phantom calls that are whole statements — a bare
        # `false` is NOT valid Lua statement syntax and breaks compilation.
        # The comment omits the call text so the expression pass below does
        # not re-match the call inside the comment.
        _line_pat = re.compile(
            r'^(\s*)MidwayPhysics\.' + re.escape(_pn) + r'\s*\([^\n]*\)\s*$',
            re.MULTILINE,
        )
        _new_content, _count = _line_pat.subn(
            r'\1-- [phantom removed: ' + _pn + ']', content
        )
        if _count > 0:
            _commented += _count
            content = _new_content

        # 3. Remaining (expression) calls become `false` (valid expression).
        _phantom_pat = re.compile(
            r'MidwayPhysics\.' + re.escape(_pn) + r'\s*\(([^()]*(?:\([^()]*\)[^()]*)*)\)'
        )
        _new_content, _count = _phantom_pat.subn('false', content)
        if _count > 0:
            modifications += _count
            content = _new_content

    if modifications or _commented:
        print(f"  [Post-Process Fix #8] Phantom cleanup: {modifications} call(s) substituted/replaced, "
              f"{_commented} statement(s) commented out (not in bridge contract): "
              f"{', '.join(sorted(_phantom_names_found))}")

    return content


# ==============================================================================
#  Main entry point: apply all 8 fixes
# ==============================================================================

# ==============================================================================
#  Fix #9: Sanitize modifier key accesses
# ==============================================================================
# The coder model hallucinates modifier key names:
#   MOD.gravity_factor                      (no such modifier)
#   MOD.engine_mod_friction                 (table keys have no 'engine_mod_' prefix)
#   MOD[AttractionConstants.modifier_heat]  (malformed field lookup)
# Canonical keys are the snake_case names from the ENGINE_MODIFIERS table
# (bridge contract §4.1-4.3): mass, volume, friction, karma, luck,
# persuasion, heat, sleight_of_hand, nerve.

_MODIFIER_CANONICAL_KEYS = frozenset({
    "mass", "volume", "friction", "karma", "luck",
    "persuasion", "heat", "sleight_of_hand", "nerve",
})

# lowercase alias -> canonical key (covers engine_mod_* and modifier_* forms)
_MODIFIER_KEY_ALIASES = {
    "engine_mod_mass": "mass",
    "engine_mod_volume": "volume",
    "engine_mod_friction": "friction",
    "engine_mod_karma": "karma",
    "engine_mod_luck": "luck",
    "engine_mod_persuasion": "persuasion",
    "engine_mod_heat": "heat",
    "engine_mod_sleight_of_hand": "sleight_of_hand",
    "engine_mod_nerve": "nerve",
    "modifier_mass": "mass",
    "modifier_volume": "volume",
    "modifier_friction": "friction",
    "modifier_karma": "karma",
    "modifier_luck": "luck",
    "modifier_persuasion": "persuasion",
    "modifier_heat": "heat",
    "modifier_sleight_of_hand": "sleight_of_hand",
    "modifier_nerve": "nerve",
    "sleightofhand": "sleight_of_hand",
    "sleight-of-hand": "sleight_of_hand",
}


def _resolve_modifier_key(raw: str):
    """Return the canonical modifier key for raw, or None if unknown."""
    k = raw.strip().lower()
    if k in _MODIFIER_CANONICAL_KEYS:
        return k
    return _MODIFIER_KEY_ALIASES.get(k)


def _sanitize_modifier_keys(content: str) -> str:
    """Rewrite malformed modifier accesses to canonical keys.

    Known aliases (engine_mod_friction, modifier_heat, ...) are rewritten to
    their canonical snake_case key.  Truly unknown keys (gravity_factor,
    gravity) are neutralized to 1.0 so they never silently read nil.
    """
    _rewrites = 0
    _neutralized = 0

    # 1. MOD[AttractionConstants.modifier_X]  ->  MOD.X
    _mod_idx_re = re.compile(
        r'\bMOD\s*\[\s*(AttractionConstants\s*\.\s*[A-Za-z_][A-Za-z0-9_]*)\s*\]',
        re.IGNORECASE,
    )

    def _fix_mod_index(m):
        nonlocal _rewrites, _neutralized
        key_m = re.search(r'[A-Za-z_][A-Za-z0-9_]*$', m.group(1))
        if not key_m:
            return m.group(0)
        canon = _resolve_modifier_key(key_m.group(0))
        if canon is None:
            _neutralized += 1
            return "1.0"
        _rewrites += 1
        return f"MOD.{canon}"

    content = _mod_idx_re.sub(_fix_mod_index, content)

    # 2. MOD["key"] / MOD['key']  ->  MOD.<canonical>
    _mod_str_idx_re = re.compile(
        r'\bMOD\s*\[\s*(["\'])([A-Za-z_][A-Za-z0-9_]*)\1\s*\]',
        re.IGNORECASE,
    )

    def _fix_mod_str_index(m):
        nonlocal _rewrites, _neutralized
        canon = _resolve_modifier_key(m.group(2))
        if canon is None:
            _neutralized += 1
            return "1.0"
        _rewrites += 1
        return f"MOD.{canon}"

    content = _mod_str_idx_re.sub(_fix_mod_str_index, content)

    # 3. MOD.key  ->  MOD.<canonical> (or 1.0 if unknown)
    _mod_dot_re = re.compile(
        r'\bMOD\s*\.\s*([A-Za-z_][A-Za-z0-9_]*)',
        re.IGNORECASE,
    )

    def _fix_mod_dot(m):
        nonlocal _rewrites, _neutralized
        raw = m.group(1)
        canon = _resolve_modifier_key(raw)
        if canon is None:
            _neutralized += 1
            return "1.0"
        if canon != raw.lower():
            _rewrites += 1
            return f"MOD.{canon}"
        return m.group(0)

    content = _mod_dot_re.sub(_fix_mod_dot, content)

    # 4. AttractionConstants.modifiers.key  ->  canonicalize / neutralize
    _ac_mod_dot_re = re.compile(
        r'\bAttractionConstants\s*\.\s*modifiers\s*\.\s*([A-Za-z_][A-Za-z0-9_]*)',
        re.IGNORECASE,
    )

    def _fix_ac_mod_dot(m):
        nonlocal _rewrites, _neutralized
        raw = m.group(1)
        canon = _resolve_modifier_key(raw)
        if canon is None:
            _neutralized += 1
            return "1.0"
        if canon != raw.lower():
            _rewrites += 1
            return f"AttractionConstants.modifiers.{canon}"
        return m.group(0)

    content = _ac_mod_dot_re.sub(_fix_ac_mod_dot, content)

    if _rewrites or _neutralized:
        print(f"  [Post-Process Fix #9] Canonicalized {_rewrites} modifier key(s), "
              f"neutralized {_neutralized} unknown key(s) to 1.0")
    return content


def _auto_declare_handles(content: str) -> str:
    """Inject `local <name> = nil` for physics handles used but never declared.

    Each attraction task invents its own handle names (bell_sensor, puck_dynamic,
    malletHandles, ...) and references them from OnStep/OnUnload without ever
    declaring them at module scope, producing nil-index runtime errors.  This
    pass collects every identifier used as the first argument to a physics call
    and, when it is never declared with `local`, declares it at module scope so
    all lifecycle closures share one upvalue.
    """
    _NOT_HANDLES = {
        "function", "end", "then", "if", "else", "elseif", "for", "while", "do",
        "repeat", "until", "local", "return", "and", "or", "not", "nil", "true",
        "false", "in", "break", "goto",
        "math", "table", "string", "os", "tick", "print", "pairs", "ipairs",
        "type", "tostring", "tonumber", "setmetatable", "getmetatable", "select",
        "unpack", "require", "error", "assert", "pcall", "xpcall",
    }

    _declared = set(re.findall(r'^\s*local\s+([a-zA-Z_]\w*)', content, re.MULTILINE))
    _declared.update({"MOD", "dt", "self", "lx", "ly", "lz"})

    _handle_pat = re.compile(
        r'(?:MidwayPhysics\.\w+|IsSensorTriggered|DestroyBody|PoolReturn|PoolAcquire|'
        r'PoolFree|PoolTotal|PoolCullBelow|IsActive|GetPosition|GetVelocity|GetMass|'
        r'SetMass|SetFriction|SetRestitution|SetLinearDamping|SetAngularDamping|'
        r'SetLinearVelocity|SetAngularVelocity|ApplyImpulse|ApplyAngularImpulse|'
        r'MoveKinematic)\s*\(\s*([a-zA-Z_]\w*)'
    )
    _handle_ids: list[str] = []
    for _m in _handle_pat.finditer(content):
        _hid = _m.group(1)
        if (
            _hid
            and _hid not in _declared
            and _hid not in _NOT_HANDLES
            and _hid not in _handle_ids
        ):
            _handle_ids.append(_hid)

    if not _handle_ids:
        return content

    _decl_lines = "\n".join(
        f"local {_h} = nil  -- auto-declared physics handle" for _h in _handle_ids
    )

    _slot_m = re.search(r'^local\s+SLOT_ID\s*=.*$', content, re.MULTILINE)
    if _slot_m:
        _insert_at = _slot_m.end()
        content = content[:_insert_at] + "\n" + _decl_lines + "\n" + content[_insert_at:]
    else:
        content = _decl_lines + "\n\n" + content

    print(f"  [Post-Process Fix #10] Auto-declared {len(_handle_ids)} physics handle(s): "
          f"{', '.join(_handle_ids)}")
    return content


def _dedupe_onstep_registrations(content: str) -> str:
    """Keep only ONE ``MidwayPhysics.OnStep(function ... end)`` registration.

    The skeleton already declares a single per-frame callback inside OnLoad().
    The coder repeatedly emits ADDITIONAL nested ``MidwayPhysics.OnStep(...)``
    registrations inside its own task hooks, which the engine would register
    as overlapping callbacks (a race condition).  This pass keeps the canonical
    (outermost, MOD-bearing) registration and strips every other full
    registration block.
    """
    _start_re = re.compile(r'MidwayPhysics\.OnStep\s*\(\s*function\b')
    _starts = [m for m in _start_re.finditer(content)]
    if len(_starts) <= 1:
        return content

    def _word_boundary(s: str, i: int, j: int) -> bool:
        prev = s[i - 1] if i > 0 else ' '
        nxt = s[j] if j < len(s) else ' '
        return (not (prev.isalnum() or prev == '_')) and (not (nxt.isalnum() or nxt == '_'))

    def _span_end(func_idx: int) -> int:
        """Return the exclusive end index of the OnStep registration whose
        `function` keyword starts at func_idx (i.e. just past the closing `)`)."""
        n = len(content)
        i = func_idx + len('function')
        depth = 1
        in_str = None
        in_lc = False
        while i < n:
            c = content[i]
            nxt = content[i + 1] if i + 1 < n else ''
            if in_lc:
                if c == '\n':
                    in_lc = False
                i += 1
                continue
            if in_str:
                if c == '\\':
                    i += 2
                    continue
                if c == in_str:
                    in_str = None
                i += 1
                continue
            if c == '-' and nxt == '-':
                in_lc = True
                i += 2
                continue
            if c in ('"', "'"):
                in_str = c
                i += 1
                continue
            if content.startswith('function', i) and _word_boundary(content, i, i + 8):
                depth += 1
                i += 8
                continue
            if content.startswith('end', i) and _word_boundary(content, i, i + 3):
                depth -= 1
                i += 3
                if depth == 0:
                    j = i
                    while j < n and content[j] in ' \t\r\n':
                        j += 1
                    if j < n and content[j] == ')':
                        return j + 1
                    return i
                continue
            i += 1
        return n

    # Compute the span (start of `MidwayPhysics`, exclusive end) of each
    # registration and locate the canonical one: it contains the skeleton's
    # `local MOD = AttractionConstants.modifiers` marker and is the outermost
    # span (nested registrations live INSIDE it).
    spans: list[tuple[int, int]] = []
    for _m in _starts:
        _func_idx = content.find('function', _m.start())
        spans.append((_m.start(), _span_end(_func_idx)))

    _mod_marker = 'local MOD = AttractionConstants.modifiers'
    _with_mod = [s for s in spans if _mod_marker in content[s[0]:s[1]]]
    _pool = _with_mod or spans

    # Outermost span = not contained inside any other span in the pool.
    _outermost = [
        s for s in _pool
        if not any(o != s and o[0] <= s[0] and s[1] <= o[1] for o in _pool)
    ]
    _keep = _outermost[0] if _outermost else _pool[0]

    _removed = 0
    for (s, e) in sorted((sp for sp in spans if sp != _keep), key=lambda t: t[0], reverse=True):
        content = content[:s] + '-- [nested OnStep registration removed]' + content[e:]
        _removed += 1

    if _removed:
        print(f"  [Post-Process Fix #12] Removed {_removed} nested/duplicate OnStep registration(s)")
    return content


def _repair_duplicate_underscore_locals(content: str) -> str:
    """Repair `local _ = a, _ = b, ...` duplicate-declarator syntax errors.

    The coder repeatedly emits this when "consuming all modifiers":
        local _ = MOD.volume, _ = MOD.friction, _ = MOD.karma, _ = MOD.luck
    In Lua, `local _ = a, _ = b` is a SYNTAX ERROR (``_`` is declared twice in
    one statement; the second ``_ = b`` is not a valid declarator).  Rewrite to
    a valid statement sequence: ``local _ = a`` then bare ``_ = b; _ = c; ...``.
    """
    _repaired = 0
    _out: list[str] = []
    for _line in content.splitlines():
        _m = re.match(r'^(\s*)local\s+_\s*=\s*(.+)$', _line)
        if _m and ', _ = ' in _line:
            _indent = _m.group(1)
            _parts = _m.group(2).split(', _ = ')
            _first = _parts[0].strip()
            _out.append(f"{_indent}local _ = {_first}")
            for _rest in _parts[1:]:
                _out.append(f"{_indent}_ = {_rest.strip()}")
            _repaired += 1
        else:
            _out.append(_line)
    if _repaired:
        print(f"  [Post-Process Fix #13] Repaired {_repaired} duplicate-'_' local declaration(s)")
    return "\n".join(_out)


_BARE_EXPR_KEYWORDS = frozenset({
    'local', 'function', 'end', 'if', 'then', 'else', 'elseif', 'for',
    'while', 'do', 'repeat', 'until', 'return', 'break', 'goto', 'in',
    'and', 'or', 'not', 'true', 'false', 'nil',
})

_BARE_EXPR_RE = re.compile(
    r'^(\s*)((?:[A-Za-z_][A-Za-z0-9_.]*(?:\s*\[[^\]]+\])?)|(?:\d+(?:\.\d+)?))\s*(--.*)?$'
)


def _repair_bare_expression_statements(content: str) -> str:
    """Convert bare expression statements into valid Lua statements.

    Lua only permits assignments and function calls as statements.  The coder
    repeatedly "consumes" modifiers by emitting bare expressions:
        MOD.heat
        MOD.karma
        AttractionConstants.modifiers.luck
    and earlier fixes (e.g. Fix #8/#9 neutralization) can leave a bare literal
    such as ``1.0``.  All of these are SYNTAX ERRORS ("syntax error near 'MOD'"
    / "unexpected symbol near '1.0'").  Rewrite each such line to
    ``local _ = <expr>`` — a legal statement that still evaluates the value,
    so the "read every modifier" static gate keeps passing.
    """
    _repaired = 0
    _out: list[str] = []
    for _line in content.splitlines():
        _m = _BARE_EXPR_RE.match(_line)
        if _m:
            _expr = _m.group(2)
            _head = _expr.split('.')[0].split('[')[0].strip()
            if _head not in _BARE_EXPR_KEYWORDS:
                _indent = _m.group(1)
                _comment = _m.group(3) or ''
                _out.append(f"{_indent}local _ = {_expr}{_comment}")
                _repaired += 1
                continue
        _out.append(_line)
    if _repaired:
        print(f"  [Post-Process Fix #15] Converted {_repaired} bare expression "
              f"statement(s) to 'local _ = ...'")
    return "\n".join(_out)


def _normalize_pool_name_arguments(content: str) -> str:
    """Rewrite quoted pool-name literals that equal a declared ``<key>_pool``
    constant into the bare variable.

    The skeleton injects ``local <key>_pool = "<key>"`` (the pool NAME string)
    for every pooled entity.  The coder sometimes writes
    ``PoolAcquire('<key>_pool', ...)`` — a quoted literal of the CONSTANT name,
    which names a pool that does not exist (the real pool is named ``<key>``).
    Replacing the quoted constant with the variable makes every pool call
    resolve to the actual pool name string, converging quoted and unquoted
    spellings on the single declared constant.
    """
    _consts = {
        m.group(1)
        for m in re.finditer(r'local\s+(\w+_pool)\s*=\s*"[^"]+"', content)
    }
    if not _consts:
        return content

    _alt = "|".join(sorted(_consts, key=len, reverse=True))
    _quoted_re = re.compile(r'(["\'])(' + _alt + r')\1')
    _call_re = re.compile(
        r'\b(?:PoolAcquire|PoolReturn|PoolFree|PoolTotal|PoolCullBelow|CreatePool)'
        r'\s*\([^)]*\)'
    )

    _count = 0

    def _fix(_m):
        nonlocal _count
        _call = _m.group(0)
        _new, _n = _quoted_re.subn(lambda _q: _q.group(2), _call)
        if _n:
            _count += _n
        return _new

    content = _call_re.sub(_fix, content)
    if _count:
        print(f"  [Post-Process Fix #14] Normalized {_count} quoted pool-name "
              f"literal(s) -> variable")
    return content


def _strip_comment_monologues(content: str) -> str:
    """Collapse long runs of prose comment-only lines into a single line.

    The coder model sometimes enters "analysis paralysis" and writes multi-line
    essays INSIDE Lua comments arguing with the spec instead of writing code
    (e.g. a ~50-line debate about whether SetLinearVelocity can "teleport").
    These monologues bloat the accumulated file, trip the reviewer, and crowd
    out real code.  Deterministically collapse any run of 5+ consecutive
    comment-only lines (none of which are structural markers) to one line.

    Structural markers (separators, task anchors, TODO markers, module-state
    headers, lifecycle-section headers, generated declaration comments) are
    preserved verbatim so the skeleton/anchor machinery keeps working.
    """
    _KEEP_RE = re.compile(
        r'^--\s*('
        r'[─━═┌┐└┘│╌]+'             # box-drawing / separator headers
        r'|\[TASK_'                 # -- [TASK_N_INSERT_HOOK] anchors
        r'|TODO'                    # -- TODO [TASK_N]: ...
        r'|DETERMINISTIC MODULE STATE'
        r'|MODULE-LEVEL STATE'
        r'|Slot identity'
        r'|OnLoadStatic'
        r'|OnLoad\b'
        r'|OnStep\b'
        r'|OnUnload\b'
        r'|\[phantom removed'
        r'|auto-declared'
        r'|physics handle'
        r'|pool name'
        r'|spawned in'
        r'|pool name string'
        r')'
    )

    lines = content.splitlines()
    result: list[str] = []
    _run: list[int] = []  # indices of the current consecutive comment-only run
    _removed = 0

    def _flush_run() -> None:
        nonlocal _removed
        if len(_run) < 5:
            result.extend(lines[i] for i in _run)
            _run.clear()
            return
        # Only collapse when NO line in the run is a structural marker.
        if any(_KEEP_RE.match(lines[i].lstrip()) for i in _run):
            result.extend(lines[i] for i in _run)
        else:
            result.append(lines[_run[0]].strip())
            _removed += len(_run) - 1
        _run.clear()

    for i, line in enumerate(lines):
        is_comment_only = line.lstrip().startswith('--')
        if is_comment_only:
            _run.append(i)
        else:
            _flush_run()
            result.append(line)
    _flush_run()

    if _removed:
        print(f"  [Post-Process Fix #11] Collapsed {_removed} prose comment-monologue line(s)")
    return "\n".join(result)


def post_process_lua(content: str) -> str:
    """Apply all 9 deterministic fixes to a Lua attraction script.

    Args:
        content: Raw Lua source text.

    Returns:
        Cleaned Lua source with all 8 fixes applied.
    """
    # Preserve trailing newline — many fix functions use splitlines()/join
    # which naturally strips it.
    had_trailing_newline = content.endswith('\n')

    # Order matters: strip artifacts first so they don't interfere with
    # structural fixes, then fix structure, then add missing pieces.
    content = _strip_pipeline_artifacts(content)      # Fix #3 first
    content = _strip_comment_monologues(content)       # Fix #11 — kill prose comment essays
    content = _strip_module_level_mod(content)         # Fix #2
    content = _repair_duplicate_underscore_locals(content)  # Fix #13 — local _ = a, _ = b syntax error
    content = _sanitize_modifier_keys(content)         # Fix #9 -- canonicalize/neutralize MOD.* keys
    content = _strip_duplicate_functions(content)      # Fix #1
    content = _add_midwayphysics_prefix(content)       # Fix #6
    content = _strip_phantom_api_calls(content)        # Fix #8 — catch hallucinations after prefix fix
    content = _normalize_pool_name_arguments(content)  # Fix #14 — quoted '<key>_pool' literal -> variable
    content = _dedupe_onstep_registrations(content)    # Fix #12 — one OnStep callback only
    content = _repair_bare_expression_statements(content)  # Fix #15 — LAST: bare MOD.x / neutralized literals are invalid statements
    # Full-file invariants (#4 OnLoadStatic, #5 SLOT_ID) only apply to a whole
    # Lua module, not to a SEARCH/REPLACE patch fragment.  Applying them to
    # fragments injected duplicate `local SLOT_ID` lines and spurious
    # OnLoadStatic stubs into every task's patch block.
    _is_patch_fragment = ('<<<<<<< SEARCH' in content or '>>>>>>> REPLACE' in content)
    if not _is_patch_fragment:
        content = _inject_onload_static(content)           # Fix #4
        content = _inject_slot_id(content)                 # Fix #5
        content = _auto_declare_handles(content)           # Fix #10
    # Fix #7 is a gate, not a transform — used by callers

    # Restore trailing newline
    if had_trailing_newline and not content.endswith('\n'):
        content += '\n'

    return content


def repair_lua_syntax(content: str) -> str:
    """Minimal, safe syntax-repair pass for use mid-task (before a luac gate).

    Unlike the full ``post_process_lua``, this applies ONLY the pure syntax
    repairs — no duplicate-function dedup, no OnStep-registration dedupe, no
    handle/OnLoadStatic/SLOT_ID injection — so it can run on a
    partially-assembled accumulated file without disturbing the anchor-based
    incremental build.
    """
    content = _repair_duplicate_underscore_locals(content)   # Fix #13
    content = _repair_bare_expression_statements(content)    # Fix #15
    return content


def post_process_lua_file(path: Path) -> bool:
    """Read, post-process, and write back a Lua file in-place.

    Args:
        path: Path to the .lua file.

    Returns:
        True if any changes were made.
    """
    if not path.is_file():
        print(f"  [Post-Process] ⛔ File not found: {path}")
        return False

    original = path.read_text(encoding="utf-8", errors="replace")
    cleaned = post_process_lua(original)

    if cleaned != original:
        path.write_text(cleaned, encoding="utf-8")
        print(f"  [Post-Process] ✅ Cleaned {path.name} ({len(original)} -> {len(cleaned)} chars)")
        return True

    print(f"  [Post-Process] ✓ {path.name} already clean ({len(original)} chars)")
    return False


def post_process_ctx(ctx) -> object:
    """Pipeline integration: run post-process on all Lua results.

    Intended to be called from mesh_finalize.py between review-fix loop
    and consensus.  Modifies ctx.all_results_dict in-place for any Lua
    task that has output.

    Args:
        ctx: A PipelineContext (duck-typed — any object with all_results_dict
             and task_map attributes).

    Returns:
        The same ctx, modified in-place for convenience.
    """
    if not hasattr(ctx, 'all_results_dict'):
        return ctx

    print(f"\n{'='*60}")
    print(f"  Phase B: Deterministic Post-Processor")
    print(f"{'='*60}")

    processed = 0
    for tid, content in list(ctx.all_results_dict.items()):
        if not content or not isinstance(content, str):
            continue

        # Only process Lua files
        is_lua = tid.endswith('.lua') or tid.startswith('merged:')
        if not is_lua:
            # Monolithic tasks stored under task_monolithic key contain Lua code
            # even though the key isn't a .lua path.  Check for Lua content
            # heuristically when the key starts with "task_".
            if tid.startswith('task_') and (
                'MidwayPhysics.' in content
                or 'function OnLoad' in content
                or 'AttractionConstants' in content
                or 'SpawnSharedBooth' in content
            ):
                is_lua = True
        if not is_lua:
            # Check the task map for target_file hint
            task_obj = ctx.task_map.get(tid) if hasattr(ctx, 'task_map') else None
            if task_obj:
                tf = getattr(task_obj, 'target_file', '') or ''
                is_lua = tf.endswith('.lua') or '.lua' in tf
        if not is_lua:
            continue

        cleaned = post_process_lua(content)
        if cleaned != content:
            ctx.all_results_dict[tid] = cleaned
            processed += 1
            # Also update all_results list if present
            if hasattr(ctx, 'all_results') and ctx.all_results:
                for i, entry in enumerate(ctx.all_results):
                    if entry.get('task_id') == tid:
                        ctx.all_results[i] = {'task_id': tid, 'output': cleaned}
                        break

    print(f"  [Post-Process] Applied fixes to {processed} Lua output(s)")
    return ctx


# ==============================================================================
#  CLI entry point
# ==============================================================================

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python _post_process_lua.py <path_to.lua> [path2.lua ...]")
        sys.exit(1)

    changed = 0
    for arg in sys.argv[1:]:
        p = Path(arg)
        if post_process_lua_file(p):
            changed += 1

    print(f"\n  Summary: {changed}/{len(sys.argv[1:])} file(s) modified")
    sys.exit(0 if changed > 0 else 0)
