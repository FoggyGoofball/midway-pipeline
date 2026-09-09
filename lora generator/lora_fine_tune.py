#!/usr/bin/env python3
"""
lora_fine_tune.py
=================
Unsloth-based QLoRA fine-tuning script for Qwen2.5-Coder-7B
on the Universal Virtual Memory Paging Protocol dataset.

CRITICAL DESIGN CONSTRAINT (train_on_inputs: false)
---------------------------------------------------
Loss is ONLY computed on assistant-role outputs. The system prompt (which
contains the STRICT XML PAGING PROTOCOL) and user messages are masked out
from the loss calculation. This is enforced via:

  1) format_with_messages(): preserves raw messages[] structure
  2) tokenizer.apply_chat_template(): applies Qwen's ChatML formatting
  3) DataCollatorForCompletionOnlyLM(response_template): masks non-assistant
     tokens so the model only learns the correct XML token emission

Target Hardware
---------------
Steam Deck (16 GB total, ~12-13 GB free VRAM)
  -> 4-bit NormalFloat quantization fits Qwen2.5-Coder-7B in ~6 GB
  -> LoRA r=16, alpha=32 adds ~30 MB of trainable parameters

Output
------
  lora_output/
  +-- adapter_model.safetensors
  +-- adapter_config.json
  +-- tokenizer.json / tokenizer_config.json
  +-- checkpoint-*/

Prerequisites
-------------
  pip install unsloth
  pip install --upgrade transformers datasets accelerate peft trl

Usage
-----
  python lora_generator/lora_fine_tune.py

  (Assumes lora_generator/paging_lora_dataset.jsonl exists from
   a prior run of lora_dataset_generator.py)
"""

import argparse
import json
import os
import sys
import time
from typing import Dict, List

os.environ["CUBLAS_WORKSPACE_CONFIG"] = ":4096:8"
os.environ["TORCH_CUDNN_DETERMINISTIC"] = "1"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DATASET_PATH = os.path.join(SCRIPT_DIR, "paging_lora_dataset.jsonl")
DEFAULT_OUTPUT_DIR = os.path.join(SCRIPT_DIR, "lora_output")

# Cartridge-specific defaults
CARTRIDGE_DATASET_PATH = os.path.join(SCRIPT_DIR, "midway_lora_dataset.jsonl")
CARTRIDGE_OUTPUT_DIR = os.path.join(SCRIPT_DIR, "lora_output_cartridge")

# -- Hyperparameters ------------------------------------------------------
BASE_MODEL_NAME = "unsloth/Qwen2.5-Coder-7B"

LORA_R = 16
LORA_ALPHA = 32
LORA_DROPOUT = 0

# ---------------------------------------------------------------------------
# LOCKED QLoRA hyperparameters (16 GB VRAM constraint — DO NOT MODIFY).
#   load_in_4bit                = True           (enforced at load time)
#   r                           = 16             (LORA_R)
#   per_device_train_batch_size = 2              (TRAIN_BATCH_SIZE)
#   gradient_accumulation_steps = 4              (GRADIENT_ACCUM_STEPS)
#   optim                       = "adamw_8bit"   (TrainingArguments)
#   use_gradient_checkpointing  = "unsloth"      (enforced at load time)
# ---------------------------------------------------------------------------
# max_seq_length is NEVER hardcoded — it is computed dynamically from the
# longest training item (see compute_dynamic_max_seq_length).  Allocating a
# fixed 4096/8192 window wastes VRAM on padded empty tokens.
MAX_SEQ_LENGTH_FLOOR = 512
MAX_SEQ_LENGTH_CEILING = 32768  # native window of the base model

LEARNING_RATE = 2e-5
WARMUP_STEPS = 50
NUM_EPOCHS = 3

TRAIN_BATCH_SIZE = 2
GRADIENT_ACCUM_STEPS = 4
SAVE_STEPS = 500
LOGGING_STEPS = 25


# -- Dataset helpers ------------------------------------------------------

def load_and_format_dataset(path: str) -> List[Dict]:
    """Load JSONL and produce a list of {messages: [...]} dicts."""
    if not os.path.exists(path):
        print(f"ERROR: Dataset not found at {path}")
        print("Run lora_dataset_generator.py first.")
        sys.exit(1)

    samples: List[Dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            samples.append({"messages": obj["messages"]})

    print(f"Loaded {len(samples)} training samples from {path}")
    return samples


def compute_dynamic_max_seq_length(
    samples: List[Dict],
    tokenizer,
    floor: int = MAX_SEQ_LENGTH_FLOOR,
    ceiling: int = MAX_SEQ_LENGTH_CEILING,
    multiple_of: int = 8,
) -> int:
    """Dynamically size ``max_seq_length`` to the longest training item.

    Architectural Standard #3: ``max_seq_length`` must never be hardcoded to a
    large default (4096/8192).  Allocating a fixed window pads every sample with
    empty tokens that still consume KV cache and activation memory, exhausting the
    16 GB budget.  We tokenize every sample through the chat template and return
    the EXACT length of the longest one (rounded up to ``multiple_of`` for GPU
    alignment, clamped to [floor, ceiling]).
    """
    lengths: List[int] = []
    for sample in samples:
        messages = sample.get("messages")
        if not messages:
            continue
        text = tokenizer.apply_chat_template(messages, tokenize=False)
        if text is None:
            text = tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=False
            )
        if text is None:
            text = json.dumps(messages, ensure_ascii=False)
        ids = tokenizer.encode(text, add_special_tokens=False)
        lengths.append(len(ids))

    if not lengths:
        print(f"  WARNING: no tokenizable samples; falling back to floor={floor}")
        return floor

    longest = max(lengths)
    seq = ((longest + multiple_of - 1) // multiple_of) * multiple_of
    seq = max(floor, min(ceiling, seq))
    if longest > ceiling:
        print(
            f"  WARNING: longest sample ({longest} tok) exceeds ceiling "
            f"({ceiling}); clamping. Consider truncating this sample."
        )
    print(
        f"  [Dynamic max_seq_length] longest sample = {longest} tok "
        f"-> max_seq_length = {seq} (floor={floor}, ceiling={ceiling})"
    )
    return seq


# -- Main training routine ------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(description="LoRA fine-tuning for Qwen2.5-Coder-7B")
    parser.add_argument(
        "--cartridge", "-c",
        action="store_true",
        help="Train on cartridge API dataset (midway_lora_dataset.jsonl) "
             "instead of paging dataset. Output goes to lora_output_cartridge/",
    )
    parser.add_argument(
        "--dataset",
        default=None,
        help="Override dataset path (overrides --cartridge dataset selection)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Override output directory",
    )
    parser.add_argument(
        "--epochs",
        type=int,
        default=None,
        help=f"Override NUM_EPOCHS (default: {NUM_EPOCHS})",
    )
    parser.add_argument(
        "--lr",
        type=float,
        default=None,
        help=f"Override LEARNING_RATE (default: {LEARNING_RATE})",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    # Resolve dataset and output paths
    if args.dataset:
        dataset_path = args.dataset
        output_dir = args.output or os.path.join(SCRIPT_DIR, "lora_output_custom")
    elif args.cartridge:
        dataset_path = CARTRIDGE_DATASET_PATH
        output_dir = args.output or CARTRIDGE_OUTPUT_DIR
    else:
        dataset_path = args.dataset or DEFAULT_DATASET_PATH
        output_dir = args.output or DEFAULT_OUTPUT_DIR

    # Override hyperparams from env (for trainer server) or CLI
    num_epochs = args.epochs or int(os.environ.get("LORA_OVERRIDE_EPOCHS", NUM_EPOCHS))
    learning_rate = args.lr or float(os.environ.get("LORA_OVERRIDE_LR", LEARNING_RATE))

    mode_name = "Cartridge API" if args.cartridge else "Paging Protocol"
    print("=" * 72)
    print(f"  {mode_name} - LoRA Fine-Tuning")
    print("  Base Model: Qwen2.5-Coder-7B (4-bit QLoRA)")
    print(f"  Dataset:    {dataset_path}")
    print(f"  Output:     {output_dir}")
    print(f"  Epochs:     {num_epochs}")
    print(f"  LR:         {learning_rate}")
    if args.cartridge:
        print("  Loss mask:  train_on_inputs=true (full sequence, factual API knowledge)")
    else:
        print("  Loss mask:  response template only (train_on_inputs=false)")
    print("=" * 72)

    # Step 1: Load dataset ------------------------------------------------
    print("\n[1/5] Loading dataset...")
    samples = load_and_format_dataset(dataset_path)

    # Step 2: Import dependencies -----------------------------------------
    print("\n[2/5] Initializing Unsloth...")
    try:
        from unsloth import FastLanguageModel, is_bfloat16_supported
    except ImportError:
        print("ERROR: unsloth is not installed.")
        print("Install with: pip install unsloth")
        sys.exit(1)

    import torch
    import transformers

    if not torch.cuda.is_available():
        print("WARNING: CUDA not detected. CPU training will be extremely slow.")
        inp = input("  Continue on CPU? (y/N): ")
        if inp.lower() != "y":
            sys.exit(1)

    device_name = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU"
    print(f"  Device: {device_name}")

    # Step 3: Load model with 4-bit QLoRA ---------------------------------
    print(f"\n[3/5] Loading {BASE_MODEL_NAME} with 4-bit QLoRA...")
    print("  (downloads ~4 GB on first run)")

    # -- Standard #3: dynamic max_seq_length -------------------------------
    # Load the tokenizer up-front to measure the longest training item BEFORE
    # allocating the model's KV/activation buffers.  A hardcoded 4096/8192
    # window pads every sample with empty tokens that waste VRAM.
    print("  Loading tokenizer to compute dynamic max_seq_length...")
    _measure_tokenizer = transformers.AutoTokenizer.from_pretrained(BASE_MODEL_NAME)
    if _measure_tokenizer.pad_token is None:
        _measure_tokenizer.pad_token = _measure_tokenizer.eos_token
    max_seq_length = compute_dynamic_max_seq_length(samples, _measure_tokenizer)

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=BASE_MODEL_NAME,
        max_seq_length=max_seq_length,
        dtype=None,
        load_in_4bit=True,
        device_map="auto",
        # Unsloth-native gradient checkpointing — recomputes activations on the
        # backward pass so batch_size=2 x grad_accum=4 fits in 16 GB VRAM.
        use_gradient_checkpointing="unsloth",
    )
    print(f"  Model loaded successfully (max_seq_length={max_seq_length})")

    # Verify tokenizer has a chat_template
    if tokenizer.chat_template is None:
        print("WARNING: tokenizer has no chat_template; applying Qwen ChatML.")
        tokenizer.chat_template = (
            "{% for message in messages %}"
            "{% if message['role'] == 'system' %}"
            "<|im_start|>system\n{{ message['content'] }}<|im_end|>\n"
            "{% elif message['role'] == 'user' %}"
            "<|im_start|>user\n{{ message['content'] }}<|im_end|>\n"
            "{% elif message['role'] == 'assistant' %}"
            "<|im_start|>assistant\n{{ message['content'] }}<|im_end|>\n"
            "{% endif %}"
            "{% endfor %}"
        )

    # Set pad_token if missing
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    # Step 4: Apply LoRA adapters -----------------------------------------
    print("\n[4/5] Applying LoRA adapters...")
    model = FastLanguageModel.get_peft_model(
        model,
        r=LORA_R,
        lora_alpha=LORA_ALPHA,
        lora_dropout=LORA_DROPOUT,
        target_modules=[
            "q_proj", "k_proj", "v_proj", "o_proj",
            "gate_proj", "up_proj", "down_proj",
        ],
        use_rslora=False,
        loftq_config=None,
        bias="none",
    )

    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())
    print(f"  Trainable params: {trainable:,} / {total:,} "
          f"({100 * trainable / total:.2f}%)")

    # Step 5: Prepare dataset with loss masking ---------------------------
    print("\n[5/5] Preparing dataset with assistant-only loss masking...")

    from datasets import Dataset as HFDataset
    from trl import SFTTrainer, DataCollatorForCompletionOnlyLM
    from transformers import TrainingArguments

    # Preserve raw messages for the tokenizer's chat template.
    # We do NOT flatten to text here -- the trainer will use
    # DataCollatorForCompletionOnlyLM to apply loss masking.
    dataset = HFDataset.from_list(samples)

    # The response template is the start of an assistant turn.
    # Everything before is masked from loss.
    response_template = "<|im_start|>assistant"
    collator = DataCollatorForCompletionOnlyLM(
        response_template=response_template,
        tokenizer=tokenizer,
    )

    # Show one sample as a sanity check
    sample_text = tokenizer.apply_chat_template(
        samples[0]["messages"], tokenize=False
    )
    print("\n  Sample formatted training example (first 500 chars):")
    print("-" * 60)
    print(sample_text[:500] + "...")
    print("-" * 60)

    training_args = TrainingArguments(
        output_dir=output_dir,
        per_device_train_batch_size=TRAIN_BATCH_SIZE,
        gradient_accumulation_steps=GRADIENT_ACCUM_STEPS,
        warmup_steps=WARMUP_STEPS,
        num_train_epochs=num_epochs,
        learning_rate=learning_rate,
        fp16=not is_bfloat16_supported(),
        bf16=is_bfloat16_supported(),
        logging_steps=LOGGING_STEPS,
        save_steps=SAVE_STEPS,
        save_total_limit=3,
        optim="adamw_8bit",
        weight_decay=0.01,
        lr_scheduler_type="cosine",
        seed=42,
        report_to="none",
        remove_unused_columns=False,
        dataloader_num_workers=0,
    )

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        args=training_args,
        train_dataset=dataset,
        max_seq_length=max_seq_length,
        dataset_num_proc=1,
        packing=False,
        data_collator=collator,  # <-- CRITICAL: masks non-assistant tokens
    )

    # -- Train ------------------------------------------------------------
    print("\n  Starting training...")
    print(f"  Epochs:    {num_epochs}")
    print(f"  LR:        {learning_rate}")
    print(f"  Batch:     {TRAIN_BATCH_SIZE} (accum {GRADIENT_ACCUM_STEPS})")
    print(f"  Seq len:   {max_seq_length} (dynamic — longest training item)")
    print(f"  Loss mask: assistant-only (<|im_start|>assistant template)")
    print()

    start_time = time.time()
    trainer.train()
    elapsed = time.time() - start_time
    print(f"\n  Training completed in {elapsed / 60:.1f} minutes")

    # -- Save -------------------------------------------------------------
    print(f"\n  Saving LoRA adapter to {output_dir}...")
    model.save_pretrained(output_dir)
    tokenizer.save_pretrained(output_dir)

    # Try GGUF export for Ollama compatibility
    try:
        print("  Attempting GGUF export for Ollama...")
        model.save_pretrained_gguf(
            output_dir,
            tokenizer,
            quantization_method="q4_k_m",
        )
        print("  GGUF export completed.")
    except Exception as e:
        print(f"  (GGUF export skipped: {e})")

    print("\n" + "=" * 72)
    print("  LoRA fine-tuning complete!")
    print(f"  Adapter: {output_dir}/adapter_model.safetensors")
    print(f"  Config:  {output_dir}/adapter_config.json")
    print("=" * 72)

    print("\n  Test with:")
    print("    python lora_generator/test_lora_adapter.py")
    print()
    print("  To create an Ollama model:")
    model_name = "qwen-midway-api" if args.cartridge else "qwen-paging"
    print(f'    ollama create {model_name} -f - << EOF')
    print('    FROM qwen2.5-coder:7b')
    print(f'    ADAPTER {output_dir}/adapter_model.safetensors')
    print('    TEMPLATE """{{ .Prompt }}"""')
    print('    EOF')


if __name__ == "__main__":
    main()
