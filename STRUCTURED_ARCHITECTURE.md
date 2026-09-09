# Modernized Architecture — 16 GB VRAM Pipeline

This document records the refactor that replaces brittle regex parsing, hallucinated
outputs, and OOM risks with four architectural standards. The legacy pipeline keeps
running untouched; the new modules are additive and the Architect pass now prefers the
structured path with a graceful fallback.

## New modules

| Module | Standard | Responsibility |
| --- | --- | --- |
| `structured_schemas.py` | #1 | Rigid Pydantic `BaseModel` contracts for every LLM output (architect design, task decomposition, review verdict, patches, syntax check, intent). |
| `structured_client.py` | #1 + #2 | `instructor`-wrapped extraction + `tenacity` retry (exp. backoff, max 3) that feeds the **exact** `ValidationError` back to the LLM; `two_turn_extract` decouples reasoning from formatting. |
| `serve_xgrammar.py` | #4 | vLLM / SGLang deployment configs using the `xgrammar` backend, plus an Ollama-compatible HTTP facade. |
| `lora generator/lora_fine_tune.py` | #3 | Locked QLoRA hyperparameters + dynamically computed `max_seq_length`. |
| `mesh_architect.py` | #1 + #2 | Architect pass now uses the two-turn structured path, falling back to the legacy regex parser. |

## Standard 1 — Output validation (Pydantic + Instructor + Tenacity)

* Regex / custom string parsers (`_extract_json`, `_repair_json`, `### Task N:` grammar) are
  deprecated in favour of `structured_schemas.py`.
* Every call is routed through `instructor.from_openai(OpenAI(base_url="{OLLAMA_HOST}/v1"))`
  in `Mode.JSON` (Ollama's OpenAI-compat layer does not support function-calling/TOOLS).
* `tenacity` retries on `pydantic.ValidationError`:

```python
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    retry=retry_if_exception_type(ValidationError),
    reraise=True,
)
```

Each retry appends the exact error JSON as a user turn so the model self-corrects.

## Standard 2 — Cognitive load separation (two-turn)

Turn 1 (`raw_chat`) requests raw chain-of-thought with **no** schema in context and **no**
formatting mandate. Turn 2 feeds that freeform analysis into the instructor-wrapped schema:

```python
client.two_turn_extract(
    schema=AttractionDesignOutput,
    cot_system=ARCHITECT_COT_SYSTEM,
    user=prompt,
    extract_system=ARCHITECT_EXTRACT_SYSTEM,
)
```

## Standard 3 — Hardware-bound training (Unsloth QLoRA)

Locked in `lora generator/lora_fine_tune.py`:

| Hyperparameter | Value |
| --- | --- |
| `load_in_4bit` | `True` |
| `r` | `16` |
| `per_device_train_batch_size` | `2` |
| `gradient_accumulation_steps` | `4` |
| `optim` | `"adamw_8bit"` |
| `use_gradient_checkpointing` | `"unsloth"` |

`max_seq_length` is **never** hardcoded. `compute_dynamic_max_seq_length()` tokenizes every
training item through the chat template and returns the exact length of the longest one
(rounded up to a multiple of 8, clamped to `[512, 32768]`), so no VRAM is spent on padded
empty tokens.

## Standard 4 — Constrained inference (xgrammar)

`serve_xgrammar.py` compiles the Pydantic JSON schema into an `xgrammar` grammar automaton:

```python
from vllm.sampling_params import GuidedDecodingParams
guided = GuidedDecodingParams(json=build_xgrammar_schema("task_decomposition"), backend="xgrammar")
```

This guarantees token-level syntactic compliance — malformed structural output is
mathematically impossible at the sampler. SGLang is shown as an alternative.

## Dependencies

```bash
# Structured extraction (Standard 1 & 2)
pip install pydantic instructor openai tenacity

# Constrained inference (Standard 4)
pip install xgrammar vllm          # vLLM path
pip install xgrammar sglang        # SGLang path
pip install fastapi uvicorn        # optional HTTP facade

# Training (Standard 3)
pip install unsloth transformers datasets accelerate peft trl
```

## Notes

* The recommended 16 GB deployment remains **Ollama + `structured_client.py`** (vLLM/SGLang
  are heavier runners intended for a dedicated dGPU; both share the same Pydantic schemas).
* The legacy regex parsers are retained solely as fallbacks — they can be deleted once the
  structured path has run cleanly across the full cartridge test matrix.
