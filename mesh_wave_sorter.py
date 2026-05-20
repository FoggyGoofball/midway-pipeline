"""
mesh_wave_sorter.py — Topological DAG wave sorter
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

    Args:
        tasks_list: List of task dicts with 'id', 'domain', 'title', 'depends_on'.
        ctx:        Optional PipelineContext — used to merge cartridge domain
                    registry for model-affinity sorting within each wave.

    Returns:
        List of waves, each wave being a list of task dicts.
    """
    if not tasks_list:
        return []

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

        waves.append(ready)
        for t in ready:
            processed_ids.add(t["id"])
            remaining.remove(t)

    # ── Model-affinity sort within each wave ─────────────────────────────────
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
