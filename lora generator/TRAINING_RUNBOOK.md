# LoRA Training Runbook

> One base model, two adapters, five separated dataset feeds.

## Architecture

Both adapters train from the **same base** (`unsloth/Qwen2.5-Coder-7B-Instruct`),
so there is ONE GGUF to download, ONE trainer, ONE config. They differ only in
**which feeds** are combined and **which Ollama tag** they become.

```
Base: qwen2.5-coder-7b-instruct
├── midway-coder-lora    (coder adapter)
│     feeds: paging + search_replace + cartridge + signals
│     roles: Lua, C++, PHYS, SHADER, NET, OBSERVABILITY
└── midway-reasoner-lora (reasoner adapter)
      feeds: signals + verdict
      roles: Reviewer, Tribunal, Conf, Director, DOC
```

`qwen3.5:9b` is NOT trainable (multimodal hybrid) — it stays only as an
emergency fallback via `MIDWAY_REVIEWER_MODEL`.

## Separation principle

- **Feeds are generated separately** (`*_lora_generator.py` → `*_lora_dataset.jsonl`)
  and never edited by hand.
- **Combining happens only in `combine_datasets.py`**, keyed by preset. Signals
  is the SHARED feed (both adapters need the mesh tags); paging/search_replace/
  cartridge are coder-only; verdict is reasoner-only.
- **Each adapter has its own output dir** (`lora_output_custom` vs
  `lora_output_reasoner`).

## Dataset feeds

| Feed | Generator | Default samples | `train_on_inputs` |
|---|---|---|---|
| `paging_lora_dataset.jsonl` | `lora_dataset_generator.py` | 1500 | false |
| `search_replace_lora_dataset.jsonl` | `search_replace_lora_generator.py` | 1500 | false |
| `midway_lora_dataset.jsonl` | `cartridge_lora_generator.py` | ~2000 | **true** (factual API) |
| `signals_lora_dataset.jsonl` | `signals_lora_generator.py` | 2000 | false |
| `verdict_lora_dataset.jsonl` | `verdict_lora_generator.py` | 1500 | false |

## Step 1 — (re)generate every feed

```bash
python "lora generator/lora_dataset_generator.py" --num-samples 1500
python "lora generator/search_replace_lora_generator.py" --num-samples 1500
python "lora generator/cartridge_lora_generator.py"
python "lora generator/signals_lora_generator.py" --num-samples 2000
python "lora generator/verdict_lora_generator.py" --num-samples 1500
```

All are seeded (`--seed 42`) and deterministic.

## Step 2 — combine into each adapter's training set

```bash
python "lora generator/combine_datasets.py" --preset coder      # -> combined_lora_dataset.jsonl
python "lora generator/combine_datasets.py" --preset reasoner   # -> reasoner_lora_dataset.jsonl
```

## Step 3 — train (Colab, Unsloth T4/L4/A100)

Upload `lora_fine_tune.py` + `lora_config.py` + the combined JSONL, then:

**Coder adapter:**

```bash
python lora_fine_tune.py --dataset combined_lora_dataset.jsonl --train-on-inputs false --epochs 2 --save-steps 100
```

**Reasoner adapter:**

```bash
python lora_fine_tune.py --dataset reasoner_lora_dataset.jsonl --train-on-inputs false --epochs 2 --save-steps 100 --output lora_output_reasoner
```

Notes:
- Both use **completion-only loss** (`train_on_inputs=false`) — behaviour and
  format are what's being taught. (Only the raw `midway` feed is factual; the
  combined default stays false.)
- 1 epoch is usually enough; 2 if loss is still dropping. Watch the first
  `{'loss': ...}` line — it MUST be non-zero and decreasing.
- `--save-steps 100` so a killed run still leaves salvageable checkpoints.

## Step 4 — deploy to Ollama (Deck)

Each run produces a merged GGUF (`save_pretrained_gguf`) in
`lora_output_custom_gguf/` or `lora_output_reasoner_gguf/`. Upload the GGUF +
`Modelfile` to the Deck, then:

```bash
ollama create midway-coder-lora -f Modelfile        # from the coder GGUF folder
ollama create midway-reasoner-lora -f Modelfile     # from the reasoner GGUF folder
```

## Step 5 — point the pipeline at them

```bash
# coder: default is already midway-coder-lora (or set MIDWAY_CODER_MODEL)
set MIDWAY_REVIEWER_MODEL=midway-reasoner-lora     # reasoner roles
set MIDWAY_SCAFFOLD_MODEL=midway-coder-lora        # scaffold rides the coder
```

## Validation checklist before training

- Every feed line is valid JSONL with a `messages` key.
- Every emitted bracket tag parses via `signals.py::extract_signals()` (0 failures).
- The combined file's turn-count distribution shows single-turn + round-trip samples.

## Initial vs Genuine data (the retrain loop)

The failure corpus is split by SOURCE so a retrain can fold real, user-driven
data on top of the deterministic bootstrap:

- `failure_corpus.initial.jsonl` — deterministic seeders (`failure_mode_seeder.py`,
  `contract_failure_generator.py`). Regenerated on demand, reproducible.
- `failure_corpus.genuine.jsonl` — live runtime captures (`post_process_lua_observed`
  defaults to `source="genuine"`) plus any hand-written user corrections. Grows
  only from real behaviour.

Convert either with `failure_corpus_to_dataset.py --source initial|genuine`, then
combine with the matching preset:

- `--preset coder`          -> `combined_lora_dataset.jsonl`         (bootstrap: initial only)
- `--preset coder-retrain`  -> `combined_retrain_lora_dataset.jsonl` (initial + genuine)

Flow: train v1 on `coder` → run the pipeline with `MIDWAY_FAILURE_CORPUS=1` →
the genuine corpus fills from real fixes → retrain v2 on `coder-retrain`.

