# Internal API Ledger <a name="internal-api-ledger"></a>
> Auto-generated — public methods and global variables exposed by Midway to Nowhere subsystems.
> Maintained by the Contract Arbiter. Append new signatures here as they are created.
> Cross-reference: `docs/engine_lua_bridge_contract.md` for full bridge contract details.

## Core Engine (C++)

### PhysicsManager
*(`ReturnType PhysicsManager::MethodName(ParamType1, ParamType2, ...) — description`)*

### AttractionManager
*(Add signatures)*

### Engine
*(Add signatures)*

### DevConsole
*(Add signatures)*

### DebugRenderer
*(Add signatures)*

### MidwayPhysics (Bridge)
*(Add signatures)*

---

## Lua-Exposed API

### Attraction Lifecycle
*(Add signatures)*

### Modifier System
*(Add signatures)*

### Economy
*(Add signatures)*

### Physics Handles
*(Add signatures)*

---

## Global Variables

| Variable | Type | Defined In | Description |
|----------|------|------------|-------------|
| BOOTH_WORLD_X | float | Engine.cpp | World-space X center of this slot |
| BOOTH_WORLD_Z | float | Engine.cpp | World-space Z center of this slot |
| BOOTH_SLOT_ID | int | Engine.cpp | Unique slot identifier |
| BOOTH_IS_STATIC | bool | Engine.cpp | True during static load, false during dynamic load |
| ENGINE_MOD_MASS | float | Engine.cpp | GDD §4.1 Core Physical — neutral default 1.0 |
| ENGINE_MOD_VOLUME | float | Engine.cpp | GDD §4.1 Core Physical — neutral default 1.0 |
| ENGINE_MOD_FRICTION | float | Engine.cpp | GDD §4.1 Core Physical — neutral default 1.0 |
| ENGINE_MOD_KARMA | float | Engine.cpp | GDD §4.2 Meta-Navigational — neutral default 0.0 (-1..1) |
| ENGINE_MOD_LUCK | float | Engine.cpp | GDD §4.2 Meta-Navigational — neutral default 0.0 |
| ENGINE_MOD_PERSUASION | float | Engine.cpp | GDD §4.2 Meta-Navigational — neutral default 0.0 |
| ENGINE_MOD_HEAT | float | Engine.cpp | GDD §4.2 Meta-Navigational — neutral default 0.0 |
| ENGINE_MOD_SLEIGHT_OF_HAND | float | Engine.cpp | GDD §4.3 Tactile — neutral default 0.0 |
| ENGINE_MOD_NERVE | float | Engine.cpp | GDD §4.3 Tactile — neutral default 0.0 |
| ENGINE_MODIFIERS | table | Engine.cpp | Structured table with all 9 modifier values in snake_case keys |

## Public Constants

| Constant | Value | Defined In | Description |
|----------|-------|------------|-------------|
| booth.width_x | 9.0 | AttractionConstants.booth | Canonical booth width X |
| booth.height_y | 9.0 | AttractionConstants.booth | Canonical booth height Y |
| booth.depth_z | 15.0 | AttractionConstants.booth | Canonical booth depth Z |
| CYCLE_LENGTH | 150.0 | MidwayPhysics.h | Vicious Cycle teleport trigger threshold |

---

## Agent Registration Rules

When any agent generates code that introduces a new:

- **Public method** → Append to the appropriate C++ or Lua section with:
  ```markdown
  `ReturnType MethodName(ParamType1, ParamType2) — description`
  ```
- **Global variable** → Append to the Global Variables table with:
  ```markdown
  | Name | Type | File:Lineno | Description |
  ```
- **Public constant** → Append to the Public Constants table.

The Contract Arbiter performs this registration automatically during pipeline execution.
If a downstream agent references a symbol not in this ledger, the Contract Arbiter flags it as a hallucination.
