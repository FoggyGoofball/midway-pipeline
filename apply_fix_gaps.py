#!/usr/bin/env python3
"""Apply remaining fix gaps:
#1b: Strengthen enclosing-block SEARCH instruction
#4b: Add whole-file arch rewrite fallback
#5c: Add ANTI-PATTERNS to _prompts.py
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
MODS = 0

# =====================================================================
# FIX #1b: Strengthen enclosing-block SEARCH instruction
# =====================================================================
eh = ROOT / "_helpers_exec.py"
text = eh.read_text(encoding="utf-8")

old_enc = (
    f'                        f"If you need to add new code, SEARCH for the nearest enclosing block "\n'
    f'                        f"or a TODO placeholder and REPLACE it with the expanded version."'
)
new_enc = (
    f'                        f"IMPORTANT: Your SEARCH block MUST include the entire enclosing function/block "\n'
    f'                        f"(e.g. the whole `function OnLoad() ... end` block). Do NOT search for just "\n'
    f'                        f"2-3 specific lines. The entire function body gives the patcher enough context "\n'
    f'                        f"to anchor the replacement reliably. If you are adding new code inside an "\n'
    f'                        f"existing hook, SEARCH for the full hook definition and REPLACE it with "\n'
    f'                        f"the expanded version containing your additions."'
)

if old_enc in text and "entire enclosing function" not in text:
    text = text.replace(old_enc, new_enc, 1)
    MODS += 1
    print("[FIX #1b] Strengthened enclosing-block SEARCH instruction")
else:
    print("[FIX #1b] WARN: Could not find enclosing block instruction")
    
eh.write_text(text, encoding="utf-8")

# =====================================================================
# FIX #4b: Add whole-file arch rewrite fallback to _finalize_preflight.py
# When the per-task arch fix cycle is exhausted, fall back to sending
# the entire aggregated file for a single unified fix.
# =====================================================================
fp = ROOT / "_finalize_preflight.py"
text = fp.read_text(encoding="utf-8")

# Find the section where unapplied tasks remain after all extraction attempts
# (the "circuit breaker" section at bottom of the fix loop)
# Add a whole-file rewrite fallback after the circuit breaker.
old_circuit = (
    '                        ctx.retry_counts[tid] = _strike + 1\n'
    '                        print(f"  [Arch Fix] \\u26a0 Executed individual task re-execution for {tid} ({domain})")\n'
)

new_circuit = (
    '                        ctx.retry_counts[tid] = _strike + 1\n'
    '                        print(f"  [Arch Fix] \\u26a0 Executed individual task re-execution for {tid} ({domain})")\n'
    '\n'
    '                # ── Whole-file rewrite fallback ──────────────────────\n'
    '                # When per-task fixes are exhausted and tasks still fail,\n'
    '                # send the entire aggregated file with the error log for\n'
    '                # a single unified fix. This prevents duplicate-function\n'
    '                # proliferation caused by fixing each task individually.\n'
    '                _remaining = [t for t in failing_tids if t not in applied_tids]\n'
    '                if _remaining and ctx.pre_flight_errors:\n'
    '                    # Build the aggregated file content for the first failing file\n'
    '                    _agg_task = ctx.task_map.get(_remaining[0])\n'
    '                    if _agg_task and getattr(_agg_task, "target_file", None):\n'
    '                        _agg_file = str(_agg_task.target_file).replace("\\\\", "/")\n'
    '                        # Collect all task outputs for this file\n'
    '                        _agg_parts = []\n'
    '                        for _agg_tid, _agg_out in ctx.all_results_dict.items():\n'
    '                            if _agg_tid.startswith("merged:") and _agg_file in _agg_tid:\n'
    '                                _agg_parts.append(_agg_out)\n'
    '                            elif _agg_tid in ctx.task_map:\n'
    '                                _agg_tsk = ctx.task_map[_agg_tid]\n'
    '                                if getattr(_agg_tsk, "target_file", None) and str(_agg_tsk.target_file).replace("\\\\", "/") == _agg_file:\n'
    '                                    _agg_parts.append(_agg_out)\n'
    '                        if _agg_parts:\n'
    '                            _agg_merged = "\\n\\n".join(_agg_parts)\n'
    '                            print(f"  [Arch Fix] \\ud83d\\udd04 Whole-file rewrite fallback for {_agg_file} "\n'
    '                                  f"({len(_remaining)} remaining tasks)")\n'
    '                            ctx.pre_flight_errors += (\n'
    '                                f"\\n## Whole-File Rewrite Request  \\u2014 {domain} ({_agg_file})\\n"\n'
    '                                f"The following {len(_remaining)} tasks could not be fixed individually: "\n'
    '                                f"{', '.join(_remaining)}.\\n"\n'
    '                                f"Use a SINGLE unified SEARCH/REPLACE block targeting the COMPLETE file.\\n"\n'
    '                                f"Send the entire corrected file content.\\n"\n'
    '                            )\n'
)

if old_circuit in text and "Whole-file rewrite fallback" not in text:
    text = text.replace(old_circuit, new_circuit, 1)
    MODS += 1
    print("[FIX #4b] Added whole-file rewrite fallback")
else:
    print("[FIX #4b] WARN: Could not add whole-file fallback")
    
fp.write_text(text, encoding="utf-8")

# =====================================================================
# FIX #5c: Add ANTI-PATTERNS to _prompts.py scripter system prompt
# =====================================================================
pp = ROOT / "_prompts.py"
text = pp.read_text(encoding="utf-8")

# Find the ARCHITECT_FIX_SYSTEM prompt
idx = text.find("ARCHITECT_FIX_SYSTEM")
if idx >= 0 and "never do these" not in text[idx:idx+2000]:
    # Find the end of the ARCHITECT_FIX_SYSTEM string
    end_idx = text.find('"""', idx)
    if end_idx > idx:
        # Insert anti-patterns before the closing """
        anti_block = (
            '\n\n### ANTI-PATTERNS (NEVER DO THESE)\n'
            '- ANTI-PATTERN: Code at module root level. MidwayPhysics.SpawnDynamicSphere(...) '
            'must NEVER appear outside a function body. Code at root level crashes the engine '
            'because the MidwayPhysics API is not yet initialized.\n'
            '- ANTI-PATTERN: function OnStep(dt) at module level. You MUST use '
            'MidwayPhysics.OnStep(function(dt) ... end) inside OnLoad(). A bare OnStep(dt) '
            'function will never be called by the engine.\n'
            '- ANTI-PATTERN: Creating multiple OnLoadStatic() / OnLoad() / OnUnload() functions. '
            'Each lifecycle hook must appear exactly once. Duplicate definitions cause Lua parse errors.\n'
            '- ANTI-PATTERN: Caching AttractionConstants.modifiers at load time. Always read '
            'local MOD = AttractionConstants.modifiers inside the OnStep callback every frame.\n'
            '- ANTI-PATTERN: Using sol.* APIs from Lua. sol is a C++ binding layer and does not '
            'exist at Lua runtime. Never call sol.set_function, sol.state, etc. from Lua scripts.\n'
            '- ANTI-PATTERN: Spawning static geometry for gameplay objects. Use SpawnStaticBox/Sphere/Capsule '
            'ONLY for permanent cabinet geometry (walls, ramps, shelves). Use SpawnDynamicSphere/Box/Capsule '
            'for any body that moves during gameplay (balls, projectiles, tokens).\n'
        )
        text = text[:end_idx] + anti_block + text[end_idx:]
        MODS += 1
        print("[FIX #5c] Added ANTI-PATTERNS to ARCHITECT_FIX_SYSTEM in _prompts.py")
else:
    if "never do these" in text[idx:idx+2000]:
        print("[FIX #5c] Already applied")
    else:
        print("[FIX #5c] WARN: ARCHITECT_FIX_SYSTEM not found")

pp.write_text(text, encoding="utf-8")

print(f"\nApplied {MODS} additional fix(es).")
