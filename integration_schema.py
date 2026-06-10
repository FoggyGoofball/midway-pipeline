"""
integration_schema.py  Cross-Agent Handle/Lifecycle Schema

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

# -- Declaration patterns -------------------------------------------------------
# Pattern keys expected in the cartridge-provided schema_patterns dict:
#   "handle_assign"  — regex matching local handle assignments from spawn calls
#   "shared_var"     — regex matching local shared-variable declarations
#   "onstep"         — regex detecting OnStep registration calls
#   "onload"         — regex detecting OnLoad / OnLoadStatic function definitions
#
# These patterns are no longer hardcoded here. They are sourced from the active
# cartridge via Inversion of Control (IoC) so the kernel remains project-agnostic.


def _parse_declarations(
    task_id: str,
    lua_text: str,
    schema_patterns: dict = None,
) -> tuple[
    List[SchemaHandleEntry], List[str], bool, List[str]
]:
    """
    Parse a Lua agent output and extract:
      - handle entries (SchemaHandleEntry list)
      - shared variable names
      - whether the task registers an OnStep callback
      - lifecycle hooks declared (OnLoad / OnLoadStatic)

    Regex patterns are sourced from the ``schema_patterns`` dict provided by
    the active cartridge.  Expected keys:
        "handle_assign"  — captures handle name in group(1)
        "shared_var"     — captures variable name in group(1)
        "onstep"         — boolean match, group(1) not required
        "onload"         — captures hook name in group(1)

    If ``schema_patterns`` is None or any key is missing, the function returns
    empty results (empty lists, False) — graceful degradation.
    """
    handles: List[SchemaHandleEntry] = []
    seen_names: set = set()

    if schema_patterns is not None and "handle_assign" in schema_patterns:
        _re_handle = re.compile(schema_patterns["handle_assign"], re.MULTILINE)
        for m in _re_handle.finditer(lua_text):
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
    if schema_patterns is not None and "shared_var" in schema_patterns:
        _re_var = re.compile(schema_patterns["shared_var"], re.MULTILINE)
        for m in _re_var.finditer(lua_text):
            shared_vars.append(m.group(1))

    has_onstep = False
    if schema_patterns is not None and "onstep" in schema_patterns:
        _re_onstep = re.compile(schema_patterns["onstep"], re.MULTILINE)
        has_onstep = bool(_re_onstep.search(lua_text))

    lifecycle_hooks: List[str] = []
    if schema_patterns is not None and "onload" in schema_patterns:
        _re_onload = re.compile(schema_patterns["onload"], re.MULTILINE)
        for m in _re_onload.finditer(lua_text):
            lifecycle_hooks.append(m.group(1))

    return handles, shared_vars, has_onstep, lifecycle_hooks


def _resolve_schema_patterns(ctx: PipelineContext) -> dict:
    """
    Resolve the schema regex patterns from the active cartridge.

    Checks two cartridge mounting paths:
      Path A — EcosystemCartridgeContract (mount_ecosystem) via
               ctx.mounted_cartridge.get_schema_patterns()
      Path B — class-based cartridge (mount_cartridge) via
               ctx._cartridge_schema_patterns

    Returns None if no cartridge or method is available — caller handles
    graceful degradation.
    """
    # Path A: EcosystemCartridgeContract instance
    cartridge = getattr(ctx, "mounted_cartridge", None)
    if cartridge is not None and hasattr(cartridge, "get_schema_patterns"):
        return cartridge.get_schema_patterns()
    # Path B: class-based cartridge mount
    if hasattr(ctx, "_cartridge_schema_patterns"):
        return ctx._cartridge_schema_patterns
    return None


def update_schema_from_task(
    ctx: PipelineContext,
    task_id: str,
    lua_text: str,
) -> List[SchemaConflict]:
    """
    Parse a completed task's output and register its declarations into
    ctx.integration_schema.  Returns any newly detected conflicts.

    Schema regex patterns are sourced from the active cartridge via
    IoC.  If no cartridge is mounted or the cartridge provides no patterns,
    the schema is created but no declarations are parsed (no crash).

    Creates the schema if it does not yet exist (e.g. architect pass was skipped).
    """
    if ctx.integration_schema is None:
        ctx.integration_schema = IntegrationSchema()

    schema = ctx.integration_schema
    new_conflicts: List[SchemaConflict] = []

    schema_patterns = _resolve_schema_patterns(ctx)
    handles, shared_vars, has_onstep, lifecycle_hooks = _parse_declarations(
        task_id, lua_text, schema_patterns
    )

    # -- Guard: Task 2+ must not redefine lifecycle hooks --
    # Downstream agents must patch the Task 1 scaffold via SEARCH/REPLACE,
    # not declare duplicate OnLoad/OnLoadStatic entry points.
    # Checks both "task_1" (lowercase pipeline convention) and "Task 1"
    # (human-readable convention) to be safe regardless of task_id format.
    _is_task_1 = (
        task_id
        and (
            task_id.lower().startswith("task_1")
            or task_id.lower().startswith("task 1")
        )
    )
    if lifecycle_hooks and task_id and not _is_task_1:
        hook_names = [h for h in lifecycle_hooks if h.lower() in ("onload", "onloadstatic")]
        if hook_names:
            conflict = SchemaConflict(
                name=", ".join(hook_names),
                declared_by=[task_id],
                reason=(
                    f"Task 2+ must not redefine lifecycle hooks. "
                    f"Task '{task_id}' attempted to declare {hook_names} "
                    f"instead of patching the Task 1 scaffold via SEARCH/REPLACE."
                ),
            )
            new_conflicts.append(conflict)
            return new_conflicts  # early return: do NOT register declarations from this task

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
