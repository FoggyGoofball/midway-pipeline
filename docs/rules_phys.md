# Physics Architecture Review Checklist — Midway to Nowhere
## Unified Jolt Physics Standard Validation

### Engine Selection & Isolation (Unified Jolt Standard)
- [ ] **ALL attractions** — both 3D volumetric (Coin Cascade, Claw Machine, Roulette Chamber, Globe of Death) and 2D planar (Crumbling Façade, Plinko, Slingshot, Bumper Cars) — run inside the **global Jolt Physics world**.
- [ ] **Box2D is fully deprecated.** No Box2D initialization vectors, physics ticks, body creation, or API references anywhere in the codebase.
- [ ] **3D volumetric attractions** use unconstrained Jolt rigid bodies (full 6-DOF).
- [ ] **2D planar attractions** are modeled via **Degree-of-Freedom (DOF) constraints**: Z-translation locked, X/Y rotation locked, Z-only rotation permitted. Bodies remain confined to the XY plane inside the global Jolt world.
- [ ] Single fixed-timestep accumulator (60 Hz) drives all physics. No separate tick rates for different modules.

### Vicious Cycle — Teleportation Stability
- [ ] Teleport trigger: `|playerZ| >= 150.0f`. Check this every physics step.
- [ ] On teleport: set `playerZ = 0.0f`, increment lap counter, broadcast to NET layer.
- [ ] **Body re-indexing**: After teleport, all static booth bodies must be re-indexed relative to new Z=0 origin.
- [ ] **Kinematic bodies** (pushers, crushers, moving platforms) must have their motion paths recalculated after teleport. Do not let them drift across the seam.
- [ ] **Dynamic bodies** crossing the seam must be teleported with the player. Do not leave orphaned bodies at Z > 150.
- [ ] **Sensors** must be re-checked after teleport. A sensor that was triggered pre-teleport may no longer be valid.
- [ ] **BroadPhase optimization**: Call `OptimizeBroadPhase()` after all static bodies are re-indexed post-teleport.

### Booth Lifecycle — Physics Integration
- [ ] `BeginBoothCapture(slotID)` → spawn static geometry → `EndBoothCapture()` → `OptimizeBroadPhase()`.
- [ ] `BeginDynamicCapture(slotID)` → spawn dynamic bodies → `EndDynamicCapture()`.
- [ ] On walk-away: `DestroyBodies()` for the slot's dynamic capture list. Static bodies persist.
- [ ] On booth activation: `ActivateBodies()` for the slot's static bodies.
- [ ] On booth deactivation: `DeactivateBodies()` for the slot's static bodies (zero solver cost, still renderable).

### Body & Handle Management
- [ ] All spawn functions return `int` handles. Handle `0` = invalid. Never expose `JPH::BodyID` or `b2BodyId` to Lua.
- [ ] Handle map uses `std::vector<JPH::BodyID>` with `m_nextHandle` counter. Handle 0 is reserved.
- [ ] `DestroyBody(handle)` must remove from handle map AND physics system. No dangling handles.
- [ ] `IsSensorTriggered(sensorHandle)` checks overlap state from the previous physics step.
- [ ] `GetPosition(handle)` returns local-space position (relative to booth origin). Not world-space.

### Object Pools (Two-Tier: Hot/Cold)
- [ ] `CreatePool(name, hotN, coldN, params)` — hot slots are active bodies, cold slots are parked at Y=-9999.
- [ ] `PoolAcquire(name, lx, ly, lz)` — takes from cold store or reuses a freed hot slot.
- [ ] `PoolReturn(name, handle)` — parks body at Y=-9999, returns handle to hot free stack.
- [ ] `PoolCullBelow(name, yThreshold)` — returns all hot bodies below threshold to cold store.
- [ ] Pool names follow pattern `"{attraction}_{type}_{slotID}"` (e.g., `"plinko_balls_3"`).
- [ ] Pool params include: shape, dimensions, mass, friction, restitution, linear/angular damping.

### Per-Body Property Overrides
- [ ] `SetFriction(handle, f)` — overrides default friction. Range 0.0–1.0.
- [ ] `SetRestitution(handle, r)` — overrides bounciness. Range 0.0–1.0.
- [ ] `SetGravityFactor(handle, f)` — 0.0 = no gravity, 1.0 = normal, negative = reverse.
- [ ] `SetMass(handle, kg)` — overrides mass. Must be > 0.0.
- [ ] `SetLinearDamping(handle, d)` — velocity decay per step.
- [ ] `SetAngularDamping(handle, d)` — rotational velocity decay per step.

### Kinematic Control
- [ ] `MoveKinematic(handle, lx, ly, lz, dt)` — sets target position in local space. Called each step.
- [ ] Kinematic bodies use `JPH::EMotionType::Kinematic`. Do not set velocity directly.
- [ ] Kinematic pushers (e.g., Plinko sweeper arm) must have their motion path recalculated after Vicious Cycle teleport.

### Impulse & Velocity
- [ ] `ApplyImpulse(handle, ix, iy, iz)` — instantaneous force in local space.
- [ ] `ApplyAngularImpulse(handle, ix, iy, iz)` — instantaneous torque.
- [ ] `SetLinearVelocity(handle, vx, vy, vz)` — overrides current velocity.
- [ ] `AddLinearVelocity(handle, vx, vy, vz)` — additive velocity change.
- [ ] All velocity/impulse values are in local space. PhysicsManager applies booth rotation internally.

### Collision Layers & Filtering
- [ ] `Layers::NON_MOVING` (0) — static geometry. Collides with MOVING only.
- [ ] `Layers::MOVING` (1) — dynamic/kinematic bodies. Collides with everything.
- [ ] Sensors use MOVING layer. They detect overlap but produce no collision response.
- [ ] `ObjLayerPairFilterImpl`: NON_MOVING ↔ MOVING = collide. MOVING ↔ MOVING = collide.
- [ ] `ObjVsBPFilterImpl`: NON_MOVING bodies only appear in MOVING broadphase layer.

### Coordinate System
- [ ] Local booth space: +X right, +Y up, +Z back. 1 unit = 1 meter.
- [ ] `LocalToWorld(lx, lz)` applies booth offset (m_boothX, m_boothZ) and yaw rotation.
- [ ] Canonical booth dimensions: 9.0 x 9.0 x 15.0 meters.
- [ ] Booth transform is set via `SetBoothTransform(worldX, worldZ, yawDeg)` before any spawn calls.

### Performance
- [ ] Physics culling: suspend simulation for bodies beyond 14.0m interaction radius.
- [ ] `DeactivateBodies()` for off-screen booths. Bodies still exist but cost zero solver time.
- [ ] `OptimizeBroadPhase()` after all static geometry for a batch is loaded.
- [ ] Max active bodies per slot: 200. Pool cold store can exceed this.
- [ ] Physics tick rate: 60 Hz fixed timestep. Use accumulator pattern, not variable timestep.

### Error Handling
- [ ] All spawn functions return 0 on failure. Never crash on invalid parameters.
- [ ] `GetPosition`/`GetVelocity` return `bool` + out params. False = handle not found.
- [ ] `IsActive(handle)` returns false for destroyed or parked bodies.
- [ ] Log errors to `midway.log` with timestamp. Never `assert()` or `abort()` in release builds.
- [ ] Handle map bounds-checked on every access. No out-of-bounds reads.
