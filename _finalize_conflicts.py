"""
_finalize_conflicts.py — Phase 5: Conflict Resolution
======================================================
Extracted from mesh_finalize.py — handles VETO and OBJECT signal resolution.

Exported:
    _run_conflict_resolution(ctx) -> PipelineContext
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from models import PipelineContext
from pipeline import ALL_DOMAINS, call_ollama, resolve_agent_name


# ──────────────────────────────────────────────────────────────────────
#  Phase 5: Conflict Resolution
# ──────────────────────────────────────────────────────────────────────

def _run_conflict_resolution(ctx: PipelineContext) -> PipelineContext:
    """Phase 5 conflict resolution — resolve VETO and OBJECT signals
    between domain agents via the CONF (Conflict Resolution) domain agent."""
    print(f"\n{'='*70}")
    print(f"  Phase 5: Conflict Resolution")
    print(f"{'='*70}")
    ctx.output_parts.append("\n## Phase 5: Conflict Resolution\n")

    ctx.conflict_resolutions = []
    for veto in ctx.all_vetos:
        target = resolve_agent_name(veto["target"])
        from_agent = resolve_agent_name(veto["from"])

        conflict_prompt = (
            f"## VETO Signal\n"
            f"**From:** {ALL_DOMAINS.get(from_agent, {}).get('name', from_agent)}\n"
            f"**Target:** {ALL_DOMAINS.get(target, {}).get('name', target)}\n"
            f"**Reason:** {veto['reason']}\n\n"
            f"## Original Feature Request\n{ctx.user_prompt}\n\n"
            f"## Director's Task Breakdown\n{ctx.director_output}\n\n"
        )

        if veto["task_id"] in ctx.all_results_dict:
            conflict_prompt += (
                f"## Output that triggered VETO\n{ctx.all_results_dict[veto['task_id']]}\n\n"
            )

        conflict_output = call_ollama(
            ALL_DOMAINS["CONF"]["system_prompt"],
            conflict_prompt,
            f"Conflict Resolution: {veto['from']} vs {veto['target']}",
        )
        ctx.conflict_resolutions.append(conflict_output)
        ctx.output_parts.append(
            f"### VETO: {veto['from']} -> {veto['target']}\n{conflict_output}\n"
        )

    for obj in ctx.all_objects:
        target = resolve_agent_name(obj["target"])
        from_agent = resolve_agent_name(obj["from"])

        object_prompt = (
            f"## OBJECT Signal\n"
            f"**From:** {ALL_DOMAINS.get(from_agent, {}).get('name', from_agent)}\n"
            f"**Target:** {ALL_DOMAINS.get(target, {}).get('name', target)}\n"
            f"**Concern:** {obj['concern']}\n\n"
            f"## Original Feature Request\n{ctx.user_prompt}\n\n"
        )

        if obj["task_id"] in ctx.all_results_dict:
            object_prompt += (
                f"## Output that triggered OBJECT\n{ctx.all_results_dict[obj['task_id']]}\n\n"
            )

        object_output = call_ollama(
            ALL_DOMAINS["CONF"]["system_prompt"],
            object_prompt,
            f"Conflict Resolution: {obj['from']} OBJECTS {obj['target']}",
        )
        ctx.conflict_resolutions.append(object_output)
        ctx.output_parts.append(
            f"### OBJECT: {obj['from']} -> {obj['target']}\n{object_output}\n"
        )

    return ctx
