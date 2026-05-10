# Integration Review Rules — Midway to Nowhere
## Cross-Domain Validation Checklist for `/review`

### API Contract Verification
- [ ] **C++ → Lua bridge:** Every Lua API function called in the script must be registered in `MidwayPhysics.cpp` or `Engine.cpp` via sol2. Check function signatures match exactly.
- [ ] **Lua → C++ handles:** All handles returned to Lua are integers (0 = invalid). No raw `JPH::BodyID` or `b2BodyId` leaks into Lua.
- [ ] **Pool name consistency:** Pool names in Lua (`CreatePool("name_N", ...)`) must match the C++ routing key. Pattern: `"{attraction}_{type}_{slotID}"`.
- [ ] **Modifier globals:** `SyncModifierGlobalsToLua()` in C++ must set all 9 globals. Lua must read them from `AttractionConstants.modifiers.*` each frame. Names must match.

### Feature Completeness
- [ ] **No orphaned tasks:** Every task in the Director's breakdown has corresponding generated code. No [C++] task left unimplemented.
- [ ] **No phantom APIs:** The Lua script does not call any API that doesn't exist in the C++ bridge.
- [ ] **NET wraps C++:** Every [NET] task's state replication covers the bodies and events created by the corresponding [C++] and [Lua] tasks.
- [ ] **Economy events:** If the feature awards tickets/tokens, the Lua `Engine.AwardTickets/Tokens` call must be wrapped by a server-authoritative NET layer.

### Coordinate System Alignment
- [ ] **Local ↔ world transform:** Lua positions are in local booth space (+X right, +Y up, +Z back). C++ `LocalToWorld()` applies booth offset. NET replicates world-space positions.
- [ ] **Canonical booth:** All Lua geometry fits within 9.0 x 9.0 x 15.0 meters. C++ booth lifecycle uses `BeginBoothCapture`/`EndBoothCapture`.
- [ ] **Physics engine match:** 3D attractions (Coin Cascade, Claw) use Jolt. 2D attractions (Crumbling Façade, Slingshot) use Box2D. Never mix in one attraction.

### Vicious Cycle Consistency
- [ ] **Teleport authority:** NET layer broadcasts lap count + teleport frame. C++ applies the teleport. Lua does NOT independently detect or trigger seam crossing.
- [ ] **Body re-indexing:** If booths wrap around the seam, C++ re-indexes static bodies. NET broadcasts the new mapping. Lua receives updated `BOOTH_WORLD_X/Z` globals.

### Modifier System Integrity
- [ ] **9 values synced:** Mass, Volume, Friction, Karma, Luck, Persuasion, Heat, SleightOfHand, Nerve — all present in C++ sync, Lua read, and NET broadcast.
- [ ] **Live updates:** F1 panel changes in C++ propagate to Lua `AttractionConstants.modifiers` every frame. NET broadcasts changes to remote clients.

### Error Handling & Security
- [ ] **No raw pointers:** C++ uses `unique_ptr`, `vector`, `map`. No `new`/`delete`.
- [ ] **RPC authentication:** Every NET RPC includes a session token. Rate-limited to 10/sec per client.
- [ ] **Server-authoritative economy:** `Engine.AwardTickets/Tokens` is server-only. Client cannot modify economy state.
- [ ] **Death's Door:** Server-authoritative. Client cannot trigger or resolve death.

### CRITICAL ARCHITECTURAL BOUNDARY: ENGINE VS. ATTRACTIONS
1. **The C++ Domain:** STRICTLY reserved for generic engine systems (Graphics, Audio, Core Jolt Physics world, Sol2 bindings). The C++ engine MUST NOT contain game-specific logic or hardcoded classes for individual games (e.g., no `SkeeballCollider` or `GreedGutter` in C++).
2. **The Lua Domain:** ALL individual midway attractions and game-specific mechanics MUST be written in pure Lua. If an attraction requires physics, the Lua script MUST utilize the generic Sol2 bridges exposed by the C++ engine (e.g., `MidwayPhysics.SpawnStaticBox`).
3. **Task Decomposition:** The Director MUST NOT create [C++] tasks for specific games.
