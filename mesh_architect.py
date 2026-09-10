"""
mesh_architect.py  Phase 0: Pre-Decomposition Architect Pass

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

# -- Architect System Prompt ----------------------------------------------------

ARCHITECT_SYSTEM = (
    "You are the ATTRACTION ARCHITECT. "
    "Your ONLY job is to read a feature request and produce a structured JSON design "
    "document that will guide every downstream agent. "
    "Do NOT write implementation code. Do NOT decompose into tasks. "
    "Output ONLY valid JSON inside a ```json ... ``` fence  nothing else.\n\n"
    "The JSON MUST have exactly these top-level keys:\n"
    '  "title"            : string  short name for the attraction\n'
    '  "summary"          : string  one paragraph describing what the attraction does\n'
    '  "handles"          : array of {name, lua_type, owner_hint, lifecycle, description}\n'
    '                       owner_hint is a short domain label like "physics" or "economy"\n'
    '                       lifecycle is one of: OnLoadStatic | OnLoad | runtime\n'
    '  "lifecycle_order"  : ordered array of strings  the sequence of registrations\n'
    '                       that must appear inside OnLoad (e.g. "MidwayPhysics.OnStep(...)")\n'
    '  "event_flow"       : array of {trigger, action} edges\n'
    '                       trigger: a Lua condition expression\n'
    '                       action:  what happens (domain:call syntax)\n'
    '  "pool_requirements": object mapping pool key to minimum count (integer)\n'
    '  "economy_hooks"    : array of economy API names actually needed\n'
    '  "feature_checklist": array of plain-English feature statements to verify at the end\n\n'
    '  "task_anchors"     : array of {task_id, hook, location}  one per expected task.\n'
    '                       Each entry declares a deterministic anchor comment that will be\n'
    '                       inserted into the initial file scaffold so downstream agents\n'
    '                       have exact SEARCH targets.\n'
    '                       task_id: string like "3"\n'
    '                       hook:    the literal anchor comment like "-- [TASK_3_INSERT_HOOK] -- physics / pool setup"\n'
    '                       location: where to insert in the scaffold like "inside OnLoad()",\n'
    '                                 "inside OnStep(dt)", "inside OnUnload()", or "at module root"\n\n'
    "Rules:\n"
    "1. Only declare handles that are genuinely needed  do not invent extras.\n"
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

# -- Two-turn cognitive-load separation (Standard #2) ----------------------------
# Turn 1: raw, unstructured chain-of-thought (no formatting pressure).
ARCHITECT_COT_SYSTEM = (
    "You are the ATTRACTION ARCHITECT for 'Midway to Nowhere', a Lua-based arcade game. "
    "Attractions are single Lua files wired to the MidwayPhysics bridge — there is NO "
    "C++ class hierarchy: no Entity, GameObject, InputDevice, EconomyService, EventBus, "
    "GameSession, CreditSystem, or StateMachine classes. Never invent such architecture.\n"
    "Ground every idea ONLY in these real APIs:\n"
    "- Lifecycle: global Lua functions OnLoadStatic(), OnLoad(), OnStep(dt), OnUnload().\n"
    "- Physics: MidwayPhysics.SpawnStaticBox/Sphere/Capsule/Cylinder, SpawnDynamic*, "
    "SpawnKinematic*, SpawnSensor*, CreatePool/PoolAcquire/PoolReturn, ApplyImpulse, "
    "GetPosition, MoveKinematic, IsSensorTriggered, DestroyBody.\n"
    "- Input: MidwayInput.IsActionDown('fire' | 'power_up' | 'power_down' | ...).\n"
    "- Modifiers: read AttractionConstants.modifiers (or ENGINE_MOD_* globals) INSIDE "
    "OnStep every frame — never cache at load time.\n"
    "- Economy: Engine.AwardTickets(n, label) / Engine.AwardTokens(n, label), with "
    "Engine.GetStreak() as the multiplier.\n"
    "Handles are Lua-local userdata values returned by Spawn*/CreatePool calls.\n"
    "Be CONCISE: give 5-8 short bullet points covering handles, lifecycle order, event "
    "flow, pools, and economy. Do NOT output JSON yet — this is only the thinking step."
)

# Turn 2: clean extraction into the Pydantic schema (instructor + tenacity).
ARCHITECT_EXTRACT_SYSTEM = (
    "You are the ATTRACTION ARCHITECT. Convert the supplied raw analysis into the "
    "exact JSON schema requested, using ONLY the Lua/MidwayPhysics APIs named above "
    "(MidwayPhysics.*, MidwayInput.*, AttractionConstants.modifiers, Engine.AwardTickets/"
    "AwardTokens). Do NOT introduce C++ classes, managers, services, or any engine "
    "architecture. Return valid JSON only."
)

# Single-turn structured extraction system.  The two-turn CoT protocol was
# removed: the freeform turn rambled, returned empty output, and tripped the
# 60-minute socket timeout.  The schema + instructor validation + tenacity
# self-correction is more reliable than an unvalidated reasoning turn.
ARCHITECT_STRUCTURED_SYSTEM = (
    "You are the ATTRACTION ARCHITECT for 'Midway to Nowhere', a Lua-based arcade game. "
    "Attractions are single Lua files wired to the MidwayPhysics bridge — there is NO "
    "C++ class hierarchy: no Entity, GameObject, InputDevice, EconomyService, EventBus, "
    "GameSession, CreditSystem, or StateMachine classes. Never invent such architecture.\n"
    "Ground every idea ONLY in these real APIs:\n"
    "- Lifecycle: global Lua functions OnLoadStatic(), OnLoad(), OnStep(dt), OnUnload().\n"
    "- Physics: MidwayPhysics.SpawnStaticBox/Sphere/Capsule/Cylinder, SpawnDynamic*, "
    "SpawnKinematic*, SpawnSensor*, CreatePool/PoolAcquire/PoolReturn, ApplyImpulse, "
    "GetPosition, MoveKinematic, IsSensorTriggered, DestroyBody.\n"
    "- Input: MidwayInput.IsActionDown('fire' | 'power_up' | 'power_down' | ...).\n"
    "- Modifiers: read AttractionConstants.modifiers (or ENGINE_MOD_* globals) INSIDE "
    "OnStep every frame — never cache at load time.\n"
    "- Economy: Engine.AwardTickets(n, label) / Engine.AwardTokens(n, label), with "
    "Engine.GetStreak() as the multiplier.\n"
    "Handles are Lua-local userdata values returned by Spawn*/CreatePool calls.\n"
    "Produce the attraction design as a single JSON object matching the provided schema. "
    "Be CONCISE: keep the summary, every description, and every title to ONE short line. "
    "Do NOT pad, repeat, or invent extra placeholder entries. "
    "Return ONLY the JSON object — no prose, no markdown fences."
)

# -- JSON extractor -------------------------------------------------------------

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)


def _repair_json(text: str) -> str:
    """Apply lightweight heuristic repairs to a JSON string before parsing.

    Handles the most common model output defects:
    - Trailing ) or ); after the closing brace  (seen in phi3:14b output)
    - Trailing commas before } or ]             (JSON spec violation)
    - Single-quoted strings                     (Python-style output)
    """
    # Strip everything after the last closing brace
    last_brace = text.rfind("}")
    if last_brace != -1:
        text = text[:last_brace + 1]
    # Remove trailing commas before closing brace/bracket
    text = re.sub(r",\s*(\}|\])", r"\1", text)
    # Replace single-quoted string values/keys with double-quoted
    text = re.sub(r"(?<![\\])'", '"', text)
    return text


def _extract_json(text: str) -> Optional[dict]:
    """Extract the first JSON object from a model response."""
    # Try fenced block first
    m = _JSON_FENCE_RE.search(text)
    if m:
        candidate = m.group(1)
        for attempt in (candidate, _repair_json(candidate)):
            try:
                return json.loads(attempt)
            except json.JSONDecodeError:
                pass
    # Try bare JSON object with optional repair
    brace_start = text.find("{")
    if brace_start != -1:
        raw_candidate = text[brace_start:]
        for attempt in (raw_candidate, _repair_json(raw_candidate)):
            for end in range(len(attempt), 0, -1):
                try:
                    return json.loads(attempt[:end])
                except json.JSONDecodeError:
                    continue
    return None


# -- Design doc builder --------------------------------------------------------

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

    task_anchors = []
    for a in data.get("task_anchors") or []:
        if isinstance(a, dict) and a.get("task_id") and a.get("hook"):
            task_anchors.append({
                "task_id": str(a["task_id"]),
                "hook": str(a["hook"]),
                "location": str(a.get("location", "")),
            })

    return AttractionDesign(
        title=str(data.get("title") or ""),
        summary=str(data.get("summary") or ""),
        handles=handles,
        lifecycle_order=[str(x) for x in (data.get("lifecycle_order") or [])],
        event_flow=event_flow,
        pool_requirements=pool_req,
        economy_hooks=[str(x) for x in (data.get("economy_hooks") or [])],
        feature_checklist=[str(x) for x in (data.get("feature_checklist") or [])],
        task_anchors=task_anchors,
        raw_json=raw_json,
    )


# -- Seed integration schema from design ---------------------------------------

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


def _try_structured_architect(prompt: str) -> Optional[AttractionDesign]:
    """Extract the design via native Ollama JSON mode.

    Returns None if the structured path is unavailable or fails, so the caller
    falls back to the legacy regex parser without disturbing the pipeline.

    NOTE: this deliberately avoids the openai/instructor client — the OpenAI-
    compatible /v1/chat/completions endpoint was crashing the 9B runner with
    'model runner has unexpectedly stopped' (500), and instructor's tenacity
    retry schedule could silently re-run a full slow generation (10+ min each)
    on a validation failure.  The native /api/chat path with ``format:"json"``
    and a ``num_predict`` cap is both crash-free and bounded.
    """
    try:
        from structured_schemas import AttractionDesignOutput
    except Exception as e:
        print(f"  [Architect] ℹ Structured extraction unavailable ({e}); using legacy parser.")
        return None

    try:
        from pipeline import REASONING_MODEL, call_ollama
        from ollama_client import is_fatal_ollama_error

        print("  [Architect] Extracting structured design (single-turn, native JSON)...")
        out = call_ollama(
            ARCHITECT_STRUCTURED_SYSTEM,
            prompt,
            "Architect Design Pass (structured JSON)",
            REASONING_MODEL,
            params={"format": "json", "num_predict": 2048},
            skip_pre_summarizer=True,
        )
        if is_fatal_ollama_error(out):
            raise RuntimeError(f"Ollama error during architect extraction: {out[:200]}")

        # Tolerate stray prose / fences around the JSON object.
        _start = out.find("{")
        _end = out.rfind("}")
        if _start == -1 or _end <= _start:
            raise ValueError("no JSON object in architect response")
        design = AttractionDesignOutput.model_validate_json(out[_start:_end + 1])

        print(f"  [Architect] ✅ Structured design extracted: '{design.title}' "
              f"({len(design.handles)} handles, {len(design.event_flow)} event edges, "
              f"{len(design.feature_checklist)} checklist items).")
        return design.to_attraction_design()
    except Exception as e:
        print(f"  [Architect] ⚠ Structured extraction failed ({e}); falling back to legacy parser.")
        return None


# -- Main entry point ----------------------------------------------------------

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
        print("  [Architect] ℹ Design doc already present  skipping re-generation (blueprint continuation).")
        if ctx.integration_schema is None:
            ctx.integration_schema = _seed_integration_schema(ctx.attraction_design)
        return ctx

    prompt = ctx.canonical_request.strip()
    if not prompt:
        print("  [Architect] ⚠ No user prompt found  skipping architect pass.")
        return ctx

    print("\n" + "=" * 60)
    print("  [Architect] 🏗 Running pre-decomposition design pass...")
    print("=" * 60)

    # Give the architect the scoped GDD context and target attraction so it has
    # the actual design spec to work from — previously it received only the raw
    # one-line request and produced empty output.
    _gdd = (getattr(ctx, 'gdd_context', '') or '').strip()
    _target = (getattr(ctx, '_scope_target', '') or '').strip()
    architect_prompt = (
        f"## Feature Request\n{prompt}\n\n"
        + (f"## Target Attraction\n{_target}\n\n" if _target else "")
        + (f"## Relevant GDD Context\n{_gdd[:4000]}\n\n" if _gdd else "")
        + "Produce the JSON design document now."
    )

    try:
        # -- Standard #1/#2: instructor + tenacity two-turn extraction first. --
        # Falls back to the legacy regex JSON parser if unavailable or failed.
        _structured_design = _try_structured_architect(architect_prompt)
        if _structured_design is not None:
            ctx.attraction_design = _structured_design
            ctx.integration_schema = _seed_integration_schema(_structured_design)
            print(f"  [Architect] ✅ Design doc created (structured): '{_structured_design.title}'")
            print(f"             {len(_structured_design.handles)} handles, "
                  f"{len(_structured_design.event_flow)} event edges, "
                  f"{len(_structured_design.feature_checklist)} checklist items")
            ctx.output_parts.append(
                f"\n## 🏗 Attraction Design Document\n{_structured_design.to_context_block()}\n"
            )
            return ctx

        from pipeline import call_ollama, REASONING_MODEL
        from ollama_client import is_fatal_ollama_error

        architect_input = architect_prompt

        raw = call_ollama(
            ARCHITECT_SYSTEM,
            architect_input,
            "Architect Design Pass",
            REASONING_MODEL,
            params={"num_predict": 2048},
            skip_pre_summarizer=True,
        )

        if is_fatal_ollama_error(raw):
            print(f"  [Architect] ⛔ Ollama error during architect pass  continuing without design doc.")
            return ctx

        data = _extract_json(raw)
        if not data:
            print(f"  [Architect] ⚠ Could not parse JSON from architect response  continuing without design doc.")
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
