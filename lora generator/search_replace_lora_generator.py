#!/usr/bin/env python3
"""
search_replace_lora_generator.py
================================
Generates a JSONL dataset that teaches the model the SEARCH/REPLACE patch
format — the exact behavior the pipeline's coder (qwen3.5:9b) has failed at
across 30+ runs.

The pipeline's anchor/fix loop consumes three-part blocks:

    <<<<<<< SEARCH
    <old content — must match the file byte-for-byte>
    =======
    <new content — the replacement>
    >>>>>>> REPLACE

The failure modes this dataset targets (seeded from _tombstones.py):

  S1  emit a well-formed SEARCH/REPLACE block (positive)
  S2  markers leaked into the file body -> output clean code, no markers
  S3  orphaned `else` (dangling else with no `if`) -> corrected if/else
  S4  keywords concatenated (`endend`, `thenif`) -> split onto lines
  S5  JSON table syntax -> Lua `key = value`
  S6  engine method called with `:` -> flat `MidwayPhysics.Method(handle, ...)`

Output format is ChatML JSONL, identical to the paging/cartridge datasets so
it can be mixed into the same `lora_fine_tune.py` training run.

Usage
-----
  python lora_generator/search_replace_lora_generator.py
      --num-samples 1500 --seed 42
"""

import argparse
import json
import random
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from lora_config import SEARCH_REPLACE_DATASET, SEED as DEFAULT_SEED, BASE_MODEL_NAME

# The coder persona system prompt, kept terse so the model learns the FORMAT,
# not an essay.
SYSTEM_PROMPT = (
    "You are the Lua coder for 'Midway to Nowhere'. You edit code by emitting "
    "ONE three-part SEARCH/REPLACE block per defect:\n"
    "<<<<<<< SEARCH\n<exact current code>\n=======\n<replacement code>\n>>>>>>> REPLACE\n"
    "The SEARCH side must match the file byte-for-byte. Never write the "
    "<<<<<<< / ======= / >>>>>>> markers into the file body itself."
)

# Small Midway-flavoured snippets so the SEARCH side looks like real code.
SNIPPET_POOL = [
    ("local hBall = MidwayPhysics.SpawnDynamicBall(0, 1, 2, 0.5)",
     "local hBall = MidwayPhysics.SpawnDynamicSphere(0.0, 1.0, 2.0, 0.5)"),
    ("RemoveBody(hPuck)",
     "MidwayPhysics.DestroyBody(hPuck)"),
    ("local MOD = AttractionConstants.modifiers",
     "local MOD = AttractionConstants.modifiers or {}"),
    ('local cfg = {"shape": "sphere", "radius": 0.3}',
     'local cfg = { shape = "sphere", radius = 0.3 }'),
    ("player:MidwayPhysics.MoveKinematic(1, 0, 0)",
     "MidwayPhysics.MoveKinematic(player, 1.0, 0.0, 0.0, dt)"),
    ("MidwayPhysics.SetMass(hBody)",
     "MidwayPhysics.SetMass(hBody, 5.0)"),
]


def _block(old: str, new: str) -> str:
    return f"<<<<<<< SEARCH\n{old}\n=======\n{new}\n>>>>>>> REPLACE"


def _gen_emit_correct(seed: int) -> dict:
    """S1: emit a well-formed SEARCH/REPLACE block."""
    rng = random.Random(seed)
    old, new = rng.choice(SNIPPET_POOL)
    return {"messages": [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Fix this line:\n```lua\n{old}\n```"},
        {"role": "assistant", "content": _block(old, new)},
    ]}


def _gen_strip_markers(seed: int) -> dict:
    """S2: leaked markers -> output clean code (no markers in the body)."""
    rng = random.Random(seed)
    _, new = rng.choice(SNIPPET_POOL)
    broken = f"<<<<<<< SEARCH\nlocal x = 1\n=======\n{new}\n>>>>>>> REPLACE"
    return {"messages": [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",
         "content": "The file body leaked SEARCH/REPLACE markers. Rewrite it as clean Lua:\n```lua\n" + broken + "\n```"},
        {"role": "assistant", "content": new},
    ]}


def _gen_orphaned_else(seed: int) -> dict:
    """S3: dangling else -> corrected if/else."""
    broken = (
        "function OnLoad()\n"
        "    else\n"
        "        swing_cooldown = math.max(0, swing_cooldown - dt)\n"
        "    end\n"
        "end\n"
    )
    fixed = (
        "function OnLoad()\n"
        "    if swing_phase >= 1.0 then\n"
        "        swing_cooldown = math.max(0, swing_cooldown - dt)\n"
        "    end\n"
        "end\n"
    )
    return {"messages": [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "This Lua has a dangling `else` with no `if`. Fix it with a SEARCH/REPLACE block:\n```lua\n" + broken + "\n```"},
        {"role": "assistant", "content": _block(
            "    else\n        swing_cooldown = math.max(0, swing_cooldown - dt)\n    end",
            "    if swing_phase >= 1.0 then\n        swing_cooldown = math.max(0, swing_cooldown - dt)\n    end",
        )},
    ]}


def _gen_keyword_concat(seed: int) -> dict:
    """S4: `endend` -> properly separated closers."""
    broken = "function OnLoad()\n    if x then\n        a = 1\nendend\n"
    fixed = "function OnLoad()\n    if x then\n        a = 1\n    end\nend\n"
    return {"messages": [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "Keywords were concatenated (`endend`). Emit a SEARCH/REPLACE block to fix it:\n```lua\n" + broken + "\n```"},
        {"role": "assistant", "content": _block("endend", "    end\nend")},
    ]}


def _gen_json_table(seed: int) -> dict:
    """S5: JSON table -> Lua key = value."""
    old = 'local cfg = {"shape": "sphere", "radius": 0.3}'
    new = 'local cfg = { shape = "sphere", radius = 0.3 }'
    return {"messages": [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Rewrite this JSON-style table as Lua:\n```lua\n{old}\n```"},
        {"role": "assistant", "content": _block(old, new)},
    ]}


def _gen_colon_call(seed: int) -> dict:
    """S6: `obj:MidwayPhysics.X` -> flat call."""
    old = "player:MidwayPhysics.MoveKinematic(1, 0, 0)"
    new = "MidwayPhysics.MoveKinematic(player, 1.0, 0.0, 0.0, dt)"
    return {"messages": [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Fix the method call:\n```lua\n{old}\n```"},
        {"role": "assistant", "content": _block(old, new)},
    ]}


SCENARIOS = [
    (_gen_emit_correct, 40),
    (_gen_strip_markers, 20),
    (_gen_orphaned_else, 10),
    (_gen_keyword_concat, 10),
    (_gen_json_table, 10),
    (_gen_colon_call, 10),
]


def generate(num_samples: int = 1500, seed: int = DEFAULT_SEED, output: Path = SEARCH_REPLACE_DATASET) -> int:
    rng = random.Random(seed)
    funcs = [f for f, _ in SCENARIOS]
    weights = [w for _, w in SCENARIOS]
    samples = [rng.choices(funcs, weights=weights, k=1)[0](i) for i in range(num_samples)]
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        for s in samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    print(f"Generated {len(samples)} SEARCH/REPLACE samples -> {output}")
    return len(samples)


def main():
    parser = argparse.ArgumentParser(description="Generate the SEARCH/REPLACE LoRA dataset")
    parser.add_argument("--num-samples", "-n", type=int, default=1500)
    parser.add_argument("--seed", "-s", type=int, default=DEFAULT_SEED)
    parser.add_argument("--output", "-o", default=str(SEARCH_REPLACE_DATASET))
    args = parser.parse_args()
    generate(args.num_samples, args.seed, Path(args.output))


if __name__ == "__main__":
    main()
