#!/usr/bin/env python3
"""
cartridge_lora_generator.py
===========================
Cartridge-aware LoRA dataset generator.

Generates project-specific API training data from any cartridge class
that exposes get_bridge_contract(), get_review_prompt_extra(), or
equivalent structured API metadata.

The output JSONL uses identical ChatML format as the paging dataset
(lora_generator/paging_lora_dataset.jsonl) so it can be fed into
the same lora_fine_tune.py training pipeline.

Key difference from paging dataset: train_on_inputs=true
Because the cartridge LoRA teaches factual API knowledge (which functions exist
and their correct signatures), we want the model to learn from the full context,
not just the assistant response.

Output
------
  lora_generator/midway_lora_dataset.jsonl
  (or --output specified path)

Scenarios Generated
-------------------
  1. SPAWN_SCENARIO    (30%): Write OnLoad/OnLoadStatic for a given attraction
  2. FIX_PHANTOM       (20%): Correct phantom API calls to valid equivalents
  3. FULL_ATTRACTION   (20%): Write complete attraction with skeleton template
  4. POOL_SCENARIO     (15%): Object pool lifecycle (CreatePool, Acquire, Return, Cull)
  5. ECONOMY_SCENARIO  (15%): Economy API usage (AwardTickets, GetStreak, etc.)
"""

import json
import random
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
DEFAULT_OUTPUT = SCRIPT_DIR / "midway_lora_dataset.jsonl"

# Ensure project root is on sys.path for cartridge imports
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

# ---------------------------------------------------------------------------
# Cartridge discovery helper (same pattern as cartridge_loader.py)
# ---------------------------------------------------------------------------
def _load_cartridge_class(cartridge_name: str):
    """Dynamically import a cartridge class by name (e.g. 'MidwayAgentCartridge')."""
    import importlib
    for module_file in (PROJECT_DIR / "cartridges").glob("*.py"):
        if module_file.name.startswith("_"):
            continue
        module_name = f"cartridges.{module_file.stem}"
        try:
            mod = importlib.import_module(module_name)
        except Exception as e:
            continue
        for attr_name in dir(mod):
            if attr_name == cartridge_name:
                return getattr(mod, attr_name)
    raise ImportError(f"Cartridge class '{cartridge_name}' not found in cartridges/")


# ---------------------------------------------------------------------------
# Synthetic attraction names for scenario variety
# ---------------------------------------------------------------------------
ATTRACTION_NAMES = [
    "Coin Cascade", "Plinko", "Skeeball", "Whack-a-Barker",
    "Fishing Pond", "Ring Toss", "Wheel of Misfortune",
    "Balloon Dart", "Milk Jug Takedown", "Test Your Strength",
    "Lucky Duck Pond", "Mini Golf Hole 3", "Basketball Toss",
    "Penny Pusher", "Claw Machine", "Racing Pods",
]

ATTRACTION_KEYS = [
    "coin_cascade", "plinko", "skeeball", "whackabarker",
    "fishing_pond", "ring_toss", "wheel_of_misfortune",
    "balloon_dart", "milk_jug", "test_strength",
    "lucky_duck", "minigolf_03", "basketball_toss",
    "penny_pusher", "claw_machine", "racing_pods",
]

# ---------------------------------------------------------------------------
# Phantom API correction pairs (parsed from _MIDWAY_PROHIBITIONS P3a)
# ---------------------------------------------------------------------------
PHANTOM_CORRECTIONS: List[Tuple[str, str]] = [
    ("SpawnDynamicBall", "SpawnDynamicSphere(lx, ly, lz, radius)"),
    ("SpawnDynamicBody", "SpawnDynamicBox / SpawnDynamicSphere / SpawnDynamicCapsule"),
    ("SpawnStaticBody (generic)", "SpawnStaticBox / SpawnStaticSphere / SpawnStaticCapsule"),
    ("RemoveBody", "DestroyBody(handle)"),
    ("DestroyEntity", "DestroyBody(handle)"),
    ("ReleaseHandle", "DestroyBody(handle)"),
    ("CheckCollision", "IsSensorTriggered(handle)"),
    ("GetLinearVelocity", "GetVelocity(handle)"),
    ("SetLinearVelocity(handle, vec)", "SetLinearVelocity(handle, vx, vy, vz)"),
    ("MoveKinematic(handle, vec)", "MoveKinematic(handle, lx, ly, lz, dt)"),
    ("SetMass", "[does not exist] Pass mass during SpawnDynamic* spawn instead"),
    ("sol.on_load", "[does not exist] Use function OnLoad() — called by engine automatically"),
    ("sol.on_step", "[does not exist] Register via MidwayPhysics.OnStep(fn) inside OnLoad()"),
    ("sol.on_unload", "[does not exist] Use function OnUnload()"),
    ("sol.set_function", "[does not exist]"),
    ("function OnStep(dt) ... end (bare global)", "Register via MidwayPhysics.OnStep(function(dt) ... end) inside OnLoad()"),
    ("UnregisterOnStep / MidwayPhysics.UnregisterOnStep", "[does not exist] OnStep auto-released on slot unmount"),
    ("SetPosition / MidwayPhysics.SetPosition", "Use MoveKinematic for kinematics, PoolReturn+PoolAcquire for dynamics"),
    ("MidwayPhysics.Teleport", "[does not exist] Use PoolReturn/PoolAcquire to recycle a body"),
    ("MidwayPhysics.OnStep(nil)", "[CRASH] Do NOT call OnStep(nil) — callbacks auto-release on unmount"),
    ("table.clear(t)", "Use `for k in pairs(t) do t[k]=nil end` or simply `t = {}`"),
    ("b2World", "Jolt — Box2D is fully deprecated"),
    ("b2Vec2", "Jolt — Box2D is fully deprecated"),
    ("b2Body", "Jolt — Box2D is fully deprecated"),
    ("jpBody", "Jolt — Box2D is fully deprecated"),
]

# Box2D phantom phrases
BOX2D_PHANTOMS = ["b2World", "b2Vec2", "b2Body", "jpBody", "Box2D", "b2Fixture"]

# ===========================================================================
# System prompt builder
# ===========================================================================
def _build_api_block(contract: dict) -> str:
    """Format the bridge contract into a system-prompt API reference block."""
    lines = [
        "---",
        "ACTIVE BRIDGE CONTRACT — MidwayPhysics / Engine APIs",
        "All valid functions are listed below. Do NOT use any function not in this list.",
        "",
    ]

    spawn = contract.get("midwayphysics_spawn_api", {})
    if spawn:
        lines.append("### Spawn & Physics API")
        for sig, desc in spawn.items():
            lines.append(f"  {sig}")
        lines.append("")

    pools = contract.get("object_pools", {})
    if pools:
        lines.append("### Object Pools")
        for sig, desc in pools.items():
            lines.append(f"  {sig} — {desc}")
        lines.append("")

    economy = contract.get("economy_api", {})
    if economy:
        lines.append("### Economy API")
        for sig, desc in economy.items():
            lines.append(f"  {sig}")
        lines.append("")

    lifecycle = contract.get("script_lifecycle", {})
    if lifecycle:
        lines.append("### Script Lifecycle")
        for hook, desc in lifecycle.items():
            lines.append(f"  {hook}: {desc}")
        lines.append("")

    globals_inj = contract.get("globals_injected", {})
    if globals_inj:
        lines.append("### Injected Globals")
        for name, desc in globals_inj.items():
            lines.append(f"  {name} — {desc}")
        lines.append("")

    lines.append("---")
    return "\n".join(lines)


# ===========================================================================
# Scenario Generators
# ===========================================================================

# ── Helper: random float within range ───────────────────────────────────
def _rand_pos() -> str:
    """Random position string: 'lx, ly, lz'"""
    return f"{random.uniform(-3.0, 3.0):.1f}, {random.uniform(0.0, 2.0):.1f}, {random.uniform(-5.0, 0.0):.1f}"

def _rand_dims() -> str:
    """Random WxHxD for a box."""
    return f"{random.uniform(0.2, 2.0):.1f}, {random.uniform(0.2, 2.0):.1f}, {random.uniform(0.2, 2.0):.1f}"


# ── Scenario 1: Spawn OnLoadStatic / OnLoad ────────────────────────────
def _gen_spawn_scenario(seed: int) -> dict:
    rng = random.Random(seed)
    name = rng.choice(ATTRACTION_NAMES)
    key = ATTRACTION_KEYS[ATTRACTION_NAMES.index(name)]

    # Decide static vs dynamic load
    is_static = rng.choice([True, False])
    hook = "OnLoadStatic" if is_static else "OnLoad"
    explanation = (
        "Spawn permanent cabinet geometry" if is_static
        else "Spawn dynamic bodies + register OnStep callback"
    )

    # Pick 2-4 random spawn shapes
    shapes = rng.sample([
        ("Box", _rand_dims()),
        ("Sphere", f"{random.uniform(0.2, 0.8):.1f}"),
        ("Capsule", f"{random.uniform(0.2, 0.6):.1f}, {random.uniform(0.5, 1.5):.1f}"),
        ("Cylinder", f"{random.uniform(0.2, 0.6):.1f}, {random.uniform(0.5, 1.5):.1f}"),
    ], rng.randint(2, 4))

    spawn_lines = []
    for idx, (shape, dims) in enumerate(shapes):
        prefix = "SpawnStatic" if is_static else "SpawnDynamic"
        pos = _rand_pos()
        handle = f"h{key.split('_')[0]}_{idx}"
        spawn_lines.append(f"    local {handle} = MidwayPhysics.{prefix}{shape}({pos}, {dims})")

    user_msg = (
        f"Write the {hook} function for the '{name}' attraction.\n"
        f"{explanation}. The booth is at (BOOTH_WORLD_X, 0, BOOTH_WORLD_Z).\n"
        f"Use local-space positions relative to the booth origin."
    )

    # Build assistant response
    resp_lines = [f"function {hook}()"]
    if is_static:
        resp_lines.append("    SpawnSharedBooth()")
    resp_lines.extend(spawn_lines)
    if not is_static:
        resp_lines.append("")
        resp_lines.append("    MidwayPhysics.OnStep(function(dt)")
        resp_lines.append(f"        local mods = AttractionConstants.modifiers")
        resp_lines.append("        -- Read modifiers each frame, never cache at load time")
        resp_lines.append("    end)")
    resp_lines.append("end")
    resp_lines.append("")

    return {
        "messages": [
            {"role": "system", "content": "You are the Lua attraction scripter for 'Midway to Nowhere'. Write ONLY pure Lua as loaded by the engine."},
            {"role": "user", "content": user_msg},
            {"role": "assistant", "content": "\n".join(resp_lines)},
        ]
    }


# ── Scenario 2: Fix Phantom API calls ──────────────────────────────────
def _gen_phantom_scenario(seed: int) -> dict:
    rng = random.Random(seed + 1000)
    phantom, correction = rng.choice(PHANTOM_CORRECTIONS)
    name = rng.choice(ATTRACTION_NAMES)
    key = ATTRACTION_KEYS[ATTRACTION_NAMES.index(name)]

    # Construct bad code snippet
    if "SpawnDynamicBall" in phantom:
        bad_code = f"local hBall = MidwayPhysics.SpawnDynamicBall({_rand_pos()}, 0.5)"
    elif "RemoveBody" in phantom:
        bad_code = f"RemoveBody(hSomeHandle)  -- phantom"
    elif "SetPosition" in phantom:
        bad_code = f"MidwayPhysics.SetPosition(hBall, {_rand_pos()})"
    elif "OnStep(nil)" in phantom:
        bad_code = "MidwayPhysics.OnStep(nil)  -- trying to unregister"
    elif "table.clear" in phantom:
        bad_code = "table.clear(some_table)  -- phantom Lua function"
    elif "b2World" in phantom or "b2Body" in phantom or "Box2D" in phantom or "b2Vec2" in phantom or "jpBody" in phantom:
        bad_code = f"-- Box2D reference:\nlocal world = b2World(0, -9.81)"
    elif "sol.on_load" in phantom or "sol.on_step" in phantom:
        bad_code = f"sol.on_step(function() ... end)  -- phantom sol API"
    elif "SetLinearVelocity(handle, vec)" in phantom:
        bad_code = f"MidwayPhysics.SetLinearVelocity(hBall, {{x=1, y=0, z=0}})  -- wrong signature"
    elif "MidwayPhysics.Teleport" in phantom:
        bad_code = f"MidwayPhysics.Teleport(hBall, {_rand_pos()})  -- phantom API"
    elif "UnregisterOnStep" in phantom:
        bad_code = "MidwayPhysics.UnregisterOnStep()  -- does not exist"
    elif "SpawnDynamicBody" in phantom:
        bad_code = "local hBody = MidwayPhysics.SpawnDynamicBody()  -- too generic"
    elif "SpawnStaticBody" in phantom:
        bad_code = "local hWall = MidwayPhysics.SpawnStaticBody()  -- too generic"
    elif "DestroyEntity" in phantom:
        bad_code = "DestroyEntity(hEnemy)  -- phantom"
    elif "ReleaseHandle" in phantom:
        bad_code = "ReleaseHandle(hTemporary)  -- phantom"
    elif "CheckCollision" in phantom:
        bad_code = "if CheckCollision(hBall, hWall) then  -- phantom"
    elif "GetLinearVelocity" in phantom:
        bad_code = "local vx, vy, vz = MidwayPhysics.GetLinearVelocity(hBall)  -- phantom"
    elif "MoveKinematic(handle, vec)" in phantom:
        bad_code = "MidwayPhysics.MoveKinematic(hPlatform, {x=1, y=0, z=0})  -- wrong signature"
    elif "SetMass" in phantom:
        bad_code = "MidwayPhysics.SetMass(hBody, 5.0)  -- phantom"
    elif "sol.set_function" in phantom:
        bad_code = "sol.set_function('myFunc', function() end)  -- phantom sol API"
    elif "function OnStep" in phantom:
        bad_code = "function OnStep(dt)\n    -- raw global, engine never calls this\nend"
    elif "table.clear" in phantom:
        bad_code = "table.clear(scoreTable)  -- phantom in Lua 5.4"
    else:
        bad_code = f"-- Phantom API usage:\nlocal result = {phantom.split(' ')[0]}(...)  -- does not exist"

    user_msg = (
        f"Fix the following phantom API usage in the '{name}' Lua attraction.\n"
        f"Replace the phantom call with the correct MidwayPhysics API.\n"
        f"```lua\n{bad_code}\n```"
    )

    resp_lines = [
        f"-- FIXED: {phantom} → use {correction}",
        f"-- Correction:",
    ]
    # Generate a corrected version
    if "SpawnDynamicBall" in phantom:
        resp_lines.append(f"local hBall = MidwayPhysics.SpawnDynamicSphere({_rand_pos()}, 0.5)")
    elif "RemoveBody" in phantom or "DestroyEntity" in phantom or "ReleaseHandle" in phantom:
        resp_lines.append("MidwayPhysics.DestroyBody(hSomeHandle)")
    elif "SpawnDynamicBody" in phantom:
        pos = _rand_pos()
        dims = _rand_dims()
        resp_lines.append(f"local hBody = MidwayPhysics.SpawnDynamicBox({pos}, {dims})")
    elif "SpawnStaticBody" in phantom:
        pos = _rand_pos()
        dims = _rand_dims()
        resp_lines.append(f"local hWall = MidwayPhysics.SpawnStaticBox({pos}, {dims})")
    elif "SetPosition" in phantom:
        resp_lines.append(f"-- Use MoveKinematic for kinematic bodies:")
        resp_lines.append(f"MidwayPhysics.MoveKinematic(hBall, {_rand_pos()}, dt)")
    elif "OnStep(nil)" in phantom:
        resp_lines.append("-- OnStep callbacks auto-release on slot unmount")
        resp_lines.append("-- Remove the OnStep(nil) call entirely")
    elif "table.clear" in phantom:
        resp_lines.append("for k in pairs(some_table) do some_table[k] = nil end")
    elif "b2World" in phantom or "b2Body" in phantom or "Box2D" in phantom or "b2Vec2" in phantom or "jpBody" in phantom:
        resp_lines.append("-- Box2D is fully deprecated. Use Jolt physics via MidwayPhysics APIs.")
        pos = _rand_pos()
        dims = _rand_dims()
        resp_lines.append(f"local hBody = MidwayPhysics.SpawnStaticBox({pos}, {dims})")
    elif "sol.on_load" in phantom or "sol.on_step" in phantom:
        resp_lines.append("-- sol lifecycle hooks do NOT exist. Use Lua functions called by engine:")
        resp_lines.append("function OnLoad()")
        resp_lines.append("    MidwayPhysics.OnStep(function(dt)")
        resp_lines.append("        -- per-frame logic here")
        resp_lines.append("    end)")
        resp_lines.append("end")
    elif "SetLinearVelocity(handle, vec)" in phantom:
        resp_lines.append(f"MidwayPhysics.SetLinearVelocity(hBall, 1.0, 0.0, 0.0)  -- correct: vx, vy, vz")
    elif "MidwayPhysics.Teleport" in phantom:
        resp_lines.append(f"-- PoolReturn + PoolAcquire for repositioning:")
        resp_lines.append(f"MidwayPhysics.PoolReturn('{key}_balls', hBall)")
        resp_lines.append(f"local newBall = MidwayPhysics.PoolAcquire('{key}_balls', {_rand_pos()})")
    elif "UnregisterOnStep" in phantom:
        resp_lines.append("-- OnStep auto-releases on slot unmount. No need to unregister.")
    elif "CheckCollision" in phantom:
        resp_lines.append("if MidwayPhysics.IsSensorTriggered(hBall) then  -- use IsSensorTriggered")
    elif "GetLinearVelocity" in phantom:
        resp_lines.append("local vx, vy, vz = MidwayPhysics.GetVelocity(hBall)  -- correct name")
    elif "MoveKinematic(handle, vec)" in phantom:
        resp_lines.append(f"MidwayPhysics.MoveKinematic(hPlatform, 1.0, 0.0, 0.0, dt)  -- correct: lx, ly, lz, dt")
    elif "SetMass" in phantom:
        pos = _rand_pos()
        resp_lines.append(f"-- Pass mass during spawn:")
        resp_lines.append(f"local hBody = MidwayPhysics.SpawnDynamicBox({pos}, {_rand_dims()}, 5.0)  -- mass=5")
    elif "sol.set_function" in phantom:
        resp_lines.append("-- sol.set_function does NOT exist. Use sol2 bindings in C++ or Lua callbacks.")
    elif "function OnStep" in phantom:
        resp_lines.append("-- Register OnStep callback inside OnLoad():")
        resp_lines.append("function OnLoad()")
        resp_lines.append("    MidwayPhysics.OnStep(function(dt)")
        resp_lines.append("        -- per-frame logic")
        resp_lines.append("    end)")
        resp_lines.append("end")
    else:
        resp_lines.append(f"-- Use the approved API instead")

    return {
        "messages": [
            {"role": "system", "content": "You are a Lua code reviewer for 'Midway to Nowhere'. Correct phantom API calls to valid MidwayPhysics APIs."},
            {"role": "user", "content": user_msg},
            {"role": "assistant", "content": "\n".join(resp_lines)},
        ]
    }


# ── Scenario 3: Full Attraction (skeleton template) ────────────────────
def _gen_full_attraction_scenario(seed: int) -> dict:
    rng = random.Random(seed + 2000)
    name = rng.choice(ATTRACTION_NAMES)
    key = ATTRACTION_KEYS[ATTRACTION_NAMES.index(name)]

    # Pick a game mechanic
    mechanics = [
        "Player presses a button to launch a ball up a ramp. Ball rolls back down and scores based on landing zone.",
        "Player pulls back a launcher to propel a projectile at targets. Each target awards tickets on hit.",
        "Player drops a coin from the top. Coin cascades down pegs and lands in a scoring slot at the bottom.",
        "Player uses a mallet to strike a target. Force determines how high the puck rises on the tower.",
        "Player throws rings onto floating pegs. Each ring scores points based on peg difficulty.",
    ]
    mechanic = rng.choice(mechanics)

    user_msg = (
        f"Write the complete Lua attraction script for '{name}'.\n"
        f"Mechanic: {mechanic}\n\n"
        f"Requirements:\n"
        f"1. OnLoadStatic() — SpawnSharedBooth() + spawn permanent cabinet geometry\n"
        f"2. OnLoad() — Spawn dynamic/kinematic bodies, register OnStep callback\n"
        f"3. Read AttractionConstants.modifiers each frame (never cache at load)\n"
        f"4. Use local-space positions relative to booth origin\n"
        f"5. Award tickets on successful completion via Engine.AwardTickets()\n"
    )

    resp_lines = [
        f"-- {name} — Full Attraction Script",
        f"-- generated by pipeline cartridge",
        f"",
        f"function OnLoadStatic()",
        f"    SpawnSharedBooth()",
        f"",
        f"    -- Cabinet: back wall",
        f"    local hBackWall = MidwayPhysics.SpawnStaticBox(0.0, 4.5, -7.5, 9.0, 9.0, 0.2)",
        f"    -- Cabinet: floor",
        f"    local hFloor = MidwayPhysics.SpawnStaticBox(0.0, 0.0, -3.0, 9.0, 0.2, 8.0)",
        f"end",
        f"",
        f"function OnLoad()",
        f"    -- Spawn a ball",
        f"    local hBall = MidwayPhysics.SpawnDynamicSphere(0.0, 1.0, 2.0, 0.3)",
        f"    MidwayPhysics.SetRestitution(hBall, 0.3)",
        f"    MidwayPhysics.SetFriction(hBall, 0.5)",
        f"",
        f"    -- Register per-frame callback",
        f"    MidwayPhysics.OnStep(function(dt)",
        f"        local mods = AttractionConstants.modifiers",
        f"        local massMult = mods.mass or 1.0",
        f"        local frictionMult = mods.friction or 1.0",
        f"        -- Per-frame logic here (scoring, ball tracking, etc.)",
        f"    end)",
        f"end",
        f"",
        f"function OnUnload()",
        f"    -- Bodies auto-destroyed; cleanup custom state if needed",
        f"end",
    ]

    return {
        "messages": [
            {"role": "system", "content": "You are the Lua attraction scripter for 'Midway to Nowhere'. Write complete attraction scripts using ONLY approved MidwayPhysics APIs."},
            {"role": "user", "content": user_msg},
            {"role": "assistant", "content": "\n".join(resp_lines)},
        ]
    }


# ── Scenario 4: Object Pool lifecycle ──────────────────────────────────
def _gen_pool_scenario(seed: int) -> dict:
    rng = random.Random(seed + 3000)
    name = rng.choice(ATTRACTION_NAMES)
    key = ATTRACTION_KEYS[ATTRACTION_NAMES.index(name)]
    pool_name = f"{key}_balls_1"

    user_msg = (
        f"Write an OnLoad() function for '{name}' that uses the two-tier object pool system.\n"
        f"Pool name: '{pool_name}'\n"
        f"Pool should have 20 hot slots and 80 cold slots, using sphere shape with radius 0.3\n"
        f"Show: CreatePool, PoolAcquire, PoolReturn, and PoolCullBelow usage."
    )

    resp_lines = [
        f"function OnLoad()",
        f"    -- Create two-tier pool: 20 hot slots, 80 cold, sphere radius 0.3",
        f"    MidwayPhysics.CreatePool('{pool_name}', 20, 80, {{",
        f"        shape = 'sphere',",
        f"        radius = 0.3,",
        f"        mass = 0.5,",
        f"        friction = 0.3,",
        f"        restitution = 0.2,",
        f"    }})",
        f"",
        f"    -- Pre-acquire some balls for active use",
        f"    local activeBalls = {{}}",
        f"    for i = 1, 5 do",
        f"        local h = MidwayPhysics.PoolAcquire('{pool_name}', {_rand_pos()})",
        f"        if h > 0 then",
        f"            table.insert(activeBalls, h)",
        f"        end",
        f"    end",
        f"",
        f"    -- Register per-frame callback",
        f"    MidwayPhysics.OnStep(function(dt)",
        f"        local mods = AttractionConstants.modifiers",
        f"        -- Cull balls below Y=-2.0",
        f"        MidwayPhysics.PoolCullBelow('{pool_name}', -2.0)",
        f"    end)",
        f"end",
    ]

    return {
        "messages": [
            {"role": "system", "content": "You are the Lua attraction scripter. Use MidwayPhysics.CreatePool/PoolAcquire/PoolReturn/PoolCullBelow for object recycling."},
            {"role": "user", "content": user_msg},
            {"role": "assistant", "content": "\n".join(resp_lines)},
        ]
    }


# ── Scenario 5: Economy API ────────────────────────────────────────────
def _gen_economy_scenario(seed: int) -> dict:
    rng = random.Random(seed + 4000)
    name = rng.choice(ATTRACTION_NAMES)
    key = ATTRACTION_KEYS[ATTRACTION_NAMES.index(name)]

    # Pick a reward condition
    conditions = [
        "When the ball enters the goal zone, award 5 tickets and increment streak.",
        "When all targets are hit, award 10 tickets and display a win banner.",
        "Each successful ring toss awards 3 tickets. On 3 consecutive successes, award 5 bonus tickets.",
        "When the puck hits the bell at the top, award 20 tickets.",
        "On game over, award tickets based on score: 1 ticket per 100 points.",
    ]
    condition = rng.choice(conditions)

    user_msg = (
        f"Write the OnStep callback for '{name}' that uses economy API calls.\n"
        f"Condition: {condition}\n"
        f"Read AttractionConstants.modifiers each frame. Award tickets via Engine.AwardTickets()."
    )

    resp_lines = [
        f"-- Economy callback for {name}",
        f"local score = 0",
        f"local targetsHit = 0",
        f"",
        f"MidwayPhysics.OnStep(function(dt)",
        f"    local mods = AttractionConstants.modifiers",
        f"    local luckMult = mods.luck or 1.0",
        f"    local karmaMult = mods.karma or 0.0",
        f"",
        f"    -- Check sensor for ball entry",
        f"    if MidwayPhysics.IsSensorTriggered(hGoalSensor) then",
        f"        local tickets = math.floor(5 * luckMult)",
        f"        Engine.AwardTickets(tickets, '{name} goal scored')",
        f"        local streak = Engine.GetStreak()",
        f"    end",
        f"    ",
        f"    -- Apply karma modifier to payout",
        f"    if karmaMult > 0 then",
        f"        local bonus = math.floor(tickets * karmaMult)",
        f"        Engine.AwardTickets(bonus, 'Karma bonus')",
        f"    end",
        f"end)",
    ]

    return {
        "messages": [
            {"role": "system", "content": "You are the Lua attraction scripter. Use Engine.AwardTickets, Engine.GetStreak, Engine.GetTickets, Engine.GetTokens for economy interactions."},
            {"role": "user", "content": user_msg},
            {"role": "assistant", "content": "\n".join(resp_lines)},
        ]
    }


# ===========================================================================
# Main generator
# ===========================================================================
def generate_dataset(
    output_path: str = None,
    num_samples: int = 2000,
    cartridge_name: str = "MidwayAgentCartridge",
    seed: int = 42,
) -> None:
    """
    Generate cartridge API training dataset.

    Args:
        output_path: Path to write JSONL output. Default: midway_lora_dataset.jsonl
        num_samples: Number of samples to generate.
        cartridge_name: Cartridge class name to source API metadata from.
        seed: RNG seed for reproducibility.
    """
    output = Path(output_path) if output_path else DEFAULT_OUTPUT
    rng = random.Random(seed)

    # ── Load cartridge for structured data ───────────────────────────────
    try:
        Cartridge = _load_cartridge_class(cartridge_name)
    except ImportError as e:
        print(f"[Error] {e}")
        print("Falling back to hardcoded MidwayPhysics API data...")
        Cartridge = None

    if Cartridge is not None:
        try:
            contract = Cartridge.get_bridge_contract()
            print(f"[Load] Loaded bridge contract from {cartridge_name}")
        except Exception as e:
            print(f"[Warn] get_bridge_contract() failed: {e}")
            contract = {}
    else:
        contract = {}

    # ── Scenario distribution ────────────────────────────────────────────
    # weight -> (generator_func, weight)
    scenarios = [
        (_gen_spawn_scenario, 30),
        (_gen_phantom_scenario, 20),
        (_gen_full_attraction_scenario, 20),
        (_gen_pool_scenario, 15),
        (_gen_economy_scenario, 15),
    ]
    weights = [w for _, w in scenarios]
    generators = [g for g, _ in scenarios]

    # ── Generate ─────────────────────────────────────────────────────────
    print(f"\nGenerating {num_samples} training samples...")
    print(f"  Output: {output}")
    print(f"  Seed:   {seed}")
    print(f"  Distribution:")
    total_weight = sum(weights)
    for g, w in scenarios:
        pct = w / total_weight * 100
        print(f"    {g.__name__.replace('_gen_', '').replace('_scenario', '')}: {w/total_weight*num_samples:.0f} ({pct:.0f}%)")

    samples: List[dict] = []
    for i in range(num_samples):
        generator = rng.choices(generators, weights=weights, k=1)[0]
        sample = generator(i)
        samples.append(sample)

    # ── Write ────────────────────────────────────────────────────────────
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        for sample in samples:
            f.write(json.dumps(sample, ensure_ascii=False) + "\n")

    print(f"\nDone! Generated {len(samples)} samples → {output}")
    print(f"Estimated dataset size: {output.stat().st_size / 1024:.0f} KB")


# ===========================================================================
# CLI entry point
# ===========================================================================
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate cartridge LoRA training dataset")
    parser.add_argument(
        "--output", "-o",
        default=str(DEFAULT_OUTPUT),
        help=f"Output JSONL path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--num-samples", "-n",
        type=int,
        default=2000,
        help="Number of samples to generate (default: 2000)",
    )
    parser.add_argument(
        "--cartridge", "-c",
        default="MidwayAgentCartridge",
        help="Cartridge class name (default: MidwayAgentCartridge)",
    )
    parser.add_argument(
        "--seed", "-s",
        type=int,
        default=42,
        help="RNG seed (default: 42)",
    )
    args = parser.parse_args()

    generate_dataset(
        output_path=args.output,
        num_samples=args.num_samples,
        cartridge_name=args.cartridge,
        seed=args.seed,
    )
