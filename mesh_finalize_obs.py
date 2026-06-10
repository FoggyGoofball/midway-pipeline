"""
mesh_finalize_obs.py -- Observability instrumentation and phantom-API gate
extracted from mesh_finalize.py.

Exported:
    _run_observability_pass(ctx) -> PipelineContext
    _run_phantom_api_gate(ctx) -> PipelineContext
"""

from __future__ import annotations

import re
from typing import Any

from models import PipelineContext
from _domain_sandbox import reject_cross_domain_output
from runtime_sim import run_phantom_api_final_pass
from pipeline import (
    ALL_DOMAINS,
    resolve_agent_name,
    get_agent_system,
    call_ollama,
)

# -- Phase 6b: Observability Instrumentation Pass ----------------------

def _run_observability_pass(ctx: PipelineContext) -> PipelineContext:
    """Phase 6b: Independent Observability Instrumentation Pass.
    
    Processes reviewed code blocks to inject mandatory logging. Enforces
    canonical file-level sandboxing using the original task's domain key
    to physically prevent cross-language hallucinations.
    """
    if ctx.review_verdict != "PASS":
        return ctx  # Skip logging injection if core logic failed review

    print(f"\n{'='*70}")
    print(f"  Phase 6b: Independent Observability Pass")
    print(f"{'='*70}")
    ctx.output_parts.append("\n## Phase 6b: Independent Observability Pass\n")

    obs_system = get_agent_system("OBSERVABILITY")

    for tid, current_code in list(ctx.all_results_dict.items()):
        task_obj = ctx.task_map.get(tid)
        if not task_obj:
            continue

        original_domain_key = resolve_agent_name(task_obj.agent)
        
        # Skip read-only domains
        if original_domain_key in ("DOC", "CONF", "TRIBUNAL", "LIBRARIAN", "REVIEWER", "DIRECTOR"):
            continue

        domain_name = ALL_DOMAINS.get(original_domain_key, {}).get("name", original_domain_key)
        print(f"  [Observability] Instrumenting {domain_name} task ({tid})...")

        obs_input = (
            f"## TARGET DOMAIN: {original_domain_key}\n\n"
            f"Review the following working code block. It has passed core review but lacks complete logging.\n"
            f"Inject mandatory function entry and state logging strictly native to the {original_domain_key} domain.\n"
            f"Do NOT alter existing core logic.\n\n"
            f"```\n{current_code}\n```"
        )

        from pipeline import EXECUTION_MODEL as _execution_model
        raw_obs_output = call_ollama(
            obs_system, 
            obs_input, 
            f"Observability Pass ({tid})", 
            _execution_model
        )

        # DIRECTIVE A: Canonical File-Level Sandboxing Enforcement
        # Validate output against the original domain (e.g., 'Lua') rather than 'OBSERVABILITY'
        is_clean, safe_output = reject_cross_domain_output(
            domain_key=original_domain_key,
            output_text=raw_obs_output,
            persona_name=f"Observability Auditor targeting {original_domain_key}"
        )

        # Anti-Contamination Intercept
        if any(marker in safe_output for marker in ["<<<<<<< SEARCH", "======= ", ">>>>>>> REPLACE"]):
            print(f"  [Observability] ⛔ Raw diff marker contamination detected for {tid}. Discarding corrupt output.")
            is_clean = False

        if not is_clean:
            print(f"  [Observability] ⛔ Language drift or cross-extension write detected for {tid}. Discarding logs and falling back to safe un-instrumented code.")
            ctx.output_parts.append(f"### Observability Pass ({tid})  REJECTED (Language Drift)\nFallback to un-instrumented working code retained.\n")
            continue

        # FM4: Re-run phantom API guard on the instrumented output before
        # committing.  The observability model can silently introduce new
        # phantom calls (e.g. MidwayPhysics.log, Engine.SomeNewThing) that
        # would otherwise bypass all static guards.
        _approved_lua_obs = getattr(ctx, '_bridge_exclusion_set', set())
        _domain_key_obs = resolve_agent_name(task_obj.agent) if task_obj else ""
        if _domain_key_obs.upper() == "LUA":
            import re as _re_obs
            # sol.log_message and MidwayPhysics.log_message are NOT registered in the
            # Lua bridge (MidwayPhysics.cpp exposes no logging function).  The only safe
            # logging primitive in Lua is the built-in print().
            # Full approved API set verified against engine_lua_bridge_contract.md.
            # The bridge contract dict uses slash-delimited compound keys
            # (e.g. "SpawnStaticBox/Sphere/Capsule/Cylinder/Mesh") so the
            # dynamic _bridge_exclusion_set only captures the first variant of
            # each group.  This set is the authoritative supplement that covers
            # every variant so the observability guard never false-positive rejects
            # instrumented code that uses a valid but non-first spawn variant.
            _always_ok_obs = {
                # Economy
                "engine.awardtickets", "engine.awardtokens",
                "engine.gettickets", "engine.gettokens", "engine.getstreak",
                # Physics lifecycle
                "midwayphysics.onstep", "midwayphysics.destroybody",
                # Static spawn variants
                "midwayphysics.spawnstaticbox", "midwayphysics.spawnstaticsphere",
                "midwayphysics.spawnstaticcapsule", "midwayphysics.spawnstaticcylinder",
                "midwayphysics.spawnstaticmesh",
                "midwayphysics.spawnstaticboxr", "midwayphysics.spawnstaticspherer",
                "midwayphysics.spawnstaticcapsuler", "midwayphysics.spawnstaticcylinderr",
                # Kinematic spawn variants
                "midwayphysics.spawnkinematicbox", "midwayphysics.spawnkinematicsphere",
                "midwayphysics.spawnkinematiccapsule", "midwayphysics.spawnkinematiccylinder",
                "midwayphysics.spawnkinematicboxr",
                # Dynamic spawn variants
                "midwayphysics.spawndynamicbox", "midwayphysics.spawndynamicsphere",
                "midwayphysics.spawndynamiccapsule", "midwayphysics.spawndynamiccylinder",
                "midwayphysics.spawndynamicmesh",
                "midwayphysics.spawndynamicboxr", "midwayphysics.spawndynamicspherer",
                "midwayphysics.spawndynamiccapsuler", "midwayphysics.spawndynamiccylinderr",
                # Sensor spawn variants
                "midwayphysics.spawnsensorbox", "midwayphysics.spawnSensorsphere",
                # Queries / velocity / impulse / movement
                "midwayphysics.movekinematic", "midwayphysics.getposition",
                "midwayphysics.getvelocity", "midwayphysics.getrotation",
                "midwayphysics.isactive", "midwayphysics.issensortriggered",
                "midwayphysics.setlinearvelocity", "midwayphysics.addlinearvelocity",
                "midwayphysics.applyimpulse", "midwayphysics.applyangularimpulse",
                # Per-body property overrides
                "midwayphysics.setfriction", "midwayphysics.setrestitution",
                "midwayphysics.setgravityfactor",
                "midwayphysics.setlineardamping", "midwayphysics.setangulardamping",
                # Object pools
                "midwayphysics.createpool", "midwayphysics.poolacquire",
                "midwayphysics.poolreturn", "midwayphysics.poolcullbelow",
                "midwayphysics.poolfree", "midwayphysics.pooltotal",
                # Lua stdlib
                "table.insert", "table.remove", "table.concat", "table.sort",
                "math.floor", "math.ceil", "math.abs", "math.max", "math.min",
                "math.sqrt", "math.random", "string.format", "string.len",
                "string.sub", "string.find", "string.gsub",
                "tostring", "tonumber", "ipairs", "pairs", "print",
            }
            # Explicit deny list: these look plausible but have no bridge registration.
            _phantom_deny_obs = {"sol.log_message", "midwayphysics.log_message", "sol.log", "midwayphysics.log"}
            _approved_obs = _approved_lua_obs | _always_ok_obs
            _phantom_obs = [
                _m.group(1)
                for _m in _re_obs.finditer(r'\b([A-Za-z_]\w*\.[A-Za-z_]\w*)\s*\(', safe_output)
                if _m.group(1).lower() in _phantom_deny_obs
                or (
                    _m.group(1).lower() not in _approved_obs
                    and _m.group(1).lower().split(".")[0] not in (
                        "math", "string", "table", "io", "os", "coroutine", "package", "debug", "utf8"
                    )
                )
            ]
            if _phantom_obs:
                print(f"  [Observability] ⛔ Phantom API(s) introduced by instrumentation for {tid}: "
                      + ", ".join(_phantom_obs[:5])
                      + "  discarding instrumented output, retaining prior code.")
                ctx.output_parts.append(
                    f"### Observability Pass ({tid})  REJECTED (Phantom APIs introduced)\n"
                    f"Discarded: {', '.join(_phantom_obs[:5])}. Un-instrumented code retained.\n"
                )
                continue

        # Commit instrumented code
        ctx.all_results_dict[tid] = safe_output
        
        # Synchronous array update: keep all_results list in sync with all_results_dict
        _found = False
        for i, entry in enumerate(ctx.all_results):
            if entry.get("task_id") == tid:
                ctx.all_results[i] = {"task_id": tid, "output": safe_output}
                _found = True
                break
        if not _found:
            ctx.all_results.append({"task_id": tid, "output": safe_output})
        
        ctx.output_parts.append(f"### Observability Pass ({tid})\n{safe_output}\n")

        
        # Synchronous Index Re-hydration: update the active_code_index entry
        # with the newly instrumented safe_output text.
        if f"### [{tid}]" in ctx.active_code_index:
            start_marker = f"### [{tid}]"
            end_marker = "\n### ["
            start_idx = ctx.active_code_index.find(start_marker)
            if start_idx != -1:
                end_idx = ctx.active_code_index.find(end_marker, start_idx + len(start_marker))
                if end_idx == -1:
                    end_idx = len(ctx.active_code_index)
                replacement_block = start_marker + "\n```\n" + safe_output + "\n```"
                ctx.active_code_index = (
                    ctx.active_code_index[:start_idx]
                    + replacement_block
                    + ctx.active_code_index[end_idx:]
                )

    print("  [Observability] ✓ Instrumentation complete.")
    return ctx


# -- Phase 6c: Final Phantom-API Gate ---------------------------------

def _run_phantom_api_gate(ctx: PipelineContext) -> PipelineContext:
    """Phase 6c: Deterministic phantom-API and economy/modifier gate.

    Runs AFTER observability so it catches any phantom calls introduced
    by instrumentation.  Results are stored on ctx.phantom_pass_errors
    and surfaced in the consensus gate so approval is physically blocked
    on any violation.
    """
    print(f"\n{'='*70}")
    print(f"  Phase 6c: Phantom API Final Gate")
    print(f"{'='*70}")
    ctx.output_parts.append("\n## Phase 6c: Phantom API Final Gate\n")

    phantom_errors = run_phantom_api_final_pass(ctx)

    if phantom_errors:
        ctx.output_parts.append("### ❌ Phantom API violations found\n")
        for err in phantom_errors:
            ctx.output_parts.append(f"- {err}\n")
            print(f"  [PhantomAPIGate] ❌ {err}")
    else:
        ctx.output_parts.append("### ✅ Phantom API gate  clean\n")
        print("  [PhantomAPIGate] ✅ All outputs pass the phantom-API gate.")

    return ctx
