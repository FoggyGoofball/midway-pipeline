"""
Signal parsing — extract mesh communication signals, double-check sections,
and verdicts from agent output text. Used throughout the pipeline to parse
inter-agent messaging defined in the engine-lua bridge contract.

No async/await — purely synchronous regex parsing.
"""

from __future__ import annotations
import re
from typing import Dict, List, Optional, Any
from models import SignalType, MeshSignal


# ── Signal Patterns ──────────────────────────────────────────────────────────
# Mesh inter-agent signal patterns — regexes that match strict markdown
# bracket-tag syntax in LLM agent output. The regex captures the three
# sections: target agent, content payload, and optional source/hash tag.

SIGNAL_PATTERNS: Dict[str, str] = {
    "QUERY": r"\[QUERY:([^\]]+):([^\]]+)\]",
    "DELEGATE": r"\[DELEGATE:([^\]]+):([^\]]+)\]",
    "RESULT": r"\[RESULT:([^\]]+)\]",
    "APPROVE": r"\[APPROVE\]",
    "REVISE": r"\[REVISE:([^\]]+):([^\]]+)\]",
    "VETO": r"\[VETO:([^\]]+):([^\]]+)\]",
    "OBJECT": r"\[OBJECT:([^\]]+):([^\]]+)\]",
    "RECOURSE": r"\[RECOURSE:([^\]]+):([^\]]+)\]",
    "CONSULT": r"\[CONSULT:([^\]]+):([^\]]+)\]",
    "FETCH": r"\[FETCH:([^\]]+)\]",
    "READ_OFFLOADED": r"\[READ_OFFLOADED:([^\]]+)\]",
}

# Double-check pattern — captures a structured agent self-review section
# containing the three marked sections, allowing bullet items across lines.
# NOTE: Multiple heading variants are supported for backward compatibility
# with existing characterization tests (e.g. "Original prompt:" vs "Original Prompt:").
DOUBLE_CHECK_PATTERN: str = (
    r"## Double-Check\s*\n"
    r"\*\*[Oo]riginal [Pp]rompt:\*\*\s*(.*?)\n"
    r"\*\*(?:What This Addresses|My output addresses):\*\*\s*((?:(?:\*(?:.|\n)*?)+?|.+?))\n"
    r"\*\*(?:What Remains Unresolved|Unresolved items):\*\*\s*((?:(?:\*(?:.|\n)*?)+?|.+?))(?=\n##|\Z)"
)


def extract_signals(text: str) -> List[Dict[str, Any]]:
    """Extract all mesh communication signals from agent output.

    Args:
        text: Raw agent output text to scan.

    Returns:
        List of signal dicts with keys: type, match, target, content.
    """
    signals: List[Dict[str, Any]] = []
    for signal_type, pattern in SIGNAL_PATTERNS.items():
        for match in re.finditer(pattern, text, re.DOTALL):
            groups = match.groups()
            signal: Dict[str, Any] = {"type": signal_type, "match": match.group(0)}
            if signal_type == "APPROVE":
                signal["target"] = None
                signal["content"] = None
            elif signal_type in ("RESULT", "FETCH", "READ_OFFLOADED"):
                signal["target"] = None
                signal["content"] = groups[0].strip()
            else:
                signal["target"] = groups[0].strip()
                signal["content"] = groups[1].strip()
            signals.append(signal)
    return signals


def extract_double_check(text: str) -> Optional[Dict[str, str]]:
    """Extract the double-check section from agent output.

    Uses a multi-line pattern that captures bullet-point content across lines
    rather than stopping at the first newline.

    Args:
        text: Raw agent output text.

    Returns:
        Dict with keys 'original_prompt', 'addresses', 'unresolved'
        or None if no double-check section found.
    """
    match = re.search(DOUBLE_CHECK_PATTERN, text, re.DOTALL)
    if match:
        return {
            "original_prompt": match.group(1).strip(),
            "addresses": match.group(2).strip(),
            "unresolved": match.group(3).strip(),
        }
    return None


def get_verdict(review_text: str) -> str:
    """Extract the verdict from a review output.

    Returns 'PASS', 'FAIL', or 'UNKNOWN'.
    FAIL is checked first (higher priority) to avoid false PASS on negative commentary.

    Args:
        review_text: Review agent's output text.

    Returns:
        'PASS', 'FAIL', or 'UNKNOWN'.
    """
    # Check FAIL first — bold or bare
    if re.search(r"\*\*FAIL\*\*", review_text):
        return "FAIL"
    if re.search(r"(?m)^FAIL$", review_text):
        return "FAIL"
    # Then check PASS
    if re.search(r"\*\*PASS\*\*", review_text):
        return "PASS"
    if re.search(r"(?m)^PASS$", review_text):
        return "PASS"
    return "UNKNOWN"


def parse_signal(text: str, source: str = None) -> List[MeshSignal]:
    """Parse a text for mesh signals, returning MeshSignal objects.

    Args:
        text: Raw text to parse for signals.
        source: Source agent identifier.

    Returns:
        List of MeshSignal objects.
    """
    raw = extract_signals(text)
    result: List[MeshSignal] = []
    for s in raw:
        try:
            st = SignalType(s["type"])
        except ValueError:
            continue
        ms = MeshSignal(
            type=st,
            target=s.get("target") or "",
            content=s.get("content") or "",
            source=source or "",
        )
        result.append(ms)
    return result
