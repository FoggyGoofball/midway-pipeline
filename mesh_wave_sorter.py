"""
mesh_wave_sorter.py  Topological DAG wave sorter
==================================================
Extracted from mesh_loops.py to keep individual files under 1 000 lines.

Provides sort_tasks_into_waves(), which groups a flat task list into
dependency-ordered waves so each wave can run independently.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from models import PipelineContext


def sort_tasks_into_waves(
    tasks_list: List[Dict[str, Any]],
    ctx: "Optional[PipelineContext]" = None,
) -> List[List[Dict[str, Any]]]:
    """Topological sort: group tasks into waves of independent tasks.

    Each task dict may have a 'depends_on' key (list of task IDs it depends on).
    Groups tasks into waves where all tasks in a wave can run in parallel.

    **Single-File Serialization**: If multiple tasks share the exact same
    'target_file' (or 'output_file'), they are forced into sequential waves
    (one task per wave per file). This prevents patch conflicts when multiple
    agents try to SEARCH/REPLACE the same file concurrently.

    **Dirty Flag Re-Evaluation**: When the File-Linearizer in the Director phase
    overwrites depends_on, it sets ``ctx._tasks_dirty = True``. This function
    checks that flag and, when set, pre-processes the task list to inject
    cross-file depends_on chains BEFORE the topological sort so the linearized
    ordering is respected by the wave computation.

    Args:
        tasks_list: List of task dicts with 'id', 'domain', 'title', 'depends_on'.
        ctx:        Optional PipelineContext — used to merge cartridge domain
                    registry for model-affinity sorting within each wave.

    Returns:
        List of waves, each wave being a list of task dicts.
    """
    if not tasks_list:
        return []

    # ── Dirty-Flag Pre-Processing ────────────────────────────────────────
    # When the File-Linearizer has overwritten depends_on, re-evaluate all
    # single-file task groups and inject synthetic cross-task depends_on
    # chains so the topological sort inherits the linearized ordering.
    # Without this, tasks whose depends_on was changed AFTER enrichment
    # but whose original depends_on said "None" would all land in wave 1.
    if ctx is not None and getattr(ctx, '_tasks_dirty', False):
        print("  [Wave Sorter] 🔄 Dirty flag detected — re-evaluating file-linearized depends_on")
        _file_groups: Dict[str, List[Dict[str, Any]]] = {}
        _ungrouped: List[Dict[str, Any]] = []
        for _t in tasks_list:
            _tf = _t.get("target_file", "") or _t.get("output_file", "") or ""
            if _tf:
                _file_groups.setdefault(_tf, []).append(_t)
            else:
                _ungrouped.append(_t)

        for _tf, _group in _file_groups.items():
            if len(_group) <= 1:
                continue
            _group.sort(key=lambda x: int(x["id"]) if x["id"].isdigit() else x["id"])
            _prev_id: Optional[str] = None
            for _t in _group:
                _original_deps = _t.get("depends_on") or []
                if _prev_id is not None and _prev_id not in _original_deps:
                    _t["depends_on"] = list(set(_original_deps + [_prev_id]))
                    print(f"  [Wave Sorter] 🔗 Injected synthetic depends_on [{_prev_id}] for task '{_t['id']}' (file '{_tf}')")
                _prev_id = _t["id"]

        ctx._tasks_dirty = False
        print("  [Wave Sorter] ✅ Dirty flag cleared — synthetic depends_on injected")

    id_to_task: Dict[str, Dict[str, Any]] = {t["id"]: t for t in tasks_list}
    remaining = list(tasks_list)
    waves: List[List[Dict[str, Any]]] = []
    processed_ids: set = set()

    while remaining:
        # A task is ready when all its dependencies have been processed
        ready = [
            t for t in remaining
            if all(dep in processed_ids for dep in (t.get("depends_on") or []))
        ]
        if not ready:
            # Cycle detected or unresolvable deps — dump everything remaining
            ready = remaining[:]

        # -- Single-File Serialization Pass ------------------------------------
        # If two tasks in this wave target the same file, keep only ONE per file
        # in the current wave and defer the rest to the next wave.
        _file_seen: Dict[str, str] = {}  # target_file -> task_id kept in this wave
        _deferred: list = []
        for _t in ready:
            _tf = _t.get("target_file", "") or _t.get("output_file", "") or ""
            if not _tf:
                continue  # no file constraint — always ready
            if _tf in _file_seen:
                # Same file collision: defer this task to next wave
                _deferred.append(_t)
                print(f"  [Wave Sorter] 🔒 Deferred task '{_t['id']}' to next wave "
                      f"(file '{_tf}' already claimed by '{_file_seen[_tf]}')")
            else:
                _file_seen[_tf] = _t["id"]

        if _deferred:
            # Remove deferred tasks from ready list
            ready = [t for t in ready if t not in _deferred]
            # Deferred tasks are already in remaining (they were never removed);
            # they will surface naturally in the next iteration.
            # If ready is now empty after deferral, skip this wave entirely
            if not ready:
                continue

        waves.append(ready)
        for t in ready:
            processed_ids.add(t["id"])
            remaining.remove(t)

    # -- Model-affinity sort within each wave ---------------------------------
    # Sort tasks inside every wave by their resolved model name so that all
    # tasks sharing the same model run consecutively. This minimises VRAM
    # churn caused by back-to-back model evictions when the pipeline executes
    # tasks sequentially within a wave (single model in VRAM at any time).
    # Uses the merged registry (cartridge + kernel) so cartridge-only domains
    # get their real model affinity rather than falling through to empty string.
    try:
        from domain_registry import ALL_DOMAINS as _KERNEL_DOMAINS
        _cartridge_registry = getattr(ctx, 'domain_registry', {}) if ctx is not None else {}
        _merged_sort_registry = {**_KERNEL_DOMAINS, **_cartridge_registry}

        def _task_model(t: Dict[str, Any]) -> str:
            domain_meta = _merged_sort_registry.get(t.get("domain", ""), {})
            return domain_meta.get("model", t.get("model", ""))

        waves = [sorted(w, key=_task_model) for w in waves]
    except Exception:
        pass  # never break wave sorting due to import or key error

    return waves
