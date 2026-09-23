#!/usr/bin/env python3
"""
combine_datasets.py — concatenate LoRA datasets into one training file.

The trainer trains on ONE dataset path. To train the coder on BOTH the
SEARCH/REPLACE format and the Midway contract in a single run, concatenate
them here. The combined file uses completion-only loss (see
lora_config.DATASET_TRAIN_ON_INPUTS["combined"]).

Usage
-----
  python lora_generator/combine_datasets.py
      --inputs search_replace_lora_dataset.jsonl midway_lora_dataset.jsonl
      --output combined_lora_dataset.jsonl
"""

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from lora_config import (
    PAGING_DATASET,
    SEARCH_REPLACE_DATASET,
    NEGATIVE_API_DATASET,
    FAILURE_CORPUS_INITIAL_DATASET,
    FAILURE_CORPUS_GENUINE_DATASET,
    CARTRIDGE_DATASET,
    SIGNALS_DATASET,
    VERDICT_DATASET,
    ARBITER_DATASET,
    COMBINED_DATASET,
    COMBINED_RETRAIN_DATASET,
    REASONER_DATASET,
)

# Presets: which feeds compose which adapter's training set. Feeds stay
# SEPARATE on disk; the combiner is the only place they are joined.
PRESETS = {
    "coder": {
        "inputs": [PAGING_DATASET, SEARCH_REPLACE_DATASET, NEGATIVE_API_DATASET, FAILURE_CORPUS_INITIAL_DATASET, CARTRIDGE_DATASET, SIGNALS_DATASET],
        "output": COMBINED_DATASET,
        "desc": "midway-coder-lora bootstrap (synthetic + deterministic INITIAL failure corpus)",
    },
    "coder-retrain": {
        "inputs": [PAGING_DATASET, SEARCH_REPLACE_DATASET, NEGATIVE_API_DATASET, FAILURE_CORPUS_INITIAL_DATASET, FAILURE_CORPUS_GENUINE_DATASET, CARTRIDGE_DATASET, SIGNALS_DATASET],
        "output": COMBINED_RETRAIN_DATASET,
        "desc": "midway-coder-lora retrain (INITIAL bootstrap + GENUINE user-driven failure corpus)",
    },
    "reasoner": {
        "inputs": [SIGNALS_DATASET, VERDICT_DATASET, ARBITER_DATASET],
        "output": REASONER_DATASET,
        "desc": "midway-reasoner-lora (signals + review/arbitration + deterministic debate)",
    },
}


def combine(inputs, output: Path) -> int:
    total = 0
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as out:
        for p in inputs:
            p = Path(p)
            if not p.is_file():
                print(f"SKIP missing: {p}")
                continue
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    out.write(line + "\n")
                    total += 1
    print(f"Combined {total} samples -> {output}")
    return total


def main():
    parser = argparse.ArgumentParser(description="Concatenate LoRA datasets into an adapter training set")
    parser.add_argument(
        "--preset", choices=sorted(PRESETS), default="coder",
        help="which adapter training set to build (default: coder)",
    )
    parser.add_argument(
        "--inputs", nargs="+", default=None,
        help="explicit dataset JSONL files (overrides --preset)",
    )
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    preset = PRESETS[args.preset]
    inputs = args.inputs or [str(p) for p in preset["inputs"]]
    output = Path(args.output) if args.output else preset["output"]
    print(f"preset={args.preset} -> {preset['desc']}")
    combine(inputs, output)


if __name__ == "__main__":
    main()
