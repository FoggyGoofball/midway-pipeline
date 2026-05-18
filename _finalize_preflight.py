"""
_finalize_preflight.py — Pre-Flight Checks & Architect Fix
===========================================================
Extracted from mesh_finalize.py — handles background compilation,
syntax validation, and the Architect Syntax Fix invocation loop.

Exported:
    _run_preflight_checks(ctx) -> PipelineContext
"""

from __future__ import annotations

import re
import subprocess
import sys
from typing import Any, Dict, List, Optional

from models import PipelineContext
from _pipeline_helpers import atomic_write_text
from token_budget import TokenBudget
import _prompts as _prompts_mod  # live module ref — reads post-bootstrap values
from pipeline import (
    PROJECT_ROOT,
    call_ollama,
)


# ──────────────────────────────────────────────────────────────────────
#  Pre-Flight Checks: Compilation & Syntax Validation
# ──────────────────────────────────────────────────────────────────────

def _flush_results_to_workspace(ctx: PipelineContext) -> None:
    """Pre-compilation file sync: flush all result content to disk before build."""
    for tid, content in ctx.all_results_dict.items():
        task = ctx.task_map.get(tid)
        if task and task.target_file:
            target_path = ctx.project_root / task.target_file
            clean_content = _strip_search_replace_metadata(content)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(target_path, clean_content)


def _strip_search_replace_metadata(content: str) -> str:
    """Sanitize SEARCH/REPLACE blocks: apply diff instructions natively in memory."""
    import re as _re
    # Apply SEARCH/REPLACE blocks: replace old content with new content
    block_pattern = _re.compile(
        r'<<<<<<< SEARCH\n(.*?)\n=======\n(.*?)\n>>>>>>> REPLACE',
        _re.DOTALL,
    )
    result = content
    for match in block_pattern.finditer(content):
        # Use the REPLACE side as the new content
        result = result.replace(match.group(0), match.group(2), 1)
    return result


_COMMENT_ONLY_RE = re.compile(
    r'^\s*(//[^\n]*|/\*.*?\*/|#[^\n]*)\s*$',
    re.DOTALL,
)


def _is_comment_only(content: str) -> bool:
    """Return True if content is nothing but comments and whitespace."""
    # Strip fenced code block markers, then check.
    stripped = re.sub(r'```[^\n]*\n?|```', '', content).strip()
    return bool(_COMMENT_ONLY_RE.match(stripped)) or not stripped


# Detects a meaningful code block: at least one SEARCH/REPLACE pair, a fenced
# code block, or a bare function/class/variable declaration line.
_HAS_CODE_RE = re.compile(
    r'(<{7}\s*SEARCH|`{3}|\bfunction\b|\bclass\b|\bdef\b|\bvoid\b|\bint\b|\breturn\b)',
    re.IGNORECASE,
)


# Patterns that indicate the output is a pure signal/delegation with no real code.
_DELEGATE_ONLY_RE = re.compile(
    r'^\s*(\[DELEGATE:|\[QUERY:DOC:|\[CONF:|\[REVISE:)',
    re.IGNORECASE | re.MULTILINE,
)

def _task_has_code(content: str) -> bool:
    """Return True if *content* contains at least one concrete code construct
    AND is not a pure delegation/signal response.

    Rejects outputs that are primarily markdown table rows (hallucinated API
    ledgers) even when they contain function keywords, since those keywords
    appear inside table cell text, not as real code constructs.
    """
    if not content or not content.strip():
        return False
    # If the entire (stripped) content is only signal lines, treat as no-code.
    non_signal_lines = [
        ln for ln in content.splitlines()
        if ln.strip() and not _DELEGATE_ONLY_RE.match(ln)
    ]
    if not non_signal_lines:
        return False
    # Ledger-hallucination guard: if the majority of non-empty lines look like
    # markdown table rows (start with '|'), the LLM dumped a ledger table, not code.
    _table_rows = sum(1 for ln in non_signal_lines if ln.strip().startswith('|'))
    if _table_rows > 0 and _table_rows / len(non_signal_lines) > 0.40:
        return False
    # Must contain a real code construct inside a fenced block or as a bare statement.
    return bool(_HAS_CODE_RE.search(content))


def _inject_empty_output_errors(ctx: PipelineContext) -> None:
    """
    Universal guard: if a task result contains only prose with no code, inject a
    pre-flight error so the fix loop forces a proper implementation before review.
    This check is project-agnostic — empty outputs are always a failure regardless
    of domain or technology.
    """
    for tid, content in list(ctx.all_results_dict.items()):
        if not content or not content.strip() or _is_comment_only(content):
            ctx.pre_flight_errors += (
                f"\n## Empty Output — Task {tid}\n"
                f"Task {tid} produced no output at all (or only comments). "
                f"The agent must provide a concrete implementation.\n"
            )
            print(f"  [Pre-Flight] EMPTY OUTPUT detected for task {tid} — injecting fix demand.")
        elif _DELEGATE_ONLY_RE.search(content) and not _task_has_code(content):
            ctx.pre_flight_errors += (
                f"\n## Delegation Signal Only — Task {tid}\n"
                f"Task {tid} responded with only a [DELEGATE/QUERY/CONF] signal and no "
                f"concrete code. Delegation signals are forbidden as a substitute for "
                f"implementation. The agent MUST produce a complete code block.\n"
            )
            print(f"  [Pre-Flight] DELEGATE-ONLY output detected for task {tid} — injecting fix demand.")
        elif _DELEGATE_ONLY_RE.search(content[:500]) and not _task_has_code(content):
            # Delegate signal buried at the start of a long hallucinated dump.
            ctx.pre_flight_errors += (
                f"\n## Delegation Signal Buried in Output — Task {tid}\n"
                f"Task {tid} began with a [DELEGATE/QUERY/CONF] signal followed by "
                f"non-code content (e.g., a ledger table dump). This is not a valid "
                f"implementation. The agent MUST produce a complete working code block.\n"
            )
            print(f"  [Pre-Flight] DELEGATE+HALLUCINATION detected for task {tid} — injecting fix demand.")
        elif not _task_has_code(content):
            ctx.pre_flight_errors += (
                f"\n## No Code Block — Task {tid}\n"
                f"Task {tid} response contains only prose with no code construct. "
                f"The agent must produce at least one concrete code block "
                f"that implements the task specification.\n"
            )
            print(f"  [Pre-Flight] NO CODE BLOCK detected for task {tid} — injecting fix demand.")


def _inject_static_pattern_errors(ctx: PipelineContext) -> None:
    """Deterministic, compiler-free checks for patterns that are always wrong.

    These fire before any LLM reviewer sees the code, so they cannot be
    talked past by a permissive reviewer or skipped when no CMakeCache exists.
    Each guard targets a failure pattern witnessed in real pipeline runs.
    """
    # ── Guard patterns ────────────────────────────────────────────────────
    # Each entry: (domain_filter, regex, short_label, explanation)
    # domain_filter: None = all domains, otherwise only tasks for that agent.
    _GUARDS = [
        # Lua: require('nlohmann.json') — nlohmann is a C++ library.
        (
            "Lua",
            re.compile(r"""require\s*\(\s*['"]nlohmann""", re.IGNORECASE),
            "phantom require('nlohmann.json')",
            "nlohmann/json is a C++ library and cannot be require()'d from Lua. "
            "Use Engine.LoadJSON(path) or parse via the bridge contract instead.",
        ),
        # Lua: SpawnDynamicBall / SpawnStaticBall — never existed in the bridge.
        (
            "Lua",
            re.compile(r'\bMidwayPhysics\.(SpawnDynamicBall|SpawnStaticBall)\s*\(', re.IGNORECASE),
            "phantom API MidwayPhysics.SpawnDynamic/StaticBall — use SpawnDynamicSphere / SpawnStaticSphere",
            "SpawnDynamicBall and SpawnStaticBall do not exist in the bridge contract. "
            "Use MidwayPhysics.SpawnDynamicSphere(lx, ly, lz, r, [mass]) or "
            "MidwayPhysics.SpawnStaticSphere(lx, ly, lz, r) instead.",
        ),
        # Lua: require('midway_physics') — the bridge exposes globals, not a module.
        (
            "Lua",
            re.compile(r"""require\s*\(\s*['"]midway_physics['"]\s*\)""", re.IGNORECASE),
            "phantom require('midway_physics') — bridge APIs are globals, not a module",
            "The C++ bridge injects MidwayPhysics.* as globals directly into the Lua state. "
            "There is no 'midway_physics' module to require(). "
            "Remove the require() call and use MidwayPhysics.SpawnDynamicSphere(...) directly.",
        ),
        # C++: lua.set_function("X.Y", ...) — sol2 dot-notation table paths.
        (
            "C++",
            re.compile(r'\.set_function\s*\(\s*"[A-Za-z_]\w*\.[A-Za-z_]\w*"', re.IGNORECASE),
            "wrong sol2 table registration (dot-notation in set_function)",
            'sol2 set_function() does not accept "Table.Method" dot-path strings. '
            'Use lua["Table"]["Method"] = ... to register table-scoped functions.',
        ),
        # C7: Lua calling any sol.* method — sol is a C++ binding layer with no Lua-side object.
        # Covers sol.set_function, sol.new_usertype, sol.state, sol.script, sol.log_message, etc.
        (
            "Lua",
            re.compile(r'\bsol\s*\.\s*[A-Za-z_]\w*\s*\(', re.IGNORECASE),
            "sol.* called from Lua — sol is a C++ binding layer with no Lua-side object",
            "'sol' is a C++ namespace/object and does not exist at Lua runtime. "
            "Remove all sol.* calls from Lua code. Use print() for logging.",
        ),
        # C8: Bare DestroyBody(...) without MidwayPhysics. namespace in Lua.
        (
            "Lua",
            re.compile(r'(?<!MidwayPhysics\.)\bDestroyBody\s*\(', re.IGNORECASE),
            "bare DestroyBody() — missing MidwayPhysics. namespace",
            "DestroyBody is not a global function. "
            "Use MidwayPhysics.DestroyBody(handle) instead.",
        ),
        # F19: SpawnStaticPlane — phantom API, never existed in the bridge.
        (
            "Lua",
            re.compile(r'\bMidwayPhysics\.SpawnStaticPlane\s*\(', re.IGNORECASE),
            "phantom API MidwayPhysics.SpawnStaticPlane — does not exist",
            "SpawnStaticPlane is not in the bridge contract. "
            "Use SpawnStaticBox with a very small Y half-extent to approximate a ground plane, "
            "e.g. MidwayPhysics.SpawnStaticBox(0, 0, 0, 10, 0.05, 10).",
        ),
        # C10: Duplicate top-level function definition in Lua.
        # Detected via post-guard check below — regex finds all names first.
        # C12: Bare OnStep defined — reliable two-pass check (see below).
        # C++: re-registering existing bridge spawn functions.
        (
            "C++",
            re.compile(
                r'sol::state[^;]{0,200}(?:SpawnDynamic|SpawnStatic|SpawnKinematic|SpawnSensor)',
                re.DOTALL | re.IGNORECASE,
            ),
            "re-registration of existing bridge spawn function",
            "SpawnDynamic/Static/Kinematic/SensorXxx are already registered in the sol2 "
            "bridge contract. Re-adding them causes duplicate bindings. "
            "Remove the C++ registration task and use the existing API from Lua instead.",
        ),
        # F17: Singleton method bound as free function pointer.
        (
            "C++",
            re.compile(
                r'set_function\s*\(\s*"[^"]+"\s*,\s*&[A-Z][A-Za-z_0-9]*::[A-Z][A-Za-z_0-9]*\s*\)',
                re.IGNORECASE,
            ),
            "singleton method bound as free function pointer via set_function",
            "Instance methods on singletons (e.g. &Engine::GetStreak) cannot be bound "
            "directly as free function pointers. Wrap in a lambda: "
            '[]{ return Engine::GetStreak(); }',
        ),
        # F18: Engine.Method() dot-notation in C++ (should be Engine::Method()).
        (
            "C++",
            re.compile(r'\bEngine\.[A-Z][A-Za-z_0-9]*\s*\(', re.IGNORECASE),
            "Engine.Method() dot-notation in C++ — should be Engine::Method()",
            "C++ uses the :: scope operator, not the dot operator. "
            "Replace Engine.GetStreak() with Engine::GetStreak() (or via the singleton accessor).",
        ),
        # F18b: MidwayPhysics.Method() dot-notation in C++ (not Lua).
        (
            "C++",
            re.compile(r'\bMidwayPhysics\.[A-Z][A-Za-z_0-9]*\s*\(', re.IGNORECASE),
            "MidwayPhysics.Method() dot-notation in C++ — should be MidwayPhysics::Method()",
            "C++ uses the :: scope operator. "
            "Replace MidwayPhysics.ApplyImpulse(...) with MidwayPhysics::ApplyImpulse(...) etc.",
        ),
        # C15: MidwayPhysics.log / MidwayPhysics.log_message — not in the bridge.
        (
            "Lua",
            re.compile(r'\bMidwayPhysics\.(log_message|log)\s*\(', re.IGNORECASE),
            "phantom API MidwayPhysics.log_message / MidwayPhysics.log — not registered in the bridge",
            "MidwayPhysics exposes no logging function. Use print() for Lua-side logging.",
        ),
        # C16: require() referencing a wrapper or non-existent attraction file.
        # Attractions are loaded by AttractionManager, not via Lua require().
        (
            "Lua",
            re.compile(r'\brequire\s*\(\s*["\'](?:wrapped_|skeebalooks|skeeball_wrap)', re.IGNORECASE),
            "require() targeting a non-existent wrapper file",
            "Attraction scripts are loaded by AttractionManager directly. "
            "Do not use require() to load other attraction files. "
            "Define OnLoadAttraction() and OnUnload() directly in the target file.",
        ),
    ]

    for tid, content in list(ctx.all_results_dict.items()):
        if not content:
            continue
        task_obj = ctx.task_map.get(tid) if ctx.task_map else None
        domain = getattr(task_obj, "agent", None) if task_obj else None

        for (guard_domain, pattern, label, explanation) in _GUARDS:
            if guard_domain and domain and domain != guard_domain:
                continue
            if pattern.search(content):
                ctx.pre_flight_errors += (
                    f"\n## Static Pattern Violation — Task {tid} [{domain or '?'}]\n"
                    f"**Rule:** {label}\n"
                    f"**Why this is always wrong:** {explanation}\n"
                    f"Fix this before the reviewer sees the code.\n"
                )
                print(f"  [Static Guard] ❌ Task {tid} [{domain or '?'}]: {label}")

        # ── C6: SpawnDynamicXxx / SpawnStaticXxx argument count hard check ────
        # Maps function name → (min_args, max_args).
        # Optional trailing args (e.g. mass, yawDeg) widen the max bound.
        # The bridge registers mass via sol::object so it is always optional in Lua.
        _SPAWN_SIGS = {
            # name:                (min, max)
            "SpawnDynamicSphere":   (4, 5),  # lx ly lz r [mass]
            "SpawnDynamicBox":      (6, 7),  # lx ly lz w h d [mass]
            "SpawnDynamicCapsule":  (5, 6),  # lx ly lz halfH r [mass]
            "SpawnDynamicCylinder": (5, 6),  # lx ly lz halfH r [mass]
            "SpawnDynamicMesh":     (6, 6),  # lx ly lz yaw mass path
            "SpawnDynamicBoxR":     (7, 8),  # lx ly lz w h d mass [yawDeg]
            "SpawnDynamicSphereR":  (5, 6),  # lx ly lz r mass [yawDeg]
            "SpawnDynamicCapsuleR": (6, 7),
            "SpawnDynamicCylinderR":(6, 7),
            "SpawnStaticBox":       (6, 6),  # lx ly lz w h d
            "SpawnStaticSphere":    (4, 4),  # lx ly lz r
            "SpawnStaticCapsule":   (5, 5),  # lx ly lz halfH r
            "SpawnStaticCylinder":  (5, 5),  # lx ly lz halfH r
            "SpawnStaticMesh":      (4, 8),  # lx ly lz yaw path [sx sy sz]
            "SpawnStaticBoxR":      (7, 7),
            "SpawnStaticSphereR":   (5, 5),
            "SpawnStaticCapsuleR":  (6, 6),
            "SpawnStaticCylinderR": (6, 6),
            "SpawnKinematicBox":    (6, 6),
            "SpawnKinematicSphere": (4, 4),
            "SpawnKinematicCapsule":(5, 5),
            "SpawnKinematicCylinder":(5, 5),
            "SpawnKinematicBoxR":   (7, 7),
            "SpawnSensorBox":       (6, 6),
            "SpawnSensorSphere":    (4, 4),
        }
        if domain == "Lua":
            for _call_m in re.finditer(
                r'MidwayPhysics\.(Spawn\w+)\s*\(([^)]{0,200})\)',
                content, re.IGNORECASE
            ):
                _fn_name = _call_m.group(1)
                _args_str = _call_m.group(2).strip()
                _expected = _SPAWN_SIGS.get(_fn_name)
                if _expected is None:
                    # Unknown spawn call — flag as phantom API (C9 overlap)
                    ctx.pre_flight_errors += (
                        f"\n## Static Pattern Violation — Task {tid} [Lua]\n"
                        f"**Rule:** phantom spawn API MidwayPhysics.{_fn_name}\n"
                        f"**Why this is always wrong:** {_fn_name} is not in the bridge contract. "
                        f"Check docs/engine_lua_bridge_contract.md for the approved list.\n"
                        f"Fix this before the reviewer sees the code.\n"
                    )
                    print(f"  [Static Guard] ❌ Task {tid} [Lua]: phantom spawn API {_fn_name}")
                    continue
                if _args_str:
                    # Count top-level commas (ignore commas inside nested parens)
                    _depth = 0
                    _commas = 0
                    for _ch in _args_str:
                        if _ch == '(':
                            _depth += 1
                        elif _ch == ')':
                            _depth -= 1
                        elif _ch == ',' and _depth == 0:
                            _commas += 1
                    _actual = _commas + 1
                    _min_exp, _max_exp = _expected
                    if not (_min_exp <= _actual <= _max_exp):
                        _sig_hint = f"{_fn_name}({_min_exp}–{_max_exp} args)"
                        ctx.pre_flight_errors += (
                            f"\n## Static Pattern Violation — Task {tid} [Lua]\n"
                            f"**Rule:** MidwayPhysics.{_fn_name} wrong argument count "
                            f"(got {_actual}, expected {_min_exp}–{_max_exp})\n"
                            f"**Why this is always wrong:** Wrong argument count causes a "
                            f"runtime error or silent incorrect physics. "
                            f"Signature: {_sig_hint}.\n"
                            f"Fix this before the reviewer sees the code.\n"
                        )
                        print(f"  [Static Guard] ❌ Task {tid} [Lua]: {_fn_name} arg count {_actual}≠{_min_exp}–{_max_exp}")

        # ── C9: Phantom API scan — Lua calls not in the bridge contract ───────
        if domain == "Lua":
            _approved_lua = getattr(ctx, '_bridge_exclusion_set', set())
            if _approved_lua:
                # Engine namespaces whose methods we validate; user-defined table
                # methods (skeeball.score, self.handle, etc.) are NOT engine calls
                # and must never be flagged as phantom APIs.
                _ENGINE_NAMESPACES = {"midwayphysics", "engine", "attractionconstants", "sol"}
                # Comprehensive list of all valid bridge calls (lowercased).
                # Keeps C9 from firing when the cartridge exclusion set has a parse gap.
                _always_ok = {
                    # MidwayPhysics — spawn
                    "midwayphysics.spawnstaticbox", "midwayphysics.spawnstaticsphere",
                    "midwayphysics.spawnstaticcapsule", "midwayphysics.spawnstaticcylinder",
                    "midwayphysics.spawnstaticmesh",
                    "midwayphysics.spawnstaticboxr", "midwayphysics.spawnstaticspherer",
                    "midwayphysics.spawnstaticcapsuler", "midwayphysics.spawnstaticcylinderr",
                    "midwayphysics.spawnkinematicbox", "midwayphysics.spawnkinematicsphere",
                    "midwayphysics.spawnkinematiccapsule", "midwayphysics.spawnkinematiccylinder",
                    "midwayphysics.spawnkinematicboxr",
                    "midwayphysics.spawndynamicbox", "midwayphysics.spawndynamicsphere",
                    "midwayphysics.spawndynamiccapsule", "midwayphysics.spawndynamiccylinder",
                    "midwayphysics.spawndynamicmesh",
                    "midwayphysics.spawndynamicboxr", "midwayphysics.spawndynamicspherer",
                    "midwayphysics.spawndynamiccapsuler", "midwayphysics.spawndynamiccylinderr",
                    "midwayphysics.spawnsensorbox", "midwayphysics.spawnsensorsphere",
                    # MidwayPhysics — queries / movement
                    "midwayphysics.destroybody", "midwayphysics.onstep",
                    "midwayphysics.getposition", "midwayphysics.getvelocity",
                    "midwayphysics.getrotation", "midwayphysics.isactive",
                    "midwayphysics.issensortriggered",
                    "midwayphysics.movekinematic",
                    # MidwayPhysics — impulse / velocity
                    "midwayphysics.setlinearvelocity", "midwayphysics.addlinearvelocity",
                    "midwayphysics.applyimpulse", "midwayphysics.applyangularimpulse",
                    # MidwayPhysics — per-body properties
                    "midwayphysics.setfriction", "midwayphysics.setrestitution",
                    "midwayphysics.setgravityfactor", "midwayphysics.setmass",
                    "midwayphysics.setlineardamping", "midwayphysics.setangulardamping",
                    # MidwayPhysics — pools
                    "midwayphysics.createpool", "midwayphysics.poolacquire",
                    "midwayphysics.poolreturn", "midwayphysics.poolcullbelow",
                    "midwayphysics.poolfree", "midwayphysics.pooltotal",
                    # Engine economy
                    "engine.awardtickets", "engine.awardtokens",
                    "engine.gettickets", "engine.gettokens", "engine.getstreak",
                    # AttractionConstants
                    "attractionconstants.modifiers", "attractionconstants.booth",
                    "attractionconstants.runtime",
                    # Lua stdlib (belt-and-suspenders; namespace guard below also covers these)
                    "table.insert", "table.remove", "table.concat", "table.sort",
                    "math.floor", "math.ceil", "math.abs", "math.max", "math.min",
                    "math.sqrt", "math.random", "string.format", "string.len",
                    "string.sub", "string.find", "string.gsub",
                    "io.open", "io.close", "os.time", "os.clock",
                }
                _approved_lower = _approved_lua | _always_ok
                for _api_m in re.finditer(
                    r'\b([A-Za-z_]\w*\.[A-Za-z_]\w*)\s*\(',
                    content,
                ):
                    _call = _api_m.group(1).lower()
                    _ns = _call.split(".")[0]
                    # Only validate calls whose namespace is a known engine namespace.
                    # User-defined table methods (e.g. skeeball.score()) are NOT engine
                    # calls and must never be reported as phantom APIs.
                    if _ns not in _ENGINE_NAMESPACES:
                        continue
                    # Skip if it matches any approved name
                    if _call in _approved_lower:
                        continue
                    # Skip Lua stdlib namespaces
                    if _ns in ("math", "string", "table", "io", "os", "coroutine",
                               "package", "debug", "utf8"):
                        continue
                    ctx.pre_flight_errors += (
                        f"\n## Static Pattern Violation — Task {tid} [Lua]\n"
                        f"**Rule:** phantom API call '{_api_m.group(1)}'\n"
                        f"**Why this is always wrong:** '{_api_m.group(1)}' is not in the "
                        f"approved bridge contract. Any engine call not on the approved list "
                        f"will crash at runtime. Check docs/engine_lua_bridge_contract.md.\n"
                        f"Fix this before the reviewer sees the code.\n"
                    )
                    print(f"  [Static Guard] ❌ Task {tid} [Lua]: phantom API '{_api_m.group(1)}'")

        # ── C10: Duplicate top-level function definition in Lua ───────────────
        if domain == "Lua":
            _fn_names_seen: dict = {}
            for _fn_m in re.finditer(r'^function\s+(\w+)\s*\(', content, re.MULTILINE):
                _name = _fn_m.group(1)
                _fn_names_seen[_name] = _fn_names_seen.get(_name, 0) + 1
            for _name, _count in _fn_names_seen.items():
                if _count > 1:
                    ctx.pre_flight_errors += (
                        f"\n## Static Pattern Violation — Task {tid} [Lua]\n"
                        f"**Rule:** duplicate function definition '{_name}' ({_count}×)\n"
                        f"**Why this is always wrong:** Lua silently overwrites the first "
                        f"definition. The second definition wins, causing unpredictable "
                        f"behaviour. Remove the duplicate.\n"
                        f"Fix this before the reviewer sees the code.\n"
                    )
                    print(f"  [Static Guard] ❌ Task {tid} [Lua]: duplicate function '{_name}'")

        # ── C11: Local variable used outside its defining scope ───────────────
        # Detect: local X declared inside OnLoad/OnLoadStatic body,
        # then referenced inside OnUnload body without re-declaration.
        if domain == "Lua":
            _func_bodies: dict = {}
            for _fb_m in re.finditer(
                r'^(?:local\s+)?function\s+(\w+)\s*\([^)]*\)(.*?)^end\b',
                content, re.DOTALL | re.MULTILINE
            ):
                _func_bodies[_fb_m.group(1)] = _fb_m.group(2)
            _spawn_fns = {"OnLoad", "OnLoadStatic"}
            _cleanup_fns = {"OnUnload"}
            _defined_locals: set = set()
            for _fn in _spawn_fns:
                body = _func_bodies.get(_fn, "")
                for _loc_m in re.finditer(r'\blocal\s+(\w+)\s*=', body):
                    _defined_locals.add(_loc_m.group(1))
            for _fn in _cleanup_fns:
                body = _func_bodies.get(_fn, "")
                if not body:
                    continue
                for _ref_m in re.finditer(r'\b(\w+)\b', body):
                    _ref = _ref_m.group(1)
                    if _ref in _defined_locals:
                        # Check it's not re-declared locally in OnUnload
                        if not re.search(r'\blocal\s+' + re.escape(_ref) + r'\b', body):
                            ctx.pre_flight_errors += (
                                f"\n## Static Pattern Violation — Task {tid} [Lua]\n"
                                f"**Rule:** local variable '{_ref}' used in {_fn} but declared in OnLoad/OnLoadStatic\n"
                                f"**Why this is always wrong:** Local variables are scoped to their "
                                f"function. '{_ref}' will be nil in {_fn}. "
                                f"Declare it as a module-level (file-scope) variable instead.\n"
                                f"Fix this before the reviewer sees the code.\n"
                            )
                            print(f"  [Static Guard] ❌ Task {tid} [Lua]: local '{_ref}' out-of-scope in {_fn}")
                            break  # one error per function pair is sufficient

        # ── C12: Bare OnStep defined without MidwayPhysics.OnStep registration ─
        # Two-pass: (1) bare global OnStep exists, (2) MidwayPhysics.OnStep absent.
        if domain == "Lua":
            _has_bare_onstep = bool(re.search(
                r'^(?:local\s+)?function\s+OnStep\s*\(',
                content, re.MULTILINE
            ))
            _has_registered_onstep = bool(re.search(
                r'MidwayPhysics\.OnStep\s*\(',
                content, re.IGNORECASE
            ))
            if _has_bare_onstep and not _has_registered_onstep:
                ctx.pre_flight_errors += (
                    f"\n## Static Pattern Violation — Task {tid} [Lua]\n"
                    f"**Rule:** OnStep defined as bare global but MidwayPhysics.OnStep never registered\n"
                    f"**Why this is always wrong:** The engine ignores bare OnStep globals. "
                    f"You MUST register via MidwayPhysics.OnStep(function(dt) ... end) inside OnLoad().\n"
                    f"Fix this before the reviewer sees the code.\n"
                )
                print(f"  [Static Guard] ❌ Task {tid} [Lua]: bare OnStep without MidwayPhysics.OnStep")


def _run_preflight_checks(ctx: PipelineContext) -> PipelineContext:
    """
    Phase 6 pre-flight: run background compilers (C++ / Make), Lua syntax
    checks, and Pro Mode unit test compilation/execution. If errors are
    detected, invoke the Architect Syntax Fix cycle to patch them.
    """
    print("  [Pre-Flight] Flushing results to workspace before compilation...")
    _flush_results_to_workspace(ctx)
    ctx.pre_flight_errors = ""

    # Universal guard: catch empty or prose-only outputs before any compiler runs.
    _inject_empty_output_errors(ctx)

    # Static pattern guard: catch known-bad patterns that are always wrong
    # regardless of compiler availability.
    _inject_static_pattern_errors(ctx)

    # Platform-aware compilation check — only run if a configured build tree exists.
    _cmake_cache = ctx.project_root / "CMakeCache.txt"
    _makefile = ctx.project_root / "Makefile"
    try:
        if sys.platform == "win32":
            if not _cmake_cache.is_file():
                print("  [Pre-Flight] No CMakeCache.txt found — skipping cmake build check.")
            else:
                cmake_build = subprocess.run(
                    ["cmake", "--build", "."],
                    capture_output=True, text=True, cwd=ctx.project_root,
                    shell=True, timeout=30,
                )
                if cmake_build.returncode != 0:
                    err_tail = "\n".join(cmake_build.stderr.splitlines()[-50:])
                    # Discard cmake infrastructure errors — only propagate real compiler diagnostics.
                    _infra_error = any(
                        kw in err_tail.lower()
                        for kw in ("could not load cache", "no cmake_cache", "run cmake first",
                                   "cmake error", "cmake warning", "configuring incomplete")
                    )
                    if not _infra_error:
                        ctx.pre_flight_errors += f"\n## C++ Compiler Errors:\n```\n{err_tail}\n```"
                        # Circuit Breaker: isolate retry strikes to matching domain agents
                        failing_domains = ("C++", "PHYS", "NET", "SHADER")
                        for tid in list(ctx.all_results_dict.keys()):
                            task_obj = ctx.task_map.get(tid)
                            if task_obj and task_obj.agent in failing_domains:
                                ctx.retry_counts[tid] = ctx.retry_counts.get(tid, 0) + 1
                                print(f"  [Circuit Breaker] {tid} ({task_obj.agent}) retry_count incremented to {ctx.retry_counts[tid]} (build failure)")
                    else:
                        print(f"  [Pre-Flight] cmake infrastructure error suppressed (not a compiler diagnostic): {err_tail[:120]}")
        else:
            if not _makefile.is_file():
                print("  [Pre-Flight] No Makefile found — skipping make build check.")
            else:
                make_process = subprocess.run(
                    ["make", "-j4"], capture_output=True, text=True,
                    cwd=ctx.project_root, timeout=30,
                )
                if make_process.returncode != 0:
                    err_tail = "\n".join(make_process.stderr.splitlines()[-50:])
                    _infra_error = any(
                        kw in err_tail.lower()
                        for kw in ("no targets", "nothing to be done", "no rule to make")
                    )
                    if not _infra_error:
                        ctx.pre_flight_errors += f"\n## C++ Compiler Errors:\n```\n{err_tail}\n```"
                        # Circuit Breaker: isolate retry strikes to matching domain agents
                        failing_domains = ("C++", "PHYS", "NET", "SHADER")
                        for tid in list(ctx.all_results_dict.keys()):
                            task_obj = ctx.task_map.get(tid)
                            if task_obj and task_obj.agent in failing_domains:
                                ctx.retry_counts[tid] = ctx.retry_counts.get(tid, 0) + 1
                                print(f"  [Circuit Breaker] {tid} ({task_obj.agent}) retry_count incremented to {ctx.retry_counts[tid]} (build failure)")
                    else:
                        print(f"  [Pre-Flight] make infrastructure message suppressed: {err_tail[:120]}")
    except subprocess.TimeoutExpired:
        ctx.pre_flight_errors += "\n## Compiler Timeout:\n```\nC++ build timed out after 30s\n```\n"
    except Exception:
        pass

    # ── Pro Mode: Unit Test Compilation & Execution ────────────────────
    # Only runs when at least one task was actually flagged as math-heavy
    # and the user opted in (ctx.math_heavy_tasks is non-empty).
    if ctx.math_heavy_tasks:
        print("  [Pro Mode] Compiling and executing test suite...")
        test_build_dir = ctx.project_root / "build" / "tests"
        test_binary = test_build_dir / "run_tests"
        if sys.platform == "win32":
            test_binary = test_build_dir / "run_tests.exe"

        # Only attempt to build tests if a configured cmake build tree exists.
        _cmake_cache = ctx.project_root / "CMakeCache.txt"
        if not _cmake_cache.is_file():
            print("  [Pro Mode] No CMakeCache.txt — test binary build skipped (no configured build tree).")
            ctx.pre_flight_errors += (
                "\n## Pro Mode — Test Suite Note:\n"
                "```\nNo CMake build tree configured. Generated test files are saved to tests/ "
                "for manual review but cannot be compiled automatically until cmake is configured.\n```\n"
            )
        else:
            # Build test target if cmake project is configured
            test_build_ok = True
            try:
                test_build_proc = subprocess.run(
                    ["cmake", "--build", ".", "--target", "run_tests"],
                    capture_output=True, text=True, cwd=ctx.project_root,
                    shell=True, timeout=60,
                )
                if test_build_proc.returncode != 0:
                    test_build_ok = False
                    err_tail = "\n".join(test_build_proc.stderr.splitlines()[-50:])
                    _infra_kws = (
                        "could not load cache", "no cmake_cache", "run cmake first",
                        "cmake error", "cmake warning", "configuring incomplete",
                    )
                    if any(kw in err_tail.lower() for kw in _infra_kws):
                        print(f"  [Pro Mode] cmake infrastructure error suppressed during test build: {err_tail[:120]}")
                    else:
                        ctx.pre_flight_errors += (
                            f"\n## Test Suite Compilation Errors:\n```\n{err_tail}\n```\n"
                        )
                        print(f"  [Pro Mode] ⚠ Test suite failed to compile — treating as [VETO]")
            except (subprocess.TimeoutExpired, Exception) as e:
                test_build_ok = False
                ctx.pre_flight_errors += (
                    f"\n## Test Suite Compilation Exception:\n```\n{e}\n```\n"
                )

            if test_build_ok and test_binary.is_file():
                try:
                    test_run = subprocess.run(
                        [str(test_binary)], capture_output=True, text=True,
                        timeout=60,
                    )
                    if test_run.returncode != 0:
                        test_stderr = test_run.stderr.strip() or test_run.stdout.strip()
                        ctx.pre_flight_errors += (
                            f"\n## Unit Test Failures:\n```\n{test_stderr[:2000]}\n```\n"
                        )
                        print(f"  [Pro Mode] ⛔ Tests FAILED — feeding errors back to domain agents")
                    else:
                        print(f"  [Pro Mode] ✅ All unit tests passed!")
                except subprocess.TimeoutExpired:
                    ctx.pre_flight_errors += (
                        "\n## Unit Test Timeout:\n```\nTest binary timed out after 60s\n```\n"
                    )
                except Exception as e:
                    ctx.pre_flight_errors += (
                        f"\n## Unit Test Execution Error:\n```\n{e}\n```\n"
                    )

    for lf in ctx.project_root.rglob("*.lua"):
        try:
            lua_proc = subprocess.run(
                ["luac", "-p", str(lf)], capture_output=True, text=True, timeout=30,
            )
            if lua_proc.returncode != 0:
                ctx.pre_flight_errors += (
                    f"\n## Lua Syntax Error in {lf.name}:\n```\n{lua_proc.stderr}\n```"
                )
        except subprocess.TimeoutExpired:
            ctx.pre_flight_errors += (
                f"\n## Lua Syntax Error in {lf.name}:\n```\nluac timed out after 30s\n```\n"
            )
        except Exception:
            pass

    # ── Architect Syntax Fix Cycle ─────────────────────────────────────
    if ctx.pre_flight_errors:
        print("  [Pre-Flight] Syntax errors detected. Forcing Architect Syntax Fix (per-domain).")

        # E16: Snapshot outputs that are both non-empty AND free of static guard
        # violations as the anchor for the arch fix.  Anchoring to a broken output
        # (the previous behaviour) re-introduces the same errors on every cycle.
        # Strategy: re-run the guard patterns against each output; only outputs
        # with zero matches are eligible as anchors.
        _GUARD_PATTERNS_QUICK = [
            re.compile(r'MidwayPhysics\.SpawnStaticPlane', re.IGNORECASE),
            re.compile(r'MidwayPhysics\.ApplyForce\b', re.IGNORECASE),
            re.compile(r'MidwayPhysics\.SpawnStaticMesh\s*\(\s*[\'"]', re.IGNORECASE),
            re.compile(r'(?<!MidwayPhysics\.)\bDestroyBody\s*\(', re.IGNORECASE),
            re.compile(r'\bsol\s*\.\s*(?:set_function|new_usertype|state)\s*\(', re.IGNORECASE),
        ]
        _last_good: dict = {}
        for _tid, _out in ctx.all_results_dict.items():
            if not _out or not _out.strip() or _is_comment_only(_out):
                continue
            if not _task_has_code(_out):
                continue
            # Reject if any quick guard fires on this output
            if any(p.search(_out) for p in _GUARD_PATTERNS_QUICK):
                continue
            _last_good[_tid] = _out

        # Guard: skip any block the LLM filled with an [ERROR] stub, a bare
        # delegation token, or a placeholder — these would poison the next cycle.
        _error_stub_re = re.compile(
            r'^\s*(\[ERROR\]|\[DELEGATE|\[QUERY:DOC|\[CONF:|\[REVISE|<<<<<<|import sys'
            r'|name .sys. is not defined'
            r'|int task_\d+_solution|void task_\d+_solution)',
            re.IGNORECASE | re.MULTILINE
        )

        # Group failing tasks by domain so each call is scoped to one language.
        # This prevents qwen2.5-coder from being asked to write Lua, C++, and
        # PHYS simultaneously and falling back to language-agnostic placeholders.
        # NOTE: Task uses .agent (canonical domain key like "Lua", "C++") not .domain.
        from collections import defaultdict
        domain_task_groups: dict = defaultdict(list)
        for tid, output in ctx.all_results_dict.items():
            task_obj = ctx.task_map.get(tid)
            domain = (task_obj.agent if task_obj and getattr(task_obj, 'agent', None)
                      else "Unknown")
            domain_task_groups[domain].append((tid, output))

        for domain, task_pairs in domain_task_groups.items():
            # Build labelled task blocks — LLM is explicitly instructed to
            # echo back the same ### task_N header so the extraction regex works.
            task_blocks = [f"### {tid}\n{output}" for tid, output in task_pairs]
            domain_code_str = "\n\n".join(task_blocks)
            failing_tids = [tid for tid, _ in task_pairs]

            # E16: Build per-task anchor blocks from the last-good snapshot.
            # Collapsed to a safe budget via block-aware paging so VRAM is not
            # blown by injecting multiple large outputs simultaneously.
            _ANCHOR_BUDGET = 1500  # chars per task anchor
            _anchor_blocks = []
            for _atid, _aout in task_pairs:
                _anchor = _last_good.get(_atid)
                if _anchor:
                    _collapsed_anchor = TokenBudget._block_aware_collapse(_anchor, _ANCHOR_BUDGET)
                    _anchor_blocks.append(
                        f"### {_atid} [ANCHOR — repair this, do NOT rewrite from scratch]\n"
                        f"{_collapsed_anchor}"
                    )
            _anchor_str = (
                "\n\n## Previous Implementation (ANCHOR)\n"
                "The following is the last known implementation for each task.\n"
                "You MUST base your fix on this code. Do NOT discard it and write an empty scaffold.\n"
                + "\n\n".join(_anchor_blocks)
                + "\n"
            ) if _anchor_blocks else ""

            fix_input = (
                f"Domain: [{domain}]\n"
                f"The following {domain} task(s) failed pre-flight checks.\n"
                f"Errors:\n{TokenBudget._block_aware_collapse(ctx.pre_flight_errors, 1200)}\n"
                f"{_anchor_str}"
                f"CRITICAL OUTPUT FORMAT RULES (violations will be discarded):\n"
                f"1. Output ONLY working {domain} code. Do NOT output any [DELEGATE:...],"
                f" [QUERY:DOC:...], [CONF:...], prose explanations, or placeholder stubs.\n"
                f"2. Prefix EACH task's fixed code block with its exact header line: ### task_N\n"
                f"   Example for two tasks:\n"
                f"   ### task_1\n   ```lua\n   -- corrected code here\n   ```\n\n"
                f"   ### task_2\n   ```lua\n   -- corrected code here\n   ```\n"
                f"3. Do NOT invent new API names. Use only approved engine primitives.\n"
            )

            # Use domain-specific system prompt if available, else ARCHITECT_FIX_SYSTEM
            from domain_registry import get_agent_system as _get_sys
            domain_system = _get_sys(domain) or _prompts_mod.ARCHITECT_FIX_SYSTEM

            # Use domain-specific model: prefer live cartridge registry, then kernel dict
            _live_registry = getattr(ctx, 'domain_registry', None) or {}
            from pipeline import ALL_DOMAINS as _kernel_domains
            fix_model = (
                _live_registry.get(domain, {}).get('model')
                or _kernel_domains.get(domain, {}).get('model')
                or __import__('pipeline').EXECUTION_MODEL
            )

            fixed_str = call_ollama(
                domain_system, fix_input,
                f"Architect Syntax Fix [{domain}]", fix_model
            )

            # ── Primary extraction: LLM used required ### task_N headers ────
            # The regex tolerates any trailing label the model echoes after the
            # task ID (e.g. "### task_1 [ANCHOR — repair this...]") because the
            # prompt injects such labels and the model verbatim-echoes them.
            # Previously the regex required ONLY whitespace after the ID, causing
            # every arch-fix round to produce "no valid block" and retain bad code.
            applied_tids: set = set()
            for match in re.finditer(
                r"###\s*(task_\d+)[^\n]*\n(.*?)(?=###\s*task_\d+|\Z)", fixed_str, re.DOTALL
            ):
                tid = match.group(1).strip()
                fixed_code = match.group(2).strip()
                if not fixed_code or _error_stub_re.search(fixed_code):
                    print(f"  [Arch Fix] Skipping stub/error block for {tid} ({domain}) "
                          f"— LLM did not produce valid code.")
                    continue
                if _is_comment_only(fixed_code) or not _task_has_code(fixed_code):
                    print(f"  [Arch Fix] Skipping comment-only/empty block for {tid} ({domain}) "
                          f"— output contains no real implementation.")
                    continue
                if tid in applied_tids:
                    print(f"  [Arch Fix] ⚠ Duplicate block for {tid} ({domain}) — ignoring second copy.")
                    continue
                if tid in ctx.all_results_dict:
                    ctx.all_results_dict[tid] = fixed_code
                    applied_tids.add(tid)
                    print(f"  [Arch Fix] ✅ Applied fix for {tid} ({domain})")
                    # Ledger: record final committed signatures (arch-fix may be
                    # the first time real code appears for a task that delegated)
                    try:
                        from ledger import update_internal_api_ledger
                        update_internal_api_ledger(fixed_code, domain)
                    except Exception:
                        pass

            # ── Fallback extraction: LLM ignored headers but produced code ──
            # When only one task is failing OR the LLM produced exactly one
            # fenced code block, apply it to all un-patched failing tasks.
            unapplied = [t for t in failing_tids if t not in applied_tids]
            if unapplied:
                _fence_re = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
                code_blocks = [
                    m.group(1).strip() for m in _fence_re.finditer(fixed_str)
                    if m.group(1).strip()
                    and not _error_stub_re.search(m.group(1))
                    and not _is_comment_only(m.group(1))
                    and _task_has_code(m.group(1))
                ]
                if len(code_blocks) == 1 and len(unapplied) == 1:
                    # Single block, single un-patched task — safe 1:1 apply.
                    tid = unapplied[0]
                    if tid in ctx.all_results_dict:
                        ctx.all_results_dict[tid] = code_blocks[0]
                        print(f"  [Arch Fix] ✅ Applied single-block fallback fix for {tid} ({domain})")
                        try:
                            from ledger import update_internal_api_ledger
                            update_internal_api_ledger(code_blocks[0], domain)
                        except Exception:
                            pass
                elif len(code_blocks) == 1 and len(unapplied) > 1:
                    # One block but multiple un-patched tasks — refuse to broadcast;
                    # it would stamp every task with the same content.
                    print(f"  [Arch Fix] ⚠ Single fallback block cannot be broadcast to "
                          f"{len(unapplied)} tasks ({domain}) — leaving each task for retry.")
                elif len(code_blocks) == len(unapplied):
                    # One block per un-patched task — zip in order
                    for tid, blk in zip(unapplied, code_blocks):
                        if tid in ctx.all_results_dict:
                            ctx.all_results_dict[tid] = blk
                            print(f"  [Arch Fix] ✅ Applied ordered fallback fix for {tid} ({domain})")
                            try:
                                from ledger import update_internal_api_ledger
                                update_internal_api_ledger(blk, domain)
                            except Exception:
                                pass
                else:
                    for tid in unapplied:
                        print(f"  [Arch Fix] ⚠ No valid fallback block for {tid} ({domain}) — retaining previous output.")

    return ctx
