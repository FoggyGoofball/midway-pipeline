# Refactor Change Manifest

**Date:** 2026-05-12
**Plan:** Hardening the Midway MoE Orchestration Kernel (May 12)

---

## Phase I: Semantic Paging Core (MemGPT Alignment)

### Changes Made

| File | Change | Purpose |
|------|--------|---------|
| `models.py` | `PipelineContext.core_memory_table: Dict[str, str]` | Immutable core memory table anchored in persistent execution RAM |
| `token_budget.py` | `_block_aware_collapse()` excludes `core_memory_table` from eviction count | Prevents semantic anchors from being pruned during context window saturation |
| `offload_store.py` | `_generate_keywords()` appends auto-generated summary arrays to offloaded blocks | Enables sub-agents to precisely target offloaded content via `read_offloaded_file()` |

---

## Phase II: Speculative Multi-Draft Synthesis (MoA Alignment)

### Changes Made

| File | Change | Purpose |
|------|--------|---------|
| `_prompts.py` | `CODER_BASE_INSTRUCTIONS_MULTI_DRAFT` prompt directive added | Commands the primary model to stream alternative structural blocks bounded by `[OPTION_A]`/`[OPTION_B]` delimiters |
| `_pipeline_helpers.py` | `execute_task()` updated with multi-draft parsing | Intercepts dual-candidate markers, parses blocks apart, bypasses plain-text log append |
| `_mesh_api.py` | `resolve_conflict()` consensus API routing | Pipes split source arrays to 14B CONF expert persona for structural layout synthesis |
| `ledger.py` | `ensure_ledger_header()` + `_append_to_ledger()` integration | Applies synthetic tracking headers and writes merged output securely to disk |

---

## Phase III: AST Patch State Reducers (LangGraph Alignment)

### Changes Made

| File | Change | Purpose |
|------|--------|---------|
| `models.py` | `ASTPatch` Pydantic schema | Dedicated AST Patch model mapping replacement payloads to target file indices |
| `signals.py` | `AST_PATCH_BLOCK_PATTERN` regex signature | Captures `[AST_PATCH:path]```code```[/AST_PATCH]` blocks from agent output streams |
| `_finalize_review.py` | Phase 6 reducer ingestion in `_run_review_fix_loop()` | Extracts AST patches, validates paths, routes malformed payloads through `qwen2.5-coder:1.5b` syntax repair pass |

---

## Phase IV: Uncommitted Virtual Staging Filesystem (AIOS Alignment)

### Changes Made

| File | Change | Purpose |
|------|--------|---------|
| `_helpers_io.py` | `enable_staging()`, `disable_staging()`, `commit_staging()`, `is_staging_active()`, `get_staging_path()` | Global staging workspace that redirects all file writes to `.staging_workspace/` |
| `_helpers_io.py` | `atomic_write_text()` staging redirect | When staging active, writes are redirected to `.staging_workspace/` preserving relative path layout |
| `mesh_finalize.py` | `run_code_merge()` staging activation | Enables staging before review/fix loop begins |
| `mesh_finalize.py` | `_handle_approved()` staging commit | Atomic single-pass sync: mirrors `.staging_workspace/` → primary roots on approval |
| `mesh_finalize.py` | `_handle_blocked()` staging preservation | Disables staging but preserves staged files for inspection |

---

## Verification

Run the characterization suite to confirm stability:

```bash
python -m pytest tests/ -v --tb=short
```

Or via batch file:

```batch
run_tests.bat
```
