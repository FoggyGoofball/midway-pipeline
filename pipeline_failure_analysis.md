# Pipeline Failure Analysis — Skeeball Run (2026-06-03)

## Executive Summary

The pipeline accepted a single-file request ("build me the skeeball attraction... contained within a single skeeball.lua") and catastrophically decomposed it into **7 independent Lua Scripting tasks**, **all targeting the same file**. Each task independently generated a complete (and mutually incompatible) version of `skeeball.lua`. The review-fix loop then burned **4 cycles × 7 agents = 28 LLM calls** trying to fix contradictory static-guard violations, hallucinated API names, and scope conflicts — all of which were unsalvageable because the fundamental architecture (7 independent tasks writing the same file) was broken from the start.

**Total runtime:** ~96 minutes. **Result:** FAILED. **Useful output:** 0 lines of working code.

---

## 8 Distinct Failure Modes

### 🔴 FAILURE 1: Single-File Decomposition → 7 Mutually Destructive Tasks (FATAL)

**Observed:** The user said *"the entire game is contained within a single skeeball.lua"*. The Director decomposed this into 7 tasks, ALL targeting `attractions/skeeball/skeeball.lua`:

```
Task 1: Define skeeball game rules and scoring system → skeeball.lua
Task 2: Implement ball launch on player input → skeeball.lua
Task 3: Create object pools → skeeball.lua
Task 4: Register MidwayPhysics.OnStep → skeeball.lua
Task 5: Track remaining balls with counter → skeeball.lua
Task 6: Read modifiers every frame → skeeball.lua
Task 7: Award tickets on score events → skeeball.lua
```

**Root Cause:** The pipeline has no concept of "this is a single-file target." The `[File-Linearizer]` saw 7 tasks for 1 file and chained them "sequentially" — but during MESH execution (Phase 4), **all 7 tasks ran in a single wave** (`Wave 1: Task 1–7`), each overwriting the file with a different complete-but-incompatible version.

**Evidence from dump:**
```
[DAG] Sorted 7 task(s) into 1 wave(s):
  Wave 1: Task 1 [Lua], Task 2 [Lua], ..., Task 7 [Lua]
[Merge] SKIPPED — shared-file merge abolished (real-time patch mode).
```

**Fix:** The pipeline needs a file-level dependency analyzer. When N tasks target the same file, they MUST be either (a) merged into a single task with a prompt that combines all sub-tasks, or (b) run in strict sequence where each task appends/patches the output of the previous one, or (c) the Director must produce one task per file, not one task per sub-feature.

---

### 🔴 FAILURE 2: Static Guard vs. Bridge Contract Contradiction (CATCH-22)

**Observed:** The `_preflight_static.py` guard C14 (lines 746–818) flags:
```
**Rule:** static cache of modifiers at module/OnLoad scope (`MOD`)
```

**But the official bridge contract** (`../midway/docs/engine_lua_bridge_contract.md`, line 94) SHOWS this exact pattern as correct:
```lua
MidwayPhysics.OnStep(function(dt)
    local MOD = AttractionConstants.modifiers  -- shown in OFFICIAL docs
    -- game logic here
end)
```

**Root Cause:** The static guard uses a naive regex that matches ANY `local MOD = AttractionConstants.modifiers` regardless of whether it's inside `OnStep` closure (correct per docs) or at module/OnLoad scope (actually bad). The guard doesn't distinguish between:
- `local MOD = ...` inside `OnStep(dt)` closure → **CORRECT** (lives every frame)
- `local MOD = ...` at module level or inside `OnLoad()` → **BUG** (cached at load)

**Evidence:** Every single fix cycle tried to "fix" the MOD declaration but the model kept producing `local MOD = AttractionConstants.modifiers` inside `OnStep` — which is CORRECT per the docs, but the guard flagged it every time.

**Fix:** The static guard needs scope-aware detection. Track indentation depth / function nesting to only flag `local MOD = AttractionConstants.modifiers` when it appears OUTSIDE the `MidwayPhysics.OnStep(function(dt) ... end)` closure.

---

### 🔴 FAILURE 3: Circuit Breaker on Wrong Entity

**Observed:** The circuit breaker tripped on `task_1` with message:
```
⛔ [CIRCUIT BREAKER TRIPPED] Task task_1 has failed 3 times.
```

**Root Cause:** The retry counter at line 801 of `_finalize_review.py` increments `ctx.retry_counts[tid]` for EVERY review-fix cycle, regardless of whether the task was the source of the failure. Task 1 (just defining game_rules and scoresystem tables) was never the problem — but it accumulated strikes because it was included in every fix cycle.

**Breakdown of task_1's 3 "failures":**
1. Initial execution (clean — produced valid tables)
2. Fix cycle 1 (model added `local MOD` to satisfy reviewer — flagged by static guard)
3. Fix cycle 2 (model removed `local MOD` — but static guard found it in a different location)
4. Fix cycle 3 (model regenerated file — introduced new errors)

**Fix:** Circuit breaker should account for the TYPE of failure. Preflight static guard violations on task_1 should not count as "task_1 failures" when the real issue is a guard defect (Failure #2 above).

---

### 🔴 FAILURE 4: Merge Strategy Abolished — No File Assembly

**Observed:**
```
[Merge] SKIPPED — shared-file merge abolished (real-time patch mode).
```

**Root Cause:** The merge step was explicitly turned off. With 7 independent task outputs all representing different pieces of the same file, there was no mechanism to combine them into a single coherent `skeeball.lua`.

**What the merged file looked like (from luac errors):**
```
skeeball.lua line 2: syntax error  (task_1's output starts)
skeeball.lua line 2: syntax error  (task_2's output overwrites task_1 mid-stream)
skeeball.lua line 4: syntax error  (task_3's output overwrites again)
skeeball.lua: OK                  (task_4's output is syntactically valid but semantically wrong)
```

**Fix:** Either implement a proper file-level merge (concatenating sections with clear ownership boundaries) OR restore single-task-per-file decomposition. Real-time patch mode needs AST-aware merging to work correctly.

---

### 🔴 FAILURE 5: Phantom API Hallucination Death Spiral

**Observed:**
```
[Static Guard] ❌ Task task_7 [Lua]: bare 'PoolAcquire()' missing MidwayPhysics. prefix
[Static Guard] ❌ Task task_7 [Lua]: bare 'PoolFree()' missing MidwayPhysics. prefix
[Static Guard] ❌ Task task_7 [Lua]: bare 'ApplyImpulse()' missing MidwayPhysics. prefix
[Static Guard] ❌ Task task_7 [Lua]: bare 'PoolReturn()' missing MidwayPhysics. prefix
```

These persisted across ALL 4 review cycles despite explicit instructions to add the `MidwayPhysics.` prefix.

**Root Cause:** The contract validator (`contract_validator.py`) technically catches these, but the fix loop has structural limitations:
1. The ARCHITECT_FIX_SYSTEM prompt processes ALL 7 tasks at once in a single model call
2. The fix model's context window fills with pre-flight error text, leaving limited room for actual code
3. The fix model tends to regenerate the entire file rather than patching specific calls
4. When regenerating, it reproduces the same hallucinated API names from its training data

**Evidence:** In fix cycle 3, task_7's output still had `PoolAcquire(...)` while also having `MidwayPhysics.DestroyBody(...)` — the model inconsistently applied the prefix.

**Fix:** (A) Use targeted find-and-replace patches instead of full-file regeneration for simple namespace fixes. (B) The preflight "Fix G" auto-patcher (`_preflight_static.py` lines 478–558) should be extended to cover pool functions, not just spawn arg counts.

---

### 🔴 FAILURE 6: Context Collapse in Review

**Observed:**
```
[VRAM Guard] Review input oversized (29381 chars) — collapsing 2348 chars from code body
```

**Root Cause:** The reviewer prompt construction in `_finalize_review.py` (lines 193–388) collapses the code body when the total exceeds a hard cap. When the code body is collapsed, the reviewer cannot see the actual code and must guess at violations from task titles and pre-flight error headers.

**Evidence in review output:** The reviewer's "Issues" sections contain only the exact pre-flight error labels repeated — not any NEW issues discovered by reading the code. This is because the code wasn't visible.

**Worse:** The collapsed text included a `[SYSTEM KERNEL: ...]` message saying the code was truncated, but the reviewer model treated this as part of the code to review, not as a truncation notice.

**Fix:** When code cannot fit in context, skip LLM review entirely and let the pre-flight static guards be the sole gate. LLM review of collapsed code is worse than no review — it hallucinates false violations and wastes fix cycles.

---

### 🔴 FAILURE 7: The Architect Fix Loop is Counter-Productive

**Observed:** The syntax fix loop ran 3 iterations, each time asking a SINGLE model call to fix ALL 7 task outputs simultaneously:
```
[Architect Syntax Fix [Lua]] Calling Ollama (qwen2.5-coder:7b)
  → produces fixes for task_1, task_2, task_3
  → produces fixes for task_4, task_5, task_6
  → produces fixes for task_7
```

**Root Cause:** Each fix cycle called the model ONCE for N tasks. The model had to:
1. Read all 7 failing outputs
2. Understand which errors belonged to which task
3. Generate 7 corrected outputs
4. Ensure they remained compatible with each other

This is a harder problem than the original code generation. The model consistently failed, often replacing real code with stubs and losing work from previous cycles.

**Evidence from dump:**
- Fix cycle 1 output: Only touched 3 of 7 tasks, the rest were unchanged
- Fix cycle 2 output: task_1's output was replaced with an empty stub
- Fix cycle 3 output: task_1 was regenerated from scratch, losing the original game_rules definition

**Fix:** Either (A) fix each task individually (7 model calls, each with focused context) or (B) feed the ORIGINAL task output plus the SPECIFIC error back to the original task agent with a targeted fix prompt. The current "fix everything at once" approach fails.

---

### 🔴 FAILURE 8: AUTO_APPROVE_GATES=True Silenced Human Oversight

**Observed:**
```
[Blueprint Gate] Blueprint auto-approved (AUTO_APPROVE_GATES=True).
[Blueprint Gate] ✓ Design auto-approved (AUTO_APPROVE_GATES=True).
```

Both the Blueprint Gate and the Architect Design Gate were skipped. A human reviewing the blueprint would have immediately spotted:
- 7 tasks targeting the same file (impossible to merge)
- Task dependencies that were ignored (all 7 ran in wave 1 with no ordering)
- Missing tasks for user input handling (aim, power)
- No mention of how the 6-ball-per-round mechanic would be implemented

**Fix:** AUTO_APPROVE_GATES should require at minimum a deterministic pass/fail check based on task diversity. If N tasks > 1 target the same file, BLOCK and require human review.

---

## Secondary Issues (Performance & Reliability)

### ⚠️ Model Temperature Inconsistency
The pipeline uses the SAME model (`qwen2.5-coder:7b`) for code generation AND syntax fixing AND review. This means:
- Fixing: "I wrote this code but it has a bug" — same model that wrote the bug tries to fix it
- Reviewing: Same model reviews its own output

**Fix:** Use separate models (or at minimum different temperature settings) for generation vs. review.

### ⚠️ No Input Handling Implementation
The user specifically requested "the ability for the user to **aim** and **set power** on the balls." The Director's task breakdown omits this entirely. None of the 7 tasks cover player input processing.

### ⚠️ VRAM Thrashing
The pipeline loaded and evicted:
- `llama3.1:8b-instruct-q4_K_M` (7.0 GB) — loaded 4 times
- `phi3:14b` (9.0 GB) — loaded 2 times  
- `qwen2.5-coder:7b` (6.6 GB) — loaded ~30+ times

Each model swap takes 10-30 seconds. Across the 96-minute run, approximately 20+ minutes was spent on model loading/unloading.

---

## Recommended Fix Priority Order

1. **IMMEDIATE** — Fix the static guard C14 scope detection (MOD variable). This false positive is the single largest waste of fix cycles.
2. **IMMEDIATE** — Add a single-file task deduplication rule: if 2+ tasks target the same `.lua` file, merge them into one task.
3. **HIGH** — Restore file merge or implement task concatenation for single-target files.
4. **HIGH** — When code body is collapsed for review, skip LLM review and use only deterministic guards.
5. **MEDIUM** — Implement targeted find-and-replace for namespace fixes (MidwayPhysics. prefix) instead of full-file regeneration.
6. **MEDIUM** — Split the monolithic architect fix call into per-task fix calls.
7. **LOW** — Implement per-task model temperature differentiation.
8. **LOW** — Add player input handling to required task checklist for attractions.

---

## Appendix: Pipeline Source Files Examined

| File | Role |
|------|------|
| `_finalize_preflight.py` | Pre-flight checks, syntax validation, architect fix loop |
| `_preflight_static.py` | 17 static pattern guards (MOD caching, phantom APIs, arg counts, etc.) |
| `_finalize_review.py` | Review-fix loop (up to 4 cycles), circuit breaker, context collapse |
| `_helpers_exec.py` | LLM config, intent classification, project state |
| `contract_validator.py` | Contract-driven API validation against bridge contract |
| `models.py` | Pydantic data models for pipeline context |
| `pipeline.py` | Main orchestrator |
| `pipeline_stream_server.py` | HTTP/SSE server |
| `../midway/docs/engine_lua_bridge_contract.md` | Authoritative Lua bridge contract |
