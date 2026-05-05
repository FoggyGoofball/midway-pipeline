"""
GDD (Game Design Document) section extractor — maps keywords to GDD sections
and extracts relevant sections from the master GDD file based on prompt analysis.

No async/await — purely synchronous file I/O and regex parsing.
"""

from __future__ import annotations
import re
from pathlib import Path
from typing import Dict, List, Optional


# ── Constants ────────────────────────────────────────────────────────────────
GDD_PATH = Path(__file__).parent.parent / "GDD" / "Midway_to_Nowhere_Master_GDD_v19.md"


# ── GDD Section Mapping ──────────────────────────────────────────────────────
# Maps high-level keywords to GDD section titles for focused extraction.
GDD_SECTION_MAP: Dict[str, List[str]] = {
    "plinko": ["Plinko", "Plinko Attraction"],
    "crumbling facade": ["Crumbling Facade", "CrumblingFacade"],
    "coin cascade": ["Coin Cascade", "CoinCascade"],
    "economy": ["Economy System", "Economy", "Currency"],
    "booth": ["Booth System", "Booth Lifecycle"],
    "ticket": ["Ticket System", "Ticket Booth"],
    "modifier": ["Modifier System", "Modifier Effects"],
    "park": ["Park Layout", "Midway Layout"],
    "visual": ["Visual Style", "Rendering"],
    "physics": ["Physics", "Collision"],
    "attraction": ["Attraction System", "Attraction Manager"],
    "audio": ["Audio", "Sound Design"],
    "narrative": ["Narrative", "Story", "Dialog"],
}

# Reverse map: keyword -> canonical section names
KEYWORD_TO_SECTION: Dict[str, List[str]] = {}
for keyword, sections in GDD_SECTION_MAP.items():
    for section in sections:
        KEYWORD_TO_SECTION[section] = [keyword]


def _find_section_in_gdd(content: str, section_title: str) -> Optional[str]:
    """Find a section in GDD content by title and return its text.

    Args:
        content: Full GDD markdown content.
        section_title: Section title to search for (case-insensitive).

    Returns:
        Section text content, or None if not found.
    """
    lines = content.splitlines()
    in_section = False
    section_lines: List[str] = []
    section_depth = 0

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#"):
            # Check if this is the target section header
            if section_title.lower() in stripped.lower():
                in_section = True
                section_depth = len(stripped.split("#")[0]) + 1
                section_lines.append(stripped)
                continue

            if in_section:
                # Check if we've hit a header at same or higher level
                current_depth = len(stripped.split("#")[0]) + 1
                if current_depth <= section_depth:
                    break

        if in_section:
            section_lines.append(line)

    if section_lines:
        return "\n".join(section_lines)
    return None


def extract_gdd_sections(prompt: str) -> str:
    """Extract only relevant GDD sections based on the prompt.

    Args:
        prompt: User prompt to analyze for relevant GDD sections.

    Returns:
        Formatted markdown string with relevant GDD sections.
    """
    prompt_lower = prompt.lower()

    # Find matching sections
    matched_sections: List[str] = []
    for keyword, sections in GDD_SECTION_MAP.items():
        if keyword.lower() in prompt_lower:
            matched_sections.extend(sections)

    if not matched_sections:
        return ""

    # Read GDD file
    if not GDD_PATH.is_file():
        return "*GDD file not found.*\n"

    try:
        content = GDD_PATH.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"*Error reading GDD: {e}*\n"

    parts: List[str] = ["## Relevant GDD Sections\n"]
    for section in matched_sections:
        section_content = _find_section_in_gdd(content, section)
        if section_content:
            parts.append(f"### GDD: {section}\n")
            parts.append(section_content)
            parts.append("\n---\n")

    if len(parts) == 1:
        return ""

    return "\n".join(parts)


def search_memory() -> str:
    """Search tool for the Librarian agent — reads the Memory Ledger Table of Contents.

    Returns:
        Formatted ledger TOC string.
    """
    from ledger import ledger_toc
    return ledger_toc()
