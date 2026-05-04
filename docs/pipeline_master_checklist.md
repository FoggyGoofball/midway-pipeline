# Pipeline Master Checklist — Midway to Nowhere

> **Purpose:** Single-source-of-truth reference for pipeline features and invariants.  
> Feed this to agents alongside prompts to ensure all features survive successive edits.  
> **File:** `pipeline.py` (4,489 lines, 180 KB)  
> **Last Verified:** 2026-05-03

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

### Model Registry Constants (pipeline.py L52-L62)

```python
CODER_MODEL = "qwen2.5-coder:7b"
REVIEWER_MODEL = "phi-3:14b-q4_K_M"                    # 14B reviewer for deeper reasoning
FALLBACK_REVIEWER_MODEL = "llama3.1:8b-instruct-q4_K_M" # kept as fallback reference
DIRECTOR_MODEL = REVIEWER_MODEL    # phi-3:14b
EXECUTION_MODEL = CODER_MODEL      # qwen2.5-coder:7b
REASONING_MODEL = REVIEWER_MODEL   # phi-3:14b
LIBRARIAN_MODEL = "llama3.1:8b-instruct-q4_K_M"
SYNTAX_GATE_MODEL = "qwen2.5-coder-1.5b"   # micro-model for fast syntax
INTENT_CLASSIFIER_MODEL = "llama-3.2-1b"    # micro-model for intent gate
```

---

## 💾 Context-to-HDD Overflow Expansion

With **32K input context** and **12K max tokens** (enabled by KV cache q8_0 quantization fitting within 12GB VRAM):

1. **Overflow Ledger Rotation** (`TokenBudget.add()` at L404-462)
   - When `overflow_ledger.md` exceeds **200 KB**, it is archived to `overflow_ledger_v{N}.md`
   - A fresh file is created with a link back to the archive
   - Version numbering increments automatically (v1, v2, v3...)

2. **Block-Aware Collapse** (`TokenBudget._block_aware_collapse()` at L126-349)
   - Never does blind head/tail slicing
   - Preserves function signatures, markdown headers, code fences
   - Drops oldest blocks first (reverse-order iteration)
   - Collapses function bodies with `[... function body collapsed for context budget ...]` notice

3. **Agent Retrieval of Lost Context**
   - Agents use `[FETCH:docs/memory/overflow_ledger_v{N}.md#HeaderName]` to pull back truncated content
   - `handle_fetch_signal()` (L2184) calculates **Chronological Read Depth** and tags fetched content with how far back in the archive chain it came from
   - **Never delete overflow archives** — they are the formal project history

4. **Virtual Context OffloadStore** (NEW — `OffloadStore` class at L478)
   - When `_block_aware_collapse` prunes old context blocks, they are **losslessly saved** to `offload_store/` instead of permanently deleted
   - Placeholder `<OFFLOADED_CONTEXT>` tags are injected into the active LLM context
   - Agents retrieve via `[READ_OFFLOADED:<block_id>]` signal (handled at L2691 in mesh loop)
   - `read_offloaded_file()` at L2287 retrieves the block from disk
   - `_page_out_context()` at L2338 auto-compresses an inactive block to make room when a retrieved block would overflow the budget

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

The `TagSuggester` class (`pipeline.py`) analyzes the session timeline after each pipeline run and suggests tags:

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
└────────────────────────────────────────────────────────────┘
    │                        │
    │ QUERY                  │ MODIFICATION
    ▼                        ▼
┌────────────────┐  ┌──────────────────────────────────────────┐
│ The Librarian  │  │ Phase 0.05: Intent Router (DIAGNOSTIC?)  │
│ (read-only)    │  │ Phase 0.2: QA Anchor Extraction          │
│                │  │ Phase 0.5: Lead Producer (Scope Gate)    │
│ 1. search_     │  │ Phase 1: GDD Librarian                   │
│    memory()    │  │ Phase 2: Project Context + File Refs     │
│    → TOC       │  │ Phase 3: Director (Task Decomposition)   │
│ 2. FETCH       │  │                                          │
│    ledgers     │  │ Phase 4: Mesh Execution (Work Queue)     │
│ 3. Answer      │  │   ├─ Agent tasks (C++, Lua, Review...)   │
│    (no code!)  │  │   ├─ QUERY/CONSULT signal routing        │
│                │  │   ├─ FETCH resolution (memory recall)    │
│    ↓           │  │   ├─ DELEGATE sub-task nesting           │
│ Session        │  │   ├─ VETO/OBJECT handling                │
│ Timeline       │  │   ├─ Disk-Write Interceptor (→ ledgers)  │
│                │  │   ├─ Ledger Guard (synthetic headers)    │
│                │  │   ├─ Progressive File Disclosure tools   │
│                │  │   └─ Insanity Detector (state hashing)   │
└────────────────┘  │                                          │
                    │ Phase 5: Consensus Gate (VETO Check)     │
                    │ Phase 6: Review-Fix Loop                  │
                    │   ├─ Compiler pre-flight (cmake/luac)    │
                    │   ├─ Syntax Gate (micro-model pass)      │
                    │   ├─ Reviewer (full code audit)          │
                    │   ├─ Architect Fix cycle                 │
                    │   ├─ Active Code Index (FETCH-based TOC) │
                    │   ├─ Reviewer FETCH handling (no cycle++)│
                    │   └─ Insanity Detector (loop prevention) │
                    │ Phase 7: Consensus Check                  │
                    │ Phase 8: Final Approval / Failure Report  │
                    │   ├─ Circuit Breaker → BLOCKED suspend   │
                    │   ├─ Post-Mortem generation               │
                    │   └─ Blueprint step chaining             │
                    │                                          │
                    │ Session Timeline (log)                    │
                    └──────────────────────────────────────────┘
```

---

## 📦 Core Data Structure: ALL_DOMAINS Registry

**File:** `pipeline.py` L921-1097
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

`PERSONA_MAP` (L1203) is built at module load from `ready=True` domains.

---

## 📋 Per-Phase Feature Checklist

### [PHASE 0.03] Intent Classification

| # | Feature | Code Lines | Depends On | Invariant |
|---|---------|------------|------------|-----------|
| 1 | `classify_intent(user_prompt)` → MODIFICATION or QUERY | L57-L57 | `INTENT_CLASSIFIER_MODEL` (llama-3.2-1b micro-model) | Must run before any execution |
| 2 | Uses micro-model (1B), single lightweight call | L2098-2126 | `call_ollama()` | Default to MODIFICATION if ambiguous |
| 3 | QUERY → bypass entire coding mesh → route to Librarian | L1423-1443 | `classify_intent()` | No code gen, no review, no Director |
| 4 | QUERY → log to session timeline | L711-777 | `log_to_session_timeline()` | Must include Librarian as agent |

### [PHASE 0.05] Autonomous Intent Router + Diagnostic Oracle

| # | Feature | Code Lines | Depends On | Invariant |
|---|---------|------------|------------|-----------|
| 1 | DIAGNOSTIC intent detection (second gate) | L60-L60 | `REASONING_MODEL` | Separate from MODIFICATION/QUERY |
| 2 | Resume multi-turn diagnostic session | L2473-2486 | Checkpoint system | Chat history truncated at 2000 chars |
| 3 | Fresh diagnostic session | L2473-2486 | Checkpoint system | Must detect `[AWAITING_INPUT]` |
| 4 | QA Ledger update from diagnostic resolution | L2691-3879 | `qa_ledger.md` | Backup before overwrite |
| 5 | Checkpoint archive on resolution | L2473-2486 | `save_checkpoint()` | Rename to `.archived.json` |

### [PHASE 0.2] QA Anchor Extraction

| # | Feature | Code Lines | Depends On | Invariant |
|---|---------|------------|------------|-----------|
| 1 | Affirmative/negative keyword pre-filter | L2127-2146 | None | Append-only, never rewrites qa_ledger.md |
| 2 | Sentiment hint classification | L2127-2146 | None | WORKING / BROKEN / AMBIGUOUS / NONE |
| 3 | QA Anchor format extraction | L2098-L60 | REASONING_MODEL | Only writes if output != "NONE" |

### [PHASE 0.5] Lead Producer — Scope Gate & Auto-Feeder

| # | Feature | Code Lines | Depends On | Invariant |
|---|---------|------------|------------|-----------|
| 1 | Auto-feed next task from blueprint | L2691-3879 | `docs/project_blueprint.md` | Checks for `- [ ] Task` pattern |
| 2 | Scope gate (TOO_BROAD / NARROW) | L2098-L2246 | REASONING_MODEL | SCOPE_FILE_LIMIT=5, SCOPE_LINE_LIMIT=400 |
| 3 | Blueprint generation for broad tasks | L2691-3879 | REASONING_MODEL | Returns early |

### [PHASE 1] GDD Librarian

| # | Feature | Code Lines | Depends On | Invariant |
|---|---------|------------|------------|-----------|
| 1 | `extract_gdd_sections(gdd_path)` | L1686-1718 | GDD/Midway_to_Nowhere_Master_GDD_v19.md | Block-aware, structured extraction |
| 2 | `recursive_librarian(user_prompt)` | L1719-1752 | Domain agent matching | Searches GDD by domain agent relevance |

### [PHASE 2] Project Context

| # | Feature | Code Lines | Depends On | Invariant |
|---|---------|------------|------------|-----------|
| 1 | `get_project_state()` | L1539-1591 | PROJECT_ROOT scan | Lists src/, docs/, attractions/ |
| 2 | `curate_project_structure(user_prompt)` | L2098-L1161 | REASONING_MODEL | Token-budget aware |
| 3 | Auto-Fetch Referenced Files | L1708-L1788 | `parse_file_references()`, `fetch_referenced_files()` | Cached via `set_referenced_files_cache()` |

### [PHASE 3] Director — Task Decomposition

| # | Feature | Code Lines | Depends On | Invariant |
|---|---------|------------|------------|-----------|
| 1 | `build_director_prompt()` | L1608-1634 | domain system prompts | Includes all available agents |
| 2 | Director LLM call (REASONING_MODEL) | L2691 | DIRECTOR_SYSTEM | Must output `### Task N: [Domain] — Title` |
| 3 | Task parsing from Director output | L2691-3879 | regex `### Task (\d+): \[([^\]]+)\] — (.+)` | Fallback: single default C++ task |
| 4 | Resurrection skip (Phases 1-3) | L2691-3879 | BLOCKED checkpoint | Reconstructs task map from serialized data |

### [PHASE 4] Mesh Execution

| # | Feature | Code Lines | Depends On | Invariant |
|---|---------|------------|------------|-----------|
| 1 | Work queue processing (depth-first) | L2691-3879 | `Task` objects | Sequential — one task at a time |
| 2 | Domain agent LLM call via `execute_task()` | L2691-3879 | `call_ollama()`, memory TOC, file context | Strictly sequential |
| 3 | Ledger Guard: `ensure_ledger_header()` | L2379-2407 | `_generate_module_name()` | Auto-fixes missing headers in agent output |
| 4 | Disk-Write Interceptor: `_append_to_ledger()` | L2691-3879 | Domain ledger files | Only domain tasks (not queries) |
| 5 | QUEST signal routing (QUERY/CONSULT) | L2691-3879 | `extract_signals()` | Re-queues parent after answer received |
| 6 | `pending_queries` tracker | L2691-3879 | QUERY/CONSULT signals | Prevents lost responses to stalled agents |
| 7 | FETCH resolution (via DOC oracle) | L2184-2280 | `handle_fetch_signal()` | Max depth 3, routes through DOC for reasoning |
| 8 | DELEGATE sub-tasks | L2691-3879 | Max 5 sub-tasks per agent | No infinite nesting |
| 9 | VETO / OBJECT signal collection | L4189-4241 | Conflict resolution | HARD BLOCK semantics |
| 10 | REVISE signal (re-queue target) | L2691-3879 | Target agent | Creates new task with revision spec |
| 11 | RECOURSE signal (appeal to Director) | L2691-3879 | None | Director handles in consensus phase |
| 12 | Progressive File Disclosure Tools | L1835-1861 | `handle_file_read()`, `handle_file_list()` | Agents use `[FILE_READ:]` / `[FILE_LIST:]` |
| 13 | Snapshot save on iteration boundary | L2691-3879 | `HAS_SNAPSHOT` | Best-effort, never crashes pipeline |
| 14 | Double-check re-queue | L2467-L64 | Task unresolved items | max MAX_ITERATIONS=3 re-queues |

### [PHASE 5] Consensus Gate (VETO Check)

| # | Feature | Code Lines | Depends On | Invariant |
|---|---------|------------|------------|-----------|
| 1 | Collect all VETO/OBJECT signals | L2691-3879 | Signal collection | Must check ALL vetos |
| 2 | `resolve_conflict()` for each veto | L4189-4241 | CONF domain system prompt | SUSTAIN/OVERRULE/COMPROMISE |
| 3 | `detect_cross_agent_edits()` | L4121-4188 | `difflib` | Line-by-line diff conflict detection |
| 4 | Conflict resolutions stored for review | L4189-4241 | `conflict_resolutions` list | Injected into Review-Fix prompts |

### [PHASE 6] Review-Fix Loop

| # | Feature | Code Lines | Depends On | Invariant |
|---|---------|------------|------------|-----------|
| 1 | Compiler pre-flight (platform-aware) | L2691-3879 | cmake (Windows) or make (Linux), luac | Errors → Architect Fix cycle |
| 2 | Architect Syntax Fix pass | L1291-L1291 | `ARCHITECT_FIX_SYSTEM` | Parses `### task_N` blocks, updates per-task |
| 3 | Active Code Index (TOC for review) | L2691-3879 | `active_run_ledger.md` | Agents use `[FETCH:path#anchor]` to load code |
| 4 | Insanity Detector: pre-truncation fingerprint hashing | L675, L675 | `_normalize_fix_fingerprint()`, `hashlib.md5` | Detects infinite loops via canonical input fingerprint — computed BEFORE `call_ollama()` context truncation |
| 5 | Insanity Detector → BLOCKED | L2691-3879 | Fingerprint collision | Trips circuit breaker → suspend protocol |
| 6 | Reviewer FETCH signal handling (no cycle++) | L2184-2280 | `handle_fetch_signal()` | Re-injects fetched code, does NOT increment cycle |
| 7 | `get_verdict(review_output)` | L2163-2183 | APPROVED/BLOCKED/FAIL detection | Must parse natural language verdicts |
| 8 | Architect Fix cycle (max REVIEW_MAX_ITERATIONS=3) | L64-64 | `ARCHITECT_FIX_SYSTEM` | Passes conflicts_str + active_code_index |
| 9 | Review output collected per cycle | L2718, L2733 | `review_output` | Appended to output_parts |

### [PHASE 7] Consensus Check

| # | Feature | Code Lines | Depends On | Invariant |
|---|---------|------------|------------|-----------|
| 1 | All checks list (logic + structural) | `consensus_checks` L2691-3879 | Director output | Each check is a dict with source |
| 2 | Per-check pass/fail/unknown | L2163-2183 | `get_verdict()` | Printed as checklist |

### [PHASE 8] Final Approval / Failure Report / Circuit Breaker

| # | Feature | Code Lines | Depends On | Invariant |
|---|---------|------------|------------|-----------|
| 1 | Circuit Breaker (BLOCKED verdict) | L2691-3879 | Insanity Detector | Saves full state to checkpoint |
| 2 | Suspend protocol → BLOCKED checkpoint | L2473-2486 | `save_checkpoint()` | Phase="BLOCKED" for resurrection |
| 3 | Post-Mortem generation | L2691-3879 | `generate_failure_report()`, Scope analysis | 7-arg internal signature + REST overload |
| 4 | Final Approval (APPROVED verdict) | L2691-3879 | ALL checks pass | Cleanup: archive checkpoint |
| 5 | Blueprint step chaining | L2691-3879 | `project_blueprint.md` | Marks task as `[x]` |
| 6 | Snapshot apply on approval | L2957-2962 | SnapshotManager | Best-effort |
| 7 | Session Timeline log (MODIFICATION flow) | L711-777 | ALL agents | Agent list, tools accessed |

### [Session Timeline] — Cross-Phase

| # | Feature | Code Lines | Invariant |
|---|---------|------------|-----------|
| 1 | `log_to_session_timeline()` | L711-777 | Reverse chronological (newest at top) |
| 2 | Read-prepend-write file I/O | L711-777 | Single-file, never loads more than one file |
| 3 | Truncation at 4000 chars | L711-777 (up from 2000) | Overflow → `[... output truncated ...]` |
| 4 | Logged fields: User Input, Agent, Tools, Final Output | L711-777 | All 4 fields required |
| 5 | Called in QUERY path | L2691-3879 | Agent="Librarian", tools="search_memory, fetch" |
| 6 | Called in MODIFICATION path | L2691-3879 | Agent=agent_list[:5], tools=all accessed |

### [Librarian] — Read-Only Research Agent

| # | Feature | Code Lines | Invariant |
|---|---------|------------|-----------|
| 1 | `search_memory()` → Memory Ledger TOC | L1372-1404 | Only reads headers/anchors, never full files |
| 2 | Librarian LLM call (DIRECTOR_MODEL) | L1328-1348 | Strictly grounded: answer ONLY from memory docs |
| 3 | FETCH signal resolution (single depth) | L2691-3879 | Exactly one re-read cycle with fetched context |
| 4 | Output to `pipeline_output_{run_id}.md` | L2691-3879 | Never touches src/ or docs/ except memory |
| 5 | Session timeline logged | L711-777 | Must complete before output save |

### [Resurrection Protocol] — BLOCKED Pipeline Recovery

| # | Feature | Code Lines | Invariant |
|---|---------|------------|-----------|
| 1 | BLOCKED checkpoint detection | L2691-3879 | Phase field = "BLOCKED" |
| 2 | Work queue reconstruction from serialized data | L2691-3879 | `ckpt_data.get("work_queue", [])` |
| 3 | `all_results` and `processed_ids` restoration | L2691-3879 | Prevents re-execution of completed tasks |
| 4 | User fix injection | L2691-3879 | user_prompt treated as manual fix |
| 5 | Jump to Review-Fix cycle (skip Phases 1-3) | L2691-3879 (+ else block at L2216) | Only enters if `not _resumed_blocked` |
| 6 | BLOCKED skip reconstructs placeholder tasks | L2691-3879 | `tasks = [{"id": str(i), "domain": "RESUMED", ...}]` |

### [System Prompts] — All 13 Agent Prompts

| # | Prompt | Code Lines | Purpose |
|---|--------|------------|---------|
| 1 | `DIRECTOR_SYSTEM` | L1241-1254 | Task decomposition prompt for Director |
| 2 | `REVIEW_SYSTEM` | L1255-1261 | Integration review system prompt |
| 3 | `REVIEW_PROMPT` | L1262-1278 | Full review input template with 6-point checklist |
| 4 | `FINAL_APPROVAL_SYSTEM` | L1279-1284 | Final approval / revision decision |
| 5 | `SELF_CORRECT_SYSTEM` | L1285-1290 | Self-correction prompt (used after MAX_ITERATIONS) |
| 6 | `ARCHITECT_FIX_SYSTEM` | L1291-1298 | Architect fix cycle prompt |
| 7 | `LIBRARIAN_SYSTEM` | L1299-1305 | GDD librarian research prompt |
| 8 | `DIAGNOSTIC_ORACLE_SYSTEM` | L1306-1314 | Multi-turn diagnostic session prompt |
| 9 | `INTENT_ROUTER_SYSTEM` | L1315-1327 | DEVELOPMENT vs DIAGNOSTIC gate |
| 10 | `INTENT_CLASSIFIER_SYSTEM` | L1349-1363 | MODIFICATION vs QUERY gate |
| 11 | `SEARCH_MEMORY_SYSTEM` | L1364-1371 | Memory TOC navigation |
| 12 | `MESH_AGENT_SYSTEM_EXTENSION` | L1444-1472 | Cross-agent signal protocol appended to all agents |
| 13 | `AGENT_FILE_TOOLS_PROMPT` | L1847-1861 | Progressive file disclosure tools for agents |

### [Helper Functions] — Support Layer

| # | Function | Code Lines | Called By |
|---|----------|------------|-----------|
| 1 | `get_available_domains_text()` | L1592-1599 | Director prompt builder |
| 2 | `get_unavailable_domains_text()` | L1600-1607 | Director prompt builder |
| 3 | `build_anchor_toc()` | L778-799 | Internal TOC generator |
| 4 | `resolve_agent_name()` | L2535-2564 | Signal target→domain key resolution |
| 5 | `get_agent_system()` | L2565-2579 | System prompt + mesh extension builder |
| 6 | `ensure_ledger_header()` | L2379-2407 | Ledger Guard: auto-fix missing `### [Header]` |
| 7 | `_generate_module_name()` | L2408-2429 | Synthetic header name generator |
| 8 | `register_progress_listener()` | L4269-4274 | Streaming/progressive output registration |
| 9 | `_emit_progress()` | L4275-4288 | Progress broadcast to all listeners |
| 10 | `parse_file_references()` | L1755-1777 | Auto-detect `reference [filepath]` in prompts |
| 11 | `fetch_referenced_files()` | L1778-1834 | Auto-inject referenced file contents |

### [Mesh Work Queue API] — REST Server Integration

| # | Function | Code Lines | Purpose |
|---|----------|------------|---------|
| 1 | `submit_mesh_task(task_type, payload, priority)` | L3971-3997 | Submit a task to the global queue with priority |
| 2 | `get_mesh_task_status(task_id)` | L3998-4002 | Check status of a submitted task |
| 3 | `list_mesh_tasks()` | L4003-4007 | List all tasks in the registry |
| 4 | `cancel_mesh_task(task_id)` | L4008-4025 | Cancel a queued task (status='queued' only) |
| 5 | `get_mesh_work_queue()` | L4026-4033 | Get pending queue (without payload) |
| 6 | `get_mesh_results()` | L4034-4045 | Get all completed results |

### [Signal/Routing Classes] — Structured Protocol

| # | Class/Function | Code Lines | Purpose |
|---|----------------|------------|---------|
| 1 | `class SignalType(Enum)` | L4046-4058 | Enum of all 9 signal types (QUERY, DELEGATE, RESULT, APPROVE, REVISE, VETO, OBJECT, RECOURSE, CONSULT) |
| 2 | `class MeshSignal` | L4059-4079 | Data class with `to_dict()` for REST serialization |
| 3 | `parse_signal()` | L4059-4079 | Structured signal parser returning MeshSignal objects |
| 4 | `class ConsensusResult` | L4100-4101 | Conflict resolution result with verdict/merged_code/explanation/warnings |

### [Cross-Agent Edit Detection]

| # | Function | Code Lines | Purpose |
|---|----------|------------|---------|
| 1 | `detect_cross_agent_edits(a_code, b_code)` | L4121-4188 | Line-by-line diff using `difflib`, returns conflict dicts |
| 2 | `resolve_conflict(a_code, b_code, justification, request)` | L4189-4241 | Structured conflict resolution via CONF agent, returns ConsensusResult |
| 3 | `_generate_failure_report_rest(task_id, error_details)` | L2691-3879 | REST API 2-arg overload wrapper for failure reports |

### [CLI Entry Point]

| # | Feature | Code Lines | Purpose |
|---|---------|------------|---------|
| 1 | argparse: positional `prompt` | L4415-4489 | Feature request string |
| 2 | argparse: `--checkpoint` | L4415-4489 | Resume from checkpoint ID |
| 3 | argparse: `--list-checkpoints` | L4415-4489 | List all saved checkpoints |
| 4 | `run_pipeline()` entry point | L4415-4489 | Emits progress, calls run_mesh_pipeline() |

---

## 📐 Configuration Constants Reference

| Constant | Current Value | Notes |
|----------|---------------|-------|
| `OLLAMA_HOST` | `http://192.168.0.16:11434` | Steam Deck address |
| `CODER_MODEL` | `qwen2.5-coder:7b` | 7B primary |
| `REVIEWER_MODEL` | `phi-3:14b-q4_K_M` | 14B Q4 — deeper reasoning |
| `FALLBACK_REVIEWER_MODEL` | `llama3.1:8b-instruct-q4_K_M` | 8B fallback reference |
| `DIRECTOR_MODEL` | `REVIEWER_MODEL` | Same as reviewer (phi-3:14b) |
| `EXECUTION_MODEL` | `CODER_MODEL` | Same as coder |
| `REASONING_MODEL` | `REVIEWER_MODEL` | Same as reviewer (phi-3:14b) |
| `LIBRARIAN_MODEL` | `llama3.1:8b-instruct-q4_K_M` | 8B librarian |
| `SYNTAX_GATE_MODEL` | `qwen2.5-coder-1.5b` | 1.5B micro-model |
| `INTENT_CLASSIFIER_MODEL` | `llama-3.2-1b` | 1B micro-model |
| `MAX_ITERATIONS` | `3` | Per-task self-correction limit |
| `MAX_CONSENSUS_ITERATIONS` | `3` | Consensus loop limit |
| `MAX_SUBTASKS_PER_AGENT` | `5` | Nested sub-task cap |
| `REVIEW_MAX_ITERATIONS` | `3` | Review→fix→re-review cycles |
| `SCOPE_FILE_LIMIT` | `5` | Files before TOO_BROAD |
| `SCOPE_LINE_LIMIT` | `400` | Lines before TOO_BROAD |
| `OLLAMA_TIMEOUT` | `420` | Seconds (7 min) |
| `OLLAMA_NUM_CTX` | `32768` | 32K input context (KV cache q8_0) |
| `MAX_TOKENS` | `12000` | Output token ceiling |
| `TokenBudget.hard_limit` | `12000` | Matches MAX_TOKENS |
| `_MAX_OUTPUT_CHARS` | `4000` | Session timeline truncation |
| `_DOC_CACHE_TTL` | `300` (5 min) | LRU cache TTL |
| `_DOC_CACHE_MAX` | `8` | LRU cache entry cap |
| Overflow rotation threshold | `200 KB` | Archives overflow_ledger.md |
| File truncation (agent tools) | `12000 chars` | Progressive disclosure cap |
| File truncation (fetch_ref) | `16000 chars` | Auto-injected ref cap |
| File truncation (find_relevant) | `12000 chars` | Relevant file discovery cap |

---

## 🔐 System Invariants (Must Never Be Broken)

| # | Invariant | Location | Enforcement |
|---|-----------|----------|-------------|
| 1 | **No async/await** anywhere | Entire file | No `import asyncio`, no `async def`, no `await` |
| 2 | **No threading** | Entire file | No `import threading`, no `import concurrent` |
| 3 | **No multiprocessing** | Entire file | No `import multiprocessing` |
| 4 | `keep_alive: "0"` in EVERY Ollama request | `call_ollama()` L2098 | Model unloads instantly after each call to free VRAM on Steam Deck's 12GB budget |
| 5 | **7-14B models for review/reasoning, 7B for coding** | L52-L62 | phi-3:14b for Reviewer/Director/Reasoning Gate; micro-models only for syntax/routing; llama3.1:8b retained as fallback |
| 6 | Context window: **32K input, 12K max tokens** | L72 | `OLLAMA_NUM_CTX=32768`, `MAX_TOKENS=12000` |
| 7 | KV Cache: q8_0 quantization (implicit via Ollama) | L675 (comment in `call_ollama()`) | Halves context memory, enables 32K in 12GB |
| 8 | LRU doc cache: **max 8 entries, 5 min TTL** | L640-671 | Never grows unbounded in RAM |
| 9 | Session timeline: **always reverse chronological** | `log_to_session_timeline()` | Newest entry at line 1 of file |
| 10 | Librarian: **NEVER modifies code** | System prompt | Strictly read-only |
| 11 | Tag suggestions: **always `[Suggested]` prefix** | Post-approval step | Never auto-apply authoritative tags |
| 12 | Overflow rotation: **200 KB threshold** | `TokenBudget.add()` L92 | Archive → `overflow_ledger_v{N}.md` |
| 13 | Block-aware truncation: **NEVER blind head/tail** | `_block_aware_collapse()` | Only legal truncation path |
| 14 | Checkpoint archive on completion | Phase 8 | `.archived.json` suffix |
| 15 | Single-file I/O | `log_to_session_timeline()` | Never loads more than one file into RAM |
| 16 | QA Ledger: **append-only** | Phase 0.2 | Backed up before overwrite in Diagnostic Oracle |
| 17 | FETCH max depth: **3** | L2506-2517 | Prevents infinite recursive FETCH loops |
| 18 | Insanity Detector: **pre-truncation fingerprint hashing** | L675 | `_normalize_fix_fingerprint()` canonical fingerprint → BLOCKED circuit breaker — computed BEFORE context truncation inside `call_ollama()` |
| 19 | Progress listeners: **best-effort** | L3864-3870 | Never crashes pipeline on listener error |
| 20 | Mesh Work Queue: **no persistent state** | L3552-3553 | In-memory only; lost on restart |
| 21 | **Virtual Context OffloadStore**: disk-based overflow buffer | `OffloadStore` class L478 | Offloaded blocks serialized to `offload_store/`; retain content losslessly for agent retrieval |
| 22 | **Bi-directional context paging**: auto page-out on retrieve | `_page_out_context()` L2287 | If read_offloaded_file would overflow budget, auto-compresses an inactive block to make room |
| 23 | **Offloaded context placeholder format**: actionable tags | `_build_offload_placeholder()` L2287 | Tag format: `<OFFLOADED_CONTEXT> Preview (first 10 lines): {preview} Use read_offloaded_file('{path}')` |
| 24 | **Token Budget Ratio Overflow**: density-aware safety margin | `TokenBudget.estimate_tokens()` L92 | Samples first 2000 chars; if alphanumeric density > 60%, uses conservative 1.5 char/token ratio |

---

## 🔄 Update Protocol

When adding a new feature to `pipeline.py`:

1. **Identify the Phase** it belongs to (or add a new Phase)
2. **Add a row** to the per-phase checklist table above
3. **Update the anchor index** (`docs/pipeline_anchor_index.md`) with the new function/class line
4. **Test invariants** — does the new feature break any of the 24 invariants?
5. **Update the mesh flow diagram** if the routing changed
6. **Run structural diff** against `pipeline.py.old` to confirm zero regressions
7. **Update `docs/pipeline_agent_todo.md`** if scope changed or new tasks were discovered

---

## 📋 Re-Architecture Tasks + Critical Fixes — Implementation Status

All features from `docs/pipeline_agent_todo.md` have been implemented (19/22 complete):

| # | Feature | Status | Location |
|---|---------|--------|----------|
| 1 | Intra-Agent Reasoning Gate | ✅ Implemented | `execute_task()` — `REASONING_GATE_DOMAINS` check after `call_ollama()` |
| 2 | Tag System Auto-Detection | ✅ Implemented | `TagSuggester` class — `analyze()` called before `return final_output` |
| 3 | Explicit KV Cache Configuration | ✅ Implemented | `call_ollama()` options dict — `use_mmap`, KV cache q8_0 comment doc |
| 4 | Virtual Context Management (OffloadStore) | ✅ Implemented | `OffloadStore` class L478, `_block_aware_collapse` modified L478 to save instead of delete, `read_offloaded_file()` L2287, `_page_out_context()` L2287, `handle_read_offloaded_signal()` L2287 |
| 5 | **Path Traversal Vulnerability** — `resolve()` + `startswith()` guard | ✅ Implemented 2026-05-03 | `parse_file_references()` — resolves `(PROJECT_ROOT / path).resolve()` and verifies prefix against `PROJECT_ROOT.resolve()` |
| 6 | **Ledger Guard False Positive** — strip code blocks before header check | ✅ Implemented 2026-05-03 | `ensure_ledger_header()` — `re.sub(r'```.*?```', '', output, flags=re.DOTALL)` before checking `LEDGER_HEADER_PATTERN` |
| 7 | **Insanity Detector Bypass** — already handled by `set()`-based hashing | ✅ Already Handled | Phase 6 review-fix loop — `seen_code_hashes = set()` stores ALL historical hashes, not just the last one |
| 8 | **Token Budget Ratio Overflow** — density-aware safety margin | ✅ Implemented 2026-05-03 | `TokenBudget.estimate_tokens()` L92 — 1.5 char/token ratio when alphanumeric density > 60% |
| 9 | **Chat Session Segmentation** | ✅ Implemented 2026-05-03 | `pipeline_session.py` — `SessionManager` class; `run_mesh_pipeline()` accepts `session_id`; stream server auto-detect |
| 10 | **Locked File Lifecycle — Edge Case Investigation** | ✅ Implemented 2026-05-03 | `_timed_lock()` with 30s timeout; atomic write-to-tmp+rename; mkdir moved inside LEDGER_WRITE_LOCK; GLOBAL_MESH_LOCK documented; thread.join() note added |
| 11 | **Pre-Truncation Hash Validation** — canonical input fingerprint before context budget collapse | ✅ Implemented 2026-05-03 | `_normalize_fix_fingerprint()` L675-718 — normalizes cycle numbers, strips collapse artifacts, collapses whitespace; hash computed on `fix_input` BEFORE `call_ollama()` context truncation |
| 12 | **Snapshot Hash Verification** — content hash in save_proposal() + verification in apply_proposals() | ✅ Implemented 2026-05-03 | `pipeline_snapshot.py` — `save_proposal()` stores `hashlib.md5(content).hexdigest()` as `content_hash` in manifest; `apply_proposals()` rehashes from disk before `shutil.copy2()` and rejects on mismatch |
