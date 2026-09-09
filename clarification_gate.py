#!/usr/bin/env python3
"""
clarification_gate.py — Pre-blueprint clarification gate.

If the feature request is too vague to blueprint safely (e.g. "implement a
strongman high striker" with no mechanics, scoring, or input specified), this
gate asks the model to propose a handful of concrete implementation directions
and then REQUIRES the user to pick one before the blueprint is generated.

Model reuse
-----------
The gate runs on DIRECTOR_MODEL (llama3.1:8b), which is still resident from the
intent-classification call earlier in the same phase.  Because it runs BEFORE
the blueprint phase evicts it and loads the reasoning model, this check adds
latency but does NOT force an extra model reload.

Safety
------
* Non-interactive sessions (no TTY) skip the gate entirely (fail-open) so an
  unattended server can never block forever on input().
* An Ollama failure or an unparseable response also fails open — the pipeline
  proceeds with the original request rather than dying at the gate.
"""

from __future__ import annotations

import re
import sys

CLARIFY_SYSTEM = (
    "You are a game-design clarification assistant for 'Midway to Nowhere', a "
    "Lua-based arcade game built on MidwayPhysics + the Engine economy bridge. "
    "Decide whether a feature request is specific enough to implement directly, "
    "or too vague to blueprint. "
    "A request is SPECIFIC if it states WHAT the feature is, HOW it plays, and "
    "HOW it scores or what systems it touches. "
    "It is VAGUE if it only names the feature without saying what the player "
    "does, what the win/lose condition is, or how scoring works. "
    "If vague, propose 3 concrete, DISTINCT implementation directions. "
    "Respond in EXACTLY this format and nothing else:\n"
    "SPECIFICITY: SPECIFIC\n"
    "(OR)\n"
    "SPECIFICITY: VAGUE\n"
    "1. <short title> — <one-line description>\n"
    "2. <short title> — <one-line description>\n"
    "3. <short title> — <one-line description>\n"
    "Do NOT invent engine architecture — the stack is Lua + MidwayPhysics.* + "
    "Engine.AwardTickets/AwardTokens + AttractionConstants.modifiers."
)

# Numbered option lines: "1. Title — description" (em-dash, en-dash, or hyphen).
_OPTION_RE = re.compile(
    r"^\s*(\d+)[.)]\s*([^—\-\n]{2,80}?)\s*[—\-]\s*(.{2,240}?)\s*$",
    re.MULTILINE,
)


def _parse(out: str):
    specificity = None
    m = re.search(r"SPECIFICITY\s*:\s*(SPECIFIC|VAGUE)", out, re.IGNORECASE)
    if m:
        specificity = m.group(1).upper()
    options = []
    for om in _OPTION_RE.finditer(out):
        options.append((om.group(2).strip(), om.group(3).strip()))
    # If the model listed options it clearly considers the request vague.
    if options and specificity is None:
        specificity = "VAGUE"
    return specificity, options


def run_clarification_gate(ctx, gdd_snippet: str, state_snippet: str) -> bool:
    """Run the pre-blueprint clarification gate.

    Returns True to proceed with the pipeline, False to abort.  When the
    request is vague, stores the user's chosen direction into
    ``ctx.clarified_directive`` and prepends it to ``ctx.gdd_context`` so the
    blueprint, director, and all downstream agents respect it.
    """
    # Never block an unattended session on input().
    try:
        if not sys.stdin.isatty():
            print("  [Clarify Gate] Non-interactive session — skipping clarification.")
            return True
    except Exception:
        return True

    from pipeline import DIRECTOR_MODEL, call_ollama
    from ollama_client import is_fatal_ollama_error

    prompt = (
        f"## Feature Request\n{ctx.user_prompt}\n\n"
        f"## Available Project Context (GDD)\n{gdd_snippet}\n\n"
        f"## Current Project State\n{state_snippet}\n"
    )

    out = call_ollama(
        CLARIFY_SYSTEM,
        prompt,
        "Clarification Gate",
        DIRECTOR_MODEL,
        params={"num_predict": 512},
    )
    if is_fatal_ollama_error(out):
        print("  [Clarify Gate] ⚠ Ollama error — proceeding without clarification.")
        return True

    specificity, options = _parse(out)
    print(f"  [Clarify Gate] Specificity: {specificity or 'UNKNOWN'}")

    if specificity == "SPECIFIC":
        print("  [Clarify Gate] ✓ Request is specific — proceeding to blueprint.")
        return True

    if specificity != "VAGUE":
        print("  [Clarify Gate] Could not determine specificity — proceeding without clarification.")
        return True

    # Vague: present options and REQUIRE a decision.
    print("\n" + "=" * 64)
    print("  [Clarify Gate] ⚠ This feature request is ambiguous.")
    print("=" * 64)
    if options:
        print("  Choose ONE of the following directions (or describe your own):")
        for i, (title, desc) in enumerate(options, 1):
            print(f"    {i}) {title} — {desc}")
        print("    0) I'll describe the direction myself.")
    else:
        print("  The request does not specify enough to generate options automatically.")
        print("  Please describe the intended gameplay/feature in one or two sentences:")

    directive: str = ""
    while not directive:
        choice = input("  Your choice [number or free text]: ").strip()
        if options and choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(options):
                directive = f"{options[idx][0]}: {options[idx][1]}"
            else:
                print("  Invalid option number — try again.")
                continue
        elif choice:
            directive = choice
        else:
            print("  Input required — pick a number or describe the intended direction.")

    ctx.clarified_directive = "## User-Selected Direction (MANDATORY)\n" + directive + "\n"
    # Prepend to gdd_context so blueprint + director + task agents all see it.
    ctx.gdd_context = ctx.clarified_directive + (ctx.gdd_context or "")
    ctx.output_parts.append(ctx.clarified_directive)
    print(f"  [Clarify Gate] ✓ Direction locked: {directive}")
    return True
