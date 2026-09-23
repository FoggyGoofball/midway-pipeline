#!/usr/bin/env python3
"""
Apply the 5 critical fixes to the pipeline codebase.

Fix #1: Fuzzy Matching (4th tier: partial-content fallback)
Fix #2: Scaffold (persist file constraint to ctx so scaffold fires)
Fix #3: Wave Serialization (already done - just verify)
Fix #4: Architect Syntax Fixer (de-duplicate OnLoadStatic et al)
Fix #5: Anti-Patterns in prompt
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent

MODS = 0

# =====================================================================
# FIX #2: Persist file constraint to ctx so scaffold fires
# =====================================================================
bf = ROOT / "mesh_fetches_blueprint.py"
text = bf.read_text(encoding="utf-8")

# The scaffold code exists but ctx._user_file_constraint_canonical is
# never set. The scaffold at line ~720 reads it from ctx.
# Fix: Save the local variable to ctx after computing it.
old_fc = (
    '            _user_file_constraint_canonical = f"attractions/{_attr_slug}/{_attr_slug}.lua"\n'
    '            print(f"  [Blueprint] \\U0001f512 Auto-enforced file constraint: {_user_file_constraint_canonical}")'
)
new_fc = (
    '            _user_file_constraint_canonical = f"attractions/{_attr_slug}/{_attr_slug}.lua"\n'
    '            # Persist to ctx so the scaffold writer below can see it.\n'
    '            ctx._user_file_constraint_canonical = _user_file_constraint_canonical\n'
    '            print(f"  [Blueprint] \\U0001f512 Auto-enforced file constraint: {_user_file_constraint_canonical}")'
)

if old_fc in text:
    text = text.replace(old_fc, new_fc, 1)
    MODS += 1
    print("[FIX #2] Saved _user_file_constraint_canonical to ctx")
else:
    # Try without the emoji escape (raw file may have different encoding)
    old_fc2 = (
        '            _user_file_constraint_canonical = f"attractions/{_attr_slug}/{_attr_slug}.lua"\n'
        '            print(f"  [Blueprint] \U0001f512 Auto-enforced file constraint: {_user_file_constraint_canonical}")'
    )
    new_fc2 = (
        '            _user_file_constraint_canonical = f"attractions/{_attr_slug}/{_attr_slug}.lua"\n'
        '            # Persist to ctx so the scaffold writer below can see it.\n'
        '            ctx._user_file_constraint_canonical = _user_file_constraint_canonical\n'
        '            print(f"  [Blueprint] \U0001f512 Auto-enforced file constraint: {_user_file_constraint_canonical}")'
    )
    if old_fc2 in text:
        text = text.replace(old_fc2, new_fc2, 1)
        MODS += 1
        print("[FIX #2] Saved _user_file_constraint_canonical to ctx (alt match)")
    else:
        print("[FIX #2] WARN: Could not find scaffold assignment line")

bf.write_text(text, encoding="utf-8")

# =====================================================================
# FIX #1: Add 4th tier fuzzy match (partial content fallback)
# =====================================================================
eh = ROOT / "_helpers_exec.py"
text = eh.read_text(encoding="utf-8")

# Add a 4th tier to _fuzzy_apply_patch: when all 3 tiers fail, strip comments
# from both search and file content, then try matching.
old_end = "    # No match found \u2014 return unchanged\n    return file_content\n\n\ndef _extract_search_replace_blocks"

# The exact version
replacements_4th_tier = """
    # 4. Partial-content fallback: when the model is too noisy to produce a
    # matching window, try matching line-by-line using only the content lines
    # (skip comment-only lines and blank lines in the SEARCH block). This is
    # an aggressive fallback for small local LLMs (e.g. qwen2.5-coder:7b)
    # that hallucinate extra whitespace, comments, or blank lines.
    search_stripped = search_text.strip()
    if search_stripped:
        # Build a set of non-empty, non-comment search lines
        search_content = [s.rstrip() for s in search_stripped.splitlines() if s.strip() and not s.strip().startswith("--")]
        if len(search_content) >= 2:
            # Try to find these lines as a contiguous sequence in the file
            file_lines_all = file_content.splitlines()
            for i in range(len(file_lines_all) - len(search_content) + 1):
                window = file_lines_all[i:i + len(search_content)]
                window_content = [w.rstrip() for w in window]
                if window_content == search_content:
                    original_window_str = "\\n".join(window)
                    if original_window_str in file_content:
                        return file_content.replace(original_window_str, replace_text, 1)

    # No match found \u2014 return unchanged
    return file_content


def _extract_search_replace_blocks"""

# Try multiple escape variants
for variant in [
    ("    # No match found \u2014 return unchanged\n    return file_content\n\n\ndef _extract_search_replace_blocks", replacements_4th_tier),
    ("    # No match found \\u2014 return unchanged\n    return file_content\n\n\ndef _extract_search_replace_blocks", replacements_4th_tier),
    ("    # No match found -- return unchanged\n    return file_content\n\n\ndef _extract_search_replace_blocks", replacements_4th_tier),
]:
    if variant[0] in text and "4. Partial-content fallback" not in text:
        text = text.replace(variant[0], variant[1], 1)
        MODS += 1
        print("[FIX #1] Added 4th-tier partial-content fallback to _fuzzy_apply_patch")
        break

if "4. Partial-content fallback" not in text:
    print("[FIX #1] WARN: Could not add 4th-tier fallback - checking file for exact match string...")
    # Find the exact line
    for i, line in enumerate(text.splitlines()):
        if "No match found" in line and "return unchanged" in line:
            print(f"  Line {i+1}: {repr(line)}")
            break

eh.write_text(text, encoding="utf-8")

# =====================================================================
# FIX #4: Add duplicate-function guard to per-task architect fix cycle
# =====================================================================
fp = ROOT / "_finalize_preflight.py"
text = fp.read_text(encoding="utf-8")

# Before the per-task arch fix applies a fix, check if the fix would
# introduce a duplicate function in the file. Find the section where
# ctx.all_results_dict[tid] is updated (line ~694).
# We need to add a guard after _strip_search_replace_metadata but before
# the assignment.

# Strategy: Find the "Guard: refuse to replace a real implementation" block
# and add a duplicate-function guard right after it.
old_guard = """                    _af_code = _strip_search_replace_metadata(fixed_code)
                    # Guard: refuse to replace a real implementation with a stub."""

new_guard = """                    _af_code = _strip_search_replace_metadata(fixed_code)
                    # Guard: refuse to introduce duplicate function definitions.
                    # When multiple tasks all target the same file and the LLM
                    # fix generates e.g. "function OnLoadStatic() end" for each
                    # one, this guard prevents the file from accumulating
                    # duplicates. Check if this fix would create a duplicate
                    # in the aggregated file.
                    _af_task_obj = ctx.task_map.get(tid)
                    if _af_task_obj and getattr(_af_task_obj, 'target_file', None):
                        _af_target_file = str(_af_task_obj.target_file).replace('\\\\', '/')
                        # Build the aggregated merged output for this file
                        _af_merged_lines = {}
                        for _af_mtid, _af_mout in ctx.all_results_dict.items():
                            if _af_mtid.startswith('merged:') and _af_target_file in _af_mtid:
                                _af_merged_lines[_af_mtid] = _af_mout
                            elif _af_mtid != tid:
                                _af_mtobj = ctx.task_map.get(_af_mtid)
                                if _af_mtobj and getattr(_af_mtobj, 'target_file', None):
                                    if str(_af_mtobj.target_file).replace('\\\\', '/') == _af_target_file:
                                        _af_merged_lines[_af_mtid] = _af_mout
                        # Check for duplicate function definitions in the fix
                        _af_new_funcs = set(re.findall(r'^function\\s+(\\w+)\\s*\\(', _af_code, re.MULTILINE))
                        _af_existing_funcs = set()
                        for _af_v in _af_merged_lines.values():
                            if _af_v:
                                _af_existing_funcs.update(
                                    re.findall(r'^function\\s+(\\w+)\\s*\\(', _af_v, re.MULTILINE)
                                )
                        _af_dupes = _af_new_funcs & _af_existing_funcs
                        if _af_new_funcs and _af_dupes:
                            # Strip the duplicate function definitions from the fix
                            # by removing "function <name>...end" blocks that already exist
                            for _af_dfunc in _af_dupes:
                                _af_old_count = len(_af_code)
                                # Remove the duplicate function definition
                                _af_code = re.sub(
                                    r'function\\s+' + re.escape(_af_dfunc) + r'\\s*\\([^)]*\\)[^e]*?end\\s*',
                                    '',
                                    _af_code,
                                    count=1
                                )
                                if len(_af_code) < _af_old_count:
                                    print(f"  [Arch Fix] \\u26a0 Stripped duplicate function '{_af_dfunc}' "
                                          f"from fix for {tid} (already defined by another task)")
                    # Guard: refuse to replace a real implementation with a stub."""

if old_guard in text and "refuse to introduce duplicate function" not in text:
    text = text.replace(old_guard, new_guard, 1)
    MODS += 1
    print("[FIX #4] Added duplicate-function guard to per-task arch fix cycle")
else:
    print("[FIX #4] WARN: Could not add duplicate-function guard")
    # Show surrounding context for debugging
    idx = text.find("Guard: refuse to replace a real implementation")
    if idx >= 0:
        print(f"  Found at position {idx}, snippet: {text[idx:idx+200]}")

fp.write_text(text, encoding="utf-8")

# =====================================================================
# FIX #5: Add ANTI-PATTERNS section to docs/rules_lua.md
# =====================================================================
rl = ROOT / "docs" / "rules_lua.md"
text = rl.read_text(encoding="utf-8")

anti_patterns = """
### ANTI-PATTERNS (NEVER DO THESE)
- [ ] **ANTI-PATTERN: Code at module root level.** `MidwayPhysics.SpawnDynamicSphere(...)` must NEVER appear outside a function body. Code at root level crashes the engine because the MidwayPhysics API is not yet initialized.

- [ ] **ANTI-PATTERN: `function OnStep(dt)` at module level.** You MUST use `MidwayPhysics.OnStep(function(dt) ... end)` inside `OnLoad()`. A bare `OnStep(dt)` function will never be called by the engine.

- [ ] **ANTI-PATTERN: Creating multiple `OnLoadStatic()` / `OnLoad()` / `OnUnload()` functions.** Each lifecycle hook must appear exactly once in the file. Duplicate definitions cause Lua parse errors at load time.

- [ ] **ANTI-PATTERN: Caching `AttractionConstants.modifiers` at load time.** Always read `local MOD = AttractionConstants.modifiers` inside the `OnStep` callback every frame. Cache-invalidation is handled by the engine; caching at module level reads stale values.

- [ ] **ANTI-PATTERN: Using `sol.*` APIs from Lua.** `sol` is a C++ binding layer and does not exist at Lua runtime. Never call `sol.set_function`, `sol.state`, etc. from Lua scripts.

- [ ] **ANTI-PATTERN: Spawning static geometry for gameplay objects.** Use `SpawnStaticBox/Sphere/Capsule` ONLY for permanent cabinet geometry (walls, ramps, shelves). Use `SpawnDynamicSphere/Box/Capsule` for any body that moves during gameplay (balls, projectiles, tokens)."""

if "### ANTI-PATTERNS (NEVER DO THESE)" not in text:
    # Append before the module export section
    old_export = "### Module Export & Sandbox Safety (Critical)"
    if old_export in text:
        text = text.replace(old_export, anti_patterns + "\n\n" + old_export, 1)
        MODS += 1
        print("[FIX #5] Added ANTI-PATTERNS section to docs/rules_lua.md")
    else:
        text += "\n" + anti_patterns
        MODS += 1
        print("[FIX #5] Appended ANTI-PATTERNS to docs/rules_lua.md (fallback)")

rl.write_text(text, encoding="utf-8")

# =====================================================================
# FIX #5b: Add ANTI-PATTERNS to the bridge cheatsheet in _helpers_exec.py
# =====================================================================
text = eh.read_text(encoding="utf-8")

# Find the bridge cheatsheet section in _helpers_exec.py
old_cheatsheet_end = '                "Lifecycle: OnLoadStatic() / OnLoad() / OnUnload() \\u2014 bare globals, no return.\\n'
new_cheatsheet_end = (
    '                "Lifecycle: OnLoadStatic() / OnLoad() / OnUnload() \\u2014 bare globals, no return.\\n'
    '                "ANTI-PATTERNS (ALWAYS WRONG):\\n'
    '                "  - DO NOT put SpawnDynamic* calls at module root level (crashes engine)\\n'
    '                "  - DO NOT define function OnStep(dt) at module level; use MidwayPhysics.OnStep(function(dt)...end)\\n'
    '                "  - DO NOT create duplicate OnLoadStatic() / OnLoad() functions\\n'
    '                "  - DO NOT cache AttractionConstants.modifiers at module level; read inside OnStep\\n'
)

if old_cheatsheet_end in text and "ANTI-PATTERNS (ALWAYS WRONG)" not in text:
    text = text.replace(old_cheatsheet_end, new_cheatsheet_end, 1)
    MODS += 1
    print("[FIX #5b] Added ANTI-PATTERNS to bridge cheatsheet in _helpers_exec.py")
else:
    print("[FIX #5b] WARN: Could not update bridge cheatsheet")

eh.write_text(text, encoding="utf-8")

# =====================================================================
# FIX #3: Verify wave serialization already works (read-only check)
# =====================================================================
ws = ROOT / "mesh_wave_sorter.py"
text = ws.read_text(encoding="utf-8")

if "Single-File Serialization" in text:
    print("[FIX #3] OK: Single-file serialization already implemented in mesh_wave_sorter.py")
    MODS += 1
else:
    print("[FIX #3] WARN: Single-file serialization NOT found!")
    
# Also verify target_file propagation from tasks to the wave sorter
# Check if mesh_tasks.py passes target_file correctly
mt = ROOT / "mesh_tasks.py"
if mt.exists():
    mt_text = mt.read_text(encoding="utf-8")
    if '"target_file"' in mt_text or "'target_file'" in mt_text:
        print("[FIX #3] OK: target_file is propagated in mesh_tasks.py")

print(f"\n{'='*50}")
print(f"Applied {MODS} fix(es) successfully.")
print(f"{'='*50}")
