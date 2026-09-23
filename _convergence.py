"""
_convergence.py — deterministic convergence detection for the review-fix loop.

The review-fix loop is bounded (REVIEW_MAX_ITERATIONS) but not *convergence-aware*:
a small model can re-emit the same structural defect every cycle, and the loop
burns every remaining cycle (plus a tribunal) on a file whose deterministic
errors did not move at all. These helpers give the loop a hard, deterministic
"no progress" signal so it can stop early instead of ghost-chasing.

Pure text normalization + list comparison — no model calls, no I/O.
"""

from __future__ import annotations

import re
from typing import List

# luac line/column markers (`:135:` / `:135:7:`) and prose "at line N".
_LINE_MARKER_RE = re.compile(r':\d+(?::\d+)?')
_LINE_WORD_RE = re.compile(r'\bline\s+\d+\b', re.IGNORECASE)


def error_signature(text: str) -> str:
    """Reduce a deterministic-issues block to a stable, comparable signature.

    Strips line/column numbers (which can legitimately shift while the *same*
    structural defect persists) and collapses whitespace. Two cycles that
    produced the same defect at a different line number still hash to the same
    signature.
    """
    if not text:
        return ""
    s = _LINE_MARKER_RE.sub(':L', text)
    s = _LINE_WORD_RE.sub('line L', s)
    s = re.sub(r'\s+', ' ', s).strip().lower()
    return s


def should_trip_on_stale(history: List[str], current: str, threshold: int = 2) -> bool:
    """Return True when `current`'s signature has already been seen enough times
    in `history` that another fix cycle is unlikely to help.

    `history` is the list of prior-cycle signatures (as produced by
    `error_signature`); `current` is this cycle's raw issues text. With the
    default threshold of 2, a signature seen once before trips — i.e. one full
    LLM fix cycle produced zero deterministic progress.
    """
    sig = error_signature(current)
    if not sig:
        return False
    return history.count(sig) + 1 >= threshold
