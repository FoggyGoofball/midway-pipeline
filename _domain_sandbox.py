"""
_domain_sandbox.py — Directive A: Absolute File-Level Sandboxing (Domain Enforcement)
=====================================================================================
Provides file-extension sandboxing validation for all agent outputs.

Key functions:
  - get_allowed_extensions(domain_key) -> set of allowed extensions
  - validate_domain_file_write(domain_key, output_text) -> (is_valid, violations)
  - reject_cross_domain_output(domain_key, output_text) -> (is_clean, safe_output)

Usage:
    from _domain_sandbox import reject_cross_domain_output
    is_clean, safe_output = reject_cross_domain_output("Lua", agent_output, "Lua Scripter")
"""

from __future__ import annotations
import re
from pathlib import Path
from typing import Optional

__all__ = [
    "DOMAIN_ALLOWED_EXTENSIONS",
    "get_allowed_extensions",
    "extract_file_paths_from_output",
    "validate_domain_file_write",
    "build_sandbox_constraint",
    "reject_cross_domain_output",
]


# ── Canonical File Extension Mapping ──────────────────────────────────────
# Maps domain keys to the set of file extensions they are ALLOWED to modify.
# Read-only domains (DOC, CONF, TRIBUNAL, LIBRARIAN, REVIEWER) have empty sets
# — any SEARCH/REPLACE block from them is a violation.

DOMAIN_ALLOWED_EXTENSIONS: dict = {
    "C++": {".cpp", ".h", ".hpp", ".c", ".hxx", ".cxx"},
    "PHYS": {".cpp", ".h", ".hpp", ".c", ".hxx", ".cxx"},
    "Lua": {".lua"},
    "SHADER": {".glsl", ".vert", ".frag", ".geom"},
    "DOC": set(),       # read-only — no file writes allowed
    "CONF": set(),      # read-only — no file writes allowed
    "TRIBUNAL": set(),  # read-only — no file writes allowed
    "LIBRARIAN": set(), # read-only — no file writes allowed
    "REVIEWER": set(),  # read-only — no file writes allowed
    "DIRECTOR": set(),  # read-only — no file writes allowed
    "SYNTAX_GATE": set(),  # read-only — no file writes allowed
    "INTENT_CLASSIFIER": set(),  # read-only — no file writes allowed
    "DIAGNOSTIC": set(),  # read-only — no file writes allowed
}


def get_allowed_extensions(domain_key: str) -> set:
    """Return the set of allowed file extensions for a domain key."""
    return DOMAIN_ALLOWED_EXTENSIONS.get(domain_key, set())


# ── Regex Patterns for Detecting File Paths ──────────────────────────────

_FILE_PATH_HEADING = re.compile(
    r'###\s*File:\s*([^\s\n]+)', re.IGNORECASE
)

_FILE_PATH_BACKTICK_HEADING = re.compile(
    r'###\s*`([^`]+\.\w+)`', re.IGNORECASE
)

_FILE_PATH_BEFORE_SEARCH = re.compile(
    r'(?:^|\n)\s*([\w\/.-]+\.(?:cpp|h|hpp|lua|py|c|hxx|cxx|glsl|vert|frag|geom))'
    r'\s*\n<<<<<<< SEARCH',
    re.MULTILINE,
)


def extract_file_paths_from_output(output_text: str) -> list[str]:
    """
    Extract all unique file paths referenced in an agent's output.
    Checks markdown headings (### File: path), ### `backtick` headings,
    and paths appearing just before <<<<<<< SEARCH blocks.
    """
    paths: set[str] = set()

    # 1. ### File: path headings
    for match in _FILE_PATH_HEADING.finditer(output_text):
        path = match.group(1).strip()
        if path:
            paths.add(path)

    # 2. ### `filename.ext` headings
    for match in _FILE_PATH_BACKTICK_HEADING.finditer(output_text):
        path = match.group(1).strip()
        if path:
            paths.add(path)

    # 3. Paths immediately before <<<<<<< SEARCH
    for match in _FILE_PATH_BEFORE_SEARCH.finditer(output_text):
        path = match.group(1).strip()
        if path:
            paths.add(path)

    return list(paths)


def validate_domain_file_write(
    domain_key: str,
    output_text: str,
    known_whitelist: Optional[set[str]] = None,
) -> tuple[bool, list[str]]:
    """
    Validate that an agent's output does NOT attempt to modify files outside
    its domain's allowed extensions.

    Args:
        domain_key: Canonical domain key (e.g. "C++", "Lua", "PHYS").
        output_text: The raw text output from the agent.
        known_whitelist: Optional pre-computed set of allowed paths.

    Returns:
        Tuple of (is_valid: bool, violations: list[str])
    """
    allowed_exts = get_allowed_extensions(domain_key)

    # Read-only domain — any file write attempt is a violation
    if not allowed_exts:
        referenced = extract_file_paths_from_output(output_text)
        if referenced:
            return False, [
                f"[SANDBOX] {domain_key} is READ-ONLY but referenced files: {referenced}"
            ]
        return True, []

    # If no SEARCH/REPLACE blocks detected, no code modification is happening
    search_replace_count = output_text.count("<<<<<<< SEARCH")
    if search_replace_count == 0:
        return True, []  # No code modifications detected

    # Check each extracted file path against the domain's allowed extensions
    file_paths = extract_file_paths_from_output(output_text)
    violations: list[str] = []

    for fpath in file_paths:
        ext = Path(fpath).suffix.lower()
        if ext not in allowed_exts:
            violations.append(
                f"[SANDBOX] {domain_key} attempted to modify '{fpath}' "
                f"(extension '{ext}' not in allowed set: {allowed_exts})"
            )

    return len(violations) == 0, violations


def build_sandbox_constraint(allowed_exts: set) -> str:
    """Build a CRITICAL FILE RESTRICTION system-prompt fragment.

    Args:
        allowed_exts: Set of allowed file extensions for a domain.

    Returns:
        Formatted constraint string to append to an agent's system prompt.
    """
    if not allowed_exts:
        return ""
    if ".lua" in allowed_exts and len(allowed_exts) == 1:
        return (
            "\n\n---\n"
            "CRITICAL FILE RESTRICTION:\n"
            "You are physically restricted to modifying ONLY [.lua] files. "
            "Any SEARCH/REPLACE blocks targeting .cpp, .h, .hpp, .py, .md, .json, or any "
            "other extension will trigger a fatal system error and your output will be discarded."
        )
    ext_str = str(list(allowed_exts))
    return (
        "\n\n---\n"
        "CRITICAL FILE RESTRICTION:\n"
        "You are physically restricted to modifying "
        f"{ext_str} files. "
        "Any SEARCH/REPLACE blocks targeting files with extensions outside this set "
        "will trigger a fatal system error and your output will be discarded."
    )


def reject_cross_domain_output(
    domain_key: str,
    output_text: str,
    persona_name: str = "",
) -> tuple[bool, str]:
    """
    Hard reject: if an agent output contains cross-domain file writes,
    truncate the output to a safe error message and log the violation.

    Args:
        domain_key: Canonical domain key.
        output_text: The agent's raw output text.
        persona_name: Human-readable agent name for logging.

    Returns:
        Tuple of (is_clean: bool, safe_output: str)
            is_clean — True if no cross-domain violations found.
            safe_output — Original output if clean, or truncated safe message.
    """
    is_valid, violations = validate_domain_file_write(domain_key, output_text)
    if is_valid:
        return True, output_text

    # Log violations for diagnostics
    name = persona_name or domain_key
    for v in violations:
        print(f"  [SANDBOX] DOMAIN VIOLATION: {v}")

    # Truncate — return a safe stub that won't poison the next cycle
    ext_list = list(get_allowed_extensions(domain_key))
    violations_str = "\n".join(f"- {v}" for v in violations)
    safe_stub = (
        f"## DOMAIN SANDBOX VIOLATION --- Output Rejected\n\n"
        f"The {name} agent attempted to modify files outside its domain scope.\n"
        f"**Violations detected:**\n"
        f"{violations_str}\n\n"
        f"This output has been discarded. Please restrict modifications to "
        f"files with extensions: {ext_list}.\n"
    )
    return False, safe_stub
