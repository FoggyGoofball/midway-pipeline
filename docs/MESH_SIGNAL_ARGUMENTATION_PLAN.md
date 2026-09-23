# Mesh Signal Argumentation — Design Doc

> Saved from planning session, 2026-09-17.
> Premise: **the personas (agents) should be able to argue and reason with one another** —
> object, veto, appeal, ask, and flag ambiguity — as in the original monolithic pipeline.
> The signal parsing and dispatch machinery already exists; what's missing is the prompt-side
> awareness that lets a model *use* it, plus one new signal (`AMBIGUITY`).
> Scope: this doc covers the **mesh communication layer only** — how agents emit and respond
> to signals. The trace/analysis stage those signals can trigger is specified separately in
> `SPEC_TRACE_GATE_PLAN.md`.
>
> **Implementation status (2026-09-21):** Phase 0 (AMBIGUITY plumbing) and Phase 1
> (argumentation prompt blocks) are SHIPPED. Phase 2 (route into TraceGate) remains open.

---

## 1. Current state

| Piece | State |
|---|---|
| `signals.py` (`SIGNAL_PATTERNS`, `extract_signals`) | ✅ regex parser for QUERY/DELEGATE/VETO/OBJECT/RECOURSE/CONSULT/APPROVE/REVISE/APPEAL/MERGE/REJECT/… |
| `mesh_tasks.py` (`_process_task_signals`) | ✅ dispatch for all of the above |
| `models.py` (`SignalType`, `PipelineContext`) | ✅ 19 `SignalType` values incl. `AMBIGUITY`; ✅ `PipelineContext.ambiguity_signals` accumulator |
| `domain_registry.py` (`MESH_AGENT_SYSTEM_EXTENSION`) | ⚠️ full signal vocabulary exists, but **stripped in `lean` (anchor) mode** |
| `_orig_pipeline_monolith.py` | ✅ the reference behavior: every agent saw the signal protocol and could object/veto/appeal |

**What's missing:** the anchor/coder path (`get_agent_system(..., lean=True)`) returns only
`base_prompt + sandbox_constraint`, so the coder never sees that it may object, appeal, or
flag ambiguity. The old monolith had this in every prompt; the anchor refactor removed it.

---

## 2. Why it was removed (and the risk of re-adding it)

- The `lean` mode comment is explicit: "The mesh/ledger/virtual-memory protocols are
  LoRA-dependent and unused by untrained models; the staging block carries the SEARCH/REPLACE
  format instructions instead."
- Hardening history (repo memory): prompting the untrained coder (qwen3.5:9b / qwen2.5-coder:7b)
  with free-form reasoning/signal latitude produced **comment monologues** and **full-file
  rewrites** instead of anchored SEARCH/REPLACE patches (0/3 usable first attempts).
- Therefore the restoration must be **code-first**: signals travel *alongside* code, never
  *instead of* code. The coder's primary output is still the SEARCH/REPLACE block.

---

## 3. The four-point wiring

A model does not *intrinsically* know a signal tag exists. The tag only works if **four
independent layers** agree; missing any one makes the signal silently do nothing.

1. **Prompt enumeration** — the model's system prompt must list the tag and when to use it.
   The centralized enumeration is `domain_registry.py::MESH_AGENT_SYSTEM_EXTENSION` (full
   mode). To restore argumentation we add a **code-first** block to the `lean` branch.
2. **Enum** — `models.py::SignalType` must include the value (e.g. `AMBIGUITY`), and
   `PipelineContext` gets an accumulator (`ambiguity_signals`).
3. **Parser regex** — `signals.py::SIGNAL_PATTERNS` must have a pattern, or
   `extract_signals()` returns nothing for it.
4. **Dispatcher** — `mesh_tasks.py::_process_task_signals` must have a branch for the type.

A malformed or unknown tag is silently ignored by `extract_signals()` (the regex simply does
not match), so shipping points 2–4 without point 1 is a no-op.

---

## 4. Signal vocabulary to expose (argumentation subset)

The full mode already enumerates these. The anchor path should expose a **curated subset**
focused on argumentation, phrased code-first:

- `[OBJECT:Agent:Reason]` — the instruction/context is wrong or contradicts the engine contract.
- `[APPEAL:Agent:Defense]` — defend your implementation against an objection you believe is incorrect.
- `[CONSULT:Agent:Question]` — ask a technical question when the contract is genuinely ambiguous.
- `[QUERY:Agent:Question]` — ask another agent for information.
- `[AMBIGUITY:Agent:Issue]` — the task spec is underspecified or self-contradictory; you cannot proceed correctly without clarification.
- `[VETO:Agent:Justification]` / `[REVISE:Agent:Reason]` — hard block / revision request (reviewer-side).

Anchor-path phrasing: **"Write the code AND append the signal. Never emit a signal in place of
a required code block."**

---

## 5. New signal: AMBIGUITY

- Syntax: `[AMBIGUITY:<target>:<what is underspecified or contradictory>]`
- Emitted by any agent that cannot proceed because the input is underspecified or
  self-contradictory.
- Handler records into `ctx.ambiguity_signals` (from, target, ambiguity, task_id, task_spec)
  and routes into the TraceGate stage (`SPEC_TRACE_GATE_PLAN.md`).
- Two trigger kinds:
  - **Code-detected** — deterministic TraceGate finds a dangling ref / numeric conflict /
    orphan unit. No model awareness needed.
  - **Model-emitted** — an agent proactively flags ambiguity. Requires all four points above.

---

## 6. Activation & gating

- **Always on (full mode):** ✅ shipped — `MESH_AGENT_SYSTEM_EXTENSION` lists `AMBIGUITY`
  for the non-anchor agents (director, reviewer, tribunal; DOC/CONF excluded).
- **Anchor path (opt-in by default):** ✅ shipped — `_ANCHOR_ARGUMENTATION_EXTENSION`
  (code-first subset) is included in the `lean` return, gated by `MIDWAY_ANCHOR_SIGNALS=1`
  (default off) so the working SEARCH/REPLACE path is unchanged until opted in.
- **Fail-open:** a model that never emits a signal behaves exactly as today; signals are
  additive, not required.

---

## 7. Implementation phases

### Phase 0 — AMBIGUITY plumbing — ✅ DONE
1. `models.py`: `SignalType.AMBIGUITY` + `PipelineContext.ambiguity_signals`. ✅
2. `signals.py`: `AMBIGUITY` regex in `SIGNAL_PATTERNS`. ✅
3. `mesh_tasks.py`: `elif stype == "AMBIGUITY"` handler (record + log; TraceGate routing later). ✅
4. Test: `tests/test_signals.py::test_extract_ambiguity_signal`. ✅

### Phase 1 — Argumentation prompt blocks — ✅ DONE
1. Add `AMBIGUITY` + a "disagreement protocol" clause to `MESH_AGENT_SYSTEM_EXTENSION`. ✅
2. Add `_ANCHOR_ARGUMENTATION_EXTENSION` (code-first subset) and include it in the `lean`
   return, gated by `MIDWAY_ANCHOR_SIGNALS`. ✅

### Phase 2 — Route into TraceGate
Wire the `AMBIGUITY` handler (and review-loop objections) into `run_trace_gate(...)` per
`SPEC_TRACE_GATE_PLAN.md`.

---

## 8. Failure modes & mitigations

| Risk | Mitigation |
|---|---|
| Coder emits signals *instead of* code (comment-monologue regression) | Code-first phrasing; `MIDWAY_ANCHOR_SIGNALS` flag to disable; existing empty-block / no-real-code guards still fire |
| Untrained model never emits signals (wasted tokens) | Block is short (~6 lines); opt-in in anchor mode |
| Malformed tag silently dropped | Parser is strict; this is fail-open by design (model just proceeds) |
| AMBIGUITY spam (every task flagged) | Handler only records; no forced re-prompt until TraceGate decides `NEEDS_CLARIFICATION` |

---

## 9. Relationship to TraceGate

This doc specifies the **communication layer** (how agents signal each other). The stage that
*consumes* an `AMBIGUITY` finding — building the requirement↔build-unit trace, scoring
sufficiency, and detecting contradictions — is `SPEC_TRACE_GATE_PLAN.md`. The two meet at
`mesh_tasks.py::_process_task_signals` (the `AMBIGUITY` branch) → `run_trace_gate(...)`.
