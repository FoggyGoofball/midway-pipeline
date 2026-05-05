# Midway to Nowhere — Documentation Index

> Central documentation hub for the Midway to Nowhere project.
> Generated: 2026-04-28

---

## Project Overview

**Midway to Nowhere** is a purgatorial carnival game built in C++17 with OpenGL 3.3, Jolt Physics, Lua scripting, and Dear ImGui. Players walk an infinite midway corridor (The Vicious Cycle), playing arcade games with modifiers driven by the GDD's 9-stat system (Mass, Volume, Friction, Karma, Luck, Persuasion, Heat, Sleight of Hand, Nerve).

---

## Document Map

### 📋 Planning & Tracking

| Document | Description | Path |
|---|---|---|
| **GDD (Game Design Document)** | The master design document — GDD v19 | [`GDD/Midway_to_Nowhere_Master_GDD_v19.md`](../GDD/Midway_to_Nowhere_Master_GDD_v19.md) |
| **Completed Features** | Full inventory of what's implemented vs GDD | [`docs/completed_features.md`](completed_features.md) |
| **Development TODO** | Prioritised task list (P0→P3) with file references | [`docs/todo.md`](todo.md) |
| **Task Progress** | Live task tracking for the current work session | [`task_progress.md`](../task_progress.md) |

### 🏗️ Technical Documentation

| Document | Description | Path |
|---|---|---|
| **Engine ↔ Lua Bridge Contract** | Spec for all Lua/C++ bridge APIs | [`docs/engine_lua_bridge_contract.md`](engine_lua_bridge_contract.md) |
| **Attraction Specs** | Attraction folder structure and conventions | [`docs/attraction_specs.md`](attraction_specs.md) |

### 🎮 Attractions

| Document | Description | Path |
|---|---|---|
| **Attractions README** | Entry-point contract, globals, and MidwayPhysics API reference | [`attractions/README.md`](../attractions/README.md) |
| **Shared Constants** | Booth dimensions, game defaults, modifier definitions | [`attractions/_shared/attraction_constants.lua`](../attractions/_shared/attraction_constants.lua) |
| **Booth Shared Geometry** | Common booth spawning code | [`attractions/booth_shared.lua`](../attractions/booth_shared.lua) |
| **Coin Cascade** | Fully playable coin pusher attraction | [`attractions/coin_cascade/coin_cascade.lua`](../attractions/coin_cascade/coin_cascade.lua) |
| **Purgatorial Plinko** | Fully playable hostile plinko board | [`attractions/plinko/plinko.lua`](../attractions/plinko/plinko.lua) |
| **Crumbling Façade** | Fully playable brick-wall breaker | [`attractions/crumblingfacade/crumblingfacade.lua`](../attractions/crumblingfacade/crumblingfacade.lua) |

### 🗄️ Data Assets

| Document | Description | Path |
|---|---|---|
| **Prizes** | Prize pool (currently empty schema) | [`assets/data/prizes.json`](../assets/data/prizes.json) |
| **Augments** | Machine augments (currently empty schema) | [`assets/data/augments.json`](../assets/data/augments.json) |
| **Dialogue** | Barker reactive dialogue (currently empty schema) | [`assets/data/dialogue.json`](../assets/data/dialogue.json) |
| **Modifier State** | Persistent modifier values at last session end | [`modifier_state.json`](../modifier_state.json) |

### 🖼️ Source Code Overview

| Module | Header | Implementation | Purpose |
|---|---|---|---|
| **Engine** | [`src/Engine.h`](../src/Engine.h) | [`src/Engine.cpp`](../src/Engine.cpp) | Main loop, camera, player input, cycle system |
| **PhysicsManager** | [`src/PhysicsManager.h`](../src/PhysicsManager.h) | [`src/PhysicsManager.cpp`](../src/PhysicsManager.cpp) | Jolt physics system, body spawning, pools |
| **AttractionManager** | [`src/AttractionManager.h`](../src/AttractionManager.h) | [`src/AttractionManager.cpp`](../src/AttractionManager.cpp) | Station lifecycle, Lua script loading, slot recycling |
| **MidwayPhysics** | [`src/MidwayPhysics.h`](../src/MidwayPhysics.h) | [`src/MidwayPhysics.cpp`](../src/MidwayPhysics.cpp) | Lua bridge binding (40+ functions) |
| **DebugRenderer** | [`src/DebugRenderer.h`](../src/DebugRenderer.h) | [`src/DebugRenderer.cpp`](../src/DebugRenderer.cpp) | Wireframe + mesh rendering, shaders |
| **ModelManager** | [`src/ModelManager.h`](../src/ModelManager.h) | [`src/ModelManager.cpp`](../src/ModelManager.cpp) | GLB model loading via Assimp, texture cache |
| **DevConsole** | [`src/DevConsole.h`](../src/DevConsole.h) | [`src/DevConsole.cpp`](../src/DevConsole.cpp) | ImGui overlay + F1 modifier tuning panel |
| **ModifierState** | [`src/ModifierState.h`](../src/ModifierState.h) | (inline) | 9-stat modifier state struct |
| **main** | — | [`src/main.cpp`](../src/main.cpp) | Entry point |

### 🖼️ Assets & Models

| Path | Description |
|---|---|
| [`resources/models/`](../resources/models/) | 3D models: `defaultbooth.glb`, `goldcoin.glb`, `silvercoin.glb` |
| [`resources/textures/`](../resources/textures/) | Model textures (externally referenced from GLB) |
| [`assets/textures/`](../assets/textures/) | Environment & UI textures |
| [`assets/meshes/`](../assets/meshes/) | Environment meshes |
| [`assets/audio/`](../assets/audio/) | Audio assets (empty — SoLoud not yet integrated) |
| [`assets/fonts/`](../assets/fonts/) | Font files |
| [`assets/shaders/`](../assets/shaders/) | Custom shader programs |
| [`barker/`](../barker/) | Barker sprite PNGs (10 poses, billboard-ready) |

---

## Quick Reference

### Build & Run

```bash
# Build
cmake --build build

# Run
./build/Debug/midway.exe
```

### Key Controls

| Key | Action |
|---|---|
| WASD | Move along corridor |
| Mouse | Look around (yaw/pitch) |
| SPACE | Activate / leave nearest booth |
| F1 | Toggle modifier tuning panel |
| Escape | Exit |

### Engine Architecture Flow

```
main.cpp
  └─ Engine::Run()
       ├─ PhysicsManager::Step()       ← Jolt physics tick
       ├─ DevConsole::NewFrame()       ← ImGui frame start
       ├─ AttractionManager::Update()  ← Station lifecycle + Lua callbacks
       │    ├─ OnLoadStatic()          ← Booth cabinet geometry
       │    ├─ OnLoad()                ← Gameplay bodies + OnStep()
       │    └─ OnStep()                ← Per-frame Lua callback
       ├─ DebugRenderer::RenderBodies()← Wireframe + mesh draw calls
       └─ DevConsole::Render()         ← ImGui overlay rendering
```

### GDD Modifier Stats

| Stat | Range | Category |
|---|---|---|
| Mass | 0.1–5.0 | Core Physical |
| Volume | 0.1–5.0 | Core Physical |
| Friction | 0.0–3.0 | Core Physical |
| Karma | -1.0–1.0 | Meta-Navigational |
| Luck | -5.0–5.0 | Meta-Navigational |
| Persuasion | -5.0–5.0 | Meta-Navigational |
| Heat | 0.0–10.0 | Meta-Navigational |
| Sleight of Hand | -5.0–5.0 | Tactile |
| Nerve | -5.0–5.0 | Tactile |

---

## Implementation Status

```
Phase 1 (Engine Foundation):    ████████████████░░░░  80%
Phase 2 (Economy + UX):         ██░░░░░░░░░░░░░░░░░░  10%
Phase 3 (Content + Polish):     █░░░░░░░░░░░░░░░░░░░   5%
Phase 4 (Bosses + Endgame):     ░░░░░░░░░░░░░░░░░░░░   0%
```

See [`docs/completed_features.md`](completed_features.md) for the full inventory and [`docs/todo.md`](todo.md) for what's next.
