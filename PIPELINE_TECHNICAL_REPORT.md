# Midway Mesh Consensus Pipeline — Technical Feature Report

**Document Type:** Comprehensive Technical Audit  
**Scope:** All pipeline features, subsystems, data flows, and operational safeguards  
**Status:** Read-only documentation. No code changes.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Entry Points & CLI](#2-entry-points--cli)
3. [Intent Classification & Routing](#3-intent-classification--routing)
4. [Cartridge System](#4-cartridge-system)
5. [Domain Registry](#5-domain-registry)
6. [Phase 0.5–3: Director & Task Decomposition](#6-phase-05-3-director--task-decomposition)
7. [Phase 4: Mesh Execution (DAG / Wave Engine)](#7-phase-4-mesh-execution-dag--wave-engine)
8. [Signal Protocol](#8-signal-protocol)
9. [Phase 5: Conflict Resolution](#9-phase-5-conflict-resolution)
10. [Phase 6: Integration Review & Fix Loop](#10-phase-6-integration-review--fix-loop)
11. [Preflight Checks & Architect Syntax Fix](#11-preflight-checks--architect-syntax-fix)
12. [Phase 7–8: Consensus & Final Approval](#12-phase-7-8-consensus--final-approval)
13. [Observability Pass](#13-observability-pass)
14. [Domain Sandbox (Cross-Domain File Guard)](#14-domain-sandbox-cross-domain-file-guard)
15. [Memory & Ledger System](#15-memory--ledger-system)
16. [Internal API Ledger](#16-internal-api-ledger)
17. [Session Timeline](#17-session-timeline)
18. [Token Budget Manager](#18-token-budget-manager)
19. [VRAM Budget Manager](#19-vram-budget-manager)
20. [Paging Kernel (Context Offload)](#20-paging-kernel-context-offload)
21. [Offload Store](#21-offload-store)
22. [Ollama Client & Streaming Resilience](#22-ollama-client--streaming-resilience)
23. [Checkpoint System](#23-checkpoint-system)
24. [Snapshot Manager](#24-snapshot-manager)
25. [Staging I/O Layer](#25-staging-io-layer)
26. [Session Manager](#26-session-manager)
27. [Streaming HTTP Server & SSE](#27-streaming-http-server--sse)
28. [Pipeline Stream Generator](#28-pipeline-stream-generator)
29. [Insanity Detector & Loop Guard](#29-insanity-detector--loop-guard)
30. [Tag Suggester](#30-tag-suggester)
31. [Fetch Handler & URL Signals](#31-fetch-handler--url-signals)
32. [File Hash Integrity Tracker](#32-file-hash-integrity-tracker)
33. [Tribunal / Appellate System](#33-tribunal--appellate-system)
34. [Pro Mode (Reasoning Gate)](#34-pro-mode-reasoning-gate)
35. [GDD Extractor & Librarian](#35-gdd-extractor--librarian)
36. [Context Extractor & File Relevance](#36-context-extractor--file-relevance)
37. [Data Models (models.py)](#37-data-models-modelspy)
38. [Model Role Assignments](#38-model-role-assignments)
39. [Output Artifacts](#39-output-artifacts)
40. [Module Dependency Map](#40-module-dependency-map)

---

## 1. Architecture Overview

The pipeline is a **thin generic kernel** layered with a **hot-swappable ecosystem cartridge**. The kernel handles orchestration, routing, VRAM management, context windowing, streaming, persistence, and review logic. The cartridge supplies all project-specific domain prompts, bridge contracts, agent rules, and reviewer checklists.

```
User Prompt
    │
    ▼
run_pipeline()          ← pipeline.py  (entry, session, progress)
    │
    ▼
run_mesh_pipeline()     ← pipeline.py  (checkpoint resume, intent routing, cartridge bootstrap)
    │
    ├─► CHAT route      ← call_ollama  (direct response with project context)
    ├─► INFORMATIONAL   ← Analyst agent with Librarian-first context
    └─► EXECUTE route
            │
            ▼
    Phase 0.5-3: Director (task decomposition, GDD, scope gate)
            │
            ▼
    Phase 4: run_tasks()        ← mesh_tasks.py  (DAG / wave execution)
            │
            ▼
    Phase 5-8: run_code_merge() ← mesh_finalize.py → _finalize_conflicts
                                                    → _finalize_review
                                                    → _finalize_preflight
                                                    → consensus + final approval
            │
            ▼
    Output saved to pipeline_output_<timestamp>.md
```

All state is carried in a single **`PipelineContext`** object (defined in `models.py`), which is passed by reference through every phase. There is no async/await; execution is purely synchronous.

---

## 2. Entry Points & CLI

**File:** `pipeline.py`  
**Entry function:** `run_pipeline(user_prompt, checkpoint_id=None, session_id=None)`

### CLI Arguments

| Argument | Description |
|---|---|
| `prompt` | Natural-language feature request (positional) |
| `--checkpoint <id>` | Resume pipeline from a saved checkpoint |
| `--list-checkpoints` | Print all saved checkpoints to stdout |
| `--session-id <id>` | Attach to an existing session for continuity tracking |
| `--chat` | Force CHAT mode, bypassing intent classification |

### Runtime Behaviour

- On startup, `run_pipeline()` creates or resumes a `SessionManager` instance.
- It calls `_emit_progress("init", "started", ...)` to notify any progress listeners registered on `PipelineContext`.
- After the pipeline completes, it calls `session_mgr.mark_completed()`.
- The final output string is returned and also printed to stdout.

---

## 3. Intent Classification & Routing

**File:** `pipeline.py`, `_helpers_exec.py`  
**Functions:** `is_likely_chat()`, `classify_intent()`

Before entering the mesh, every prompt is classified into one of three routes:

### Route: CHAT
- **Trigger:** `is_likely_chat()` returns `True` (heuristic pattern match for conversational phrasing), or `--chat` flag is passed, or the prompt is prefixed `[CHAT_FORCED]`.
- **Behaviour:** A single `call_ollama()` call using `CHAT_SYSTEM` prompt. Project state and directory structure are injected so the model has situational awareness.
- **Bypasses:** All mesh phases, director, review, and approval.

### Route: INFORMATIONAL
- **Trigger:** `classify_intent()` returns `"INFORMATIONAL"` via a small LLM call against `DIRECTOR_MODEL`.
- **Behaviour:** Runs a Librarian-first context assembly (GDD sections, project state, directory structure, relevant source files), then calls the Analyst agent in read-only mode.
- **Bypasses:** All write phases. No code is generated or committed.

### Route: EXECUTE
- **Trigger:** All other prompts, or when a BLOCKED checkpoint is resurrected.
- **Behaviour:** Full mesh pipeline (Phases 0.5–8).

### Resurrection Override
When a checkpoint with status `BLOCKED` is loaded, the intent classification is bypassed entirely. The pipeline re-hydrates the saved work queue and resumes from Phase 4, skipping Phases 0.5–3 to avoid re-running the director on an already-decomposed task set.

---

## 4. Cartridge System

**Files:** `cartridges/midway_ecosystem.py`, `cartridges/midway_data.py`  
**Model:** `models.EcosystemCartridgeContract`

The cartridge is the mechanism by which the pipeline's generic kernel becomes project-aware. It is hot-swappable at bootstrap time by calling `ctx.mount_cartridge(CartridgeClass)`.

### What a Cartridge Provides

| Field | Description |
|---|---|
| `domains` | Dictionary of `DomainConfig` entries keyed by domain ID (e.g., `"C++"`, `"Lua"`, `"OBSERVABILITY"`) |
| `alias_map` | Maps human-readable shorthand (e.g., `"lua"`, `"physics"`) to canonical domain keys |
| `environment_metadata` | Ecosystem-specific metadata per domain (file paths, build info, etc.) |
| `review_prompt_extra` | Injected into `REVIEW_PROMPT` at assembly time. Contains project-specific review checklist items. |
| `coding_mandates` | Appended to `LEDGER_MEMORY_RULE`. Governs binding libraries, API usage, and naming conventions. |
| `reasoning_gate_domains` | Set of domain keys whose outputs are routed through the Reasoning Gate before acceptance. |
| `terminology_note` | Injected into `ANALYST_SYSTEM` to prevent terminology confusion (e.g., "game" means "Attraction"). |
| `architecture_ledger` | Relative path to the architecture/director memory ledger. |

### Midway Cartridge Specifics (`midway_ecosystem.py`)

- Exposes `get_domain_registry()`, `get_review_prompt_extra()`, `get_director_extra()`, `get_coding_mandates()`, `get_bridge_contract()`, and `get_project_context()`.
- The review checklist hardcodes that `AttractionConstants.modifiers` is the correct modifier source, rejects Lua stdlib file/system APIs (`io.*`, `os.*`, `require()`, `package.*`), and enforces the `MidwayPhysics.OnStep(function(dt) ... end)` lifecycle contract.

### Cartridge Override Propagation

After mounting, `pipeline.py` propagates overridden model names and domain configs into:
- `domain_registry` module-level constants
- `ollama_client` module-level constants
- `_pipeline_helpers` module-level constants
- `ALL_DOMAINS` dict entries (per-domain `model` field)
- `_helpers_exec._ALL_DOMAINS` (the copy used by `execute_task()`)

This ensures every downstream module sees the cartridge's model assignments, not stale import-time defaults.

---

## 5. Domain Registry

**File:** `domain_registry.py`  
**Key exports:** `ALL_DOMAINS`, `get_agent_system()`, `resolve_agent_name()`, `PERSONA_MAP`

The domain registry defines every agent available to the Director. Each domain entry is a dictionary containing:

| Key | Description |
|---|---|
| `model` | LLM model name to use for this domain |
| `system_prompt` | Full system prompt for this agent |
| `ledger` | Path to this domain's memory ledger file |
| `allowed_extensions` | Set of file extensions this domain is permitted to write |
| `ledger_rule` | Instruction injected into the prompt controlling memory-write behaviour |
| `description` | Human-readable description of the domain's purpose |

### Standard Domains (Midway Cartridge)

| Domain Key | Role |
|---|---|
| `C++` | C++ engine and gameplay programmer |
| `PHYS` | Physics simulation and collision specialist |
| `SHADER` | GLSL/shader programming |
| `Lua` | Lua attraction scripting against the Midway bridge API |
| `DOC` | Documentation writer |
| `OBSERVABILITY` | Observability instrumentation (metrics, logging, diagnostics) |
| `CONF` | Configuration management |
| `TRIBUNAL` | Appellate reviewer / conflict arbiter |
| `LIBRARIAN` | Read-only documentation/GDD retriever |

### `get_agent_system(domain_key)`
Returns the assembled system prompt for a domain, incorporating the base domain prompt, any `MESH_AGENT_SYSTEM_EXTENSION` (memory/ledger rules), and cartridge-injected sandbox constraints.

### `resolve_agent_name(name)`
Maps an unqualified or aliased agent name (e.g., `"lua"`, `"coder"`, `"c++"`) to its canonical domain key, using the `AGENT_ALIAS_MAP`.

---

## 6. Phase 0.5–3: Director & Task Decomposition

**File:** `pipeline.py`, `_helpers_exec.py`  
**Function:** `build_director_prompt()`, director `call_ollama()` call

### Phase 0.5: GDD & Project State Injection

Before the Director runs, the pipeline assembles a comprehensive context package:
1. **GDD sections** — retrieved by `recursive_librarian()`, which queries the Librarian agent to extract relevant Game Design Document sections for the user's prompt.
2. **Project state** — `get_project_state()` scans the project root for completed features, todo items, domain availability, and build artefacts.
3. **Project structure** — `curate_project_structure()` walks the directory tree, filtering to files relevant to the prompt.
4. **Interface manifest** — surfaces public API headers or interface files.

### Phase 1–2: Director (Task Decomposition)

The Director model (`DIRECTOR_MODEL`, currently `llama3.1:8b-instruct-q4_K_M`) receives the enriched context and produces a structured task breakdown. Each task specifies:
- **Agent** — which domain should handle it
- **Spec** — what should be implemented
- **Dependencies** — which other tasks must complete first
- **Target file** — optional hint for output routing

The Director prompt (`DIRECTOR_SYSTEM`) is supplemented by the cartridge's `get_director_extra()`, which adds Midway-specific decomposition constraints (e.g., C++ handles engine mechanics, Lua handles attraction behaviour, they must not cross-write each other's files).

### Phase 3: Scope Gate

A scope guard enforces `SCOPE_FILE_LIMIT` (5) and `SCOPE_LINE_LIMIT` (400) ceilings. Tasks exceeding these limits are flagged. The gate prevents runaway context expansion before execution begins.

---

## 7. Phase 4: Mesh Execution (DAG / Wave Engine)

**File:** `mesh_tasks.py`  
**Function:** `run_tasks(ctx)`

This is the core execution engine. It processes the Director's task list as a Directed Acyclic Graph (DAG), executing tasks in dependency-resolved waves.

### DAG Construction

`ctx.task_map` is built from the task list. Each `Task` object carries its `depends_on` list. `sort_tasks_into_waves()` (from `mesh_wave_sorter.py`) performs a topological sort, grouping independent tasks into parallel-ready waves.

### Wave Execution

Tasks within the same wave have no mutual dependencies and are conceptually parallel (though the current implementation is single-threaded). Tasks in later waves are blocked until all tasks in preceding waves complete. This guarantees a C++ task always finishes before the Lua task that depends on its output.

### Task Execution (`execute_task()`)

**File:** `_helpers_exec.py`  
For each task, `execute_task()` assembles the prompt context in this order:

1. Original feature request
2. Director's task breakdown
3. Relevant file context (from `find_relevant_files()`)
4. GDD context (from Librarian)
5. **Internal API Ledger** (live confirmed symbol list — prevents hallucination of non-existent functions)
6. Referenced files cache
7. Parent task output (code artifacts only, stripped of prose)
8. Sibling task outputs (pre-sanitized)
9. Previous iteration output (code artifacts only, if iterating)
10. Task-specific context (query results, pro-test injections)

A **context character cap** of 4,000 chars is enforced on `task.context` to prevent VRAM overrun across 3+ iterations.

When `task.iteration >= MAX_ITERATIONS - 1`, the `SELF_CORRECT_SYSTEM` prompt is substituted to force the agent to critically review and fix its own previous output.

### Iteration Loop

Each task may run up to `MAX_ITERATIONS` (default: 3) times. If the output fails validation (signal rejection, empty code, review failure), the task is re-queued with the previous output as context for correction.

### Signal Processing (`_process_task_signals()`)

After each task completes, all embedded signals are parsed and dispatched. See [Section 8](#8-signal-protocol) for the full signal catalogue.

### Model Unloading

After each wave completes, `unload_model()` is called via `ollama_client` to release VRAM before the next wave loads its models, preventing accumulation across waves.

---

## 8. Signal Protocol

**File:** `signals.py`  
**Constants:** `SIGNAL_PATTERNS`, `DOUBLE_CHECK_PATTERN`

Agents communicate with the pipeline through structured in-band signals embedded in their text output. The pipeline parses these with regex patterns after each task completes.

### Signal Types

| Signal | Format | Effect |
|---|---|---|
| `QUERY` | `[QUERY: agent : question]` | Spawns a sub-task assigned to `agent` to answer the question. Result is injected back as context for the originating task. |
| `DELEGATE` | `[DELEGATE: agent : spec]` | Spawns a sub-task assigned to `agent` with the given spec. |
| `VETO` | `[VETO: reason]` | Marks the task output as rejected. Records to `ctx.all_vetos`. |
| `OBJECT` | `[OBJECT: reason]` | Weaker objection. Records to `ctx.all_objects`. |
| `APPEAL` | `[APPEAL: agent : reason]` | Escalates to the Tribunal agent. Queued in `ctx.pending_appeals`. |
| `APPROVE` | `[APPROVE]` | Marks the current output as reviewer-approved. |
| `REVISE` | `[REVISE: agent : instructions]` | Requests a targeted revision from the named agent. |
| `MERGE` | `[MERGE: file : content]` | Requests a content merge into the named file. |
| `REJECT` | `[REJECT: agent : reason]` | Rejects another agent's output. |
| `FETCH` | `[FETCH: url]` | Triggers an HTTP fetch of the given URL via `fetch_handler`. |
| `REQUEST_API` | `[REQUEST_API: name \| url]` | Registers a request for an external API with documentation URL. |
| `READ_OFFLOADED` | `[READ_OFFLOADED: block_id]` | Reads a previously paged-out context block from the offload store. |
| `EXTRACT_SKELETON` | `[EXTRACT_SKELETON: file]` | Extracts the structural skeleton of a source file for lightweight context. |
| `MATH_EVAL` | `[MATH_EVAL: expression]` | Evaluates a mathematical expression and returns the result. |
| `AST_PATCH` | `[AST_PATCH: file] ... [/AST_PATCH]` | Carries a discrete AST modification block for a named file. |
| `FLUSH` | `[FLUSH]` | Signals the pipeline to flush in-progress state to disk immediately. |

### Double-Check Block

Agents are instructed to end their output with a structured self-review section:

```
## Double-Check
Original Prompt: ...
What This Addresses: ...
What Remains Unresolved: ...
```

The `DOUBLE_CHECK_PATTERN` regex extracts these three fields. The unresolved items are fed back into the next iteration or used by the review phase.

---

## 9. Phase 5: Conflict Resolution

**File:** `_finalize_conflicts.py`  
**Called by:** `mesh_finalize.py::run_code_merge()`

After all tasks complete, outputs from different agents may contain conflicting edits to the same file. Phase 5 detects these and runs a dedicated conflict-resolution pass.

- Each file that appears in multiple agent outputs is identified.
- The conflicting blocks are presented to the Reviewer model with the full context of both changes.
- The resolver produces a merged output, selecting or combining the non-conflicting portions.
- Conflict resolutions are appended to `ctx.conflict_resolutions` for audit.

---

## 10. Phase 6: Integration Review & Fix Loop

**File:** `_finalize_review.py`  
**Called by:** `mesh_finalize.py::run_code_merge()`

Phase 6 applies a multi-pass integration review to the combined output of all tasks.

### Review System Prompt (`REVIEW_SYSTEM`, `REVIEW_PROMPT`)

The review prompt checks for:
- Cross-domain rule violations
- Memory safety issues (buffer overflows, null dereferences)
- Phantom / hallucinated API calls not present in the project
- Empty or stub implementations with no concrete code
- Observability alignment (instrumentation contract compliance)
- Rule compliance against the cartridge's `review_prompt_extra` checklist

### Fix Loop

The reviewer runs up to `REVIEW_MAX_ITERATIONS` (default: 4) times. If the verdict is not `APPROVED`:
- The reviewer's critique is extracted.
- The failing agent's output is re-submitted to the original agent with the critique as additional context.
- The new output replaces the old entry in `ctx.all_results_dict`.
- The review runs again on the corrected output.

The **insanity detector** (`check_insanity_similarity()`) monitors fix attempts. If the normalized fix prompt is ≥95% similar to a previously seen prompt (by `SequenceMatcher.quick_ratio()`), the loop is broken to prevent infinite repair cycles.

---

## 11. Preflight Checks & Architect Syntax Fix

**File:** `_finalize_preflight.py`  
**Called by:** `mesh_finalize.py::run_code_merge()`

### Empty Output Detection

`_inject_empty_output_errors()` scans all task outputs for tasks that produced no code. It distinguishes:

- **Delegate-only outputs:** Outputs that contain only `[DELEGATE:...]` signals with no concrete code are treated as no-code outputs. A fix demand is injected.
- **Single-block fallback broadcast:** If one task produced code but other tasks are empty and the single block is not a plausible fill for all empty tasks, the block is NOT broadcast across them. Each empty task receives an individual targeted fix demand.
- **Genuinely empty outputs:** A fix demand is injected instructing the agent to produce concrete implementation.

### Lua Syntax Check

For tasks assigned to the `Lua` domain, the output is run through `luac -p` (Lua compiler in parse-only mode). If syntax errors are detected, they are formatted and injected into the fix demand for the next iteration.

### Architect Syntax Fix Loop

For code that passes Lua syntax but fails the reviewer's structural checks, an Architect Fix cycle (`ARCHITECT_FIX_SYSTEM`) is triggered. The architect is a specialised persona instructed to fix structural and API-contract violations without changing the feature's intent.

---

## 12. Phase 7–8: Consensus & Final Approval

**File:** `mesh_finalize.py`  
**Models:** `REVIEWER_MODEL`, `FINAL_APPROVAL_SYSTEM`

### Consensus (Phase 7)

`ctx.consensus_checks` is populated by the review loop. For each task output that received a review verdict, the verdict (`APPROVED` / `REJECTED` / `CONDITIONAL`) is recorded. The pipeline aggregates these into a pass/fail summary.

If `ctx.final_verdict == "VRAM_OVERRUN"` at this point, the pipeline aborts and saves a failure report. The VRAM overrun flag is set by the Ollama client when two consecutive connection-reset errors occur for the same task.

### Final Approval (Phase 8)

A `FINAL_APPROVAL_SYSTEM` call presents the consolidated output to the reviewer model one last time with the full consensus summary. This approval is the last gate before the output is written to disk.

If final approval fails, `generate_failure_report()` is called, which produces a structured markdown failure report containing:
- The original prompt
- Each agent's output
- The reviewer's critique
- Consensus check results
- Recommended next steps

---

## 13. Observability Pass

**File:** `mesh_finalize.py` (`_run_observability_pass()`)

After conflict resolution and before the final review, a dedicated Observability pass runs. The `OBSERVABILITY` domain agent (`phi3:14b`) is called with the merged output and instructed to:
- Inject instrumentation (metrics, log statements, diagnostics) into the code where appropriate.
- Output the **entire modified file** as a plain code block (not as SEARCH/REPLACE diff markers, which would cause the sanitizer to self-reject the output).
- Follow the project's observability mandate from `midway_data.py`.

The observability output replaces the relevant sections of `ctx.all_results_dict` before the final review runs. This guarantees observability is always present in the final output regardless of whether the domain agents included it in their task outputs.

---

## 14. Domain Sandbox (Cross-Domain File Guard)

**File:** `_domain_sandbox.py`  
**Functions:** `validate_domain_file_write()`, `reject_cross_domain_output()`, `build_sandbox_constraint()`

Every agent is restricted to writing files within its allowed extension set. The sandbox enforces this at two levels:

### Constraint Injection (Prompt Level)
`build_sandbox_constraint()` generates a `CRITICAL FILE RESTRICTION` paragraph that is appended to the agent's system prompt. For example, the Lua agent receives:
> *"You are physically restricted to modifying ONLY [.lua] files. Any SEARCH/REPLACE blocks targeting .cpp, .h, .hpp, .py, .md, .json, or any other extension will trigger a fatal system error and your output will be discarded."*

### Output Validation (Post-generation Level)
`validate_domain_file_write()` scans the agent's output for SEARCH/REPLACE blocks and extracts the file paths referenced. If any path's extension falls outside the domain's `allowed_extensions`, the violation is logged.

`reject_cross_domain_output()` wraps this validation with a hard reject: the output is replaced with a safe stub error message so it cannot contaminate downstream phases.

---

## 15. Memory & Ledger System

**File:** `ledger.py`  
**Memory directory:** `docs/memory/`

Each agent domain has a persistent memory ledger file (e.g., `docs/memory/cpp_ledger.md`). The ledger is a markdown file where the agent records facts it wants to remember across pipeline runs — architectural decisions, completed subtasks, known issues.

### `_append_to_ledger(output, agent_key, task_spec)`

After each task completes, the output is parsed for ledger-worthy content and appended to the domain's ledger file. A header (`ensure_ledger_header()`) is enforced to give each entry a canonical anchor-linkable title.

### `ledger_toc(domain_key)`

Builds a Table of Contents from all entries in a domain's ledger file using anchor-based heading detection. Used to inject a compact summary of historical decisions into future task contexts.

### `build_anchor_toc(doc_path)`

Generalized TOC builder for any markdown document. Produces anchor links from `## [...]` heading patterns.

### LRU Doc Cache (`_get_doc_cached()`)

Documentation and ledger files are cached in memory with a 5-minute TTL and a max capacity of 8 entries. The oldest entry is evicted on overflow. This prevents repeated disk reads for frequently-accessed reference documents.

---

## 16. Internal API Ledger

**File:** `ledger.py`  
**Functions:** `extract_api_signatures()`, `update_internal_api_ledger()`, `read_internal_api_ledger()`  
**File on disk:** `docs/internal_api_ledger.md`

The Internal API Ledger is the shared source of truth for all function signatures, class interfaces, and API endpoints generated or modified during a pipeline run.

### Write Path

After each task completes, `extract_api_signatures()` scans the output for code fences containing function signatures, class declarations, and method definitions. These are deduplicated and appended to the internal API ledger via `update_internal_api_ledger()`.

### Read Path (`read_internal_api_ledger()`)

At the start of every `execute_task()` call, the live ledger is read (up to 3,000 chars) and injected into the agent's context under the heading `## Internal API Ledger`. This ensures every downstream agent has a definitive list of confirmed symbols, preventing hallucination of non-existent function names.

### `_generate_module_name(task_spec, agent_key)`

Derives a stable, human-readable module name from a task spec and agent key. Used to generate consistent ledger section headings.

---

## 17. Session Timeline

**File:** `ledger.py`  
**Function:** `log_to_session_timeline()`  
**File on disk:** `docs/memory/session_timeline.md`

After each pipeline run, a timeline entry is written recording:
- Timestamp
- Agent assigned
- User input
- Tools/files accessed
- Final output (truncated to 4,000 chars)

Entries are written in **reverse chronological order** (newest at top) so they appear within the model's token budget in the next run's context window. Writes use a read-prepend-write atomic pattern via a temp file.

---

## 18. Token Budget Manager

**File:** `token_budget.py`  
**Class:** `TokenBudget`

Provides token estimation and budget enforcement before any prompt is sent to Ollama.

### Per-Model Context Limits (`MODEL_TOKEN_LIMITS`)

Each model has a configured `(threshold, context_window)` pair where the threshold is 80% of the context window (the `VRAM_CRITICAL_RATIO`). The 80% ceiling provides headroom for output tokens and KV cache overhead.

| Model | Threshold | Context Window |
|---|---|---|
| `qwen2.5-coder:7b` | 6,553 tokens | 8,192 |
| `phi3:14b` | 6,553 tokens | 8,192 |
| `phi3.5:latest` | 13,107 tokens | 16,384 |
| `llama3.1:8b` | 6,553 tokens | 8,192 |
| `llama3.2:1b` | 6,553 tokens | 8,192 |

### `TokenBudget.estimate_tokens(text)`

Uses a **density-aware estimation heuristic**:
- Normal text/code: 1 token ≈ 3 characters
- Dense data (base64, hex arrays, Unicode — alpha density > 60%): 1 token ≈ 1.5 characters (conservative)

### Budget Enforcement

When the estimated token count for a prompt exceeds the threshold for the target model, a `VRAM Kernel Interrupt` warning is prepended to the prompt instructing the agent to use `<PAGE_OUT>` to offload non-essential content.

---

## 19. VRAM Budget Manager

**File:** `vram_budget.py`

Tracks which models are currently resident in VRAM and their estimated memory footprint in GB.

### Registry

`_active_registry: Dict[str, float]` maps model names to their estimated VRAM cost. `_total_used_gb` tracks the running total.

### Eviction Selection

When additional VRAM is needed, the eviction algorithm selects the smallest models first (least costly to reload) until sufficient capacity is freed. This minimizes reload overhead by preferring to evict cheap small models over expensive large ones.

### `reset_budget()`

Clears the registry and resets the total. Called at pipeline start and after major phase transitions.

---

## 20. Paging Kernel (Context Offload)

**File:** `paging_kernel.py`

The paging kernel implements a **software context window** — a virtual memory system for LLM context that allows agents to page content in and out of the active context window when VRAM limits would otherwise be exceeded.

### Core Classes

#### `PagingBuffer`
A streaming token accumulator that watches for `<PAGE_OUT>` and `<PAGE_IN>` tokens within the model's output stream in real time. When a page token is detected, it:
1. Captures the content that precedes the token.
2. Identifies what should be paged out (by concept or explicit file reference).
3. Triggers the page operation without interrupting the stream.

#### `PagingController`
The stateful orchestrator of the paging lifecycle for a single task execution.

| Method | Description |
|---|---|
| `feed_token(token)` | Processes each streamed token. Returns `(should_page, page_info)`. |
| `execute_page(page_info)` | Performs the page-out or page-in operation. |
| `build_resume_payload(system_prompt)` | Constructs the message array for resuming generation after a page operation, including the Ghost Buffer (paged-out content summary). |
| `_aggressive_history_truncation()` | Reduces message history when context has grown dangerously large, prioritising the most recent content. |
| `reset_cycle()` | Resets paging state for the next task. |

#### `ActiveMessages`
A stateful message array that maintains the full conversation history for multi-turn paging cycles. Supports `inject_system()`, `inject_continuation()`, and `pop_last_user()` for surgical message manipulation.

### Page-In Mechanics (`handle_page_in()`)

- Accepts a file path with optional line range (e.g., `src/Engine.cpp:100-200`) or a search term.
- Reads the relevant content from disk.
- Returns a formatted context block with file path, content, and line count metadata.
- Uses `_resolve_dynamic_page_limit()` to compute a safe content size based on the currently allocated context window.

### Page-Out Mechanics (`handle_page_out()`)

- Stores the content to be offloaded in the `OffloadStore` with a generated block ID.
- Returns a stub placeholder so the model knows what was offloaded and how to retrieve it.

### VRAM Stub Detection (`detect_vram_stubs()`)

Scans model output for `[VRAM_STUB: block_id]` markers — placeholders left by a previous page-out that can be filled in by a subsequent page-in.

---

## 21. Offload Store

**File:** `offload_store.py`  
**Class:** `OffloadStore`

A disk-backed overflow buffer for context blocks that have been paged out.

### Storage Layout

Each block is stored as an individual JSON file in `midway-pipeline/offload_store/`. A `.index_cache.json` file provides fast lookups without reading all block files.

### Index Caching

The index is lazy-loaded with a 5-minute TTL. Stale index reads fall back to a fresh disk scan.

### Operations

| Operation | Description |
|---|---|
| `store(block_id, content, metadata)` | Saves a content block with its metadata. |
| `retrieve(block_id)` | Reads a block by ID. Returns `None` if not found. |
| `list()` | Returns all block IDs in the store. |
| `delete(block_id)` | Removes a specific block. |
| Garbage Collection | Expired blocks (by TTL or reference count) are purged during GC passes. |

---

## 22. Ollama Client & Streaming Resilience

**File:** `ollama_client.py`  
**Functions:** `call_ollama()`, `call_ollama_streamed()`, `unload_model()`

The Ollama client is the only layer that communicates directly with the Ollama HTTP API. All model calls in the pipeline route through this module.

### `call_ollama(system, user, label, model, params, messages)`

Synchronous model call. Streams the response internally and returns the accumulated string. The `messages` parameter allows stateful multi-turn conversations (used by the paging kernel's resume path).

### `call_ollama_streamed(system, user, label, model, params)`

Generator-based streaming call. Yields tokens as they arrive. Used by the streaming server and progress-listener infrastructure.

### Context Window Policy

`resolve_ctx_size(model_name)` selects the context window size from a per-model table. This was a critical fix: previously all models used the global `OLLAMA_NUM_CTX` ceiling, causing heavy models to request windows beyond their VRAM capacity and triggering offload/slowdown. The current policy uses 8,192 for 7B–14B models and 16,384 for the pre-summarizer (phi3.5, a 3.8B model with sufficient headroom).

### Stream Resilience (`_cooldown_and_retry()`)

When a `ConnectionResetError` (Windows WinError 10054) is caught mid-stream:
1. A critical warning is printed with thermal/timeout context.
2. A 5-second VRAM cooldown sleep is executed.
3. The LLM temperature is decremented by 0.1 (floor: 0.1) to reduce output variance on retry.
4. A single automatic retry is attempted.
5. If a second connection reset occurs, `_stream_crashed = True` is set, the output is discarded, and `ctx.final_verdict` is set to `"VRAM_OVERRUN"`.

### Model Unloading

`unload_model(model_name)` sends a keep_alive=0 request to Ollama to evict the model from VRAM immediately. Called between waves to prevent model accumulation.

---

## 23. Checkpoint System

**File:** `checkpoint.py`  
**Functions:** `save_checkpoint()`, `load_checkpoint()`, `list_checkpoints()`  
**Storage:** `.pipeline_checkpoints/` directory (JSON files)

### Save

`save_checkpoint(checkpoint_id, phase, data)` writes a JSON file containing:
- `checkpoint_id` — unique identifier (UUID or custom string)
- `phase` — pipeline phase at time of save (e.g., `"director"`, `"mesh_execution"`, `"BLOCKED"`)
- `timestamp` — ISO 8601 datetime
- `data` — arbitrary pipeline state dict (tasks, outputs, signals, etc.)

### Load

`load_checkpoint(checkpoint_id)` reads and deserialises the checkpoint JSON. Returns `None` if the file is missing or corrupt.

### List

`list_checkpoints()` returns all checkpoints sorted by filename. Accessible via `--list-checkpoints` CLI flag.

### Resurrection

When `pipeline.py` detects a loaded checkpoint with `phase == "BLOCKED"`, it re-hydrates the task work queue from the checkpoint data, rebuilds `ctx.task_map`, and sets `ctx.resumed_blocked = True` to bypass intent classification and Phases 0.5–3.

---

## 24. Snapshot Manager

**File:** `pipeline_snapshot.py`  
**Class:** `SnapshotManager`

The Snapshot Manager provides a **per-run proposal and diff system** for safely applying agent-generated code changes to the project.

### Lifecycle

| Phase | Operation |
|---|---|
| Before run | `save_originals(file_paths)` — copies the current state of all in-scope files to a snapshot directory. |
| During run | `save_proposal(persona, task_id, rel_path, content)` — stores each agent's proposed change without applying it. |
| After run | `generate_diff(rel_path)` — produces a unified diff between original and proposal. |
| Apply | `apply_proposals(rel_paths)` — writes proposals to the real project tree. |
| Revert | `revert_all()` — restores all files to their pre-run state. |
| Cycle revert | `revert_to_cycle(rel_path, cycle_index)` — restores a file to a specific historical proposal cycle. |

### Storage Layout

Each run gets a directory in `.pipeline_snapshots/<run_id>/` containing:
- `originals/` — untouched copies of all source files
- `proposals/` — one file per agent+task combination
- `manifest.json` — index of all snapshots for this run

### Agent Output Ingestion

`save_agent_output(persona, task_id, output_text)` extracts code blocks from the raw agent output using `_parse_code_blocks()` and `_try_infer_filepath()`, then stores each extracted file separately as a proposal. This allows the snapshot system to work even when agents do not explicitly name their target files.

---

## 25. Staging I/O Layer

**File:** `_helpers_io.py`  
**Functions:** `enable_staging()`, `disable_staging()`, `commit_staging()`, `is_staging_active()`, `get_staging_path()`

The staging layer intercepts all file writes during finalization and redirects them to a temporary staging directory. This protects the real source tree from partial or failed writes.

### `enable_staging()`

Activates staging mode. All subsequent `atomic_write_text()` calls and SEARCH/REPLACE applications are redirected to `<project_root>/.pipeline_staging/`.

### `get_staging_path(target_path)`

Computes the staging-area path for any target path. Used by write operations to transparently redirect output.

### `commit_staging()`

Moves all staged files atomically into their final target paths in the real project tree. Returns the count of committed files. This is the only point at which the real source tree is modified.

### `disable_staging()`

Deactivates staging mode. Staged files that have not been committed are discarded.

### `is_staging_active()`

Returns `True` if the staging mode flag is set. Checked by write operations to decide whether to redirect.

---

## 26. Session Manager

**File:** `pipeline_session.py`  
**Class:** `SessionManager`  
**Function:** `get_or_create_session()`

Provides **run continuity tracking** across pipeline invocations. Each session has a unique ID (SHA-256 hash of timestamp + prompt) and persists metadata to `.pipeline_sessions/`.

### Session Metadata

| Field | Description |
|---|---|
| `session_id` | Unique identifier |
| `user_prompt` | Original prompt text |
| `status` | `active`, `completed`, `failed` |
| `phase` | Current pipeline phase |
| `checkpoint_id` | Last saved checkpoint for this session |
| `model` | Model used for execution |
| `message_count` | Number of model calls made |
| `created_at` | ISO 8601 creation timestamp |
| `updated_at` | ISO 8601 last-updated timestamp |

### Key Methods

| Method | Description |
|---|---|
| `update_phase(phase)` | Records the current pipeline phase and flushes metadata. |
| `update_checkpoint(checkpoint_id)` | Saves the checkpoint ID for resume support. |
| `increment_message_count()` | Tracks how many model calls have been made. |
| `mark_completed()` / `mark_failed()` | Sets final session status. |
| `list_recent_sessions(limit)` | Returns the N most recent sessions from the index. |
| `list_active_sessions(limit)` | Returns sessions with `status == "active"`. |

---

## 27. Streaming HTTP Server & SSE

**File:** `pipeline_stream_server.py`  
**Class:** `StreamHandler`, `ThreadingHTTPServer`  
**Default:** `0.0.0.0:8765`

Exposes the pipeline over HTTP as a **Server-Sent Events (SSE)** stream and as an **OpenAI-compatible `/v1/chat/completions` endpoint**, allowing IDE extensions (e.g., Continue) to connect natively.

### Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/health` | GET | Returns `{"status": "ok", "timestamp": ...}` |
| `/stream` | GET | SSE stream of pipeline output tokens. Accepts `prompt`, `checkpoint`, `session_id` query params. |
| `/v1/chat/completions` | POST | OpenAI-compatible chat endpoint. Accepts `messages`, `stream`, `session_id` in JSON body. |
| `/v1/models` | GET | Returns a static model list for OpenAI compatibility. |
| `/` | GET | Returns a minimal HTML index page. |

### Threading

`ThreadingHTTPServer` extends `HTTPServer` with `ThreadingMixIn`, giving each request its own thread. `daemon_threads = True` ensures threads do not block server shutdown.

### CORS

All responses include permissive CORS headers (`Access-Control-Allow-Origin: *`) to allow browser-based clients to connect.

---

## 28. Pipeline Stream Generator

**File:** `pipeline_stream.py`  
**Function:** `stream_pipeline_generator(prompt, checkpoint_id, session_id)`

Wraps `run_pipeline()` in a generator that yields tokens as they are produced by the underlying streaming model calls.

### Instrumentation

`_instrumented_call_streamed()` monkey-patches `call_ollama_streamed` for the duration of the run. Every token yielded by any model call within the pipeline is intercepted by `_token_callback()`, which adds it to a thread-safe token queue. The generator reads from this queue and yields tokens to the HTTP server.

### Progress Events

Special progress events (phase transitions, errors, completions) are injected into the token stream as SSE `data:` lines with a `[PROGRESS]` prefix, allowing clients to track pipeline phases in real time.

---

## 29. Insanity Detector & Loop Guard

**File:** `ledger.py`  
**Functions:** `_normalize_fix_fingerprint()`, `check_insanity_similarity()`

Prevents the review/fix loop from entering an infinite repair cycle where the same broken output is submitted and rejected repeatedly.

### Fingerprint Normalisation

Before comparing, the fix prompt is normalised to remove:
- Cycle numbers (`Cycle 2` → `Cycle N`)
- Collapsed/truncated code markers (`[... body collapsed ...]`)
- Excessive blank lines

### Similarity Check

`check_insanity_similarity(normalized, seen_set, threshold=0.95)` uses `difflib.SequenceMatcher.quick_ratio()` for O(n) approximate string comparison. If any previously seen fingerprint matches the current one at ≥95%, the loop is broken.

---

## 30. Tag Suggester

**File:** `tagsuggester.py`  
**Class:** `TagSuggester`

A post-pipeline analysis utility that scans the session timeline for patterns suggesting tags relevant to stability, regression risk, and feature classification.

### Methods

| Method | Description |
|---|---|
| `analyze(*args)` | Scans the session timeline and returns a list of suggested tag strings. |
| `suggest_stable(*args)` | Returns a stability tag suggestion based on timeline patterns. |
| `suggest_regression(*args)` | Returns a regression-risk tag based on recurring failure patterns. |

Used during post-processing after `finalize_mesh()` to annotate the output report with suggested workflow tags.

---

## 31. Fetch Handler & URL Signals

**File:** `fetch_handler.py`  
**Functions:** `handle_fetch_signal()`, `read_offloaded_file()`, `handle_read_offloaded_signal()`

### `handle_fetch_signal(fetch_tag)`

Processes a `[FETCH: url]` signal. Performs an HTTP GET to the specified URL and returns the response body (truncated to a safe size for context injection). Used when an agent needs live documentation or API reference data.

### `handle_read_offloaded_signal(block_id, task_context)`

Processes a `[READ_OFFLOADED: block_id]` signal. Retrieves the block from the `OffloadStore` and returns it formatted for context injection.

### `_page_out_context(context_text, needed_chars)`

Selects a portion of the context text to page out when the context is over the safe limit. Prefers to page out the oldest/least-recently-used sections first.

---

## 32. File Hash Integrity Tracker

**File:** `_helpers_io.py`  
**Functions:** `compute_file_hash()`, `save_initial_file_hashes_from_context()`, `verify_file_hashes()`, `get_tracked_file_hashes()`

Before the pipeline modifies any file, a SHA-256 hash of its current content is recorded. At the end of the pipeline, hashes are re-computed and compared.

### Purpose

- Detects unintended modifications (files changed by the pipeline that were not in the approved task scope).
- Provides an audit trail of which files changed and which did not.
- Can be used as a gate: if files outside the scope changed, the run can be flagged.

### `ctx.file_hashes`

Populated by `save_initial_file_hashes_from_context()` from the file context that was assembled for the Director. Carries `{relative_path: sha256_hex}` pairs.

---

## 33. Tribunal / Appellate System

**File:** `models.py`, `mesh_tasks.py`, domain `TRIBUNAL`

When an agent issues an `[APPEAL: reason]` signal, the conflict is escalated to the Tribunal.

### State Fields

| Field | Description |
|---|---|
| `ctx.pending_appeals` | List of appeal dictionaries queued for tribunal review |
| `ctx.tribunal_verdicts` | Dict mapping appeal keys to `SUSTAINED` / `OVERRULED` verdicts |

### Tribunal Agent

The `TRIBUNAL` domain uses the `REASONING_MODEL` (phi3:14b) and is specialised for impartial cross-domain conflict adjudication. It reviews both the original output and the appealing agent's objection, then issues a binding verdict. A `SUSTAINED` verdict can force an agent to revise its output; an `OVERRULED` verdict accepts the original.

---

## 34. Pro Mode (Reasoning Gate)

**File:** `_helpers_exec.py`, `_prompts.py`  
**Constants:** `REASONING_GATE_DOMAINS`, `REASONING_GATE_SYSTEM`

For tasks whose domain is listed in `REASONING_GATE_DOMAINS` (defined by the cartridge), an additional reasoning model call is made before the main execution model call.

### Purpose

The reasoning gate substitutes a more capable reasoning model (`REASONING_MODEL`, currently phi3:14b) for tasks that require deep logical analysis — physics calculations, complex state machine design, or mathematical verification.

### Activation

- **Per-task:** Set by the Director via a `[PRO_MODE]` marker in the task spec, recorded in `ctx.math_heavy_tasks`.
- **Global override:** Set interactively if the user answers "always" at a runtime prompt, stored in `ctx.pro_mode_always`.
- **Domain-based:** Any task in a domain listed in `REASONING_GATE_DOMAINS` automatically uses the reasoning model.

---

## 35. GDD Extractor & Librarian

**Files:** `gdd_extractor.py`, `_helpers_exec.py`  
**Functions:** `extract_gdd_sections()`, `recursive_librarian()`

### `extract_gdd_sections(prompt, gdd_text)`

Parses the Game Design Document (GDD) markdown and extracts sections whose keywords match the user's prompt. Uses `KEYWORD_TO_SECTION` to map feature terms to document headings.

### `recursive_librarian(user_prompt)`

Invokes the `LIBRARIAN` agent (`LIBRARIAN_MODEL`, llama3.1:8b) with the user's prompt to retrieve the most relevant GDD sections. "Recursive" refers to the fact that the Librarian can follow cross-references within the GDD to pull in related sections, not just exact matches.

The Librarian operates in read-only mode — it cannot issue write signals and its output is used only for context enrichment.

---

## 36. Context Extractor & File Relevance

**File:** `context_extractor.py`, `_helpers_io.py`  
**Function:** `find_relevant_files(prompt, persona, project_root)`

### `find_relevant_files()`

Given a prompt and a domain persona, this function scores all source files in the project root by relevance. The scoring uses:
- Filename and path substring matching against keywords in the prompt
- File extension matching against the domain's `allowed_extensions`
- Recency heuristics (recently modified files get a small boost)
- Explicit include/exclude lists per persona

Returns an ordered list of `(relative_path, content_preview)` tuples, capped at `SCOPE_FILE_LIMIT` (5 files) and `SCOPE_LINE_LIMIT` (400 lines each).

### VRAM Stub Insertion

`_make_vram_stub(rel_path, content)` replaces large file contents with a compact stub `[VRAM_STUB: block_id]` when the file content would push the context over the VRAM-safe threshold. The full content is stored in the `OffloadStore` and can be retrieved via a `[READ_OFFLOADED]` signal.

---

## 37. Data Models (`models.py`)

### `SignalType` (Enum)

Enumerates all valid signal types: `QUERY`, `DELEGATE`, `VETO`, `OBJECT`, `APPEAL`, `APPROVE`, `REVISE`, `MERGE`, `REJECT`, `FETCH`, `FLUSH`, `AST_PATCH`, `MATH_EVAL`, `EXTRACT_SKELETON`, `READ_OFFLOADED`, `REQUEST_API`.

### `OrchestrationConfig`

Pydantic model for pipeline tuning parameters. Carrying fields for all model names, iteration limits, context sizes, and timeouts. Populated by the cartridge and propagated to globals by `pipeline.py`.

### `MeshSignal`

Represents a parsed signal. Fields: `type (SignalType)`, `target_agent`, `content`, `source_task_id`.

### `ConsensusResult`

Records a reviewer verdict for one task. Fields: `task_id`, `verdict (APPROVED|REJECTED|CONDITIONAL)`, `critique`, `iteration`.

### `TaskDescriptor`

Lightweight descriptor for Director-generated tasks before they become full `Task` objects. Fields: `agent`, `spec`, `depends_on`, `target_file`.

### `Task`

The main work-item class. Plain Python (not Pydantic) for backward compatibility. Key fields:

| Field | Type | Description |
|---|---|---|
| `agent` | str | Domain key or alias |
| `spec` | str | Task specification from Director |
| `parent` | str | Parent task ID (for spawned sub-tasks) |
| `task_id` | str | Unique identifier |
| `depends_on` | List[str] | Task IDs that must complete first |
| `target_file` | str | Optional output file path hint |
| `iteration` | int | Current fix iteration count |
| `output` | str | Last model output |
| `signals` | List[MeshSignal] | Extracted signals from output |
| `completed` | bool | Whether this task is done |
| `pinned_blocks` | Set[str] | Block IDs pinned to prevent page-out |
| `paged_files_cache` | Dict[str, str] | In-memory cache of paged-in file content |

### `PipelineContext`

The single authoritative state bag. Notable fields beyond what is listed above:

| Field | Description |
|---|---|
| `seen_code_hashes` | SHA-256 hashes of all code blocks seen — used by insanity detector |
| `all_vetos / all_objects / all_recourses` | Accumulated signal records |
| `conflict_resolutions` | Resolved conflict descriptions |
| `active_code_index` | Maps file paths to the most recent code for that file |
| `domain_registry / alias_map` | Mounted cartridge's domain and alias tables |
| `mounted_cartridge` | The `EcosystemCartridgeContract` object currently in use |

---

## 38. Model Role Assignments

| Role | Model | Purpose |
|---|---|---|
| `EXECUTION_MODEL` | `qwen2.5-coder:7b` | Primary code generation model for all domain agents |
| `CODER_MODEL` | `qwen2.5-coder:7b` | Alias for EXECUTION_MODEL |
| `REVIEWER_MODEL` | `phi3:14b` | Code review, conflict resolution, consensus |
| `DIRECTOR_MODEL` | `llama3.1:8b-instruct-q4_K_M` | Task decomposition and intent classification |
| `REASONING_MODEL` | `phi3:14b` | Pro-mode reasoning gate |
| `LIBRARIAN_MODEL` | `llama3.1:8b-instruct-q4_K_M` | GDD and document retrieval |
| `PRE_SUMMARIZER_MODEL` | `phi3.5:latest` | Large-context compression before phi3:14b review |
| `SYNTAX_GATE_MODEL` | `qwen2.5-coder:1.5b` | Lightweight syntax validation |
| `INTENT_CLASSIFIER_MODEL` | `llama3.2:1b` | Lightweight prompt routing classification |
| `CHAT_MODEL` | `qwen2.5-coder:7b` | Direct conversational responses |

All model assignments are overridable per-run via cartridge `OrchestrationConfig`.

---

## 39. Output Artifacts

Each pipeline run produces the following artifacts:

| Artifact | Location | Description |
|---|---|---|
| `pipeline_output_<timestamp>.md` | `midway/` project root | Full pipeline output including all agent outputs, review results, and final approval. |
| `pipeline_abort_<timestamp>.md` | `midway/` project root | Saved only on VRAM overrun abort. Contains the partial output and abort reason. |
| `docs/internal_api_ledger.md` | `midway/docs/` | Accumulated confirmed API signatures from all runs. |
| `docs/memory/active_run_ledger.md` | `midway/docs/memory/` | Ledger entries written during the most recent run. |
| `docs/memory/session_timeline.md` | `midway/docs/memory/` | Reverse-chronological record of all pipeline events. |
| `docs/memory/<domain>_ledger.md` | `midway/docs/memory/` | Per-domain persistent memory ledger. |
| `.pipeline_checkpoints/<id>.json` | `midway-pipeline/` | Checkpoint files for interrupted run resumption. |
| `.pipeline_sessions/<id>/` | `midway-pipeline/` | Session metadata for continuity tracking. |
| `.pipeline_snapshots/<run_id>/` | `midway-pipeline/` | Pre/post-run file snapshots and diffs. |
| `offload_store/` | `midway-pipeline/` | Paged-out context blocks. |

---

## 40. Module Dependency Map

```
pipeline.py
├── models.py                   (PipelineContext, Task, SignalType, etc.)
├── signals.py                  (SIGNAL_PATTERNS, extract_signals, parse_signal)
├── domain_registry.py          (ALL_DOMAINS, get_agent_system, resolve_agent_name)
├── ollama_client.py            (call_ollama, call_ollama_streamed, unload_model)
├── token_budget.py             (TokenBudget, MODEL_TOKEN_LIMITS)
├── _prompts.py                 (DIRECTOR_SYSTEM, REVIEW_SYSTEM, REVIEW_PROMPT, ...)
├── _pipeline_helpers.py        (facade re-exporting from helpers sub-modules)
│   ├── _helpers_exec.py        (execute_task, classify_intent, recursive_librarian)
│   ├── _helpers_io.py          (staging, find_relevant_files, atomic_write_text)
│   └── _helpers_text.py        (is_likely_chat, strip_to_code_artifacts, generate_failure_report)
├── ledger.py                   (ledger append, API ledger, session timeline, insanity detector)
├── checkpoint.py               (save/load/list checkpoints)
├── vram_budget.py              (VRAM registry, eviction)
├── offload_store.py            (disk-backed context overflow)
├── paging_kernel.py            (PagingController, PagingBuffer, handle_page_in/out)
├── fetch_handler.py            (URL fetch, read_offloaded)
├── context_extractor.py        (file relevance scoring)
├── gdd_extractor.py            (GDD section extraction)
├── tagsuggester.py             (post-run tag analysis)
├── mesh_loops.py               (lazy-import facade for run_tasks, run_code_merge)
│   ├── mesh_tasks.py           (Phase 4: DAG execution, signal processing)
│   │   └── mesh_wave_sorter.py (topological wave sort)
│   └── mesh_finalize.py        (Phase 5-8 facade)
│       ├── _finalize_conflicts.py   (Phase 5: conflict resolution)
│       ├── _finalize_review.py      (Phase 6: review/fix loop)
│       ├── _finalize_preflight.py   (preflight, syntax fix, empty-output guard)
│       └── _domain_sandbox.py       (cross-domain file write enforcement)
├── pipeline_session.py         (SessionManager, run continuity)
├── pipeline_snapshot.py        (SnapshotManager, proposal/diff/revert)
├── pipeline_stream.py          (generator wrapper for SSE)
└── pipeline_stream_server.py   (HTTP server, SSE + OpenAI-compat endpoints)

cartridges/
├── midway_ecosystem.py         (Midway cartridge wrapper — review extras, bridge contract)
└── midway_data.py              (Midway domain prompts, Lua/C++ rules, observability mandate)
```

---

*End of Technical Report*
