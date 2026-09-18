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
