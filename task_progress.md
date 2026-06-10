# Option C: Forensic Cleanup & Hardening

## Part A — Quick Cleanup (stale artifacts)
- [x] Delete: apply_5_fixes.py, verify_5_fixes.py, fix_5b_final.py, apply_fix_gaps.py
- [x] Delete: fix_5b_cheatsheet.py, apply_security_patches.py, hardening_patches.py

## Part B — Architectural Hardening
- [ ] Fix _build_skeleton.py _ANCHOR_MARKERS — add TASK_1, TASK_2
- [ ] Fix _helpers_text.py _FILE_CTX_CHAR_BUDGET — 12000→24000 for 32K context
- [ ] Remove deprecated sig patterns (FETCH, READ_OFFLOADED, EXTRACT_SKELETON, MATH_EVAL) from signals.py
- [ ] Simplify recursive_librarian() in _helpers_exec.py
- [ ] Update test_import.py expectations for signal patterns
- [ ] Run tests and verify import chain still works
