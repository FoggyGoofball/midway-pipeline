# Midway Pipeline Master Roadmap
*Status: Active*

## Phase 0: Core Stabilization (Resumed)
- [x] **Task 1 (Hard-Anchor):** Completed.
- [x] **Task 2 (Phase Reorder):** Completed.
- [x] **Task 3 (Ground Blueprint):** Completed.
- [x] **Task 4 (Scope Regex):** Completed.
- [x] **Task 5 (Auto-Feed Fallthrough):** Completed.
- [x] **Task 6 (SOS Prompt):** Implemented in `_pipeline_helpers.py` `generate_failure_report` (lines 791-813) — SOS block with user prompt, deadlock context, vetos/objects, and offending draft code.
- [x] **Task 7 (Universal Logging):** `docs/rules_logging.md` created in sibling `midway` dir. `find_relevant_files` hardcodes `"docs/rules_logging.md"` (line 651). `REVIEW_SYSTEM` in `_prompts.py` has OBSERVABILITY MANDATE (lines 58-63).

## Phase 1: I/O Resiliency
- [x] **Task 8 (Array Fetch):** `handle_file_read` (lines 487-532) and `handle_file_list` (lines 565-585) support comma-separated paths. `AGENT_FILE_TOOLS_PROMPT` (lines 409-428) documents usage.
- [x] **Task 9 (Targeted Line Reading):** `_read_single_file` (lines 432-484) detects `lines N-M` bounds and slices content, bypassing the 6k truncation.

## Phase 2: Memory & Human-in-the-Loop (HITL)
- [x] **Task 10 (User-Gated Ledgers):** `run_tasks` in `mesh_loops.py` has no `_append_to_ledger` call. `_handle_approved` in `mesh_finalize.py` has terminal `input()` gate before ledger save.
- [x] **Task 11 (Timeline Archiver):** `_handle_flush_signal` in `mesh_finalize.py` detects `[FLUSH]`, archives last 50 timeline entries to `architecture_ledger.md`, and wipes the timeline.


## Phase 3 & 4: Production Safety & Consensus (PENDING)
- [ ] **Task 12 (Diff Merging):** Refactor Coder prompts for Unified Diff output.
- [ ] **Task 13 (Hash Locking):** Abort merges if file MD5 hashes change.
- [ ] **Task 14 (Appellate Court):** Tribunal agent to blind-review VETOs.
- [ ] **Task 15 (Amicus Curiae):** Terminal user prompt to break Appellate deadlocks.
