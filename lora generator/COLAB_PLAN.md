# LoRA Training — Colab Runbook

Train a Qwen2.5-Coder-7B LoRA on the **SEARCH/REPLACE format + Midway contract**
datasets, then load the adapter into Ollama on the Steam Deck as the new coder.

> Hardware: train on a CUDA GPU (Colab free T4 is the default). The Steam Deck
> is the **inference** host (loads the adapter via Ollama), not the training host.

---

## 0. What we're training

- **Base model:** `unsloth/Qwen2.5-Coder-7B` (text-only coder — Unsloth-supported).
  (`qwen3.5:9b` is a multimodal hybrid with SSM + vision and is NOT trainable via
  this toolchain — see the session notes.)
- **Dataset:** `combined_lora_dataset.jsonl` — 3500 samples
  (1500 SEARCH/REPLACE + 2000 Midway contract). Loss mask: completion-only.

## 1. Prepare files (on the Colab runtime)

Upload these three files from `lora generator/`:
- `lora_config.py`
- `lora_fine_tune.py`
- `combined_lora_dataset.jsonl`

Or, quicker: `git clone` the repo and upload only the `.jsonl`.

## 2. Install dependencies

```python
!pip install -q unsloth
!pip install -q transformers datasets accelerate peft trl bitsandbytes
```

## 3. Train

```python
!python lora_fine_tune.py \
    --dataset combined_lora_dataset.jsonl \
    --train-on-inputs false \
    --epochs 3 \
    --lr 2e-5
```

Expected: ~3500 samples, batch 2 × grad-accum 4, 3 epochs on a T4 ≈ **20–40 min**.

## 4. Download the adapter

`lora_output/` contains `adapter_model.safetensors` + `adapter_config.json`.
Download both.

## 5. Load into Ollama on the Steam Deck (next day)

```bash
# on the Deck, in the midway dir:
cat > Modelfile.coder <<'EOF'
FROM qwen2.5-coder:7b
ADAPTER ./lora_output
EOF
ollama create midway-coder-lora -f Modelfile.coder
```

## 6. Point the pipeline at it

In `lora_config` this is already single-source; for the pipeline set the env var:

```
MIDWAY_CODER_MODEL=midway-coder-lora
```

or edit `CODER_MODEL` in `ollama_config.py` / `pipeline.py`.

## 7. Validate

- `test_lora_adapter.py --ollama midway-coder-lora` (SEARCH/REPLACE emission).
- Re-run the `strongman` build and compare `[Invariants]` / `[Convergence]` lines.

## Tuning knobs (all in lora_config.py)

- `LORA_R`, `LORA_ALPHA` — capacity vs. overfitting.
- `NUM_EPOCHS` — 3 is conservative; 5 if underfitting.
- `LEARNING_RATE` — 2e-5 default; try 1e-5 if loss spikes.
- `DATASET_TRAIN_ON_INPUTS` — per-dataset loss-mask policy.

## Later (deferred)

- `signals_lora_generator.py` — persona-mesh signals (QUERY/VETO/APPEAL…), a
  *second* persona LoRA.
- Failure-corpus harvesting — append tombstone/repair markers from run logs and
  retrain (the closed loop in `LORA_GENERATION_DESIGN.md`).
