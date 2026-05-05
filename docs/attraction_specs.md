# Midway Attraction Specs

## 1. Global unit and axis conventions
- **1 engine unit = 1 meter**
- **Local booth origin**: center of booth at floor level
- **Axes**:
  - `+X` = right
  - `+Y` = up
  - `+Z` = back
  - booth opening faces `-Z`

All CAD exports, Lua placement logic, and gameplay tuning should follow this convention.

## 2. Canonical booth footprint
All midway booths use the same outer footprint.

### Booth shell target size
- `booth.width_x   = 9.0`
- `booth.height_y  = 9.0`
- `booth.depth_z   = 15.0`

These match the current shared booth placement used by the engine.

### Midway placement
- Each station slot is spaced by `15.0` units along Z.
- Booths are placed at `x = ±9.0` from the midway centerline.
- Booths should **touch visually** from stall to stall but never intersect.

## 3. Shared booth assumptions
The shared booth provides:
- outer shell / tent / booth enclosure
- front interaction area
- standard button placement zone

### Default button zone
- local position: `(0.0, 1.7, -HD + 0.5)`
- where `HD = booth.depth_z * 0.5`

## 4. Attraction cabinet spec
Each game should fit inside the canonical booth footprint.

Recommended first-pass cabinet envelope:
- max width: `7.0`
- max height: `6.5`
- max depth: `11.0`

Keep at least:
- `0.5m` side clearance
- `0.5m` rear clearance
- visible front access / interaction area

## 5. Dynamic parts policy
Moving parts should be authored as separate meshes whenever possible.

Examples:
- pusher
- tray
- handle
- lever
- rotating wheel
- gates
- decorative animated parts

Physics should remain primitive-driven until gameplay is stable.
Visual mesh and gameplay collision should be treated separately.

## 6. Coin pusher mechanical envelope
The **mechanical envelope** is the full space a machine’s moving/interactive parts may occupy during gameplay.
It includes:
- pusher stroke range
- tray/playfield area
- coin spawn zone
- coin loss/gutter zone
- payout edge
- all moving-part clearances

For a coin pusher, this should be defined before finalizing cabinet art, because the cabinet must be built around the motion limits of the mechanism.

## 7. Future engine-driven modifiers
The engine/Lua bridge should expose only the modifier classes defined in GDD §4.1–4.3.
These should be treated as configuration values injected by the Midway Host, not hardcoded per attraction.

### GDD §4.1 Core Physicals
- Mass
- Volume
- Friction

### GDD §4.2 Meta-Navigational Stats
- Karma
- Luck
- Persuasion
- Heat

### GDD §4.3 Tactile Stats
- Sleight of Hand
- Nerve

All such values should be read from the shared constants/config layer, even if they currently default to neutral placeholder values until the engine begins supplying real runtime data.

## 8. CAD export checklist
Before exporting a booth/cabinet mesh:
- model in meters
- confirm origin is at floor-center
- confirm front faces `-Z`
- freeze transforms / apply scale
- ensure no accidental 100x / 0.01x unit conversion
- verify mesh fits canonical booth dimensions
- keep triangle count modest for low-end hardware

## 9. Near-term workflow
1. lock booth shell dimensions
2. lock cabinet envelope
3. define dynamic movement envelopes
4. design cabinet in CAD
5. export GLB
6. keep physics primitive-based until gameplay is correct
