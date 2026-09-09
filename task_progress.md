# Bug Fix Implementation: Bugs P through U

> ✅ All six bugs verified implemented in code (checked 2026-09-02). This file was
> stale — the checkboxes previously showed `[ ]` while the fixes were already live.

- [x] Bug P: Fix monolithic fix prompt - add complete approved API list + bare names
      → `_finalize_review.py` — "Bug P: Use the single-source-of-truth bridge snippet builder" (`build_fix_bridge_snippet`), bare (MidwayPhysics-less) forms, and a comprehensive fallback API list.
- [x] Bug Q: Add bare-call phantom-API detection to runtime_sim.py
      → `runtime_sim.py:678` `_BARE_PHYSICS_CALL_RE` + `run_phantom_api_final_pass()` §3b "Bare-call phantom-API check (Bug Q)".
- [x] Bug R: Reduce fix cycles 4→2, add regression-abort logic
      → `pipeline.py:166` `REVIEW_MAX_ITERATIONS = 2`; circuit breaker (`retry_counts` / `CIRCUIT BREAKER TRIPPED`) + insanity detector in `_finalize_review.py`.
- [x] Bug S: Fix server-mode reconciliation gate (no TTY fallback)
      → `_finalize_review.py:1132` "Bug S: Check for forced-server-mode env variable FIRST" + `MIDWAY_FORCED_DETERMINISTIC` set in `pipeline_stream_server.py:29`.
- [x] Bug T: Fix blueprint coverage for monolithic mode
      → `_finalize_review.py:623` "Bug T: Report coverage" (monolithic task-count reporting + full-file regeneration path).
- [x] Bug U: Add anti-hallucination guard to review prompt
      → `_prompts.py` `build_review_system()` — "ANTI-HALLUCINATION RULE (ABSOLUTE)", "CRITICAL ANTI-HALLUCINATION GUARD", and "CRITICAL ANTI-PATTERN HALLUCINATION GUARD".
