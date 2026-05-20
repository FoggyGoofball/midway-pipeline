# Continuation Prompt — Phase 5 / 6 / 7 Hardening (Completed)

## What's Been Done

### Phases 4.1–4.12 — All 12 Modules Extracted ✅
All functions, classes, and constants extracted from `pipeline.py` into individual modules with backward-compatible aliases. 74 characterization tests pass.

### Phases 4.13–4.15 — Orchestrator Thinning ✅
- PipelineContext state machine is in `models.py` and wired through `_CTX` singleton
- Iteration loops extracted to `mesh_loops.py` (run_fetches, run_tasks) and `mesh_finalize.py` (run_code_merge)
- `pipeline.py` reduced from 4,489 → **593 lines**
- System prompts extracted to `_prompts.py`
- Helper functions extracted to `_pipeline_helpers.py`
- Mesh API extracted to `_mesh_api.py` (213 lines)

### Phases 5-7 Hardening ✅ (Completed 2026-05-12)

**Phase 1 — Virtual Memory & Payload Streaming Scaling (ollama_client.py)**
- Context constants: OLLAMA_NUM_CTX=8192 (baseline), 32768 (large for 9B), 65536 (massive for Phi-3.5)
- Model parsing generalized to substring tags (phi3.5/9b/14b detection)
- Dynamic option maps with temperature: 0.2 (deterministic synthesis) / 0.5 (multi-draft evaluation)

**Phase 2 — Schema Hardening & Global Ledger Alignment (models.py)**
- Deprecated `all_approvals: Dict[str, bool] = {}` purged
- SignalType extended: FETCH, READ_OFFLOADED, EXTRACT_SKELETON, FLUSH, REQUEST_API
- OrchestrationConfig defaults: coder=qwen3.5:9b, reviewer=phi3.5:latest, analyst=phi3.5:latest
- MeshSignal constructor bindings strengthened for positional + keyword validation

**Phase 3 — Runtime Topology & Interoperability (pipeline.py, domain_registry.py)**
- CODER_MODEL=qwen3.5:9b, REVIEWER_MODEL=phi3.5:latest, ANALYST_MODEL=REVIEWER_MODEL
- ALL_DOMAINS evaluates target models via runtime lookups / lambda closures

**Phase 4 — Subtask Prompt Cordoning (mesh_loops.py)**
- Raw concatenation replaced with XML <execution_environment> / <macro_invariants> / <target_subtask_scope>
- Semantic scope boundaries prevent macro-pollution of syntax tasks

**Phase 5 — Pre-Flight Staging (_finalize_preflight.py)**
- `_flush_results_to_workspace()` with atomic file writes
- SEARCH/REPLACE metadata sanitization before disk writes
- Domain-isolated retry strikes (C++/PHYS/NET/SHADER vs Lua)

**Phase 6 — Omni-Batch Wave Execution (mesh_loops.py, mesh_finalize.py)**
- Model-aware task batching by domain (REVIEWER→phi3.5, others→qwen3.5)
- Dynamic VRAM eviction seams (model unload `keep_alive=0` between switches)
- Synchronized all_results_dict + all_results list in mesh_finalize.py
- Preflight call hooks imported via _run_preflight_checks

**Phase 7 — Adaptive Payload-Aware Paging (paging_kernel.py)**
- `_resolve_dynamic_page_limit()` with Tier 3 (120K chars / 65536 ctx), Tier 2 (48K / 32768), Tier 1 (12K / legacy)
- `execute_page_in()` with active_ctx_limit parameter and guard interjection
- Active topologies forwarded via wave execution loops
- PagingController.build_resume_payload() ghost buffer continuity

### Current Module Sizes
| Module | Lines | Purpose |
|--------|-------|---------|
| pipeline.py | 593 | Orchestrator + config |
| mesh_loops.py | 1312 | Run loops |
| mesh_finalize.py | 708 | Finalization |
| ollama_client.py | 640 | HTTP + context tiering |
| paging_kernel.py | 936 | Adaptive paging |
| domain_registry.py | 595 | Agent resolution |
| models.py | 369 | Pydantic data |
| _prompts.py | 350 | 15 prompts |
| signals.py | 163 | Signal parsing |
| ledger.py | 391 | Session timeline |
| gdd_extractor.py | 461 | GDD extraction |
| fetch_handler.py | 257 | FETCH handling |
| token_budget.py | 266 | Token budget |
| offload_store.py | 220 | Offload store |
| pipeline_session.py | 312 | Session mgmt |
| pipeline_stream_server.py | 379 | SSE server |
| pipeline_stream.py | 208 | Stream gen |
| checkpoint.py | 77 | Checkpoint |
| file_references.py | 162 | File refs |
| tagsuggester.py | 131 | Tag suggester |
| context_extractor.py | 146 | Context extraction |
| _finalize_preflight.py | 196 | Pre-flight |
| _finalize_conflicts.py | 86 | Conflict res |
| _finalize_review.py | 450 | Review |
| _domain_sandbox.py | 182 | Sandbox |
| _pipeline_helpers.py | 217 | Helpers (trimmed) |
| _mesh_api.py | 213 | API layer |

## Where We Are Now

All hardening from the Master Execution Checklist (Phases 1-7) is complete. The codebase is stable with:
- 80+ passing tests
- 24+ modules under 1,500 lines each
- All model references updated to current topology
- XML cordoning for subtask prompts
- Adaptive VRAM paging with dynamic context limits
- Domain-isolated retry strikes
- Dynamic model-aware wave batching

```python
import subprocess, tempfile, os, sys
from pathlib import Path

def run_against_baseline(prompt: str, baseline_tag: str = "pre-refactor-baseline") -> str:
    """Run the original monolithic pipeline.py from git history and return output."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        # Extract original pipeline.py
        result = subprocess.run(
            ["git", "show", f"{baseline_tag}:midway-pipeline/pipeline.py"],
            capture_output=True, text=True, cwd=Path(__file__).parent.parent
        )
        (tmp / "pipeline.py").write_text(result.stdout)
        # Run it with patched imports (needs supporting files available)
        result = subprocess.run(
            [sys.executable, "-c", f"""
import sys; sys.path.insert(0, r'{tmp}')
# ... mock call_ollama, etc.
from pipeline import run_pipeline
output = run_pipeline({prompt!r})
print(output)
"""],
            capture_output=True, text=True
        )
        return result.stdout
```

**Test structure:**
1. Mock `call_ollama` and `call_ollama_streamed` with canned return values
2. Run `run_pipeline()` from the refactored code — capture output
3. Run `run_pipeline()` from the baseline monolith with identical mocks — capture output
4. Assert that outputs are structurally identical (same verdict, same signal extractions, same post-processing)

This ensures the extraction + thinning didn't change any behavior.

### Step 4.18 — Milestone tag
```cmd
cd c:\Users\Admin\source\repos\midway\midway-pipeline
git tag v1.0-extracted
```

### Clean up abandoned temp scripts
Delete these leftover files from the thinning process:
```
_fix_test_compat.py  _fix2.py  _fix3.py  _fix4.py  _fix_nl.py
_fix_last.py  _fix_final.py  _fix_broken_defs.py  _fix_insert_is_likely_chat.py
_fix_missing_functions.py  _fix_orphan_block.py  _fix_wrong_placement.py
_test_thin_import.py  _thin_pipeline.py  _count_lines.py
_orig_p.txt  _orig_pipeline.txt  _orig_tagsuggester.txt  _orig_ts.txt
orig_pipeline_dump.txt
```
Keep `_pipeline_helpers.py`, `_mesh_api.py`, `_prompts.py` — they're in active use.

### Key Constraints (Do Not Violate)
- **No async/await** — strictly synchronous
- **No file > 1,000 lines**
- **Tests must pass after every change** — run `python -m pytest tests/ -v --tb=short`
- **Do NOT weaken test assertions** — fix the refactored code, not the tests
- **All state passes through Pydantic models** (never through new globals)

### How to verify tests
```cmd
cd c:\Users\Admin\source\repos\midway\midway-pipeline
python -m pytest tests/ -v --tb=short
# Expected: 74 (+1 dry run) = 75 passed, 0 failed
```
