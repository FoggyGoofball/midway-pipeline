# Midway to Nowhere — Completed Features

> A comprehensive inventory of implemented features compared against GDD v19.
> Generated: 2026-04-28

---

## Table of Contents
1. [Engine & Core Architecture](#1-engine--core-architecture)
2. [Physics & Integration](#2-physics--integration)
3. [Lua Scripting Bridge](#3-lua-scripting-bridge)
4. [Attractions Implemented](#4-attractions-implemented)
5. [Rendering & Shaders](#5-rendering--shaders)
6. [Developer Tooling](#6-developer-tooling)
7. [Asset Pipeline](#7-asset-pipeline)
8. [Project Infrastructure](#8-project-infrastructure)
9. [Partially Implemented Systems](#9-partially-implemented-systems)

---

## 1. Engine & Core Architecture

### ✅ SDL2 Window & OpenGL 3.3 Context (GDD §2.1)
- Fully functional `SDL2` window creation with OpenGL 3.3 core profile (`Engine.cpp`)
- Double-buffered rendering with VSync toggle
- Window dimensions: 1280×720
- GLEW initialisation for modern OpenGL extension loading

### ✅ The Vicious Cycle (GDD §1, §2.2)
- Seamless spatial looping system at Z = ±`MIDWAY_LENGTH` (180m) (`Engine.cpp:466-480`)
- Player is seamlessly teleported from Z=180 back to Z=0 (and vice versa)
- Lap counter tracks total cycles
- Booth stations shift their Z positions to match the teleport
- All out-of-range stations recycled in a single frame (no holes or overlaps)

### ✅ First-Person Camera (GDD §2.1)
- Free-look mouse control with yaw/pitch clamping (±60°)
- WASD movement in the corridor (X clamped to ±6m corridor width)
- Player standing height: **3.9m** (camera raises 50% from 2.6m to see over stall edges)
- Booth interaction camera snaps to standing height (3.9m) matching corridor view
- Mouse look disabled while dev console is open

### ✅ Configurable `init.lua` Bootstrap
- `init.lua` loaded at engine start
- Contains `config` table with title, version, camera speed, and cycle limit
- Readable from C++ via `m_lua["config"]`

### ✅ Corridor Geometry
- Booth side X offset: ±10.5m (shell clears 12m-wide corridor)
- Midway length: 12 stations × 15m spacing = 180m total span
- Z-symmetric station distribution around origin

---

## 2. Physics & Integration

### ✅ Jolt Physics 3D Engine (GDD §2.1, §2.2, §8)
- Fully registered and configured Jolt Physics system (`PhysicsManager.cpp`)
- Two-layer collision: NON_MOVING (static) and MOVING (kinematic/dynamic) layers
- Broadphase layer filtering with custom `BPLayerInterfaceImpl`, `ObjVsBPFilterImpl`, `ObjLayerPairFilterImpl`
- Gravity: -9.81 m/s² on Y axis
- Multi-threaded job system with configurable worker threads
- Body pool budget: 12,288 bodies total (1024 hot + ~9500 cold + headroom)
- Body locked contact budget: 4096 pairs, 4096 constraints
- Temp allocator: 16MB

### ✅ Generic Physics Spawning API
- Static bodies: Box, Sphere, Capsule, Cylinder, Mesh (glTF/OBJ via Assimp)
- Kinematic bodies: Box, Sphere, Capsule, Cylinder
- Dynamic bodies: Box, Sphere, Capsule, Cylinder, Mesh
- Sensor bodies (overlap-only triggers): Box, Sphere

### ✅ Local-to-World Transform System
- Booth transform stores world X, Z, and yaw rotation
- `LocalToWorld()` transforms local-space coordinates to booth world-space
- Lua scripts operate entirely in local space — engine handles translation

### ✅ Per-Slot Transform Cache
- Each slot's transform is stored and restored during step callbacks
- Supports multiple simultaneous slots with independent transforms

### ✅ Generic Named Object Pools (GDD §2.2)
- Two-tier pool system: hot (active) + cold (parked/removed)
- Hot bodies sleep in broadphase; cold bodies are fully removed
- Supports Box, Sphere, Capsule, Cylinder shapes
- Configurable friction, restitution, mass, damping per pool
- Per-slot pool isolation with unique pool names

### ✅ Per-Slot Step Callbacks
- Each slot registers its own `OnStep` callback keyed by slot ID
- Standalone (global) callback also supported
- Callback safety: lambda captures callback locally, preventing self-destruction crashes
- Transform context is restored before each slot's callback fires

### ✅ Body Property Overrides
- `SetFriction`, `SetRestitution`, `SetGravityFactor`, `SetMass`
- `SetLinearVelocity`, `AddLinearVelocity`, `ApplyImpulse`, `ApplyAngularImpulse`
- `SetLinearDamping`, `SetAngularDamping`
- `GetPosition`, `GetVelocity`, `IsActive`, `IsSensorTriggered`

### ✅ Kinematic Movement
- `MoveKinematic` for pushers, moving platforms, etc.

### ✅ Booth Static/Dynamic Lifecycle
- Static bodies captured during `OnLoadStatic()`, never destroyed
- Dynamic bodies captured during `OnLoad()`, destroyed on walk-away
- Deactivate/activate for sleeping/waking kinematic bodies

---

## 3. Lua Scripting Bridge

### ✅ sol2 Lua Integration (GDD §2.2)
- sol2 library bound to Lua 5.x
- Lua libraries loaded: `base`, `math`, `string`, `table`, `io`, `os`
- `MidwayPhysics` namespace registered as Lua table

### ✅ Complete Lua ↔ C++ Bridge (GDD §2.2)
- 40+ C++ functions exposed to Lua via `MidwayPhysics.*` API
- **Static spawners**: `SpawnStaticMesh`, `SpawnStaticBox`, `SpawnStaticSphere`, `SpawnStaticCapsule`, `SpawnStaticCylinder`
- **Kinematic spawners**: `SpawnKinematicBox`, `SpawnKinematicSphere`, `SpawnKinematicCapsule`, `SpawnKinematicCylinder`
- **Dynamic spawners**: `SpawnDynamicMesh`, `SpawnDynamicBox`, `SpawnDynamicSphere`, `SpawnDynamicCapsule`, `SpawnDynamicCylinder`
- **Sensors**: `SpawnSensorBox`, `SpawnSensorSphere`
- **Movement**: `MoveKinematic`
- **Queries**: `GetPosition`, `GetVelocity`, `IsActive`, `IsSensorTriggered`
- **Impulses**: `SetLinearVelocity`, `AddLinearVelocity`, `ApplyImpulse`, `ApplyAngularImpulse`
- **Properties**: `SetFriction`, `SetRestitution`, `SetGravityFactor`, `SetMass`, `SetLinearDamping`, `SetAngularDamping`
- **Pool API**: `CreatePool`, `PoolAcquire`, `PoolReturn`, `PoolCullBelow`, `PoolFree`, `PoolTotal`
- **Lifetime**: `DestroyBody`, `OnStep`

### ✅ Engine Modifier Globals (GDD §4.1–4.3)
All 9 modifier globals synced to Lua every frame:
- Core Physicals: `mass`, `volume`, `friction`
- Meta-Navigational: `karma`, `luck`, `persuasion`, `heat`
- Tactile: `sleight_of_hand`, `nerve`

### ✅ Bridge Diagnostic Logging
- All Lua↔C++ calls logged to `bridge_log.txt` with microsecond timestamps
- `MoveKinematic` excluded from logging to prevent flooding
- Error callbacks logged with slot ID context

### ✅ Booth Identity Globals
- `BOOTH_WORLD_X`, `BOOTH_WORLD_Z`, `BOOTH_SLOT_ID`, `BOOTH_IS_STATIC`

### ✅ Attraction Constants & Config
- `AttractionConstants` table with booth dimensions, game defaults, modifier values
- `ENGINE_MODIFIERS` structured table (mirrors globals)
- `SharedBooth` helper functions (`HalfDepth()`, `ButtonZ()`)

---

## 4. Attractions Implemented

### ✅ The Coin Cascade (GDD §8 — Fully Playable)
- Full coin pusher simulation with Jolt 3D physics
- Oscillating kinematic pusher shelf (sinusoidal motion)
- Cylindrical coin pool (500 hot + 9500 cold)
- Modifier integration: mass, friction, heat accelerate pusher, luck boosts coin target, sleight of hand extends stroke
- Gutter culling system — coins below Y=-2 returned to pool
- Configurable column spawn pattern
- 80 coin target live count

### ✅ Purgatorial Plinko (GDD §8 — Fully Playable)
- 5 peg types: Normal (40%), Bouncy (20%), Sticky (20%), Fragile (10%), Explosive (10%)
- Triangular/diamond peg field: 8 rows, 7/6 alternating pattern
- Fragile pegs shatter and kick balls on proximity
- Explosive pegs apply radial impulse on proximity
- 7 scoring slots with sensor boxes, multipliers: [2, 5, 10, 50, 10, 5, 2]
- Modifier integration: mass, friction, heat speeds drops, sleight narrows spread, luck boosts score
- Deterministic peg layout per slot (seeded RNG by slot ID)
- Ball pool (20 hot + 80 cold sphere-based)
- Score tracking across session

### ✅ The Crumbling Façade (GDD §8 — Fully Playable)
- Destructible wall with 5×5 brick grid (25 bricks)
- Ball launcher with configurable force and angle
- Brick pool (25 hot + 75 cold box-based)
- Ball pool (5 hot + 45 cold sphere-based)
- Score tracking: 10 pts per brick, 250 max
- Modifier integration: mass/volume affect ball size, friction affects bounce, luck boosts score
- Self-contained constants (no external config dependency)

### ✅ Booth Infrastructure
- **`SpawnSharedBooth()`** — loads `defaultbooth.glb` scaled to canonical 9×9×15m footprint
- Shared play button collision block at standard position
- Booth shell: `defaultbooth.glb` model

---

## 5. Rendering & Shaders

### ✅ Debug Renderer
- Wireframe rendering of boxes and cylinders (Jolt debug view)
- GLSL 330 core shader pipeline
- Solid mesh shader for GLB assets with N·L diffuse lighting
- Texture support: albedo (sRGB), normal map (linear), emissive (sRGB)
- Material detection for texture presence
- Per-frame render logging to `render_log.txt` (every 60 frames)
- Corridor center-line dashes (yellow) spanning 2× cycle length
- Booth highlight (green wireframe) on nearest activatable slot

### ✅ Assimp GLB Model Loading (GDD §2.1)
- Full Assimp integration for glTF/OBJ model loading
- Vertex processing with normals and UV coordinates
- Embedded texture extraction from GLB files
- AABB computation per model
- GPU upload with VAO/VBO/EBO
- Texture cache (prevents duplicate loads)
- Auto-sizing: mesh fits to requested world-space dimensions

### ✅ Billboard-ready Barker Assets
- 10 high-res Barker sprite PNGs in `barker/` directory
- Crouching, congratulatory poses, laughter, etc.
- Master key reference art

---

## 6. Developer Tooling

### ✅ Dear ImGui Console (GDD §2.1)
- Always-visible dev overlay with:
  - FPS / frame time
  - Player position (X, Y, Z) and yaw/pitch
  - Vicious Cycle lap counter and seam position
  - Station status (12 stations, live/idle, left/right slot status)
  - Physics body counts (total/active)
  - Economy: Soul Tokens, Tickets, Streak
  - All 9 modifier values displayed
  - Keybinding hint bar: **F1 Modifiers  F2 Tilt  F3 Reset Game**
- **F1 Modifier Tuning Panel:**
  - Sliders for all 9 GDD modifiers with appropriate ranges
  - **Reset Modifiers** button — reverts to startup-loaded values
  - **Tilt (F2)** button — applies random impulse to all active physics bodies
  - **Reset Game (F3)** button — reloads the current attraction from scratch
  - Live synced to Lua every frame
  - Auto-saved to `modifier_state.json` on shutdown
- **Cursor management:** F1 releases/captures mouse so ImGui panels are fully interactive
- **F2 — TILT:** applies random lateral + upward impulse to every dynamic body in the active slot (GDD §4.3 Sleight of Hand)
- **F3 — Reset Game:** unloads and immediately reloads dynamic layer of active attraction

### ✅ Modifier State Persistence
- `modifier_state.json` loaded at startup and saved on shutdown
- Initial modifier state snapshot for reset support

### ✅ Logging Infrastructure
- `midway.log` — stdout/stderr capture
- `bridge_log.txt` — Lua↔C++ bridge diagnostics
- `render_log.txt` — Renderer diagnostics
- Comprehensive debug output throughout all engine subsystems

---

## 7. Asset Pipeline

### ✅ vcpkg Dependency Management
- SDL2, OpenGL, GLEW, glm, sol2, JoltPhysics, Lua, imgui, assimp, stb, nlohmann-json
- CMake build system with presets

### ✅ Asset Directory Structure
- `resources/models/` — `defaultbooth.glb`, `goldcoin.glb`, `silvercoin.glb`
- `assets/audio/`, `assets/data/`, `assets/fonts/`, `assets/meshes/`, `assets/shaders/`, `assets/textures/`
- `assets/data/` has JSON schemas ready for:
  - `prizes.json` (empty, schema-ready)
  - `augments.json` (empty, schema-ready)
  - `dialogue.json` (empty, schema-ready)

### ✅ GLB Model Auto-Sizing
- Models are auto-fitted to requested world-space dimensions
- Native AABB is computed on load
- Final scale = target size / native AABB per axis

---

## 8. Project Infrastructure

### ✅ CMake Build System
- C++17 standard, minimal MSVC runtime
- Presets via `CMakePresets.json`
- `midway.slnx` solution file
- `build_only.bat` for quick rebuilds

### ✅ Station Recycling System
- 12 stations with left/right slots
- Stations recycle when player walks past (Z threshold exceeded)
- All out-of-range stations recycled in a single frame (no holes or overlaps)
- Static geometry destroyed and rebuilt on recycle
- Dynamic bodies cleaned up on walk-away

### ✅ Space Bar Interaction
- SPACE activates nearest looking-at booth
- Camera snaps to booth view (front-facing)
- SPACE again to leave (restores saved position/rotation)
- Interaction radius: 14m, minimum dot product: 0.6

---

## 9. Partially Implemented Systems

### 🔶 Modifier System (GDD §4)
- **Complete**: Engine syncs all 9 modifier globals to Lua each frame
- **Complete**: F1 panel provides live tuning
- **Complete**: JSON persistence on startup/shutdown
- **Incomplete**: Games only *read* modifiers but do not *apply* them to meaningful gameplay outcomes
- **Incomplete**: No meta-progression loop awards Prizes that modify these stats
- **Incomplete**: Coin Cascade and Plinko both read modifiers but their effect on gameplay is minimal (debug-level tuning only)

### 🔶 Prize/Augment Economy (GDD §5, Appendix A)
- **Complete**: Prize lexicon defined in GDD (286 entries across 5 rarities)
- **Complete**: JSON schema files created (`prizes.json`, `augments.json`) with empty arrays
- **Incomplete**: No runtime prize loading, inventory system, bribery, or Hilux effect

### 🔶 Machine-Specific Augments (GDD §5)
- **Complete**: 284 augments defined in GDD Appendix A for Coin Cascade, Plinko, Claw, Slingshot, Ring Toss, Crumbling Façade, Swami, Lost & Found
- **Incomplete**: None of these are loadable or usable at runtime

### ✅ Dual-Currency Economy (GDD §3) — Phase 1
- `EconomyManager` tracks **Soul Tokens** (starts 100) and **Tickets**
- Streak counter (`IncrementStreak` / `ResetStreak`)
- Persisted to `economy_state.json` — loaded on startup, saved on shutdown
- Lua bridge: `Engine.AwardTickets(n, label)`, `Engine.AwardTokens(n, label)`, `Engine.GetTickets()`, `Engine.GetTokens()`, `Engine.GetStreak()`
- **Win notification banners:** centred large fading overlay, 3.5s TTL, stacks multiple awards
- Both attractions wire ticket rewards:
  - **Plinko** — awards tickets per scoring slot with multiplier label ("PLINKO ×50")
  - **Coin Cascade** — awards 1 ticket per coin that falls off the pusher into the gutter

### 🔶 Emotional Baggage / Meta-Progression (GDD §4.4, §6.3)
- **Incomplete**: Empty `assets/data/baggage/` directory
- **Incomplete**: No Lost & Found hub, no save system, no meta-progression

### 🔶 Barker AI & Dialogue (GDD §15)
- **Incomplete**: Barker sprites exist but are not rendered with billboarding system
- **Incomplete**: `dialogue.json` is empty — no reactive dialogue matrix
- **Incomplete**: No Barker entity in the game world

### 🔶 Karma System (GDD §4.2)
- **Incomplete**: Variable exists and is tunable via F1 panel
- **Incomplete**: No gameplay consequences for Karma shifts
- **Incomplete**: No Angelic Assist / Demonic Skew visual effects

### 🔶 Visual Effects (GDD §2.3, §12)
- **Incomplete**: No PS1 vertex snapping shader
- **Incomplete**: No Demonic Skew vertex displacement
- **Incomplete**: No Karmic-Temporal Transmutation Matrix
- **Incomplete**: No dithering or pixelation effects

### 🔶 Expansions & Future Attractions (GDD §9)
- **Not Started**: Sunk Costs (Duck Pond), The Guilt Trip (Haunted House), The Frog Bog, The Strongman High Striker, The Penny Arcade, Skeeball, The Coin Ski Jump

### 🔶 Terminal Wagers & Endgame (GDD §11)
- **Not Started**: Death's Door state, The Architect's Run, The Barker's Regalia

### 🔶 Concessions & Utility Stalls (GDD §7)
- **Not Started**: The Wight & Measure, Just Desserts, Spirits & Libations

### 🔶 Boss Encounters (GDD §10)
- **Not Started**: 10 Emotional Baggage boss encounters
- **Not Started**: The Geometric Fracture transition

### 🔶 SoLoud Audio (GDD §2.1)
- **Not Started**: No audio engine integrated yet
- **Not Started**: No 3D positional audio
- **Not Started**: No sound effects for attractions

### 🔶 Box2D Integration (GDD §2.1)
- **Not Started**: No Box2D library in dependencies
- **Not Started**: No 2D planar attractions

### 🔶 Origin Cluster (GDD §1)
- **Not Started**: The Lost & Found, Grand Prize Pavilion, Swami Samsara

### 🔶 Save System (GDD §6.1)
- **Not Started**: No save/load functionality
- **Not Started**: No Maintenance Alley or Park Bench
