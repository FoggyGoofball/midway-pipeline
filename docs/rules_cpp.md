# C++ Engine Rules — Midway to Nowhere
## Review Checklist for `/arch_cpp`

### Language & Build
- [ ] **C++17 only.** No C++20 features. No exceptions (`-fno-exceptions`). CMake build via `CMakeLists.txt`.
- [ ] **Stack:** SDL2 (window/input), OpenGL 3.3+ (rendering), GLEW (extensions), GLM (math), nlohmann/json (data).
- [ ] **Physics:** Jolt Physics for 3D attractions (Coin Cascade, Claw). Box2D for 2D attractions (Crumbling Façade, Slingshot). Never mix engines in one attraction.
- [ ] **Lua bridge:** sol2 v3 bindings. Every Lua-accessible function must be registered in `MidwayPhysics.cpp` or `Engine.cpp`.

### Vicious Cycle (Spatial Seam)
- [ ] **Teleport trigger:** When `|playerZ| >= CYCLE_LENGTH (150.0f)`, teleport player back to `Z=0` with zero visual interruption.
- [ ] **Lap counter:** Increment `m_viciousCycleLap` on each seam crossing. Used for Heat scaling and difficulty escalation.
- [ ] **Physics bodies crossing the seam:** All dynamic bodies must be re-positioned by the same offset. Static bodies (booths) are re-indexed, not moved.

### Slot & Booth Architecture
- [ ] **Booth lifecycle:** `BeginBoothCapture(staticIDs, slotID)` → spawn geometry → `EndBoothCapture()` → `OptimizeBroadPhase()`.
- [ ] **Dynamic lifecycle:** `BeginDynamicCapture(dynamicIDs, slotID)` → spawn gameplay bodies → `EndDynamicCapture()`.
- [ ] **Physics culling:** Suspend physics for slots > 14.0m from player (`interaction_radius`). Destroy dynamic bodies on walk-away.
- [ ] **Coordinate transform:** `LocalToWorld(lx, lz, wx, wz)` applies booth transform. All Lua positions are local booth-space; engine auto-transforms.

### Body & Handle Management
- [ ] **Handle map:** Return integer handles to Lua (0 = invalid). Never expose raw `JPH::BodyID` to scripts.
- [ ] **Object pools:** Two-tier pools via `CreatePool(name, hotN, coldN, params)`. Hot = sleeping in broadphase. Cold = removed from system. Pool names must be unique per slot (e.g., `"plinko_balls_3"`).
- [ ] **Per-slot callbacks:** `SetSlotCallback(slotID, fn)`. Each slot gets its own `OnStep(dt)`. No global step callback in production mode.

### Modifier System (GDD §4.1–4.3)
- [ ] **Live sync:** `SyncModifierGlobalsToLua()` must run every frame. All 9 modifier values (mass, volume, friction, karma, luck, persuasion, heat, sleightOfHand, nerve) synced to Lua globals.
- [ ] **Persistence:** Load from `modifier_state.json` at startup. Save on shutdown. F1 panel changes are live and auto-saved.

### Rendering & Shaders
- [ ] **Billboarding:** The Carnival Barker sprite must use billboard transform (always face camera). 2D high-res sprite only.
- [ ] **Karmic-Temporal Transmutation Matrix:** Dual-axis GLSL shader. X-axis = PS1 vertex snapping ↔ smooth PBR. Y-axis = Karma (Demonic = bruised skies/film grain, Angelic = warm bloom).
- [ ] **Demonic Skew:** At extreme negative Karma, apply 3D noise-driven vertex displacement in GLSL.
- [ ] **Barker's Exemption:** The Barker's 2D sprite ignores the Transmutation Matrix entirely — always high-res and fully lit.

### Memory & Performance
- [ ] **No raw `new`/`delete`.** Use `std::unique_ptr`, `std::vector`, `std::map`. RAII for all resources.
- [ ] **Physics culling:** Only simulate physics for the active slot + adjacent slots. Destroy all dynamic bodies when player leaves.
- [ ] **Steam Deck target:** Assume 1280×720 resolution. Minimize draw calls. Use instancing for repeated geometry (coins, pegs, bricks).
