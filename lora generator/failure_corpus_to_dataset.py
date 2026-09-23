#!/usr/bin/env python3
"""
failure_corpus_to_dataset.py
============================
Convert the observed failure corpus (``failure_corpus.jsonl``, populated by
``_post_process_lua.post_process_lua_observed`` when ``MIDWAY_FAILURE_CORPUS=1``)
into ChatML SEARCH/REPLACE training samples.

This closes the loop the synthetic generators only approximate: REAL observed
``broken -> fixed`` deltas become training examples for the next fine-tune.

Output shape is identical to ``negative_api_lora_generator.py`` so it can be
mixed into the coder training set via ``combine_datasets.py --preset coder``.

Language-agnostic: the corpus tags each record with a ``language``; this
converter renders the code fence from a per-language profile.

Usage
-----
  python "lora generator/failure_corpus_to_dataset.py"
      --input failure_corpus.jsonl --output failure_corpus_dataset.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

_REPO_ROOT = SCRIPT_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from failure_corpus import read_corpus  # noqa: E402

#: Per-language code-fence tag.  A new language adds a profile here.
LANGUAGE_FENCES = {
    "lua": "lua",
    "python": "python",
    "cpp": "cpp",
}

SYSTEM_PROMPT = (
    "You are the coder for 'Midway to Nowhere'. You edit code by emitting "
    "ONE three-part SEARCH/REPLACE block per defect:\n"
    "<<<<<<< SEARCH\n<exact current code>\n=======\n<replacement code>\n>>>>>>> REPLACE\n"
    "The SEARCH side must match the file byte-for-byte. Never write the "
    "<<<<<<< SEARCH / ======= / >>>>>>> REPLACE markers into the file body."
)


def _fence(language: str) -> str:
    return LANGUAGE_FENCES.get(language, LANGUAGE_FENCES["lua"])


def _block(before: str, after: str) -> str:
    return f"<<<<<<< SEARCH\n{before}\n=======\n{after}\n>>>>>>> REPLACE"


def corpus_to_samples(records, language: str = None) -> list[dict]:
    """Convert corpus records to ChatML samples (deduped by before+after)."""
    samples: list[dict] = []
    seen: set = set()
    for rec in records:
        lang = language or rec.get("language", "lua")
        before = (rec.get("before") or "").strip()
        after = (rec.get("after") or "").strip()
        if not before or not after:
            continue
        key = (before, after)
        if key in seen:
            continue
        seen.add(key)
        fence = _fence(lang)
        samples.append({"messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": (
                f"Fix this code:\n```{fence}\n{before}\n```"
            )},
            {"role": "assistant", "content": _block(before, after)},
        ]})
    return samples


def write_dataset(samples: list[dict], output: Path) -> int:
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as fh:
        for s in samples:
            fh.write(json.dumps(s, ensure_ascii=False) + "\n")
    return len(samples)


def main():
    parser = argparse.ArgumentParser(description="Convert failure corpus to ChatML SEARCH/REPLACE dataset")
    parser.add_argument("--input", default=None, help="corpus JSONL (default: failure_corpus.jsonl)")
    parser.add_argument("--output", default=None, help="output JSONL (default: failure_corpus_dataset.jsonl)")
    parser.add_argument("--language", default=None, help="override the language tag")
    args = parser.parse_args()

    input_path = Path(args.input) if args.input else (
        _REPO_ROOT / "lora generator" / "failure_corpus.jsonl"
    )
    output_path = Path(args.output) if args.output else (
        _REPO_ROOT / "lora generator" / "failure_corpus_dataset.jsonl"
    )

    records = read_corpus(input_path)
    samples = corpus_to_samples(records, args.language)
    n = write_dataset(samples, output_path)
    print(f"Converted {len(records)} corpus record(s) -> {n} unique sample(s) -> {output_path}")


if __name__ == "__main__":
    main()
