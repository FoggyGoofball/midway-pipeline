# Research Manifest — Midway Pipeline Convergence

> Prepared 2026-09-18. Hand this to an external research AI (or human researcher) that
> has NO access to the workspace. Everything needed to understand the problem is inline.
> Goal: identify research-backed techniques to get a small-model codegen pipeline from
> "suspended" to "success", where success = a syntactically valid, feature-complete Lua
> attraction script.

---

## 1. What the pipeline is (30-second context)

We run an agentic code-generation pipeline that writes **Lua 5.4 attraction scripts** for a
carnival arcade game. A prompt ("build the strongman striker") is decomposed into ~11
anchor tasks; each task fills a `-- [TASK_N_INSERT_HOOK]` anchor in a shared `.lua` file via
SEARCH/REPLACE patches. A deterministic post-processor + review-fix loop then cleans and
validates the result.

**Hardware/serving:** a Steam Deck (12 GB unified RAM) runs **Ollama**. Models:
- coder + reviewer: `qwen3.5:9b` (instruct-tuned, ~9.7B, quantized)
- director/clarifier: `llama3.1:8b-instruct-q4_K_M`
- summarizer: `phi3.5:latest`
- All calls: Ollama `/api/chat`, `think:false`, `num_ctx=32768`, KV cache q8_0.

**The pipeline already has ~26 deterministic post-processor fixes** (bare-call prefixing,
phantom-API stripping, arity repair, duplicate-function dedupe, symbol-table-based
read-before-write/global-leak repair, comment-monologue collapse, etc.). A newly-added
"Mechanics Scaffold" stage reasons each mechanic in bounded pseudocode + exact API
signatures before codegen.

**Current milestone:** the final "phantom API" gate now **passes** — API names/arity/namespaces
are correct. The blocker has moved to **Lua syntax validity**: the file is still
syntactically broken, and the review-fix loop cannot repair it, so the run suspends.

---

## 2. The exact failure modes to research (do not skip this section)

These are the concrete, observed blockers. Any research should map to one of these.

1. **Unclosed parenthesis / bracket in multi-line calls.** The coder emits
   `MidwayPhysics.CreatePool("puck_pool", 2, 2, { ...` and never closes it. `luac` reports
   `')' expected (to close '(' at line 95) near 'function'`.
2. **Unbalanced `end` / `function` / `if` blocks.** Surplus or missing `end` tokens, often
   after a fix cycle removes/duplicates a block. We have a simple `_lua_balancer` that
   removes surplus `end`s, but it cannot insert the *right* missing opener and it corrupts
   `end)` (anonymous-function closer) into an orphaned `)`.
3. **The review-fix loop re-emits broken whole-file blocks.** Given "fix task N", the fixer
   ignores its bounded SEARCH/REPLACE mandate and emits a full module header + a duplicated
   economy block (same code 2–3×), with unbalanced braces. This fails `luac` every cycle and
   the loop reverts to the last-clean snapshot → no progress → circuit breaker trips.
4. **Identifier invention.** The coder uses handle/variable names not in the design contract
   (`mallet_handle`, `puckBody`, `ball_handles`) and leaks globals (`power_delta`, `aim_delta`).
5. **JSON-isms leaking into Lua.** `{"shape": "sphere"}` (JSON colon syntax), `local`
   keyword inside table constructors, `rand()` instead of `math.random()`, bare
   `MOD.heat` statements (valid Python, invalid Lua).
6. **Truncated "consume all modifiers" fragments.** A mandate to "read all 9 modifiers"
   drives a single-statement `local _, _, ... = MOD.a, MOD.b, ...` that truncates to a
   dangling `local _)`.

---

## 3. Research themes (prioritized)

### P1 — Constrained / grammar-constrained decoding (highest leverage)

**The hypothesis:** most of §2's syntax failures are *impossible by construction* if the
model's output is constrained to a grammar that only admits balanced parentheses/brackets
and valid table literals.

- Does Ollama support **GBNF grammars** (llama.cpp `grammar`) via the native `/api/chat`
  endpoint, and can we pass a per-call `grammar` (not just `format: json`)?
- Can we write a GBNF (or JSON-schema-with-regex) that enforces:
  - balanced `( )` / `[ ]` / `{ }`,
  - `key = value` (not `key: value`) inside `{ }`,
  - no `local` inside `{ }`,
  - `math.random(` not bare `rand(`?
- If full-Lua GBNF is impractical, can we at least constrain the **SEARCH/REPLACE envelope**
  (`<<<<<<< SEARCH` … `=======` … `>>>>>>> REPLACE`) so the fixer cannot emit prose/full-file
  dumps?
- What do `outlines`, `guidance`, `llama.cpp` grammar examples say about token-level
  bracket-balancing grammars for code (not just JSON)?

**Deliverable wanted:** a concrete Ollama grammar file + how to pass it, or a clear
"not supported at this size" verdict with the workaround.

### P2 — Robust deterministic Lua syntax repair (the other high-leverage item)

**The hypothesis:** a real Lua parser with error recovery can fix the structural wrecks
(§2.1–2.2) that our regex balancer cannot.

- Is there a maintained **Lua 5.4 parser with error recovery / auto-repair**?
  Candidates: `luaparse` (JS), `lua-parser` (Python), tree-sitter-lua, `FullMoon`,
  `lua-format`, `stylua`. Which, if any, can *insert* a missing `)` or `end`?
- Can **tree-sitter-lua** + a node-matching pass deterministically repair: an unclosed
  `CreatePool(` (insert `)` at the right line), a surplus `end`, a missing `end`?
- How do Lua formatters/linters (`luacheck`, `stylua --check`, `selene`) classify these
  errors, and can their error positions drive a *surgical* repair?
- Is there prior art on "LLM emits broken code → deterministic parser-based repair → re-run
  compiler" (this is the loop we're trying to make converge)?

**Deliverable wanted:** a shortlist of 1–2 tools we can actually invoke from Python on
Windows, with example repair output on the exact error strings above.

### P3 — How similar agent pipelines gate/repair model edits

**The hypothesis:** we've reinvented a diff-apply + lint gate that mature tools already
solved.

- **Aider**: its "repo map" + unified-diff edit format + `git apply` rejection. How does it
  force a small model to emit a *well-formed* edit, and what happens on malformed edits?
  Is its edit format more robust than our SEARCH/REPLACE markers?
- **SWE-agent / SWE-bench**: agent scaffold + test-gated verification loop. How do they
  give the model *actionable* compiler feedback (exact line/col + the failing code), and
  how do they prevent infinite regress?
- **OpenHands / Cline / Continue**: how they stream-apply diffs and roll back on failure.
- **Cognition/Devin, Factory, etc.** planning→code→verify loops — what's the *verifier*
  doing that we're not?

**Deliverable wanted:** a table of "tool → edit-format → gate → rollback" with notes on what
to copy, and specifically whether a *git-apply-style* strict-apply (reject whole edit on any
hunk mismatch) beats our fuzzy SEARCH/REPLACE.

### P4 — Small-model code generation best practices (~9B class, quantized)

**The hypothesis:** specific decoding/prompt choices materially change the structural-error
rate for a 9B instruct model.

- For code, what's the evidence on: temperature 0 vs low, repetition penalty, `top_k`/
  `top_p`, and **stop sequences** (can we stop at a mismatched token)?
- Does **fill-in-the-middle / infilling** help with "fill this anchor" tasks? Does Ollama
  support FIM on qwen3.5?
- **Few-shot canonical examples**: does embedding a single exact-format worked example (we
  do this in the scaffold) reduce structural errors more than prose rules?
- **Self-consistency / sampling N and picking the one that compiles**: with a slow deck this
  is expensive — what's the cheapest variant (e.g. N=2, or temperature 0.3 + best-of-2)?
- **Chain-of-thought vs direct**: we have direct evidence CoT causes "comment monologues".
  What does the literature say about CoT *hurting* small code models, and what's the
  recommended alternative (scratchpad? plan-then-code in two calls?)?

**Deliverable wanted:** a concrete decoding profile (temperature/top_p/repeat/stop) for
qwen3.5-class models doing code, plus a yes/no on FIM in Ollama.

### P5 — Iterative self-repair with weak models

**The hypothesis:** a 9B model *can* fix its own syntax errors if the feedback is shaped
correctly.

- **Self-Correction / Reflexion / CRITIC** literature: when does feeding the compiler error
  back actually improve output, and when does it cause regress (we observe regress)?
- What feedback format maximizes a weak model's repair success: (a) raw `luac` stderr,
  (b) exact line/col + the offending line only, (c) a diff of expected vs actual?
- Is "re-ask with the error + a *corrected* example" more effective than "re-ask with the
  error" (we currently do the latter)?
- **Verifier-in-the-loop RL / GRPO-style** self-training for code — relevant only if we
  ever fine-tune (see P7).

**Deliverable wanted:** a recommended feedback template for the fix loop (the exact prompt
shape) grounded in whatever papers show it works for small models.

### P6 — Evaluation methodology so we can tell if anything worked

We currently judge by "did the run converge" (multi-hour, non-deterministic). We need a
cheap signal.

- What's the standard metric for "small model emits valid code": **pass@1**, **compile@1**,
  **edit-apply rate**? How to compute it cheaply on ~20 held-out anchor tasks?
- How to A/B model/prompt/grammar changes given (a) slow inference, (b) non-deterministic
  decomposition, (c) a deck that throttles? (paired sampling, fixed seeds, batched eval?)
- Any existing **small-model codegen benchmark** we could reuse as a proxy (HumanEval-Fix,
  MBPP, CRUXEval, LiveCodeBench) — specifically ones measuring *syntactic validity*?

**Deliverable wanted:** a 3-step eval protocol (what to measure, how to hold it fixed, how
many samples) that runs in < 1 hour.

### P7 — Model selection & fine-tuning (long-term)

- Current best **small** (≤10 GB quantized) instruct models for *code*, 2025-2026: is
  `qwen3.5:9b` still the best instruction-follower, or is there a better code-specialized
  model (qwen2.5-coder:7b, starcoder2, codestral, phi-4, gemma-3, etc.) that fits 12 GB?
- We already tried `deepseek-coder-v2:16b` and it failed (GMod-era training, wraps answers
  in JSON, slow). What makes a model *syntactically reliable* on Lua specifically?
- Is **LoRA fine-tuning** (we have a `lora generator/` scaffold) on a curated "valid Lua
  anchor fills" dataset the real fix? What dataset size/mix does the literature say moves
  pass@1 for a 9B model? (This links to our LoRA design doc.)

**Deliverable wanted:** a ranked model shortlist with a yes/no on fine-tuning value, and
rough dataset recipe.

---

## 4. Constraints the researcher must respect

- **Windows** dev box running the pipeline; **Steam Deck (Linux, 12 GB)** running Ollama.
- **Latency matters**: a full run is 1.5–3 hours; a single 9B call is ~30–90 s TTFT.
  Anything O(N) calls is likely too slow.
- **No bigger model** unless it fits 12 GB alongside the KV cache.
- The pipeline must remain **deterministic-first**: every fix we've added is pure
  Python/regex + `luac`. Prefer research that adds a *gate* (grammar, parser) over research
  that adds more LLM calls.
- Lua 5.4, not LuaJIT. `luac -p` is our syntax oracle.

---

## 5. Pointers for follow-up (what to hand back)

When the researcher returns findings, we need, for each theme: (1) the technique, (2) the
evidence (paper/tool/benchmark), (3) whether it fits our constraints, and (4) a concrete
integration step (which file/function in our pipeline it touches). Themes are already
prioritized P1→P7; P1 and P2 are the ones most likely to unblock success this week.

---

## 6. The pipeline in enough detail to reason about it

Full pipeline (Python 3.12 stdlib, no external web framework):

```
pipeline_stream_server.py  (ThreadingHTTPServer, port 8765, SSE streaming)
   └─ pipeline.py::run_mesh_pipeline
        ├─ Phase 0  mesh_architect.py     — director decomposes prompt → AttractionDesign
        │                                   (handles, edges, checklist, module_state_variables,
        │                                    pool_requirements). Native /api/chat format:json,
        │                                    num_predict=2048.
        ├─ Phase 1  mesh_fetches_blueprint / _director — enrich tasks (File:/Outputs: parsing).
        ├─ Phase 2  _build_skeleton.py — deterministically writes strongman.lua skeleton:
        │             11 anchors  -- [TASK_1_INSERT_HOOK] … [TASK_11_INSERT_HOOK],
        │             module-state vars, pool-name constants, lifecycle stubs
        │             (OnLoadStatic/OnLoad/OnStep/OnUnload), SpawnSharedBooth.
        ├─ Phase 3  mesh_tasks.py — wave-sorted per-anchor execution.
        │           mechanics_scaffold.py — (optional, MIDWAY_MECHANICS_SCAFFOLD=1) per logic
        │             task, emit bounded {INTENT, PSEUDO, API-calls-with-exact-signatures}.
        │           _helpers_exec.py::execute_task — anchor staging block (shows ~5 lines around
        │             the task's hook + scaffold + shared-state contract), asks coder for a body
        │             or SEARCH/REPLACE, applies via _fuzzy_apply_patch / _splice_body_at_anchor.
        ├─ Phase 4  _finalize_preflight.py — deterministic static guard (C-checks), arity
        │             fixes (Fix G), error-owner routing via anchor-line→task map.
        ├─ Phase 5  _finalize_review.py — per-anchor review-fix loop:
        │             _run_tribunal_coder_debate (coder↔tribunal argue, ≤3 rounds),
        │             _deterministic_verdict (luac + runtime sim + preflight + economy),
        │             coverage gaps, revert-on-regression to last luac-clean snapshot.
        ├─ Phase 6  mesh_finalize.py — post_process_lua_file, observability, PhantomAPI gate.
        └─ Final    Final Approval Director (llama3.1:8b) — may demand revision.
```

**Key facts the researcher must assume are true (verified in production):**

- **The syntax oracle is `luac -p`** (Lua 5.4, full path resolved by `_luac_path.py`).
  "luac-clean" == compiles without error. The pipeline snapshots the last luac-clean
  file content and reverts to it when a fix introduces a syntax error.
- **Staging:** `atomic_write_text` redirects writes to `.staging_workspace`; reads must use
  `get_staging_path()` or the fix loop silently reads stale content. A recurring bug class.
- **The anchor marker `-- [TASK_N_INSERT_HOOK]` is load-bearing** across ~10 regex sites.
  Do not suggest renaming it.
- **Post-processing is deterministic-first**: ~26 pure-Python/regex fixes + `luac`. The
  team strongly prefers a *gate* (grammar, parser) over more LLM calls.
- **`ns.capitalize()` on namespace keys loses inner caps** (MidwayInput→Midwayinput).
  The bridge contract must keep an original-case map.
- **`re.sub(pattern, repl)` interprets `\n` in the replacement as a newline** — this has
  corrupted files. Not researcher-relevant except as a caution about the codebase.

---

## 7. Full failure history (chronological, distilled)

Everything below is observed behaviour from real runs. It is included so the researcher can
(a) map recommendations to real symptoms, and (b) avoid recommending things already tried.

### 7.1 Model experiments and verdicts

| Model | Role tried | Tok/s (deck) | Outcome |
|---|---|---|---|
| `qwen2.5-coder:7b` | coder | 7.9–8.5 | Dumps full lifecycle fns into every anchor → duplicate-function errors; caches `MOD` at module scope; `PoolTotal()`/`PoolFree()` with 0 args; needed retry 11/11 for SEARCH/REPLACE; hallucinated GMod APIs when retry prompt was context-free. |
| `qwen3.5:9b` (current) | coder + reviewer | 2–3 | Much better instruction-following (8–10/11 first-try SEARCH/REPLACE), but: hallucinates phantom APIs (`SetFriction`, `AddLinearVelocity`, `RegisterCallback`, `OnStepNext`, `MOD.power_up`); caches `MOD`; writes ~50-line "comment monologues" arguing with the spec; leaks/invents identifiers. Is a **thinking model** — see §7.2. |
| `deepseek-coder-v2:16b` | coder (trial) | MoE, 2.4B active; TTFT 120–150 s | Failed: 2-year-old GMod-era training worse at the MidwayPhysics contract; wraps *every* answer in ```json; phantom `Physics.*` namespace; 0/9 first-try SEARCH/REPLACE; ctx=16K was 112–123% oversubscribed → stalls; ctx=24K too slow. Reverted. |
| `llama3.1:8b-instruct-q4_K_M` | director/clarifier | 1.4–3.4 | OK for planning JSON, not for code. |
| `phi3.5:latest` | summarizer/oracles | fast | Budgeted to one call/run + deterministic fallbacks (see §7.4). |

### 7.2 Ollama serving specifics (do not re-research these — they are solved)

- The OpenAI-compat `/v1/chat/completions` endpoint **crashes the 9B runner** (HTTP 500
  "model runner has unexpectedly stopped"). Native `/api/chat` works.
- `qwen3.5:9b` is a **thinking model**: on strict-output prompts it emits `message.thinking`
  (CoT) before `message.content`; the streaming parser reads only `content`, so a short
  `num_predict` yields empty output. `think:false` must be a **top-level** payload key —
  placing it inside `options{}` is silently ignored.
- KV cache must be `q8_0` (f16 OOMs). `num_ctx=32768` (64K intermittently OOMs: 9B weights
  ~10.3 GB + 64K KV ~2.7 GB + buffers > 12 GB).
- 9B + 7B cannot co-reside (16.9 GB > 12 GB). This is why coder==reviewer==9B today.

### 7.3 Observed model failure modes (the catalog)

1. **Full-lifecycle dumps**: coder re-emits `OnLoadStatic/OnLoad/OnStep/OnUnload` inside a
   single anchor → duplicate function declarations (legal Lua shadowing, but breaks logic).
   Mitigated by refusing any fix that redefines lifecycle hooks, and `_strip_lifecycle`.
2. **Bare API calls**: `IsSensorTriggered(...)` instead of `MidwayPhysics.IsSensorTriggered(...)`.
   Mitigated deterministically (`_add_midwayphysics_prefix`), but historically the guard
   validated per-task *fragments*, not the accumulated file, so prefixes were invisible and
   re-flagged every cycle.
3. **Phantom APIs**: calling names that don't exist in the bridge contract (GMod or invented).
   `_strip_phantom_api_calls` (Fix #22) + a final PhantomAPI gate now catch these — this
   gate currently **passes**.
4. **JSON-isms**: `{"shape": "sphere"}` colon tables, `local` inside `{ }`, `rand()` bare,
   bare `MOD.heat` statements. Fixed by #25/#23/#15.
5. **Comment monologues**: 50-line essays in Lua comments arguing with the spec (e.g. a
   GDD contradiction about "teleport" that the API cannot express). Fixed by Fix #11 + a
   COMMENT DISCIPLINE prompt ban + rewording the GDD itself.
6. **Marker/format corruption**: `<<<<<` (5 chars) instead of `<<<<<<<`, stray `=======`
   inside the REPLACE text (lands literally in the file → invalid Lua), copying the staging
   block's `NN |` line-number prefixes into SEARCH blocks. Mitigated: lenient marker regex
   `[<>=]{5,7}`, marker-line sanitization, "never copy line-number prefixes" instruction.
7. **Duplicate `_` declarators**: `local _ = MOD.a, _ = MOD.b, ...` is a Lua syntax error.
   Fix #13 splits into `local _ = MOD.a` + `_ = MOD.b; _ = MOD.c`.
8. **Bare expression statements**: `MOD.heat` / `1.0` as statements (valid Python, invalid
   Lua). Fix #15 rewrites to `local _ = MOD.heat`. Must run LAST (after neutralization turns
   things into bare `1.0`).
9. **Unclosed multi-line call**: `CreatePool("puck_pool", 2, 2, { ...` never closed →
   `')' expected … near 'function'`. **STILL OPEN** — this is the current blocker.
10. **Broken table literals from duplicated blocks**: the fixer re-emits the same economy
    block 2–3× per REPLACE; the duplicate `local` redeclarations are *legal* (Lua 5.4
    shadowing), so the luac killer is the unbalanced parens/ends in the duplicated block.

### 7.4 What has already been tried (do not recommend as "new")

- **Deterministic verdict instead of LLM review**: `_deterministic_verdict` (luac + runtime
  sim + preflight + economy) replaced a 9B LLM verdict that hung/crashed every cycle.
- **Revert-on-regression**: the fix loop reverts to the last luac-clean snapshot when a fix
  introduces a syntax error. This stops corruption but does *not* converge — the model
  cannot close the remaining gaps, so the run correctly FAILs at the circuit breaker.
- **Tribunal debate** (`_run_tribunal_coder_debate`, ≤3 rounds): coder↔tribunal argue to
  consensus. The tribunal can MERGE/REJECT but cannot *write* a correct block (same 9B
  model), so deterministic enforcement is still required.
- **Mechanics Scaffold** (`mechanics_scaffold.py`): pre-reason each mechanic into
  {INTENT, PSEUDO, exact API signatures} before codegen. Result: API names/arity/namespaces
  became correct end-to-end (PhantomAPI gate passed for the first time), but identifier
  discipline and structural syntax did *not* improve.
- **Module-state injection** (skeleton writes `local <handle> = nil` / pool-name constants at
  module root; tasks never declare shared state): killed the "we don't have a puck handle"
  panic class, but tasks still invent *unregistered* identifiers.
- **Arity repair** (Fix G): truncates call args to the bridge-contract max arity. Covers
  Spawn*/Body calls; `CreatePool` 9-arg was historically missed (later extended).
- **Symbol-table read-before-write / global-leak repair**: `_lua_symbol_table` + Fixes
  #20/#10 — localizes leaked globals, auto-declares invented first-args of physics calls.
- **Comment stripping before API scanning**: `_analyse_lua_text` + phantom gate strip
  `--[[ ]]` and `--` before scanning (comments were causing false phantom flags).
- **Staging-aware reads everywhere** (staged vs real file mismatch caused silent reverts).
- **VRAM stub summarization** (`_make_vram_stub` → phi3.5 domain-aware ≤250-char summaries):
  architecture files summarized once, byte-identical KV prefix across tasks.
- **Phi3.5 oracle budgeting**: one upfront run summary per run, then deterministic fallbacks,
  so oracle calls stop evicting the 9B coder from VRAM.
- **Thermal/cooldown pacing**: `MIDWAY_MODEL_CALL_COOLDOWN` + `MIDWAY_THERMAL_COOLDOWN` to
  stop Steam Deck connection drops (WinError 10054/10060).
- **Body-first writer** (`_splice_body_at_anchor`): ask for a plain fenced Lua body at the
  marker instead of SEARCH/REPLACE, because the 9B is reliable at emitting a whole fenced
  function but unreliable at surgical SEARCH/REPLACE.
- **Full-file diff application** (`_apply_full_file_rewrite_via_diff`): diff a model's
  full-file output against disk, apply only changed hunks, preserve protected lines.

### 7.5 Current state (2026-09-18, attempt 3)

- PhantomAPI Final Gate **PASSED** for the first time (economy calls present, no phantom APIs).
- But the run **SUSPENDED** via circuit breaker; final strongman.lua (5988 chars) is **not
  luac-clean**: `line 112: ')' expected (to close '(' at line 95) near 'function'` — an
  unclosed `CreatePool(` that the fix loop never repaired.
- Remaining known weaknesses: (1) review-fix error routing is **coarse** — whole-file line
  errors get stamped onto one task (error-owner routing was just fixed but unverified in a
  live run); (2) variable hygiene still broken (Fix #20/#10 patch, don't prevent);
  (3) blueprint task decomposition is **non-deterministic** across runs — task N is not the
  same mechanic run-to-run, so per-task scaffolds/debugging must not assume stable numbering.

---

## 8. Exact error strings and symptoms (for the researcher's reproduction)

- `luac: strongman.lua:112: ')' expected (to close '(' at line 95) near 'function'`
- `luac: strongman.lua:140: unexpected symbol near '=='` (stray `=======` marker leaked)
- `luac: <eof> expected near 'end'` (unbalanced end after duplicated block)
- `luac: syntax error near 'MOD'` (bare `MOD.heat` statement)
- `')' expected near '='` from `local _ = MOD.a, _ = MOD.b` (duplicate `_` declarators)
- `WinError 10054` / `10060` (Steam Deck thermal/connection drop)
- `model runner has unexpectedly stopped` (Ollama 9B OOM via /v1 endpoint)
- Phantom gate false positives: bare `SpawnSharedBooth` (a legitimate *bare global* helper)
  was being flagged phantom — the scaffold REQUIRES `SpawnSharedBooth()` and it must never be
  replaced with `SpawnStatic*`.

## 9. Files the researcher may see referenced (glossary)

| Name | Purpose |
|---|---|
| `_post_process_lua.py` | 26 deterministic fixes; `_lua_symbol_table`, `post_process_lua`, `post_process_surgery`, `repair_lua_syntax` |
| `_preflight_static.py` | C-checks; `_ARITY_SIGS`, `_fix_spawn_arities_in_text` (Fix G) |
| `_finalize_preflight.py` | static-guard run; `_resolve_anchor_owner` (line→task owner); luac gate |
| `_finalize_review.py` | `_coder_defend_or_revise`, `_run_tribunal_coder_debate`, `_deterministic_verdict`, `_coverage_gaps`, `_run_review_fix_loop` |
| `_helpers_exec.py` | `execute_task`, `_extract_search_replace_blocks`, `_fuzzy_apply_patch`, `_splice_body_at_anchor`, `_apply_full_file_rewrite_via_diff`, `_build_design_state_contract` |
| `mechanics_scaffold.py` | `run_mechanics_scaffold`, `get_scaffold_for_task`, `_match_call`, `_LUA_GLOBAL_FUNCS` whitelist |
| `runtime_sim.py` | `run_runtime_sim`, `run_phantom_api_final_pass`, `_effective_lua_files` |
| `contract_validator.py` | `build_lua_contract` (symbol existence + arity + case map) |
| `_build_skeleton.py` | skeleton + `render_module_state_block` / `inject_module_state` |
| `ollama_client.py` / `ollama_config.py` | native `/api/chat`, `think:false` top-level, KV q8_0, cooldowns |
| `mesh_tasks.py` / `mesh_fetches_*.py` / `mesh_architect.py` | task decomposition, enrichment, architect |
| `_luac_path.py` | resolves full `luac.exe` path |

---

## 10. What the researcher should return

For each theme (P1–P7): (1) technique, (2) evidence (paper/tool/benchmark), (3) fit against
§4 constraints and §7 history, (4) a concrete integration step named by file/function from
§9. Priority is already ordered — **P1 (grammar-constrained decoding)** and **P2 (robust
deterministic Lua repair)** are the two most likely to convert this week's "suspended" into
"success", because every other gate (API correctness, arity, phantom stripping) is already
passing and the sole remaining blocker is *structural Lua syntax validity* that neither the
model nor the fix loop can currently guarantee.
