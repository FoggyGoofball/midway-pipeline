#!/usr/bin/env bash
# One-shot LoRA training on Colab via google-colab-cli.
#
# PREREQUISITE (one-time, your Google account):
#   uv tool install google-colab-cli   # or: pip install google-colab-cli
#   colab auth
#
# Run from inside WSL, from the `lora generator/` directory:
#   bash colab_train.sh
#
# NOTE: verify the GPU tier first — the CLI's --gpu T4 may use paid compute
# units (see `colab pay`), unlike the free browser T4.
set -euo pipefail

SESSION=lora
cd "$(dirname "$0")"

echo "[1/6] Provisioning T4 runtime..."
colab new -s "$SESSION" --gpu T4

echo "[2/6] Installing dependencies..."
colab install -s "$SESSION" unsloth transformers datasets accelerate peft trl bitsandbytes

echo "[3/6] Uploading trainer + config + dataset..."
colab upload -s "$SESSION" lora_config.py /content/lora_config.py
colab upload -s "$SESSION" lora_fine_tune.py /content/lora_fine_tune.py
colab upload -s "$SESSION" combined_lora_dataset.jsonl /content/combined_lora_dataset.jsonl
colab upload -s "$SESSION" train_runner.py /content/train_runner.py

echo "[4/6] Training (~20-40 min)..."
colab exec -s "$SESSION" -f train_runner.py

echo "[5/6] Downloading adapter..."
colab download -s "$SESSION" /content/lora_output ./lora_output

echo "[6/6] Stopping runtime..."
colab stop -s "$SESSION"
echo "Done. Adapter is in ./lora_output/"
