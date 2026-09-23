"""
arbiter_dataset_generator.py — deterministic training data for the supreme arbiter.

The coder has the failure-mode seeder (broken -> fixed deltas).  This is the
arbiter's analog: pair the SAME deterministic broken/clean code with the
CORRECT verdict, derived from ground truth rather than a template.

For every failure mode in `failure_mode_seeder.MUTATIONS` we emit:

  1. REJECT   — (broken code + open violation) -> [REJECT:Tribunal:<objection>]
  2. MERGE    — (fixed code) -> [MERGE:Tribunal:<justification>]
  3. QUESTION — (broken code) -> [QUESTION:oracle:<factual question>]
                teaches the arbiter to INTERROGATE before assuming.
  4. DEBATE   — a multi-turn chain: broken -> QUESTION -> oracle answer -> REJECT.
                Teaches the full ask/verify/rule loop the debate executes.

The objection text is deterministic because WE injected the defect; for modes
with an oracle-answerable question, the multi-turn sample uses the REAL oracle
answer (arbiter.answer_oracle_question), so the "fact" in the training data is
exactly what the arbiter will receive at inference.

Output: arbiter_lora_dataset.jsonl (ChatML JSONL, completion-only loss), folded
into the reasoner preset by combine_datasets.py.

Usage
-----
  python "lora generator/arbiter_dataset_generator.py" --files attractions/plinko/plinko.lua ...
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

from failure_mode_seeder import MUTATIONS  # noqa: E402
from arbiter import answer_oracle_question  # noqa: E402

TRIBUNAL_SYSTEM = (
    "You are the TRIBUNAL AGENT — a neutral appellate arbiter who ARGUES WITH THE CODER "
    "until the implementation is correct. You do NOT write code yourself.\n"
    "Each turn, review the implementation against the open violations and render ONE of:\n"
    "- [MERGE:Tribunal:<justification>] — the implementation is now acceptable.\n"
    "- [REJECT:Tribunal:<justification>] — followed by a numbered OBJECTIONS list of the "
    "specific, concrete defects the coder must still fix.\n"
    "- [QUESTION:oracle:<factual question>] — ask the DETERMINISTIC oracle to verify a "
    "fact: whether an API/global exists, a call's arity/signature, where a local is "
    "declared (module vs function scope), or whether the file compiles.\n"
    "- [QUESTION:coder:<design-intent question>] — ask the coder to explain its intent.\n"
    "Use QUESTION turns to interrogate the code BEFORE you render MERGE/REJECT. When "
    "unsure whether a symbol exists, ASK the oracle instead of assuming."
)

# Per failure mode: the factual question the arbiter SHOULD ask (or None), and
# the deterministic objection citing the specific defect we injected.
ARBITER_META = {
    "phantom_api": {
        "question": "Is MidwayPhysics.GetBodyFromHandle a real API?",
        "objection": "phantom API 'MidwayPhysics.GetBodyFromHandle' is not in the bridge contract",
    },
    "phantom_engine": {
        "question": "Is Engine.ModifyPuckState a real API?",
        "objection": "phantom API 'Engine.ModifyPuckState' is not in the bridge contract",
    },
    "bare_namespace": {
        "question": "Is SpawnStaticBox a MidwayPhysics member or a bare global?",
        "objection": "bare physics calls (SpawnStaticBox / PoolTotal / PoolAcquire / IsActionDown) "
                    "must be namespace-qualified with MidwayPhysics. / MidwayInput.",
    },
    "arity_over": {
        "question": "How many arguments does SpawnStaticBox take?",
        "objection": "SpawnStaticBox is called with 7 arguments but takes exactly 6",
    },
    "arity_under": {
        "question": "How many arguments does ApplyImpulse take?",
        "objection": "ApplyImpulse is called with 3 arguments but requires 4",
    },
    "modifier_standalone": {
        "question": None,
        "objection": "AttractionConstants.modifiers is read without an 'or {}' nil guard "
                    "(and risks being cached at load time instead of read per-frame)",
    },
    "modifier_lowercase": {
        "question": None,
        "objection": "the lowercase 'mods' table is not declared; modifiers must be read via "
                    "MOD or AttractionConstants.modifiers",
    },
    "duplicate_underscore": {
        "question": None,
        "objection": "duplicate '_' declarators in a single local statement are invalid Lua",
    },
    "bare_expression": {
        "question": None,
        "objection": "a bare expression statement is not a valid Lua statement",
    },
    "json_colon_table": {
        "question": None,
        "objection": "table constructor uses JSON-style quoted keys with colons; Lua requires key = value",
    },
    "broken_local": {
        "question": None,
        "objection": "truncated declaration is malformed Lua",
    },
    "roblox": {
        "question": None,
        "objection": "Roblox-style API (Vector3.new) is not available in this engine",
    },
}


def _fence(code: str) -> str:
    return "```\n" + (code or "").strip() + "\n```"


def _user(code: str, violations: str) -> str:
    return (
        "## Open Violations\n" + (violations.strip() or "(none listed)")
        + "\n\n## Implementation Under Review\n" + _fence(code)
        + "\n\nRender your verdict now."
    )


def generate(file_paths: list[str]) -> int:
    total = 0
    out = SCRIPT_DIR / "arbiter_lora_dataset.jsonl"
    with open(out, "w", encoding="utf-8") as fh:
        for fp in file_paths:
            if not Path(fp).is_file():
                print(f"  SKIP missing: {fp}")
                continue
            clean = Path(fp).read_text(encoding="utf-8")
            for name, meta in ARBITER_META.items():
                mutate = MUTATIONS.get(name)
                if mutate is None:
                    continue
                from _post_process_lua import post_process_lua
                broken = mutate(clean)
                fixed = post_process_lua(broken)
                if fixed == broken:
                    continue
                objection = meta["objection"]
                question = meta.get("question")

                # 1. REJECT: broken code -> cite the specific defect.
                fh.write(json.dumps({"messages": [
                    {"role": "system", "content": TRIBUNAL_SYSTEM},
                    {"role": "user", "content": _user(broken, objection)},
                    {"role": "assistant", "content": f"[REJECT:Tribunal:{objection}]"},
                ]}) + "\n")
                total += 1

                # 2. MERGE: fixed code -> approve.
                fh.write(json.dumps({"messages": [
                    {"role": "system", "content": TRIBUNAL_SYSTEM},
                    {"role": "user", "content": _user(fixed, "(none listed)")},
                    {"role": "assistant",
                     "content": "[MERGE:Tribunal:the flagged defect is resolved and the "
                                "implementation is now contract-clean]"},
                ]}) + "\n")
                total += 1

                if not question:
                    continue

                # 3. QUESTION: broken code -> interrogate the oracle.
                fh.write(json.dumps({"messages": [
                    {"role": "system", "content": TRIBUNAL_SYSTEM},
                    {"role": "user", "content": _user(broken, "(none listed)")},
                    {"role": "assistant", "content": f"[QUESTION:oracle:{question}]"},
                ]}) + "\n")
                total += 1

                # 4. DEBATE: multi-turn ask -> oracle answer -> reject.
                answered, fact = answer_oracle_question(question, broken)
                if not answered:
                    continue
                fh.write(json.dumps({"messages": [
                    {"role": "system", "content": TRIBUNAL_SYSTEM},
                    {"role": "user", "content": _user(broken, "(none listed)")},
                    {"role": "assistant", "content": f"[QUESTION:oracle:{question}]"},
                    {"role": "user", "content": f"[oracle] {fact}"},
                    {"role": "assistant", "content": f"[REJECT:Tribunal:{objection}]"},
                ]}) + "\n")
                total += 1

    print(f"Wrote {total} arbiter sample(s) -> {out.name}")
    return total


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--files", nargs="+", help="clean Lua files to derive arbiter samples from")
    args = ap.parse_args()
    if not args.files:
        ap.error("--files is required")
    generate(args.files)


if __name__ == "__main__":
    main()
