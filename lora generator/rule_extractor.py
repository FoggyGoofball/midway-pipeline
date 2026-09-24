"""
rule_extractor.py — turn an observed broken->fixed delta into a CANDIDATE
guard rule for the catalog (never auto-committed).

This is the "retroactive rule" half of the hole-closing loop.  When a NEW
failure mode is discovered that no fixer covers, a human (or the arbiter)
fixes it once, and this module proposes a parameterized mutator from that
single example so the hole can be regenerated deterministically as INITIAL
training data — instead of hand-writing every sample.

Output is ALWAYS a proposal flagged for human review.  Nothing is applied to
the catalog, the contract, or the fixer sequence by this module.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import asdict, dataclass
from typing import List, Tuple


@dataclass
class RuleProposal:
    category: str              # detected failure class, or "unknown"
    confidence: float          # 0..1 heuristic confidence
    mutate_template: str       # a representative broken line/pattern
    fix_hint: str              # what the fix did (for human review)
    needs_review: bool = True  # ALWAYS True — proposals are never auto-applied
    raw_diff: str = ""         # full unified diff for the reviewer

    def to_dict(self) -> dict:
        return asdict(self)


# Recognized failure shapes: (category, regex matched against a REMOVED line).
# Order matters: more specific patterns first.
_SHAPES: List[Tuple[str, re.Pattern]] = [
    ("duplicate_underscore", re.compile(r"\blocal\s+_\s*,\s*_\b")),
    ("json_colon", re.compile(r"^\s*[\"'][A-Za-z_][\w]*[\"']\s*:")),
    ("mods_lowercase", re.compile(r"\bmods\.([a-z_][a-z0-9_]*)\b")),
    ("roblox", re.compile(r"\b(Vector3|CFrame|Instance|workspace|game|script)\b")),
    ("phantom_api", re.compile(r"\b(?:MidwayPhysics|MidwayInput|Engine)\.(\w+)")),
    ("bare_call", re.compile(r"^\s*(?:local\s+\w+\s*=\s*)?([A-Z][A-Za-z0-9_]*)\s*\(")),
]


def _line_diff(before: str, after: str) -> Tuple[List[str], List[str]]:
    """Return (removed_lines, added_lines) from a line-level unified diff."""
    before_lines = (before or "").splitlines()
    after_lines = (after or "").splitlines()
    removed: List[str] = []
    added: List[str] = []
    for line in difflib.unified_diff(before_lines, after_lines, lineterm="", n=0):
        if line.startswith(("---", "+++", "@@")):
            continue
        if line.startswith("-"):
            removed.append(line[1:])
        elif line.startswith("+"):
            added.append(line[1:])
    return removed, added


def propose_rule(before: str, after: str) -> RuleProposal:
    """Propose a parameterized guard rule from ONE broken/fixed example.

    A single removed line matching a known shape yields a low-confidence
    proposal of that category; anything else is flagged ``unknown`` with the
    raw diff attached for the reviewer.
    """
    removed, added = _line_diff(before, after)
    raw = "\n".join(difflib.unified_diff(
        (before or "").splitlines(), (after or "").splitlines(), lineterm="", n=1))

    for category, pat in _SHAPES:
        for rl in removed:
            if pat.search(rl):
                return RuleProposal(
                    category=category,
                    confidence=0.5,
                    mutate_template=rl.strip(),
                    fix_hint="; ".join(a.strip() for a in added[:3]),
                    needs_review=True,
                    raw_diff=raw,
                )

    return RuleProposal(
        category="unknown",
        confidence=0.0,
        mutate_template=(removed[0].strip() if removed else ""),
        fix_hint="; ".join(a.strip() for a in added[:3]),
        needs_review=True,
        raw_diff=raw,
    )


def propose_rules(examples: List[Tuple[str, str]]) -> List[RuleProposal]:
    """Propose rules from a list of ``(broken, fixed)`` examples."""
    return [propose_rule(b, a) for b, a in examples]
