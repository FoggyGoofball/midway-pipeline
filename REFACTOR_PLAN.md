
## Current State (as of commit `5d215af`)

**Monolith**: `midway-pipeline/pipeline.py` — 4,489 lines
**Tests**: 74 characterization tests in `midway-pipeline/tests/` — **all passing**
**Git tags**: `pre-refactor-baseline`, `v0.2-characterization-tests`

### What Has Been Completed (Phases 1–4)

- ✅ Full line-level inventory of every function, class, constant, global variable (`pipeline_inventory.json`)
- ✅ Cross-reference map: constants → functions
- ✅ Import dependency map
- ✅ Global mutable state documented
- ✅ 74 tests across 12 modules: OffloadStore, Signal Parsing, Memory Ledger TOC, GDD Extraction, File References, Ollama Client (mocked HTTP), FETCH handler, Checkpoint round-trip, Consensus/MeshSignal, TagSuggester, Domain Resolution, Fingerprint Normalization, Ledger Headers
- ✅ CI-ready: `pytest.ini` + `conftest.py`
- ✅ 3 real bugs discovered & fixed during test writing


---

## Target Architecture (Post-Hardening)

No file shall exceed **1,500 lines**. The orchestration remains **strictly synchronous** (no async/await). All MoE state passes through a single `PipelineContext` Pydantic model.

```
midway-pipeline/
├── pipeline.py               ← ~593 lines (thin orchestrator + config constants)
├── __init__.py               ← Re-exports PipelineContext
├── models.py                 ← SignalType, MeshSignal, Task, PipelineContext, OrchestrationConfig
├── signals.py                ← Signal parsing + regex patterns
├── domain_registry.py        ← Agent names + runtime model resolution
├── ollama_client.py          ← HTTP client + context-tiered model parsing
├── paging_kernel.py          ← PagingController + dynamic page limits + ghost buffers
├── token_budget.py           ← TokenBudget class
├── offload_store.py          ← OffloadStore class
├── checkpoint.py             ← save/load/list checkpoints
├── file_references.py        ← FileReferenceCache
├── ledger.py                 ← Memory ledger + fingerprint normalization
├── context_extractor.py      ← Project context extraction
├── gdd_extractor.py          ← GDD section parsing
├── tagsuggester.py           ← TagSuggester class
├── fetch_handler.py          ← FETCH signal handler + page-out
├── mesh_loops.py             ← run_fetches() + run_tasks() — 1312 lines
├── mesh_finalize.py          ← run_code_merge() + review/consensus — 708 lines
├── _pipeline_helpers.py      ← Doc cache, helpers, file tools — 217 lines
├── _mesh_api.py              ← REST API mesh work queue — 213 lines
├── _prompts.py               ← All 15 system prompts — 350 lines
├── _finalize_preflight.py    ← Pre-compilation file sync + SEARCH/REPLACE sanitization — 196 lines
├── _finalize_conflicts.py    ← VETO/OBJECT mediation — 86 lines
├── _finalize_review.py       ← Review-fix loop + Architect fix — 450 lines
├── _domain_sandbox.py        ← Domain enforcement — 182 lines
├── pipeline_session.py       ← SessionManager — 312 lines
├── pipeline_stream.py        ← Stream generator — 208 lines
├── pipeline_stream_server.py ← SSE HTTP server — 379 lines
└── tests/                    ← Existing 80 tests (untouched)
```

---

## Shared Data Contract (`models.py`)

This is the **mandatory** import for every extracted module. All state flows through these Pydantic models.

```python
from __future__ import annotations
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class SignalType(str, Enum):
    QUERY = "QUERY"
    DELEGATE = "DELEGATE"
    RESULT = "RESULT"
    APPROVE = "APPROVE"
    REVISE = "REVISE"
    VETO = "VETO"
    OBJECT = "OBJECT"
    RECOURSE = "RECOURSE"
    CONSULT = "CONSULT"


class MeshSignal(BaseModel):
    type: SignalType
    target: str
    content: str
    source: str
    timestamp: datetime = Field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type.value,
            "target": self.target,
            "content": self.content,
            "source": self.source,
        }


class ConsensusResult(BaseModel):
    verdict: str  # "APPROVED" | "CONFLICT" | "REVISE"
    merged_code: str
    explanation: str
    warnings: List[str] = []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "verdict": self.verdict,
            "merged_code": self.merged_code,
            "explanation": self.explanation,
            "warnings": self.warnings,
        }


class TaskDescriptor(BaseModel):
    """What one expert is asked to do."""
    id: str
    agent: str
    domain: str
    prompt: str
    signals: List[MeshSignal] = []


class PipelineContext(BaseModel):
    """The single authoritative state bag passed sequentially through all experts.
    
    This is how the highly interconnected MoE features communicate across file boundaries.
    No async/await — purely synchronous state threading through the entire pipeline.
    """
    project_root: Path
    memory_dir: Path
    session_id: str
    tasks: List[TaskDescriptor]
    global_signals: List[MeshSignal]
    current_task_index: int = 0
    consensus_results: Dict[str, ConsensusResult] = {}
    ollama_endpoint: str = "http://localhost:11434"
```

---

## Phase 4 Extraction Results (Completed 5/4/2026)

**All 12 modules extracted from `pipeline.py` into individual files.** Each module contains its original definition(s) plus backward-compatible aliases in `pipeline.py`. All 74 characterization tests pass without modification.

### Extracted Files

| File | Source Class/Function(s) | Lines |
|------|--------------------------|-------|
| `models.py` | SignalType, MeshSignal, ConsensusResult, TaskDescriptor, PipelineContext | 130 |
| `signals.py` | extract_signals, parse_signal, extract_double_check, get_verdict, SIGNAL_PATTERNS, DOUBLE_CHECK_PATTERN | 148 |
| `domain_registry.py` | ALL_DOMAINS, AGENT_ALIAS_MAP, PERSONA_MAP, resolve_agent_name, get_agent_system, MESH_AGENT_SYSTEM_EXTENSION, LEDGER_MEMORY_RULE | 180 |
| `ollama_client.py` | call_ollama, call_ollama_streamed, OLLAMA_HOST, MODEL, CODER_MODEL, REVIEWER_MODEL, OLLAMA_TIMEOUT, OLLAMA_NUM_CTX, MAX_TOKENS | 210 |
| `token_budget.py` | TokenBudget class | 210 |
| `offload_store.py` | OffloadStore class, get_offload_store() | 130 |
| `checkpoint.py` | save_checkpoint, load_checkpoint, list_checkpoints | 60 |
| `file_references.py` | parse_file_references, fetch_referenced_files, _REFERENCED_FILES_CACHE, set/get cache | 120 |
| `ledger.py` | _normalize_fix_fingerprint, log_to_session_timeline, build_anchor_toc, ledger_toc, ensure_ledger_header, _append_to_ledger | 275 |
| `gdd_extractor.py` | GDD_SECTION_MAP, KEYWORD_TO_SECTION, extract_gdd_sections, search_memory | 115 |
| `tagsuggester.py` | TagSuggester class | 95 |
| `fetch_handler.py` | handle_fetch_signal, read_offloaded_file, handle_read_offloaded_signal, _page_out_context | 170 |

### Remaining Phase 4 Steps (Not Started)

The following higher-risk steps involve deeper restructuring of `pipeline.py` itself and remain as future work:
- 4.13 — PipelineContext state machine
- 4.14 — Extract iteration loops
- 4.15 — Thin pipeline.py to ~800 lines
- 4.16 — Verify tests
- 4.17 — Integration test
- 4.18 — Milestone tag

---

## Phase 4 Status — Completed: Steps 4.1–4.18 ✅ | Remaining: None

### ✅ Completed Extraction (4.1–4.12) + Thinning (4.13–4.16) + Finalization (4.17–4.18)

All 12 modules extracted from `pipeline.py` into individual files, with backward-compatible aliases in `pipeline.py`. All 74 characterization tests pass.

**Current `pipeline.py` size: 356 lines** (reduced from 4,489). The orchestrator now imports from all extracted modules and delegates to `mesh_loops.py` / `mesh_finalize.py` for iteration logic.

**`git tag v1.0-extracted` has been created.**

---

## Phase 4 Extraction Order (Original Plan — 18 Steps)



### Step 4.1 — `models.py` (Pydantic models)
**Lines**: ~80 | **Risk**: None (pure data)
**Actions**:
1. Create `midway-pipeline/models.py` with the Pydantic models above
2. Add `from models import SignalType, MeshSignal, ConsensusResult` to `pipeline.py`
3. Add aliases in `pipeline.py`: `SignalType = models.SignalType` so tests still import from `pipeline`
4. Run: `python -m pytest tests/` — all 74 must pass
5. Commit: "Phase 4.1: Extract models.py"

### Step 4.2 — `signals.py` (signal parsing)
**Lines**: ~150 | **Risk**: Low
**Move**: `extract_signals()`, `parse_signal()`, `extract_double_check()`, `get_verdict()`, `SIGNAL_PATTERNS`, `DOUBLE_CHECK_PATTERN`

### Step 4.3 — `domain_registry.py` (agent names)
**Lines**: ~100 | **Risk**: Low
**Move**: `ALL_DOMAINS`, `AGENT_ALIAS_MAP`, `resolve_agent_name()`, `get_agent_system()`

### Step 4.4 — `ollama_client.py` (HTTP client)
**Lines**: ~200 | **Risk**: Low
**Move**: `call_ollama()`, `call_ollama_streamed()`, `MAX_TOKENS`, `OLLAMA_ENDPOINT`

### Step 4.5 — `token_budget.py` (budget manager)
**Lines**: ~150 | **Risk**: Low
**Move**: `TokenBudgetManager` class

### Step 4.6 — `offload_store.py`
**Lines**: ~100 | **Risk**: Low
**Move**: `OffloadStore` class

### Step 4.7 — `checkpoint.py`
**Lines**: ~150 | **Risk**: Low
**Move**: `PipelineCheckpoint` class

### Step 4.8 — `file_references.py`
**Lines**: ~100 | **Risk**: Low
**Move**: `FileReferenceCache` class

### Step 4.9 — `ledger.py` (memory ledger)
**Lines**: ~250 | **Risk**: Medium
**Move**: `_normalize_fix_fingerprint()`, `collect_ledger_entries()`, `build_ledger_toc()`, `ensure_ledger_header()`, `append_to_ledger()`, etc.

### Step 4.10 — `gdd_extractor.py`
**Lines**: ~80 | **Risk**: None (pure data)
**Move**: `GDD_SECTION_MAP`, `KEYWORD_TO_SECTION`, GDD extraction function

### Step 4.11 — `tagsuggester.py`
**Lines**: ~120 | **Risk**: Low
**Move**: `TagSuggester` class

### Step 4.12 — `fetch_handler.py`
**Lines**: ~200 | **Risk**: Medium
**Move**: `handle_fetch_signal()`, `fetch_file_content()`

### Step 4.13 — PipelineContext state machine
**Lines**: N/A | **Risk**: High
**Action**: Convert all global state (`current_tasks`, `global_signal_list`, etc.) into a single `PipelineContext` instance passed through functions

### Step 4.14 — Extract iteration loops
**Lines**: ~300 | **Risk**: High
**Action**: Extract `run_fetches()`, `run_tasks()`, `run_code_merge()` as pure functions that take `PipelineContext` → return updated `PipelineContext`

### Step 4.15 — Thin pipeline.py to ~800 lines
**Lines**: N/A | **Risk**: High
**Action**: The remaining `pipeline.py` becomes a thin orchestrator that imports from all extracted modules

### Step 4.16 — Verify tests
**Action**: Run all 74 tests against refactored imports — must pass

### Step 4.17 — Integration test
**Lines**: ~100 | **Risk**: New
**Action**: Add `test_full_pipeline_dry_run.py` with mocked LLM

### Step 4.18 — Milestone tag
**Action**: `git tag v1.0-extracted`

---

## Extraction Pattern (Use for Every Step)

For each extraction step, follow this exact pattern:

1. **Create** the new file with the extracted code
2. **Add** `from new_file import ...` to `pipeline.py`
3. **Keep** old definitions as aliases (e.g., `extract_signals = signals.extract_signals`)
4. **Run** `python -m pytest tests/` — all 74 tests must still pass
5. **Commit** with message format: "Phase 4.N: Extract module_name"

### Critical Rule: Import Aliases Before Removing Originals

Never remove the original definition from `pipeline.py` until you've verified:
1. The alias works (tests pass)
2. All internal references in `pipeline.py` are updated
3. All test imports still resolve

---

## How to Verify Tests

```bash
cd midway-pipeline
python -m pytest tests/ -v --tb=short
```

Expected: **74 passed, 0 failed**

---

## Key Constraints (Do Not Violate)

- **No async/await** — the MoE must remain strictly synchronous
- **No file > 1,000 lines**
- **All state passes through Pydantic models** (never through globals after Phase 4.13)
- **Tests must pass after every commit** — no "I'll fix it later"

---

## Git Log (History)

```
5d215af  2026-05-04  Phase 2 complete: 74/74 characterization tests pass  (tag: v0.2-characterization-tests)
[earlier]              Initial baseline  (tag: pre-refactor-baseline)
```
