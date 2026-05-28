"""
integration_schema.py — Cross-Agent Handle/Lifecycle Schema

Provides utilities for agents to declare their outputs into the live
IntegrationSchema and for the pipeline to detect conflicts before merge.

The schema acts as a shared coordination contract:
  - Agents READ it before writing code (injected via mesh_tasks.py context)
  - Agents DECLARE into it after producing output (parse their result text)
  - The conflict validator runs before the review-fix loop

Declaration parsing uses lightweight regex patterns against agent output text
rather than full AST parsing, so it works on any Lua output regardless of
formatting style.
"""

from __future__ import annotations

import re
from typing import List, Optional

from models import (
    IntegrationSchema,
    SchemaHandleEntry,
    SchemaConflict,
    PipelineContext,
)

# ── Declaration patterns ───────────────────────────────────────────────────────
# Match local handle assignments produced by MidwayPhysics spawn calls.
# Examples:
#   local hBall = MidwayPhysics.SpawnDynamicSphere(...)
#   local hGate = MidwayPhysics.SpawnStaticBox(...)
#   local hLane = MidwayPhysics.SpawnStaticCylinder(...)

_HANDLE_ASSIGN_RE = re.compile(
    r"\blocal\s+(h[A-Za-z0-9_]+)\s*=\s*MidwayPhysics\.\w+\s*\(",
    re.MULTILINE,
)

# Match variable declarations that look like shared state
# e.g.  local score = 0   /  local ballCount = 0
_SHARED_VAR_RE = re.compile(
    r"^local\s+([a-z][A-Za-z0-9_]+)\s*=\s*(?:0|false|true|\"|\{)",
    re.MULTILINE,
)

# Detect OnStep registration
_ONSTEP_RE = re.compile(r"\bMidwayPhysics\.OnStep\s*\(", re.MULTILINE)

# Detect OnLoad / OnLoadStatic bare-function definitions
_ONLOAD_RE = re.compile(r"\bfunction\s+(OnLoad(?:Static)?)\s*\(\s*\)", re.MULTILINE)


def _parse_declarations(task_id: str, lua_text: str) -> tuple[
    List[SchemaHandleEntry], List[str], bool, List[str]
]:
    """
    Parse a Lua agent output and extract:
      - handle entries (SchemaHandleEntry list)
      - shared variable names
      - whether the task registers an OnStep callback
      - lifecycle hooks declared (OnLoad / OnLoadStatic)
    """
    handles: List[SchemaHandleEntry] = []
    seen_names: set = set()

    for m in _HANDLE_ASSIGN_RE.finditer(lua_text):
        name = m.group(1)
        if name not in seen_names:
            seen_names.add(name)
            handles.append(SchemaHandleEntry(
                name=name,
                declared_by=task_id,
                lua_type="userdata",
                created_in="OnLoad",
            ))

    shared_vars: List[str] = []
    for m in _SHARED_VAR_RE.finditer(lua_text):
        shared_vars.append(m.group(1))

    has_onstep = bool(_ONSTEP_RE.search(lua_text))

    lifecycle_hooks: List[str] = []
    for m in _ONLOAD_RE.finditer(lua_text):
        lifecycle_hooks.append(m.group(1))

    return handles, shared_vars, has_onstep, lifecycle_hooks


def update_schema_from_task(
    ctx: PipelineContext,
    task_id: str,
    lua_text: str,
) -> List[SchemaConflict]:
    """
    Parse a completed task's output and register its declarations into
    ctx.integration_schema.  Returns any newly detected conflicts.

    Creates the schema if it does not yet exist (e.g. architect pass was skipped).
    """
    if ctx.integration_schema is None:
        ctx.integration_schema = IntegrationSchema()

    schema = ctx.integration_schema
    new_conflicts: List[SchemaConflict] = []

    handles, shared_vars, has_onstep, lifecycle_hooks = _parse_declarations(task_id, lua_text)

    for entry in handles:
        conflict = schema.declare_handle(entry)
        if conflict:
            new_conflicts.append(conflict)

    for var in shared_vars:
        if var not in schema.shared_vars:
            schema.shared_vars[var] = task_id

    if has_onstep and task_id not in schema.onstep_subscribers:
        schema.onstep_subscribers.append(task_id)

    if task_id not in schema.onload_order:
        schema.onload_order.append(task_id)

    return new_conflicts


def validate_schema_conflicts(ctx: PipelineContext) -> str:
    """
    Return a formatted error string for any unresolved conflicts in the schema.
    Empty string means no conflicts.  Injects into ctx.pre_flight_errors format.
    """
    if ctx.integration_schema is None:
        return ""

    conflicts = ctx.integration_schema.conflicts
    if not conflicts:
        return ""

    lines = ["\n## ⚡ Integration Schema Conflicts (must be resolved before merge)\n"]
    for c in conflicts:
        lines.append(
            f"  Handle `{c.name}` declared by MULTIPLE tasks: "
            + " and ".join(c.declared_by)
            + f"\n  → {c.reason}\n"
            "  Fix: choose one task as the sole owner and remove the declaration from the other.\n"
        )
    return "\n".join(lines)


def get_schema_context_block(ctx: PipelineContext) -> str:
    """
    Return a compact context block for injection into agent prompts.
    Returns empty string if no schema exists yet.
    """
    if ctx.integration_schema is None:
        return ""
    block = ctx.integration_schema.to_context_block()
    # Hard cap so it never dominates the context window
    if len(block) > 2000:
        block = block[:2000] + "\n  ... (schema truncated)"
    return block
