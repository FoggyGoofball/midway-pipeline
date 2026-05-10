# Day 3 Pipeline Roadmap: Pro Mode & Math Guardrails
*Status: ✅ Complete*

## Part 1: Pro Mode Auto-Detect (Recovered Tasks)
- [x] **Task 1 (State Context):** `models.py` — `pro_mode: bool = False` added to `PipelineContext` dataclass.
- [x] **Task 2 (Math Sensor):** `_pipeline_helpers.py` — `build_director_prompt` appends MATH_HEAVY instruction.
- [x] **Task 3 (The NL Gate):** `mesh_loops.py` — `run_fetches()` parses `[MATH_HEAVY]`, prompts user for Pro Mode via `input()`, sets `ctx.pro_mode = True` on 'y'.

## Part 2: The Math Oracle & Unit Test Gate
- [x] **Task 4 (Math Oracle Signal):** `signals.py` — `MATH_EVAL` added to `SignalType` enum with regex `\[MATH_EVAL: .+?\]`.
- [x] **Task 5 (Sandbox Execution):** `mesh_loops.py` — `MATH_EVAL` handler uses `subprocess.run` to execute Python code safely, captures stdout/stderr, injects result back.
- [x] **Task 6 (TDD Prompt Injection):** `domain_registry.py` — `C++` and `PHYS` agent prompts include gtest/catch2 TDD requirement when `pro_mode == True`.
- [x] **Task 7 (The Unit Test Gate):** `mesh_finalize.py` — if `ctx.pro_mode`, compiles `run_tests` target, executes binary, treats failures as `[VETO]` with stderr fed back.

## Part 3: N-Version Consensus (The Frankenstein Merge)
- [x] **Task 8 (Multi-Draft Generation):** `mesh_loops.py` — `run_tasks()` intercepts `[PHYS]` tasks when `pro_mode`, calls Coder agent 3 times at temps 0.2/0.5/0.8 for drafts A/B/C.
- [x] **Task 9 (The Tribunal Merge):** `mesh_loops.py` — Tribunal agent (Director 14B prompt) evaluates 3 drafts, discards hallucinations, outputs single master file saved to `ctx.all_results_dict`.

## Supporting Infrastructure
- [x] `ollama_client.py` — `call_ollama()` and `call_ollama_streamed()` accept `params` dict for temperature control.
- [x] `_pipeline_helpers.py` — `execute_task()` accepts and forwards `ollama_params` to `call_ollama`.
