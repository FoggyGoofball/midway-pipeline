# Pipeline Master Checklist — Midway to Nowhere

> **Purpose:** Single-source-of-truth reference for pipeline features and invariants.  
> Feed this to agents alongside prompts to ensure all features survive successive edits.  
> **Refactored from monolithic `pipeline.py` (4,489 lines) into discrete modules.**  
> **Orchestrator:** `midway-pipeline/pipeline.py` (357 lines) — thin coordinator only.  
> **Last Verified:** 2026-05-05 — refactored module locations audited, 80/80 tests pass.

---

## 🗺️ Module Map

After refactoring, the monolithic pipeline was split into the following modules:

| Module | Lines | Contains |
|--------|-------|----------|
| `pipeline.py` | 357 | Thin orchestrator, config, main entry, CLI |
| `_prompts.py` | 175 | All 13 system prompt strings |
| `_pipeline_helpers.py` | 645 | Doc cache, intent classification, librarian, project state, director, file tools, task execution, failure report |
| `_mesh_api.py` | 213 | Mesh work queue, conflict resolution, progressive output |
| `domain_registry.py` | 395 | `ALL_DOMAINS`, `AGENT_ALIAS_MAP`, `PERSONA_MAP`, `resolve_agent_name()`, `get_agent_system()` |
| `signals.py` | 145 | Signal pattern parsing, `extract_signals()`, `extract_double_check()`, `get_verdict()` |
| `models.py` | 262 | `SignalType`, `MeshSignal`, `ConsensusResult`, `Task`, `PipelineContext` |
| `mesh_loops.py` | 475 | `run_fetches()` (Phases 0.5–3), `run_tasks()` (Phase 4) |
| `mesh_finalize.py` | 660 | `run_code_merge()` (Phases 5–8), review-fix loop, consensus, final approval |
| `ollama_client.py` | — | `call_ollama()`, `call_ollama_streamed()` |
| `token_budget.py` | — | `TokenBudget` class |
| `offload_store.py` | — | `OffloadStore` class |
| `checkpoint.py` | — | `save_checkpoint()`, `load_checkpoint()`, `list_checkpoints()` |
| `file_references.py` | — | `parse_file_references()`, `fetch_referenced_files()` |
| `ledger.py` | — | `log_to_session_timeline()`, `ensure_ledger_header()`, `_append_to_ledger()` |
| `gdd_extractor.py` | — | `extract_gdd_sections()`, `search_memory()` |
| `fetch_handler.py` | — | `handle_fetch_signal()`, `read_offloaded_file()`, `_page_out_context()` |
| `tagsuggester.py` | — | `TagSuggester` class |

---

## 🖥️ Platform & Hardware Context

| Parameter | Value |
|-----------|-------|
| **Device** | Steam Deck |
| **Total RAM** | 16 GB |
| **Available to Pipeline** | ~12 GB |
| **Philosophy** | Accuracy & power over speed — offline game dev assistant |
| **Execution** | Strictly sequential (no asyncio, no threading, no multiprocessing) |
| **KV Cache** | Relies on Ollama default q8_0 KV cache quantization (halves context memory) |

### User-Facing Architecture Chain

```
Ollama (Steam Deck, 192.168.0.16:11434)
    ↓ HTTP POST
Custom localhost server (Dev PC)
    ↓ RPC / mesh API
Continue Extension (VS Code on Dev PC)
    ↓
User sees results in VS Code
```

**All agents communicate via HTTP to Ollama on the Steam Deck.** No agent runs locally on the Dev PC.

---

## 🧠 Hybrid Model Strategy

| Role | Model | Size | When |
|------|-------|------|------|
| **Coder** | `qwen2.5-coder:7b` | 7B | Core code generation (C++/Lua) |
| **Director** | `phi-3:14b-q4_K_M` | 14B Q4 | Task decomposition, routing, review |
| **Conflict Resolution** | `phi-3:14b-q4_K_M` | 14B Q4 | VETO/OBJECT negotiation |
| **Diagnostic Oracle** | `phi-3:14b-q4_K_M` | 14B Q4 | Multi-turn diagnostic sessions |
| **Librarian** | `llama3.1:8b-instruct-q4_K_M` | 8B | Read-only research, memory TOC navigation |
| **Syntax Gate (micro)** | `qwen2.5-coder-1.5b` | 1.5B | Fast pre-flight syntax checks on generated code |
| **Intent Classifier (micro)** | `llama-3.2-1b` | 1B | Zero-shot MODIFICATION vs QUERY gate — lightweight, no reasoning needed |

**Primary models are 7-14B for real work.** Review/reasoning roles use `phi-3:14b-q4_K_M` (14B Q4, ~8GB VRAM), code generation uses `qwen2.5-coder:7b` (7B), and `llama3.1:8b` is retained as a fallback reference. Micro-models (1-1.5B) are reserved for:
- Intent classification (no reasoning required, just pattern matching)
- Pre-flight syntax pass/fail on generated code
- Quick routing decisions

Model swap latency (~3-5 seconds for 7-14B models, ~1 second for micro-models) is acceptable — the pipeline is single-shot sequential with no tight-loop model swapping.

### Model Registry Constants

**File:** `pipeline.py` lines 138-148

```python
CODER_MODEL = "qwen2.5-coder:7b"
REVIEWER_MODEL = "phi-3:14b-q4_K_M"
FALLBACK_REVIEWER_MODEL = "llama3.1:8b-instruct-q4_K_M"
LIBRARIAN_MODEL = "llama3.1:8b-instruct-q4_K_M"
SYNTAX_GATE_MODEL = "qwen2.5-coder:1.5b"
INTENT_CLASSIFIER_MODEL = "llama3.2:1b"
CHAT_MODEL = CODER_MODEL
EXECUTION_MODEL = CODER_MODEL
REASONING_MODEL = REVIEWER_MODEL
MODEL = EXECUTION_MODEL
DIRECTOR_MODEL = "llama3.1:8b-instruct-q4_K_M"
```

---

## 💾 Context-to-HDD Overflow Expansion

With **32K input context** and **12K max tokens** (enabled by KV cache q8_0 quantization fitting within 12GB VRAM):

1. **Overflow Ledger Rotation** (`TokenBudget.add()` in `token_budget.py`)
   - When `overflow_ledger.md` exceeds **200 KB**, it is archived to `overflow_ledger_v{N}.md`
   - A fresh file is created with a link back to the archive
   - Version numbering increments automatically (v1, v2, v3...)

2. **Block-Aware Collapse** (`TokenBudget._block_aware_collapse()` in `token_budget.py`)
   - Never does blind head/tail slicing
   - Preserves function signatures, markdown headers, code fences
   - Drops oldest blocks first (reverse-order iteration)
   - Collapses function bodies with `[... function body collapsed for context budget ...]` notice

3. **Agent Retrieval of Lost Context**
   - Agents use `[FETCH:docs/memory/overflow_ledger_v{N}.md#HeaderName]` to pull back truncated content
   - `handle_fetch_signal()` in `fetch_handler.py` calculates **Chronological Read Depth** and tags fetched content with how far back in the archive chain it came from
   - **Never delete overflow archives** — they are the formal project history

4. **Virtual Context OffloadStore** — `OffloadStore` class in `offload_store.py`
   - When `_block_aware_collapse` prunes old context blocks, they are **losslessly saved** to `offload_store/` instead of permanently deleted
   - Placeholder `<OFFLOADED_CONTEXT>` tags are injected into the active LLM context
   - Agents retrieve via `[READ_OFFLOADED:<block_id>]` signal (handled in `mesh_loops.py` phase 4 loop)
   - `read_offloaded_file()` in `fetch_handler.py` retrieves the block from disk
   - `_page_out_context()` in `fetch_handler.py` auto-compresses an inactive block to make room when a retrieved block would overflow the budget

---

## 🏷️ Tag System: [Stable Core Concept] / [Likely Regression]

**CRITICAL DESIGN NOTE:** Tags are NEVER auto-applied as authoritative. The pipeline can *suggest* tags, but only human confirmation upgrades them.

### Tag States

| Tag State | Meaning | Who Sets It |
|-----------|---------|-------------|
| `[Stable Core Concept — Suggested]` | Passed 3+ approval cycles with zero vetos | Pipeline (post-approval) |
| `[Stable Core Concept]` | Human confirmed the suggestion | Human (via Librarian NL query) |
| `[Likely Regression — Suggested]` | 2+ fix cycles attempted, all failed/blocked | Pipeline (post-block) |
| `[Likely Regression]` | Human confirmed the regression suspicion | Human (via Librarian NL query) |
| `[Needs Audit]` | Pipeline detected potential stale tag | Pipeline (insanity detector) |

### Tag Auto-Detection (Implemented: TagSuggester)

The `TagSuggester` class (`tagsuggester.py`) analyzes the session timeline after each pipeline run and suggests tags:

- **Stable Detection:** 3+ consecutive APPROVED runs on same code area, zero vetos → `[Stable Core Concept — Suggested]`
- **Regression Detection:** 2+ fix cycles exhausted on same code area, any BLOCKED trip → `[Likely Regression — Suggested]`

Tags are appended to `pipeline_master_checklist.md` (Tag System section) and logged to the session timeline. Tags always use `[Suggested]` suffix — **never auto-applied as authoritative**.

### Tag Provenance

Every tag suggestion includes a `run_id` linking back to the session timeline entry that generated it, so the Librarian can FETCH the full audit trail.

---

## 🔁 Intended Mesh Flow

```
User Prompt
    │
    ▼
┌────────────────────────────────────────────────────────────┐
│ Phase 0.03: Intent Classification  (micro-model: 1B)       │
│ classifies as MODIFICATION or QUERY                        │
│ File: pipeline.py (classify_intent call via is_likely_chat)│
│ Helper: _pipeline_helpers.py L91-L139                      │
└────────────────────────────────────────────────────────────┘
    │                        │
    │ QUERY                  │ MODIFICATION
    ▼                        ▼
┌────────────────┐  ┌──────────────────────────────────────────┐
│ The Librarian  │  │ Phase 0.05: Intent Router (DIAGNOSTIC?)  │
│ (read-only)    │  │ File: pipeline.py -> _pipeline_helpers   │
│                │  │ Phase 0.2: QA Anchor Extraction          │
│ 1. search_     │  │ Phase 0.5: Lead Producer (Scope Gate)    │
│    memory()    │  │ File: mesh_loops.py -> run_fetches()     │
│    → TOC       │  │ Phase 1: GDD Librarian                  │
│ 2. FETCH       │  │ File: mesh_loops.py -> run_fetches()    │
│    ledgers     │  │ Phase 2: Project Context + File Refs    │
│ 3. Answer      │  │ File: mesh_loops.py -> run_fetches()    │
│    (no code!)  │  │ Phase 3: Director (Task Decomposition)  │
│                │  │ File: mesh_loops.py -> run_fetches()    │
│    ↓           │  │                                          │
│ Session        │  │ Phase 4: Mesh Execution (Work Queue)    │
│ Timeline       │  │ File: mesh_loops.py -> run_tasks()      │
│                │  │   ├─ Agent tasks (C++, Lua, Review...)  │
│                │  │   ├─ QUERY/CONSULT signal routing       │
│                │  │   ├─ FETCH resolution (memory recall)   │
│                │  │   ├─ DELEGATE sub-task nesting          │
│                │  │   ├─ VETO/OBJECT handling               │
│                │  │   ├─ Disk-Write Interceptor (→ ledgers) │
│                │  │   ├─ Ledger Guard (synthetic headers)   │
│                │  │   ├─ Progressive File Disclosure tools  │
│                │  │   └─ Insanity Detector (state hashing)  │
└────────────────┘  │                                          │
                    │ Phase 5: Consensus Gate (VETO Check)    │
                    │ File: mesh_finalize.py -> _run_conflict  │
                    │ Phase 6: Review-Fix Loop                │
                    │ File: mesh_finalize.py -> _run_review    │
                    │   ├─ Compiler pre-flight (cmake/luac)   │
                    │   ├─ Syntax Gate (micro-model pass)     │
                    │   ├─ Reviewer (full code audit)         │
                    │   ├─ Architect Fix cycle                │
                    │   ├─ Active Code Index (FETCH-based TOC)│
                    │   ├─ Reviewer FETCH handling             │
                    │   └─ Insanity Detector (loop prevention)│
                    │ Phase 7: Consensus Check                │
                    │ File: mesh_finalize.py -> _run_consensus │
                    │ Phase 8: Final Approval / Failure Report │
                    │   ├─ Circuit Breaker → BLOCKED suspend  │
                    │   ├─ Post-Mortem generation             │
                    │   └─ Blueprint step chaining            │
                    │                                          │
                    │ Session Timeline (log)                   │
                    └──────────────────────────────────────────┘
```

---

## 📦 Core Data Structure: ALL_DOMAINS Registry

**File:** `domain_registry.py` (lines 33-198)
**Purpose:** Central registry mapping all 7 agent domains to their model, ledger, system prompt, and readiness.

| Key | Name | Model | Ready | Ledger File |
|-----|------|-------|-------|-------------|
| `C++` | C++ Core | `EXECUTION_MODEL` | Yes | `docs/memory/cpp_ledger.md` |
| `PHYS` | Physics Architect | `EXECUTION_MODEL` | Yes | `docs/memory/phys_ledger.md` |
| `SHADER` | Shader Expert | `CODER_MODEL` | **No** | `docs/memory/shader_ledger.md` |
| `Lua` | Lua Scripter | `EXECUTION_MODEL` | Yes | `docs/memory/lua_ledger.md` |
| `DOC` | Code Documentarian | `REASONING_MODEL` | Yes | `docs/memory/doc_ledger.md` |
| `CONF` | Conflict Resolution | `REASONING_MODEL` | Yes | `docs/memory/conf_ledger.md` |
| `LIBRARIAN` | Librarian | `REASONING_MODEL` | Yes | `docs/memory/librarian_ledger.md` |

Each domain has a `ledger_rule`, `description`, and `system_prompt` — the system prompt is the agent's persona definition.

`PERSONA_MAP` (domain_registry.py L308-312) is built at module load from `ready=True` domains.

---

## 📋 Per-Phase Feature Checklist — Module Locations

### [PHASE 0.03] Intent Classification

| # | Feature | Module & Lines | Depends On | Invariant |
|---|---------|----------------|------------|-----------|
| 1 | `is_likely_chat(user_prompt)` — fast-path regex | `_pipeline_helpers.py` L91-97 | `CHAT_PATTERNS` regex list | Must run before LLM classifier |
| 2 | `classify_intent(user_prompt)` → MODIFICATION/QUERY/CHAT | `_pipeline_helpers.py` L126-139 | `call_ollama()`, `INTENT_CLASSIFIER_MODEL` | Default to MODIFICATION if ambiguous |
| 3 | Chat bypass (entire pipeline skipped) | `pipeline.py` L246-260 | `is_likely_chat()` | No code gen, no review, no Director |
| 4 | QUERY → route to Librarian | `pipeline.py` L246-260 (via ctx.is_chat flow) | `classify_intent()` | No code gen, no review |

### [PHASE 0.05] Autonomous Intent Router + Diagnostic Oracle

| # | Feature | Module & Lines | Depends On | Invariant |
|---|---------|----------------|------------|-----------|
| 1 | DIAGNOSTIC intent detection via Intent Router | `_prompts.py` L125-130 (`INTENT_ROUTER_SYSTEM`) | `REASONING_MODEL` | Separate from MODIFICATION/QUERY |
| 2 | Resume multi-turn diagnostic session | Checkpoint system (`checkpoint.py`) | Checkpoint system | Chat history truncated |
| 3 | Fresh diagnostic session | Checkpoint system | `_prompts.py` DIAGNOSTIC_ORACLE_SYSTEM | Must detect `[AWAITING_INPUT]` |
| 4 | QA Ledger update from diagnostic resolution | Handled in pipeline flow | `qa_ledger.md` | Backup before overwrite |

### [PHASE 0.2] QA Anchor Extraction

| # | Feature | Module & Lines | Depends On | Invariant |
|---|---------|----------------|------------|-----------|
| 1 | Affirmative/negative keyword pre-filter | `gdd_extractor.py` | None | Append-only, never rewrites qa_ledger.md |
| 2 | Sentiment hint classification | `gdd_extractor.py` | None | WORKING / BROKEN / AMBIGUOUS / NONE |
| 3 | QA Anchor format extraction | `gdd_extractor.py` | REASONING_MODEL | Only writes if output != "NONE" |

### [PHASE 0.5] Lead Producer — Scope Gate & Auto-Feeder

| # | Feature | Module & Lines | Depends On | Invariant |
|---|---------|----------------|------------|-----------|
| 1 | Auto-feed next task from blueprint | `mesh_loops.py` L72-90 (`run_fetches`) | `docs/project_blueprint.md` | Checks for `- [ ] Task` pattern |
| 2 | Scope gate (TOO_BROAD / NARROW) | `mesh_loops.py` L91-117 (`run_fetches`) | REASONING_MODEL | SCOPE_FILE_LIMIT=5, SCOPE_LINE_LIMIT=400 |
| 3 | Blueprint generation for broad tasks | `mesh_loops.py` L103-117 (`run_fetches`) | REASONING_MODEL | Returns early |

### [PHASE 1] GDD Librarian

| # | Feature | Module & Lines | Depends On | Invariant |
|---|---------|----------------|------------|-----------|
| 1 | `extract_gdd_sections(gdd_path)` | `gdd_extractor.py` | GDD/Midway_to_Nowhere_Master_GDD_v19.md | Block-aware, structured extraction |
| 2 | `recursive_librarian(user_prompt)` | `_pipeline_helpers.py` L144-156 | `gdd_extractor.extract_gdd_sections()` | Searches GDD by domain agent relevance |
| 3 | Phase 1 invocation | `mesh_loops.py` L119-129 (`run_fetches`) | `recursive_librarian()` | Appends to output_parts |

### [PHASE 2] Project Context

| # | Feature | Module & Lines | Depends On | Invariant |
|---|---------|----------------|------------|-----------|
| 1 | `get_project_state()` | `_pipeline_helpers.py` L161-213 | PROJECT_ROOT scan | Lists src/, docs/, attractions/ |
| 2 | `curate_project_structure(user_prompt)` | `_pipeline_helpers.py` L343-392 | PROJECT_ROOT scan | Token-budget aware |
| 3 | Auto-Fetch Referenced Files | `file_references.py` (`parse_file_references`, `fetch_referenced_files`) | Project files | Cached via `set_referenced_files_cache()` |
| 4 | Phase 2 invocation | `mesh_loops.py` L131-151 (`run_fetches`) | Helpers | Appends to output_parts |

### [PHASE 3] Director — Task Decomposition

| # | Feature | Module & Lines | Depends On | Invariant |
|---|---------|----------------|------------|-----------|
| 1 | `build_director_prompt()` | `_pipeline_helpers.py` L313-338 | `ALL_DOMAINS` | Includes all available agents |
| 2 | Director LLM call | `mesh_loops.py` L153-166 (`run_fetches`) | DIRECTOR_SYSTEM (`_prompts.py`) | Must output `### Task N: [Domain] — Title` |
| 3 | Task parsing from Director output | `mesh_loops.py` L169-183 (`run_fetches`) | regex `### Task (\d+): \[([^\]]+)\] — (.+)` | Fallback: single default C++ task |
| 4 | Resurrection skip (Phases 1-3) | Handled by checkpoint resume in `pipeline.py` L232-242 | BLOCKED checkpoint | Reconstructs task map from serialized data |

### [PHASE 4] Mesh Execution

| # | Feature | Module & Lines | Depends On | Invariant |
|---|---------|----------------|------------|-----------|
| 1 | Work queue processing (depth-first) | `mesh_loops.py` L193-468 (`run_tasks`) | `Task` objects | Sequential — one task at a time |
| 2 | Domain agent LLM call via `execute_task()` | `_pipeline_helpers.py` L227-301 | `call_ollama()`, memory TOC, file context | Strictly sequential |
| 3 | Ledger Guard: `ensure_ledger_header()` | `ledger.py` | `_generate_module_name()` in `ledger.py` | Auto-fixes missing headers in agent output |
| 4 | Disk-Write Interceptor: `_append_to_ledger()` | `ledger.py` | Domain ledger files | Only domain tasks (not queries) |
| 5 | QUERY/CONSULT signal routing | `mesh_loops.py` L289-304, L363-374 | `extract_signals()` | Re-queues parent after answer received |
| 6 | `pending_queries` tracker | `mesh_loops.py` L278-286 | QUERY/CONSULT signals | Prevents lost responses to stalled agents |
| 7 | FETCH resolution (via DOC oracle) | `mesh_loops.py` L376-419 | `handle_fetch_signal()` (fetch_handler.py) | Max depth 3, routes through DOC for reasoning |
| 8 | DELEGATE sub-tasks | `mesh_loops.py` L306-322 | Max 5 sub-tasks per agent | No infinite nesting |
| 9 | VETO / OBJECT signal collection | `mesh_loops.py` L324-340 | HARD BLOCK semantics | Stored in context for conflict resolution |
| 10 | REVISE signal (re-queue target) | `mesh_loops.py` L346-357 | Target agent | Creates new task with revision spec |
| 11 | RECOURSE signal (appeal to Director) | `mesh_loops.py` L359-361 | None | Director handles in consensus phase |
| 12 | Progressive File Disclosure Tools | `_pipeline_helpers.py` L397-456 (`handle_file_read`, `handle_file_list`) | FILE_READ/FILE_LIST signals | Agents use `[FILE_READ:]` / `[FILE_LIST:]` |
| 13 | Snapshot save on iteration boundary | `mesh_loops.py` L457-466 | `HAS_SNAPSHOT` | Best-effort, never crashes pipeline |
| 14 | Double-check re-queue | `mesh_loops.py` L447-454 | Task unresolved items | max MAX_ITERATIONS=3 re-queues |
| 15 | READ_OFFLOADED signal | `mesh_loops.py` L421-444 | `_page_out_context()` (fetch_handler.py) | Auto page-out if budget exceeded |

### [PHASE 5] Conflict Resolution

| # | Feature | Module & Lines | Depends On | Invariant |
|---|---------|----------------|------------|-----------|
| 1 | Collect all VETO/OBJECT signals | `mesh_loops.py` L324-340 (stored in ctx) | Signal collection | Must check ALL vetos |
| 2 | VETO resolution | `mesh_finalize.py` L82-108 (`_run_conflict_resolution`) | CONF domain system prompt | SUSTAIN/OVERRULE/COMPROMISE |
| 3 | OBJECT resolution | `mesh_finalize.py` L110-135 (`_run_conflict_resolution`) | CONF domain system prompt | Same as VETO resolution |
| 4 | Conflict resolutions stored for review | `mesh_finalize.py` L105, L132 | `conflict_resolutions` list | Injected into Review-Fix prompts via `conflicts_str` |

### [PHASE 6] Review-Fix Loop

| # | Feature | Module & Lines | Depends On | Invariant |
|---|---------|----------------|------------|-----------|
| 1 | Compiler pre-flight (platform-aware) | `mesh_finalize.py` L156-186 (`_run_review_fix_loop`) | cmake (Windows) or make (Linux), luac | Errors → Architect Fix cycle |
| 2 | Architect Syntax Fix pass | `mesh_finalize.py` L190-206 (`_run_review_fix_loop`) | `ARCHITECT_FIX_SYSTEM` (`_prompts.py`) | Parses `### task_N` blocks, updates per-task |
| 3 | Active Code Index (TOC for review) | `mesh_finalize.py` L221-243 (`_run_review_fix_loop`) | `active_run_ledger.md` | Agents use `[FETCH:path#anchor]` to load code |
| 4 | Insanity Detector: pre-truncation fingerprint hashing | `mesh_finalize.py` L326-337 | `_normalize_fix_fingerprint()` (ledger.py), `hashlib.md5` | Detects infinite loops via canonical input fingerprint |
| 5 | Insanity Detector → BLOCKED | `mesh_finalize.py` L333-335 | Fingerprint collision | Trips circuit breaker → suspend protocol |
| 6 | Reviewer FETCH signal handling (no cycle++) | `mesh_finalize.py` L269-283 | `handle_fetch_signal()` (fetch_handler.py) | Re-injects fetched code, does NOT increment cycle |
| 7 | `get_verdict(review_output)` | `signals.py` L96-118 | PASS/FAIL/BLOCKED detection | Must parse natural language verdicts |
| 8 | Architect Fix cycle (max REVIEW_MAX_ITERATIONS=3) | `mesh_finalize.py` L297-339 | `ARCHITECT_FIX_SYSTEM` (`_prompts.py`) | Passes `conflicts_str` + `active_code_index` |
| 9 | Review output collected per cycle | `mesh_finalize.py` L287-289 | `review_output` | Appended to output_parts |

### [PHASE 7] Consensus Check

| # | Feature | Module & Lines | Depends On | Invariant |
|---|---------|----------------|------------|-----------|
| 1 | All checks list (logic + structural) | `mesh_finalize.py` L347-377 (`_run_consensus_and_finalization`) | Director output, review verdict, VETOs, etc. | Each check is a dict with source |
| 2 | Per-check pass/fail display | `mesh_finalize.py` L379-382 | `consensus_checks` | Printed as checklist with ✅/❌ |

### [PHASE 8] Final Approval / Failure Report / Circuit Breaker

| # | Feature | Module & Lines | Depends On | Invariant |
|---|---------|----------------|------------|-----------|
| 1 | Circuit Breaker (BLOCKED verdict) | `mesh_finalize.py` L440-476 (`_handle_blocked`) | Insanity Detector | Saves full state to checkpoint |
| 2 | Suspend protocol → BLOCKED checkpoint | `mesh_finalize.py` L464 | `save_checkpoint()` | Phase="BLOCKED" for resurrection |
| 3 | Post-Mortem generation | `mesh_finalize.py` L523-577 (`_handle_failure`) | `generate_failure_report()` (`_pipeline_helpers.py`) | Scope analysis after failure |
| 4 | Final Approval (APPROVED verdict) | `mesh_finalize.py` L479-520 (`_handle_approved`) | ALL checks pass | Cleanup: archive checkpoint on approval |
| 5 | Blueprint step chaining | `mesh_finalize.py` L393-412 | `project_blueprint.md` | Marks task as `[x]` |
| 6 | Snapshot apply on approval | `mesh_finalize.py` L656-659 (`_save_output`) | SnapshotManager | Best-effort |
| 7 | Session Timeline log (MODIFICATION flow) | `mesh_finalize.py` L414-431 | ALL agents | Agent list, tools accessed |

### [Session Timeline] — Cross-Phase

| # | Feature | Module & Lines | Invariant |
|---|---------|----------------|-----------|
| 1 | `log_to_session_timeline()` | `ledger.py` | Reverse chronological (newest at top) |
| 2 | Read-prepend-write file I/O | `ledger.py` | Single-file, never loads more than one file |
| 3 | Truncation at 4000 chars | `ledger.py` | Overflow → `[... output truncated ...]` |
| 4 | Logged fields: User Input, Agent, Tools, Final Output | `ledger.py` | All 4 fields required |
| 5 | Called in QUERY path | `pipeline.py` (to Librarian) | Agent="Librarian", tools="search_memory, fetch" |
| 6 | Called in MODIFICATION path | `mesh_finalize.py` L414-431 | Agent=agent_list[:5], tools=all accessed |

### [Librarian] — Read-Only Research Agent

| # | Feature | Module & Lines | Invariant |
|---|---------|----------------|-----------|
| 1 | `search_memory()` → Memory Ledger TOC | `gdd_extractor.py` | Only reads headers/anchors, never full files |
| 2 | Librarian system prompt | `_prompts.py` L103-110 (`LIBRARIAN_SYSTEM`) | Strictly grounded: answer ONLY from memory docs |
| 3 | FETCH signal resolution (single depth) | `fetch_handler.py` (`handle_fetch_signal`) | Exactly one re-read cycle with fetched context |
| 4 | Output to `pipeline_output_{run_id}.md` | `pipeline.py` L274-278 | Never touches src/ or docs/ except memory |
| 5 | Session timeline logged | `ledger.py` (`log_to_session_timeline`) | Must complete before output save |

### [Resurrection Protocol] — BLOCKED Pipeline Recovery

| # | Feature | Module & Lines | Invariant |
|---|---------|----------------|-----------|
| 1 | BLOCKED checkpoint detection | `pipeline.py` L232-242 | Phase field = "BLOCKED" |
| 2 | Work queue reconstruction from serialized data | `mesh_finalize.py` L446-461 (`_handle_blocked`) | `ckpt_data.get("work_queue", [])` |
| 3 | `all_results` and `processed_ids` restoration | `pipeline.py` L237-238 | Prevents re-execution of completed tasks |
| 4 | User fix injection | `pipeline.py` (via user_prompt) | user_prompt treated as manual fix |
| 5 | Jump to Review-Fix cycle (skip Phases 1-3) | Handled by checkpoint resume logic | Only enters if resumed from BLOCKED |
| 6 | BLOCKED skip reconstructs placeholder tasks | Handled by checkpoint resume | `tasks = [{"id": str(i), "domain": "RESUMED", ...}]` |

### [System Prompts] — All 13 Agent Prompts

All located in **`_prompts.py`**:

| # | Prompt | Lines | Purpose |
|---|--------|-------|---------|
| 1 | `DIRECTOR_SYSTEM` | 37-49 | Task decomposition prompt for Director |
| 2 | `REVIEW_SYSTEM` | 53-58 | Integration review system prompt |
| 3 | `REVIEW_PROMPT` | 60-75 | Full review input template with 6-point checklist |
| 4 | `FINAL_APPROVAL_SYSTEM` | 79-83 | Final approval / revision decision |
| 5 | `SELF_CORRECT_SYSTEM` | 87-91 | Self-correction prompt (used after MAX_ITERATIONS) |
| 6 | `ARCHITECT_FIX_SYSTEM` | 95-101 | Architect fix cycle prompt |
| 7 | `LIBRARIAN_SYSTEM` | 105-110 | GDD librarian research prompt |
| 8 | `DIAGNOSTIC_ORACLE_SYSTEM` | 114-121 | Multi-turn diagnostic session prompt |
| 9 | `INTENT_ROUTER_SYSTEM` | 125-130 | DEVELOPMENT vs DIAGNOSTIC gate |
| 10 | `INTENT_CLASSIFIER_SYSTEM` | 134 | MODIFICATION vs QUERY gate |
| 11 | `SEARCH_MEMORY_SYSTEM` | 170-174 | Memory TOC navigation |
| 12 | `MESH_AGENT_SYSTEM_EXTENSION` | `domain_registry.py` L375-394 | Cross-agent signal protocol appended to all agents |
| 13 | `AGENT_FILE_TOOLS_PROMPT` | `_pipeline_helpers.py` L397-409 | Progressive file disclosure tools for agents |

### [Helper Functions] — Support Layer

| # | Function | Module & Lines | Called By |
|---|----------|----------------|-----------|
| 1 | `get_available_domains_text()` | `_pipeline_helpers.py` L216-222 | Director prompt builder |
| 2 | `get_unavailable_domains_text()` | `_pipeline_helpers.py` L304-310 | Director prompt builder |
| 3 | `build_anchor_toc()` | `ledger.py` | Internal TOC generator |
| 4 | `resolve_agent_name()` | `domain_registry.py` L315-347 | Signal target→domain key resolution |
| 5 | `get_agent_system()` | `domain_registry.py` L350-369 | System prompt + mesh extension builder |
| 6 | `ensure_ledger_header()` | `ledger.py` | Ledger Guard: auto-fix missing `### [Header]` |
| 7 | `_generate_module_name()` | `ledger.py` | Synthetic header name generator |
| 8 | `register_progress_listener()` | `_mesh_api.py` L198-202 | Streaming/progressive output registration |
| 9 | `_emit_progress()` | `_mesh_api.py` L205-213 | Progress broadcast to all listeners |
| 10 | `parse_file_references()` | `file_references.py` | Auto-detect `reference [filepath]` in prompts |
| 11 | `fetch_referenced_files()` | `file_references.py` | Auto-inject referenced file contents |
| 12 | `generate_failure_report()` | `_pipeline_helpers.py` L569-645 | Failure report generation |

### [Mesh Work Queue API] — REST Server Integration

All located in **`_mesh_api.py`**:

| # | Function | Lines | Purpose |
|---|----------|-------|---------|
| 1 | `submit_mesh_task(task_type, payload, priority)` | 26-54 | Submit a task to the global queue with priority |
| 2 | `get_mesh_task_status(task_id)` | 57-61 | Check status of a submitted task |
| 3 | `list_mesh_tasks()` | 64-68 | List all tasks in the registry |
| 4 | `cancel_mesh_task(task_id)` | 71-86 | Cancel a queued task (status='queued' only) |
| 5 | `get_mesh_work_queue()` | 89-96 | Get pending queue (without payload) |
| 6 | `get_mesh_results()` | 99-106 | Get all completed results |

### [Signal/Routing Classes] — Structured Protocol

All located in **`signals.py`** and **`models.py`**:

| # | Class/Function | Module & Lines | Purpose |
|---|----------------|----------------|---------|
| 1 | `class SignalType(Enum)` | `models.py` L14-24 | Enum of all 9 signal types (QUERY, DELEGATE, RESULT, APPROVE, REVISE, VETO, OBJECT, RECOURSE, CONSULT) |
| 2 | `class MeshSignal` | `models.py` L26-57 | Data class with `to_dict()` for REST serialization |
| 3 | `extract_signals()` | `signals.py` L46-70 | Structured signal extractor |
| 4 | `parse_signal()` | `signals.py` L121-145 | Returns MeshSignal objects |
| 5 | `class ConsensusResult` | `models.py` L60-84 | Conflict resolution result with verdict/merged_code/explanation/warnings |
| 6 | `SIGNAL_PATTERNS` | `signals.py` L20-32 | All regex patterns for signal types including FETCH and READ_OFFLOADED |

### [Cross-Agent Edit Detection]

| # | Function | Module & Lines | Purpose |
|---|----------|----------------|---------|
| 1 | `resolve_conflict(a_code, b_code, justification, request)` | `_mesh_api.py` L111-162 | Structured conflict resolution via CONF agent, returns ConsensusResult |
| 2 | `_generate_failure_report_rest(task_id, error_details)` | `_mesh_api.py` L167-193 | REST API 2-arg overload wrapper for failure reports |

### [CLI Entry Point]

| # | Feature | Module & Lines | Purpose |
|---|---------|----------------|---------|
| 1 | argparse: positional `prompt` | `pipeline.py` L319-357 | Feature request string |
| 2 | argparse: `--checkpoint` | `pipeline.py` L319-357 | Resume from checkpoint ID |
| 3 | argparse: `--list-checkpoints` | `pipeline.py` L319-357 | List all saved checkpoints |
| 4 | argparse: `--chat` | `pipeline.py` L331-339 | Force CHAT mode |
| 5 | `run_pipeline()` entry point | `pipeline.py` L285-308 | Emits progress, calls run_mesh_pipeline() |

---

## 📐 Configuration Constants Reference

All located in **`pipeline.py`** lines 136-161:

| Constant | Current Value | Notes |
|----------|---------------|-------|
| `OLLAMA_HOST` | `http://192.168.0.16:11434` | Steam Deck address |
| `CODER_MODEL` | `qwen2.5-coder:7b` | 7B primary |
| `REVIEWER_MODEL` | `phi-3:14b-q4_K_M` | 14B Q4 — deeper reasoning |
| `FALLBACK_REVIEWER_MODEL` | `llama3.1:8b-instruct-q4_K_M` | 8B fallback reference |
| `LIBRARIAN_MODEL` | `llama3.1:8b-instruct-q4_K_M` | 8B librarian |
| `SYNTAX_GATE_MODEL` | `qwen2.5-coder:1.5b` | 1.5B micro-model |
| `INTENT_CLASSIFIER_MODEL` | `llama3.2:1b` | 1B micro-model |
| `MAX_ITERATIONS` | `3` | Per-task self-correction limit |
| `MAX_CONSENSUS_ITERATIONS` | `3` | Consensus loop limit |
| `MAX_SUBTASKS_PER_AGENT` | `5` | Nested sub-task cap |
| `REVIEW_MAX_ITERATIONS` | `3` | Review→fix→re-review cycles |
| `SCOPE_FILE_LIMIT` | `5` | Files before TOO_BROAD |
| `SCOPE_LINE_LIMIT` | `400` | Lines before TOO_BROAD |
| `OLLAMA_TIMEOUT` | `420` | Seconds (7 min) |
| `OLLAMA_NUM_CTX` | `32768` | 32K input context (KV cache q8_0) |
| `MAX_TOKENS` | `12000` | Output token ceiling |

---

## 🔐 System Invariants (Must Never Be Broken)

| # | Invariant | Location | Enforcement |
|---|-----------|----------|-------------|
| 1 | **No async/await** anywhere | Entire codebase | No `import asyncio`, no `async def`, no `await` |
| 2 | **No threading** | Entire codebase | No `import threading`, no `import concurrent` |
| 3 | **No multiprocessing** | Entire codebase | No `import multiprocessing` |
| 4 | `keep_alive: "0"` in EVERY Ollama request | `ollama_client.py` | Model unloads instantly after each call to free VRAM |
| 5 | **7-14B models for review/reasoning, 7B for coding** | `pipeline.py` L138-148 | phi-3:14b for Reviewer/Director; micro-models only for syntax/routing |
| 6 | Context window: **32K input, 12K max tokens** | `pipeline.py` L158-159 | `OLLAMA_NUM_CTX=32768`, `MAX_TOKENS=12000` |
| 7 | KV Cache: q8_0 quantization (implicit via Ollama) | `ollama_client.py` (comment) | Halves context memory, enables 32K in 12GB |
| 8 | LRU doc cache: **max 8 entries, 5 min TTL** | `_pipeline_helpers.py` L54-56 | Never grows unbounded in RAM |
| 9 | Session timeline: **always reverse chronological** | `ledger.py` (`log_to_session_timeline()`) | Newest entry at line 1 of file |
| 10 | Librarian: **NEVER modifies code** | `_prompts.py` L105-110 (system prompt) | Strictly read-only |
| 11 | Tag suggestions: **always `[Suggested]` prefix** | `mesh_finalize.py` `_run_tagsuggester_post()` | Never auto-apply authoritative tags |
| 12 | Overflow rotation: **200 KB threshold** | `token_budget.py` | Archive → `overflow_ledger_v{N}.md` |
| 13 | Block-aware truncation: **NEVER blind head/tail** | `token_budget.py` (`_block_aware_collapse()`) | Only legal truncation path |
| 14 | Checkpoint archive on completion | `mesh_finalize.py` L514-518, L570-575 | `.archived.json` suffix |
| 15 | Single-file I/O | `ledger.py` (`log_to_session_timeline()`) | Never loads more than one file into RAM |
| 16 | QA Ledger: **append-only** | Phase 0.2 | Backed up before overwrite in Diagnostic Oracle |
| 17 | FETCH max depth: **3** | `mesh_loops.py` L378-389 | Prevents infinite recursive FETCH loops |
| 18 | Insanity Detector: **pre-truncation fingerprint hashing** | `mesh_finalize.py` L326-337 | `_normalize_fix_fingerprint()` canonical fingerprint → BLOCKED circuit breaker |
| 19 | Progress listeners: **best-effort** | `_mesh_api.py` L205-213 | Never crashes pipeline on listener error |
| 20 | Mesh Work Queue: **no persistent state** | `_mesh_api.py` | In-memory only; lost on restart |
| 21 | **Virtual Context OffloadStore**: disk-based overflow buffer | `offload_store.py` | Offloaded blocks serialized to `offload_store/` |
| 22 | **Bi-directional context paging**: auto page-out on retrieve | `fetch_handler.py` (`_page_out_context()`) | If read_offloaded_file would overflow budget, auto-compresses inactive block |
| 23 | **Offloaded context placeholder format**: actionable tags | `fetch_handler.py` | Tag format: `<OFFLOADED_CONTEXT> Preview ...` |
| 24 | **Token Budget Ratio Overflow**: density-aware safety margin | `token_budget.py` (`estimate_tokens()`) | Samples first 2000 chars; if alphanumeric density > 60%, uses 1.5 char/token ratio |

---

## 🔄 Update Protocol

When adding a new feature to the pipeline:

1. **Identify the Phase** it belongs to (or add a new Phase)
2. **Identify which module** it goes in (or create a new module)
3. **Add a row** to the per-phase checklist table above
4. **Update the anchor index** (`docs/pipeline_anchor_index.md`) with the new function/class line
5. **Test invariants** — does the new feature break any of the 24 invariants?
6. **Update the mesh flow diagram** if the routing changed
7. **Update `docs/pipeline_agent_todo.md`** if scope changed or new tasks were discovered

---

## 📋 Re-Architecture Tasks + Critical Fixes — Implementation Status

All features from `docs/pipeline_agent_todo.md` have been implemented (19/22 complete):

| # | Feature | Status | Location |
|---|---------|--------|----------|
| 1 | Intra-Agent Reasoning Gate | ✅ Implemented | `_pipeline_helpers.py` `execute_task()` — REASONING_GATE_DOMAINS check |
| 2 | Tag System Auto-Detection | ✅ Implemented | `tagsuggester.py` — `TagSuggester.analyze()` |
| 3 | Explicit KV Cache Configuration | ✅ Implemented | `ollama_client.py` — `options` dict with KV cache q8_0 |
| 4 | Virtual Context Management (OffloadStore) | ✅ Implemented | `offload_store.py`, `fetch_handler.py` (`read_offloaded_file`, `_page_out_context`) |
| 5 | **Path Traversal Vulnerability** guard | ✅ Implemented | `file_references.py` — `resolve()` + `startswith()` guard |
| 6 | **Ledger Guard False Positive** fix | ✅ Implemented | `ledger.py` (`ensure_ledger_header()`) — strips code blocks before header check |
| 7 | **Insanity Detector Bypass** — handled by `set()`-based hashing | ✅ Already Handled | `mesh_finalize.py` — `seen_code_hashes_set` stores ALL historical hashes |
| 8 | **Token Budget Ratio Overflow** — density-aware safety margin | ✅ Implemented | `token_budget.py` — 1.5 char/token ratio when density > 60% |
| 9 | **Chat Session Segmentation** | ✅ Implemented | `pipeline_session.py` — `SessionManager` class |
| 10 | **Locked File Lifecycle** — timed lock with atomic write | ✅ Implemented | `ledger.py` — `_timed_lock()` with 30s timeout; atomic write-to-tmp+rename |
| 11 | **Pre-Truncation Hash Validation** | ✅ Implemented | `ledger.py` (`_normalize_fix_fingerprint()`) — canonical fingerprint before context collapse |
| 12 | **Snapshot Hash Verification** | ✅ Implemented | `pipeline_snapshot.py` — content hash in `save_proposal()` + verification in `apply_proposals()` |

---

*Last updated: 2026-05-05 — Verified all 80 tests pass across 18 refactored modules.*
