# Day 5: Omni-Batch, Auditor & QoL
*Status: ✅ Complete*

## Part 1: QoL Audio Chimes (The "Chore" Bell)
- [x] **Task 1 (Chime Utility):** In `_pipeline_helpers.py`, imported `platform`. Created `trigger_chime()` function. If `platform.system() == "Windows"`, uses `winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)`. Fallback to `print('\a')`. (lines 58-71)
- [x] **Task 2 (Wire the Chimes):** In `mesh_loops.py`, added `trigger_chime()` before all `input()` calls: Blueprint Gate (line ~484), Pro Mode Gate (line ~591), and Domain Consultant Gate (line ~132). Also added in `mesh_finalize.py` Reconciliation Gate (line ~447). (lines 132, 484, 591)

## Part 2: The Domain Consultant (The "Neon Arcade" Gate)
- [x] **Task 3 (Dichotomy Logic):** In `mesh_loops.py` (Phase 0.5/Scope Gate), added dormant domain keyword scanning with keywords for shader, neon, multiplayer, audio, save. Cross-references `get_unavailable_domains_text()`. (lines 101-140)
- [x] **Task 4 (Consultant Output):** If dichotomy found, chime triggers and prompts: *"You asked for a feature relying on an unavailable domain. Implement a functional wireframe/debug placeholder instead? [y/N]"*. If 'y', appends wireframe instruction to `ctx.user_prompt`. (lines 132-145)

## Part 3: DAG Decomposition (The Wave Generator)
- [x] **Task 5 (DAG Structure):** In `models.py`, added `depends_on: List[str] = field(default_factory=list)` to the `Task` class. (line ~48)
- [x] **Task 6 (Director Upgrades):** Updated `build_director_prompt` in `_pipeline_helpers.py` to command the Director to output `DependsOn: Task N` in task format. (line ~285)
- [x] **Task 7 (Wave Sorter):** In `mesh_loops.py`, implemented `sort_tasks_into_waves()` with topological sort algorithm that groups independent tasks into parallel waves. (lines 46-90)

## Part 4: Omni-Batch Execution & VRAM Governor
- [x] **Task 8 (Batch Loop):** Refactored `run_tasks` in `mesh_loops.py` to compute DAG waves first, then iterate `for wave_idx, wave in enumerate(waves):`. (lines 605-700)
- [x] **Task 9 (VRAM Lock):** Inside wave loop, when executing standard 7B generation tasks, `ollama_params` includes `{"keep_alive": "15m"}`. (line ~641)
- [x] **Task 10 (Explicit VRAM Flush):** In `ollama_client.py`, created `unload_model(model_name)` that sends blank API with `keep_alive=0`. In `mesh_loops.py`, called before 7B->14B swap. (ollama_client.py lines ~85-95, mesh_loops.py line ~635)
- [x] **Task 11 (Batch Compilation):** Compiler circuit breaker triggers per-wave via cmake build verification. Wave build failure increments retry counts for all tasks in that wave. (lines ~715-770)

## Part 5: The Active Rule Auditor (Governance)
- [x] **Task 12 (Tag Harvester):** Created `auditor.py` with `harvest_approved_tags()` that scans all `.md` files in `docs/memory/` and extracts markdown headers. (auditor.py lines 1-45)
- [x] **Task 13 (Conflict Scanner):** Updated `LIBRARIAN` agent in `domain_registry.py` with `[AUDIT]` mode for cross-referencing ledgers. (domain_registry.py lines ~110-125)
- [x] **Task 14 (Reconciliation Gate):** In `mesh_finalize.py` (Phase 6 final review), added Audit trigger if Tribunal struggles to reach consensus (`review_verdict != "PASS"`). Chimes and uses `input()` to ask resolution. (mesh_finalize.py lines ~440-475)
