"""train_runner.py — run lora_fine_tune.py on the Colab VM with the combined dataset.

Executed remotely via `colab exec -f train_runner.py`. The trainer, config, and
dataset are assumed to already be uploaded to /content/ (see colab_train.sh).
"""
import subprocess
import sys

subprocess.run(
    [
        sys.executable,
        "/content/lora_fine_tune.py",
        "--dataset", "/content/combined_lora_dataset.jsonl",
        "--train-on-inputs", "false",
        "--epochs", "3",
    ],
    check=True,
)
