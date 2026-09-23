"""
lora_config.py — single source of truth for LoRA training configuration.

All tunables live here so future changes (new base model, new dataset, new
cartridge, new hyperparameters) are ONE-LINE edits, not scattered constants.
The trainer and dataset generators import from this module.

Forward-thinking principles:
  - Every knob has a comment explaining WHY it is set that way.
  - Model names, paths, and hyperparameters are never hardcoded elsewhere.
  - `show_config()` prints the active configuration for auditability.
  - Training is CUDA-only (Unsloth); the Steam Deck is the INFERENCE host
    (loads the adapter via Ollama), not the training host.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict

SCRIPT_DIR = Path(__file__).resolve().parent

# ── Base model ────────────────────────────────────────────────────────────
# IMPORTANT: this must be the HuggingFace base for the Ollama model the
# pipeline will run as the new coder. An adapter only loads against the SAME
# base model it was trained on -> Ollama base = `qwen2.5-coder:7b-instruct`.
#
# Use the INSTRUCT variant, NOT the raw base: Unsloth's fix_untrained_tokens
# guard raises on the base model because <|im_start|>/<|im_end|> embeddings
# are all-zero (never trained in the base). Instruct already has them trained,
# so the guard passes cleanly.
BASE_MODEL_NAME = "unsloth/Qwen2.5-Coder-7B-Instruct"

# ── LoRA hyperparameters (locked by 16 GB VRAM budget) ───────────────────
LORA_R = 16
LORA_ALPHA = 32
LORA_DROPOUT = 0.0
LORA_TARGET_MODULES = [
    "q_proj", "k_proj", "v_proj", "o_proj",
    "gate_proj", "up_proj", "down_proj",
]

# ── Training hyperparameters ─────────────────────────────────────────────
NUM_EPOCHS = 3
LEARNING_RATE = 2e-5
WARMUP_STEPS = 50
TRAIN_BATCH_SIZE = 2
GRADIENT_ACCUM_STEPS = 4
SAVE_STEPS = 500
LOGGING_STEPS = 25
SEED = 42
LOAD_IN_4BIT = True
USE_GRADIENT_CHECKPOINTING = "unsloth"

# Dynamic max_seq_length (computed from the longest sample, never hardcoded).
MAX_SEQ_LENGTH_FLOOR = 512
MAX_SEQ_LENGTH_CEILING = 32768

# ── Datasets ─────────────────────────────────────────────────────────────
# Feeds are kept SEPARATE so each adapter trains on exactly the skills its
# role needs. See TRAINING_RUNBOOK.md for the mapping and commands.
#
#   Coder adapter   (midway-coder-lora):   paging + search_replace + cartridge + signals
#   Reasoner adapter(midway-reasoner-lora): signals + verdict
PAGING_DATASET = SCRIPT_DIR / "paging_lora_dataset.jsonl"
CARTRIDGE_DATASET = SCRIPT_DIR / "midway_lora_dataset.jsonl"
SEARCH_REPLACE_DATASET = SCRIPT_DIR / "search_replace_lora_dataset.jsonl"
NEGATIVE_API_DATASET = SCRIPT_DIR / "negative_api_lora_dataset.jsonl"
FAILURE_CORPUS_DATASET = SCRIPT_DIR / "failure_corpus_dataset.jsonl"
SIGNALS_DATASET = SCRIPT_DIR / "signals_lora_dataset.jsonl"
VERDICT_DATASET = SCRIPT_DIR / "verdict_lora_dataset.jsonl"
COMBINED_DATASET = SCRIPT_DIR / "combined_lora_dataset.jsonl"       # coder training set
REASONER_DATASET = SCRIPT_DIR / "reasoner_lora_dataset.jsonl"       # reasoner training set

OUTPUT_DIR = SCRIPT_DIR / "lora_output"
CARTRIDGE_OUTPUT_DIR = SCRIPT_DIR / "lora_output_cartridge"
REASONER_OUTPUT_DIR = SCRIPT_DIR / "lora_output_reasoner"

# ── Loss-mask policy per dataset ─────────────────────────────────────────
# train_on_inputs:
#   True  -> factual knowledge (the API contract) must be IN the loss.
#   False -> behavior (protocol/format emission) is completion-only, so the
#            model learns to EMIT the right form, not to memorize the prompt.
# Keyed by a substring of the dataset filename.
DATASET_TRAIN_ON_INPUTS: Dict[str, bool] = {
    "paging": False,
    "midway": True,          # cartridge API facts
    "search_replace": False,
    "negative_api": False,   # correction behavior -> completion-only (never reinforce the bare call)
    "failure_corpus": False, # observed fixes -> completion-only (SEARCH side is the broken code)
    "signals": False,
    "verdict": False,        # review/argumentation behavior -> completion-only
    "combined": False,       # coder mixed behavior+facts -> completion-only (safe default)
    "reasoner": False,       # reasoner mixed behavior -> completion-only
}


def resolve_train_on_inputs(dataset_path) -> bool:
    """Infer the loss-mask policy from the dataset filename (default False)."""
    name = Path(dataset_path).name.lower()
    for key, flag in DATASET_TRAIN_ON_INPUTS.items():
        if key in name:
            return flag
    return False


def show_config() -> str:
    """Return a human-readable dump of the active configuration."""
    return (
        f"BASE_MODEL_NAME        = {BASE_MODEL_NAME}\n"
        f"LoRA r/alpha/dropout   = {LORA_R}/{LORA_ALPHA}/{LORA_DROPOUT}\n"
        f"epochs/lr/batch/accum  = {NUM_EPOCHS}/{LEARNING_RATE}/{TRAIN_BATCH_SIZE}/{GRADIENT_ACCUM_STEPS}\n"
        f"load_in_4bit           = {LOAD_IN_4BIT}\n"
        f"gradient_checkpointing = {USE_GRADIENT_CHECKPOINTING}\n"
        f"datasets               = paging:{PAGING_DATASET.name}, "
        f"cartridge:{CARTRIDGE_DATASET.name}, sr:{SEARCH_REPLACE_DATASET.name}, "
        f"signals:{SIGNALS_DATASET.name}, verdict:{VERDICT_DATASET.name}\n"
        f"coder   combined       = {COMBINED_DATASET.name}\n"
        f"reasoner combined      = {REASONER_DATASET.name}\n"
        f"train_on_inputs        = {DATASET_TRAIN_ON_INPUTS}\n"
    )
