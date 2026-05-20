"""
GDD Extractor — structured block extraction from the unabridged 16-chapter Master GDD v19.0.
Parses the Game Design Document using comprehensive regular expression headers
to dynamically locate and extract relevant sections without hardcoded line numbers.

No async/await — purely synchronous regex scanning and text extraction.
"""

import re
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ── VRAM Stub Threshold ─────────────────────────────────────────────────────
# Sections exceeding this char count are replaced with <VRAM_STUB> pointers.
VRAM_STUB_CHAR_THRESHOLD: int = 2000


# ── 16-Chapter Master GDD Section Map ──────────────────────────────────────
# Each entry maps a canonical section key to its chapter number, title label,
# and a list of regex header patterns used to locate the section boundaries
# within the raw GDD markdown document.
GDD_SECTION_MAP: Dict[str, Dict] = {
    "executive": {
        "chapter": 1,
        "label": "1. Executive Summary & Core Architecture",
        "patterns": [
            r"^#\s+Executive\s+Summary",
            r"^#\s+1\.?\s*Executive\s+Summary",
            r"^##\s+Core\s+Architecture",
            r"^##\s+Executive\s+Summary",
        ],
    },
    "technical": {
        "chapter": 2,
        "label": "2. Technical Backbone: Custom C++ Engine & Optimization",
        "patterns": [
            r"^#\s+Technical\s+Backbone",
            r"^#\s+2\.?\s*Technical\s+Backbone",
            r"^##\s+Custom\s+C\+\+\s+Engine",
            r"^##\s+Engine\s+Architecture",
        ],
    },
    "economy": {
        "chapter": 3,
        "label": "3. The Dual-Currency Economy & Streak Protocol",
        "patterns": [
            r"^#\s+Dual.Currency\s+Economy",
            r"^#\s+3\.?\s*Dual.Currency",
            r"^##\s+Economy",
            r"^##\s+Streak\s+Protocol",
            r"^##\s+Currency",
        ],
    },
    "modifier": {
        "chapter": 4,
        "label": "4. Global Identity Stats & Emergent Mechanics",
        "patterns": [
            r"^#\s+Global\s+Identity\s+Stats",
            r"^#\s+4\.?\s*Global\s+Identity",
            r"^##\s+Modifier",
            r"^##\s+Identity\s+Stats",
            r"^##\s+Emergent\s+Mechanics",
        ],
    },
    "loot": {
        "chapter": 5,
        "label": "5. The Augmentation & Bribery Loop (Loot Paradigm)",
        "patterns": [
            r"^#\s+Augmentation",
            r"^#\s+5\.?\s*Augmentation",
            r"^##\s+Loot",
            r"^##\s+Bribery",
            r"^##\s+Augmentation\s+Loop",
        ],
    },
    "pacing": {
        "chapter": 6,
        "label": "6. Pacing, Onboarding, and Meta-Progression",
        "patterns": [
            r"^#\s+Pacing",
            r"^#\s+6\.?\s*Pacing",
            r"^##\s+Onboarding",
            r"^##\s+Meta.Progression",
        ],
    },
    "concessions": {
        "chapter": 7,
        "label": "7. Concessions & Utility Stalls (Food & Alignment)",
        "patterns": [
            r"^#\s+Concessions",
            r"^#\s+7\.?\s*Concessions",
            r"^##\s+Utility\s+Stalls",
            r"^##\s+Food",
            r"^##\s+Alignment",
        ],
    },
    "coin cascade": {
        "chapter": 8,
        "label": "8. The Coin Cascade",
        "patterns": [
            r"^#\s+Coin\s+Cascade",
            r"^##\s+Coin\s+Cascade",
            r"^###\s+Coin\s+Cascade",
        ],
    },
    "plinko": {
        "chapter": 8,
        "label": "8. Purgatorial Plinko",
        "patterns": [
            r"^#\s+Plinko",
            r"^##\s+Plinko",
            r"^###\s+Plinko",
            r"^###\s+Purgatorial\s+Plinko",
        ],
    },
    "claw": {
        "chapter": 8,
        "label": "8. The Claw of Condemnation",
        "patterns": [
            r"^#\s+Claw\s+of\s+Condemnation",
            r"^##\s+Claw\s+of\s+Condemnation",
            r"^###\s+Claw",
        ],
    },
    "slingshot": {
        "chapter": 8,
        "label": "8. The Slingshot Array",
        "patterns": [
            r"^#\s+Slingshot",
            r"^##\s+Slingshot",
            r"^###\s+Slingshot\s+Array",
        ],
    },
    "ring toss": {
        "chapter": 8,
        "label": "8. The Ring Toss",
        "patterns": [
            r"^#\s+Ring\s+Toss",
            r"^##\s+Ring\s+Toss",
            r"^###\s+Ring\s+Toss",
        ],
    },
    "crumbling": {
        "chapter": 8,
        "label": "8. The Crumbling Façade",
        "patterns": [
            r"^#\s+Crumbling\s+Fa[çc]ade",
            r"^##\s+Crumbling\s+Fa[çc]ade",
            r"^###\s+Crumbling\s+Fa[çc]ade",
        ],
    },
    "bumper cars": {
        "chapter": 8,
        "label": "8. Bumper Cars",
        "patterns": [
            r"^#\s+Bumper\s+Cars",
            r"^##\s+Bumper\s+Cars",
            r"^###\s+Bumper\s+Cars",
        ],
    },
    "roulette": {
        "chapter": 8,
        "label": "8. Roulette Chamber",
        "patterns": [
            r"^#\s+Roulette",
            r"^##\s+Roulette",
            r"^###\s+Roulette\s+Chamber",
        ],
    },
    "globe of death": {
        "chapter": 8,
        "label": "8. Globe of Death",
        "patterns": [
            r"^#\s+Globe\s+of\s+Death",
            r"^##\s+Globe\s+of\s+Death",
            r"^###\s+Globe\s+of\s+Death",
        ],
    },
    "future": {
        "chapter": 9,
        "label": "9. Midway Expansion Catalog (Future Attractions)",
        "patterns": [
            r"^#\s+Midway\s+Expansion",
            r"^#\s+9\.?\s*Midway\s+Expansion",
            r"^#\s+Expansion\s+Catalog",
            r"^##\s+Future\s+Attractions",
            r"^##\s+Expansion",
        ],
    },
    "boss": {
        "chapter": 10,
        "label": "10. Identity Loadouts & Climactic Encounters (Final Bosses)",
        "patterns": [
            r"^#\s+Identity\s+Loadouts",
            r"^#\s+10\.?\s*Identity\s+Loadouts",
            r"^#\s+Climactic\s+Encounters",
            r"^##\s+Boss",
            r"^##\s+Final\s+Boss",
            r"^##\s+Encounter",
        ],
    },
    "endgame": {
        "chapter": 11,
        "label": "11. Terminal Wagers & The True Endgame",
        "patterns": [
            r"^#\s+Terminal\s+Wagers",
            r"^#\s+11\.?\s*Terminal\s+Wagers",
            r"^#\s+True\s+Endgame",
            r"^##\s+Endgame",
            r"^##\s+Terminal",
        ],
    },
    "narrative": {
        "chapter": 12,
        "label": "12. Narrative Delivery, Environmental Shifts & The Collapse Sequence",
        "patterns": [
            r"^#\s+Narrative\s+Delivery",
            r"^#\s+12\.?\s*Narrative",
            r"^##\s+Environmental\s+Shifts",
            r"^##\s+Collapse\s+Sequence",
            r"^##\s+Narrative",
        ],
    },
    "origins": {
        "chapter": 13,
        "label": "13. Narrative Origins: The Genesis of the House Edge & The Grand Pier Disaster",
        "patterns": [
            r"^#\s+Narrative\s+Origins",
            r"^#\s+13\.?\s*Narrative\s+Origins",
            r"^##\s+Genesis\s+of\s+the\s+House\s+Edge",
            r"^##\s+Grand\s+Pier\s+Disaster",
            r"^##\s+Origins",
            r"^###\s+House\s+Edge",
        ],
    },
    "roadmap": {
        "chapter": 14,
        "label": "14. Solo Developer Roadmap: Exhaustive Task List",
        "patterns": [
            r"^#\s+Solo\s+Developer\s+Roadmap",
            r"^#\s+14\.?\s*Solo\s+Developer",
            r"^##\s+Task\s+List",
            r"^##\s+Roadmap",
        ],
    },
    "ai_handoff": {
        "chapter": 15,
        "label": "15. AI Handoff: Ready-to-Execute Agent Prompts",
        "patterns": [
            r"^#\s+AI\s+Handoff",
            r"^#\s+15\.?\s*AI\s+Handoff",
            r"^##\s+Agent\s+Prompts",
            r"^##\s+Handoff",
        ],
    },
    "appendix_prize": {
        "chapter": 16,
        "label": "16. Appendix A: The Complete Prize Lexicon",
        "patterns": [
            r"^#\s+Appendix\s+A",
            r"^#\s+16\.?\s*Appendix",
            r"^##\s+Prize\s+Lexicon",
            r"^##\s+Appendix",
        ],
    },
}


# ── Keyword-to-Section Routing ──────────────────────────────────────────────
# Maps user-facing keywords to canonical GDD_SECTION_MAP keys for librarian routing.
KEYWORD_TO_SECTION: Dict[str, str] = {
    # Chapter 1: Executive Summary
    "executive": "executive", "summary": "executive", "overview": "executive",
    "architecture": "executive",
    # Chapter 2: Technical Backbone
    "engine": "technical", "c++": "technical", "technical": "technical",
    "physics": "technical", "rendering": "technical", "shader": "technical",
    "lua": "technical", "sol2": "technical", "sdl2": "technical",
    "opengl": "technical", "jolt": "technical", "optimization": "technical",
    # Chapter 3: Economy
    "economy": "economy", "token": "economy", "ticket": "economy",
    "currency": "economy", "streak": "economy", "dual": "economy",
    "let it ride": "economy",
    # Chapter 4: Modifiers
    "modifier": "modifier", "stats": "modifier", "karma": "modifier",
    "identity": "modifier", "stat": "modifier", "mass": "modifier",
    "volume": "modifier", "friction": "modifier", "luck": "modifier",
    "persuasion": "modifier", "heat": "modifier", "nerve": "modifier",
    "sleight": "modifier",
    # Chapter 5: Loot
    "loot": "loot", "prize": "loot", "augment": "loot", "bribery": "loot",
    "augmentation": "loot", "reward": "loot",
    # Chapter 6: Pacing
    "pacing": "pacing", "onboarding": "pacing", "meta": "pacing",
    "progression": "pacing", "tutorial": "pacing",
    # Chapter 7: Concessions
    "concession": "concessions", "food": "concessions", "stall": "concessions",
    "alignment": "concessions", "utility": "concessions",
    # Chapter 8: Attractions
    "plinko": "plinko", "coin cascade": "coin cascade", "coin": "coin cascade",
    "cascade": "coin cascade", "crumbling": "crumbling", "facade": "crumbling",
    "façade": "crumbling", "claw": "claw", "slingshot": "slingshot",
    "ring toss": "ring toss", "ring": "ring toss", "bumper": "bumper cars",
    "bumper cars": "bumper cars", "roulette": "roulette", "globe": "globe of death",
    "globe of death": "globe of death", "attraction": "coin cascade",
    "attractions": "coin cascade",
    # Chapter 9: Future/Expansion
    "future": "future", "expansion": "future", "catalog": "future",
    # Chapter 10: Bosses
    "boss": "boss", "bosses": "boss", "encounter": "boss",
    "loadout": "boss", "final boss": "boss",
    # Chapter 11: Endgame
    "endgame": "endgame", "terminal": "endgame", "wager": "endgame",
    # Chapter 12: Narrative Delivery
    "narrative": "narrative", "delivery": "narrative",
    "environmental": "narrative", "collapse": "narrative",
    # Chapter 13: Origins
    "origins": "origins", "genesis": "origins", "house edge": "origins",
    "pier": "origins", "disaster": "origins",
    # Chapter 14: Roadmap
    "roadmap": "roadmap", "task list": "roadmap", "developer": "roadmap",
    "todo": "roadmap",
    # Chapter 15: AI Handoff
    "handoff": "ai_handoff", "ai": "ai_handoff", "agent prompt": "ai_handoff",
    # Chapter 16: Appendix
    "appendix": "appendix_prize", "prize": "appendix_prize",
    "lexicon": "appendix_prize", "prize lexicon": "appendix_prize",
}


def _resolve_gdd_path() -> Optional[Path]:
    """Resolve the Master GDD file path using runtime cartridge context or sibling directory."""
    local_dir = Path(__file__).resolve().parent
    project_root = Path(os.environ.get("MIDWAY_PROJECT_ROOT", local_dir.with_name("midway")))
    docs_dir = project_root / "docs"

    # Check mounted cartridge context first
    try:
        from pipeline import _CTX
        if _CTX and getattr(_CTX, 'project_root', None):
            docs_dir = _CTX.project_root / "docs"
    except ImportError:
        pass

    if docs_dir.is_dir():
        for p in docs_dir.glob("*.md"):
            if "gdd" in p.name.lower():
                return p
    return None


def _find_section_boundaries(
    content: str, patterns: List[str]
) -> Optional[Tuple[int, int]]:
    """Find the start and end byte offsets of a section using header regex patterns.

    Args:
        content: Full GDD markdown text.
        patterns: List of regex patterns matching the section header.

    Returns:
        Tuple of (start_offset, end_offset) or None if not found.
    """
    combined_pattern = "|".join(f"(?:{p})" for p in patterns)
    header_re = re.compile(combined_pattern, re.MULTILINE)

    match = header_re.search(content)
    if not match:
        return None

    start = match.start()

    # Find the next header at the same or higher level (same level: #, lower level: ##, ###)
    # Match the next header that is level 1-4
    next_header = re.search(
        r"^(?:#\s+|#{1,4}\s+)(?!$)",
        content[match.end():],
        re.MULTILINE,
    )
    if next_header:
        end = match.end() + next_header.start()
    else:
        end = len(content)

    return (start, end)


def extract_gdd_sections(prompt: str) -> str:
    """Extract relevant GDD sections based on prompt keywords using regex header matching.

    Args:
        prompt: User's natural language query or task specification.

    Returns:
        Formatted markdown string of matched GDD excerpts, or empty string.
    """
    gdd_path = _resolve_gdd_path()
    if not gdd_path or not gdd_path.is_file():
        return ""

    prompt_lower = prompt.lower()
    selected_keys = set()

    # Map prompt keywords to canonical section keys
    for keyword, section_key in KEYWORD_TO_SECTION.items():
        if keyword in prompt_lower:
            selected_keys.add(section_key)

    # Always include Executive Summary and Technical Backbone for baseline context
    selected_keys.add("executive")
    selected_keys.add("technical")

    try:
        content = gdd_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""

    excerpts = []
    for key in sorted(selected_keys, key=lambda k: GDD_SECTION_MAP[k]["chapter"]):
        section = GDD_SECTION_MAP.get(key)
        if not section:
            continue

        boundaries = _find_section_boundaries(content, section["patterns"])
        if not boundaries:
            continue

        start, end = boundaries
        section_text = content[start:end].strip()
        if not section_text:
            continue

        excerpts.append(f"### {section['label']}\n{section_text}")

    if not excerpts:
        return ""

    result = "## Relevant GDD Excerpts\n" + "\n\n".join(excerpts)
    print(f"  [GDD Extractor] Extracted {len(selected_keys)} section(s) from GDD via regex header matching")
    return result


def generate_vram_stub(filepath: str, content: str = "", summary: str = "") -> str:
    """Generate a <VRAM_STUB> pointer for a large GDD section.

    Args:
        filepath: Path or identifier for the referenced section.
        content: Optional full text for auto-summary.
        summary: Optional manual summary override.

    Returns:
        A stub string like: <VRAM_STUB id="..." summary="..." />
    """
    if not summary and content:
        first_line = content.split("\n", 1)[0].strip()
        summary = first_line[:100] if first_line else "(empty section)"
    if not summary:
        summary = f"GDD section: {filepath}"
    return f'<VRAM_STUB id="{filepath}" summary="{summary}" />'
