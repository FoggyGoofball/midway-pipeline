# Chat-Mode Inter-Persona Signals & Escalation — Plan

> Status: **PARTIAL** — Phase 0 (AMBIGUITY plumbing) and Phase 1 (prompt-side awareness)
> are shipped; Phase 2 (chat signal router + escalation) and Phase 3 (TraceGate
> consumption) remain plans. Next target once NEW_ATTRACTION builds converge.
> Companion to `docs/MESH_SIGNAL_ARGUMENTATION_PLAN.md` (communication layer) and
> `docs/SPEC_TRACE_GATE_PLAN.md` (the stage that consumes AMBIGUITY).

## 0. Goal

Give the **chat / planning path** the ability to (a) talk to other personas, and
(b) **escalate** — when the default "council" model fails, is ambiguous, or the
user asks, route the question to the specialized personas (reviewer, director,
coder, analyst, …) running their own models.

Current state: chat mode (`pipeline.py` `is_chat` branch) is a single direct
`call_ollama(CHAT_SYSTEM, …)` — no signals, no mesh, no escalation.

## 1. The two layers

1. **Signal layer** — parse `[CONSULT:/[AMBIGUITY:/[QUERY:/[OBJECT:/[VETO:/[APPEAL:`
   out of chat/planner output (reuses `signals.py::extract_signals`).
2. **Escalation layer** — the user (or a detected failure) can invoke the
   specialized personas directly, not just the council.

Both are **opt-in and fail-open**: a run that never emits a signal and never
asks to escalate behaves exactly as today.

## 2. The council model

Default first-line: `CHAT_MODEL` (qwen3.5:9b) with `PLANNING_SYSTEM` (planner) or
`CHAT_SYSTEM` (general chat). One model, one turn, no mesh — unchanged from today.

## 3. Escalation triggers

| Trigger | Detection | Action |
|---|---|---|
| **Implicit signal** | council output contains `[CONSULT:<persona>:<q>]` / `[AMBIGUITY:<persona>:<issue>]` | one-round consult to that persona (flag-gated: `MIDWAY_CHAT_SIGNALS=1`) |
| **User request** | prompt matches escalate keywords or names a persona | run the requested persona(s) |
| **Failure** | `is_fatal_ollama_error(response)` or empty/short response | surface "council failed — escalate?" and offer persona list |

Escalate keywords: `escalate`, `second opinion`, `review this`, `full council`,
`what does <persona> think`, `ask the <domain> team`, `get <domain>'s opinion`.
Persona naming resolves through `domain_registry.AGENT_ALIAS_MAP` /
`resolve_agent_name` (so "reviewer", "the C++ team", "analyst" all map).

## 4. Escalation execution

1. **Resolve personas** — from the user request, or a default set `{Reviewer, Director}`
   when unspecified ("full council" = all ready domains).
2. **Run each persona sequentially** (one model resident at a time —
   `_evict_previous_model` already handles the swap) via
   `call_ollama(get_agent_system(key), question, f"Escalated {name}", ALL_DOMAINS[key]["model"])`.
3. **Emit a council report**:
   ```
   ## Council Report
   - Reviewer (qwen3.5:9b): …
   - Director (llama3.1:8b): …
   ```
4. **Fold the report into the planning draft** (`draft["history"]` as a system
   note) so the planner can incorporate the objection/answer on the next turn.

Persistence: the report lives in `docs/plans/<slug>_draft.json` alongside the
dialogue, so "now incorporate the reviewer's objection" works across turns.

## 5. Escalation loop

Council → (signal | user request | failure) → specialized personas → report back
into council → next turn. The council remains the orchestrator; escalation is
advisory input, not a replacement, unless the user says "use the reviewer's
answer directly".

## 6. Cost & safety

- Default escalation set = 1 persona (cheapest useful consult); "full council"
  is explicit and user-paid.
- Escalation is **sequential** (no parallel model residency — VRAM constraint).
- `MIDWAY_CHAT_SIGNALS=1` gates the *implicit* signal auto-consult; user-initiated
  escalation needs no flag.
- Malformed/unknown persona → surface "unknown persona, available: …".

## 7. Implementation phases

### Phase 0 — AMBIGUITY plumbing (mesh, no chat) — ✅ DONE
1. `models.py`: `SignalType.AMBIGUITY` + `ctx.ambiguity_signals`. ✅
2. `signals.py`: `SIGNAL_PATTERNS["AMBIGUITY"]`. ✅
3. `mesh_tasks.py::_process_task_signals`: `AMBIGUITY` branch (record + log). ✅
4. Tests. ✅

### Phase 1 — prompt-side awareness — ✅ DONE
1. `MESH_AGENT_SYSTEM_EXTENSION` (full mode): add `AMBIGUITY` + disagreement clause. ✅
2. `_ANCHOR_ARGUMENTATION_EXTENSION` (lean/coder mode), gated `MIDWAY_ANCHOR_SIGNALS=1`. ✅

### Phase 2 — chat signal router + escalation
1. `planning.py`: after each council response, `extract_signals`; dispatch
   `CONSULT`/`QUERY` → one-off persona call (flag-gated).
2. New `escalate.py` (or in `planning.py`): `is_escalation_request(prompt)`,
   `resolve_escalation_personas(prompt)`, `run_council_report(personas, question, ctx)`.
3. `pipeline.py` chat branch: route escalation requests before the normal chat
   path; fold council report into the active planning draft (or emit standalone).
4. Failure hook: `is_fatal_ollama_error` / empty response → offer escalation.

### Phase 3 — TraceGate consumption
Route `ctx.ambiguity_signals` into `run_trace_gate(...)` (`SPEC_TRACE_GATE_PLAN.md`).

## 8. Open decisions

- **Escalation surface**: chat response only, or also the dashboard `/api/input`
  mechanism? (Recommend: chat response first.)
- **Default escalation set**: `{Reviewer}` alone vs `{Reviewer, Director}`.
- **Parallel vs sequential**: sequential now (VRAM); parallel only if the Deck
  ever supports two residents.
