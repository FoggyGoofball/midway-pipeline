# Pipeline Failure Analysis — 2026-06-03

**Run Timestamp:** 14:35:09 – 15:58:09 (≈23 minutes)
**Feature:** Build Skeeball Attraction (single-file `skeeball.lua`)
**Result:** ❌ FAILED (Circuit breaker tripped after 3 retries)

---

## 🎯 Root Cause (Primary)

### The `NARROW` → `TOO_BROAD` Scope Gate Override

The **Scope Gate** (LLM-powered intent classifier) correctly returned:

```
[VERDICT: NARROW]
```

This was the right answer — the skeeball attraction is a contained, single-file request.

**However, the Lead Producer immediately overrode this verdict** at `mesh_fetches.py:587-593`:

```python
if final_verdict in ("NARROW", None) and getattr(ctx, '_scope_mode', 'GENERAL') == "NEW_ATTRACTION":
    print(f"\n  [Lead Producer] Scope override: {final_verdict} → TOO_BROAD "
          f"(scope classifier identified a NEW_ATTRACTION request).")
    final_verdict = "TOO_BROAD"
```

**The code assumes all `NEW_ATTRACTION` scope classifications require a blueprint.** This logic is flawed because:

1. The Scope Gate's chain-of-thought analysis already *correctly* determined NARROW — the feature is simple enough
2. The override discards the LLM's nuanced evaluation in favor of a hardcoded rule
3. This forces blueprint generation (which creates 4-10 tasks) for features that could be handled as 1-3 tasks

**The override cascade looked like this:**
```
Scope Gate says NARROW → Lead Producer overrides to TOO_BROAD
  → Blueprint generated (9 tasks)
  → File-Linearizer chains them all sequentially
  → 9 waves, each trying to SEARCH/REPLACE the SAME file
  → Most SEARCH blocks fail because file content drifted between waves
  → Fix loop cycles 1-4 try to repair but keep re-introducing same bugs
  → Circuit breaker trips on task_1
```

---

## 🔴 Secondary Root Cause: Single-File Race Condition

The Wave Sorter (`mesh_wave_sorter.py`) correctly serializes tasks targeting the same file into sequential waves. However, **sequential execution ≠ correct execution** when each wave's model output is hallucinating file state:

- **Wave 1** generates `OnLoadStatic()` content
- **Wave 2** tries to SEARCH/REPLACE content that doesn't match actual file (file was scaffolded by `mesh_fetches_blueprint.py:728-738`, but the model generates SEARCH blocks matching its *own mental model*, not what's on disk)
- **Waves 4-7** all fail with `⚠ SEARCH block not found` because the file content differs from what the model expects

The scaffold system at `mesh_fetches_blueprint.py:728-738` writes:

```lua
local balls = {}
function OnLoadStatic() end
function OnLoad()
    MidwayPhysics.OnStep(function(dt)
        local MOD = AttractionConstants.modifiers
    end)
end
function OnUnload() end
```

But subsequent agents generate SEARCH blocks like:

```
<<<<<<< SEARCH
function OnLoadStatic()
    -- Define static booth and target geometry
    SpawnSharedBooth()
    ...
=======
```

This SEARCH block doesn't match the scaffold (which has an empty `OnLoadStatic()`), so the patch is **silently skipped**. The agent's output is stored as a "synthetic header" but never applied to the actual file.

---

## 🔴 Tertiary Root Cause: Fix Loop Death Spiral

The `[Architect Syntax Fix]` system attempted **4 cycles × 9 tasks = 36 individual model calls** to fix the following persistent violations:

| Violation | Occurrences | Why it kept failing |
|-----------|-------------|-------------------|
| **`MOD` cached at module level** | All 9 tasks × 4 cycles | Each fix agent kept writing `local MOD = AttractionConstants.modifiers` at the top of the file (outside `OnStep`), then the guard flagged it. The fix agent would then move it into `OnLoad()` instead of inside the `OnStep` closure. |
| **`skeeballPhysics` out-of-scope** | Tasks 1-3 | Fix agent kept declaring `local skeeballPhysics` inside `OnLoad()` but then referenced it in `OnUnload()`. The guard correctly flags this as scope violation. But the fix agent kept re-creating the same pattern. |
| **`OnLoadStatic()` missing** | Tasks 4-6 | The agent generated code with `OnLoad()` and `OnUnload()` but omitted `OnLoadStatic()`. |

The fundamental problem: **the model could not simultaneously satisfy all three Anti-Pattern rules** (no module-level spawns, no cached MOD, module-level variable visibility) because the rules conflict when applied to sequentially patched single-file tasks.

---

## 📋 Full Failure Cascade Timeline

```
[14:35:09] Intent Classifier: MODIFICATION
[14:35:29] Scope Classifier: NEW_ATTRACTION, target=skeeball
[14:35:29] Context Extract: matched 'Skeeball', cross-cut '3. Dual-Currency Economy'
           ✓ Model swapped to phi3:14b

[14:35:30] Scope Gate (phi3:14b): Correctly returns [VERDICT: NARROW] ✓
[14:35:30] Lead Producer: Overrides NARROW → TOO_BROAD ← FIRST FAILURE ❌
           (Hardcoded rule at mesh_fetches.py:587)

[14:37:33] Blueprint Generation (phi3:14b): 9 tasks created
           File constraint: attractions/skeeball/skeeball.lua
           ✓ Blueprint appears reasonable

[14:43:30] Blueprint Gate: Auto-approved (AUTO_APPROVE_GATES=True)
[14:43:30] Scaffold written to skeeball.lua ✓

[14:43:30] Director (llama3.1:8b): Decomposes into 5 tasks
           Director Guard: Injected mandatory modifier task → 5 tasks
           File-Linearizer: Chained 4 tasks sequentially
           ⚠ Flagged as dirty

[14:48:42] Blueprint Enricher: 9 enriched tasks with full DAG metadata
[14:49:57] Architect Design Pass (qwen2.5-coder:7b): ✓ Design doc created

[14:50:58] Phase 4: Mesh Execution — 9 Tasks, 9 Waves
           Wave 1 (task_1): Generated OnLoadStatic() — applied to file ✓
           Wave 2 (task_2): SEARCH block not found — skipped ⚠
           Wave 3 (task_3): Generated UpdateGame() — applied ✓  
           Wave 4 (task_4): SEARCH block not found — skipped ⚠
           Wave 5 (task_5): SEARCH block not found — skipped ⚠
           Wave 6 (task_6): SEARCH block not found — skipped ⚠
           Wave 7 (task_7): SEARCH block not found — skipped ⚠
           Wave 8 (task_8): Applied OnStep/OnUnload — collision with task_3's output ✓ 
           Wave 9 (task_9): Applied ball counter patch ✓

[15:12:28] Pre-Flight: 
           9× ❌ static cache of MOD
           9× ❌ OnLoadStatic() missing
           ⛔ Syntax error in skeeball.lua (line 2)
           ⛔ 18 runtime error(s) detected

[15:12:35] → Arch Fix Round 1 (qwen2.5-coder:7b) — tasks 1-3
[15:17:20] → Arch Fix Round 2 — tasks 4-6
[15:19:21] → Arch Fix Round 3 — tasks 7-9
           Still 9 violations remaining

[15:25:58] → Review-Fix Cycle 1: FAIL
            → Fix cycle: 6 agent calls
[15:37:15] → Review-Fix Cycle 2: FAIL
            → Fix cycle: 5 agent calls
[15:46:21] → Review-Fix Cycle 3: FAIL
            → Fix cycle: 5 agent calls
[15:50:05] → Review-Fix Cycle 4: FAIL

[15:50:05] ⛔ CIRCUIT BREAKER TRIPPED — task_1 failed 3 times

[15:57:33] Phantom API Gate: Missing economy hook ❌
[15:58:09] Lead Producer Post-Mortem: "TOO_BROAD" ← ironically confirms the wrong verdict
[15:58:09] Pipeline Complete — FAILED
```

---

## 🔧 Remediation Recommendations

### 1. Fix the Scope Gate Override Logic (`mesh_fetches.py:587`)

The automatic `NARROW → TOO_BROAD` override for `NEW_ATTRACTION` should be removed or made conditional:

```python
# Current (broken):
if final_verdict in ("NARROW", None) and getattr(ctx, '_scope_mode', 'GENERAL') == "NEW_ATTRACTION":
    final_verdict = "TOO_BROAD"  # ← forces blueprint even for simple features

# Better approach: trust the scope gate for simple single-file features
# Only override when the LLM specifically requested "multi-file" or
# when the feature explicitly requires cross-cutting concerns
```

### 2. Single-File Task Consolidation (`mesh_wave_sorter.py`)

When all tasks target **the same file**, consolidate them into **a single monolithic task** instead of 9 sequential waves. The current approach creates a patch race condition that cannot recover.

### 3. Anchor-Based Patching (Blueprinted in `mesh_architect.py`)

The Architect pass (`mesh_architect.py`) already defines `task_anchors` — deterministic comment markers that downstream agents can SEARCH for. This feature should be **fully implemented** so that:

- Task 1 writes the scaffold with `-- [TASK_2_INSERT_HOOK]` markers
- Task 2+ use exact SEARCH blocks matching these markers (not hallucinated file state)
- This eliminates the "SEARCH block not found" failure mode entirely

### 4. Single-Task Condition

Add a "monolithic mode" when the Director determines a single-file task is simple enough:

- If all tasks target 1 file AND total complexity is low → collapse to 1 task
- The single task implements ALL 4 lifecycle hooks in one model call
- Skip the wave sorter entirely for this case

---

## 📊 Key Metrics

| Metric | Value |
|--------|-------|
| Total pipeline duration | ~23 min |
| Model calls (all types) | ~40 |
| Successful SEARCH/REPLACE patches | 3/9 (33%) |
| Failed SEARCH/REPLACE patches | 4/9 (44%) |
| Fix loop cycles attempted | 4 |
| Circuit breaker tripped on | task_1 |
| Syntax errors in final file | 3 |
| Runtime errors detected | 18 |
