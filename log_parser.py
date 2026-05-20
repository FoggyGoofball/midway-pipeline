#!/usr/bin/env python3
"""
log_parser.py — Extensible Compiler Log Pre-Processor

Implements a deterministic Strategy Registry pattern for processing
multiline diagnostic blocks from compiler/linter output. Provides
domain-scoped log truncation to protect VRAM freshness while preserving
critical error context.

Exported:
    LOG_PROCESSOR — global DiagnosticRegistry instance
"""

from __future__ import annotations

import re
from typing import Dict, List, Protocol


class LogTruncationStrategy(Protocol):
    """Protocol for log truncation strategies."""

    def extract_relevant_blocks(self, raw_logs: str) -> str:
        """Extract and return relevant blocks from raw log output."""
        ...


class CppCompilerStrategy:
    """
    Strategy for C++ compiler diagnostic blocks.

    Captures multiline diagnostic blocks anchored to source file paths
    (.cpp/.hpp/.h). Returns the most recent 3 blocks joined by ``\\n\\n``.
    Falls back to the last 1500 characters if no blocks match.
    """

    # Matches a line referencing a source file with optional colon-line
    # suffix, which is the standard GCC/Clang/MSVC diagnostic anchor:
    #   src/main.cpp:10:5: error: ...
    _BLOCK_START = re.compile(
        r'^.*\.(?:cpp|hpp|h)(?::\d+(?::\d+)?)?.+$',
        re.MULTILINE,
    )

    def extract_relevant_blocks(self, raw_logs: str) -> str:
        if not raw_logs or not raw_logs.strip():
            return ""

        starts: List[int] = [
            m.start()
            for m in self._BLOCK_START.finditer(raw_logs)
        ]

        if not starts:
            # No diagnostic blocks found — check for cmake/build-system config
            # errors that are not real compiler diagnostics and should be suppressed.
            _infra_kws = (
                "could not load cache", "no cmake_cache", "run cmake first",
                "cmake error", "cmake warning", "configuring incomplete",
                "error: could not",
            )
            if any(kw in raw_logs.lower() for kw in _infra_kws):
                return ""  # Not a compiler error — suppress entirely
            # Guarantee fresh errors are visible
            return raw_logs[-1500:]

        # Build blocks by slicing from each start position to the next
        blocks: List[str] = []
        for i, pos in enumerate(starts):
            end = starts[i + 1] if i + 1 < len(starts) else len(raw_logs)
            blocks.append(raw_logs[pos:end].strip())

        # Return the most recent 3 blocks
        return "\n\n".join(blocks[-3:])


class LuaLinterStrategy:
    """
    Strategy for Lua linter/runtime diagnostic blocks.

    Captures multiline traces anchoring to ``sol2``, ``lua``, or
    ``stack traceback:`` keywords. Returns the most recent 3 blocks
    joined by ``\\n\\n``. Falls back to the last 1000 characters if
    no blocks match.
    """

    _BLOCK_PATTERN = re.compile(
        r'(?:sol2|lua|stack traceback:).*?(?=\n(?:sol2|lua|stack traceback:)|\Z)',
        re.IGNORECASE | re.DOTALL,
    )

    def extract_relevant_blocks(self, raw_logs: str) -> str:
        if not raw_logs or not raw_logs.strip():
            return ""

        blocks: List[str] = [
            m.group(0).strip()
            for m in self._BLOCK_PATTERN.finditer(raw_logs)
            if m.group(0).strip()
        ]

        if not blocks:
            # No diagnostic blocks found — guarantee fresh errors are visible
            return raw_logs[-1000:]

        # Return the most recent 3 blocks
        return "\n\n".join(blocks[-3:])


class DiagnosticRegistry:
    """
    Registry mapping domain keys to log truncation strategies.

    Pre-initialized with strategies for C++, Physics (PHYS), and Lua
    domains. Exposes ``process_logs(domain_key, raw_logs)`` as the
    single entry point for domain-scoped log processing.
    """

    def __init__(self) -> None:
        self._strategies: Dict[str, LogTruncationStrategy] = {
            "C++": CppCompilerStrategy(),
            "PHYS": CppCompilerStrategy(),   # Physics reuses C++ compiler diagnostics
            "Lua": LuaLinterStrategy(),
        }

    def process_logs(self, domain_key: str, raw_logs: str) -> str:
        """
        Process raw logs through the strategy registered for *domain_key*.

        Args:
            domain_key: One of ``"C++"``, ``"PHYS"``, or ``"Lua"``.
            raw_logs: The raw compiler/linter output string.

        Returns:
            Truncated, domain-relevant log output.
        """
        strategy = self._strategies.get(domain_key)
        if strategy is None:
            # Unknown domain: return raw logs truncated to last 2000 chars
            return raw_logs[-2000:]
        return strategy.extract_relevant_blocks(raw_logs)


# Global singleton instance — importable as:
#   from log_parser import LOG_PROCESSOR
LOG_PROCESSOR = DiagnosticRegistry()
