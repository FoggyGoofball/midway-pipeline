# Signal Tags Reference — LoRA Generator Source of Truth

> Single source of truth for **every structured signal** the pipeline emits or
> parses, categorized for direct consumption by the LoRA dataset generators in
> `lora generator/`. Each entry gives: **syntax**, **semantics**, **dispatcher
> behavior**, and a **ChatML dataset sample** the generator should synthesize.
>
> Companion docs:
> - `docs/MESH_SIGNAL_ARGUMENTATION_PLAN.md` — the communication layer (how agents argue).
> - `docs/CHAT_PERSONA_SIGNALS_PLAN.md` — chat/planning escalation on top of the same tags.
> - `docs/SPEC_TRACE_GATE_PLAN.md` — the stage that *consumes* `AMBIGUITY`.
> - `docs/LORA_GENERATION_DESIGN.md` — how these tags become training data.

---

## Part A — Inter-agent mesh signals (bracket tags)

These are parsed by `signals.py::SIGNAL_PATTERNS` + `extract_signals()` and, where
noted, dispatched by `mesh_tasks.py::_process_task_signals()`. They are the
personas' way of talking to one another inside the mesh.

| # | Tag | Arity | Emitter (typical) | Dispatcher |
|---|---|---|---|---|
| 1 | `QUERY` | `[QUERY:Target:Question]` | any agent | spawns a query task, records `pending_queries` |
| 2 | `DELEGATE` | `[DELEGATE:Target:Task]` | director/architect | spawns a sub-task (capped by `MAX_SUBTASKS_PER_AGENT`) |
| 3 | `VETO` | `[VETO:Target:Justification]` | reviewer | records `all_vetos`, bumps retry count for target |
| 4 | `OBJECT` | `[OBJECT:Target:Justification]` | reviewer/peer | records `all_objects` |
| 5 | `RECOURSE` | `[RECOURSE:Target:ProposedResolution]` | any agent | pass — arbitrated in consensus phase |
| 6 | `CONSULT` | `[CONSULT:Target:TechnicalQuestion]` | coder | spawns a consult query task, records `pending_queries` |
| 7 | `APPROVE` | `[APPROVE]` | any agent | sets `all_approvals_dict[agent] = True` |
| 8 | `RESULT` | `[RESULT:Output]` | any agent | completion marker (parser-only) |
| 9 | `REVISE` | `[REVISE:Target:Reason]` | reviewer | spawns a revision task for the target |
| 10 | `FLUSH` | `[FLUSH]` | any agent | context-flush marker (parser-only) |
| 11 | `APPEAL` | `[APPEAL:Target:Defense]` | coder | records `pending_appeals`, spawns a blind `TRIBUNAL` task |
| 12 | `MERGE` | `[MERGE:Target:Justification]` | tribunal | records `tribunal_verdicts`, clears the matching veto |
| 13 | `REJECT` | `[REJECT:Target:Justification]` | tribunal | records `tribunal_verdicts` |
| 14 | `REQUEST_API` | `[REQUEST_API: name \| https://url]` | coder/doc | fetches an external API doc (parser-only) |
| 15 | `AST_PATCH` | `[AST_PATCH:path]```code```[/AST_PATCH]` | coder/fixer | applied by `_finalize_review.py` (Phase III patch) |
| 16 | `AMBIGUITY` | `[AMBIGUITY:Target:Issue]` | any agent | records `ctx.ambiguity_signals` (record-only → TraceGate) |

Deprecated / removed from the active parser (documented for completeness only):
`FETCH`, `READ_OFFLOADED`, `EXTRACT_SKELETON`, `MATH_EVAL` — superseded by the
paging protocol in Part B.1.

### A.1 `QUERY` — ask another agent for information

- **Syntax:** `[QUERY:<domain-key>:<question>]`
- **Semantics:** request information from another persona (e.g. the Librarian or DOC).
- **Dispatcher:** resolves the target via `resolve_agent_name()`, enqueues an
  `is_query` task, and records the parent in `ctx.pending_queries` so the answer
  is folded back into the parent task's context.
- **LoRA sample:**

```json
{"messages": [
  {"role": "system", "content": "You are the Lua coder for 'Midway to Nowhere'. Use the MESH COMMUNICATION PROTOCOL to talk to other agents."},
  {"role": "user", "content": "Implement the mallet strike. You need the exact SpawnDynamicCapsule signature."},
  {"role": "assistant", "content": "[QUERY:DOC:What is the exact signature of MidwayPhysics.SpawnDynamicCapsule and its arity?]"}
]}
```

### A.2 `DELEGATE` — hand off a sub-task

- **Syntax:** `[DELEGATE:<domain-key>:<task-spec>]`
- **Semantics:** split a sub-task to another domain (e.g. Lua → PHYS).
- **Dispatcher:** enqueues a `sub_*` task if the parent has fewer than
  `MAX_SUBTASKS_PER_AGENT` children.

```json
{"messages": [
  {"role": "system", "content": "You are the Director. Decompose work with [DELEGATE:...]."},
  {"role": "user", "content": "The attraction needs a networked scoreboard."},
  {"role": "assistant", "content": "[DELEGATE:OBSERVABILITY:Instrument the scoreboard event flow with the canonical telemetry API.]"}
]}
```

### A.3 `VETO` — hard block another agent's output

- **Syntax:** `[VETO:<target>:<justification>]`
- **Semantics:** reviewer-side hard rejection.
- **Dispatcher:** appends to `ctx.all_vetos` and increments the target's retry
  counter (circuit-breaker input).

```json
{"messages": [
  {"role": "system", "content": "You are the Reviewer. Veto output that violates the engine contract."},
  {"role": "user", "content": "The coder wrote `SpawnDynamicBall` (phantom API). Review it."},
  {"role": "assistant", "content": "[VETO:Lua:SpawnDynamicBall is a phantom API; use SpawnDynamicSphere(lx, ly, lz, radius).]"}
]}
```

### A.4 `OBJECT` — non-fatal objection

- **Syntax:** `[OBJECT:<target>:<justification>]`
- **Semantics:** raise a concern without forcing a re-do.
- **Dispatcher:** appends to `ctx.all_objects` (surfaced in review/consensus).

```json
{"messages": [
  {"role": "system", "content": "You are the Reviewer. Object to aspects you disagree with."},
  {"role": "user", "content": "The coder cached modifiers at load time. Review."},
  {"role": "assistant", "content": "[OBJECT:Lua:Modifiers must be read each frame in OnStep, not cached in OnLoad.]"}
]}
```

### A.5 `RECOURSE` — escalate an unresolved disagreement

- **Syntax:** `[RECOURSE:<target>:<proposed-resolution>]`
- **Semantics:** escalation of a standoff to the consensus/tribunal phase.
- **Dispatcher:** no immediate action — consumed in the consensus phase.

```json
{"messages": [
  {"role": "system", "content": "You are the Director. Escalate unresolved disputes."},
  {"role": "user", "content": "Lua and PHYS disagree on the impulse API."},
  {"role": "assistant", "content": "[RECOURSE:TRIBUNAL:Resolve the ApplyImpulse arity dispute between Lua and PHYS.]"}
]}
```

### A.6 `CONSULT` — technical clarification

- **Syntax:** `[CONSULT:<target>:<question>]`
- **Semantics:** ask a focused technical question (lighter than QUERY).
- **Dispatcher:** enqueues an `is_query` consult task; answer folds back via
  `pending_queries`.

```json
{"messages": [
  {"role": "system", "content": "You are the Lua coder. Consult the DOC agent for API certainty."},
  {"role": "user", "content": "Does IsSensorTriggered fire once or continuously while overlapping?"},
  {"role": "assistant", "content": "[CONSULT:DOC:Does IsSensorTriggered(handle) fire on enter only, or every frame while overlapping?]"}
]}
```

### A.7 `APPROVE` — approval marker

- **Syntax:** `[APPROVE]` (no colon-args)
- **Semantics:** sign off on the current direction.
- **Dispatcher:** sets `ctx.all_approvals_dict[agent] = True`.

```json
{"messages": [
  {"role": "system", "content": "You are the Reviewer. Emit [APPROVE] when output is correct."},
  {"role": "user", "content": "Review the OnLoad implementation."},
  {"role": "assistant", "content": "The implementation is correct and complete. [APPROVE]"}
]}
```

### A.8 `RESULT` — completion marker

- **Syntax:** `[RESULT:<output-summary>]`
- **Semantics:** declare work complete; the content is the deliverable summary.
- **Dispatcher:** parser-only in `extract_signals()` (used as an end-of-output marker).

```json
{"messages": [
  {"role": "system", "content": "You are the Lua coder. End completed work with [RESULT:...]."},
  {"role": "user", "content": "Write the OnLoad function."},
  {"role": "assistant", "content": "function OnLoad() ... end\n[RESULT:OnLoad spawns the ball and registers OnStep]"}
]}
```

### A.9 `REVISE` — request a revision

- **Syntax:** `[REVISE:<target>:<reason>]`
- **Semantics:** reviewer asks a specific agent to revise.
- **Dispatcher:** enqueues a `revise_*` task for the target.

```json
{"messages": [
  {"role": "system", "content": "You are the Reviewer. Request revisions with [REVISE:...]."},
  {"role": "user", "content": "The scoring callback awards tickets before the sensor confirm."},
  {"role": "assistant", "content": "[REVISE:Lua:Award tickets only after IsSensorTriggered confirms the bell hit.]"}
]}
```

### A.10 `FLUSH` — context-flush marker

- **Syntax:** `[FLUSH]`
- **Semantics:** signal that the agent is discarding stale context.
- **Dispatcher:** parser-only marker.

```json
{"messages": [
  {"role": "system", "content": "You are an agent. Emit [FLUSH] when discarding stale context."},
  {"role": "user", "content": "Context changed; rebuild your answer."},
  {"role": "assistant", "content": "[FLUSH]\nRebuilt: ..."}
]}
```

### A.11 `APPEAL` — defend against a veto

- **Syntax:** `[APPEAL:<target>:<defense>]`
- **Semantics:** coder counters a `VETO` it believes is wrong.
- **Dispatcher:** matches the most recent veto, appends to `ctx.pending_appeals`,
  and spawns a blind `TRIBUNAL` task.

```json
{"messages": [
  {"role": "system", "content": "You are the Lua coder. Appeal a VETO you believe is wrong."},
  {"role": "user", "content": "The Reviewer vetoed your 3-arg ApplyImpulse call."},
  {"role": "assistant", "content": "[APPEAL:Reviewer:ApplyImpulse is documented with a 3-arg overload in the bridge contract; the veto misreads the arity table.]"}
]}
```

### A.12 / A.13 `MERGE` / `REJECT` — binding tribunal verdicts

- **Syntax:** `[MERGE:<target>:<justification>]` / `[REJECT:<target>:<justification>]`
- **Semantics:** the blind TRIBUNAL's binding decision on an appeal.
- **Dispatcher:** `MERGE` records the verdict and clears the matched veto;
  `REJECT` records the verdict (appeal denied).

```json
{"messages": [
  {"role": "system", "content": "You are the TRIBUNAL. Issue a binding [MERGE:...] or [REJECT:...] verdict."},
  {"role": "user", "content": "Appeal: ApplyImpulse 3-arg vs 4-arg. Coder defense: bridge contract lists a 3-arg overload."},
  {"role": "assistant", "content": "[MERGE:Tribunal:The bridge contract does list a 3-arg ApplyImpulse overload; the veto is overturned.]"}
]}
```

### A.14 `REQUEST_API` — fetch an external API doc

- **Syntax:** `[REQUEST_API: <name> | https://<url>]`
- **Semantics:** request the orchestrator to pull an external reference (e.g. a
  Jolt/SDL header) into context.
- **Dispatcher:** parser-only in `extract_signals()` (pattern captures name + URL).

```json
{"messages": [
  {"role": "system", "content": "You are the C++ coder. Request missing API docs with [REQUEST_API: ...]."},
  {"role": "user", "content": "You need the Jolt body-interface reference."},
  {"role": "assistant", "content": "[REQUEST_API: Jolt BodyInterface | https://jrouwe.github.io/JoltPhysics/]"}
]}
```

### A.15 `AST_PATCH` — structured code patch (Phase III)

- **Syntax:**
  ```
  [AST_PATCH:path/to/file]
  ```lang
  <replacement code>
  ```
  [/AST_PATCH]
  ```
- **Semantics:** a discrete AST modification chunk, distinct from a full
  SEARCH/REPLACE block.
- **Dispatcher:** extracted and validated in `_finalize_review.py` (lines ~2532+),
  then applied.

```json
{"messages": [
  {"role": "system", "content": "You are the fixer. Emit targeted patches as [AST_PATCH:path]```...```[/AST_PATCH]."},
  {"role": "user", "content": "Fix the mallet scope error in OnUnload."},
  {"role": "assistant", "content": "[AST_PATCH:attractions/strongman/strongman.lua]\n```lua\n-- hoisted to OnLoad scope\nlocal mallet = nil\n```\n[/AST_PATCH]"}
]}
```

### A.16 `AMBIGUITY` — flag an underspecified / contradictory spec

- **Syntax:** `[AMBIGUITY:<target>:<what-is-underspecified-or-contradictory>]`
- **Semantics:** the agent cannot proceed correctly without clarification.
- **Dispatcher:** record-only append to `ctx.ambiguity_signals`
  (`from`, `target`, `issue`, `task_id`, `task_spec`); no forced re-prompt. The
  TraceGate stage (`SPEC_TRACE_GATE_PLAN.md`) consumes it.
- **Two trigger kinds:**
  - **Model-emitted** — requires prompt enumeration + enum + parser + dispatcher
    (the four-point wiring in `MESH_SIGNAL_ARGUMENTATION_PLAN.md`).
  - **Code-detected** — TraceGate finds a dangling ref / numeric conflict itself.

```json
{"messages": [
  {"role": "system", "content": "You are the Director. Flag underspecified task specs with [AMBIGUITY:...]."},
  {"role": "user", "content": "Plan the bell tower: 'the bell rises when struck'."},
  {"role": "assistant", "content": "[AMBIGUITY:User:The bell's max rise height and scoring bands are not specified; cannot define the OnStep physics without them.]"}
]}
```

---

## Part B — Inter-pipeline signals

These are not persona→persona; they are **agent ↔ orchestrator** (and
**iteration ↔ iteration**) control signals.

### B.1 Paging protocol — XML `<invoke_kernel>`

Already implemented as a 1500-point dataset (`lora generator/lora_dataset_generator.py`
→ `paging_lora_dataset.jsonl`). Documented here for completeness with the other
inter-pipeline signals.

| Token | Syntax | Semantics |
|---|---|---|
| `<VRAM_STUB>` | `<VRAM_STUB id="file.md" summary="..."/>` | orchestrator replaces oversized content with a compressed pointer |
| `PAGE_IN` (untargeted) | `<invoke_kernel><action>PAGE_IN</action><target>file.md</target></invoke_kernel>` | fetch a small file's full content |
| `PAGE_IN` (`<lines>`) | `...<target>file.cpp</target><lines>10-50</lines></invoke_kernel>` | fetch a line range |
| `PAGE_IN` (`<search>`) | `...<target>file.cpp</target><search>ClassName</search></invoke_kernel>` | fetch a symbol/section match |
| `PAGE_OUT` | `<invoke_kernel><action>PAGE_OUT</action><target>file.md</target></invoke_kernel>` | evict a previously paged-in block |

**Kernel reply markers** (the "inter-pipeline" side of the conversation):

| Marker | Meaning |
|---|---|
| `[SYSTEM KERNEL: Paging complete. Continue generating your response.]` | successful PAGE_IN |
| `[SYSTEM KERNEL: PAGE_IN REJECTED — <target> exceeds the untargeted Hard Cap...]` | must retry with `<search>`/`<lines>` |
| `[SYSTEM KERNEL: VRAM critical — ...]` | must emit PAGE_OUT before proceeding |
| `[SYSTEM KERNEL: PAGE_OUT completed. '<target>' has been evicted...]` | successful PAGE_OUT |

**Hard-cap rule:** files exceeding 9,000 characters require a `<lines>` or
`<search>` tag; untargeted pages of large files are rejected.

**LoRA sample:**

```json
{"messages": [
  {"role": "system", "content": "You are a Lua developer.\n\n---\nSTRICT XML PAGING PROTOCOL:\n... (as in PROTOCOL_TEXT)\n\n<VRAM_STUB id=\"docs/engine_lua_bridge_contract.md\" summary=\"Spawn + economy APIs\" />"},
  {"role": "user", "content": "Implement the mallet strike using the correct spawn API."},
  {"role": "assistant", "content": "I need the exact spawn signature.\n<invoke_kernel><action>PAGE_IN</action><target>docs/engine_lua_bridge_contract.md</target><search>SpawnDynamicCapsule</search></invoke_kernel>"},
  {"role": "user", "content": "## Paged-In File Content (Search Match)\n**Source:** `docs/engine_lua_bridge_contract.md`\n**Search:** `SpawnDynamicCapsule`\n```\nSpawnDynamicCapsule(lx, ly, lz, radius, half_height)\n```\n\n[SYSTEM KERNEL: Paging complete. Continue generating your response.]"},
  {"role": "assistant", "content": "local hMallet = MidwayPhysics.SpawnDynamicCapsule(0.0, 1.0, 0.0, 0.2, 0.6)"}
]}
```

### B.2 Snapshots — run mirror + cross-iteration carry-forward

Two distinct snapshot mechanisms exist; both are inter-pipeline (orchestrator
state, not persona state):

**B.2.1 `pipeline_snapshot.py::SnapshotManager` — run mirror (`/ .pipeline_snapshots/`)**

| API | Semantics |
|---|---|
| `SnapshotManager(run_id)` | open/create a run snapshot directory |
| `save_originals(paths)` | copy pre-modification originals |
| `save_originals_from_context(file_context)` | parse `### File:` headings and snapshot them |
| `save_proposal(persona, task_id, rel_path, content)` | store an agent's proposed write (artifacts stripped, MD5 hashed) |
| `generate_diff(rel_path)` | unified diff original → proposal |
| `apply_proposals()` / `revert()` | advance or restore originals |
| `manifest.json` | run/task/file change ledger (`files_snapshotted`, `tasks`, `proposals`, `applied`, `reverted`) |

CLI: `python pipeline_snapshot.py list | diff <run_id> [file] | revert <run_id> | advance <run_id>`.

**B.2.2 Cross-iteration carry-forward (`PipelineContext.completed_file_snapshots`)**

- Populated in `pipeline.py`'s blueprint loop before each `reset_state()`:
  every approved on-disk file path is read and stored keyed by relative path.
- Consumed by `mesh_fetches_helpers.py::_build_completed_work_snapshot()` which
  emits a `## ⚠️ COMPLETED WORK — FILES ALREADY ON DISK` block into the
  Director prompt so later iterations **extend, never re-implement**, locked
  symbols (each with a `PAGE_IN <search>SYMBOL</search>` pointer to the full body).
- This is the pipeline's "you already built this — don't rebuild it" signal to
  itself across iterations.

**LoRA sample (Director-side):**

```json
{"messages": [
  {"role": "system", "content": "You are the Director. Iterate on a blueprint without re-implementing completed work."},
  {"role": "user", "content": "## COMPLETED WORK — FILES ALREADY ON DISK\nThe following symbols are DONE AND LOCKED:\n- OnLoad (attractions/strongman/strongman.lua)\nImplement ONLY what is absent."},
  {"role": "assistant", "content": "Understood. OnLoad is locked. Next task: implement the OnStep scoring callback only, extending the existing file."}
]}
```

### B.3 Checkpoints — run-state persistence (`/ .pipeline_checkpoints/`)

| API (`checkpoint.py`) | Semantics |
|---|---|
| `save_checkpoint(checkpoint_id, phase, data)` | persist arbitrary pipeline state as JSON |
| `load_checkpoint(checkpoint_id)` | restore state dict (or None) |
| `list_checkpoints()` | enumerate saved checkpoints |

- Phases: `"director"`, `"mesh_execution"`, and crucially `"BLOCKED"` — a
  blocked run that pauses and waits for a **manual code fix** or `abort`.
- `PipelineContext.load_state()` / `reset_state()` round-trip runtime
  accumulators while preserving static config.
- CLI resume: `python pipeline.py --checkpoint <id> "<fix or prompt>"`.

**LoRA sample (resurrection path):**

```json
{"messages": [
  {"role": "system", "content": "You are the pipeline orchestrator, resuming from a BLOCKED checkpoint."},
  {"role": "user", "content": "Checkpoint 'strongman_v3' is BLOCKED. The user supplied a manual fix. Resume."},
  {"role": "assistant", "content": "Re-hydrated task_map from checkpoint data. Skipping Phases 0.5-3. Resuming from Phases 4-8 with the manual fix applied."}
]}
```

---

## Part C — Planned but not yet implemented

These tags/behaviors are in the plan docs but not yet wired end-to-end. The LoRA
generator should treat them as **target** scenarios, not current behavior.

| Item | Plan doc | Status |
|---|---|---|
| Chat/planning emits `CONSULT`/`QUERY` and escalates to personas | `CHAT_PERSONA_SIGNALS_PLAN.md` | Phase 2 (not built) |
| `AMBIGUITY` routed into TraceGate → `NEEDS_CLARIFICATION` | `SPEC_TRACE_GATE_PLAN.md` | Phase 2 (record-only today) |
| Escalation keywords / council report | `CHAT_PERSONA_SIGNALS_PLAN.md` §3-4 | not built |
| `AST_PATCH` repair round-trip | `_finalize_review.py` | partially live (lines ~2532+) |
| TraceGate deterministic findings (dangling ref, dup def, numeric conflict) | `SPEC_TRACE_GATE_PLAN.md` §4 | not built |

---

## Part D — Dataset generation matrix

How each signal family maps onto a LoRA dataset generator (extending the existing
`lora generator/` project):

| Family | Generator | `train_on_inputs` | Notes |
|---|---|---|---|
| Paging protocol (B.1) | `lora_dataset_generator.py` | **false** (protocol behavior = completion-only) | already 1500 pts, model/cartridge-agnostic |
| Cartridge API knowledge | `cartridge_lora_generator.py` | **true** (API facts must be in the loss) | spawn/phantom/pool/economy scenarios |
| **Inter-agent signals (Part A)** | *new* `signals_lora_generator.py` | **false** | one scenario class per tag; each = emit-parse round-trip |
| **Snapshots + checkpoints (B.2/B.3)** | *new* (extend the above or a small `pipeline_signals_lora_generator.py`) | **false** | Director "don't re-implement" + resurrection dialogues |
| TraceGate findings (Part C) | *deferred* until TraceGate lands | — | emit structured `ITEMS:`/`FINDINGS:` blocks |

Recommended signal-dataset scenario weights (for a 2000-pt
`midway_signals_lora_dataset.jsonl`):

```
QUERY 8%   DELEGATE 6%   VETO 8%    OBJECT 8%   RECOURSE 4%
CONSULT 8% APPROVE 6%   RESULT 6%   REVISE 6%   FLUSH 2%
APPEAL 8%  MERGE 4%     REJECT 4%   REQUEST_API 4%  AST_PATCH 8%
AMBIGUITY 8%   (snapshot/checkpoint dialogues 2%)
```
