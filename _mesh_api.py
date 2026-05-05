"""
_mesh_api.py — Mesh work queue API, conflict resolution, progressive output.
Extracted from pipeline.py to reduce its size to ~800 lines.

Exports: submit_mesh_task, get_mesh_task_status, list_mesh_tasks,
cancel_mesh_task, get_mesh_work_queue, get_mesh_results,
resolve_conflict, _generate_failure_report_rest,
register_progress_listener, _emit_progress

No async/await — purely synchronous.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# Lazy-imported at call time to avoid circular dependency:
#   from pipeline import _CTX, call_ollama, ALL_DOMAINS, OLLAMA_HOST, ...


# ── Mesh Work Queue API ─────────────────────────────────────────────────────
# These use _CTX (PipelineContext singleton) for shared mutable state.

def submit_mesh_task(task_type: str, payload: dict, priority: int = 0,
                      _CTX=None, _call_ollama=None, _ALL_DOMAINS=None) -> str:
    """Submit a new mesh task via _CTX. Returns a task_id."""
    from pipeline import _CTX as _default_CTX
    from pipeline import call_ollama as _default_call
    from pipeline import ALL_DOMAINS as _default_DOMAINS
    ctx = _CTX or _default_CTX
    task_id = f"mesh_{datetime.now().strftime('%H%M%S%f')}_{hash(str(payload)) % 10000}"
    ctx.mesh_task_registry[task_id] = {
        "task_id": task_id,
        "task_type": task_type,
        "payload": payload,
        "priority": priority,
        "status": "queued",
        "created": datetime.now().isoformat(),
        "completed": None,
        "error": None,
    }
    ctx.mesh_work_queue.append(task_id)
    # Re-sort by priority: higher priority first
    sorted_list = sorted(
        list(ctx.mesh_work_queue),
        key=lambda tid: ctx.mesh_task_registry.get(tid, {}).get("priority", 0),
        reverse=True,
    )
    ctx.mesh_work_queue.clear()
    ctx.mesh_work_queue.extend(sorted_list)
    print(f"  [MeshQueue] Submitted {task_id} (type={task_type}, priority={priority})")
    return task_id


def get_mesh_task_status(task_id: str, _CTX=None) -> dict:
    """Get the status of a submitted mesh task via _CTX."""
    from pipeline import _CTX as _default_CTX
    ctx = _CTX or _default_CTX
    return ctx.mesh_task_registry.get(task_id)


def list_mesh_tasks(_CTX=None) -> list:
    """List all mesh tasks in the registry via _CTX."""
    from pipeline import _CTX as _default_CTX
    ctx = _CTX or _default_CTX
    return list(ctx.mesh_task_registry.values())


def cancel_mesh_task(task_id: str, _CTX=None) -> bool:
    """Cancel a queued mesh task via _CTX. Returns True if cancelled."""
    from pipeline import _CTX as _default_CTX
    ctx = _CTX or _default_CTX
    if task_id in ctx.mesh_task_registry:
        entry = ctx.mesh_task_registry[task_id]
        if entry["status"] in ("queued",):
            entry["status"] = "cancelled"
            entry["completed"] = datetime.now().isoformat()
            if task_id in ctx.mesh_work_queue:
                temp = [t for t in ctx.mesh_work_queue if t != task_id]
                ctx.mesh_work_queue.clear()
                ctx.mesh_work_queue.extend(temp)
            print(f"  [MeshQueue] Cancelled {task_id}")
            return True
    return False


def get_mesh_work_queue(_CTX=None) -> list:
    """Get the current work queue as a list of task IDs with metadata via _CTX."""
    from pipeline import _CTX as _default_CTX
    ctx = _CTX or _default_CTX
    return [
        {"task_id": tid, **{k: v for k, v in ctx.mesh_task_registry.get(tid, {}).items() if k != "payload"}}
        for tid in ctx.mesh_work_queue
    ]


def get_mesh_results(_CTX=None) -> list:
    """Get all completed mesh results via _CTX."""
    from pipeline import _CTX as _default_CTX
    ctx = _CTX or _default_CTX
    return [
        {"task_id": tid, "output": output, "completed": ctx.mesh_task_registry.get(tid, {}).get("completed")}
        for tid, output in ctx.mesh_results.items()
    ]


# ── Conflict Resolution ────────────────────────────────────────────────────

def resolve_conflict(agent_a_code: str, agent_b_code: str,
                     veto_justification: str, feature_request: str,
                     _call_ollama=None, _ALL_DOMAINS=None) -> Any:
    """Resolve a conflict between two agents' code using the CONF agent.

    Returns a ConsensusResult with verdict and merged code.
    """
    from pipeline import call_ollama as _default_call
    from pipeline import ALL_DOMAINS as _default_DOMAINS
    from models import ConsensusResult
    call = _call_ollama or _default_call
    domains = _ALL_DOMAINS or _default_DOMAINS
    import re

    prompt = (
        f"## Original Feature Request\n{feature_request}\n\n"
        f"## VETO Justification\n{veto_justification}\n\n"
        f"## Agent A Code\n{agent_a_code}\n\n"
        f"## Agent B Code\n{agent_b_code}\n\n"
        f"Resolve this conflict. Output one of:\n"
        f"- **SUSTAIN** VETO: Agent B's original is more correct\n"
        f"- **OVERRULE** VETO: Agent A's change is technically correct\n"
        f"- **COMPROMISE** with a merged version\n\n"
        f"Then output the final merged code under ## Merged Code."
    )

    result_text = call(
        domains["CONF"]["system_prompt"],
        prompt,
        "Conflict Resolution (API)",
    )

    # Parse verdict
    verdict = "COMPROMISE"
    if "SUSTAIN" in result_text.upper():
        verdict = "SUSTAIN"
    elif "OVERRULE" in result_text.upper():
        verdict = "OVERRULE"

    merged_code = ""
    code_match = re.search(
        r"## Merged Code\s*\n```(?:\w+)?\s*\n(.*?)```",
        result_text, re.DOTALL,
    )
    if code_match:
        merged_code = code_match.group(1).strip()

    return ConsensusResult(
        verdict=verdict,
        merged_code=merged_code or result_text,
        explanation=result_text[:500],
    )


# ── REST API Failure Report ─────────────────────────────────────────────────

def _generate_failure_report_rest(task_id: str, error_details: str,
                                   _CTX=None, _OLLAMA_HOST=None, _EXECUTION_MODEL=None) -> str:
    """Generate a failure report from the REST API (2-arg signature) via _CTX."""
    from pipeline import _CTX as _default_CTX
    from pipeline import OLLAMA_HOST as _default_HOST
    from pipeline import EXECUTION_MODEL as _default_MODEL
    ctx = _CTX or _default_CTX
    host = _OLLAMA_HOST or _default_HOST
    model = _EXECUTION_MODEL or _default_MODEL
    task_data = ctx.mesh_task_registry.get(task_id, {})
    return (
        f"## Pipeline Failure Report (Mesh API)\n\n"
        f"**Task ID:** {task_id}\n"
        f"**Task Type:** {task_data.get('task_type', 'unknown')}\n"
        f"**Error Details:** {error_details}\n\n"
        f"### Suggested Action\n"
        f"1. Check Ollama is running at {host}\n"
        f"2. Verify the model '{model}' is pulled\n"
        f"3. Re-run with more specific constraints\n"
        f"4. Check docs/ for relevant API references\n\n"
        f"### Cross-Reference\n"
        f"- docs/rules_cpp.md\n"
        f"- docs/rules_lua.md\n"
        f"- docs/rules_phys.md\n"
        f"- docs/rules_shader.md\n"
        f"- docs/engine_lua_bridge_contract.md\n"
    )


# ── Progressive Output Support ──────────────────────────────────────────────

def register_progress_listener(callback, _CTX=None):
    """Register a callback for progressive output updates via _CTX."""
    from pipeline import _CTX as _default_CTX
    ctx = _CTX or _default_CTX
    ctx.progress_listeners.append(callback)


def _emit_progress(phase: str, status: str, detail: str = "", _CTX=None):
    """Emit a progress update to all registered listeners via _CTX."""
    from pipeline import _CTX as _default_CTX
    ctx = _CTX or _default_CTX
    for cb in ctx.progress_listeners:
        try:
            cb(phase, status, detail)
        except Exception:
            pass
