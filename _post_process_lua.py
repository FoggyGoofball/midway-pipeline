"""
_post_process_lua.py — Deterministic post-processor for Lua attraction scripts.
=============================================================================
Runs AFTER all LLM task output has been merged into the target file.
7 fixes, all 100% Python/regex, zero LLM cost.

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
#  Fix #3: Strip pipeline artifacts
# ==============================================================================
# Removes:
#   - [TASK_x_INSERT_HOOK] markers
#   - <fix-plan>...</fix-plan> blocks
#   - ### [Anchor] and similar anchor header artifacts

def _strip_pipeline_artifacts(content: str) -> str:
    """Remove pipeline artifact markers that leaked into output."""
    original = content
    lines = content.splitlines()
    filtered: list[str] = []
    removed_count = 0

    # Remove <fix-plan>...</fix-plan> blocks (may span multiple lines)
    content = re.sub(r'<fix-plan>.*?</fix-plan>', '', content, flags=re.DOTALL)

    for line in content.splitlines():
        # Remove [TASK_x_INSERT_HOOK] markers (with or without leading --)
        if re.match(r'^\s*(?:--?\s*)?\[TASK_\d+_INSERT_HOOK\]', line):
            removed_count += 1
            continue

        # Remove ### [Anchor] header artifacts
        if re.match(r'^\s*###\s*\[Anchor\]', line, re.IGNORECASE):
            removed_count += 1
            continue
        # Remove bare [TASK_x] markers left behind
        if re.match(r'^\s*\[TASK_\d+\]\s*$', line):
            removed_count += 1
            continue
        filtered.append(line)

    result = "\n".join(filtered)
    if result != original:
        print(f"  [Post-Process Fix #3] Removed {removed_count} pipeline artifact(s)")
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
    first_lines = content.splitlines()[:10]
    has_slot_id = any('SLOT_ID' in line for line in first_lines)

    if has_slot_id:
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
    """Dynamically build the set of symbols that need a MidwayPhysics. prefix
    by reading the contract validator's bare_name_to_namespace map.

    Falls back to a minimal built-in set if the contract validator cannot
    be imported (e.g. during unit testing without the full cartridge).

    Returns:
        frozenset of symbol names (e.g. "DestroyBody", "SpawnDynamicSphere").
    """
    global _KNOWN_BARE_SYMBOLS
    if _KNOWN_BARE_SYMBOLS is not None:
        return _KNOWN_BARE_SYMBOLS

    symbols: set[str] = set()

    # Try to build from the contract validator
    try:
        # Attempt to get a contract from the pipeline context if available
        from pipeline import _CTX as _contract_ctx
        if _contract_ctx is not None:
            _build_fn = getattr(_contract_ctx, '_cartridge_build_bridge_contract', None)
            if callable(_build_fn):
                _bridge = _build_fn()
                if _bridge and isinstance(_bridge, dict):
                    from contract_validator import build_lua_contract
                    _lc = build_lua_contract(_bridge)
                    if hasattr(_lc, 'bare_name_to_namespace'):
                        for _bare_name, _ns in _lc.bare_name_to_namespace.items():
                            if _ns.lower() == "midwayphysics":
                                # Re-capitalize from the contract's canonical form
                                # (the map stores lowercase keys, so look up the original)
                                symbols.add(_bare_name.capitalize() if _bare_name[0].islower() else _bare_name)
    except Exception:
        pass

    # Fallback: minimal static set if contract is unavailable
    if not symbols:
        symbols = {
            # Spawn functions
            "SpawnDynamicSphere", "SpawnDynamicBox", "SpawnDynamicCapsule",
            "SpawnDynamicCylinder", "SpawnDynamicMesh",
            "SpawnStaticSphere", "SpawnStaticBox", "SpawnStaticCapsule",
            "SpawnStaticCylinder", "SpawnStaticMesh",
            "SpawnKinematicBox", "SpawnKinematicSphere",
            "SpawnKinematicCapsule", "SpawnKinematicCylinder",
            "SpawnSensorBox", "SpawnSensorSphere",
            "SpawnDynamicBoxR", "SpawnDynamicSphereR",
            "SpawnDynamicCapsuleR", "SpawnDynamicCylinderR",
            "SpawnStaticBoxR", "SpawnStaticSphereR",
            "SpawnStaticCapsuleR", "SpawnStaticCylinderR",
            "SpawnKinematicBoxR", "SpawnKinematicBoxR",
            # Physics manipulation
            "ApplyImpulse", "DestroyBody", "GetVelocity",
            "SetVelocity", "MoveKinematic", "IsSensorTriggered",
            "GetPosition", "SetPosition", "GetAngle", "SetAngle",
            "GetTransform", "SetTransform", "ApplyForce",
            "SetGravityScale", "GetGravityScale",
            # Callback registration
            "OnStep", "OnCollision", "OnSensorEnter", "OnSensorExit",
            # Query
            "RayCast", "OverlapSphere", "OverlapBox",
        }

    _KNOWN_BARE_SYMBOLS = frozenset(symbols)
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

    # Match WORD( patterns where WORD is not already prefixed
    # Negative lookbehind: not preceded by MidwayPhysics. or .
    # Negative lookahead: not a Lua keyword or local function def
    for symbol in sorted(symbols, key=len, reverse=True):
        # Pattern: bare call like `SpawnDynamicSphere(lx, ly, lz, r)`
        # Not preceded by MidwayPhysics., not part of a larger identifier
        pattern = re.compile(
            r'(?<!MidwayPhysics\.)(?<!\.)(?<![\w.])\b'
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
#  Main entry point: apply all 7 fixes
# ==============================================================================

def post_process_lua(content: str) -> str:
    """Apply all 7 deterministic fixes to a Lua attraction script.

    Args:
        content: Raw Lua source text.

    Returns:
        Cleaned Lua source with all 7 fixes applied.
    """
    # Preserve trailing newline — many fix functions use splitlines()/join
    # which naturally strips it.
    had_trailing_newline = content.endswith('\n')

    # Order matters: strip artifacts first so they don't interfere with
    # structural fixes, then fix structure, then add missing pieces.
    content = _strip_pipeline_artifacts(content)      # Fix #3 first
    content = _strip_module_level_mod(content)         # Fix #2
    content = _strip_duplicate_functions(content)      # Fix #1
    content = _add_midwayphysics_prefix(content)       # Fix #6
    content = _inject_onload_static(content)           # Fix #4
    content = _inject_slot_id(content)                 # Fix #5
    # Fix #7 is a gate, not a transform — used by callers

    # Restore trailing newline
    if had_trailing_newline and not content.endswith('\n'):
        content += '\n'

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
        print(f"  [Post-Process] ✅ Cleaned {path.name} ({len(original)} → {len(cleaned)} chars)")
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
