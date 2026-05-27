"""
mesh_architect.py — Phase 0: Pre-Decomposition Architect Pass

Runs BEFORE the Director decomposes the prompt into tasks.  A reasoning-model
pass reads the raw user prompt and produces a structured AttractionDesign JSON
that captures:
  - Declared handle names and their types/lifecycle
  - OnLoad registration order
  - Event flow edges (trigger → action)
  - Object-pool requirements
  - Economy hooks referenced
  - A feature checklist for the coverage validator

The AttractionDesign is stored on ctx.attraction_design and injected into:
  - The Director's decomposition prompt (so tasks align with the design)
  - Every agent's context (so handle names are consistent)
  - The coverage validator (so missing features are detected)

Degrades gracefully: if the Architect fails or the model is unavailable,
ctx.attraction_design remains None and the pipeline continues without it.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from models import (
    AttractionDesign,
    HandleDeclaration,
    EventEdge,
    IntegrationSchema,
    PipelineContext,
)

# ── Architect System Prompt ────────────────────────────────────────────────────

ARCHITECT_SYSTEM = (
    "You are the ATTRACTION ARCHITECT. "
    "Your ONLY job is to read a feature request and produce a structured JSON design "
    "document that will guide every downstream agent. "
    "Do NOT write implementation code. Do NOT decompose into tasks. "
    "Output ONLY valid JSON inside a ```json ... ``` fence — nothing else.\n\n"
    "The JSON MUST have exactly these top-level keys:\n"
    '  "title"            : string — short name for the attraction\n'
    '  "summary"          : string — one paragraph describing what the attraction does\n'
    '  "handles"          : array of {name, lua_type, owner_hint, lifecycle, description}\n'
    '                       owner_hint is a short domain label like "physics" or "economy"\n'
    '                       lifecycle is one of: OnLoadStatic | OnLoad | runtime\n'
    '  "lifecycle_order"  : ordered array of strings — the sequence of registrations\n'
    '                       that must appear inside OnLoad (e.g. "MidwayPhysics.OnStep(...)")\n'
    '  "event_flow"       : array of {trigger, action} edges\n'
    '                       trigger: a Lua condition expression\n'
    '                       action:  what happens (domain:call syntax)\n'
    '  "pool_requirements": object mapping pool key to minimum count (integer)\n'
    '  "economy_hooks"    : array of economy API names actually needed\n'
    '  "feature_checklist": array of plain-English feature statements to verify at the end\n\n'
    "Rules:\n"
    "1. Only declare handles that are genuinely needed — do not invent extras.\n"
    "2. lifecycle_order must be complete enough that agents know what order to register.\n"
    "3. feature_checklist items should be independently verifiable (grep/AST checkable).\n"
    "4. If the request does not involve physics, leave handles empty.\n"
    "5. Output ONLY the JSON block. No prose before or after.\n"
    "6. ECONOMY MANDATE (NON-NEGOTIABLE): Every attraction MUST include BOTH of the following "
    "in its feature_checklist:\n"
    "   a. 'OnStep reads AttractionConstants.modifiers every frame (never cached at load time)'\n"
    "   b. 'Economy hook: Engine.AwardTickets or Engine.AwardTokens called on win/score events'\n"
    "   These two items are REQUIRED regardless of attraction type. Do NOT omit them. "
    "Also add both to economy_hooks: ['AttractionConstants.modifiers', 'Engine.AwardTickets'].\n"
    "7. For attractions with a scoring system, include 'Engine.GetStreak() multiplier applied "
    "to ticket/token payouts' as a third feature_checklist item."
)

# ── JSON extractor ─────────────────────────────────────────────────────────────

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)


def _extract_json(text: str) -> Optional[dict]:
    """Extract the first JSON object from a model response."""
    # Try fenced block first
    m = _JSON_FENCE_RE.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # Try bare JSON object
    brace_start = text.find("{")
    if brace_start != -1:
        for end in range(len(text), brace_start, -1):
            try:
                return json.loads(text[brace_start:end])
            except json.JSONDecodeError:
                continue
    return None


# ── Design doc builder ────────────────────────────────────────────────────────

def _build_design_from_dict(data: dict, raw_json: str) -> AttractionDesign:
    """Convert a parsed JSON dict into a validated AttractionDesign."""
    handles = []
    for h in data.get("handles") or []:
        if isinstance(h, dict) and h.get("name"):
            _lc_raw = h.get("lifecycle", "OnLoad")
            # Normalize: model sometimes emits a single-item list instead of a string.
            if isinstance(_lc_raw, list):
                _lc_raw = _lc_raw[0] if _lc_raw else "OnLoad"
            handles.append(HandleDeclaration(
                name=h["name"],
                lua_type=h.get("lua_type", "userdata"),
                owner_task=h.get("owner_hint", ""),
                lifecycle=str(_lc_raw),
                description=h.get("description", ""),
            ))

    event_flow = []
    for e in data.get("event_flow") or []:
        if isinstance(e, dict) and e.get("trigger") and e.get("action"):
            event_flow.append(EventEdge(
                trigger=e["trigger"],
                action=e["action"],
            ))

    pool_req = {}
    for k, v in (data.get("pool_requirements") or {}).items():
        try:
            pool_req[str(k)] = int(v)
        except (TypeError, ValueError):
            pass

    return AttractionDesign(
        title=str(data.get("title") or ""),
        summary=str(data.get("summary") or ""),
        handles=handles,
        lifecycle_order=[str(x) for x in (data.get("lifecycle_order") or [])],
        event_flow=event_flow,
        pool_requirements=pool_req,
        economy_hooks=[str(x) for x in (data.get("economy_hooks") or [])],
        feature_checklist=[str(x) for x in (data.get("feature_checklist") or [])],
        raw_json=raw_json,
    )


# ── Seed integration schema from design ───────────────────────────────────────

def _seed_integration_schema(design: AttractionDesign) -> IntegrationSchema:
    """Pre-populate the IntegrationSchema from the AttractionDesign handles."""
    from models import SchemaHandleEntry
    schema = IntegrationSchema()
    for h in design.handles:
        entry = SchemaHandleEntry(
            name=h.name,
            declared_by=h.owner_task or "architect",
            lua_type=h.lua_type,
            created_in=h.lifecycle,
        )
        schema.declare_handle(entry)
    return schema


# ── Main entry point ──────────────────────────────────────────────────────────

def run_architect_pass(ctx: PipelineContext) -> PipelineContext:
    """
    Phase 0: Run the pre-decomposition architect pass.

    Reads ctx.user_prompt (via ctx.canonical_request), calls the reasoning model,
    parses the JSON design doc, and stores it on ctx.attraction_design.
    Also seeds ctx.integration_schema from the declared handles.

    Safe to call when attraction_design already exists (blueprint continuation):
    in that case the pass is skipped to preserve the original design.
    """
    # Blueprint continuation: preserve design across batches
    if ctx.attraction_design is not None:
        print("  [Architect] ℹ Design doc already present — skipping re-generation (blueprint continuation).")
        if ctx.integration_schema is None:
            ctx.integration_schema = _seed_integration_schema(ctx.attraction_design)
        return ctx

    prompt = ctx.canonical_request.strip()
    if not prompt:
        print("  [Architect] ⚠ No user prompt found — skipping architect pass.")
        return ctx

    print("\n" + "=" * 60)
    print("  [Architect] 🏗 Running pre-decomposition design pass...")
    print("=" * 60)

    try:
        from pipeline import call_ollama, REASONING_MODEL
        from ollama_client import is_fatal_ollama_error

        architect_input = (
            f"## Feature Request\n{prompt}\n\n"
            "Produce the JSON design document now."
        )

        raw = call_ollama(
            ARCHITECT_SYSTEM,
            architect_input,
            "Architect Design Pass",
            REASONING_MODEL,
            skip_pre_summarizer=True,
        )

        if is_fatal_ollama_error(raw):
            print(f"  [Architect] ⛔ Ollama error during architect pass — continuing without design doc.")
            return ctx

        data = _extract_json(raw)
        if not data:
            print(f"  [Architect] ⚠ Could not parse JSON from architect response — continuing without design doc.")
            print(f"  Raw response (first 400 chars): {raw[:400]}")
            return ctx

        design = _build_design_from_dict(data, raw_json=raw)
        ctx.attraction_design = design
        ctx.integration_schema = _seed_integration_schema(design)

        print(f"  [Architect] ✅ Design doc created: '{design.title}'")
        print(f"             {len(design.handles)} handles, {len(design.event_flow)} event edges, "
              f"{len(design.feature_checklist)} checklist items")

        # Append the design summary to output_parts so it appears in the run log
        ctx.output_parts.append(
            f"\n## 🏗 Attraction Design Document\n{design.to_context_block()}\n"
        )

    except Exception as e:
        import traceback
        print(f"  [Architect] ⚠ Architect pass failed: {e}")
        traceback.print_exc()

    return ctx
