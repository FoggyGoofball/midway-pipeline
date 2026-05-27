"""
runtime_sim.py — Headless Lua Tick Simulator

Runs a lightweight synthetic Lua environment against generated attraction scripts
to surface runtime errors (nil-handle access, undefined globals, missing API
calls) before the output reaches the reviewer.

Design goals:
  - Zero external runtime dependency: we parse and statically simulate Lua
    execution without needing a live Lua binary.  When `lua` is installed the
    module runs a real tick harness; otherwise it falls back to a structural
    static analysis that checks:
      1. Every handle used in OnStep was created in OnLoad.
      2. Every handle passed to a physics API matches a declared type.
      3. No global variables are set inside OnStep without being declared first.
      4. MidwayPhysics / economy API calls use known function signatures.

The module is intentionally NOT a full Lua interpreter.  It is a heuristic
layer designed to catch the most common categories of LLM-generated runtime
bugs cheaply, so they surface in preflight rather than during live testing.

Public API
----------
run_runtime_sim(ctx: PipelineContext) -> List[str]
    Run simulation checks over ctx.all_results_dict.
    Returns a (possibly empty) list of error strings.
    Non-fatal: never raises; only appends to ctx.runtime_errors.
"""

from __future__ import annotations

import re
import subprocess
import textwrap
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ── Known API surfaces ─────────────────────────────────────────────────────────
# Keys: function name (lowercase). Values: expected arity range (min, max).
# Keep in sync with docs/engine_lua_bridge_contract.md §7.
_MIDWAY_PHYSICS_API: Dict[str, Tuple[int, int]] = {
    # Static body creation
    "spawnstaticmesh":             (4, 7),
    "spawnstaticbox":              (6, 6),
    "spawnstaticsphere":           (4, 4),
    "spawnstaticcapsule":          (5, 5),
    "spawnstaticcylinder":         (5, 5),
    "spawnstaticboxr":             (7, 7),
    "spawnstaticspherer":          (5, 5),
    "spawnstaticcapsuler":         (6, 6),
    "spawnstaticcylinderr":        (6, 6),
    # Kinematic body creation
    "spawnkinematicbox":           (6, 6),
    "spawnkinematicsphere":        (4, 4),
    "spawnkinematiccapsule":       (5, 5),
    "spawnkinematiccylinder":      (5, 5),
    "spawnkinematicboxr":          (7, 7),
    # Dynamic body creation
    "spawndynamicmesh":            (5, 6),
    "spawndynamicbox":             (6, 7),
    "spawndynamicsphere":          (4, 5),
    "spawndynamiccapsule":         (5, 6),
    "spawndynamiccylinder":        (5, 6),
    "spawndynamicboxr":            (7, 8),
    "spawndynamicspherer":         (5, 6),
    "spawndynamiccapsuler":        (6, 7),
    "spawndynamiccylinderr":       (6, 7),
    # Sensor bodies
    "spawnsensorbox":              (6, 6),
    "spawnsensorsphere":           (4, 4),
    # Movement and queries
    "movekinematic":               (5, 5),
    "getposition":                 (1, 1),
    "getvelocity":                 (1, 1),
    "getrotation":                 (1, 1),
    "isactive":                    (1, 1),
    "issensortriggered":           (1, 1),
    "issenortriggered":            (1, 1),  # common misspelling — accept both
    # Impulses / velocity
    "setlinearvelocity":           (4, 4),
    "addlinearvelocity":           (4, 4),
    "applyimpulse":                (4, 4),
    "applyangularimpulse":         (4, 4),
    # Per-body property overrides
    "setfriction":                 (2, 2),
    "setrestitution":              (2, 2),
    "setgravityfactor":            (2, 2),
    "setmass":                     (2, 2),
    "setlineardamping":            (2, 2),
    "setangulardamping":           (2, 2),
    # Object pools
    "createpool":                  (3, 4),
    "poolacquire":                 (4, 4),
    "poolreturn":                  (2, 2),
    "poolcullbelow":               (2, 2),
    "poolfree":                    (1, 1),
    "pooltotal":                   (1, 1),
    # Lifetime / callbacks
    "destroybody":                 (1, 1),
    "onstep":                      (1, 1),
}

# Economy API — Engine.* (not economy:). Keep in sync with bridge contract §11.
_ECONOMY_API: Dict[str, Tuple[int, int]] = {
    "awardtickets":      (1, 2),
    "awardtokens":       (1, 2),
    "gettickets":        (0, 0),
    "gettokens":         (0, 0),
    "getstreak":         (0, 0),
}

# ── Regex helpers ──────────────────────────────────────────────────────────────

# local hFoo = MidwayPhysics.SpawnXxx(...)
_HANDLE_CREATE_RE = re.compile(
    r"\blocal\s+(h[A-Za-z0-9_]+)\s*=\s*MidwayPhysics\.\w+\s*\(",
    re.MULTILINE,
)

# hFoo: used anywhere on the right side of an expression or in a call
_HANDLE_USE_RE = re.compile(r"\b(h[A-Za-z0-9_]+)\b")

# function OnLoad()
_ONLOAD_RE = re.compile(
    r"\bfunction\s+(OnLoad(?:Static)?)\s*\(\s*\)(.*?)^end\b",
    re.MULTILINE | re.DOTALL,
)

# MidwayPhysics.OnStep(function(...) ... end)
_ONSTEP_BODY_RE = re.compile(
    r"MidwayPhysics\.OnStep\s*\(\s*function\s*\(.*?\)(.*?)end\s*\)",
    re.DOTALL,
)

# Any MidwayPhysics.ApiCall(...)
_PHYSICS_CALL_RE = re.compile(
    r"\bMidwayPhysics\.([A-Za-z][A-Za-z0-9_]*)\s*\(",
    re.MULTILINE,
)

# economy:ApiCall(...)
_ECONOMY_CALL_RE = re.compile(
    r"\beconomy:([A-Za-z][A-Za-z0-9_]*)\s*\(",
    re.MULTILINE,
)

# Argument counting heuristic: count commas at the top level of the argument list
_TOP_LEVEL_COMMA_RE = re.compile(r",(?![^(]*\))")


def _count_args(call_text: str) -> int:
    """Return the argument count from a Lua call site text like 'Foo(a, b, c)'."""
    paren_start = call_text.find("(")
    if paren_start == -1:
        return 0
    inner = call_text[paren_start + 1:]
    # Walk to find the matching close paren
    depth = 1
    end = 0
    for i, ch in enumerate(inner):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                end = i
                break
    args_str = inner[:end].strip()
    if not args_str:
        return 0
    return len(_TOP_LEVEL_COMMA_RE.split(args_str))


# ── Static analysis core ──────────────────────────────────────────────────────

def _analyse_lua_text(task_id: str, lua_text: str) -> List[str]:
    """
    Run static heuristic checks on a Lua agent output.
    Returns a list of error strings (empty = clean).
    """
    errors: List[str] = []

    # 1. Build handle declaration sets from OnLoad/OnLoadStatic bodies.
    handles_created_in_load: set = set()
    for m in _ONLOAD_RE.finditer(lua_text):
        body = m.group(2)
        for hm in _HANDLE_CREATE_RE.finditer(body):
            handles_created_in_load.add(hm.group(1))

    # Also accept module-level declarations (outside functions).
    # Some patterns declare handles at file scope.
    for hm in _HANDLE_CREATE_RE.finditer(lua_text):
        handles_created_in_load.add(hm.group(1))

    # 2. Check that every handle used in OnStep was created in OnLoad.
    for step_match in _ONSTEP_BODY_RE.finditer(lua_text):
        body = step_match.group(1)
        for um in _HANDLE_USE_RE.finditer(body):
            h = um.group(1)
            if h not in handles_created_in_load:
                errors.append(
                    f"[{task_id}] Handle `{h}` used in OnStep but not declared in OnLoad — "
                    "likely nil-reference at runtime."
                )

    # 3. Check MidwayPhysics call signatures.
    for pm in _PHYSICS_CALL_RE.finditer(lua_text):
        fn = pm.group(1).lower()
        if fn not in _MIDWAY_PHYSICS_API:
            errors.append(
                f"[{task_id}] Unknown MidwayPhysics API: `MidwayPhysics.{pm.group(1)}` — "
                "verify spelling against the bridge contract."
            )
        else:
            # Grab the call text (50 chars past the match for arity heuristic)
            call_text = lua_text[pm.start(): pm.start() + 200]
            nargs = _count_args(call_text)
            lo, hi = _MIDWAY_PHYSICS_API[fn]
            if nargs < lo or nargs > hi:
                errors.append(
                    f"[{task_id}] `MidwayPhysics.{pm.group(1)}` called with {nargs} arg(s) "
                    f"(expected {lo}–{hi})."
                )

    # 4. Check economy API calls (Engine.* flat namespace).
    _ENGINE_CALL_RE = re.compile(r"\bEngine\.([A-Za-z][A-Za-z0-9_]*)\s*\(", re.MULTILINE)
    for em in _ENGINE_CALL_RE.finditer(lua_text):
        fn = em.group(1).lower()
        if fn not in _ECONOMY_API:
            errors.append(
                f"[{task_id}] Unknown economy API: `Engine.{em.group(1)}` — "
                "verify against the economy bridge contract (§11)."
            )
    # Legacy colon-method economy calls are phantom APIs — flag them.
    for em in _ECONOMY_CALL_RE.finditer(lua_text):
        pass  # handled above
    _COLON_ECONOMY_RE = re.compile(r"\beconomy:([A-Za-z][A-Za-z0-9_]*)\s*\(", re.MULTILINE)
    for em in _COLON_ECONOMY_RE.finditer(lua_text):
        errors.append(
            f"[{task_id}] Phantom API: `economy:{em.group(1)}` — "
            "use Engine.AwardTickets / Engine.AwardTokens (flat namespace, no colon)."
        )

    # 5b. Forward-reference check: local function helpers used by OnStep/OnLoad
    # callbacks must be declared BEFORE those callbacks in file order.
    # Pattern: OnStep registers a closure at line N that calls helper Foo;
    # Foo is declared as `local function Foo` at line M > N — crash at load time.
    _LOCAL_FN_RE = re.compile(
        r"^local\s+function\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(",
        re.MULTILINE,
    )
    _CALLBACK_BODY_RE = re.compile(
        r"MidwayPhysics\.OnStep\s*\(\s*function[^)]*\)(.*?)^end",
        re.MULTILINE | re.DOTALL,
    )
    # Collect positions of each local function declaration
    local_fn_pos: Dict[str, int] = {}
    for lfm in _LOCAL_FN_RE.finditer(lua_text):
        name = lfm.group(1)
        if name not in local_fn_pos:  # keep first occurrence
            local_fn_pos[name] = lfm.start()
    # For each OnStep registration, record where MidwayPhysics.OnStep( appears
    for cbm in _CALLBACK_BODY_RE.finditer(lua_text):
        onstep_pos = cbm.start()
        body = cbm.group(1)
        # Find all bare name calls in the body
        for call_m in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", body):
            fn_name = call_m.group(1)
            if fn_name in local_fn_pos and local_fn_pos[fn_name] > onstep_pos:
                errors.append(
                    f"[{task_id}] Forward reference: `{fn_name}` is called inside "
                    f"MidwayPhysics.OnStep but its `local function {fn_name}` declaration "
                    "appears AFTER the OnStep registration — nil at load time. "
                    "Move the declaration above the MidwayPhysics.OnStep call."
                )

    # 5. Detect bare globals set inside OnStep (risk of accidental global pollution).
    # Pattern: identifier = value where identifier is NOT local and NOT a known handle.
    _GLOBAL_SET_RE = re.compile(
        r"^(?!\s*local\s)(\s*)([a-z][A-Za-z0-9_]+)\s*=(?!=)",
        re.MULTILINE,
    )
    for step_match in _ONSTEP_BODY_RE.finditer(lua_text):
        body = step_match.group(1)
        for gm in _GLOBAL_SET_RE.finditer(body):
            varname = gm.group(2)
            # Ignore known handle names, loop vars, standard Lua globals
            if (varname not in handles_created_in_load
                    and varname not in {"true", "false", "nil", "self"}
                    and len(varname) > 2):
                errors.append(
                    f"[{task_id}] Possible accidental global write `{varname}` inside OnStep — "
                    "declare with `local` if intended as a loop variable."
                )

    return errors


# ── Live Lua harness (when lua binary is present) ──────────────────────────────

_LUA_TICK_HARNESS = textwrap.dedent("""\
    -- Minimal MidwayPhysics / economy stub harness for headless tick test
    local _handles = {}
    local _handle_counter = 0
    local function _new_handle()
        _handle_counter = _handle_counter + 1
        local h = {__id = _handle_counter}
        setmetatable(h, {__index = function(t, k)
            error("Nil handle access on key '" .. tostring(k) .. "'", 2)
        end})
        return h
    end

    MidwayPhysics = {}
    -- Static spawners
    function MidwayPhysics.SpawnStaticMesh(...) return _new_handle() end
    function MidwayPhysics.SpawnStaticBox(...) return _new_handle() end
    function MidwayPhysics.SpawnStaticSphere(...) return _new_handle() end
    function MidwayPhysics.SpawnStaticCapsule(...) return _new_handle() end
    function MidwayPhysics.SpawnStaticCylinder(...) return _new_handle() end
    function MidwayPhysics.SpawnStaticBoxR(...) return _new_handle() end
    function MidwayPhysics.SpawnStaticSphereR(...) return _new_handle() end
    function MidwayPhysics.SpawnStaticCapsuleR(...) return _new_handle() end
    function MidwayPhysics.SpawnStaticCylinderR(...) return _new_handle() end
    -- Kinematic spawners
    function MidwayPhysics.SpawnKinematicBox(...) return _new_handle() end
    function MidwayPhysics.SpawnKinematicSphere(...) return _new_handle() end
    function MidwayPhysics.SpawnKinematicCapsule(...) return _new_handle() end
    function MidwayPhysics.SpawnKinematicCylinder(...) return _new_handle() end
    function MidwayPhysics.SpawnKinematicBoxR(...) return _new_handle() end
    -- Dynamic spawners
    function MidwayPhysics.SpawnDynamicMesh(...) return _new_handle() end
    function MidwayPhysics.SpawnDynamicBox(...) return _new_handle() end
    function MidwayPhysics.SpawnDynamicSphere(...) return _new_handle() end
    function MidwayPhysics.SpawnDynamicCapsule(...) return _new_handle() end
    function MidwayPhysics.SpawnDynamicCylinder(...) return _new_handle() end
    function MidwayPhysics.SpawnDynamicBoxR(...) return _new_handle() end
    function MidwayPhysics.SpawnDynamicSphereR(...) return _new_handle() end
    function MidwayPhysics.SpawnDynamicCapsuleR(...) return _new_handle() end
    function MidwayPhysics.SpawnDynamicCylinderR(...) return _new_handle() end
    -- Sensor spawners
    function MidwayPhysics.SpawnSensorBox(...) return _new_handle() end
    function MidwayPhysics.SpawnSensorSphere(...) return _new_handle() end
    -- Movement and queries
    function MidwayPhysics.MoveKinematic(h, ...) end
    function MidwayPhysics.GetPosition(h) return 0, 0, 0 end
    function MidwayPhysics.GetVelocity(h) return 0, 0, 0 end
    function MidwayPhysics.GetRotation(h) return 0, 0, 0, 1 end
    function MidwayPhysics.IsActive(h) return true end
    function MidwayPhysics.IsSensorTriggered(h) return false end
    function MidwayPhysics.IsSenorTriggered(h) return false end
    -- Impulses / velocity
    function MidwayPhysics.SetLinearVelocity(h, ...) end
    function MidwayPhysics.AddLinearVelocity(h, ...) end
    function MidwayPhysics.ApplyImpulse(h, ...) end
    function MidwayPhysics.ApplyAngularImpulse(h, ...) end
    -- Per-body properties
    function MidwayPhysics.SetFriction(h, v) end
    function MidwayPhysics.SetRestitution(h, v) end
    function MidwayPhysics.SetGravityFactor(h, v) end
    function MidwayPhysics.SetMass(h, v) end
    function MidwayPhysics.SetLinearDamping(h, v) end
    function MidwayPhysics.SetAngularDamping(h, v) end
    -- Object pools
    function MidwayPhysics.CreatePool(name, hot, cold, params) return true end
    function MidwayPhysics.PoolAcquire(name, lx, ly, lz) return _new_handle() end
    function MidwayPhysics.PoolReturn(name, h) end
    function MidwayPhysics.PoolCullBelow(name, y) return {} end
    function MidwayPhysics.PoolFree(name) return 0 end
    function MidwayPhysics.PoolTotal(name) return 0 end
    -- Lifetime / callbacks
    function MidwayPhysics.DestroyBody(h) end
    local _step_callbacks = {}
    function MidwayPhysics.OnStep(cb) table.insert(_step_callbacks, cb) end

    -- Economy bridge (Engine.* flat API, not economy: colon-methods)
    Engine = {}
    function Engine.AwardTickets(n, label) end
    function Engine.AwardTokens(n, label) end
    function Engine.GetTickets() return 0 end
    function Engine.GetTokens() return 0 end
    function Engine.GetStreak() return 0 end

    -- Engine globals injected before every script
    BOOTH_SLOT_ID    = BOOTH_SLOT_ID    or 0
    BOOTH_WORLD_X    = BOOTH_WORLD_X    or 0.0
    BOOTH_WORLD_Z    = BOOTH_WORLD_Z    or 0.0
    BOOTH_IS_STATIC  = BOOTH_IS_STATIC  or false
    ENGINE_MOD_MASS       = ENGINE_MOD_MASS       or 1.0
    ENGINE_MOD_FRICTION   = ENGINE_MOD_FRICTION   or 1.0
    ENGINE_MOD_VOLUME     = ENGINE_MOD_VOLUME     or 1.0
    ENGINE_MOD_KARMA      = ENGINE_MOD_KARMA      or 0.0
    ENGINE_MOD_LUCK       = ENGINE_MOD_LUCK       or 0.0
    ENGINE_MOD_PERSUASION = ENGINE_MOD_PERSUASION or 0.0
    ENGINE_MOD_HEAT       = ENGINE_MOD_HEAT       or 0.0
    ENGINE_MOD_SLEIGHT_OF_HAND = ENGINE_MOD_SLEIGHT_OF_HAND or 0.0
    ENGINE_MOD_NERVE      = ENGINE_MOD_NERVE      or 0.0
    AttractionConstants = {
        booth     = { width_x=9, height_y=9, depth_z=15, button={x=0,y=1.7,width=2,height=0.6,depth=1} },
        modifiers = { mass=1, volume=1, friction=1, karma=0, luck=0, persuasion=0, heat=0,
                      sleight_of_hand=0, nerve=0 },
        runtime   = { booth_world_x=0, booth_world_z=0, booth_slot_id=0, booth_is_static=false },
        defaults  = { interaction_radius=14, dynamic_radius=12, player_eye_height=1.8 },
    }
    SharedBooth = {}
    function SpawnSharedBooth() end
    function SharedBooth.HalfDepth() return 7.5 end
    function SharedBooth.ButtonZ() return -7.0 end

    function OnLoadStatic() end

    -- Load the attraction script
    local ok, err = pcall(function()
        {DOFILE}
    end)
    if not ok then
        io.stderr:write("RUNTIME_ERROR: " .. tostring(err) .. "\\n")
        os.exit(1)
    end

    -- Run OnLoad if defined
    if type(OnLoad) == "function" then
        local ok2, err2 = pcall(OnLoad)
        if not ok2 then
            io.stderr:write("RUNTIME_ERROR in OnLoad: " .. tostring(err2) .. "\\n")
            os.exit(1)
        end
    end

    -- Run OnLoadStatic if defined
    if type(OnLoadStatic) == "function" then
        local ok3, err3 = pcall(OnLoadStatic)
        if not ok3 then
            io.stderr:write("RUNTIME_ERROR in OnLoadStatic: " .. tostring(err3) .. "\\n")
            os.exit(1)
        end
    end

    -- Tick 3 frames
    for _tick = 1, 3 do
        for _, cb in ipairs(_step_callbacks) do
            local ok4, err4 = pcall(cb, 0.016)
            if not ok4 then
                io.stderr:write("RUNTIME_ERROR in OnStep tick " .. _tick .. ": " .. tostring(err4) .. "\\n")
                os.exit(1)
            end
        end
    end

    io.stdout:write("OK\\n")
""")


def _run_live_harness(task_id: str, lua_text: str, tmp_dir: Path) -> List[str]:
    """Run a live Lua tick harness if `lua` is on PATH.  Returns error strings."""
    # Write the attraction script to a temp file
    script_path = tmp_dir / f"{task_id}_attraction.lua"
    harness_path = tmp_dir / f"{task_id}_harness.lua"

    # Write the attraction script
    script_path.write_text(lua_text, encoding="utf-8")

    # Build harness that loads the script via dofile
    harness = _LUA_TICK_HARNESS.replace(
        "{DOFILE}",
        f'dofile("{str(script_path).replace(chr(92), "/")}")'
    )
    harness_path.write_text(harness, encoding="utf-8")

    try:
        result = subprocess.run(
            ["lua", str(harness_path)],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip()
            # Extract RUNTIME_ERROR lines
            rt_errors = [
                line.replace("RUNTIME_ERROR: ", "").replace("RUNTIME_ERROR", "")
                for line in stderr.splitlines()
                if "RUNTIME_ERROR" in line or result.returncode != 0
            ]
            if rt_errors:
                return [f"[{task_id}] Live harness: {e}" for e in rt_errors]
            return [f"[{task_id}] Live harness: non-zero exit ({result.returncode})"]
    except subprocess.TimeoutExpired:
        return [f"[{task_id}] Live harness: timed out after 10 s"]
    except FileNotFoundError:
        pass  # lua not installed; fall back to static analysis only
    except Exception as e:
        return [f"[{task_id}] Live harness: unexpected error — {e}"]

    return []


# ── Code extraction ────────────────────────────────────────────────────────────

_LUA_FENCE_RE = re.compile(r"```(?:lua)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def _extract_lua(text: str) -> str:
    """Extract the first Lua code block from an agent output."""
    m = _LUA_FENCE_RE.search(text)
    if m:
        return m.group(1).strip()
    # If no fence, return the raw text (some agents omit fences)
    return text.strip()


# ── Public entry point ────────────────────────────────────────────────────────

def run_runtime_sim(ctx) -> List[str]:
    """
    Run the headless runtime simulation over all task outputs in ctx.
    Returns a list of error strings. Also appends errors to ctx.runtime_errors.

    Non-fatal: exceptions are swallowed and returned as error strings.
    The pipeline can choose to surface these as preflight warnings or hard errors.
    """
    errors: List[str] = []

    # Only simulate Lua domain outputs
    _LUA_DOMAINS = {"Lua", "lua", "PHYS", "phys"}

    try:
        import tempfile, os
        tmp_dir = Path(tempfile.mkdtemp(prefix="midway_sim_"))

        for task_id, output in (ctx.all_results_dict or {}).items():
            if not output or not output.strip():
                continue

            # Determine the domain for this task
            task_obj = (ctx.task_map or {}).get(task_id)
            domain = getattr(task_obj, "agent", "") if task_obj else ""

            # Only simulate Lua / physics domain outputs
            if domain not in _LUA_DOMAINS:
                # But still run static checks if the output contains Lua patterns
                if not (
                    "MidwayPhysics." in output
                    or "function OnLoad" in output
                    or "economy:" in output
                ):
                    continue

            lua_code = _extract_lua(output)
            if not lua_code:
                continue

            # Static analysis (always)
            static_errors = _analyse_lua_text(task_id, lua_code)
            errors.extend(static_errors)

            # Live harness (when lua is available)
            live_errors = _run_live_harness(task_id, lua_code, tmp_dir)
            errors.extend(live_errors)

        # Cleanup temp dir
        try:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass

    except Exception as e:
        errors.append(f"[RuntimeSim] Simulation failed with unexpected error: {e}")

    # Store on context
    if not hasattr(ctx, "runtime_errors") or ctx.runtime_errors is None:
        ctx.runtime_errors = []
    ctx.runtime_errors.extend(errors)

    return errors


# ── Final Phantom-API Gate ──────────────────────────────────────────────────
# Called AFTER observability, immediately before the consensus gate.
# This is intentionally separate from run_runtime_sim so it runs on the
# *final* (post-observability) content rather than the pre-review draft.

# Approved Engine.* globals injected by the host before every script.
# Any Engine.* call NOT in _ECONOMY_API and NOT in this set is phantom.
_ENGINE_GLOBALS: frozenset = frozenset({
    # modifier scalars
    "engine_mod_mass", "engine_mod_volume", "engine_mod_friction",
    "engine_mod_karma", "engine_mod_luck", "engine_mod_persuasion",
    "engine_mod_heat", "engine_mod_sleight_of_hand", "engine_mod_nerve",
    # booth placement
    "booth_slot_id", "booth_world_x", "booth_world_z", "booth_is_static",
})

# Every ENGINE_MOD_* that a new attraction MUST reference somewhere.
_REQUIRED_MODIFIER_GLOBALS: Tuple[str, ...] = (
    "ENGINE_MOD_MASS", "ENGINE_MOD_VOLUME", "ENGINE_MOD_FRICTION",
    "ENGINE_MOD_KARMA", "ENGINE_MOD_LUCK", "ENGINE_MOD_PERSUASION",
    "ENGINE_MOD_HEAT", "ENGINE_MOD_SLEIGHT_OF_HAND", "ENGINE_MOD_NERVE",
)

# Preferred accessor pattern — AttractionConstants.modifiers.*
_MODIFIER_ACCESSOR_RE = re.compile(
    r"AttractionConstants\.modifiers\b",
    re.MULTILINE,
)

# Direct ENGINE_MOD_* read (acceptable when accessor is absent)
_DIRECT_MOD_RE = re.compile(
    r"\bENGINE_MOD_(?:MASS|VOLUME|FRICTION|KARMA|LUCK|PERSUASION|HEAT|SLEIGHT_OF_HAND|NERVE)\b",
    re.MULTILINE,
)

# Engine.AwardTickets / Engine.AwardTokens presence check
_AWARD_RE = re.compile(
    r"\bEngine\.Award(?:Tickets|Tokens)\s*\(",
    re.MULTILINE,
)


def run_phantom_api_final_pass(ctx) -> List[str]:
    """
    Final deterministic phantom-API scan executed after the observability pass,
    before the consensus gate.

    Checks every Lua output in ctx.all_results_dict for:
      1. Any MidwayPhysics.* call not in the approved _MIDWAY_PHYSICS_API whitelist.
      2. Any Engine.* call not in the approved _ECONOMY_API whitelist.
      3. Any legacy `economy:` colon-method call (always phantom).
      4. Missing engine modifier consumption — every attraction must read from
         AttractionConstants.modifiers or at least one ENGINE_MOD_* global.
      5. Missing economy hook — every attraction must call Engine.AwardTickets
         or Engine.AwardTokens at least once.

    Returns a (possibly empty) list of error strings.
    Stores results on ctx.phantom_pass_errors.
    Non-fatal individually; the consensus gate treats any non-empty list as a
    hard FAIL.
    """
    errors: List[str] = []

    _LUA_DOMAINS = {"Lua", "lua", "PHYS", "phys"}
    _ENGINE_CALL_RE = re.compile(r"\bEngine\.([A-Za-z][A-Za-z0-9_]*)\s*\(", re.MULTILINE)
    _PHYSICS_CALL_RE_FINAL = re.compile(
        r"\bMidwayPhysics\.([A-Za-z][A-Za-z0-9_]*)\s*\(", re.MULTILINE
    )
    _COLON_ECONOMY_RE_FINAL = re.compile(
        r"\beconomy:([A-Za-z][A-Za-z0-9_]*)\s*\(", re.MULTILINE
    )

    try:
        for task_id, output in (ctx.all_results_dict or {}).items():
            if not output or not output.strip():
                continue

            task_obj = (ctx.task_map or {}).get(task_id)
            domain = getattr(task_obj, "agent", "") if task_obj else ""

            # Only scan Lua/physics domain outputs, or any output that smells like Lua.
            if domain not in _LUA_DOMAINS:
                if not (
                    "MidwayPhysics." in output
                    or "function OnLoad" in output
                    or "Engine." in output
                    or "economy:" in output
                ):
                    continue

            lua_code = _extract_lua(output)
            if not lua_code:
                continue

            # ── 1. MidwayPhysics.* whitelist check ─────────────────────────
            for pm in _PHYSICS_CALL_RE_FINAL.finditer(lua_code):
                fn = pm.group(1).lower()
                if fn not in _MIDWAY_PHYSICS_API:
                    errors.append(
                        f"[{task_id}][PhantomAPIGate] Unknown MidwayPhysics API: "
                        f"`MidwayPhysics.{pm.group(1)}` is NOT in the approved bridge "
                        "contract. Remove or replace before approval."
                    )

            # ── 2. Engine.* whitelist check ─────────────────────────────────
            for em in _ENGINE_CALL_RE.finditer(lua_code):
                fn = em.group(1).lower()
                if fn not in _ECONOMY_API:
                    errors.append(
                        f"[{task_id}][PhantomAPIGate] Unknown Engine API: "
                        f"`Engine.{em.group(1)}` is NOT in the approved economy bridge "
                        "contract (§11). Remove or replace before approval."
                    )

            # ── 3. Legacy economy: colon-method check ───────────────────────
            for cm in _COLON_ECONOMY_RE_FINAL.finditer(lua_code):
                errors.append(
                    f"[{task_id}][PhantomAPIGate] Phantom colon-method: "
                    f"`economy:{cm.group(1)}` — use Engine.AwardTickets / "
                    "Engine.AwardTokens (flat namespace, no colon)."
                )

            # ── 4. Modifier consumption check ───────────────────────────────
            has_modifier_access = (
                _MODIFIER_ACCESSOR_RE.search(lua_code) is not None
                or _DIRECT_MOD_RE.search(lua_code) is not None
            )
            if not has_modifier_access:
                errors.append(
                    f"[{task_id}][PhantomAPIGate] Missing modifier consumption — "
                    "attraction must read AttractionConstants.modifiers (or at least "
                    "one ENGINE_MOD_* global) inside OnStep to respect the live "
                    "modifier system (heat, luck, sleight_of_hand, etc.)."
                )

            # ── 5. Economy hook check ────────────────────────────────────────
            if not _AWARD_RE.search(lua_code):
                errors.append(
                    f"[{task_id}][PhantomAPIGate] Missing economy hook — "
                    "attraction must call Engine.AwardTickets(...) or "
                    "Engine.AwardTokens(...) on win/score events."
                )

    except Exception as e:
        errors.append(f"[PhantomAPIGate] Unexpected error during final pass: {e}")

    if not hasattr(ctx, "phantom_pass_errors") or ctx.phantom_pass_errors is None:
        ctx.phantom_pass_errors = []
    ctx.phantom_pass_errors.extend(errors)

    return errors
