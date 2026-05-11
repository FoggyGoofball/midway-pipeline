import re
import os
from pathlib import Path
from typing import Optional

# ── Directive A: VRAM Stub Threshold ────────────────────────────────────────
# Files/sections exceeding this char count will be replaced with <VRAM_STUB>
# instead of being injected as raw text into the LLM context window.
VRAM_STUB_CHAR_THRESHOLD: int = 2000


def generate_vram_stub(filepath: str, content: str = "", summary: str = "") -> str:
    """Generate a <VRAM_STUB> pointer for a large reference file or section.

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


def extract_project_context(prompt_text: str) -> str:
    """
    Dynamically scans the GDD for headers, scores them against user prompt tokens,
    and surgically extracts the relevant sections without requiring a manifest.
    """
    # 1. Resolve the local directory of this script first
    local_dir = Path(__file__).resolve().parent
    
    # 2. Enforce sibling-aware fallback matching the main orchestration layer
    project_root = Path(os.environ.get("MIDWAY_PROJECT_ROOT", local_dir.with_name("midway")))
    docs_dir = project_root / "docs"
    
    gdd_path: Optional[Path] = None

    # Dynamically check if mounted cartridge defines a project docs directory or custom GDD location
    try:
        from pipeline import _CTX
        if _CTX and getattr(_CTX, 'project_root', None):
            project_root = _CTX.project_root
            docs_dir = project_root / "docs"
    except ImportError:
        pass

    if docs_dir.is_dir():
        # Scan dynamically for any Markdown file containing 'gdd' case-insensitively
        for p in docs_dir.glob("*.md"):
            if "gdd" in p.name.lower():
                gdd_path = p
                break
    
    if not gdd_path or not gdd_path.is_file():
        return f"[System] Warning: GDD document not found in {docs_dir}"

    with open(gdd_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Dynamically find all headers (H1 to H4) and map their byte offsets
    header_matches = list(re.finditer(r"^(#{1,4})\s+(.+)$", content, re.MULTILINE))
    
    if not header_matches:
        return content[:4000] # Fallback if no headers exist

    # Explicitly parse the standard format separating positive intent from [block] negative constraints
    parts = re.split(r'\[block\]', prompt_text, maxsplit=1, flags=re.IGNORECASE)
    positive_intent = parts[0].strip()
    blocked_intent = parts[1].strip() if len(parts) > 1 else ""

    # Tokenize user prompt for keyword intersection using an expanded set of stopwords
    stop_words = {
        "the", "a", "an", "is", "in", "to", "and", "for", "of", "with", "on", "it", "as", 
        "build", "create", "make", "refer", "project", "list", "game", "basic", "sure", 
        "adhere", "information", "using", "want", "you", "system", "module", "from", "via"
    }
    
    # Dynamically incorporate procedural stopwords from mounted cartridge if available via global context
    try:
        from pipeline import _CTX
        if _CTX and getattr(_CTX, 'mounted_cartridge', None) and _CTX.mounted_cartridge.procedural_stopwords:
            stop_words.update([w.lower() for w in _CTX.mounted_cartridge.procedural_stopwords])
    except ImportError:
        pass
    
    prompt_words = set(re.findall(r'\b[a-zA-Z]{3,}\b', positive_intent.lower())) - stop_words
    
    blocked_words = set()
    if blocked_intent:
        blocked_words = set(re.findall(r'\b[a-zA-Z]{3,}\b', blocked_intent.lower())) - stop_words

    selected_sections = []

    for i, match in enumerate(header_matches):
        header_text = match.group(2)
        header_words = set(re.findall(r'\b[a-zA-Z]{3,}\b', header_text.lower())) - stop_words

        # Deterministic Guardrail: If the header strongly intersects with explicitly blocked concepts, actively omit it
        if blocked_words and blocked_words.intersection(header_words):
            print(f"  [Context Extractor] Actively omitting blocked section: '{header_text}'")
            continue

        # Score: Semantic intersection of positive tokens only
        if prompt_words.intersection(header_words):
            start_pos = match.start()
            end_pos = header_matches[i+1].start() if i + 1 < len(header_matches) else len(content)
            selected_sections.append(content[start_pos:end_pos].strip())

    # If no direct matches, return the first semantic section (Overview) to provide baseline context
    if not selected_sections:
        start_pos = header_matches[0].start()
        end_pos = header_matches[1].start() if len(header_matches) > 1 else len(content)
        return content[start_pos:end_pos].strip()

    # ── Directive A: VRAM Stub Injection ──────────────────────────────────
    # If any matched section exceeds VRAM_STUB_CHAR_THRESHOLD, replace it
    # with a <VRAM_STUB> pointer so agents can PAGE_IN on demand. This
    # prevents context window saturation from large GDD sections.
    stubbed_sections = []
    for section in selected_sections:
        if len(section) > VRAM_STUB_CHAR_THRESHOLD:
            # Derive a stub id from the first header line in the section
            header_match = re.match(r"^(#{1,4}\s+.+)$", section, re.MULTILINE)
            header_line = header_match.group(1).strip() if header_match else "(untitled)"
            stub_id = f"gdd_{hash(header_line) & 0xFFFF:04x}.md"
            summary = header_line[:100]
            stubbed_sections.append(
                f"\n{generate_vram_stub(stub_id, content=section[:500], summary=summary)}\n"
            )
            print(f"  [VRAM Stub] Section '{header_line[:60]}...' ({len(section)} chars) → stub injected")
        else:
            stubbed_sections.append(section)

    # Join matched sections, capped to prevent LLM context window blowout
    return "\n\n...\n\n".join(stubbed_sections)[:8000]
