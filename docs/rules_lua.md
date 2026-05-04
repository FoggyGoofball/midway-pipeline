# Lua Scripting Rules — Midway to Nowhere
## Review Checklist for `/arch_lua`

### Language & Environment
- [ ] **Lua 5.4 via sol2.** No LuaJIT-specific features. No external Lua modules — only the MidwayPhysics and Engine APIs.
- [ ] **Self-contained constants.** Every attraction defines its own LOCAL config tables (CABINET, PUSHER, COIN, FIELD, BOARD, PEG, BALL, WALL, BRICK, SCORE, etc.). Never import shared tuning files.
- [ ] **No engine bridge pollution.** Game tuning values (pusher stroke, shelf height, spawn counts, peg spacing) belong in the attraction's local constants. Never add them to `AttractionConstants` or the C++ bridge.

### Script Lifecycle (Required)
- [ ] **`OnLoadStatic()`** — Must call `SpawnSharedBooth()` first, then spawn permanent cabinet geometry. Called once per slot.
- [ ] **`OnLoad()`** — Spawn kinematic/dynamic bodies. Register `OnStep` callback via `MidwayPhysics.OnStep(fn)`. Called each time player approaches.
- [ ] **`OnUnload()`** — Optional cleanup. Engine destroys dynamic bodies automatically; use this for Lua-side state teardown.
- [ ] **`OnStep(dt)`** — Read `AttractionConstants.modifiers` every frame. Never cache modifier values at load time.

### Coordinate System
- [ ] **Local booth space:** Origin = floor center of booth. +X = right, +Y = up, +Z = back. Booth opening faces -Z.
- [ ] **1 engine unit = 1 meter.** All positions, sizes, velocities in meters.
- [ ] **Engine auto-transforms** local coords to world space. Never apply booth offset manually.

### Canonical Booth Dimensions
- [ ] **Booth shell:** `width_x = 9.0`, `height_y = 9.0`, `depth_z = 15.0`.
- [ ] **Booth center offset:** `BOOTH_SIDE_X = ±10.5` (world X), `BOOTH_SPACING = 15.0` (world Z gap).
- [ ] **Corridor:** `CORRIDOR_HALF_WIDTH = 6.0` (player walkable half-width).

### Engine Globals (Injected, Read-Only)
- [ ] **Slot identity:** `BOOTH_SLOT_ID` (int), `BOOTH_WORLD_X` (float), `BOOTH_WORLD_Z` (float), `BOOTH_IS_STATIC` (bool).
- [ ] **Modifier globals:** `ENGINE_MOD_MASS`, `ENGINE_MOD_VOLUME`, `ENGINE_MOD_FRICTION`, `ENGINE_MOD_KARMA`, `ENGINE_MOD_LUCK`, `ENGINE_MOD_PERSUASION`, `ENGINE_MOD_HEAT`, `ENGINE_MOD_SLEIGHT_OF_HAND`, `ENGINE_MOD_NERVE`.
- [ ] **Preferred access:** Read modifiers from `AttractionConstants.modifiers.*` (snake_case keys) for consistency.

### MidwayPhysics API Usage
- [ ] **Spawn functions:** `SpawnStaticBox`, `SpawnStaticSphere`, `SpawnStaticCapsule`, `SpawnStaticCylinder`, `SpawnStaticMesh` — for permanent geometry.
- [ ] **Kinematic spawn:** `SpawnKinematicBox/Sphere/Capsule/Cylinder` — for moving platforms, pushers.
- [ ] **Dynamic spawn:** `SpawnDynamicBox/Sphere/Capsule/Cylinder/Mesh` — for physics-simulated objects (coins, balls, bricks).
- [ ] **Sensors:** `SpawnSensorBox/Sphere` — trigger zones with `IsSensorTriggered(handle)`.
- [ ] **Movement:** `MoveKinematic(handle, lx, ly, lz, dt)` — for kinematic bodies each step.
- [ ] **Impulses:** `ApplyImpulse`, `ApplyAngularImpulse`, `SetLinearVelocity`, `AddLinearVelocity`.
- [ ] **Per-body overrides:** `SetFriction`, `SetRestitution`, `SetGravityFactor`, `SetMass`, `SetLinearDamping`, `SetAngularDamping`.

### Object Pools (Generic API)
- [ ] **Create pool:** `MidwayPhysics.CreatePool("unique_name_N", hotN, coldN, paramsTable)` — params: shape, w, h, d, radius, halfH, mass, friction, restitution, linearDamping, angularDamping.
- [ ] **Pool name convention:** `"{attraction}_{type}_{slotID}"` — e.g., `"plinko_balls_3"`, `"coin_cascade_coins_7"`.
- [ ] **Acquire/Return:** `PoolAcquire(name, lx, ly, lz)` → handle (0 = exhausted). `PoolReturn(name, handle)`.
- [ ] **Culling:** `PoolCullBelow(name, yThreshold)` → table of culled handles. Call in OnStep to recycle fallen objects.
- [ ] **Query:** `PoolFree(name)` → int (available hot). `PoolTotal(name)` → int (hot + cold).

### Economy API (Engine.*)
- [ ] **Award tickets:** `Engine.AwardTickets(amount, "LABEL")` — adds tickets, queues win banner.
- [ ] **Award tokens:** `Engine.AwardTokens(amount, "LABEL")` — adds/subtracts soul tokens.
- [ ] **Read balances:** `Engine.GetTickets()`, `Engine.GetTokens()`, `Engine.GetStreak()`.
- [ ] **Win banners:** Display 3.5s, fade last 1s. Multiple banners stack vertically.

### Modifier Integration (GDD §4.1–4.3)
- [ ] **Read each frame:** `local MOD = AttractionConstants.modifiers` inside `OnStep`. Apply as multipliers/offsets.
- [ ] **Mass:** Affects kinetic transfer, gravity pull, collision force.
- [ ] **Volume:** Affects hitbox size, aerodynamics, overlap detection.
- [ ] **Friction:** Affects sliding, bounce restitution, surface grip.
- [ ] **Karma:** Affects RNG tilt, physics hostility (Demonic = harder, Angelic = easier).
- [ ] **Luck:** Affects procedural generation, drop tables, peg type probability.
- [ ] **Sleight of Hand:** Affects TILT mechanic — physical abuse of machines, impulse strength.
- [ ] **Nerve:** Affects timing windows, stability under pressure. Penalty scales as tokens drop.

### Attraction-Specific Patterns
- [ ] **Coin Cascade:** Jolt 3D. Coins are cylinders with thickness. Gutter-Scaling Engine retracts side-walls at higher levels.
- [ ] **Plinko:** Peg taxonomy: Bouncy (high restitution), Sticky (high friction), Fragile (deactivate on contact), Explosive (radial impulse). Never destroy pegs — deactivate them.
- [ ] **Crumbling Façade:** 2D side-view brick wall. Bricks are dynamic boxes. Chain reactions via structural integrity checks.
- [ ] **Claw of Condemnation:** 3D gantry crane. Claw Tension, Pendulum Sway, Capsule Density calculations.
- [ ] **Slingshot Array:** Ballistic chain reactions. Band elasticity, projectile mass, wind vectors.
- [ ] **Ring Toss:** Fixed-velocity aerodynamics. Pitch/Yaw only. Ring inner diameter, bottleneck radius, lift.

### Debug & Logging
- [ ] **Bridge logging:** All Lua↔C++ calls logged to `bridge_log.txt` with microsecond timestamps. `MoveKinematic` excluded from logging.
- [ ] **Print statements:** Use `print()` for debug output. It appears in `midway.log`.
- [ ] **F1 panel:** Live modifier sliders. Changes sync to Lua globals immediately.
