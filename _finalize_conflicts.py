"""
_finalize_conflicts.py — Phase 5: Conflict Resolution + Shared-File Merge
==========================================================================
Extracted from mesh_finalize.py — handles VETO and OBJECT signal resolution,
and accumulative merge of multiple tasks that target the same output file.

Exported:
    _run_conflict_resolution(ctx) -> PipelineContext
    merge_shared_file_outputs(ctx) -> PipelineContext
"""

from __future__ import annotations

import re
from pathlib import Path
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

    # Prefer live cartridge registry for CONF so cartridge-overridden mediator prompts are used.
    _live_dr = getattr(ctx, 'domain_registry', None) or {}
    _conf_domain = _live_dr.get('CONF') or ALL_DOMAINS.get('CONF') or {}
    _conf_system = _conf_domain.get('system_prompt', 'You are a conflict resolution mediator.')

    ctx.conflict_resolutions = []

    # ── De-duplicate vetos by (from, target, reason) ────────────────────────
    # A single task output that loops delegation signals can inject dozens of
    # identical VETO entries.  Resolve each unique (from, target, reason) once.
    _MAX_CONFLICT_CALLS = 6
    seen_veto_keys: set = set()
    deduped_vetos: list = []
    for v in ctx.all_vetos:
        _key = (v.get('from', ''), v.get('target', ''), v.get('reason', ''))
        if _key not in seen_veto_keys:
            seen_veto_keys.add(_key)
            deduped_vetos.append(v)
    if len(deduped_vetos) < len(ctx.all_vetos):
        print(f"  [Conflict] Deduplicated {len(ctx.all_vetos)} veto(s) → "
              f"{len(deduped_vetos)} unique conflict(s).")
    if len(deduped_vetos) > _MAX_CONFLICT_CALLS:
        print(f"  [Conflict] ⚠ Capping conflict calls at {_MAX_CONFLICT_CALLS} "
              f"(was {len(deduped_vetos)} after dedup).")
        deduped_vetos = deduped_vetos[:_MAX_CONFLICT_CALLS]

    for veto in deduped_vetos:
        target = resolve_agent_name(veto["target"])
        from_agent = resolve_agent_name(veto["from"])

        conflict_prompt = (
            f"## VETO Signal\n"
            f"**From:** {ALL_DOMAINS.get(from_agent, {}).get('name', from_agent)}\n"
            f"**Target:** {ALL_DOMAINS.get(target, {}).get('name', target)}\n"
            f"**Reason:** {veto['reason']}\n\n"
            f"## Original Feature Request\n{ctx.canonical_request}\n\n"
            f"## Director's Task Breakdown\n{ctx.director_output}\n\n"
        )

        if veto["task_id"] in ctx.all_results_dict:
            conflict_prompt += (
                f"## Output that triggered VETO\n{ctx.all_results_dict[veto['task_id']]}\n\n"
            )

        from pipeline import DIRECTOR_MODEL as _director_model
        conflict_output = call_ollama(
            _conf_system,
            conflict_prompt,
            f"Conflict Resolution: {veto['from']} vs {veto['target']}",
            _director_model,
            skip_pre_summarizer=True,
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
            f"## Original Feature Request\n{ctx.canonical_request}\n\n"
        )

        if obj["task_id"] in ctx.all_results_dict:
            object_prompt += (
                f"## Output that triggered OBJECT\n{ctx.all_results_dict[obj['task_id']]}\n\n"
            )

        from pipeline import DIRECTOR_MODEL as _director_model
        object_output = call_ollama(
            _conf_system,
            object_prompt,
            f"Conflict Resolution: {obj['from']} OBJECTS {obj['target']}",
            _director_model,
            skip_pre_summarizer=True,
        )
        ctx.conflict_resolutions.append(object_output)
        ctx.output_parts.append(
            f"### OBJECT: {obj['from']} -> {obj['target']}\n{object_output}\n"
        )

    # ── Phase 5b: Shared-File Merge ──────────────────────────────────────
    ctx = merge_shared_file_outputs(ctx)

    return ctx


# ──────────────────────────────────────────────────────────────────────
#  Phase 5b: Shared-File Merge
# ──────────────────────────────────────────────────────────────────────

_MERGE_SYSTEM = (
    "You are an expert code integrator. You will receive multiple partial implementations "
    "from different pipeline tasks that all contribute to the SAME output file. "
    "Your job is to produce ONE complete, coherent, syntactically correct implementation "
    "that incorporates ALL functionality from every partial contribution. "
    "Rules:\n"
    "1. Do NOT drop any function, constant, or logic block from any contributor.\n"
    "2. Resolve merge conflicts by keeping the most complete version of any duplicated section.\n"
    "3. Output ONLY the final merged code — no prose, no markdown headings, no fences.\n"
    "4. Preserve the language and style of the dominant contributor (usually the last task).\n"
    "5. If contributions are in different languages, pick the language matching the file extension.\n"
)

_MERGE_CODE_BUDGET = 3000  # chars per task contribution fed to merger
_MERGE_MAX_TASKS = 8       # cap on how many contributors are sent to the LLM


def _extract_code(text: str) -> str:
    """Strip prose and fenced code blocks; return the raw code content."""
    # Try to extract from fenced block first
    fenced = re.findall(r'```(?:\w+)?\n(.*?)```', text, re.DOTALL)
    if fenced:
        return "\n\n".join(f.strip() for f in fenced)
    # Strip SEARCH/REPLACE diff markers if present
    text = re.sub(r'<<<<<<< SEARCH.*?======= ', '', text, flags=re.DOTALL)
    text = re.sub(r'>>>>>>> REPLACE', '', text)
    return text.strip()


def merge_shared_file_outputs(ctx: PipelineContext) -> PipelineContext:
    """Detect tasks sharing the same target_file and merge their outputs into
    one coherent artifact.  The merged result is:
      - written into ctx.all_results_dict under key  'merged:<rel_path>'
      - written to the staging workspace via atomic_write_text
      - stored on ctx so the review loop can reference it
    """
    # Build a mapping: target_file -> [task_id, ...]
    file_to_tasks: Dict[str, List[str]] = {}
    for tid, task in ctx.task_map.items():
        tf = getattr(task, 'target_file', None)
        if tf and tid in ctx.all_results_dict:
            file_to_tasks.setdefault(tf, []).append(tid)

    # Only merge files that have multiple contributors
    shared = {f: tids for f, tids in file_to_tasks.items() if len(tids) > 1}

    if not shared:
        return ctx  # nothing to merge

    print(f"\n{'='*70}")
    print(f"  Phase 5b: Shared-File Merge ({len(shared)} file(s) with multiple contributors)")
    print(f"{'='*70}")
    ctx.output_parts.append("\n## Phase 5b: Shared-File Merge\n")

    from pipeline import DIRECTOR_MODEL as _director_model
    from _helpers_io import atomic_write_text

    # Initialise registry on ctx so the review loop can look things up
    if not hasattr(ctx, 'merged_file_registry'):
        ctx.merged_file_registry = {}  # rel_path -> merged_key

    for rel_path, tids in shared.items():
        print(f"  [Merge] {rel_path}: combining {len(tids)} task(s): {tids}")

        contributions: List[str] = []
        for tid in tids[:_MERGE_MAX_TASKS]:
            raw = ctx.all_results_dict.get(tid, "")
            code = _extract_code(raw)
            snippet = code[:_MERGE_CODE_BUDGET]
            if len(code) > _MERGE_CODE_BUDGET:
                snippet += f"\n... [truncated — {len(code) - _MERGE_CODE_BUDGET} chars omitted]"
            task_obj = ctx.task_map.get(tid)
            domain = getattr(task_obj, 'agent', '?') if task_obj else '?'
            contributions.append(f"=== Contribution from task {tid} [{domain}] ===\n{snippet}")

        merge_prompt = (
            f"## Target File: {rel_path}\n\n"
            + "\n\n".join(contributions)
            + "\n\n## Instructions\nMerge ALL contributions above into one complete file. "
            "Output ONLY the final merged code with no extra commentary."
        )

        merged_code = call_ollama(
            _MERGE_SYSTEM,
            merge_prompt,
            f"Shared-File Merge ({rel_path})",
            _director_model,
            skip_pre_summarizer=True,
        )

        # Strip any fences the LLM may have added despite instructions
        merged_code = _extract_code(merged_code) or merged_code.strip()

        # Store under a stable merged key
        merged_key = f"merged:{rel_path}"
        ctx.all_results_dict[merged_key] = merged_code
        ctx.merged_file_registry[rel_path] = merged_key

        # Write merged artifact to staging so later phases (observability,
        # review, commit_staging) all see the unified file.
        target_path = ctx.project_root / rel_path
        try:
            atomic_write_text(target_path, merged_code)
            print(f"  [Merge] ✅ Staged merged file: {rel_path} ({len(merged_code)} chars)")
        except Exception as exc:
            print(f"  [Merge] ⚠ Could not write staged file {rel_path}: {exc}")

        ctx.output_parts.append(
            f"### Merged: {rel_path}\n"
            f"Contributors: {', '.join(tids)}\n"
            f"```\n{merged_code[:1500]}"
            + ("…\n```\n" if len(merged_code) > 1500 else "\n```\n")
        )

    return ctx
