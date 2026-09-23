#!/usr/bin/env python3
"""
verdict_lora_generator.py
=========================
Generates the JSONL dataset that teaches the REASONING personas (Reviewer,
Tribunal, Conflict Resolution, Director, DOC) how to review, arbitrate, and
decompose — the argumentation layer the coder does NOT need.

This is the FIFTH dataset feed and is intentionally SEPARATE from the coder
feeds (paging / search_replace / cartridge / signals). It trains the
`midway-reasoner-lora` adapter, a sibling of `midway-coder-lora` built from the
SAME base model (Qwen2.5-Coder-7B-Instruct).

Scenario families:
  R1  Review verdict    — read code + contract rule, emit VETO/OBJECT/REVISE/APPROVE
  R2  Tribunal ruling   — read an appeal + contract, emit MERGE/REJECT
  R3  Conflict mediation— read a VETO + both sides, emit SUSTAIN/OVERRULE/COMPROMISE
  R4  Director planning — decompose a feature into DELEGATE/AMBIGUITY/RECOURSE
  R5  DOC oracle        — answer QUERY/CONSULT with an EXACT signature + source

Every signal uses the SAME bracket-tag syntax as signals_lora_generator.py so
the reasoner and coder share one parser (signals.py::SIGNAL_PATTERNS).

Output: verdict_lora_dataset.jsonl (ChatML JSONL, completion-only loss).

Usage
-----
  python lora_generator/verdict_lora_generator.py --num-samples 1500 --seed 42
"""

import argparse
import json
import random
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from lora_config import VERDICT_DATASET, SEED as DEFAULT_SEED


# ---------------------------------------------------------------------------
# Persona system prompts — terse, enumerate ONLY the tags that persona emits.
# ---------------------------------------------------------------------------

PERSONAS = {
    "REVIEWER": {
        "name": "Integration Reviewer",
        "system": (
            "You are the Integration Reviewer for 'Midway to Nowhere'. Validate "
            "agent output against the engine contract. You do NOT write code. "
            "Emit exactly one verdict signal per defect:\n"
            "- [VETO:<target>:<justification>] hard-block a contract violation\n"
            "- [OBJECT:<target>:<justification>] raise a non-fatal concern\n"
            "- [REVISE:<target>:<reason>] request a specific revision\n"
            "- [APPROVE] sign off on correct output\n"
            "Cite the specific rule you are enforcing."
        ),
    },
    "TRIBUNAL": {
        "name": "Tribunal",
        "system": (
            "You are the TRIBUNAL agent — a neutral appellate arbiter. You do "
            "NOT write code. Judge solely on technical merit against the bridge "
            "contract. Render a BINDING verdict with exactly one signal:\n"
            "- [MERGE:<target>:<justification>] the implementation is correct\n"
            "- [REJECT:<target>:<justification>] the veto is correct\n"
        ),
    },
    "CONF": {
        "name": "Conflict Resolution",
        "system": (
            "You are the Conflict Resolution Agent for 'Midway to Nowhere'. "
            "Mediate VETO/OBJECT disputes. You do NOT write code. Decide with "
            "exactly one of:\n"
            "- SUSTAIN VETO — the original is more correct\n"
            "- OVERRULE VETO — the change is technically correct and preserves intent\n"
            "- COMPROMISE — produce a merged version\n"
            "Preserve feature intent over technical purity."
        ),
    },
    "DIRECTOR": {
        "name": "Director",
        "system": (
            "You are the Director for 'Midway to Nowhere'. Decompose features "
            "into sub-tasks and arbitrate the mesh with bracket-tag signals:\n"
            "- [DELEGATE:<domain>:<task>] assign work to an agent\n"
            "- [AMBIGUITY:<target>:<issue>] flag an underspecified requirement\n"
            "- [RECOURSE:<target>:<resolution>] escalate an unresolved dispute\n"
        ),
    },
    "DOC": {
        "name": "Code Documentarian",
        "system": (
            "You are the API Documentation Oracle for 'Midway to Nowhere'. "
            "Answer [QUERY:...] / [CONSULT:...] with the EXACT function "
            "signature, arity, and source. Never invent a signature."
        ),
    },
}


def _sig2(tag: str, target: str, content: str) -> str:
    return f"[{tag}:{target}:{content}]"


def _sig0(tag: str) -> str:
    return f"[{tag}]"


def _sample(system: str, turns) -> dict:
    return {"messages": [{"role": "system", "content": system}] + [
        {"role": r, "content": c} for (r, c) in turns
    ]}


# ---------------------------------------------------------------------------
# Scenario builders
# ---------------------------------------------------------------------------

# (broken code, fixed code, rule) triples grounded in the Midway contract.
CONTRACT_DEFECTS = [
    ("local hBall = MidwayPhysics.SpawnDynamicBall(0, 1, 2, 0.5)",
     "local hBall = MidwayPhysics.SpawnDynamicSphere(0.0, 1.0, 2.0, 0.5)",
     "SpawnDynamicBall is a phantom API; use SpawnDynamicSphere(lx, ly, lz, radius)."),
    ("local MOD = AttractionConstants.modifiers",
     "local MOD = AttractionConstants.modifiers or {}",
     "AttractionConstants.modifiers must be read per-frame in OnStep, not cached."),
    ("AwardTickets(100)",
     "if MidwayPhysics.IsSensorTriggered(bell_sensor) then MidwayPhysics.AwardTickets(100) end",
     "AwardTickets must only fire after IsSensorTriggered confirms the bell hit."),
    ("MidwayPhysics.SpawnStaticBox(0,0,0,10,2,10,1.0)",
     "MidwayPhysics.SpawnStaticBox(0.0, 0.0, 0.0, 10.0, 2.0, 10.0)",
     "SpawnStaticBox takes six arguments; the seventh is a phantom arity overflow."),
]


def _gen_review(rng: random.Random) -> dict:
    """R1: reviewer reads code + rule, emits a verdict signal."""
    broken, fixed, rule = rng.choice(CONTRACT_DEFECTS)
    reviewer = PERSONAS["REVIEWER"]
    # Pick a verdict kind by severity.
    kind = rng.choices(["VETO", "REVISE", "OBJECT"], weights=[4, 3, 2], k=1)[0]
    code = f"```lua\n{broken}\n```"
    user = f"## Implementation under review\n{code}\n\nRule: {rule}"
    if kind == "VETO":
        out = _sig2("VETO", "Lua", rule)
    elif kind == "REVISE":
        out = _sig2("REVISE", "Lua", rule)
    else:
        out = _sig2("OBJECT", "Lua", rule)
    return _sample(reviewer["system"], [("user", user), ("assistant", out)])


def _gen_review_approve(rng: random.Random) -> dict:
    """R1b: a correct implementation gets [APPROVE]."""
    reviewer = PERSONAS["REVIEWER"]
    code = (
        "```lua\n"
        "function OnLoad()\n"
        "    local hBall = MidwayPhysics.SpawnDynamicSphere(0.0, 1.0, 2.0, 0.5)\n"
        "end\n"
        "```"
    )
    user = f"## Implementation under review\n{code}"
    out = "The spawn uses the correct signature and the handle is returned. [APPROVE]"
    return _sample(reviewer["system"], [("user", user), ("assistant", out)])


def _gen_tribunal(rng: random.Random) -> dict:
    """R2: tribunal reads an appeal + contract, emits MERGE/REJECT."""
    tribunal = PERSONAS["TRIBUNAL"]
    merge = rng.random() < 0.5
    appeal = _sig2(
        "APPEAL", "Reviewer",
        "ApplyImpulse is documented with a 3-arg overload in the bridge contract")
    user = f"## Appeal\n{appeal}\n\n## Contract\nApplyImpulse(handle, ix, iy, iz) — 3-arg overload is listed."
    if merge:
        out = _sig2("MERGE", "Lua", "The bridge contract lists a 3-arg ApplyImpulse overload; the veto is overturned.")
    else:
        out = _sig2("REJECT", "Lua", "The 3-arg overload is for static bodies only; the veto stands.")
    return _sample(tribunal["system"], [("user", user), ("assistant", out)])


def _gen_conf(rng: random.Random) -> dict:
    """R3: conflict mediator resolves a VETO/OBJECT dispute."""
    conf = PERSONAS["CONF"]
    decision = rng.choices(["SUSTAIN", "OVERRULE", "COMPROMISE"], weights=[3, 2, 3], k=1)[0]
    veto = _sig2("VETO", "Lua", "SpawnDynamicBall is a phantom API")
    defense = ("Coder defense: the code predates the contract rename and should be migrated, not rejected.")
    user = f"## Dispute\n{veto}\n{defense}"
    if decision == "SUSTAIN":
        out = ("SUSTAIN VETO: the phantom API will fail at runtime regardless of "
               "history; the coder must use SpawnDynamicSphere.")
    elif decision == "OVERRULE":
        out = ("OVERRULE VETO: the coder's output is already correct against the "
               "latest contract; the veto reads a stale rule.")
    else:
        out = ("COMPROMISE: accept the coder's structure but require the migrated "
               "signature before merge.")
    return _sample(conf["system"], [("user", user), ("assistant", out)])


def _gen_director(rng: random.Random) -> dict:
    """R4: director decomposes a feature."""
    director = PERSONAS["DIRECTOR"]
    kind = rng.choices(["DELEGATE", "AMBIGUITY", "RECOURSE"], weights=[5, 3, 2], k=1)[0]
    if kind == "DELEGATE":
        task = rng.choice([
            "Model the mallet lever as a Jolt hinge constraint",
            "Instrument the bell-hit scoring path with native print telemetry",
            "Implement the wager-gated scoring using the streak multiplier",
        ])
        domain = rng.choice(["PHYS", "OBSERVABILITY", "Lua"])
        user = "Decompose the strongman striker feature."
        out = _sig2("DELEGATE", domain, task)
    elif kind == "AMBIGUITY":
        user = "Plan the bell tower. The GDD says only 'the bell rises when struck'."
        out = _sig2("AMBIGUITY", "User",
                    "The bell's max rise height and scoring bands are unspecified")
    else:
        user = "Lua and PHYS disagree on the ApplyImpulse arity."
        out = _sig2("RECOURSE", "TRIBUNAL",
                    "Resolve the ApplyImpulse arity dispute between Lua and PHYS")
    return _sample(director["system"], [("user", user), ("assistant", out)])


def _gen_doc(rng: random.Random) -> dict:
    """R5: DOC oracle answers a QUERY/CONSULT with an exact signature."""
    doc = PERSONAS["DOC"]
    q = rng.choice([
        ("QUERY", "What is the exact signature and arity of MidwayPhysics.SpawnDynamicCapsule",
         "MidwayPhysics.SpawnDynamicCapsule(lx, ly, lz, radius, half_height) -> integer handle. Source: docs/engine_lua_bridge_contract.md"),
        ("QUERY", "What are the arguments to MidwayPhysics.AwardTickets",
         "MidwayPhysics.AwardTickets(ticket_count, label) — label is optional. Source: docs/engine_lua_bridge_contract.md"),
        ("CONSULT", "Does IsSensorTriggered fire once or continuously",
         "MidwayPhysics.IsSensorTriggered(handle) returns true on first overlap and stays true until separation."),
    ])
    tag, question, answer = q
    user = f"{question}"
    out = answer
    return _sample(doc["system"], [("user", user), ("assistant", out)])


# ---------------------------------------------------------------------------
# Top-level generation
# ---------------------------------------------------------------------------

def generate(num_samples: int, seed: int) -> list:
    rng = random.Random(seed)
    samples = []
    builders = [_gen_review, _gen_review_approve, _gen_tribunal, _gen_conf,
                _gen_director, _gen_doc]
    weights = [30, 10, 20, 15, 20, 15]
    while len(samples) < num_samples:
        b = rng.choices(builders, weights=weights, k=1)[0]
        samples.append(b(rng))
    return samples[:num_samples]


def main():
    parser = argparse.ArgumentParser(description="Generate the verdict/reasoner LoRA dataset")
    parser.add_argument("--num-samples", type=int, default=1500)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--output", default=str(VERDICT_DATASET))
    args = parser.parse_args()

    samples = generate(args.num_samples, args.seed)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for s in samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    print(f"Wrote {len(samples)} samples -> {out}")


if __name__ == "__main__":
    main()
