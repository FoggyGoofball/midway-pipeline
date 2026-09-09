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


## Phase 7: Adaptive Payload-Aware Paging Protocol (COMPLETED)
- [x] **Task 12 (Context-Tiered Boundaries):** Implemented `_resolve_dynamic_page_limit()` in `paging_kernel.py` — 120000/48000/12000 char caps mapped to 65536/32768/8192 token contexts.
- [x] **Task 13 (Payload-Aware Chunking):** `execute_page_in()` dynamically queries active context ceiling before byte transfers. Files exceeding cap require `<lines>` or `<search>` targeting tags.
- [x] **Task 14 (Active Topology Forwarding):** Wave handlers in `mesh_loops.py` extract model context capacities (65536/32768/8192) and forward to `extract_signals()` as `active_context_limit`.
- [x] **Task 15 (Ghost Buffer Continuity):** `PagingController.build_resume_payload()` appends pre-fault ghost buffers (`_ghost_buffer_text`) directly into active prompt payloads as contiguous assistant entries, not discrete turns.


## Phase 8: Structured Output Modernization (COMPLETED — verified 2026-09-02)
- [x] **Task 16 (Pydantic Schemas):** `structured_schemas.py` — rigid `BaseModel` contracts for architect design, task decomposition, review verdict, patches, syntax check, and intent classification.
- [x] **Task 17 (Instructor + Tenacity):** `structured_client.py` — instructor-wrapped extraction with tenacity retry (exponential backoff, 3 attempts) feeding the exact `ValidationError` text back for self-correction.
- [x] **Task 18 (Two-Turn Extraction):** `structured_client.two_turn_extract()` — raw chain-of-thought turn then schema extraction; wired into `mesh_architect.py` with graceful fallback to the legacy JSON parser.
- [x] **Task 19 (xgrammar Serving):** `serve_xgrammar.py` — vLLM/SGLang deployment using the `xgrammar` backend for constrained decoding driven by the Pydantic schemas.
- [x] **Task 20 (Locked QLoRA + dynamic seq):** `lora generator/lora_fine_tune.py` — `load_in_4bit=True`, `r=16`, batch 2, accumulation 4, `optim="adamw_8bit"`, `use_gradient_checkpointing="unsloth"`; `max_seq_length` computed from the longest training item.
- Full details: `STRUCTURED_ARCHITECTURE.md`
