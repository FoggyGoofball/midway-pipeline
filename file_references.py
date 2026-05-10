"""
File reference cache — parse explicit file references from prompts,
fetch referenced file content, and maintain an LRU cache.

No async/await — purely synchronous file I/O.

Directive A: VRAM Stub Support
When a referenced file exceeds VRAM_STUB_CHAR_THRESHOLD characters, it is
NOT loaded raw into the context window. Instead, a compressed <VRAM_STUB>
pointer is injected. The agent can later PAGE_IN the file if it actually
needs the full content.
"""

from __future__ import annotations
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ── Directive A: VRAM Stub Threshold ──────────────────────────────────────
# Files exceeding this char count will be replaced with <VRAM_STUB> pointers.
VRAM_STUB_CHAR_THRESHOLD: int = 2000


# Global cache for fetched file reference blocks
_REFERENCED_FILES_CACHE: str = ""


def generate_vram_stub(filepath: str, content: str = "", summary: str = "") -> str:
    """Generate a <VRAM_STUB> pointer for a large reference file.

    Instead of injecting the full content into the LLM's context window,
    this produces a compressed pointer that agents can later PAGE_IN if
    they actually need the content.

    Args:
        filepath: Path or identifier for the referenced document.
        content: Optional full text (used to auto-generate summary if not provided).
        summary: Optional manual summary override.

    Returns:
        A stub string like: <VRAM_STUB id="filepath.md" summary="..." />
    """
    if not summary and content:
        first_line = content.split("\n", 1)[0].strip()
        summary = first_line[:100] if first_line else "(empty file)"
    if not summary:
        summary = f"Reference file: {filepath}"
    return f'<VRAM_STUB id="{filepath}" summary="{summary}" />'


def parse_file_references(prompt: str) -> List[Dict[str, str]]:
    """Parse a prompt for explicit file references (reference [...] or edit [...]).

    Supports patterns like:
      - reference src/Engine.h lines 20-30
      - edit src/Engine.h line 42
      - reference src/main.cpp

    Args:
        prompt: User prompt text.

    Returns:
        List of dicts with keys: type ("reference"|"edit"), path, start_line, end_line.
    """
    refs: List[Dict[str, str]] = []
    # Pattern: "reference <path>" or "reference <path> lines <start>-<end>"
    ref_pattern = re.compile(
        r"(?P<type>reference|edit)\s+"
        r"(?P<path>\S+(?:\.\w+)?)"
        r"(?:\s+lines?\s+(?P<start>\d+)(?:[-\s]+(?P<end>\d+))?)?",
        re.IGNORECASE,
    )
    for match in ref_pattern.finditer(prompt):
        refs.append({
            "type": match.group("type").lower(),
            "path": match.group("path"),
            "start_line": match.group("start"),
            "end_line": match.group("end"),
        })
    return refs


def set_referenced_files_cache(refs_block: str) -> None:
    """Set the global referenced files cache.

    Args:
        refs_block: Formatted file content block to cache.
    """
    global _REFERENCED_FILES_CACHE
    _REFERENCED_FILES_CACHE = refs_block


def get_referenced_files_cache() -> str:
    """Get the current referenced files cache.

    Returns:
        Currently cached file content block string.
    """
    return _REFERENCED_FILES_CACHE


def fetch_referenced_files(refs: List[Dict[str, str]]) -> str:
    """Fetch referenced files and format them as read-only blocks.

    Args:
        refs: List of file reference dicts from parse_file_references().

    Returns:
        Formatted markdown string with file contents.
    """
    if not refs:
        return ""
    project_root = Path(__file__).parent.resolve()
    parts: List[str] = ["## Referenced Files\n"]
    for ref in refs:
        filepath = ref["path"]
        full_path = project_root / filepath
        if not full_path.is_file():
            # Try relative to project root parent (midway project root)
            full_path = project_root.parent / filepath
        if not full_path.is_file():
            parts.append(f"*File `{filepath}` not found.*\n")
            continue
        try:
            content = full_path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            parts.append(f"*Error reading `{filepath}`: {e}*\n")
            continue
        lines = content.splitlines()
        start = int(ref["start_line"]) if ref.get("start_line") else 1
        end = int(ref["end_line"]) if ref.get("end_line") else len(lines)
        start = max(1, start)
        end = min(len(lines), end)
        selected = lines[start - 1:end]
        selected_text = "\n".join(selected)
        ext = Path(filepath).suffix.lower()
        lang_map = {
            ".cpp": "cpp", ".h": "cpp", ".hpp": "cpp",
            ".lua": "lua", ".py": "python",
            ".json": "json", ".md": "markdown",
            ".glsl": "glsl", ".vert": "glsl", ".frag": "glsl",
        }
        lang = lang_map.get(ext, "")

        # ── Directive A: VRAM Stub injection ──────────────────────────
        # If the selected content exceeds the threshold, inject a
        # <VRAM_STUB> pointer instead of the full text.
        # The agent can later <invoke_kernel><action>PAGE_IN</action>
        # <target>filepath</target></invoke_kernel> if it needs the content.
        if len(selected_text) > VRAM_STUB_CHAR_THRESHOLD:
            stub = generate_vram_stub(filepath, content=selected_text)
            print(f"  [VRAM Stub] File '{filepath}' ({len(selected_text)} chars) → stub injected")
            parts.append(f"### File: `{filepath}` (lines {start}-{end})\n")
            parts.append(f"{stub}\n")
        else:
            parts.append(f"### File: `{filepath}` (lines {start}-{end})\n")
            parts.append("```" + lang)
            parts.append(selected_text)
            parts.append("```\n")
    return "\n".join(parts)
