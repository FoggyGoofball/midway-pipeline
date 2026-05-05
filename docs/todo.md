# Midway to Nowhere — Development TODO

> Prioritized action items for continuing development, mapped to GDD v19 sections.
> Generated: 2026-04-28

---

## Legend
| Icon | Meaning |
|------|---------|
| 🟢 P0 | Core blocker — must do before anything else |
| 🟡 P1 | High priority — needed for Phase 2/3 milestone |
| 🟠 P2 | Medium priority — Phase 3/4 content |
| 🔴 P3 | Low priority — stretch goals, polish |

---

## Phase 2 Priority Items

### 🟢 P0 — Economy & Currency System (GDD §3)

| # | Item | Status | Description | Files to touch |
|---|---|---|---|---|
| 1 | Implement Soul Tokens | ✅ Done | Player currency tracked at engine level | `src/EconomyManager.h/cpp` |
| 2 | Implement Tickets | ✅ Done | Secondary currency earned from games | `src/EconomyManager.h/cpp` |
| 3 | Wire ticket rewards into games | ✅ Done | Plinko/Coin Cascade award tickets via `Engine.AwardTickets` | `attractions/plinko/plinko.lua`, `attractions/coin_cascade/coin_cascade.lua` |
| 4 | Win notification banner | ✅ Done | Centred fading "YOU WON X TICKETS" overlay | `src/DevConsole.cpp` |
| 5 | Save economy state | ✅ Done | Persists tokens/tickets/streak to `economy_state.json` | `src/EconomyManager.cpp` |
| 6 | Streak Protocol ("Let It Ride") | ⬜ Next | Consecutive-play multiplier tracking + wager flow | `src/EconomyManager.cpp` |

### 🟢 P0 — Win State & UX Feedback (GDD §8)

| # | Item | Status | Description | Files to touch |
|---|---|---|---|---|
| 6 | Show ticket count on screen | ✅ Done | Economy section in dev console | `src/DevConsole.cpp` |
| 7 | Banner notification on win | ✅ Done | Centred fading overlay with label + amount | `src/DevConsole.cpp` |
| 8 | Booth activation feedback | ⬜ Open | Visual/audio cue when SPACE activates a booth | `src/Engine.cpp` |
| 9 | Button highlight on proximity | ⬜ Open | Blink/pulse the green wireframe box | `src/DebugRenderer.cpp` |

### 🟡 P1 — Prize & Augment Loading (GDD §5, Appendix A)

| # | Item | Description | Files to touch |
|---|---|---|---|
| 10 | Load prizes from JSON | Parse `assets/data/prizes.json` into runtime data | New `src/PrizeManager.h/cpp` |
| 11 | Load augments from JSON | Parse `assets/data/augments.json` | New `src/AugmentManager.h/cpp` |
| 12 | Implement bribery | Pay Soul Tokens to override a game outcome | `src/Economy.cpp`, `src/AttractionManager.cpp` |
| 13 | Implement prize display cabinet | Show prize pool in-game (not just HUD) | New render pass |
| 14 | Hilux effect | Won prizes leave the pool | `src/PrizeManager.cpp` |

### 🟡 P1 — Modifier Consequences (GDD §4)

| # | Item | Description | Files to touch |
|---|---|---|---|
| 15 | Karma → gameplay consequences | Luck gets added to streak; Karma thresholds trigger events | `src/Engine.cpp`, game scripts |
| 16 | Heat → tangible risk | Heat above threshold changes game states / modifies odds | `attractions/plinko/plinko.lua`, `attractions/coin_cascade/coin_cascade.lua` |
| 17 | Persuasion → bribe cost modifier | Lower/higher persuasion affects bribe prices | `src/Economy.cpp` |
| 18 | Nerve → walking away penalty | Low nerve increases penalty for leaving mid-game | `src/AttractionManager.cpp` |
| 19 | Volume → scale/chaos | High volume = larger physics scale or more objects | game scripts |

### 🟡 P1 — Dialogue & Barker System (GDD §15)

| # | Item | Description | Files to touch |
|---|---|---|---|
| 20 | Billboarding system | Render Barker sprites as camera-facing quads | New `src/BarkerRenderer.h/cpp` |
| 21 | Populate dialogue.json | Reactive dialogue matrix per GDD §15 | `assets/data/dialogue.json` |
| 22 | Trigger dialogue on events | Player enters, wins, loses, streak, karma shift | `src/AttractionManager.cpp`, `src/Engine.cpp` |
| 23 | World-placed barker | Barker entity at entrance near stalls | `src/Engine.cpp` |

---

## Phase 3 Priority Items

### 🟠 P2 — Audio Engine (GDD §2.1)

| # | Item | Description | Files to touch |
|---|---|---|---|
| 24 | Integrate SoLoud | Add `soloud` to vcpkg.json, init audio engine | `vcpkg.json`, new `src/AudioManager.h/cpp` |
| 25 | Environment ambience | Purgatorial carnival background loop | `assets/audio/environment/` |
| 26 | Machine SFX | Coin drops, peg hits, slot bells | `attractions/coin_cascade/`, `attractions/plinko/` |
| 27 | 3D positional audio | Sound at booth locations attenuates with distance | `src/AudioManager.cpp` |

### 🟠 P2 — Visual Effects (GDD §2.3, §12)

| # | Item | Description | Files to touch |
|---|---|---|---|
| 28 | PS1 vertex snapping shader | Affine texture warping, vertex position rounding | `src/DebugRenderer.cpp`, new shader files |
| 29 | Demonic Skew | Vertex displacement based on Karma | New shader uniform, `src/DebugRenderer.cpp` |
| 30 | Karmic-Temporal Transmutation Matrix | Full-screen post-processing | New post-process pass |
| 31 | Dithering effect | 16-bit colour dithering post-process | New fragment shader |
| 32 | Pixelation effect | Low-res render target upscaled | New framebuffer pipeline |

### 🟠 P2 — Save System (GDD §6.1)

| # | Item | Description | Files to touch |
|---|---|---|---|
| 33 | Save/load game state | Player inventory, modifiers, economy, progress | New `src/SaveManager.h/cpp` |
| 34 | Maintenance Alley | Save station + loading screen | `src/Engine.cpp` |
| 35 | Park Bench | Mid-session save spot at Z=0 | Corridor spawn point |

### 🟠 P2 — Meta-Progression & Baggage (GDD §4.4, §6.3)

| # | Item | Description | Files to touch |
|---|---|---|---|
| 36 | Populate Emotional Baggage data | `assets/data/baggage/` with JSON entries | `assets/data/baggage/` |
| 37 | Baggage inventory system | Player can carry/view emotional baggage | New `src/BaggageManager.h/cpp` |
| 38 | Lost & Found hub | Where baggage is resolved | Origin Cluster |
| 39 | Emotional modifiers per baggage | Each baggage type changes modifier baseline | `src/ModifierState.h` |

### 🟠 P2 — Barker's Regalia & Endgame (GDD §11)

| # | Item | Description | Files to touch |
|---|---|---|---|
| 40 | Death's Door state | Threshold state before final run | `src/Engine.cpp`, `src/ModifierState.h` |
| 41 | The Architect's Run | Final run gauntlet | New game mode |
| 42 | The Barker's Regalia | Collecting pieces, unlocking endgame | `src/Economy.cpp`, `src/AttractionManager.cpp` |

### 🟠 P2 — Attraction Expansions (GDD §9)

| # | Item | Description | Difficulty |
|---|---|---|---|
| 43 | Sunk Costs (Duck Pond) | Circular magnet-fishing pond with Jolt | Medium |
| 44 | The Guilt Trip (Haunted House) | On-rails corridor with scripted triggers | Hard |
| 45 | The Frog Bog | Timing-based lid-popping game | Medium |
| 46 | High Striker | Force meter — test mass/velocity in Jolt | Easy |
| 47 | The Penny Arcade | Multi-game mini-arcade station | Hard |
| 48 | Skeeball | Classic ramp+hole physics | Medium |
| 49 | The Coin Ski Jump | Projectile arc + distance scoring | Easy |

### 🟠 P2 — Concessions & Utility Stalls (GDD §7)

| # | Item | Description | Difficulty |
|---|---|---|---|
| 50 | The Wight & Measure | Karma-driven weight/volume readout | Easy |
| 51 | Just Desserts | Prize redemption counter | Medium |
| 52 | Spirits & Libations | Barker-operated bar (atmosphere) | Medium |

---

## Phase 4 Priority Items

### 🔴 P3 — Boss Encounters (GDD §10)

| # | Item | Description |
|---|---|---|
| 53 | 10 Emotional Baggage bosses | Each unlocks at certain baggage/cycle thresholds |
| 54 | The Geometric Fracture | Final visual/physics transition between phases |
| 55 | Boss arena system | Dedicated encounter space (Origin Cluster) |

### 🔴 P3 — Origin Cluster (GDD §1)

| # | Item | Description |
|---|---|---|
| 56 | The Lost & Found | Central baggage resolution hub |
| 57 | Grand Prize Pavilion | Showcase rare/exotic prizes |
| 58 | Swami Samsara | Fortune-telling modifier preview |

### 🔴 P3 — Box2D Integration (GDD §2.1)

| # | Item | Description |
|---|---|---|
| 59 | Add Box2D dependency | `box2d` to vcpkg.json |
| 60 | 2D planar attractions API | Bridge functions for 2D attractions |
| 61 | Port Duck Pond/Penny Arcade to Box2D | 2D physics for specific games |

### 🔴 P3 — Networking & Multiplayer (GDD §2.2)

| # | Item | Description |
|---|---|---|
| 62 | Async leaderboard | Upload high scores (stretch) |
| 63 | Spectator mode | Other players can watch (stretch) |

---

## Known Technical Debt / Issues

| # | Issue | Priority | Description |
|---|---|---|---|
| TD1 | No visual booth geometry seen | 🟢 P0 | `defaultbooth.glb` renders invisible — check scale, shader, or model issues |
| TD2 | Render pipeline: bodies visible only in certain frames | 🟡 P1 | Frame‑inconsistent rendering — investigate `CollectLiveBodyIDs` vs render order |
| TD3 | Midway length mismatch | 🟡 P1 | GDD says 200m, code uses `MIDWAY_LENGTH=180m` — align with GDD or update doc |
| TD4 | init_attractions_notes.lua in root | 🟠 P2 | Scratch notes file left in root — clean up before release |
| TD5 | Log file accumulation | 🟠 P2 | `bridge_log.txt`, `render_log.txt`, `midway.log` grow unbounded — add rotation or CLI flag |
| TD6 | No unit tests | 🟠 P2 | No automated testing for physics, bridge, or economy systems |
| TD7 | imgui.ini in root | 🟠 P2 | ImGui layout config leaked — should be in `assets/` or ignored |

---

## Quick-Start: Next 5 Things to Build

1. **Booth shell visible** — diagnose `defaultbooth.glb` rendering invisible (TD2, P0)
2. **Streak Protocol** — "Let It Ride" wager flow wired to economy (P0)
3. **Prize JSON Loading** — parse `assets/data/prizes.json` into runtime prize pool (P1)
4. **Barker Billboard** — get Barker sprites rendering as camera-facing quads (P1)
5. **Win notification polish** — sound cue + colour flash on ticket win (P1)
