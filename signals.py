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
    "QUERY": r"\[\s*\*?\*?\s*QUERY\s*\*?\*?\s*:\s*([^\]]+)\s*:\s*([^\]]+)\s*\]",
    "DELEGATE": r"\[\s*\*?\*?\s*DELEGATE\s*\*?\*?\s*:\s*([^\]]+)\s*:\s*([^\]]+)\s*\]",
    "VETO": r"\[\s*\*?\*?\s*VETO\s*\*?\*?\s*:\s*([^\]]+)\s*:\s*([^\]]+)\s*\]",
    "OBJECT": r"\[\s*\*?\*?\s*OBJECT\s*\*?\*?\s*:\s*([^\]]+)\s*:\s*([^\]]+)\s*\]",
    "RECOURSE": r"\[\s*\*?\*?\s*RECOURSE\s*\*?\*?\s*:\s*([^\]]+)\s*:\s*([^\]]+)\s*\]",
    "CONSULT": r"\[\s*\*?\*?\s*CONSULT\s*\*?\*?\s*:\s*([^\]]+)\s*:\s*([^\]]+)\s*\]",
    "APPROVE": r"\[\s*\*?\*?\s*APPROVE\s*\*?\*?\s*\]",
    "RESULT": r"\[\s*\*?\*?\s*RESULT\s*\*?\*?\s*:\s*(.*?)\s*\]",
    "REVISE": r"\[\s*\*?\*?\s*REVISE\s*\*?\*?\s*:\s*([^\]]+)\s*:\s*([^\]]+)\s*\]",
    "FLUSH": r"\[\s*\*?\*?\s*FLUSH\s*\*?\*?\s*\]",
    "APPEAL": r"\[\s*\*?\*?\s*APPEAL\s*\*?\*?\s*:\s*([^\]]+)\s*:\s*([^\]]+)\s*\]",
    "MERGE": r"\[\s*\*?\*?\s*MERGE\s*\*?\*?\s*:\s*([^\]]+)\s*:\s*([^\]]+)\s*\]",
    "REJECT": r"\[\s*\*?\*?\s*REJECT\s*\*?\*?\s*:\s*([^\]]+)\s*:\s*([^\]]+)\s*\]",
    "REQUEST_API": r"\[REQUEST_API:\s*(.*?)\s*\|\s*(https?://.*?)\s*\]",
    "FETCH": r"\[\s*\*?\*?\s*FETCH\s*\*?\*?\s*:\s*([^\]]+)\s*\]",
    "READ_OFFLOADED": r"\[\s*\*?\*?\s*READ_OFFLOADED\s*\*?\*?\s*:\s*([^\]]+)\s*\]",
    "EXTRACT_SKELETON": r"\[\s*\*?\*?\s*EXTRACT_SKELETON\s*\*?\*?\s*:\s*([^\]]+)\s*\]",
    "MATH_EVAL": r"\[\s*\*?\*?\s*MATH_EVAL\s*\*?\*?\s*:\s*(.*?)\]",

    # ── Phase III: LangGraph AST Patch Signals ─────────────────────────────
    # Captures discrete AST modification chunks in agent output streams.
    # Format: [AST_PATCH:src/Engine.cpp] ... code ... [/AST_PATCH]
    "AST_PATCH": r"\[\s*\*?\*?\s*AST_PATCH\s*\*?\*?\s*:\s*([^\]]+)\s*\]\s*\n?```\w*\n(.*?)\n?```\s*\n?\[/\s*AST_PATCH\s*\]",

}

# ── Phase III: AST Patch Extraction Pattern (LangGraph Alignment) ──────
# Optimized for capturing discrete AST modification chunks within agent output.
# Matches file path + code replacement blocks with optional diff-style line markers.
AST_PATCH_BLOCK_PATTERN: str = (
    r"\[\s*\*?\*?\s*AST_PATCH\s*\*?\*?\s*:\s*([^\]]+)\s*\]"  # [AST_PATCH:path/to/file]
    r"\s*\n?(?:```\w*\n)?"                                     # optional opening code fence
    r"(.*?)"                                                    # the patch content (lazy)
    r"(?:\n?```\s*\n?)?"                                       # optional closing code fence
    r"\n?\[/\s*AST_PATCH\s*\]"                                  # [/AST_PATCH]
)




# Double-check pattern — captures a structured agent self-review section
# containing the three marked sections, allowing bullet items across lines.
# Highly permissive of markdown variations: missing hyphens, extra hashes,
# bolded headers, varied capitalization, asterisk vs dash bullets.
DOUBLE_CHECK_PATTERN: str = (
    r"#{2,4}\s*[Dd]ouble[-\s]*[Cc]heck\s*\n"
    r"(?:\*\*)?[Oo]riginal[-\s]*[Pp]rompt:?\*{0,2}\s*(.*?)\n"
    r"(?:\*\*)?(?:[Ww]hat\s+[Tt]his\s+[Aa]ddresses"
    r"|[Mm]y\s+[Oo]utput\s+[Aa]ddresses"
    r"|[Aa]ddresses):?\*{0,2}\s*"
    r"(.*?)"  # Addresses content — any text up to next section header
    r"\n?"
    r"(?:\*\*)?(?:[Ww]hat\s+[Rr]emains\s+[Uu]nresolved"
    r"|[Uu]nresolved\s+[Ii]tems"
    r"|[Uu]nresolved"
    r"|[Rr]emaining\s+[Ii]ssues):?\*{0,2}\s*"
    r"(.*?)\s*$"  # Unresolved content — any remaining text
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
        for match in re.finditer(pattern, text, re.DOTALL | re.IGNORECASE | re.MULTILINE):
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

    Enforces strict word boundaries and checks for explicit verdict tags to avoid false positives.
    """
    # Prefer explicit verdict tags first (brackets optional; tolerate lowercase/spacing)
    fail_match = re.search(
        r"^\s*(?:\[\s*)?VERDICT\s*:\s*FAIL\s*(?:\s*\])?\s*$",
        review_text,
        re.IGNORECASE | re.MULTILINE,
    )
    pass_match = re.search(
        r"^\s*(?:\[\s*)?VERDICT\s*:\s*PASS\s*(?:\s*\])?\s*$",
        review_text,
        re.IGNORECASE | re.MULTILINE,
    )

    if fail_match:
        return "FAIL"
    if pass_match:
        return "PASS"

    # Fallback: only match FAIL/PASS when they appear as a standalone verdict line
    # (e.g. "FAIL" or "**FAIL**" or "Result: FAIL"), NOT mid-sentence words like
    # "could fail to compile" or "unit test failed".  The line must end with the
    # word (optional punctuation/markdown bold markers), which prevents prose hits.
    if re.search(
        r"(?:^|[\s:*_])FAIL(?:ED|URE)?(?:[\s*_.,]|$)",
        review_text,
        re.IGNORECASE | re.MULTILINE,
    ):
        # Extra guard: ignore lines that contain hedging verbs before the word
        _fail_lines = [
            ln for ln in review_text.splitlines()
            if re.search(r"(?:^|[\s:*_])FAIL(?:ED|URE)?(?:[\s*_.,]|$)", ln, re.IGNORECASE)
            and not re.search(
                r"\b(?:could|would|might|may|should|will|can|if|when|unless|avoid|prevent|cause)\b",
                ln, re.IGNORECASE,
            )
        ]
        if _fail_lines:
            return "FAIL"
    if re.search(
        r"(?:^|[\s:*_])PASS(?:ED)?(?:[\s*_.,]|$)",
        review_text,
        re.IGNORECASE | re.MULTILINE,
    ):
        # Same guard as FAIL: ignore hedged lines so "would pass" does not trigger
        _pass_lines = [
            ln for ln in review_text.splitlines()
            if re.search(r"(?:^|[\s:*_])PASS(?:ED)?(?:[\s*_.,]|$)", ln, re.IGNORECASE)
            and not re.search(
                r"\b(?:could|would|might|may|should|will|can|if|when|unless|avoid|prevent|cause)\b",
                ln, re.IGNORECASE,
            )
        ]
        if _pass_lines:
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
