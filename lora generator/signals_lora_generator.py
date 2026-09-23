#!/usr/bin/env python3
"""
signals_lora_generator.py
=========================
Generates a JSONL dataset that teaches the coder (and the other personas)
the mesh communication protocol: the bracket-tag signals used to route work,
defer decisions, and flag ambiguity across the agent mesh.

Source of truth: docs/SIGNAL_TAGS_REFERENCE.md (Part A — 16 live tags).
Every sample is synthesized so that the assistant's tag output matches the
exact capture shape of signals.py::SIGNAL_PATTERNS (four-layer agreement:
prompt enumeration + SignalType enum + parser regex + dispatcher branch).

Three scenario families per tag (docs/LORA_GENERATION_DESIGN.md, Dimension 4):

  E1  EMIT (blocking)    — the agent cannot proceed without information, so it
                           emits a QUERY/CONSULT/AMBIGUITY signal INSTEAD of
                           guessing (the single biggest convergence killer).
  E2  EMIT (alongside)   — the agent emits a signal ALONGSIDE completed work:
                           RESULT/DELEGATE/APPEAL/VETO/OBJECT/REVISE/APPROVE
                           accompany a SEARCH/REPLACE block or a verdict,
                           never replace it.
  E3  RESPONSE           — the agent RECEIVES a signal (injected into the user
                           turn exactly as the dispatcher folds it back) and
                           reacts: VETO -> APPEAL or re-do, CONSULT/QUERY ->
                           answer, REVISE -> revise, AMBIGUITY -> clarify.
  E4  ROUND-TRIP         — a multi-turn exchange where the assistant emits a
                           signal, the parser's capture shape (type/target/
                           content) is mirrored back, and the assistant
                           continues with the actual deliverable.

Output is ChatML JSONL, identical to the paging/cartridge/search_replace
datasets so it mixes into the same combined_lora_dataset.jsonl.

Usage
-----
  python lora_generator/signals_lora_generator.py --num-samples 2000 --seed 42
"""

import argparse
import json
import random
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from lora_config import SIGNALS_DATASET, SEED as DEFAULT_SEED


# ---------------------------------------------------------------------------
# Personas — system prompts enumerate ONLY the tags that persona may emit.
# Kept terse (the model learns the grammar, not an essay), but the tag syntax
# is spelled out so prompt enumeration agrees with the parser layer.
# ---------------------------------------------------------------------------

PERSONAS = {
    "LUA": {
        "name": "Lua Scripter",
        "system": (
            "You are the Lua coder for 'Midway to Nowhere'. You edit code by "
            "emitting ONE three-part SEARCH/REPLACE block per defect:\n"
            "<<<<<<< SEARCH\n<exact current code>\n=======\n<replacement code>\n"
            ">>>>>>> REPLACE\n\n"
            "You route work and flag uncertainty with bracket-tag signals, "
            "emitted ALONGSIDE code when work is done, or INSTEAD of guessing "
            "when you lack information:\n"
            "- [QUERY:<domain>:<question>] ask another agent for information\n"
            "- [CONSULT:<domain>:<question>] ask a focused technical question\n"
            "- [DELEGATE:<domain>:<task>] hand off a sub-task to a specialist\n"
            "- [AMBIGUITY:<target>:<issue>] flag an underspecified or contradictory spec\n"
            "- [APPEAL:<target>:<defense>] defend against a VETO you believe is wrong\n"
            "- [RESULT:<summary>] mark work complete\n"
            "NEVER invent an API you are unsure of — QUERY or CONSULT first."
        ),
    },
    "REVIEWER": {
        "name": "Integration Reviewer",
        "system": (
            "You are the Integration Reviewer for 'Midway to Nowhere'. You "
            "validate agent output against the engine contract. You do NOT "
            "write code. You speak only in bracket-tag signals:\n"
            "- [VETO:<target>:<justification>] hard-block output that violates the contract\n"
            "- [OBJECT:<target>:<justification>] raise a non-fatal concern\n"
            "- [REVISE:<target>:<reason>] request a specific revision\n"
            "- [APPROVE] sign off on correct output\n"
        ),
    },
    "DIRECTOR": {
        "name": "Director",
        "system": (
            "You are the Director for 'Midway to Nowhere'. You decompose work "
            "and arbitrate the mesh. You speak in bracket-tag signals:\n"
            "- [DELEGATE:<domain>:<task>] assign a sub-task to an agent\n"
            "- [AMBIGUITY:<target>:<issue>] flag an underspecified requirement\n"
            "- [RECOURSE:<target>:<resolution>] escalate an unresolved dispute\n"
        ),
    },
    "DOC": {
        "name": "Code Documentarian",
        "system": (
            "You are the API Documentation Oracle for 'Midway to Nowhere'. "
            "You answer [QUERY:...] and [CONSULT:...] questions with EXACT "
            "function signatures and arities from the bridge contract. "
            "Never invent a signature."
        ),
    },
    "TRIBUNAL": {
        "name": "Tribunal",
        "system": (
            "You are the TRIBUNAL agent — a neutral appellate arbiter. You do "
            "NOT write code. You render a BINDING verdict with exactly one "
            "signal:\n"
            "- [MERGE:<target>:<justification>] the implementation is correct\n"
            "- [REJECT:<target>:<justification>] the veto is correct\n"
        ),
    },
    "PHYS": {
        "name": "Physics Architect",
        "system": (
            "You are the Physics Architect for 'Midway to Nowhere'. You edit "
            "code with SEARCH/REPLACE blocks and route physics questions with "
            "bracket-tag signals:\n"
            "- [CONSULT:<domain>:<question>]\n"
            "- [REQUEST_API: <name> | https://<url>] pull an external API doc\n"
            "- [AMBIGUITY:<target>:<issue>]\n"
        ),
    },
}

# ---------------------------------------------------------------------------
# Tag specs — the canonical syntax + realistic (target, content) pairs.
# Targets are real domain keys (DOC, PHYS, TRIBUNAL, REVIEWER, Lua, C++, …)
# so resolve_agent_name() resolves them in the live pipeline.
# ---------------------------------------------------------------------------

# (persona, target, content) example banks per tag. Content avoids raw ':' so
# the greedy parser splits target/content on the FIRST colon deterministically.
TAG_EXAMPLES = {
    "QUERY": [
        ("LUA", "DOC", "What is the exact signature and arity of MidwayPhysics.SpawnDynamicCapsule"),
        ("LUA", "DOC", "Does IsSensorTriggered(handle) fire on enter only or every frame while overlapping"),
        ("PHYS", "DOC", "What is the impulse API for a one-shot bell strike"),
        ("LUA", "LIBRARIAN", "Which modifier keys does the strongman attraction read from AttractionConstants"),
    ],
    "CONSULT": [
        ("LUA", "PHYS", "Should the mallet reset use SetLinearVelocity or MoveKinematic to stop cleanly"),
        ("PHYS", "DOC", "Is the puck a dynamic sphere or a capsule in the bridge contract"),
        ("LUA", "DOC", "Does AwardTickets require a streak multiplier argument"),
    ],
    "DELEGATE": [
        ("DIRECTOR", "OBSERVABILITY", "Instrument the bell-hit scoring path with native print telemetry"),
        ("DIRECTOR", "PHYS", "Model the mallet lever as a Jolt hinge constraint"),
        ("LUA", "PHYS", "Confirm the correct restitution for the puck launch impulse"),
    ],
    "AMBIGUITY": [
        ("LUA", "User", "The bell's maximum rise height and scoring bands are not specified so the OnStep physics is underdetermined"),
        ("DIRECTOR", "User", "The spec says the striker is both timed and wager-gated without saying which governs the streak"),
        ("LUA", "User", "The task asks for a sensor hit zone but never names which handle triggers the score"),
        ("PHYS", "User", "The launch impulse magnitude is not given so the puck apex height cannot be computed"),
    ],
    "APPEAL": [
        ("LUA", "Reviewer", "ApplyImpulse is documented with a 3-arg overload in the bridge contract so the veto misreads the arity table"),
        ("LUA", "Reviewer", "GetStreak returns an integer per the economy contract not a float"),
    ],
    "RESULT": [
        ("LUA", "", "OnLoad spawns the ball and registers OnStep"),
        ("LUA", "", "Scoring awards tickets only after the sensor confirms the bell hit"),
    ],
    "VETO": [
        ("REVIEWER", "Lua", "SpawnDynamicBall is a phantom API use SpawnDynamicSphere"),
        ("REVIEWER", "Lua", "Modifiers are cached in OnLoad but must be read each frame in OnStep"),
    ],
    "OBJECT": [
        ("REVIEWER", "Lua", "The scoring callback awards tickets before the sensor confirms the hit"),
        ("REVIEWER", "PHYS", "The puck uses a box collider but the contract specifies a sphere"),
    ],
    "REVISE": [
        ("REVIEWER", "Lua", "Award tickets only after IsSensorTriggered confirms the bell hit"),
        ("REVIEWER", "Lua", "Register exactly one OnStep callback the current code registers two"),
    ],
    "RECOURSE": [
        ("DIRECTOR", "TRIBUNAL", "Resolve the ApplyImpulse arity dispute between Lua and PHYS"),
    ],
    "APPROVE": [
        ("REVIEWER", "", ""),
    ],
    "MERGE": [
        ("TRIBUNAL", "Lua", "The bridge contract lists a 3-arg ApplyImpulse overload so the veto is overturned"),
    ],
    "REJECT": [
        ("TRIBUNAL", "Lua", "The appeal misreads the arity table and the reviewer veto stands"),
    ],
    "REQUEST_API": [
        ("PHYS", "Jolt BodyInterface", "https://jrouwe.github.io/JoltPhysics/"),
    ],
    "FLUSH": [
        ("LUA", "", ""),
    ],
    "AST_PATCH": [
        ("LUA", "attractions/strongman/strongman.lua", "-- hoist the mallet handle to module scope\nlocal mallet_handle = nil"),
    ],
}

# Relative weights for sampling. AMBIGUITY + the deferral/routing tags are the
# highest-value behavior (they stop the coder guessing); closure tags and the
# multi-line AST_PATCH are lower weight.
TAG_WEIGHTS = {
    "AMBIGUITY": 18, "QUERY": 12, "CONSULT": 12, "DELEGATE": 12,
    "VETO": 10, "OBJECT": 8, "REVISE": 8, "APPEAL": 8,
    "MERGE": 6, "REJECT": 6, "RECOURSE": 6,
    "RESULT": 4, "APPROVE": 4, "FLUSH": 2, "REQUEST_API": 2, "AST_PATCH": 2,
}

# Small Midway-flavoured code snippets so emit-alongside samples show a real
# SEARCH/REPLACE block next to the signal.
PATCH_POOL = [
    ("local hPuck = MidwayPhysics.SpawnDynamicBall(0, 1, 2, 0.5)",
     "local hPuck = MidwayPhysics.SpawnDynamicSphere(0.0, 1.0, 2.0, 0.5)"),
    ("RemoveBody(hPuck)",
     "MidwayPhysics.DestroyBody(hPuck)"),
    ("local MOD = AttractionConstants.modifiers",
     "local MOD = AttractionConstants.modifiers or {}"),
    ("player:MidwayPhysics.MoveKinematic(1, 0, 0)",
     "MidwayPhysics.MoveKinematic(player, 1.0, 0.0, 0.0, dt)"),
    ("MidwayPhysics.SetMass(hBody)",
     "MidwayPhysics.SetMass(hBody, 5.0)"),
    ("AwardTickets(100)",
     "MidwayPhysics.AwardTickets(100 * (1 + MidwayPhysics.GetStreak()))"),
]


# ---------------------------------------------------------------------------
# Tag formatting helpers — must match signals.py regexes exactly.
# ---------------------------------------------------------------------------

def _sig2(tag: str, target: str, content: str) -> str:
    """Two-arg bracket signal: [TAG:target:content]."""
    return f"[{tag}:{target}:{content}]"


def _sig1(tag: str, content: str) -> str:
    """One-arg bracket signal: [TAG:content]."""
    return f"[{tag}:{content}]"


def _sig0(tag: str) -> str:
    """Zero-arg bracket signal: [TAG]."""
    return f"[{tag}]"


def _block(old: str, new: str) -> str:
    return f"<<<<<<< SEARCH\n{old}\n=======\n{new}\n>>>>>>> REPLACE"


# ---------------------------------------------------------------------------
# Scenario builders
# ---------------------------------------------------------------------------

def _sample(system: str, turns) -> dict:
    """Build a ChatML message dict from a persona + list of (role, content)."""
    return {"messages": [{"role": "system", "content": system}] + [
        {"role": r, "content": c} for (r, c) in turns
    ]}


def _gen_emit_blocking(tag: str, rng: random.Random) -> dict:
    """E1: the agent emits a blocking signal INSTEAD of guessing.

    Covers QUERY / CONSULT / AMBIGUITY / REQUEST_API — signals that pause the
    task until the mesh answers. The assistant emits ONLY the signal.
    """
    persona_key, target, content = rng.choice(TAG_EXAMPLES[tag])
    persona = PERSONAS[persona_key]
    if tag == "REQUEST_API":
        name, url = target, content
        out = f"[REQUEST_API: {name} | {url}]"
        user = "You need the Jolt body-interface reference to expose the impulse API."
        return _sample(persona["system"], [("user", user), ("assistant", out)])

    out = _sig2(tag, target, content)
    # Task prompts mirror the real failure: a vague/underspecified spec.
    if tag == "AMBIGUITY":
        user = ("Plan the bell tower mechanic. The GDD says only 'the bell rises "
                "when struck'.")
    elif tag == "QUERY":
        user = ("Implement the mallet strike. The exact spawn signature is not in "
                "your context.")
    else:  # CONSULT
        user = ("Write the mallet reset logic, but confirm the correct motion API "
                "first.")
    return _sample(persona["system"], [("user", user), ("assistant", out)])


def _gen_emit_alongside(tag: str, rng: random.Random) -> dict:
    """E2: the agent emits a signal ALONGSIDE completed work (never instead)."""
    persona_key, target, content = rng.choice(TAG_EXAMPLES[tag])
    persona = PERSONAS[persona_key]

    if tag in ("RESULT", "DELEGATE", "APPEAL"):
        # Coder/director: patch (or verdict) + signal together.
        old, new = rng.choice(PATCH_POOL)
        if tag == "RESULT":
            out = _block(old, new) + f"\n\n{_sig1('RESULT', content)}"
            user = f"Fix this line:\n```lua\n{old}\n```"
        elif tag == "DELEGATE":
            out = _sig2("DELEGATE", target, content)
            user = ("The attraction needs telemetry on the scoring path. "
                    "Decompose the work.")
        else:  # APPEAL
            out = _block(old, new) + f"\n\n{_sig2('APPEAL', target, content)}"
            user = f"The Reviewer vetoed your change to `{old}`."
        return _sample(persona["system"], [("user", user), ("assistant", out)])

    if tag == "APPROVE":
        out = _sig0("APPROVE")
        user = "Review the OnLoad implementation that spawns the ball and registers OnStep."
        return _sample(persona["system"], [("user", user), ("assistant", out)])

    if tag == "FLUSH":
        out = _sig0("FLUSH")
        user = "Context changed. Rebuild your answer for the new target file."
        return _sample(persona["system"], [("user", user), ("assistant", out)])

    if tag == "AST_PATCH":
        path, code = target, content
        out = f"[AST_PATCH:{path}]\n```lua\n{code}\n```\n[/AST_PATCH]"
        user = "Fix the mallet scope error in OnUnload."
        return _sample(persona["system"], [("user", user), ("assistant", out)])

    # VETO / OBJECT / REVISE / RECOURSE / MERGE / REJECT — pure verdict signal.
    out = _sig2(tag, target, content)
    user = {
        "VETO": "Review this coder output that uses a phantom API.",
        "OBJECT": "Review this implementation. Raise any concerns.",
        "REVISE": "Review this scoring callback. Request corrections.",
        "RECOURSE": "Lua and PHYS disagree on the impulse API. Escalate.",
        "MERGE": "Rule on the ApplyImpulse arity appeal.",
        "REJECT": "Rule on the ApplyImpulse arity appeal.",
    }.get(tag, "Review the current output.")
    return _sample(persona["system"], [("user", user), ("assistant", out)])


def _gen_response(tag: str, rng: random.Random) -> dict:
    """E3: the agent RECEIVES a signal and reacts correctly."""
    # The received signal is injected into the user turn the way the
    # dispatcher folds it back (type/target/content mirrored).
    if tag == "VETO":
        coder = PERSONAS["LUA"]
        veto = _sig2("VETO", "Lua", "SpawnDynamicBall is a phantom API use SpawnDynamicSphere")
        old, new = ("local hPuck = MidwayPhysics.SpawnDynamicBall(0, 1, 2, 0.5)",
                    "local hPuck = MidwayPhysics.SpawnDynamicSphere(0.0, 1.0, 2.0, 0.5)")
        user = f"## Signal received\n{veto}\n\nFix your code to satisfy the reviewer."
        out = _block(old, new)
        return _sample(coder["system"], [("user", user), ("assistant", out)])

    if tag == "OBJECT":
        coder = PERSONAS["LUA"]
        obj = _sig2("OBJECT", "Lua", "Modifiers are cached in OnLoad but must be read each frame in OnStep")
        old, new = ("local MOD = AttractionConstants.modifiers",
                    "local MOD = AttractionConstants.modifiers or {}")
        user = f"## Signal received\n{obj}\n\nRevise your code."
        out = _block(old, new)
        return _sample(coder["system"], [("user", user), ("assistant", out)])

    if tag == "REVISE":
        coder = PERSONAS["LUA"]
        rev = _sig2("REVISE", "Lua", "Award tickets only after IsSensorTriggered confirms the bell hit")
        old, new = ("AwardTickets(100)",
                    "if MidwayPhysics.IsSensorTriggered(bell_sensor) then MidwayPhysics.AwardTickets(100) end")
        user = f"## Signal received\n{rev}\n\nApply the requested revision."
        out = _block(old, new)
        return _sample(coder["system"], [("user", user), ("assistant", out)])

    if tag == "QUERY":
        doc = PERSONAS["DOC"]
        q = _sig2("QUERY", "DOC", "What is the exact signature and arity of MidwayPhysics.SpawnDynamicCapsule")
        user = f"## Signal received\n{q}"
        out = "MidwayPhysics.SpawnDynamicCapsule(lx, ly, lz, radius, half_height) -> integer handle."
        return _sample(doc["system"], [("user", user), ("assistant", out)])

    if tag == "CONSULT":
        doc = PERSONAS["DOC"]
        q = _sig2("CONSULT", "DOC", "Does IsSensorTriggered(handle) fire on enter only or every frame while overlapping")
        user = f"## Signal received\n{q}"
        out = ("MidwayPhysics.IsSensorTriggered(handle) returns true on the first "
               "frame of overlap and stays true until the bodies separate.")
        return _sample(doc["system"], [("user", user), ("assistant", out)])

    if tag == "APPEAL":
        tribunal = PERSONAS["TRIBUNAL"]
        appeal = _sig2("APPEAL", "Reviewer", "ApplyImpulse has a 3-arg overload in the bridge contract")
        user = f"## Signal received\n{appeal}\n\nRender your binding verdict."
        out = _sig2("MERGE", "Lua", "The bridge contract lists a 3-arg ApplyImpulse overload so the veto is overturned")
        return _sample(tribunal["system"], [("user", user), ("assistant", out)])

    if tag == "AMBIGUITY":
        director = PERSONAS["DIRECTOR"]
        amb = _sig2("AMBIGUITY", "User", "The bell's maximum rise height is not specified")
        user = f"## Signal received\n{amb}\n\nResolve the ambiguity."
        out = ("[DELEGATE:Lua:Implement the bell strike with a default rise height "
               "of 2.0 world units; the scoring bands can be tuned later.]")
        return _sample(director["system"], [("user", user), ("assistant", out)])

    # Fallback: delegate handling via the director.
    director = PERSONAS["DIRECTOR"]
    out = _sig2("DELEGATE", "Lua", "Proceed with the default implementation and flag any open questions")
    return _sample(director["system"], [("user", "## Signal received\n[DELEGATE:PHYS:Model the mallet lever.]"), ("assistant", out)])


def _gen_roundtrip(tag: str, rng: random.Random) -> dict:
    """E4: assistant emits a signal, the parser's capture shape is mirrored
    back in the next user turn, and the assistant completes the deliverable."""
    if tag == "QUERY":
        coder = PERSONAS["LUA"]
        q = _sig2("QUERY", "DOC", "What is the exact signature and arity of MidwayPhysics.SpawnDynamicCapsule")
        answer = ("## Answer from DOC (QUERY)\n"
                  "MidwayPhysics.SpawnDynamicCapsule(lx, ly, lz, radius, half_height) -> integer handle.")
        return _sample(coder["system"], [
            ("user", "Implement the mallet strike. The spawn signature is not in your context."),
            ("assistant", q),
            ("user", answer),
            ("assistant", "local hMallet = MidwayPhysics.SpawnDynamicCapsule(0.0, 1.0, 0.0, 0.2, 0.6)"),
        ])

    if tag == "CONSULT":
        coder = PERSONAS["LUA"]
        c = _sig2("CONSULT", "PHYS", "Should the mallet reset use SetLinearVelocity or MoveKinematic")
        answer = ("## Answer from PHYS (CONSULT)\n"
                  "Use SetLinearVelocity(handle, 0, 0, 0) to zero the velocity, "
                  "then MoveKinematic(handle, startX, startY, startZ, dt) to home it.")
        return _sample(coder["system"], [
            ("user", "Write the mallet reset mechanic."),
            ("assistant", c),
            ("user", answer),
            ("assistant", "MidwayPhysics.SetLinearVelocity(mallet_handle, 0.0, 0.0, 0.0)"),
        ])

    if tag == "VETO":
        coder = PERSONAS["LUA"]
        first, _ = ("local hPuck = MidwayPhysics.SpawnDynamicBall(0, 1, 2, 0.5)",
                    "local hPuck = MidwayPhysics.SpawnDynamicSphere(0.0, 1.0, 2.0, 0.5)")
        appeal = _sig2("APPEAL", "Reviewer", "The bridge contract lists SpawnDynamicSphere with four arguments")
        tribunal = ("## Tribunal verdict (MERGE)\n"
                    "The veto is overturned. SpawnDynamicSphere(lx, ly, lz, radius) is correct.")
        return _sample(coder["system"], [
            ("user", f"Fix this line:\n```lua\n{first}\n```"),
            ("assistant", appeal),
            ("user", tribunal),
            ("assistant", "local hPuck = MidwayPhysics.SpawnDynamicSphere(0.0, 1.0, 2.0, 0.5)"),
        ])

    if tag == "AMBIGUITY":
        director = PERSONAS["DIRECTOR"]
        amb = _sig2("AMBIGUITY", "User", "The striker's governing mode is unspecified")
        clarified = ("## Clarification (TraceGate)\n"
                     "The striker is wager-gated; the streak multiplier applies to "
                     "ticket awards, not to physics.")
        return _sample(director["system"], [
            ("user", "Plan the strongman striker."),
            ("assistant", amb),
            ("user", clarified),
            ("assistant", "[DELEGATE:Lua:Implement the wager-gated striker scoring with the streak multiplier.]"),
        ])

    # Fallback round-trip for DELEGATE.
    director = PERSONAS["DIRECTOR"]
    d = _sig2("DELEGATE", "PHYS", "Model the mallet lever as a Jolt hinge constraint")
    ack = ("## Task spawned\n"
           "PHYS accepted the delegated sub-task and will return its patch.")
    return _sample(director["system"], [
        ("user", "Decompose the strongman lever mechanic."),
        ("assistant", d),
        ("user", ack),
        ("assistant", "[RESULT:Lever delegation issued to PHYS]"),
    ])


# ---------------------------------------------------------------------------
# Top-level generation
# ---------------------------------------------------------------------------

# Tags that can use each scenario family. The blocking signals get E1 + E4,
# the verdict/closure signals get E2 + E3, and both get a mix.
SCENARIO_TAGS = {
    "blocking":  ["QUERY", "CONSULT", "AMBIGUITY", "REQUEST_API"],
    "alongside": ["RESULT", "DELEGATE", "APPEAL", "VETO", "OBJECT", "REVISE",
                  "RECOURSE", "APPROVE", "MERGE", "REJECT", "FLUSH", "AST_PATCH"],
    "response":  ["QUERY", "CONSULT", "VETO", "OBJECT", "REVISE", "APPEAL", "AMBIGUITY", "DELEGATE"],
    "roundtrip": ["QUERY", "CONSULT", "VETO", "AMBIGUITY", "DELEGATE"],
}


def _weighted_tag_choice(rng: random.Random) -> str:
    tags = list(TAG_WEIGHTS.keys())
    weights = [TAG_WEIGHTS[t] for t in tags]
    return rng.choices(tags, weights=weights, k=1)[0]


def generate(num_samples: int, seed: int) -> list:
    rng = random.Random(seed)
    samples = []

    # Scenario mix: blocking emit (40%), alongside emit (25%), response (20%),
    # round-trip (15%). AMBIGUITY appears in both blocking and response/roundtrip.
    while len(samples) < num_samples:
        roll = rng.random()
        tag = _weighted_tag_choice(rng)
        try:
            if roll < 0.40 and tag in SCENARIO_TAGS["blocking"]:
                s = _gen_emit_blocking(tag, rng)
            elif roll < 0.65 and tag in SCENARIO_TAGS["alongside"]:
                s = _gen_emit_alongside(tag, rng)
            elif roll < 0.85 and tag in SCENARIO_TAGS["response"]:
                s = _gen_response(tag, rng)
            elif tag in SCENARIO_TAGS["roundtrip"]:
                s = _gen_roundtrip(tag, rng)
            else:
                # Tag didn't fit the rolled scenario — re-roll.
                continue
            samples.append(s)
        except (KeyError, IndexError):
            # Defensive: skip any tag missing example banks for that scenario.
            continue

    return samples[:num_samples]


def main():
    parser = argparse.ArgumentParser(description="Generate the mesh-signal LoRA dataset")
    parser.add_argument("--num-samples", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--output", default=str(SIGNALS_DATASET))
    args = parser.parse_args()

    samples = generate(args.num_samples, args.seed)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for s in samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    # Distribution report so quality is auditable.
    from collections import Counter
    dist = Counter()
    for s in samples:
        assistant = s["messages"][-1]["content"]
        # Last assistant turn carries the signal(s); count the first tag seen.
        for t in TAG_WEIGHTS:
            if f"[{t}" in assistant or f"[{t}:" in assistant:
                dist[t] += 1
                break
    print(f"Wrote {len(samples)} samples -> {out}")
    print("Tag distribution:")
    for t, n in sorted(dist.items(), key=lambda kv: -kv[1]):
        print(f"  {t:14} {n}")


if __name__ == "__main__":
    main()
