# Day 5 Bugfix & Day 6 Scaffold
*Status: Active*

## Part 1: The Exorcism (Sys.Path Fix)
- [x] **Task 1 (Delete Ghost Files):** `_cleanup_ghosts.py` created, executed (deleted 3 ghosts from `..\midway\`: pipeline.py, pipeline_stream.py, pipeline_stream_server.py), then self-deleted.
- [x] **Task 2 (Fix Import Precedence):** `pipeline_stream_server.py` — replaced `sys.path.insert(0, PROJECT_ROOT)` with local dir at `sys.path[0]` and PROJECT_ROOT appended at lower priority. `pipeline_stream.py` had no sys.path manipulation, so no changes needed.

## Part 2: Day 6 Architecture Scaffold (The Wizard)
- [x] **Task 3 (Directory Scaffold):** `profiles/`, `global_cache/`, `global_cache/api_docs/` created. `global_cache/cache_index.json` initialized as `{}`.
- [x] **Task 4 (Wizard CLI Skeleton):** `wizard.py` created — accepts `--init <project_name>` (generates manifest.json) and `--ingest <url>` (placeholder).
- [x] **Task 5 (JIT Intercept Signal):** `REQUEST_API` pattern added to `SIGNAL_PATTERNS` in `signals.py:37`.
