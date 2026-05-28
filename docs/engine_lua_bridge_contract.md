# Engine ↔ Lua Bridge Contract

This document records the **current** Midway Host ↔ Lua bridge as implemented in `src/MidwayPhysics.cpp` and `src/Engine.cpp`, and notes what is required of Lua attraction scripts.

---

## 1. GDD requirements
Per GDD §2.2, the Midway Host is responsible for:
- loading the attraction Lua file when the player approaches
- allowing the script to load models
- allowing the script to set up physics boundaries
- injecting active Modifiers

Per GDD §4.1–4.3, the engine modifier classes are:
- Mass
- Volume
- Friction
- Karma
- Luck
- Persuasion
- Heat
- Sleight of Hand
- Nerve

---

## 2. Load order — what the engine does before every script runs

For every attraction slot load (both static and dynamic), the engine injects globals then
runs the following files in order before the attraction script:

1. `attractions/_shared/attraction_constants.lua` — canonical booth dimensions, prototype
   game tuning constants, and live engine modifier values
2. `attractions/booth_shared.lua` — `SpawnSharedBooth()` helper and `SharedBooth` utilities

The attraction script is then loaded and its entry-point function called:
- **Static load:** `OnLoadStatic()` — spawn permanent cabinet geometry
- **Dynamic load:** `OnLoad()` — register `OnStep` callback and spawn gameplay bodies

---

## 3. Globals injected by the engine before script load

### Booth/slot identity (set per slot, always present)
| Global | Type | Description |
|---|---|---|
| `BOOTH_WORLD_X` | float | World-space X center of this slot |
| `BOOTH_WORLD_Z` | float | World-space Z center of this slot |
| `BOOTH_SLOT_ID` | int | Unique slot identifier |
| `BOOTH_IS_STATIC` | bool | True during static load, false during dynamic load |

Mirrored in `AttractionConstants.runtime.*` for cleaner script reads.

### Engine modifier globals (GDD §4.1–4.3, live and updated in real time)
| Global | Type | GDD source | Neutral default |
|---|---|---|---|
| `ENGINE_MOD_MASS` | float | §4.1 Core Physical | 1.0 |
| `ENGINE_MOD_VOLUME` | float | §4.1 Core Physical | 1.0 |
| `ENGINE_MOD_FRICTION` | float | §4.1 Core Physical | 1.0 |
| `ENGINE_MOD_KARMA` | float | §4.2 Meta-Navigational | 0.0 (-1..1) |
| `ENGINE_MOD_LUCK` | float | §4.2 Meta-Navigational | 0.0 |
| `ENGINE_MOD_PERSUASION` | float | §4.2 Meta-Navigational | 0.0 |
| `ENGINE_MOD_HEAT` | float | §4.2 Meta-Navigational | 0.0 |
| `ENGINE_MOD_SLEIGHT_OF_HAND` | float | §4.3 Tactile | 0.0 |
| `ENGINE_MOD_NERVE` | float | §4.3 Tactile | 0.0 |

Also available as the structured table `ENGINE_MODIFIERS` (same keys in snake_case).

Mirrored in `AttractionConstants.modifiers.*` — games should read from there for consistency.

### Important: modifier globals are live
These values are re-synced to Lua every frame whenever the F1 tuning panel changes them.
**Do not cache them at script load time.** Read them inside `OnStep` each frame.

---

## 4. What attraction scripts must implement

### Required — static geometry
```lua
function OnLoadStatic()
    SpawnSharedBooth()          -- always call first
    -- spawn cabinet-specific static geometry here
end
```

### Required — dynamic gameplay
```lua
function OnLoad()
    -- spawn kinematic/dynamic bodies
    -- register a step callback:
    MidwayPhysics.OnStep(function(dt)
        -- read AttractionConstants.modifiers each frame, not at load time
        local MOD = AttractionConstants.modifiers
        -- game logic here
    end)
end
```

### Optional
- `OnUnload()` — called when the slot is unloaded dynamically (not yet used by engine)

---

## 5. Coordinate convention
- **1 engine unit = 1 meter**
- All positions passed to `MidwayPhysics.*` are in **local booth space**
- Origin = floor center of booth
- `+X` = right, `+Y` = up, `+Z` = back, booth opening faces `-Z`
- The engine applies the booth-to-world transform automatically

---

## 6. Canonical booth dimensions
Defined in `AttractionConstants.booth` and enforced by `AttractionManager`:

| Constant | Value | Notes |
|---|---|---|
| `booth.width_x` | 9.0 | Target X size for `SpawnSharedBooth` mesh |
| `booth.height_y` | 9.0 | Target Y size |
| `booth.depth_z` | 15.0 | Target Z size, equals `BOOTH_SPACING` |
| `BOOTH_SIDE_X` | 10.5 | World X offset of booth center from midway |
| `BOOTH_SPACING` | 15.0 | Gap between station Z positions |
| `CORRIDOR_HALF_WIDTH` | 6.0 | Player walkable half-width |

Booths are centered at `x = ±10.5` so the 9m-wide shell clears the 12m corridor without overlap.

---

## 7. MidwayPhysics API
### Static body creation
- `SpawnStaticMesh(lx, ly, lz, yaw, path, [sx], [sy], [sz])` → handle
  - `sx/sy/sz` are **target world-space sizes in meters**, not multipliers
  - Engine auto-computes scale = targetSize / nativeMeshAABB
- `SpawnStaticBox(lx, ly, lz, w, h, d)` → handle
- `SpawnStaticSphere(lx, ly, lz, r)` → handle
- `SpawnStaticCapsule(lx, ly, lz, halfH, r)` → handle
- `SpawnStaticCylinder(lx, ly, lz, halfH, r)` → handle

### Kinematic body creation
- `SpawnKinematicBox(lx, ly, lz, w, h, d)` → handle
- `SpawnKinematicSphere(lx, ly, lz, r)` → handle
- `SpawnKinematicCapsule(lx, ly, lz, halfH, r)` → handle
- `SpawnKinematicCylinder(lx, ly, lz, halfH, r)` → handle

### Dynamic body creation
- `SpawnDynamicMesh(lx, ly, lz, yaw, mass, path)` → handle
- `SpawnDynamicBox(lx, ly, lz, w, h, d, [mass])` → handle
- `SpawnDynamicSphere(lx, ly, lz, r, [mass])` → handle
- `SpawnDynamicCapsule(lx, ly, lz, halfH, r, [mass])` → handle
- `SpawnDynamicCylinder(lx, ly, lz, halfH, r)` → handle

### Sensor / trigger bodies
- `SpawnSensorBox(lx, ly, lz, w, h, d)` → handle
- `SpawnSensorSphere(lx, ly, lz, r)` → handle

### Movement and queries
- `MoveKinematic(handle, lx, ly, lz, dt)`
- `GetPosition(handle)` → lx, ly, lz
- `GetVelocity(handle)` → vx, vy, vz
- `IsActive(handle)` → bool
- `IsSensorTriggered(handle)` → bool

### Impulses / velocity
- `SetLinearVelocity(handle, vx, vy, vz)`
- `AddLinearVelocity(handle, vx, vy, vz)`
- `ApplyImpulse(handle, ix, iy, iz)`
- `ApplyAngularImpulse(handle, ix, iy, iz)`

### Per-body property overrides
- `SetFriction(handle, v)`
- `SetRestitution(handle, v)`
- `SetGravityFactor(handle, v)`
- `SetMass(handle, kg)`
- `SetLinearDamping(handle, v)`
- `SetAngularDamping(handle, v)`

### Object pools (generic — replaces coin-specific pool)
All attractions manage their own object pools via this generic API.
The old dedicated `AcquireCoin`/`ReturnCoin`/`CoinPoolFree`/`CullGutterCoins` bridge
has been removed.  Every pool is now per-slot and per-game.

- `CreatePool(name, hotN, coldN, paramsTable)` → bool
  - paramsTable keys: `shape`, `w`, `h`, `d`, `radius`, `halfH`, `mass`, `friction`,
    `restitution`, `linearDamping`, `angularDamping`
  - Creates a two-tier object pool (hot = sleeping in broadphase, cold = removed).
  - Use a unique name per slot (e.g., `"plinko_balls_3"`, `"coin_cascade_coins_7"`).
- `PoolAcquire(name, lx, ly, lz)` → handle (0 = pool exhausted)
- `PoolReturn(name, handle)`
- `PoolCullBelow(name, yThreshold)` → table of culled handles
- `PoolFree(name)` → int
- `PoolTotal(name)` → int

### Lifetime / callbacks
- `DestroyBody(handle)`
- `OnStep(fn)` — registers `fn(dt)` as the per-physics-step callback for this slot
  - Pass `nil` to clear

---

## 8. Modifier bridge — current status

### Fully implemented
The engine (C++) now:
1. Loads `modifier_state.json` at startup and populates `m_modifiers`
2. Snapshots loaded values into `m_initialModifiers`
3. Syncs all nine modifier values to Lua globals and `ENGINE_MODIFIERS` table
4. Re-syncs live every frame when the F1 tuning panel changes a value
5. Saves current modifier values back to `modifier_state.json` on shutdown

### F1 dev tuning panel
- Press **F1** in-game to toggle the live modifier panel
- Sliders cover all nine GDD modifiers with appropriate ranges
- **Reset** button reverts all sliders to the values present at startup
  (i.e. whatever was in `modifier_state.json` when the session began)
- Changes are saved automatically on exit

### How a game script should consume modifiers
```lua
MidwayPhysics.OnStep(function(dt)
    local MOD = AttractionConstants.modifiers
    -- MOD.mass, MOD.friction, MOD.luck, MOD.heat, etc.
    -- Apply as multipliers or offsets to gameplay values.
    -- Do NOT read ENGINE_MOD_* globals directly — use AttractionConstants.modifiers
    -- so the script benefits from the shared defaults/clamping layer.
end)
```

---

## 9. Attraction constants — self-contained design

Each attraction script now defines its own game-specific constants locally at the top of the file.
This makes attractions **self-contained** — a modder can copy any attraction directory to create
a new one without editing shared config files.

### What stays in `AttractionConstants` (shared)
- `booth` — canonical booth dimensions (width_x, height_y, depth_z, button)
- `modifiers` — live engine modifier values (mass, friction, luck, etc.)
- `runtime` — per-slot identity globals (worldX, worldZ, slotID, isStatic)

### What each attraction defines locally
- **Coin Cascade**: CABINET, PUSHER, COIN, FIELD tables
- **Plinko**: BOARD, PEG, BALL, SLOT tables
- **Crumbling Façade**: WALL, BRICK, BALL, SCORE tables

### Strict separation
The following are **not** part of the engine modifier bridge and must not be added to it:
- coin pusher tuning values
- booth dimensions
- cabinet dimensions
- shelf heights
- pusher stroke settings
- spawn counts

Those are attraction design constants and belong in the attraction's own script only.

---

---

## 10. Input bridge — `MidwayInput.*` Lua API

Registered at startup via `MidwayInput::Register(m_lua)` in `Engine::Init()`.
Available to all attraction scripts at all times (poll from inside `MidwayPhysics.OnStep`).

Reads the live SDL keyboard state snapshot maintained by the OS — no additional event
handling is required in Lua.

### Logical-action API (recommended)

| Function | Returns | Description |
|---|---|---|
| `MidwayInput.IsActionDown("fire")` | bool | Space held |
| `MidwayInput.IsActionDown("aim_left")` | bool | A or Left-Arrow held |
| `MidwayInput.IsActionDown("aim_right")` | bool | D or Right-Arrow held |
| `MidwayInput.IsActionDown("power_up")` | bool | W or Up-Arrow held |
| `MidwayInput.IsActionDown("power_down")` | bool | S or Down-Arrow held |

### Raw-key API (escape hatch)

| Function | Returns | Description |
|---|---|---|
| `MidwayInput.IsKeyDown(name)` | bool | True while the named SDL key is held. `name` is a single letter (`"A"`) or an SDL key name (`"Space"`, `"Return"`, `"Left"`, etc.). Case-sensitive per SDL convention. |

### Usage example
```lua
MidwayPhysics.OnStep(function(dt)
    if MidwayInput.IsActionDown("fire") then
        -- player is holding the fire/throw key this frame
    end
    if MidwayInput.IsActionDown("aim_left") then
        -- player is aiming left
    end
end)
```

### Constraints
- Returns the instantaneous held state only — there is no edge-trigger (`JustPressed`/`JustReleased`).
  Track state between frames in your own Lua variable if you need a rising edge.
- `SDLK_ESCAPE`, `SDLK_F1`–`SDLK_F3`, and `SDLK_SPACE` are consumed by the engine for navigation
  and booth activation; attraction scripts should treat the `"fire"` action as advisory only
  while the player is already inside the booth interaction view.
- Do not attempt to call SDL functions directly from Lua — always use this bridge.

---

---

## 11. Economy bridge — `Engine.*` Lua API

Registered at startup via `Engine::RegisterEconomyBridge()`. Available to all attraction scripts at all times (not slot-scoped).

| Function | Returns | Description |
|---|---|---|
| `Engine.AwardTickets(n, label)` | — | Adds `n` tickets, queues a win banner with `label` |
| `Engine.AwardTokens(n, label)` | — | Adds/subtracts `n` soul tokens, queues a banner |
| `Engine.GetTickets()` | int | Current ticket balance |
| `Engine.GetTokens()` | int | Current soul token balance |
| `Engine.GetStreak()` | int | Current streak counter |

`label` is a short display string shown in the win banner (e.g. `"PLINKO  x50"`).
Pass `nil` or omit to use the default label.

### Win banner behaviour
- Appears centred on screen at ~35% from top
- Gold header text (the label), white subtext ("+N TICKETS")
- 3.5s display, fades out over the last 1s
- Multiple simultaneous banners stack vertically
Every Lua↔C++ call is logged to `bridge_log.txt` (truncated each run).
Format: `<timestamp_us>  <fn_name>  <args>  -> <result>`
`MoveKinematic` is excluded from logging to avoid flooding.



### CRITICAL ARCHITECTURAL BOUNDARY: ENGINE VS. ATTRACTIONS
1. **The C++ Domain:** STRICTLY reserved for generic engine systems (Graphics, Audio, Core Jolt Physics world, Sol2 bindings). The C++ engine MUST NOT contain game-specific logic or hardcoded classes for individual games (e.g., no `SkeeballCollider` or `GreedGutter` in C++).
2. **The Lua Domain:** ALL individual midway attractions and game-specific mechanics MUST be written in pure Lua. If an attraction requires physics, the Lua script MUST utilize the generic Sol2 bridges exposed by the C++ engine (e.g., `MidwayPhysics.SpawnStaticBox`).
3. **Task Decomposition:** The Director MUST NOT create [C++] tasks for specific games.
