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

import json
import os
import sys
import time
from typing import Dict, List

os.environ["CUBLAS_WORKSPACE_CONFIG"] = ":4096:8"
os.environ["TORCH_CUDNN_DETERMINISTIC"] = "1"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATASET_PATH = os.path.join(SCRIPT_DIR, "paging_lora_dataset.jsonl")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "lora_output")

# -- Hyperparameters ------------------------------------------------------
BASE_MODEL_NAME = "unsloth/Qwen2.5-Coder-7B"

LORA_R = 16
LORA_ALPHA = 32
LORA_DROPOUT = 0

MAX_SEQ_LENGTH = 2048
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


# -- Main training routine ------------------------------------------------

def main():
    print("=" * 72)
    print("  Universal Virtual Memory Paging Protocol - LoRA Fine-Tuning")
    print("  Base Model: Qwen2.5-Coder-7B (4-bit QLoRA)")
    print(f"  Dataset:    {DATASET_PATH}")
    print(f"  Output:     {OUTPUT_DIR}")
    print("  Loss mask:  response template only (train_on_inputs=false)")
    print("=" * 72)

    # Step 1: Load dataset ------------------------------------------------
    print("\n[1/5] Loading dataset...")
    samples = load_and_format_dataset(DATASET_PATH)

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

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=BASE_MODEL_NAME,
        max_seq_length=MAX_SEQ_LENGTH,
        dtype=None,
        load_in_4bit=True,
        device_map="auto",
    )
    print("  Model loaded successfully")

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
        output_dir=OUTPUT_DIR,
        per_device_train_batch_size=TRAIN_BATCH_SIZE,
        gradient_accumulation_steps=GRADIENT_ACCUM_STEPS,
        warmup_steps=WARMUP_STEPS,
        num_train_epochs=NUM_EPOCHS,
        learning_rate=LEARNING_RATE,
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
        max_seq_length=MAX_SEQ_LENGTH,
        dataset_num_proc=1,
        packing=False,
        data_collator=collator,  # <-- CRITICAL: masks non-assistant tokens
    )

    # -- Train ------------------------------------------------------------
    print("\n  Starting training...")
    print(f"  Epochs:    {NUM_EPOCHS}")
    print(f"  LR:        {LEARNING_RATE}")
    print(f"  Batch:     {TRAIN_BATCH_SIZE} (accum {GRADIENT_ACCUM_STEPS})")
    print(f"  Loss mask: assistant-only (<|im_start|>assistant template)")
    print()

    start_time = time.time()
    trainer.train()
    elapsed = time.time() - start_time
    print(f"\n  Training completed in {elapsed / 60:.1f} minutes")

    # -- Save -------------------------------------------------------------
    print(f"\n  Saving LoRA adapter to {OUTPUT_DIR}...")
    model.save_pretrained(OUTPUT_DIR)
    tokenizer.save_pretrained(OUTPUT_DIR)

    # Try GGUF export for Ollama compatibility
    try:
        print("  Attempting GGUF export for Ollama...")
        model.save_pretrained_gguf(
            OUTPUT_DIR,
            tokenizer,
            quantization_method="q4_k_m",
        )
        print("  GGUF export completed.")
    except Exception as e:
        print(f"  (GGUF export skipped: {e})")

    print("\n" + "=" * 72)
    print("  LoRA fine-tuning complete!")
    print(f"  Adapter: {OUTPUT_DIR}/adapter_model.safetensors")
    print(f"  Config:  {OUTPUT_DIR}/adapter_config.json")
    print("=" * 72)

    print("\n  Test with:")
    print("    python lora_generator/test_lora_adapter.py")
    print()
    print("  To create an Ollama model:")
    print('    ollama create qwen-paging -f - << EOF')
    print('    FROM qwen2.5-coder:7b')
    print(f'    ADAPTER {OUTPUT_DIR}/adapter_model.safetensors')
    print('    TEMPLATE """{{ .Prompt }}"""')
    print('    EOF')


if __name__ == "__main__":
    main()
