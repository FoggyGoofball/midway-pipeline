# Mechanics Scaffold — Design Doc

> Saved from planning session, 2026-09-17.
> Premise: **the coder fails because it invents game logic and API usage simultaneously.**
> If we first ask the model to *reason* each mechanic in bounded, annotated pseudocode and
> map it to a specific approved API — then hand that scaffold to the coder as a translation
> template — the hardest step becomes "fill in a known shape" instead of "invent from blank."
> Goals: reduce the pervasively-incoherent Lua the coder currently produces (Spawn* first-arg
> misuse, phantom globals, undeclared variables, duplicate registrations) by moving the
> *reasoning* out of the code-generation step and into a separate, checkable artifact.

---

## 1. Current state (what exists vs. what this adds)

| Piece | State | Gap this doc fills |
|---|---|---|
| `mesh_architect.py` + `structured_schemas.AttractionDesignOutput` | ✅ produces `handles`, `event_flow` (`{trigger, action}` edges), `feature_checklist`, `module_state_variables`, `pool_requirements` | The `event_flow` edges are **one-line trigger→action strings** — structure, not mechanics. Nothing captures *how* each mechanic works or *which exact API* realizes it. |
| `_helpers_exec.py::execute_task` (anchor mode) | ✅ per-anchor SEARCH/REPLACE, staging block, anchor guard | The coder gets the hook + skeleton and must **invent** the logic *and* the correct API in one shot. |
| `contract_validator.py` / `midway_api_signatures.py` | ✅ deterministic approved-API list + arity tables | Ready to validate the scaffold's API column — nothing uses them *before* code is written. |
| `_post_process_lua.py` (22+ deterministic fixes) | ✅ repairs local syntax/phantom corruptions | Repairing *after* generation is whack-a-mole; this doc prevents the corruption *before* generation. |
| `docs/SPEC_TRACE_GATE_PLAN.md` | 📄 planned trace/analysis stage | The scaffold **is** a requirement→build-unit→reference trace — the two docs compose (see §9). |

**What does not exist yet:** an intermediate artifact between the Architect JSON and the
per-anchor code that reasons about each mechanic and binds it to an exact API signature.

---

## 2. The failure this addresses (verified in the strongman run)

The suspended run's `strongman.lua` shows the coder doing both jobs at once and failing at
both:

| Corruption | Why it happened |
|---|---|
| `SpawnStaticBox(platform_sensor, …)` — handle as first arg | The coder doesn't know Spawn* takes world coords and *returns* a handle; it guessed at generation time. |
| `rand()` (phantom global) | It wrote logic in a language-neutral style, not Lua (`math.random`). |
| `friction_mult`, `bell_offset` undeclared | It referenced state it never declared — a design-level omission, not a syntax slip. |
| `if 1.0 > 0.0 then` tautology | It emitted a placeholder when it didn't actually know the mechanic. |
| Two `MidwayPhysics.OnStep` registrations | It never decided *where* the game loop lives. |

Every one of these is a **design decision**, not a typo. The coder made them inline, under
the pressure of producing valid Lua, and got them wrong. A scaffold moves those decisions
to a step where the model reasons *without* the syntax burden.

---

## 3. The scaffold artifact (bounded, structured, checkable)

One scaffold per anchor task. Strict format, token-capped, and machine-checkable:

```
### Mechanic: <name>
INTENT: <one sentence — what and why>
PSEUDO:
  <3-12 lines of language-neutral pseudocode>
API:
  <one approved call per line, exact signature>
```

Example (Bell's Curse):

```
### Mechanic: Bell's Curse
INTENT: between swings the bell shifts vertically by a luck-scaled offset
PSEUDO:
  if swing_complete then
    offset = luck * SHIFT_MAX
    bell.y += offset
  end
API:
  MoveKinematic(bell_handle, lx, ly, lz, dt)   -- lx,ly,lz are numbers; handle is a RETURN, never a first arg
```

**Rules that make it safe (anti-monologue):**

1. `INTENT` is **one sentence**, not a paragraph.
2. `PSEUDO` is ≤ 12 lines, and must use **only** approved API names (checked against
   `contract_validator`'s surface) or plain words — never invented API.
3. `API:` lists the **exact** `MidwayPhysics.X` / `Engine.X` call(s) with the real signature,
   copied from the bridge contract. This is the key: the coder never has to *recall* a
   signature; it translates the scaffold's API line verbatim.
4. The whole scaffold is token-capped so it can never grow into a prose essay.

---

## 4. Where it fits in the pipeline

```
Clarification Gate → Blueprint → Architect JSON (structure)
                                    ↓
                        MECHANICS SCAFFOLD (this doc)   ← NEW
                          per anchor: INTENT + PSEUDO + API
                                    ↓
                    deterministic API validation (contract_validator)
                                    ↓
                        Anchor coder: SEARCH/REPLACE
                          ("translate this scaffold into Lua at the hook")
                                    ↓
                        review loop / tribunal / post-process (unchanged)
```

The coder's prompt changes from *"implement TASK_N at this hook"* to *"render the following
scaffold as Lua at this hook, using the exact API signatures given."* That is a translation
task, not an invention task.

---

## 5. Deterministic validation (before any code)

The scaffold is cheap to validate, and failures are cheaper than failed code:

1. Every line in `API:` and `PSEUDO` referencing a `MidwayPhysics.*` / `Engine.*` name must
   exist in the bridge contract (reuse `contract_validator.build_lua_contract`).
2. `Spawn*` / `Pool*` first-argument sanity: a scaffold `API:` line must never show a handle
   as a Spawn/Pool first arg (static regex, same rule as the post-processor).
3. Every `INTENT` maps to ≥1 `feature_checklist` entry (traceability — see §9).
4. No scaffold mentions an API not in `midway_api_signatures` (arity source of truth).

A scaffold that fails validation is re-prompted **once** (bounded), not fed to the coder.

---

## 6. Prompt design (bounded reasoning, no monologue)

The scaffold is generated by the **reasoning model** (qwen3.5:9b), which is already resident
for the Architect JSON — no extra model reload. The prompt:

- Fixes the exact 3-field format (§3) with a worked example.
- Includes the full approved-API list (the same `approved_names_hint` the reviewer already
  gets), so the model copies signatures instead of recalling them.
- Caps output (e.g. `num_predict=1024`) and rejects prose-only responses.
- Instructs: *"Reason in PSEUDO, bind in API, then stop. Do not write Lua."* — separating
  reasoning from code is the whole point.

This is deliberately **not** the free-form "reasoned and annotated" essay that historically
triggered comment monologues; it is a constrained template with a hard stop.

---

## 7. Implementation phases

### Phase 0 — Schema
`MechanicScaffold {name, intent, pseudo, api_calls: [str]}` in `models.py`; one per anchor
task, carried on the design (or a parallel `ctx.scaffolds` map).

### Phase 1 — Generator prompt + validator
- New `_build_scaffold_prompt(design, approved_apis)` in `mesh_architect.py` or a sibling.
- `_validate_scaffold(scaffold, contract)` — rules in §5; re-prompt once on failure.

### Phase 2 — Coder integration
- In `_helpers_exec.py::execute_task` anchor path: inject the task's scaffold (INTENT/PSEUDO/API)
  into the staging block, and rewrite the mandate to "translate this scaffold."
- The coder's SEARCH/REPLACE is unchanged — only its *source of truth* changes.

### Phase 3 — Measurement
Compare `luac-clean first-attempt rate` and `review cycles to converge` with and without the
scaffold (behind `MIDWAY_MECHANICS_SCAFFOLD=1`, default off until measured).

---

## 8. Failure modes & mitigations

| Risk | Mitigation |
|---|---|
| Free-form reasoning → comment monologue | 3-field template, token cap, "do not write Lua" hard stop |
| Scaffold invents API names | `API:` column validated against the contract before use |
| Coder ignores the scaffold and invents anyway | The staging block puts the scaffold directly above the hook; the API rule is reinforced in the coder mandate |
| Extra LLM call adds latency | Reuses the resident reasoning model; runs once per task, before the (already slow) coder call |
| Scaffold itself is wrong | The tribunal/reviewer can now judge the *scaffold* (cheap) instead of only the *code* (expensive) |

---

## 9. Relationship to the other planning docs

- **`SPEC_TRACE_GATE_PLAN.md`** — TraceGate scores *sufficiency* (is each requirement buildable?)
  and *consistency* (do requirements contradict?). The scaffold is the artifact that makes a
  requirement **buildable**: `INTENT` = requirement, `PSEUDO` = build unit, `API:` = reference.
  TraceGate validates the scaffold's sufficiency/coherence; the coder consumes it.
- **`MESH_SIGNAL_ARGUMENTATION_PLAN.md`** — when a scaffold is underspecified or
  self-contradictory, the agent emits `AMBIGUITY` → TraceGate, instead of guessing. The scaffold
  gives agents something concrete to object *to*.
- **`CARTRIDGE_WIZARD_PLAN.md`** — the scaffold's API column is exactly the machine-readable
  API knowledge the cartridge should carry; a future cartridge exports the approved signatures
  that scaffold validation reads.

---

## 10. Open questions

1. **Scope of the API column** — full signature per call, or name+arity only? (Leaner is safer
   against hallucination; full signature is more prescriptive.)
2. **Where the scaffold lives** — on the design object, or a separate `ctx.scaffolds` map keyed
   by anchor? (Separate map keeps the Architect JSON stable.)
3. **Does the coder see the scaffold *and* the hook, or only the scaffold?** (Recommend both:
   the hook is where the code goes; the scaffold says what to write.)
4. **Is the scaffold worth it for structural tasks** (OnLoadStatic geometry) that have no real
   "logic"? (Recommend: scaffold only for logic-bearing hooks; geometry hooks skip it.)
