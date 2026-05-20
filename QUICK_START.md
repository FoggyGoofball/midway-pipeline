# Midway Pipeline — Quick Start Guide

The **Midway Mesh Consensus Pipeline** is an AI-assisted development pipeline
that automates the feature-request → code-review → merge workflow for the
*Midway to Nowhere* game engine.

It uses local LLMs (via [Ollama](https://ollama.ai)) to generate, review, and
iterate on code changes for C++, Lua, and GLSL source files.

---

## Prerequisites

### Dev PC (this machine)
1. **Python 3.10+** — with these packages:
   ```bash
   pip install requests
   ```
   The Python script (`pipeline.py`) runs here on your dev PC.

2. **Network access** to the Steam Deck at `192.168.0.16:11434` (OLLAMA_HOST).

### Steam Deck (remote Ollama server)
1. **Ollama** installed and running on the Steam Deck:
   ```bash
   # On the Steam Deck (SSH or terminal):
   ollama serve
   ```
   Ollama listens on `0.0.0.0:11434` so the dev PC can reach it.
   By default Ollama only binds to 127.0.0.1 — make sure it's configured
   to accept LAN connections (e.g. `OLLAMA_HOST=0.0.0.0`).

2. **Models pulled** on the Steam Deck (takes 5-15 min depending on bandwidth):
   ```bash
   ollama pull qwen3.5:9b
   ollama pull phi3.5:latest
   ollama pull llama3.1:8b-instruct-q4_K_M
   ollama pull qwen2.5-coder:1.5b
   ollama pull llama3.2:1b
   ```
   These models stay on the Steam Deck — inference does not touch the dev PC.

### Continue VS Code Extension
The Continue extension on your dev PC is configured to relay code context
to the Steam Deck's Ollama instance. The pipeline runs independently of
Continue, using the same `OLLAMA_HOST` (`192.168.0.16:11434`).


---

## Quick Start

From the **`midway/`** project root (where this pipeline repo lives as
`midway-pipeline/`):

```bash
# Basic feature request
run_pipeline.bat "add a jackpot feature to the plinko attraction"

# Resume from a checkpoint
run_pipeline.bat --checkpoint cp_abc123 "continue where I left off"

# List saved checkpoints
run_pipeline.bat --list-checkpoints

# Chat mode (bypass pipeline — just ask a question)
run_pipeline.bat --chat "what attractions currently exist?"

# Direct Python invocation
python midway-pipeline/pipeline.py "add particle effects to crumbling facade"
```

---

## How It Works

```
User Prompt
    │
    ▼
┌─────────────────┐
│  Intent Router  │ ← Classifies: MODIFICATION, QUERY, or CHAT
└────────┬────────┘
         │ (if MODIFICATION)
         ▼
┌─────────────────┐
│  Director       │ ← Decomposes feature request into 1-5 tasks
└────────┬────────┘
         │
         ▼
    ┌─────────┐
    │ Agents  │ ← Each agent (C++, Lua, etc.) generates code
    └────┬────┘
         │
         ▼
    ┌────────┐
    │ Review │ ← Cross-agent consensus + review
    └────┬───┘
         │
         ▼
    ┌─────────────┐
    │ Final Merge │ ← Synthesized output + file patch generation
    └─────────────┘
         │
         ▼
    pipeline_output_<timestamp>.md
```

---

## Module Overview

The pipeline is fully modular. Key files inside `midway-pipeline/`:

| Module | File | Purpose |
|--------|------|---------|
| **Orchestrator** | `pipeline.py` | Thin entry point (~593 lines) — imports everything else |
| **System Prompts** | `_prompts.py` | All 15 LLM system prompts + VIRTUAL_MEMORY_PROTOCOL |
| **Helpers** | `_pipeline_helpers.py` | File scanning, task execution, failure reports |
| **Mesh API** | `_mesh_api.py` | Distributed task submission/status |
| **Mesh Loops** | `mesh_loops.py` | Phases 0.5-8 iteration loops (fetches, tasks) — 1312 lines |
| **Mesh Finalize** | `mesh_finalize.py` | Code merge, consensus, final approval — 708 lines |
| **Models** | `models.py` | SignalType (14+ signals), MeshSignal, Task, PipelineContext, OrchestrationConfig |
| **Signals** | `signals.py` | Signal parsing/extraction from LLM output |
| **Domain Registry** | `domain_registry.py` | Agent definitions, persona maps, runtime model resolution |
| **Ollama Client** | `ollama_client.py` | HTTP client for Ollama API — context-tiered model parsing |
| **Paging Kernel** | `paging_kernel.py` | Adaptive VRAM paging, dynamic page limits, ghost buffer resume |
| **Ledger** | `ledger.py` | Session timeline, anchor TOC, fix fingerprinting |
| **Checkpoint** | `checkpoint.py` | Save/resume pipeline state |
| **File References** | `file_references.py` | Parse and fetch referenced files |
| **Context Extractor** | `context_extractor.py` | Project context extraction |
| **GDD Extractor** | `gdd_extractor.py` | Section extraction from Game Design Document |
| **Tag Suggester** | `tagsuggester.py` | Post-pipeline tag auto-detection |
| **Fetch Handler** | `fetch_handler.py` | Signal-driven file fetch and offload |
| **Token Budget** | `token_budget.py` | Context window management |
| **Offload Store** | `offload_store.py` | Disk-based context offloading |
| **Pre-Flight** | `_finalize_preflight.py` | File sync + SEARCH/REPLACE sanitization + domain-isolated circuit breaker |
| **Conflict Resolution** | `_finalize_conflicts.py` | VETO/OBJECT conflict mediation |
| **Review** | `_finalize_review.py` | Review-fix loop, Architect fix cycle |
| **Domain Sandbox** | `_domain_sandbox.py` | Domain enforcement, cross-domain write blocking |
| **Session** | `pipeline_session.py` | SessionManager, session isolation |
| **Stream Server** | `pipeline_stream_server.py` | SSE HTTP server for streaming |
| **Tests** | `tests/` | Pytest suite |

---

## Common Tasks

### Run the test suite
```bash
cd midway-pipeline
pytest -v
```

### Run a specific test
```bash
cd midway-pipeline
pytest tests/test_full_pipeline_dry_run.py -v
```

### View saved checkpoints
```bash
run_pipeline.bat --list-checkpoints
```

### Resume from a specific checkpoint
```bash
run_pipeline.bat --checkpoint cp_abc123 "continue feature"
```

---

## Output

Pipeline output is saved to the `midway/` project root as:

```
pipeline_output_20260405_143022.md
```

The final output contains all generated code, review feedback, and merge
instructions. These files are **gitignored** — they won't pollute the repo.

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `Connection refused` | Ensure Ollama is running (`ollama serve` or background service) |
| `Model not found` | Run `ollama pull <model>` for each model listed in Prerequisites |
| `ImportError` | Run `pip install requests` |
| Pipeline seems stuck | Checkpoints auto-save — Ctrl+C, then resume with `--checkpoint <id>` |
| Output too long | The pipeline truncates files at 4000-6000 chars to fit context windows |

---

## File Scanning

The pipeline scans the **`midway/` project root** for relevant files when
processing a feature request. **It explicitly excludes** the pipeline system
itself (`midway-pipeline/`), pipeline runtime artifacts (`.pipeline_checkpoints/`,
`offload_store/`), and pipeline ledgers (`docs/memory/`, `docs/.pipeline_journal/`).

This ensures the AI agents only see game engine source files, not pipeline
metadata.

---

## Repository Structure

```
midway/                      ← Game engine project root (this repo)
├── src/                     ← C++ game engine source
├── attractions/             ← Lua attraction scripts
├── assets/                  ← Game assets (shaders, textures, audio)
├── docs/                    ← Documentation
├── GDD/                     ← Game Design Document
├── midway-pipeline/         ← Pipeline system (separate repo)
│   ├── pipeline.py          ← Orchestrator entry point
│   ├── _pipeline_helpers.py ← File scanning helpers
│   ├── mesh_loops.py        ← Core iteration loops
│   ├── tests/               ← Pytest suite
│   └── QUICK_START.md       ← This file
├── run_pipeline.bat         ← Convenience launcher
└── .gitignore               ← (pipeline artifacts excluded)
```
