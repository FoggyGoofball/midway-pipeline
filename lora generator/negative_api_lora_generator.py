#!/usr/bin/env python3
"""
negative_api_lora_generator.py
==============================
Generates negative-example training data that teaches the coder NOT to emit
the API-contract violations that keep tripping the pipeline's deterministic
verdict:

  N1  bare call       `PoolAcquire(...)`        -> `MidwayPhysics.PoolAcquire(...)`
  N2  phantom API     `MidwayPhysics.BodyGetAabb` -> neutralized / rewritten
  N3  namespace typo  `Engineer.AwardTickets`   -> `Engine.AwardTickets`

Every sample is derived from `api_namespace_registry` (keyed by language), so
this generator is language-agnostic: adding a new target language is one
registry entry + one profile line, not a code change.

Output is ChatML JSONL in the coder's SEARCH/REPLACE persona, identical in
shape to `search_replace_lora_generator.py` so it can be mixed into the same
`combine_datasets.py --preset coder` training set.

Usage
-----
  python "lora generator/negative_api_lora_generator.py" --num-samples 1200 --seed 42
  python "lora generator/negative_api_lora_generator.py" --language lua --output out.jsonl
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

# Import the language-agnostic registry from the repo root.
_REPO_ROOT = SCRIPT_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from api_namespace_registry import (  # noqa: E402
    bare_name_to_qualified,
    get_aliases,
    get_phantoms,
    supported_languages,
)

# ─────────────────────────────────────────────────────────────────────────────
# Per-language presentation profiles.  The DATA comes from the registry; this
# profile only tells us how to render a code fragment for a given language.
# A new language adds a profile here + a registry entry, nothing else changes.
# ─────────────────────────────────────────────────────────────────────────────
LANGUAGE_PROFILES = {
    "lua": {
        "comment": "--",
        "fence": "lua",
        # A plausible bare call for a symbol, used to build realistic-looking
        # broken snippets.  `{name}` is substituted with the symbol name.
        "bare_call_tpl": "local h = {name}(1, 2, 3)",
        "call_tpl": "{name}(1, 2, 3)",
    },
    # Future: "python": {"comment": "#", "fence": "python", "bare_call_tpl": "h = {name}(1, 2, 3)"},
}


def _profile(language: str) -> dict:
    return LANGUAGE_PROFILES.get(language, LANGUAGE_PROFILES["lua"])


#: The coder persona, kept terse so the model learns the FORMAT, not an essay.
SYSTEM_PROMPT = (
    "You are the Lua coder for 'Midway to Nowhere'. You edit code by emitting "
    "ONE three-part SEARCH/REPLACE block per defect:\n"
    "<<<<<<< SEARCH\n<exact current code>\n=======\n<replacement code>\n>>>>>>> REPLACE\n"
    "The SEARCH side must match the file byte-for-byte. Never write the "
    "<<<<<<< / ======= / >>>>>>> markers into the file body itself."
)


def _block(old: str, new: str) -> str:
    return f"<<<<<<< SEARCH\n{old}\n=======\n{new}\n>>>>>>> REPLACE"


def _sample(system: str, user: str, assistant: str) -> dict:
    return {"messages": [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
        {"role": "assistant", "content": assistant},
    ]}


# ─────────────────────────────────────────────────────────────────────────────
# Sample builders (N1/N2/N3).  All data flows in from the registry.
# ─────────────────────────────────────────────────────────────────────────────

def _gen_bare_call_fix(rng: random.Random, language: str) -> dict:
    """N1: a bare call must be namespace-qualified."""
    prof = _profile(language)
    b2q = bare_name_to_qualified(language)
    if not b2q:
        raise RuntimeError(f"registry has no bare symbols for '{language}'")
    bare = rng.choice(sorted(b2q))
    qualified = b2q[bare]
    broken = prof["bare_call_tpl"].format(name=bare)
    fixed = prof["bare_call_tpl"].format(name=qualified)
    return _sample(
        SYSTEM_PROMPT,
        f"Fix this code so it calls the engine API with the correct namespace:\n"
        f"```{prof['fence']}\n{broken}\n```",
        _block(broken, fixed),
    )


def _gen_alias_fix(rng: random.Random, language: str) -> dict:
    """N3: a misspelled / hallucinated namespace must be corrected."""
    prof = _profile(language)
    aliases = get_aliases(language)
    if not aliases:
        raise RuntimeError(f"registry has no aliases for '{language}'")
    wrong, right = rng.choice(sorted(aliases.items()))
    broken = f"local v = {wrong}.GetValue(1)"
    fixed = f"local v = {right}.GetValue(1)"
    return _sample(
        SYSTEM_PROMPT,
        f"Fix the misspelled namespace in this code:\n"
        f"```{prof['fence']}\n{broken}\n```",
        _block(broken, fixed),
    )


def _gen_phantom_fix(rng: random.Random, language: str) -> dict:
    """N2: a phantom API must be neutralized or rewritten to the real one."""
    prof = _profile(language)
    phantoms = get_phantoms(language)
    if not phantoms:
        raise RuntimeError(f"registry has no phantoms for '{language}'")
    phantom, canonical = rng.choice(sorted(phantoms.items()))
    broken = prof["call_tpl"].format(name=phantom)
    if canonical:
        fixed = prof["call_tpl"].format(name=canonical)
        instruction = (
            f"The API '{phantom}' does not exist. Rewrite it to the correct "
            f"API '{canonical}':"
        )
    else:
        # Neutralize: comment the line out with an explicit reason.
        fixed = f"{prof['comment']} removed phantom API: {phantom}"
        instruction = (
            f"The API '{phantom}' does not exist in the bridge contract. "
            f"Remove or comment out the call:"
        )
    return _sample(
        SYSTEM_PROMPT,
        f"{instruction}\n```{prof['fence']}\n{broken}\n```",
        _block(broken, fixed),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Generator
# ─────────────────────────────────────────────────────────────────────────────

def generate(num_samples: int, language: str, seed: int) -> list[dict]:
    rng = random.Random(seed)
    builders = [_gen_bare_call_fix, _gen_alias_fix, _gen_phantom_fix]
    samples: list[dict] = []
    i = 0
    while len(samples) < num_samples and i < num_samples * 20:
        i += 1
        builder = rng.choice(builders)
        try:
            samples.append(builder(rng, language))
        except RuntimeError:
            # A builder is empty for this language (e.g. no aliases) — skip it.
            continue
    return samples


def write_dataset(samples: list[dict], output: Path) -> int:
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as fh:
        for s in samples:
            fh.write(json.dumps(s, ensure_ascii=False) + "\n")
    return len(samples)


def main():
    parser = argparse.ArgumentParser(description="Generate negative API-contract LoRA examples")
    parser.add_argument("--language", default="lua", choices=list(LANGUAGE_PROFILES),
                        help="target language (registry + profile must exist)")
    parser.add_argument("--num-samples", type=int, default=1200)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    from lora_config import NEGATIVE_API_DATASET
    output = Path(args.output) if args.output else NEGATIVE_API_DATASET

    samples = generate(args.num_samples, args.language, args.seed)
    n = write_dataset(samples, output)
    print(f"Wrote {n} negative API-contract samples ({args.language}) -> {output}")


if __name__ == "__main__":
    main()
