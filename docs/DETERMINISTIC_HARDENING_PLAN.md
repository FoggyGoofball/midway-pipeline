# Deterministic Hardening Plan — Enforce & Verify, Stop Repairing Indefinitely

> Status: **Phase 1 in progress** (read-only invariant reporter).
> Companion: `docs/LORA_GENERATION_DESIGN.md` (root-cause track), `docs/SIGNAL_TAGS_REFERENCE.md`.

## 0. North star

Replace "33 independent symptom repairs" with a **two-stage contract**:

1. **Enforce** — a small set of provably-safe transformations, each keyed off one
   authoritative fact source (bridge contract, symbol table, arity table, balancer).
2. **Verify-or-revert** — after enforcing, check 5 invariants. If *any* fails,
   revert to the last clean baseline. No freeform salvage.

The rule that ends ghost-chasing: **the pipeline never ships a file it can't
prove is clean; it reverts instead of repairing indefinitely.**

## 1. The five invariants (single source of truth)

| # | Invariant | Predicate | Existing check |
|---|---|---|---|
| I1 | Syntactic | `luac -p` exit 0 | `_luac_syntax_errors`, `_luac_clean` |
| I2 | No phantom API | no identifier outside bridge contract (namespace+name) | `contract_validator.validate_lua_content` |
| I3 | Exactly-one lifecycle | `OnLoadStatic` ×1, `OnLoad` ×1, `OnUnload` ×1, `OnStep` ×1 | dedupe fixes #1/#12/#17 |
| I4 | No leaked globals | every bare assignment is declared-local or engine-global | `_lua_symbol_table` |
| I5 | Correct arity | every call's arg count matches arity table | `midway_api_signatures` |

I6 **Structural balance** (brackets/blocks) is enforced by `balance_lua_blocks`
*before* I1 runs — I1 is its proof.

## 2. Collapsing 33 fixes into 6 enforcers

| Enforcer | Absorbs (Fix #) | Keyed off |
|---|---|---|
| A. Sanitize | 3, 7, 11 + `_strip_search_replace_metadata` sweep | artifact/marker/prose regexes |
| B. Structure | 13, 15, 23, 24, 25, 26, 27, 28, 33 + `balance_lua_blocks` | `_mask_noise` + block-stack scanner |
| C. Contract | 6, 8, 16, 21, 22, 29 | `bare_name_to_namespace`, contract validator |
| D. Lifecycle | 1, 4, 5, 10, 12, 17 | hook presence scan |
| E. Scope | 10, 19, 20, 32 | `_lua_symbol_table` |
| F. Semantic | 2, 9, 14, 18, 31 | modifier-key + pool-name maps |

This is a *reorganization*, not a rewrite: the 33 functions keep their bodies;
they get re-invoked by 6 enforcer functions in a fixed order. Parity tests gate
the switch.

## 3. The `verify_or_revert` gate (Phase 3)

`_verify_gate.py::run_invariant_checks` (Phase 1, read-only) grows a
`verify_or_revert(ctx, rel_path, proposed, last_clean) -> str` that returns the
clean file or the baseline. Hooked into `_post_process_workspace_lua_files` and
the surgery path; the `last_clean` baseline already exists
(`ctx._last_luac_clean_anchor`, `completed_file_snapshots`).

## 4. Phases

1. **Invariant library (read-only)** — `_verify_gate.py` + a log line. No
   behavior change. ✅ **DONE** (`tests/test_verify_gate.py`).
2. **Enforcer re-organization** — behavior-preserving; parity-tested.
3. **Enable verify-or-revert** — revert on invariant failure before the review loop.
   ✅ **DONE** — `_verify_gate.py::verify_or_revert` wired into
   `_post_process_workspace_lua_files` (reverts to `ctx._last_luac_clean_anchor`).
4. **Remove dead fixes** — only after 3+ clean runs, with coverage evidence.

### Tombstones (added 2026-09-21)

`_tombstones.py` — a durable catalogue of failure signatures, each a one-line
prohibition + one-line reason. Two consumers are wired:
- **Preflight guards** — `TOMBSTONE_GUARDS` extends `_preflight_static._GUARDS`
  so a task whose output hits a tombstone gets a hard, specific pre-flight error.
- **Fix-prompt block** — `_finalize_review` maintains a bounded (last-3, deduped)
  ring buffer and renders `## RECENT TOMBSTONES …` as concise negative
  constraints. The LoRA corpus is the deferred third consumer.

### Phase 2 — refined (prepared, awaiting the current run)

The current `post_process_lua()` order is **interleaved, not bucket-grouped**, and
has hard dependencies (e.g. `_add_midwayphysics_prefix` must precede
`_strip_phantom_api_calls`, `_dedupe_onstep_registrations` must precede
`_neutralize_orphaned_else` which precedes the 2nd `_repair_lua_structure`).
Reordering into buckets in one step would change output. So Phase 2 is split:

- **2a (shipped as preparation):**
  - `_enforcers.py` — the data-only bucket manifest (6 buckets, 32 transform
    fixes + the #7 gate; Fix #30 does not exist).
  - `tests/test_enforcer_order.py` — locks manifest coverage (no gaps/dupes,
    every function real) and the critical orderings by introspecting
    `post_process_lua` source. No fixture corpus needed.
- **2b (next, after the run completes):**
  1. Snapshot the on-disk `strongman.lua` (real + staging) into
     `tests/fixtures/` as the parity corpus.
  2. Introduce the 6 enforcer functions as *named sequences that preserve the
     current total order* (a pure extraction — no reorder), point
     `post_process_lua()` at them, and assert byte-identical output on the corpus.
  3. Only then consider reordering *within* each bucket, gated by the same
     corpus-parity test plus `tests/test_enforcer_order.py`.

## 5. Safety

- Phases 1–2 are read-only/reorganizational; Phase 3 is revert-only (fails safe).
- The `_convergence.py` trip-wire complements the gate: gate reverts on *unclean*,
  wire stops on *no progress*.
- Every enforcer is idempotent; the gate never invents code (synthetic-scaffold
  regenerate remains the only fill path).

## 6. Non-goals

- No Lua AST/parser (stay on `luac` + existing symbol/arity tables).
- No prompt/LoRA changes (separate track).
- No fix removal in Phases 1–3.
