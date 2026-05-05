# Continuation Prompt — Phase 4.17 to 4.18

## What's Been Done

### Phases 4.1–4.12 — All 12 Modules Extracted ✅
All functions, classes, and constants extracted from `pipeline.py` into individual modules with backward-compatible aliases. 74 characterization tests pass.

### Phases 4.13–4.15 — Orchestrator Thinning ✅
- PipelineContext state machine is in `models.py` and wired through `_CTX` singleton
- Iteration loops extracted to `mesh_loops.py` (run_fetches, run_tasks) and `mesh_finalize.py` (run_code_merge)
- `pipeline.py` reduced from 4,489 → **356 lines** (under 800 target)
- System prompts extracted to `_prompts.py`
- Helper functions extracted to `_pipeline_helpers.py` (566 lines — under 1,000 limit, OK)
- Mesh API extracted to `_mesh_api.py` (213 lines)

### Phase 4.16 — Tests Verified ✅
```
python -m pytest tests/ -v --tb=short
# → 74 passed in 1.15s
```

## What Needs To Be Done

### Step 4.17 — Add `test_full_pipeline_dry_run.py`
Add to `midway-pipeline/tests/` a regression test that exercises the full `run_pipeline()` with mocked LLM calls. The test must run **identically** against both the refactored pipeline AND the original monolith, then assert outputs match.

**How to run against the original monolith:**
The git tag `pre-refactor-baseline` (290fc8a) in this repo still has the original 4,489-line `pipeline.py`. You can extract it and run it in a temp directory:

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
