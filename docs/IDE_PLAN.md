# Midway Pipeline — IDE Expansion Plan

> Status: **PROPOSAL — for review, not yet implemented.**
> Owner decision required before any code is written.

---

## ⚠️ Clear note: multimodal vision path (known caveat)

The vision chat path (`VISION_MODEL`, default `qwen3.5:9b`) is wired but has one
unresolved edge case:

- The HTTP layer attaches images via `set_chat_images(...)`, and the **chat
  branch** consumes them. If an image-bearing prompt is **misclassified** as a
  build/execute command instead of chat, the pending images are **never
  consumed** and linger for the *next* chat request.

**Fix when we build it out:** add an explicit `vision:` prompt prefix (or a
dedicated `/v1/vision` endpoint) so image requests are routed deterministically
and the pending-image list can never leak across requests. This belongs in the
IDE's *Phase 4 (autonomy control)* work.

---

## 1. Vision

The pipeline stays autonomous — it plans, generates, reviews, and repairs on its
own. The IDE is the **supervisory cockpit**: a human can *watch* everything at a
glance, *reach into* any stage, *edit* any file, and *rewind* any change. The
pipeline is the autopilot; the IDE is the instrument panel **and** the override.

Three pillars, in priority order:

1. **Transparency at a glance** — phase, model, VRAM, errors, and every knob,
   visible without digging.
2. **Total control of every knob** — models, context, sampling, gates, corpus
   capture — editable and persisted.
3. **Full file + change control** — a real file explorer, an editor, and a
   changes tab that can track and revert anything the pipeline (or you) touched.

The "eventual" goals (breakpoint debugging, full IDE features) are Phase 5+ and
are planned but explicitly deferred.

---

## 2. Current state (baseline)

**Frontend** (`web/`): a single-file React 18 + Vite SPA (`App.jsx`) — a prompt
box, a log viewer with telemetry parsing, and start/stop controls. No router, no
state store, no editor.

**Backend** (`pipeline_stream_server.py`): a stdlib `http.server` with:

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/status` | run state + recent logs |
| GET | `/api/logs`, `/api/logfile` | logs |
| GET | `/api/ollama` | Deck reachability probe |
| GET | `/v1/models` | OpenAI model list |
| GET | `/stream` | SSE run stream |
| POST | `/api/run` `/api/stop` `/api/input` `/api/kill` `/api/restart` | run control |
| POST | `/v1/chat/completions` | chat (text + now images) |
| GET | `/` | serves `web/dist` (built SPA) |

**Missing for an IDE:** file tree/read/write, git status/diff/revert, config
get/set, realtime push (currently polling), and any editor surface.

---

## 3. Architecture

Keep the **single Python server** as the backplane and the **React SPA** as the
UI. Do not introduce a second backend or a new framework.

- **Editor:** Monaco (`@monaco-editor/react`) — free, VS Code-grade, Lua/C++/
  Python/Markdown syntax highlighting built in.
- **State:** Zustand store mirroring a richer `/api/state` payload.
- **Realtime:** add SSE (or a tiny WebSocket) for push updates, replacing the
  current 1–2 s polling; keep polling as a fallback.
- **Security:** the server runs arbitrary generated code, so the IDE endpoints
  are **path-sandboxed** (resolve against `PROJECT_ROOT` + pipeline root, reject
  `..`), and nothing exposes raw shell. Knobs are written to a config file, not
  injected as shell.

---

## 4. Phases

### Phase 0 — IDE shell + state model (foundation)
- Replace `App.jsx` with an app shell: left rail (files), center (tabs), bottom
  (logs), right (status/inspector), top (health bar).
- Tab system (file tabs + pipeline tabs), persistent layout.
- Introduce `Zustand` store; type the state.
- **New endpoint:** `GET /api/state` — one aggregate payload: run state, model
  registry, knob inventory, queue, last error, git summary, VRAM.
- Add `@monaco-editor/react`, `zustand`, `react-router` (or lightweight tab state).

### Phase 1 — Transparency at a glance + knobs
- **Health bar:** server up, Deck/Ollama reachable, current phase + elapsed,
  current model + label, VRAM budget, last error (click → error details).
- **Knobs drawer** (the "total control" core):
  - Models: coder / reasoner / arbiter / vision + fallbacks.
  - Context: `num_ctx`, sampling (temperature / top_p / top_k / repeat).
  - Gates: `MIDWAY_MECHANICS_SCAFFOLD`, `MIDWAY_FAILURE_CORPUS`,
    `MIDWAY_FORCED_DETERMINISTIC`, `MIDWAY_PHI35_ORACLES`, `MIDWAY_MEMGPT_RESTORE`,
    `MIDWAY_KEEP_DIRECTOR`, cooldowns.
  - Keys: `GOOGLE_API_KEY`, `OLLAMA_HOST`, `MIDWAY_LUAC_PATH`.
  - **New endpoints:** `GET /api/config`, `PUT /api/config` (write a
    `pipeline.ide.json` overlay; server hot-applies where possible, else flags
    "restart required").
- Live log console with severity filter + search + autoscroll toggle (upgrade of
  the existing one).

### Phase 2 — Full file explorer
- **New endpoints (sandboxed to `PROJECT_ROOT` + pipeline root):**
  - `GET /api/files?path=...` — directory tree.
  - `GET /api/file?path=...` — read.
  - `PUT /api/file?path=...` — write (atomic; luac-validate `.lua` on save).
  - `POST /api/file` — create / delete / rename / move.
- **UI:** tree explorer, open in Monaco with language detection, dirty-state
  badges, save (Ctrl/Cmd+S), close-unsaved prompts.
- Watch `.lua` saves: offer a one-click `luac -p` syntax check + `post_process`
  preview before writing.

### Phase 3 — Changes tab (track + revert)
- **New endpoints:** `GET /api/git/status`, `GET /api/git/diff?path=...`,
  `GET /api/git/log`, `POST /api/git/revert` (checkout a path), `POST /api/git/commit`.
- **UI:** a "Changes" panel listing modified/untracked files, an inline diff
  viewer, per-file **revert** (with confirm), and a **"restore pipeline
  baseline"** action that resets to the last luac-clean snapshot
  (`_last_luac_clean_anchor`) — so you can undo a bad agent edit in one click.
- Track the **pipeline's own mutations** separately from yours: the run already
  records fix deltas (`failure_corpus`); the Changes tab should show *what the
  last run changed* (file diffs per task/fix), not just git state.

### Phase 4 — Autonomy control (orchestrator + human gates)
- Start / stop / kill / restart (exists) **plus**: pause/resume, and a
  **step-through** mode (advance one phase / task at a time).
- **Clarification Gate UI** (exists, but surface it in a modal with the
  VAGUE/SPECIFIC options rendered as buttons).
- **Approval gates (dry-run):** a mode where the pipeline pauses before every
  apply/commit and asks **approve / reject / edit** — the human veto the user
  wants. Backed by a new `MIDWAY_APPROVAL_MODE` knob + `/api/approve` endpoint.
- **Run history timeline:** past runs, verdicts, what changed, one-click "rerun
  with same prompt/knobs".
- **Vision upload UI:** attach an image to a chat prompt (explicit, so it never
  misclassifies — resolves the caveat above).

### Phase 5 — Breakpoint debugging (the eventual goal)
Two distinct flavors, in order of tractability:

1. **Pipeline-stage breakpoints** (high value, do first): pause at named stages —
   after Clarification Gate, after Architect, after each task, before the
   tribunal. Inspect live `ctx` (`all_results_dict`, `pre_flight_errors`,
   `runtime_errors`, task map), then continue/step. Backed by a
   `MIDWAY_DEBUG_PAUSE` flag + a debug event loop in the server; IDE connects
   over SSE/WebSocket.
2. **Lua source breakpoints** (hard, later): breakpoints in a `.lua` script that
   pause the RuntimeSim harness. Requires a Lua `debug.sethook` inside the
   sandbox + a minimal DAP-lite bridge over WebSocket to the IDE editor
   (breakpoint markers, locals pane, step/continue).

---

## 5. Knob inventory (single source of truth to expose)

**Models:** `MIDWAY_CODER_MODEL`, `MIDWAY_REVIEWER_MODEL`, `MIDWAY_ARBITER_MODEL`,
`MIDWAY_VISION_MODEL`, `MIDWAY_SCAFFOLD_MODEL`, fallbacks.
**Context/sampling:** `num_ctx`, temperature/top_p/top_k/repeat (per-role).
**Gates:** `MIDWAY_MECHANICS_SCAFFOLD`, `MIDWAY_FAILURE_CORPUS`,
`MIDWAY_FORCED_DETERMINISTIC`, `MIDWAY_PHI35_ORACLES`, `MIDWAY_MEMGPT_RESTORE`,
`MIDWAY_KEEP_DIRECTOR`.
**Pacing:** `MIDWAY_MODEL_CALL_COOLDOWN`, `MIDWAY_THERMAL_COOLDOWN`.
**External:** `OLLAMA_HOST`, `GOOGLE_API_KEY`, `MIDWAY_WEB_MODEL`,
`MIDWAY_LUAC_PATH`, `MIDWAY_PROJECT_ROOT`.

These currently live as `os.getenv` reads scattered across modules. Phase 1
consolidates them into `pipeline.ide.json` (gitignored) read at startup, so the
IDE has one place to read *and* write them.

---

## 6. Priority & rough effort

| Phase | Effort | Value | Notes |
|---|---|---|---|
| 0 (shell + state) | M | foundation | blocks everything else |
| 1 (dashboard + knobs) | M | **high** | the "transparency + control" core |
| 2 (file explorer) | M–L | high | the "full file explorer" ask |
| 3 (changes tab) | M | high | the "track + revert" ask |
| 4 (orchestrator + gates) | L | high | human veto, vision upload |
| 5 (breakpoints) | XL | medium | the eventual goal; split pipeline vs Lua |

---

## 7. Open questions for you

1. **Scope of "IDE":** browser-only (current) or also a local desktop shell
   (Electron/Tauri)? Browser-only is the fast path.
2. **Editor:** Monaco (VS Code-grade, heavier) vs CodeMirror (lighter)? I
   recommend Monaco for an IDE feel.
3. **Approval gates:** always-on human veto, or opt-in "dry-run" mode?
4. **Realtime:** SSE (simpler, one-way) vs WebSocket (two-way, needed for
   breakpoints later)? I recommend SSE now, WebSocket in Phase 5.
5. **Do you want me to start Phase 0 + 1 now**, or refine this plan first?

---

## Status of the broader system (context for the IDE)

- Arbiter (`deepseek-r1:7b`) + debate + anonymized web-lookup: ✅ wired, 174 tests pass.
- LoRA training data: coder 9,125 / reasoner 3,764 / arbiter 264 samples,
  committed + pushed; Colab notebook (`midway_lora_train.ipynb`) trains all three.
- Failure corpus: initial vs genuine split, poison-guarded.
- Multimodal vision path: wired, with the caveat above.
