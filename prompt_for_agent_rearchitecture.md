# Pipeline Re-Architecture — New Agent Prompt

> **Created:** 2026-05-02  
> **Purpose:** Feed this prompt verbatim to a new coding agent to rebuild the 3 features lost during `pipeline.py` evolution.

---

## Context

You are inheriting a Python pipeline at `c:\Users\Admin\source\repos\midway\pipeline.py` (3,430 lines). It orchestrates a multi-agent mesh consensus system for game development (Midway to Nowhere). The pipeline works — but 3 critical features were lost during incremental file evolution. Your job is to rebuild them.

**Read these files first:**
1. `docs/pipeline_master_checklist.md` — full spec of every phase
2. `docs/pipeline_agent_todo.md` — the 3 tasks with pseudocode and design specs
3. `docs/pipeline_anchor_index.md` — line anchors for navigation

---

## Task 1: Intra-Agent Reasoning Gate (HIGH Priority)

**What's missing:** After a coder agent produces output in Phase 4 (Mesh Execution), there's no self-review step before the output reaches signal processing and disk-write.

**Where to insert:** In the `execute_task()` function (around L1943-1947 in current line numbers), right after `call_ollama()` returns and before `task.output = output` is set.

**Spec (copied from `docs/pipeline_agent_todo.md`):**
- If `agent_key` is in `REASONING_GATE_DOMAINS` (C++, Lua, PHYS):
  - Build a gate prompt from the agent's output + task spec
  - Call `call_ollama()` with a new `REASONING_GATE_SYSTEM` prompt and `REASONING_MODEL`
  - Extract either the "CONFIRMED" output or the "REVISED" version
  - Log the gate activity to session timeline
- Add these new constants:
  - `REASONING_GATE_DOMAINS = {"C++", "Lua", "PHYS"}`
  - `REASONING_GATE_SYSTEM` (see agent_todo.md for exact prompt content)

**Cost:** +1 LLM call per gated task. With 32K ctx and sequential execution, ~5-10s per task.

---

## Task 2: Tag System Auto-Detection (HIGH Priority)

**What's missing:** After pipeline completion (Phase 8), there's no post-processing step that suggests `[Stable Core Concept]` or `[Likely Regression]` tags based on session history.

**Where to insert:** At the end of `run_mesh_pipeline()`, right before the final return statement.

**Spec (from `docs/pipeline_agent_todo.md`):**
- Create a `TagSuggester` class:
  - `analyze(session_timeline_path, run_id)` → list of suggestion strings
  - `suggest_stable(area, run_ids)` → `"[Stable Core Concept — Suggested] (run_ids: ...)"`
  - `suggest_regression(area, run_ids)` → `"[Likely Regression — Suggested] (run_ids: ...)"`
- Detection logic:
  - **Stable:** 3+ consecutive APPROVED runs on same code area, zero vetos
  - **Regression:** 2+ fix cycles exhausted on same code area, any BLOCKED trip
- Output to `docs/pipeline_master_checklist.md` (append to Tag System section) and session timeline

---

## Task 3: Explicit KV Cache Configuration (LOW Priority)

**What's missing:** The `call_ollama()` function's options dict doesn't explicitly document KV cache quantization.

**Where to change:** In `call_ollama()` (around L1510 area), update the `options` dict.

**Spec (from `docs/pipeline_agent_todo.md`):**
```python
"options": {
    "num_ctx": OLLAMA_NUM_CTX,         # 32768
    "num_predict": MAX_TOKENS,         # 12000
    "use_mmap": True,                  # memory-map model weights
    # KV cache q8_0 is default for Ollama when VRAM is constrained
    # This enables 32K context window on Steam Deck's 12GB
}
```

---

## Important Invariants (Must NOT Break)

| # | Invariant | Why |
|---|-----------|-----|
| 1 | No `async def` / `await` / `import asyncio` | Strictly sequential |
| 2 | No `import threading` / `import multiprocessing` | No parallelism |
| 3 | `keep_alive: "60m"` in every Ollama call | Model stays hot on Steam Deck |
| 4 | Primary models: 7-8B (qwen2.5-coder:7b, llama3.1:8b) | Micro-models only for syntax/routing |
| 5 | `OLLAMA_NUM_CTX = 32768`, `MAX_TOKENS = 12000` | 32K context, 12K output |
| 6 | KV cache q8_0 (implied by Ollama) | Enables 32K ctx in 12GB VRAM |
| 7 | Block-aware truncation only | Never blind head/tail slice |
| 8 | Tag suggestions always `[Suggested]` suffix | Never auto-apply authoritative tags |

## Files to Modify

| File | Change |
|------|--------|
| `pipeline.py` | Add Reasoning Gate, TagSuggester, KV cache options |
| `docs/pipeline_master_checklist.md` | Update per-phase checklists |
| `docs/pipeline_anchor_index.md` | Add new function/class line references |
| `docs/pipeline_agent_todo.md` | Mark completed items as `[x]` |

## Verification

After implementation:
1. `python -c "import py_compile; py_compile.compile('pipeline.py', doraise=True)"` — must pass
2. Read the master checklist and confirm all new features are documented
3. Run `python pipeline.py "test"` — should boot up (will fail on Ollama connection, but that's OK)
