# PROMPT FOR QWEN CODER 2.5 7B — BUILD "THE CRUMBLING FACADE"

## CRITICAL CONTEXT (read this first)

You are writing a Lua script for an existing C++ game engine called "Midway — Purgatorial Carnival". The engine uses **Jolt Physics 3D** (the real Jolt, not a toy). You must create a new attraction called **"The Crumbling Facade"** — a 2D side-view wall that the player throws balls at, causing bricks to crumble and fall with realistic 3D physics.

**Your output must be a SINGLE Lua file** that goes in `attractions/crumbling_facade/crumbling_facade.lua`. You must also add config constants to `attractions/_shared/attraction_constants.lua`.

## ENGINE API (the Lua bridge you must use)

All physics calls go through `MidwayPhysics.*`. Here is the COMPLETE API:

### Static spawners (permanent geometry, created in OnLoadStatic):
- `MidwayPhysics.SpawnStaticBox(lx, ly, lz, w, h, d)` → handle (int)
- `MidwayPhysics.SpawnStaticSphere(lx, ly, lz, radius)` → handle
- `MidwayPhysics.SpawnStaticCapsule(lx, ly, lz, halfH, radius)` → handle
- `MidwayPhysics.SpawnStaticCylinder(lx, ly, lz, halfH, radius)` → handle
- `MidwayPhysics.SpawnStaticMesh(lx, ly, lz, yaw, path, sx, sy, sz)` → handle (for GLB models)

### Dynamic spawners (physics-simulated objects, created in OnLoad):
- `MidwayPhysics.SpawnDynamicBox(lx, ly, lz, w, h, d, [mass])` → handle
- `MidwayPhysics.SpawnDynamicSphere(lx, ly, lz, radius, [mass])` → handle
- `MidwayPhysics.SpawnDynamicCapsule(lx, ly, lz, halfH, radius, [mass])` → handle
- `MidwayPhysics.SpawnDynamicCylinder(lx, ly, lz, halfH, radius)` → handle

### Kinematic spawners (programmatically moved objects):
- `MidwayPhysics.SpawnKinematicBox(lx, ly, lz, w, h, d)` → handle
- `MidwayPhysics.SpawnKinematicSphere(lx, ly, lz, radius)` → handle
- `MidwayPhysics.SpawnKinematicCapsule(lx, ly, lz, halfH, radius)` → handle
- `MidwayPhysics.SpawnKinematicCylinder(lx, ly, lz, halfH, radius)` → handle

### Sensor spawners (trigger zones, no collision):
- `MidwayPhysics.SpawnSensorBox(lx, ly, lz, w, h, d)` → handle
- `MidwayPhysics.SpawnSensorSphere(lx, ly, lz, radius)` → handle

### Kinematic movement:
- `MidwayPhysics.MoveKinematic(handle, lx, ly, lz, dt)` — move a kinematic body each frame

### Body queries:
- `MidwayPhysics.GetPosition(handle)` → lx, ly, lz (3 return values, or nil if invalid)
- `MidwayPhysics.GetVelocity(handle)` → vx, vy, vz
- `MidwayPhysics.IsActive(handle)` → bool
- `MidwayPhysics.IsSensorTriggered(sensorHandle)` → bool

### Impulse / velocity control:
- `MidwayPhysics.SetLinearVelocity(handle, vx, vy, vz)`
- `MidwayPhysics.AddLinearVelocity(handle, vx, vy, vz)`
- `MidwayPhysics.ApplyImpulse(handle, ix, iy, iz)`
- `MidwayPhysics.ApplyAngularImpulse(handle, ix, iy, iz)`

### Property overrides:
- `MidwayPhysics.SetFriction(handle, f)`
- `MidwayPhysics.SetRestitution(handle, r)`
- `MidwayPhysics.SetGravityFactor(handle, f)`
- `MidwayPhysics.SetMass(handle, kg)`
- `MidwayPhysics.SetLinearDamping(handle, d)`
- `MidwayPhysics.SetAngularDamping(handle, d)`

### Generic named object pool (for reusable objects like balls):
- `MidwayPhysics.CreatePool(name, hotSize, coldSize, paramsTable)` → bool
  - paramsTable fields: shape ("box"|"sphere"|"capsule"|"cylinder"), w, h, d, radius, halfH, mass, friction, restitution, linearDamping, angularDamping
- `MidwayPhysics.PoolAcquire(name, lx, ly, lz)` → handle (0 if pool exhausted)
- `MidwayPhysics.PoolReturn(name, handle)`
- `MidwayPhysics.PoolCullBelow(name, yThreshold)` → table of returned handles
- `MidwayPhysics.PoolFree(name)` → int (available count)
- `MidwayPhysics.PoolTotal(name)` → int

### Lifetime:
- `MidwayPhysics.DestroyBody(handle)` — destroys a body immediately

### Step callback:
- `MidwayPhysics.OnStep(function(dt) ... end)` — register per-frame update
- `MidwayPhysics.OnStep(nil)` — clear the callback

### Economy bridge:
- `Engine.AwardTickets(amount, label)` — award tickets to the player

### Global variables injected by engine:
- `BOOTH_SLOT_ID` — integer slot ID (or -1 in standalone)
- `BOOTH_WORLD_X`, `BOOTH_WORLD_Z` — booth world position
- `BOOTH_IS_STATIC` — bool
- `AttractionConstants` — table with all config (see below)
- `AttractionConstants.modifiers` — live modifier values:
  - `.mass`, `.volume`, `.friction`, `.karma`, `.luck`, `.persuasion`, `.heat`, `.sleight_of_hand`, `.nerve`
- `SpawnSharedBooth()` — call in OnLoadStatic to create the default booth mesh

### Coordinate system:
- **LOCAL SPACE** throughout. Origin = centre of machine at floor level.
- +X = right, +Y = up, +Z = back (into the booth, away from player)
- The engine auto-transforms local coords to world coords using the booth transform
- Player stands at -Z looking into +Z

## THE ATTRACTION: "The Crumbling Facade"

### Concept:
A 2D side-view brick wall (like a Breakout/Wall-Breaking game) rendered in the ZY plane. The wall is made of individual brick-shaped dynamic boxes held in place by friction/gravity. The player throws balls at the wall. Bricks that are hit crumble and fall with realistic Jolt physics, potentially causing a chain reaction that brings down adjacent bricks.

### Visual layout (looking along +X into the booth):
- The wall sits at X=0 (the board plane), spanning Z (width) and Y (height)
- Bricks are arranged in a rectangular grid pattern, staggered like real brickwork
- Each brick is a dynamic box with realistic mass, friction, and restitution
- Balls are spheres thrown from the player's side (-Z direction)

### Required gameplay elements:

1. **Brick Wall** (the facade):
   - Grid of bricks: ~6 rows × 8 columns (adjustable)
   - Bricks are staggered (offset every other row by half a brick width)
   - Each brick is a dynamic box with mass ~0.5-2.0 kg
   - Bricks have high friction (0.8) so they grip each other
   - Bricks have low restitution (0.05) so they don't bounce much
   - Bottom row sits on a static "ledge" or floor
   - Side walls (static boxes) contain the bricks on left and right edges
   - A static "back wall" behind the bricks prevents them from falling backward

2. **Ball throwing mechanism**:
   - Player presses a button to throw a ball
   - Ball spawns at a random Z position along the wall width
   - Ball flies toward the wall (+Z direction) with configurable speed
   - Ball has slight random Y variation so it hits different heights
   - Ball pool uses the generic named pool system
   - Ball drop rate controlled by a timer (like coin_cascade)

3. **Scoring**:
   - Each brick that falls below a Y threshold = 1 point
   - Chain reaction bonus: if multiple bricks fall within a short time window, multiplier increases
   - Luck modifier boosts score
   - Heat modifier increases ball throw rate

4. **Modifier integration**:
   - `mass` → affects ball mass and brick mass
   - `friction` → affects ball friction and brick friction
   - `heat` → increases ball throw rate, makes bricks slightly more fragile
   - `luck` → score multiplier bonus
   - `sleight_of_hand` → narrows the Z spread of ball throws (more precise aiming)

5. **Reset mechanism**:
   - When all bricks are destroyed OR player walks away, the wall resets
   - OnLoad recreates the brick wall fresh
   - OnUnload cleans up

### Implementation requirements:

1. **OnLoadStatic()**: Create permanent geometry:
   - Call `SpawnSharedBooth()`
   - Static back wall (thin slab at X=0.5, behind bricks)
   - Static floor ledge at bottom of wall
   - Static side walls (left and right containment)

2. **OnLoad()**: Create dynamic gameplay:
   - Create ball pool
   - Build the brick wall grid (all dynamic boxes)
   - Register OnStep callback that:
     - Throws balls on timer
     - Checks for bricks that fell below threshold → score them + return to pool
     - Applies modifier values to live bricks and balls
     - Culls balls that miss the wall and fall away
     - Periodically prints diagnostics

3. **OnUnload()**: Cleanup:
   - Print final score stats
   - The engine handles body destruction automatically

### Brick wall construction details:
- Bricks are boxes: width (Z) = 0.8, height (Y) = 0.3, depth (X) = 0.4
- Horizontal gap between bricks: 0.05
- Vertical gap between rows: 0.05
- Stagger: even rows start at Z=0, odd rows offset by half brick width
- Wall centered at Z=0
- Bottom of wall at Y=0.5 (sitting on a ledge)
- Each brick is spawned with `MidwayPhysics.SpawnDynamicBox()`
- Store brick handles in a table for tracking

### Ball details:
- Sphere, radius 0.15
- Mass 0.05 kg
- Friction 0.3, Restitution 0.4
- Thrown at ~15 m/s toward +Z
- Pool: 10 hot, 40 cold

### Scoring:
- Each brick that falls below Y=-1.0 is "destroyed" (returned to pool)
- Base score per brick: 10 tickets
- Chain reaction: if 2+ bricks fall within 0.5 seconds, multiply score by chain count
- Luck modifier: multiply score by (1.0 + luck * 0.5)

## CONSTANTS TO ADD TO attraction_constants.lua

Add a new section under the plinko section:

```lua
    crumbling_facade = {
        wall = {
            num_rows    = 6,
            num_cols    = 8,
            brick_w     = 0.8,    -- Z width of each brick
            brick_h     = 0.3,    -- Y height of each brick
            brick_d     = 0.4,    -- X depth of each brick
            gap_h       = 0.05,   -- horizontal gap between bricks
            gap_v       = 0.05,   -- vertical gap between rows
            base_y      = 0.5,    -- Y of bottom row bottom edge
            board_x     = 0.0,    -- X plane of the wall
            backwall_x  = 0.5,    -- X position of back wall
            backwall_d  = 0.2,    -- X depth of back wall
            sidewall_w  = 0.2,    -- Z width of side containment walls
        },

        brick = {
            mass        = 1.0,
            friction    = 0.8,
            restitution = 0.05,
            linear_damping  = 0.1,
            angular_damping = 0.1,
            cull_y      = -1.0,   -- bricks below this Y are scored and removed
        },

        ball = {
            radius         = 0.15,
            mass           = 0.05,
            friction       = 0.3,
            restitution    = 0.4,
            linear_damping  = 0.05,
            angular_damping = 0.05,
            speed          = 15.0,   -- m/s toward +Z
            throw_interval = 1.0,    -- seconds between throws
            spawn_y_min    = 1.0,    -- min Y for ball spawn
            spawn_y_max    = 5.0,    -- max Y for ball spawn
            spawn_z        = -2.0,   -- Z where ball spawns (in front of wall)
            cull_y         = -2.0,   -- balls below this Y are returned to pool
            pool_hot       = 10,
            pool_cold      = 40,
        },

        scoring = {
            base_per_brick = 10,
            chain_window   = 0.5,    -- seconds for chain reaction counting
        },
    },
```

## OUTPUT FORMAT

Output ONLY the complete `crumbling_facade.lua` file content. Follow the exact patterns from `plinko.lua` and `coin_cascade.lua` — use the same structure, same variable naming conventions, same print diagnostic format.

The file must have these functions:
1. `function OnLoadStatic()` — permanent geometry
2. `function OnLoad()` — dynamic setup + OnStep callback
3. `function OnUnload()` — cleanup

Use `local SLOT_ID = BOOTH_SLOT_ID or -1` at the top.
Use `local CFG = AttractionConstants.crumbling_facade` for config access.
Use `local MOD = AttractionConstants.modifiers` for live modifier values.

## CRITICAL RULES

1. **DO NOT** use any C++ or engine features not listed in the API above
2. **DO NOT** try to create custom shapes or meshes — use only boxes and spheres
3. **DO NOT** use any Lua libraries not already loaded (base, math, string, table, io, os)
4. **ALL coordinates are LOCAL SPACE** — the engine handles world transform
5. **Use the generic named pool** for balls (like coin_cascade does)
6. **Bricks are NOT pooled** — they are individual SpawnDynamicBox calls. Track them in a table.
7. **When a brick falls below cull_y**, destroy it with `MidwayPhysics.DestroyBody(handle)` and remove from tracking table
8. **Chain reaction scoring**: track the timestamp of each brick destruction. If another brick falls within `chain_window` seconds, increment chain counter. Score = base * chain_multiplier.
9. **Print diagnostics** every 1 second showing: bricks remaining, balls in flight, score, chain multiplier
10. **The wall is 2D** (in the ZY plane) but uses full 3D Jolt physics — bricks have depth (X dimension) and can tumble in 3D

## EXAMPLE PATTERN (from plinko.lua)

```lua
-- crumbling_facade.lua  -  The Crumbling Facade
-- LOCAL SPACE throughout. Engine injects booth transform before every call.
-- Origin = centre of machine at floor level. +X right, +Y up, +Z back.
--
-- A 2D side-view brick wall with 3D Jolt physics.
-- Player throws balls at the wall; bricks crumble and fall.
-- Chain reactions bring down adjacent bricks for bonus scoring.

local SLOT_ID = BOOTH_SLOT_ID or -1
local CFG = AttractionConstants.crumbling_facade
local WALL = CFG.wall
local BRICK = CFG.brick
local BALL = CFG.ball
local SCORE = CFG.scoring

print(string.format("[crumbling_facade] OnLoadStatic slot#%d  config loaded", SLOT_ID))

-- ... rest of implementation ...
```

## FINAL CHECKLIST

Before outputting, verify:
- [ ] OnLoadStatic creates permanent geometry (shared booth + back wall + floor + side walls)
- [ ] OnLoad builds the brick grid with staggered rows
- [ ] OnLoad creates a ball pool
- [ ] OnLoad registers an OnStep callback
- [ ] OnStep throws balls on timer
- [ ] OnStep checks for fallen bricks → scores them → destroys them
- [ ] OnStep culls lost balls
- [ ] OnStep applies modifier values
- [ ] OnStep prints diagnostics every 1 second
- [ ] OnUnload prints final stats
- [ ] Chain reaction scoring is implemented
- [ ] All coordinates are in local space
- [ ] Uses only the provided MidwayPhysics API
- [ ] Follows the same patterns as plinko.lua and coin_cascade.lua
