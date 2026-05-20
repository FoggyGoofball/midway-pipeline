"""
mesh_loops.py — Thin re-export shim
=====================================
This module was refactored below the 1 000-line target by migrating each
major phase into a dedicated module:

    mesh_wave_sorter.py   -> sort_tasks_into_waves()
    mesh_fetches.py       -> run_fetches()   (Phases 0.5-3)
    mesh_tasks.py         -> run_tasks()     (Phase 4)
    mesh_finalize.py      -> run_code_merge()

All names that pipeline.py (and any other caller) previously imported from
this module are re-exported here unchanged so the public API is unbroken.

Import direction (no cycles):
    pipeline.py
        +-> mesh_loops          (this shim, lazy-loaded)
                +-> mesh_fetches      -> _pipeline_helpers, pipeline (one-way)
                +-> mesh_tasks        -> _pipeline_helpers, pipeline, mesh_wave_sorter
                +-> mesh_wave_sorter  -> domain_registry  (no upward deps)
                +-> mesh_finalize     -> _pipeline_helpers
"""

from __future__ import annotations

# -- Phase 0.5-3: Scope gate / blueprint / director --------------------------
from mesh_fetches import run_fetches

# -- Phase 4: Wave-based task execution & signal handling --------------------
from mesh_tasks import run_tasks, _process_task_signals

# -- DAG wave sorter (re-exported for any legacy callers) --------------------
from mesh_wave_sorter import sort_tasks_into_waves

# -- Phase 5: Code merge & finalisation --------------------------------------
from mesh_finalize import run_code_merge

__all__ = [
    "run_fetches",
    "run_tasks",
    "_process_task_signals",
    "sort_tasks_into_waves",
    "run_code_merge",
]
