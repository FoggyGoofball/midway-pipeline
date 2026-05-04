# Multiplayer / Networking Rules — Midway to Nowhere
## Review Checklist for `/arch_net`

### Authority Model
- [ ] **Server-authoritative.** All state mutations (score, currency, physics state) must be validated by the server. Client requests are suggestions; server applies or rejects.
- [ ] **Client-side prediction** for player movement. Server reconciliation corrects client state each tick.
- [ ] **No client authority over economy.** `Engine.AwardTickets()`, `Engine.AwardTokens()`, Streak Protocol changes are server-only. Clients can request, server validates and broadcasts.

### Transport & Protocol
- [ ] **UDP transport.** No TCP for real-time state. Use sequenced unreliable for position/velocity, reliable ordered for RPCs and state events.
- [ ] **Delta compression.** Send only changed state per tick. Full state snapshots at infrequent intervals (every 30th tick) for late-joiners and resync.
- [ ] **Bandwidth budget:** Assume Steam Deck / low-end hardware. Cap per-client bandwidth. Prioritize active slot physics over idle slot state.

### Vicious Cycle Synchronization
- [ ] **Deterministic teleport.** The Z=0 seam crossing must produce identical results on all peers. Server broadcasts the lap count and teleport frame. Clients do NOT independently decide when to teleport.
- [ ] **Body re-indexing:** When booths wrap around the seam, the server broadcasts the new booth-to-slot mapping. Clients rebuild their local booth transform cache.
- [ ] **Desync prevention:** If a client's Z position diverges from server by >1.0m, force a full state resync.

### State Replication
- [ ] **Replicate:** Position, velocity, and body type (static/kinematic/dynamic) for all active physics bodies in the player's current + adjacent slots.
- [ ] **Do NOT replicate:** Individual pool slot states. Reconstruct pool state from authoritative body positions. Server broadcasts body creation/destruction events.
- [ ] **Interest management:** Only replicate bodies within the player's `interaction_radius` (14.0m). Bodies outside this range are culled from the update stream.
- [ ] **Modifier state:** Server broadcasts the 9 GDD modifier values (mass, volume, friction, karma, luck, persuasion, heat, sleightOfHand, nerve) as a single compact packet. Clients apply locally.

### RPC Security
- [ ] **Authenticate all RPCs.** Every remote call must include a session token. Reject unauthenticated state mutations.
- [ ] **Rate-limit RPCs.** Maximum 10 RPCs per second per client. Excess dropped silently.
- [ ] **Validate parameters server-side.** Never trust client-provided positions, velocities, or scores. Server re-calculates from authoritative state.
- [ ] **TILT (Sleight of Hand) validation:** The TILT mechanic's impulse magnitude must be clamped server-side. Clients cannot send arbitrary impulse values.

### Lag Compensation
- [ ] **Server-side history rewind.** For hit detection (ballistic attractions, ring toss), server maintains a 100ms history ring buffer of body positions. On hit RPC, rewind to client's timestamp and validate.
- [ ] **Extrapolation tolerance:** Clients extrapolate positions between server updates. If extrapolation exceeds 0.5m error, snap to server position.

### Death & Streak Protocol
- [ ] **Death's Door mini-game** must be server-authoritative. Client cannot trigger or resolve death state independently.
- [ ] **Streak Protocol ("Let It Ride"):** Server tracks streak counter. Client displays but cannot modify. Streak failure (forfeit) is server-enforced.
- [ ] **Parasitic Prizes (Curses):** Assignment is server-authoritative. Client cannot discard curses. Exorcism at Swami Samsara is a server-validated transaction.

### Boss Encounters (Geometric Fracture)
- [ ] **Boss arena transition** is a synchronized event. Server triggers the Geometric Fracture (environment implosion/void/arena rise). All clients play the same sequence.
- [ ] **Boss state** is server-authoritative. Boss HP, phase transitions, and physics interactions are computed server-side. Clients receive visual state only.

### Connection & Session Management
- [ ] **Late-join support:** Full state snapshot on connect. Client receives all booth static geometry, current modifier state, economy state, and active body list.
- [ ] **Disconnect tolerance:** 5-second timeout. On reconnect, server sends delta since last acknowledged tick. If >30 seconds, force full resync.
- [ ] **Save integrity:** Ephemeral save files (Park Bench) are generated server-side. Clients cannot write save data. Prevents save-scumming per GDD §6.1.

### Performance Budget
- [ ] **Tick rate:** 20 Hz server update rate. 60 Hz client-side prediction.
- [ ] **Max players:** 4 per session. Each additional player adds ~5KB/s bandwidth.
- [ ] **Physics sync:** Only sync active slot physics. Idle slot bodies are static snapshots, not per-tick updates.
