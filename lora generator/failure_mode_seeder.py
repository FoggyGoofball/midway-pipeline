"""
failure_mode_seeder.py — generate broken->fixed training data on demand.

Two generators feed the SAME capture path (post_process_lua_observed ->
failure_corpus.jsonl -> failure_corpus_to_dataset.py -> combine_datasets.py):

1. Deterministic mutations (fast, repeatable, guaranteed coverage):
   take a clean Lua file and inject a known-broken construct; the
   deterministic fixer rewrites it and record_fix() captures the delta.
   Thousands of samples/hour, every failure mode covered, seedable.

2. Adversarial prompt harvesting (realistic, model-distributed):
   loop "shoddy prompts" that encourage each known failure mode against the
   coder model; capture the model's actual broken output and its
   deterministic repair.  This is the distribution the fine-tuned model will
   face at inference.

Both write JSONL records via failure_corpus.record_fix (gated by
MIDWAY_FAILURE_CORPUS=1), so the existing converter + combiner pick them up
unchanged.

Usage
-----
  # Deterministic: inject every failure mode into every clean file, capture deltas.
  python "lora generator/failure_mode_seeder.py" --mutate attractions/booth_shared.lua attractions/plinko.lua

  # Self-test: verify which mutations actually produce a fixer delta (no writing).
  python "lora generator/failure_mode_seeder.py" --selftest attractions/booth_shared.lua

  # Harvest: run each shoddy prompt N times against the coder, capture deltas.
  python "lora generator/failure_mode_seeder.py" --harvest --model midway-coder-lora --per-prompt 20
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Callable, Dict, List

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
_REPO_ROOT = SCRIPT_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Capture MUST be on for the seeder to be useful; set it before importing the
# post-processor so the observed pass records.
os.environ.setdefault("MIDWAY_FAILURE_CORPUS", "1")

from _post_process_lua import post_process_lua_observed, post_process_lua  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# 1. Deterministic mutation catalog
# ─────────────────────────────────────────────────────────────────────────────
# Each mutation takes CLEAN Lua code and returns code with ONE failure mode
# injected.  The fixer's corresponding fix rewrites it, and the observed pass
# records the delta.  Names mirror the fix they are designed to trigger.

def _append(code: str, snippet: str) -> str:
    return (code.rstrip() + "\n\n" + snippet + "\n")


def m_bare_namespace(code: str) -> str:  # Fix #6
    return _append(code, (
        "local base = SpawnStaticBox(0, 0, 0, 4, 4, 4)\n"
        "local ball = SpawnDynamicSphere(0, 1, 0, 1)\n"
        "PoolTotal('pucks')\n"
        "PoolAcquire('pucks', 0, 0, 0)\n"
        "if IsActionDown('fire') then return end\n"
    ))


def m_phantom_api(code: str) -> str:  # Fix #8 / #21
    return _append(code, (
        "local b = MidwayPhysics.GetBodyFromHandle(h)\n"
        "local p = MidwayPhysics.SetActionState(h, true)\n"
        "booth:GetPhysicsObject()\n"
    ))


def m_phantom_engine(code: str) -> str:  # Fix #22
    return _append(code, (
        "local t = Engine.ModifyPuckState(h)\n"
        "local s = Engine.SetStreak(9)\n"
    ))


def m_modifier_standalone(code: str) -> str:  # Fix #31
    return _append(code, "local MOD = AttractionConstants.modifiers\n")


def m_modifier_lowercase(code: str) -> str:  # Fix #9
    return _append(code, (
        "local heat = mods.heat\n"
        "local luck = mods.luck\n"
    ))


def m_arity_over(code: str) -> str:  # Fix G2 (truncate)
    return _append(code, "MidwayPhysics.SpawnStaticBox(0, 0, 0, 1, 1, 1, 9)\n")


def m_arity_under(code: str) -> str:  # Fix G2 (pad)
    return _append(code, "MidwayPhysics.ApplyImpulse(h, 0, 1)\n")


def m_duplicate_underscore(code: str) -> str:  # Fix #13
    return _append(code, "local _ = MOD.heat, _ = MOD.luck\n")


def m_bare_expression(code: str) -> str:  # Fix #15
    return _append(code, "MOD.heat\n")


def m_json_colon_table(code: str) -> str:  # Fix #25
    return _append(code, 'local t = { "radius": 1, "mass": 2 }\n')


def m_broken_local(code: str) -> str:  # Fix #24
    return _append(code, "local lx)\n")


def m_roblox(code: str) -> str:  # Fix #29
    return _append(code, "local v = Vector3.new(1, 2, 3)\n")


MUTATIONS: Dict[str, Callable[[str], str]] = {
    "bare_namespace": m_bare_namespace,
    "phantom_api": m_phantom_api,
    "phantom_engine": m_phantom_engine,
    "modifier_standalone": m_modifier_standalone,
    "modifier_lowercase": m_modifier_lowercase,
    "arity_over": m_arity_over,
    "arity_under": m_arity_under,
    "duplicate_underscore": m_duplicate_underscore,
    "bare_expression": m_bare_expression,
    "json_colon_table": m_json_colon_table,
    "broken_local": m_broken_local,
    "roblox": m_roblox,
}

# ─────────────────────────────────────────────────────────────────────────────
# 2. Adversarial prompt catalog (shoddy prompts that encourage misbehaviour)
# ─────────────────────────────────────────────────────────────────────────────
# Each template is a coder prompt engineered to trigger ONE failure mode.  The
# harvester runs them against the coder model and captures the resulting
# broken -> fixed delta via the same observed pass.

PROMPT_TEMPLATES: Dict[str, str] = {
    "phantom_api": (
        "Write a Lua helper that reads the puck's body object using "
        "GetBodyFromHandle and toggles it with SetActionState."
    ),
    "phantom_engine": (
        "Access the puck through the Engine.Puck table and modify it with "
        "Engine.ModifyPuckState."
    ),
    "modifier_standalone": (
        "Read the live modifiers into a local variable at the top of the file "
        "and use it throughout."
    ),
    "modifier_lowercase": (
        "Use the lowercase `mods` table for the heat, luck, and sleight_of_hand "
        "modifiers in your code."
    ),
    "arity_over": (
        "Spawn a static box with all seven parameters: x, y, z, width, height, "
        "depth, and mass."
    ),
    "arity_under": (
        "Apply an impulse to the puck using only three numbers."
    ),
    "roblox": (
        "Write it in Roblox style using workspace, Vector3.new, and "
        "player:GetAttribute."
    ),
    "scope_leak": (
        "Declare the mallet handle inside OnLoad but also use it in OnUnload "
        "to destroy it."
    ),
    "json_table": (
        "Build the pool params table using JSON style with quoted keys and "
        "colons."
    ),
    "comment_monologue": (
        "Write a long explanatory comment essay arguing for your approach "
        "before the code."
    ),
    "bare_calls": (
        "Call SpawnStaticBox, PoolTotal, and PoolAcquire directly without any "
        "namespace prefix."
    ),
}

# ─────────────────────────────────────────────────────────────────────────────
# Runners
# ─────────────────────────────────────────────────────────────────────────────

def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def run_mutations(file_paths: List[str]) -> Dict[str, int]:
    """Inject every mutation into every file; record each fixer delta.

    Returns {file: number_of_new_records} (approx — record_fix dedupes).
    """
    from failure_corpus import read_corpus, corpus_path
    _corpus = corpus_path("initial")
    before_count = len(read_corpus(_corpus))
    for fp in file_paths:
        if not Path(fp).is_file():
            print(f"  [mutate] SKIP missing file: {fp}")
            continue
        code = _read(fp)
        rel = str(Path(fp).name)  # corpus 'file' field: short name is fine
        for name, mutate in MUTATIONS.items():
            broken = mutate(code)
            fixed = post_process_lua_observed(broken, file_relpath=rel, source="initial")
            if fixed == broken:
                print(f"  [mutate] {name}: NO DELTA (fixer left it unchanged) — check mutation")
            else:
                print(f"  [mutate] {name}: captured delta")
    after_count = len(read_corpus(_corpus))
    return {"new_records": after_count - before_count}


def selftest(file_path: str) -> None:
    """Report, per mutation, whether it produces a fixer delta (no writing).

    Uses the NON-observed pass so the self-test does not pollute the corpus.
    """
    code = _read(file_path)
    for name, mutate in MUTATIONS.items():
        broken = mutate(code)
        fixed = post_process_lua(broken)
        print(f"  {name:22s} -> {'DELTA' if fixed != broken else 'no-op'} "
              f"(broken {len(broken)} chars, fixed {len(fixed)} chars)")


def _ollama_chat(model: str, system: str, user: str) -> str:
    """Minimal Ollama /api/chat call (Deck by default, OLLAMA_HOST overridable)."""
    import urllib.request
    host = os.environ.get("OLLAMA_HOST", "http://192.168.0.16:11434")
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "options": {"num_predict": 1024},
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{host.rstrip('/')}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=1800) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return (body.get("message") or {}).get("content", "")


def run_harvest(model: str, per_prompt: int) -> None:
    """Run every shoddy prompt N times against the coder; capture fixer deltas."""
    system = (
        "You are a Lua scripter for a physics game. Write ONLY Lua code — no "
        "prose, no markdown fences."
    )
    for name, prompt in PROMPT_TEMPLATES.items():
        for i in range(per_prompt):
            print(f"  [harvest] {name} #{i + 1}/{per_prompt}")
            try:
                out = _ollama_chat(model, system, prompt)
            except Exception as exc:
                print(f"    !! model call failed: {exc}")
                continue
            if not out.strip():
                continue
            post_process_lua_observed(out, file_relpath=f"harvest/{name}.lua")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mutate", nargs="+", help="clean Lua files to mutate + observe")
    ap.add_argument("--selftest", help="report per-mutation delta yield for one file")
    ap.add_argument("--harvest", action="store_true", help="run shoddy-prompt harvesting")
    ap.add_argument("--model", default="midway-coder-lora")
    ap.add_argument("--per-prompt", type=int, default=20)
    args = ap.parse_args()

    if args.selftest:
        selftest(args.selftest)
    elif args.mutate:
        print(f"  [seed] mutating {len(args.mutate)} file(s) x {len(MUTATIONS)} mutations")
        result = run_mutations(args.mutate)
        print(f"  [seed] done — {result['new_records']} new corpus record(s).")
    elif args.harvest:
        print(f"  [seed] harvesting {len(PROMPT_TEMPLATES)} templates x {args.per_prompt} "
              f"against {args.model}")
        run_harvest(args.model, args.per_prompt)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
