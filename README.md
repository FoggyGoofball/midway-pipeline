# Midway Pipeline Orchestrator

A multi-hop AI pipeline orchestrator that chains LLM calls through a Director → Subordinate Models → Review → Final Approval workflow. Designed for game development with the "Midway to Nowhere" project, but fully generic and extensible.

## Architecture

```
User Request
    │
    ▼
┌─────────────────────┐
│  GDD Librarian      │  Phase 0: Extract relevant design docs + project state
│  (context curation) │
└─────────┬───────────┘
          │ context
          ▼
┌─────────────────────┐
│  Director           │  Phase 1: Decompose request into 1-5 domain-tagged tasks
│  (task decomposition)│
└─────────┬───────────┘
          │ task list
          ▼
┌─────────────────────┐
│  Subordinate Models │  Phase 2: Execute each task with self-correction loops
│  (C++, PHYS, Lua,   │  Each task receives previous task results as context
│   SHADER, etc.)     │
└─────────┬───────────┘
          │ generated code
          ▼
┌─────────────────────┐
│  Integration Review │  Phase 3: Cross-domain validation + rule compliance
│  (reviewer)         │  If FAIL → architect fixes → re-review (up to 10 cycles)
└─────────┬───────────┘
          │ reviewed code
          ▼
┌─────────────────────┐
│  Director           │  Phase 4: Final APPROVED or REVISION REQUIRED
│  (final approval)   │
└─────────────────────┘
```

## Features

- **Autonomous file reading**: Scans the project tree for files relevant to each task
- **Self-correction loops**: Each task iterates up to 10 times, reviewing its own output
- **Review→Fix cycles**: Integration Reviewer identifies issues, Architect fixes them, re-review
- **Checkpoint system**: Save/restore at every phase for resume capability
- **Snapshot integration**: Optional git-like undo/redo via `pipeline_snapshot.py`
- **Dynamic domain availability**: Only assigns tasks to domains with existing code
- **GDD Librarian**: Extracts only relevant design document sections based on keywords
- **Project state awareness**: Reads completed features and todo lists for context

## Usage

### CLI

```bash
python pipeline.py "add a jackpot feature to the plinko attraction"
python pipeline.py --checkpoint <id> "continue from checkpoint"
```

### HTTP API Server

```bash
python pipeline_server.py              # starts on http://localhost:8765
python pipeline_server.py --port 9999  # custom port
```

Endpoints:
- `GET /health` — Server status
- `GET /v1/models` — Available models
- `POST /v1/chat/completions` — Run the pipeline (OpenAI-compatible)
- `GET /checkpoints` — List saved checkpoints
- `GET /checkpoints/<id>` — Get checkpoint details
- `GET /project-state` — Current project state summary
- `GET /director-prompt` — Dynamically built Director prompt

### Continue Integration

Add to your `config.ts`:

```typescript
{
  title: "Midway Pipeline",
  provider: "openai",
  model: "pipeline",
  apiBase: "http://localhost:8765/v1",
  apiKey: "not-needed",
}
```

## Configuration

Edit the top of `pipeline.py`:

```python
OLLAMA_HOST = "http://192.168.0.16:11434"  # Your Ollama server
MODEL = "qwen2.5-coder:7b"                  # Default model for subordinates
DIRECTOR_MODEL = "qwen2.5-coder:7b"         # Model for Director (can differ)
MAX_ITERATIONS = 10                          # Max self-correction / review cycles
OLLAMA_TIMEOUT = 420                         # Timeout in seconds (7 min)
```

## Domain System

Domains can be marked `ready: True` or `ready: False`. The Director will only assign tasks to ready domains. This allows you to gate features behind implementation status.

Current domains:
- **C++** (ready) — Engine architecture, physics, rendering, networking
- **PHYS** (ready) — Jolt/Box2D physics integration
- **Lua** (ready) — Attraction scripts, UI, economy
- **SHADER** (not ready) — GLSL shaders, visual effects
