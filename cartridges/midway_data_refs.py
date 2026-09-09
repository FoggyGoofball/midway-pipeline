"""
midway_data_refs.py -- API-reference, bridge-contract, and attraction-spec
builders extracted from midway_data.py.

Exported:
    build_api_references(prefix) -> Dict[str, Dict[str, Any]]
    build_bridge_contract() -> Dict[str, Any]
    build_attraction_specs() -> Dict[str, Any]

Import direction: same as midway_data.py  no pipeline or kernel imports allowed.
"""
from __future__ import annotations

from typing import Any, Dict


def build_api_references(prefix: str = "docs") -> Dict[str, Dict[str, Any]]:
    """Return structured index of API documentation files.

    ``prefix`` is accepted for backwards-compatibility but all paths now resolve
    to the canonical sister-repo location (repos/midway/docs).  Callers that
    previously passed a relative prefix will still receive a valid dict; the
    ``path`` values are absolute so agents can open them without guessing.
    """
    from pathlib import Path as _Path
    _docs = _Path(__file__).resolve().parents[2] / "midway" / "docs"

    def _p(filename: str) -> str:
        return str(_docs / filename)

    return {
        # -- Scraped external API docs ----------------------------------------
        "jolt": {
            "label": "Jolt Physics API",
            "path": _p("jolt_api.md"),
            "anchors": {
                "PhysicsSystem": "#physicssystem",
                "BodyInterface": "#bodyinterface",
                "BodyCreationSettings": "#bodycreationsettings",
                "ObjectLayerFilter": "#objectlayerfilter",
                "BroadPhaseLayer": "#broadphaselayerinterface",
                "ConstraintSettings": "#constraintsettings",
                "Body": "#body",
                "BodyID": "#bodyid",
                "EMotionType": "#emotiontype",
            },
            "search_terms": [
                "physics system", "body interface", "body creation",
                "shape settings", "constraints", "body id",
                "broadphase", "collision layers", "motion type",
            ],
        },
        "sol2": {
            "label": "sol2 Lua Binding API",
            "path": _p("sol2_api.md"),
            "anchors": {
                "sol::state": "#sol-state",
                "sol::state_view": "#sol-state-view",
                "sol::function": "#sol-function",
                "sol::table": "#sol-table",
                "sol::object": "#sol-object",
                "new_usertype": "#new-usertype",
                "set_function": "#set-function",
                "pointer_safety": "#pointer-safety",
            },
            "search_terms": [
                "lua state", "binding", "function registration",
                "usertype", "metatable", "ownership",
            ],
        },
        "cpp17": {
            "label": "C++17 Standard Library",
            "path": _p("cpp17_api.md"),
            "anchors": {
                "StructuredBindings": "#structured-bindings",
                "if_constexpr": "#if-constexpr",
                "FoldExpressions": "#fold-expressions",
                "InlineVariables": "#inline-variables",
                "filesystem": "#std-filesystem",
                "optional": "#std-optional",
                "variant": "#std-variant",
                "string_view": "#std-string-view",
                "shared_mutex": "#std-shared-mutex",
                "clamp": "#std-clamp",
            },
            "search_terms": [
                "filesystem", "optional", "variant", "string_view",
                "structured bindings", "constexpr", "fold expression",
            ],
        },
        "opengl_sdl": {
            "label": "OpenGL 3.3 + SDL2 API",
            "path": _p("opengl_sdl_api.md"),
            "anchors": {
                "SDL_CreateWindow": "#sdl-createwindow",
                "SDL_GL_CreateContext": "#sdl-gl-createcontext",
                "SDL_PollEvent": "#sdl-pollevent",
                "glCreateShader": "#gl-createshader",
                "glCreateProgram": "#gl-createprogram",
                "glGenVertexArrays": "#gl-genvertexarrays",
                "glBufferData": "#gl-bufferdata",
            },
            "search_terms": [
                "window creation", "opengl context", "shader compilation",
                "vertex array", "uniform location", "event loop",
            ],
        },
        "box2d": {
            "label": "Box2D API (deprecated  reference only)",
            "path": _p("box2d_api.md"),
            "search_terms": ["box2d", "b2World", "b2Body"],
        },
        # -- Project-authored reference docs ----------------------------------
        "engine_bridge": {
            "label": "Engine↔Lua Bridge Contract",
            "path": _p("engine_lua_bridge_contract.md"),
            "search_terms": [
                "bridge contract", "midwayphysics api", "spawn",
                "modifier bridge", "economy api", "callbacks",
            ],
        },
        "api_index": {
            "label": "Master API Index",
            "path": _p("api_index.md"),
            "search_terms": ["api index", "symbol index", "function list"],
        },
        "internal_api_ledger": {
            "label": "Internal API Ledger",
            "path": _p("internal_api_ledger.md"),
            "search_terms": ["ledger", "internal api", "registered functions"],
        },
        "attraction_specs": {
            "label": "Attraction Specifications",
            "path": _p("attraction_specs.md"),
            "search_terms": [
                "attraction", "booth", "skeeball", "coin cascade",
                "mini golf", "carnival", "slot machine",
            ],
        },
        "pipeline_anchor_index": {
            "label": "Pipeline Anchor Index",
            "path": _p("pipeline_anchor_index.md"),
            "search_terms": ["anchor", "pipeline index", "agent anchor"],
        },
        # -- Rules docs --------------------------------------------------------
        "rules_cpp": {
            "label": "C++ Coding Rules",
            "path": _p("rules_cpp.md"),
            "search_terms": ["cpp rules", "c++ standard", "coding mandates"],
        },
        "rules_lua": {
            "label": "Lua Scripting Rules",
            "path": _p("rules_lua.md"),
            "search_terms": ["lua rules", "lua scripting", "sol2 rules"],
        },
        "rules_phys": {
            "label": "Physics Rules",
            "path": _p("rules_phys.md"),
            "search_terms": ["physics rules", "jolt constraints", "collision rules"],
        },
        "rules_review": {
            "label": "Code Review Rules",
            "path": _p("rules_review.md"),
            "search_terms": ["review rules", "code review", "reviewer mandates"],
        },
        "rules_shader": {
            "label": "Shader Rules",
            "path": _p("rules_shader.md"),
            "search_terms": ["shader rules", "glsl", "vertex shader", "fragment shader"],
        },
        "rules_net": {
            "label": "Networking Rules",
            "path": _p("rules_net.md"),
            "search_terms": ["networking rules", "net code", "multiplayer"],
        },
    }


# -- Bridge contract -----------------------------------------------------------

def build_bridge_contract() -> Dict[str, Any]:
    """Return the consolidated Engine↔Lua bridge contract."""
    return {
        "globals_injected": {
            "BOOTH_WORLD_X": "float  world-space X center of this slot",
            "BOOTH_WORLD_Z": "float  world-space Z center of this slot",
            "BOOTH_SLOT_ID": "int  unique slot identifier",
            "BOOTH_IS_STATIC": "bool  true during static load, false during dynamic load",
        },
        "modifier_globals": {
            "ENGINE_MOD_MASS":           {"gdd": "§4.1 Core Physical", "default": 1.0},
            "ENGINE_MOD_VOLUME":         {"gdd": "§4.1 Core Physical", "default": 1.0},
            "ENGINE_MOD_FRICTION":       {"gdd": "§4.1 Core Physical", "default": 1.0},
            "ENGINE_MOD_KARMA":          {"gdd": "§4.2 Meta-Navigational", "default": 0.0, "range": "-1..1"},
            "ENGINE_MOD_LUCK":           {"gdd": "§4.2 Meta-Navigational", "default": 0.0},
            "ENGINE_MOD_PERSUASION":     {"gdd": "§4.2 Meta-Navigational", "default": 0.0},
            "ENGINE_MOD_HEAT":           {"gdd": "§4.2 Meta-Navigational", "default": 0.0},
            "ENGINE_MOD_SLEIGHT_OF_HAND": {"gdd": "§4.3 Tactile", "default": 0.0},
            "ENGINE_MOD_NERVE":          {"gdd": "§4.3 Tactile", "default": 0.0},
        },
        "load_order": [
            "1. attractions/_shared/attraction_constants.lua  canonical dimensions, tuning, live modifiers",
            "2. attractions/booth_shared.lua  SpawnSharedBooth() helper and SharedBooth utilities",
            "3. Attraction script  OnLoadStatic() for permanent geometry, OnLoad() for gameplay bodies",
        ],
        "script_lifecycle": {
            "OnLoadStatic": "Call SpawnSharedBooth() first, then spawn cabinet-specific static geometry.",
            "OnLoad": "Spawn kinematic/dynamic bodies. Register OnStep callback via MidwayPhysics.OnStep(fn).",
            "OnUnload": "Optional cleanup. Engine destroys dynamic bodies automatically.",
            "OnStep_dt": "Read AttractionConstants.modifiers each frame. Never cache at load time.",
        },
        "input_api": {
            "MidwayInput.IsActionDown(\"fire\") → bool": "Space held  launch ball",
            "MidwayInput.IsActionDown(\"aim_left\") → bool": "A or Left-Arrow held  aim left",
            "MidwayInput.IsActionDown(\"aim_right\") → bool": "D or Right-Arrow held  aim right",
            "MidwayInput.IsActionDown(\"power_up\") → bool": "W or Up-Arrow held  increase power",
            "MidwayInput.IsActionDown(\"power_down\") → bool": "S or Down-Arrow held  decrease power",
            "MidwayInput.IsKeyDown(name) → bool": "Raw SDL key check. name is single letter \"A\" or SDL name \"Space\", \"Return\", \"Left\", etc.",
        },
        "midwayphysics_spawn_api": {
            "SpawnStaticBox(lx, ly, lz, w, h, d) → handle": "Permanent static box",
            "SpawnStaticSphere(lx, ly, lz, radius) → handle": "Permanent static sphere",
            "SpawnStaticCapsule(lx, ly, lz, halfHeight, radius) → handle": "Permanent static capsule",
            "SpawnStaticCylinder(lx, ly, lz, halfHeight, radius) → handle": "Permanent static cylinder",
            "SpawnStaticMesh(lx, ly, lz, yaw, path) → handle": "Static mesh from asset file",
            "SpawnKinematicBox(lx, ly, lz, w, h, d) → handle": "Moving platform / pusher  box",
            "SpawnKinematicSphere(lx, ly, lz, radius) → handle": "Moving platform  sphere",
            "SpawnKinematicCapsule(lx, ly, lz, halfHeight, radius) → handle": "Moving platform  capsule",
            "SpawnKinematicCylinder(lx, ly, lz, halfHeight, radius) → handle": "Moving platform  cylinder",
            "SpawnDynamicBox(lx, ly, lz, w, h, d [, mass]) → handle": "Physics-simulated box",
            "SpawnDynamicSphere(lx, ly, lz, radius [, mass]) → handle": "Physics-simulated sphere",
            "SpawnDynamicCapsule(lx, ly, lz, halfHeight, radius [, mass]) → handle": "Physics-simulated capsule",
            "SpawnDynamicCylinder(lx, ly, lz, halfHeight, radius [, mass]) → handle": "Physics-simulated cylinder",
            "SpawnDynamicMesh(lx, ly, lz, yaw, mass, path) → handle": "Dynamic mesh from asset file",
            "SpawnSensorBox(lx, ly, lz, w, h, d) → handle": "Trigger zone  box",
            "SpawnSensorSphere(lx, ly, lz, radius) → handle": "Trigger zone  sphere",
            "MoveKinematic(handle, lx, ly, lz, dt)": "Sets kinematic body target position each step.",
            "ApplyImpulse(handle, ix, iy, iz)": "Instantaneous force in local space.",
            "ApplyAngularImpulse(handle, ix, iy, iz)": "Instantaneous torque.",
            "SetLinearVelocity(handle, vx, vy, vz)": "Override current velocity.",
            "AddLinearVelocity(handle, vx, vy, vz)": "Additive velocity change.",
            "SetFriction(handle, v)": "Per-body friction override.",
            "SetRestitution(handle, v)": "Per-body restitution override.",
            "SetGravityFactor(handle, v)": "Per-body gravity factor override.",
            "SetMass(handle, kg)": "Per-body mass override.",
            "SetLinearDamping(handle, v)": "Per-body linear damping override.",
            "SetAngularDamping(handle, v)": "Per-body angular damping override.",
            "GetPosition(handle) → lx, ly, lz": "Local-space position relative to booth origin.",
            "GetVelocity(handle) → vx, vy, vz": "Local-space velocity.",
            "IsActive(handle) → bool": "False for destroyed or parked bodies.",
            "IsSensorTriggered(handle) → bool": "Overlap state from previous physics step.",
            "DestroyBody(handle)": "Remove from handle map AND physics system.",
            "OnStep(fn)": "Register per-frame callback: MidwayPhysics.OnStep(function(dt) ... end). Call inside OnLoad().",
        },
        "object_pools": {
            "CreatePool(name, hotN, coldN, paramsTable)": "Two-tier pool. params: shape, w, h, d, radius, halfH, mass, friction, restitution, damping.",
            "PoolAcquire(name, lx, ly, lz) → handle": "0 = pool exhausted.",
            "PoolReturn(name, handle)": "Park body at Y=-9999.",
            "PoolCullBelow(name, yThreshold)": "Return hot bodies below threshold to cold store.",
            "PoolFree(name) → int": "Available hot slots.",
            "PoolTotal(name) → int": "Hot + cold slots.",
            "Naming convention": "{attraction}_{type}_{slotID} (e.g. plinko_balls_3)",
        },
        "economy_api": {
            "Engine.AwardTickets(n, label)": "Adds n tickets, queues win banner.",
            "Engine.AwardTokens(n, label)": "Adds/subtracts n soul tokens, queues banner.",
            "Engine.GetTickets() → int": "Current ticket balance.",
            "Engine.GetTokens() → int": "Current soul token balance.",
            "Engine.GetStreak() → int": "Current streak counter.",
        },
        "win_banners": {
            "display_duration": "3.5 seconds",
            "fade_duration": "1 second (last second of display)",
            "stacking": "Multiple simultaneous banners stack vertically",
            "position": "Centered on screen at ~35% from top  gold header, white subtext (+N TICKETS)",
        },
    }


# -- Attraction specs ----------------------------------------------------------

def build_attraction_specs() -> Dict[str, Any]:
    """Return consolidated attraction geometry and placement constants."""
    return {
        "unit_scale": "1 engine unit = 1 meter",
        "coordinate_system": {
            "origin": "center of booth at floor level (local space)",
            "axes": {"+X": "right", "+Y": "up", "+Z": "back", "opening": "-Z"},
        },
        "canonical_booth": {
            "width_x": 9.0,
            "height_y": 9.0,
            "depth_z": 15.0,
            "booth_side_x": 10.5,
            "booth_spacing": 15.0,
            "corridor_half_width": 6.0,
            "button_zone_local": {
                "x": 0.0,
                "y": 1.7,
                "z_expr": "-HD + 0.5 where HD = depth_z * 0.5",
            },
        },
        "cabinet_envelope": {
            "max_width": 7.0,
            "max_height": 6.5,
            "max_depth": 11.0,
            "clearance_side": 0.5,
            "clearance_rear": 0.5,
        },
        "placement": {
            "station_spacing_z": 15.0,
            "x_offset": "±10.5 from midway centerline",
            "booths_should_touch_visually_but_not_intersect": True,
        },
        "gdd_modifier_classes": {
            "core_physicals_4_1": ["Mass", "Volume", "Friction"],
            "meta_navigational_4_2": ["Karma", "Luck", "Persuasion", "Heat"],
            "tactile_4_3": ["Sleight of Hand", "Nerve"],
        },
    }
