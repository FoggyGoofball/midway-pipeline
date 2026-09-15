#!/usr/bin/env python3
"""
term_color.py — ANSI colour helpers for terminal telemetry.

The Midway console log mixes a lot of moving parts; colour makes the
telemetry line scannable at a glance:

    persona (label)   -> BLUE
    model id          -> MAGENTA
    TTFT              -> CYAN
    TPS / speed       -> YELLOW (green when healthy)

Colours are enabled only when the ORIGINAL stdout is a real console
(``sys.__stdout__.isatty()``) and are disabled by ``NO_COLOR`` /
``MIDWAY_NO_COLOR``.  All helpers return the input unchanged when colour
is disabled, so callers never need to branch.
"""

from __future__ import annotations

import os
import re
import sys

_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"

_CODES = {
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "magenta": "\033[35m",
    "cyan": "\033[36m",
    "white": "\033[37m",
    "gray": "\033[90m",
    "bright_green": "\033[92m",
    "bright_yellow": "\033[93m",
    "bright_blue": "\033[94m",
    "bright_magenta": "\033[95m",
    "bright_cyan": "\033[96m",
}

# Regex for stripping ANSI escape sequences (used by the web log tee).
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from *text*."""
    if not text:
        return text
    return ANSI_RE.sub("", text)


def _enable_vt() -> None:
    """Enable Windows console VT processing so ANSI sequences render."""
    if os.name == "nt":
        try:
            os.system("")
        except Exception:
            pass


def color_enabled() -> bool:
    """True when ANSI colours should be emitted."""
    if os.environ.get("NO_COLOR") or os.environ.get("MIDWAY_NO_COLOR"):
        return False
    try:
        # Use __stdout__ (the real console) rather than sys.stdout, which the
        # stream server replaces with a log tee.
        if hasattr(sys, "__stdout__") and sys.__stdout__ is not None:
            return bool(sys.__stdout__.isatty())
    except Exception:
        return False
    return False


_ENABLED: bool | None = None


def _is_enabled() -> bool:
    global _ENABLED
    if _ENABLED is None:
        _ENABLED = color_enabled()
        if _ENABLED:
            _enable_vt()
    return _ENABLED


def paint(text: object, color: str = "white") -> str:
    """Wrap *text* in the ANSI colour *color* (no-op when disabled)."""
    text = str(text)
    if not _is_enabled():
        return text
    code = _CODES.get(color, "")
    if not code:
        return text
    return f"{code}{text}{_RESET}"


def bold(text: object) -> str:
    text = str(text)
    if not _is_enabled():
        return text
    return f"{_BOLD}{text}{_RESET}"


def dim(text: object) -> str:
    text = str(text)
    if not _is_enabled():
        return text
    return f"{_DIM}{text}{_RESET}"
