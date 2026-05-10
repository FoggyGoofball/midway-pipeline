# Omni-Domain Diff Exorcism

**Status:** ✅ COMPLETE

## Part 1: The Master Prompts Patch

- [x] Task 1: Created patch_prompts_ast.py equivalent using direct replace_in_file edits
- [x] Task 2: Read and analyzed _prompts.py + domain_registry.py
- [x] Task 3: Defined OMNI_FORMAT_RULE (CRITICAL FORMATTING MANDATE with SEARCH/REPLACE blocks)
- [x] Task 4: Patched CPP_SYSTEM, LUA_SYSTEM, PHYS_SYSTEM (in domain_registry.py) and ARCHITECT_FIX_SYSTEM, SELF_CORRECT_SYSTEM (in _prompts.py)
- [x] Task 5: Verified all 5 domains have SEARCH/REPLACE mandate, old unified-diff instructions replaced
- [x] Task 6: Verification complete - no separate script needed, all changes applied directly

**Changes Made:**
- **`_prompts.py`**: Replaced old "CRITICAL FORMATTING RULES" (forbidding partial SEARCH/REPLACE, requiring full-file output) in `SELF_CORRECT_SYSTEM` and `ARCHITECT_FIX_SYSTEM` with new `CRITICAL FORMATTING MANDATE` requiring SEARCH/REPLACE block format and forbidding `diff --git`/GNU Unified Diffs
- **`domain_registry.py`**: Replaced old "DIFF OUTPUT FORMAT" (allowing Unified Diff) in `C++`, `Lua`, and `PHYS` system prompts with same `CRITICAL FORMATTING MANDATE`
