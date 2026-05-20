# Internal API Ledger <a name="internal-api-ledger"></a>
> Auto-generated — public methods and global variables exposed by Midway to Nowhere subsystems.
> Maintained by the Contract Arbiter. Append new signatures here as they are created.
> Cross-reference: `docs/engine_lua_bridge_contract.md` for full bridge contract details.

## Core Engine (C++)

### PhysicsManager
*(`ReturnType PhysicsManager::MethodName(ParamType1, ParamType2, ...) — description`)*

### AttractionManager
*(Add signatures)*

### Engine
*(Add signatures)*

### DevConsole
*(Add signatures)*

### DebugRenderer
*(Add signatures)*

### MidwayPhysics (Bridge)
*(Add signatures)*

---

## Lua-Exposed API

### Attraction Lifecycle
*(Add signatures)*

### Modifier System
*(Add signatures)*

### Economy
*(Add signatures)*

### Physics Handles
*(Add signatures)*

---

## Global Variables

| Variable | Type | Defined In | Description |
|----------|------|------------|-------------|
| BOOTH_WORLD_X | float | Engine.cpp | World-space X center of this slot |
| BOOTH_WORLD_Z | float | Engine.cpp | World-space Z center of this slot |
| BOOTH_SLOT_ID | int | Engine.cpp | Unique slot identifier |
| BOOTH_IS_STATIC | bool | Engine.cpp | True during static load, false during dynamic load |
| ENGINE_MOD_MASS | float | Engine.cpp | GDD §4.1 Core Physical — neutral default 1.0 |
| ENGINE_MOD_VOLUME | float | Engine.cpp | GDD §4.1 Core Physical — neutral default 1.0 |
| ENGINE_MOD_FRICTION | float | Engine.cpp | GDD §4.1 Core Physical — neutral default 1.0 |
| ENGINE_MOD_KARMA | float | Engine.cpp | GDD §4.2 Meta-Navigational — neutral default 0.0 (-1..1) |
| ENGINE_MOD_LUCK | float | Engine.cpp | GDD §4.2 Meta-Navigational — neutral default 0.0 |
| ENGINE_MOD_PERSUASION | float | Engine.cpp | GDD §4.2 Meta-Navigational — neutral default 0.0 |
| ENGINE_MOD_HEAT | float | Engine.cpp | GDD §4.2 Meta-Navigational — neutral default 0.0 |
| ENGINE_MOD_SLEIGHT_OF_HAND | float | Engine.cpp | GDD §4.3 Tactile — neutral default 0.0 |
| ENGINE_MOD_NERVE | float | Engine.cpp | GDD §4.3 Tactile — neutral default 0.0 |
| ENGINE_MODIFIERS | table | Engine.cpp | Structured table with all 9 modifier values in snake_case keys |

## Public Constants

| Constant | Value | Defined In | Description |
|----------|-------|------------|-------------|
| booth.width_x | 9.0 | AttractionConstants.booth | Canonical booth width X |
| booth.height_y | 9.0 | AttractionConstants.booth | Canonical booth height Y |
| booth.depth_z | 15.0 | AttractionConstants.booth | Canonical booth depth Z |
| CYCLE_LENGTH | 150.0 | MidwayPhysics.h | Vicious Cycle teleport trigger threshold |

---

## Pipeline Orchestration Sub-Modules (Internal Python Refactor)

The following entries document the public signatures exposed by the internal
Python subdivision modules under `midway-pipeline/`. These are orchestration
primitives — not game-engine symbols — registered here for pipeline tracking
compliance and architectural audit clarity.

### `_helpers_exec` — Base Layer & LLM Logic

| Symbol | Type | Description |
|--------|------|-------------|
| `PROJECT_ROOT` | `Path` | Root project directory (resolved from `__file__`) |
| `MAX_ITERATIONS` | `int` | Max pipeline iterations (default 25) |
| `MAX_CONSENSUS_ITERATIONS` | `int` | Max consensus gate iterations (default 5) |
| `MAX_SUBTASKS_PER_AGENT` | `int` | Max sub-tasks per agent dispatch (default 5) |
| `REVIEW_MAX_ITERATIONS` | `int` | Max review-fix loop cycles (default 5) |
| `SCOPE_FILE_LIMIT` | `int` | Max files per scope budget (default 15) |
| `SCOPE_LINE_LIMIT` | `int` | Max lines per scope budget (default 300) |
| `_ALL_DOMAINS` | `dict` | Internal global domain availability table |
| `_init_config(path)` | `None` | Bootstrap config from PROJECT_ROOT |
| `INTENT_CLASSIFIER_SYSTEM` | `str` | System prompt for intent classifier |
| `classify_intent(prompt)` | `str` | Classify user prompt intent |
| `recursive_librarian(prompt, ctx)` | `str` | Recursive librarian retrieval |
| `get_project_state(ctx, project_root)` | `str` | Get current project state summary |
| `get_available_domains_text()` | `str` | List ready domains as text |
| `get_unavailable_domains_text()` | `str` | List not-ready domains as text |
| `execute_task(agent_key, task_spec, ctx)` | `str` | Execute a single agent task |
| `build_director_prompt(user_prompt, ctx)` | `str` | Build director decomposition prompt |
| `compile_project(project_root)` | `(bool, str)` | Run platform-aware compilation; returns (success, stderr) |

### `_helpers_text` — Text Parsing & Formatting

| Symbol | Type | Description |
|--------|------|-------------|
| `CHAT_PATTERNS` | `list[str]` | Regex patterns for chat intent detection |
| `is_likely_chat(prompt)` | `bool` | Fast-path regex chat detection |
| `format_file_context(files, domain_key, ledger_toc_func)` | `str` | Format discovered files as markdown context block |
| `generate_failure_report(user_prompt, consensus_checks, vetos, objects, all_results, task_map, director_output, all_domains, resolve_agent_name_func)` | `str` | Generate curated failure report with SOS recovery block |
| `get_normalized_syntax(code)` | `str` | Strip comments and normalize whitespace for comparison |

### `_helpers_io` — File System & Hashing

| Symbol | Type | Description |
|--------|------|-------------|
| `trigger_chime()` | `None` | Play system beep/chime |
| `_DOC_CACHE` | `dict` | LRU doc cache (internal) |
| `_DOC_CACHE_TTL` | `float` | Doc cache TTL in seconds (30.0) |
| `_DOC_CACHE_MAX` | `int` | Max doc cache entries (20) |
| `_get_doc_cached(rel_path, project_root)` | `str` | Read doc file with LRU caching |
| `curate_project_structure(prompt, project_root)` | `str` | Scan project directory for relevant files |
| `find_relevant_files(prompt, persona, project_root)` | `list` | Find files relevant to prompt + persona |
| `search_memory(query, project_root)` | `str` | Search `docs/memory/` directory |
| `AGENT_FILE_TOOLS_PROMPT` | `str` | Progressive file disclosure tools description |
| `_read_single_file(pr, path)` | `str` | Read single file with path traversal check |
| `handle_file_read(signal_content, project_root)` | `str` | File read tool for agent progressive disclosure |
| `_list_single_dir(pr, path)` | `str` | List single directory with path traversal check |
| `handle_file_list(signal_content, project_root)` | `str` | Directory listing tool for agents |
| `atomic_write_text(path, content, encoding)` | `None` | Atomic file write via .tmp + rename |
| `_FILE_HASHES` | `dict` | Tracked file hash dictionary (internal) |
| `compute_file_hash(filepath, project_root)` | `str` | Compute MD5 hash of a file |
| `save_initial_file_hashes_from_context(ctx_placeholder, file_context, project_root)` | `dict` | Parse file paths from context and hash them |
| `verify_file_hashes(project_root)` | `list[str]` | Re-hash tracked files, return changed paths |
| `get_tracked_file_hashes()` | `dict` | Return current file hash snapshot |

### `_domain_sandbox` — Domain File-Level Sandboxing

| Symbol | Type | Description |
|--------|------|-------------|
| `DOMAIN_ALLOWED_EXTENSIONS` | `dict` | Canonical mapping of domain keys to allowed file extensions |
| `get_allowed_extensions(domain_key)` | `set` | Return allowed extension set for a domain |
| `extract_file_paths_from_output(output_text)` | `list[str]` | Extract file paths from markdown headings and SEARCH blocks |
| `validate_domain_file_write(domain_key, output_text, known_whitelist)` | `(bool, list[str])` | Validate agent output against domain extension whitelist |
| `build_sandbox_constraint(allowed_exts)` | `str` | Build CRITICAL FILE RESTRICTION system-prompt fragment |
| `reject_cross_domain_output(domain_key, output_text, persona_name)` | `(bool, str)` | Hard-reject cross-domain file writes from agent output |

### `_finalize_review` — Phase 6 Review & Fix Loop

| Symbol | Type | Description |
|--------|------|-------------|
| `_prune_fix_context(domain_key, task_obj, review_issues_text, pre_flight_errors, user_prompt, paged_files_cache)` | `str` | Build lean pruned context payload for domain agent fix cycle |
| `_run_review_fix_loop(ctx)` | `PipelineContext` | Phase 6 integration review, domain-aware fix cycle, insanity detection |

### `_finalize_preflight` — Pre-Flight Checks

| Symbol | Type | Description |
|--------|------|-------------|
| `_run_preflight_checks(ctx)` | `PipelineContext` | Phase 6 pre-flight: compilation, syntax validation, architect fix |

### `_finalize_conflicts` — Conflict Resolution

| Symbol | Type | Description |
|--------|------|-------------|
| `_run_conflict_resolution(ctx)` | `PipelineContext` | Phase 5 conflict resolution (VETO/OBJECT mediation) |

### `mesh_finalize` — Phases 5-8 Facade

| Symbol | Type | Description |
|--------|------|-------------|
| `finalize_mesh(ctx)` | `PipelineContext` | Complete Phases 5-8 finalization pipeline |
| `run_code_merge(ctx)` | `PipelineContext` | Phases 5-8: conflict resolution, pre-flight, review, consensus, final approval |

---

## Agent Registration Rules

When any agent generates code that introduces a new:

- **Public method** → Append to the appropriate C++ or Lua section with:
  ```markdown
  `ReturnType MethodName(ParamType1, ParamType2) — description`
  ```
- **Global variable** → Append to the Global Variables table with:
  ```markdown
  | Name | Type | File:Lineno | Description |
  ```
- **Public constant** → Append to the Public Constants table.

The Contract Arbiter performs this registration automatically during pipeline execution.
If a downstream agent references a symbol not in this ledger, the Contract Arbiter flags it as a hallucination.
