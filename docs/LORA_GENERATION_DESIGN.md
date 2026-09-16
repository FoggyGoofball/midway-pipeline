# Dynamic LoRA Generation — Design

> Status: **PLAN (saved for later)** — not yet implemented.
> Companion to `docs/CARTRIDGE_WIZARD_PLAN.md`. The wizard produces the
> machine-readable cartridge knowledge that feeds this pipeline.

## Goal

Generate a useful LoRA adapter for **every (model × cartridge) combination**
from the `lora generator/` project, instead of the current hardcoded single
`midway_lora_dataset.jsonl`.

```
LoRA dataset = f(cartridge_knowledge, model_profile, failure_corpus, n)
```

## Current state (what's hardcoded)

| File | Purpose | Limitation |
|---|---|---|
| `lora_dataset_generator.py` | 1500-pt paging-protocol dataset (PAGE_IN/PAGE_OUT XML) | ✅ already model/cartridge-agnostic |
| `cartridge_lora_generator.py` | cartridge API dataset (spawn, phantom-fix, pool, economy) | ❌ hardcoded to Midway |
| `lora_fine_tune.py` | Unsloth QLoRA trainer | ❌ hardcoded `Qwen2.5-Coder-7B` + ChatML |
| `lora_trainer_server.py` / `setup_lora_server.sh` | trainer service | fine |

Specific hardcoding in `cartridge_lora_generator.py`:

- `PHANTOM_CORRECTIONS` — 25 hardcoded Midway phantom→fix pairs.
- `ATTRACTION_NAMES` / `ATTRACTION_KEYS` — hardcoded Midway attractions.
- `_gen_phantom_scenario` — 100-line `if/elif` chain mapping each phantom string
  to a hand-written bad-code/correction snippet.
- `_build_api_block(contract)` — **already correct**: reads
  `midwayphysics_spawn_api`, `object_pools`, `economy_api`, `script_lifecycle`,
  `globals_injected` off a contract dict. This is the pattern to extend.

## Three dimensions

### 1. Cartridge dimension (API knowledge)

Derived from the cartridge's structured API surface (bridge contract +
prohibitions + domain registry — exactly what the wizard's
`DomainKnowledgeGraph` Stage-3 compiler emits):

- **Signatures + arities**: generate "write `OnLoad` using
  `SpawnStaticBox(lx,ly,lz,w,h,d)`" with correct arity, plus the negative
  "fix `SpawnStaticBox(0,0,0,10,2,10,1.0)`" → drop the 7th arg.
- **Phantom → correction pairs**: replace hardcoded `PHANTOM_CORRECTIONS`
  with `cartridge.get_prohibitions()`.
- **Lifecycle invariants** (`OnLoadStatic`→`SpawnSharedBooth`,
  `OnLoad`→`OnStep`): invariant-preservation scenarios.
- **Domain boundaries** (Lua `.` vs C++ `::`, Lua agent never edits `.cpp`):
  "reject the wrong-domain edit" scenarios.

Key refactor: `_gen_phantom_scenario`'s `if/elif` chain becomes a template
that iterates the cartridge phantom list — generic, not per-name hand-written.

### 2. Model dimension (per base model)

A `model_profile` dict drives the differences:

- **Chat template / tokenizer** (ChatML for Qwen, `llama3`, `phi3`, `qwen3.5`).
- **`train_on_inputs`** — factual API knowledge → full-context loss; protocol/
  behavior → completion-only.
- **Unsloth coverage** — gate whether the base model is trainable; fall back to
  a supported sibling otherwise.
- **Known quirks → negative examples** — seed the dataset with the model's
  observed failure modes. Catalogued this session for qwen3.5:9b:
  - `local if puck then return end` (invalid Lua)
  - line-number gutters pasted into code (`37 |`)
  - `MidwayPhysics::SpawnStaticBox` (C++ `::` in Lua)
  - 7-arg `SpawnStaticBox`
  - stray `end`, phantom `SpawnSharedBooth`

### 3. Runtime dimension (closed loop)

The pipeline already emits correction pairs in its logs. A harvester parses
`pipeline_run.log` for the deterministic-repair markers and appends them to
the dataset:

- `[Fix G] Auto-patched ... (7→6)` (arity repair)
- balancer removals of surplus `end`/`until`
- `[Phantom Strip]` results
- every fix-cycle failure

Flow: **run → collect failures → regenerate dataset → fine-tune → re-run →
measure** (fewer fix cycles, fewer circuit-breaker trips).

## Composed pipeline

```
cartridge wizard (DomainKnowledgeGraph: arity table + phantom list + domains)
        │
        ▼
cartridge_lora_generator.py (data-driven templates + cartridge knowledge)
        │  + model_profile (template, tokenizer, train_on_inputs, quirks)
        │  + failure_corpus (harvested from pipeline logs)
        ▼
<model>_<cartridge>_dataset.jsonl
        ▼
lora_fine_tune.py (per-model base + tokenizer)
        ▼
adapters/<model>_<cartridge>/
```

Naming is the concrete expression of "each model × each cartridge":
`qwen3.5-9b_midway_lora`, `qwen3.5-9b_ue4_lora`, `llama3.1-8b_midway_lora`, …

## Constraints

- **Hardware:** 16 GB → 4-bit QLoRA, r=16, batch=2, dynamic `max_seq_length`
  (already locked). Scale dataset via seeding (generators take a `seed`).
- **Unsloth coverage:** verify base model support before promising a LoRA.
- **Don't poison the factual LoRA:** phantom-fix dataset must use
  `train_on_inputs=true` — the API contract must be in the loss, not masked.

## Suggested first step

Refactor `cartridge_lora_generator.py` to read the cartridge contract +
phantom list instead of hardcoded `PHANTOM_CORRECTIONS`/`if-elif`, and add the
arity-driven scenario (correct call + "drop the extra arg" negative). That
single change makes the generator cartridge-portable and is the prerequisite
for the model-profile + failure-harvest layers.
