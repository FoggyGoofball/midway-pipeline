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


def extract_project_context(prompt_text: str,
                            scope_mode: str = "GENERAL",
                            attraction_name: str = "") -> str:
    """
    Extracts GDD sections relevant to the user prompt.

    For NEW_ATTRACTION / MODIFY_ATTRACTION scope the extraction is fully
    deterministic and bypasses the keyword pass entirely:
      - NEW_ATTRACTION  : named attraction section (if present) + Economy + Lifecycle
      - MODIFY_ATTRACTION: same but target section is authoritative

    For GENERAL scope the original keyword pass runs, then a phi3.5 semantic
    pruning gate discards irrelevant candidates when the list is large.
    """
    # 1. Resolve the local directory of this script first
    local_dir = Path(__file__).resolve().parent
    
    # 2. Default docs directory is the sister repo (midway/docs).
    #    Override with MIDWAY_PROJECT_ROOT env var if the repos are arranged differently.
    project_root = Path(os.environ.get("MIDWAY_PROJECT_ROOT", str(local_dir.with_name("midway"))))
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
        # Accept any .md whose name contains 'gdd', or the canonical GDD filename pattern
        for p in docs_dir.glob("*.md"):
            name_lower = p.name.lower()
            if "gdd" in name_lower or name_lower.startswith("midway_to_nowhere"):
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

    # ── Scope-aware deterministic fast path ──────────────────────────────
    # For NEW_ATTRACTION and MODIFY_ATTRACTION we know exactly what to pull:
    #   1. The attraction's own GDD section (by name), if it exists.
    #   2. The Economy/Dual-Currency cross-cutting section.
    #   3. The Lifecycle/Script-Lifecycle cross-cutting section.
    # The keyword pass is skipped entirely — it cannot distinguish skeeball
    # from Plinko because words like "player", "balls", "scoring" appear
    # everywhere in a game-design document.
    _CROSS_CUTTING_PATTERNS = [
        re.compile(r'dual.?currency|economy|streak\s+protocol', re.IGNORECASE),
        re.compile(r'script\s+lifecycle|lifecycle|onload|onloadstatic|onstep|onunload', re.IGNORECASE),
    ]

    def _deterministic_extract(target_name: str) -> str | None:
        """Return assembled GDD text for a named attraction + cross-cutting sections.
        Returns None only if no headers were matched at all (signals caller to fall through)."""
        _chosen: list[int] = []

        # 1. Named attraction section — two-pass search:
        #    Pass A: case-insensitive substring match on markdown header text.
        #    Pass B: if no header matched, find the bold/inline mention in the
        #            body (e.g. "**Skeeball:**") and walk back to the nearest
        #            header so we still return a meaningful section boundary.
        if target_name:
            _name_lower = target_name.lower()
            _header_hit = False
            for _i, _m in enumerate(header_matches):
                if _name_lower in _m.group(2).lower():
                    _chosen.append(_i)
                    _header_hit = True
                    print(f"  [Context Extractor] Deterministic attraction match (header): '{_m.group(2)}'")
                    break

            if not _header_hit:
                # Pass B — look for **Name:** or *Name* or bare Name as a
                # prominent in-body label anywhere in the document.
                _inline_pat = re.compile(
                    r'\*{1,2}' + re.escape(target_name) + r'[^*\n]{0,10}\*{1,2}|'
                    r'^#+\s*' + re.escape(target_name),
                    re.IGNORECASE | re.MULTILINE
                )
                _inline_m = _inline_pat.search(content)
                if _inline_m:
                    # Find the nearest header that *precedes* this match.
                    _match_pos = _inline_m.start()
                    _best_i = None
                    for _i, _m in enumerate(header_matches):
                        if _m.start() <= _match_pos:
                            _best_i = _i
                        else:
                            break

                    if _best_i is not None:
                        # Rather than using the whole parent section (which may
                        # contain dozens of other attractions as list items),
                        # extract only the paragraph/list-item that contains the
                        # inline match.  We do this by walking the raw content
                        # from the match position backward to the nearest blank
                        # line (paragraph start) and forward to the next blank
                        # line (paragraph end).
                        _para_start = content.rfind('\n\n', 0, _match_pos)
                        _para_start = (_para_start + 2) if _para_start != -1 else 0
                        _para_end = content.find('\n\n', _match_pos)
                        _para_end = _para_end if _para_end != -1 else len(content)
                        _inline_para = content[_para_start:_para_end].strip()

                        # If the paragraph is very short (just a title line),
                        # fall back to a fixed window around the match.
                        if len(_inline_para) < 80:
                            _inline_para = content[
                                max(0, _match_pos - 100):
                                min(len(content), _match_pos + 1500)
                            ].strip()

                        print(f"  [Context Extractor] Deterministic attraction match (inline bold, paragraph): "
                              f"'{header_matches[_best_i].group(2)}' ← '{_inline_m.group(0)[:40]}'")
                        _chosen.append(-1)  # sentinel: use raw paragraph, not full section
                        _inline_raw_slice = _inline_para
                    else:
                        # No preceding header — return the raw surrounding text directly.
                        _start_c = max(0, _inline_m.start() - 200)
                        _end_c = min(len(content), _inline_m.end() + 1500)
                        print(f"  [Context Extractor] Deterministic attraction match (raw inline): "
                              f"no preceding header; returning surrounding paragraph")
                        _chosen.append(-1)  # sentinel handled below
                        _inline_raw_slice = content[_start_c:_end_c].strip()

        # 2. Cross-cutting sections — scan every header for each pattern.
        for _pat in _CROSS_CUTTING_PATTERNS:
            for _i, _m in enumerate(header_matches):
                if _pat.search(_m.group(2)):
                    if _i not in _chosen:
                        _chosen.append(_i)
                        print(f"  [Context Extractor] Deterministic cross-cutting: '{_m.group(2)}'")
                    break  # one match per pattern is enough

        if not _chosen:
            return None

        _chosen = sorted(set(_chosen))
        _parts: list[str] = []
        for _i in _chosen:
            # Sentinel -1 means we found an inline bold match with no preceding header
            if _i == -1:
                _parts.append(_inline_raw_slice)
                continue
            _start = header_matches[_i].start()
            _end = header_matches[_i + 1].start() if _i + 1 < len(header_matches) else len(content)
            _sec = content[_start:_end].strip()
            # Still apply VRAM stub for large sections
            if len(_sec) > VRAM_STUB_CHAR_THRESHOLD:
                _hm = re.match(r"^(#{1,4}\s+.+)$", _sec, re.MULTILINE)
                _hl = _hm.group(1).strip() if _hm else "(untitled)"
                print(f"  [VRAM Stub] Section '{_hl[:60]}...' ({len(_sec)} chars) → stub injected")
                _parts.append(f"\n{generate_vram_stub(f'gdd_{hash(_hl)&0xFFFF:04x}.md', content=_sec[:500], summary=_hl[:100])}\n")
            else:
                _parts.append(_sec)
        return "\n\n...\n\n".join(_parts)[:8000]

    if scope_mode in ("NEW_ATTRACTION", "MODIFY_ATTRACTION"):
        _result = _deterministic_extract(attraction_name)
        if _result is not None:
            return _result
        # attraction_name was empty or nothing matched — fall through to keyword pass
        print(f"  [Context Extractor] Deterministic pass found nothing for '{attraction_name}' — falling back to keyword pass")


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

    # ── Brand-word suppression ────────────────────────────────────────────
    # Words derived from the document title (e.g. "midway", "nowhere") appear
    # in almost every section header and inflate match scores for unrelated
    # sections.  Collect them from the first H1 header and treat single-word
    # header matches that fire *only* on brand words as low-quality.
    _brand_words: set = set()
    if header_matches:
        _h1 = header_matches[0].group(2)
        _brand_words = set(re.findall(r'\b[a-zA-Z]{3,}\b', _h1.lower())) - stop_words
    # Also treat generic structural tokens as brand-like for scoring purposes
    _brand_words.update({"gdd", "todo", "doc", "docs", "spec", "notes"})
    # Also derive high-frequency domain vocabulary by scanning all H1–H2 header
    # words: if a word appears in many top-level headers it is structural rather
    # than specific and should not trigger body-content matches.
    _header_word_freq: dict = {}
    for _m in header_matches:
        if len(_m.group(1)) <= 2:  # H1 or H2 only
            for _w in re.findall(r'\b[a-zA-Z]{4,}\b', _m.group(2).lower()):
                _header_word_freq[_w] = _header_word_freq.get(_w, 0) + 1
    _hdr_brand_threshold = max(2, len(header_matches) // 10)
    for _w, _c in _header_word_freq.items():
        if _c >= _hdr_brand_threshold:
            _brand_words.add(_w)

    # ── Structural-noise suppression ──────────────────────────────────────
    # These section types are navigation/metadata aids and never contain
    # useful mechanics detail.  Skip them regardless of keyword matches.
    _STRUCTURAL_HEADER_RE = re.compile(
        r'\b(table\s+of\s+contents|roadmap|task\s+list|changelog|appendix|index|glossary)\b',
        re.IGNORECASE,
    )

    # ── Pre-build per-section body lookup for body-content scoring ────────
    # Sections whose headers don't mention skeeball/etc. may still contain
    # the target noun in their body text — we want those too.
    # We strip leading list/ToC lines (lines starting with whitespace+dash/number
    # and containing a section reference) so a ToC listing "skeeball" does not
    # cause every section in the document to match via body-content scoring.
    _toc_line_re = re.compile(r'^\s*[-\d*]+[.)]\s+.{0,120}$', re.MULTILINE)

    _section_bodies: list = []  # (index, header_text, body_text)
    for i, m in enumerate(header_matches):
        end = header_matches[i + 1].start() if i + 1 < len(header_matches) else len(content)
        raw_body = content[m.start():end]
        # Remove lines that look like ToC / ordered-list entries so skeeball
        # mentioned in a contents listing doesn't count as a body match.
        stripped_body = _toc_line_re.sub("", raw_body)
        _section_bodies.append((i, m.group(2), stripped_body))

    selected_indices: list = []

    for i, match in enumerate(header_matches):
        header_text = match.group(2)
        header_words = set(re.findall(r'\b[a-zA-Z]{3,}\b', header_text.lower())) - stop_words

        # Skip structural-noise sections (ToC, roadmap, task lists, etc.)
        if _STRUCTURAL_HEADER_RE.search(header_text):
            continue

        # Deterministic Guardrail: actively omit explicitly blocked sections
        if blocked_words and blocked_words.intersection(header_words):
            print(f"  [Context Extractor] Actively omitting blocked section: '{header_text}'")
            continue

        header_match_tokens = prompt_words.intersection(header_words)
        if not header_match_tokens:
            continue

        # ── Quality gate: suppress brand-only matches ─────────────────────
        # If the entire intersection consists solely of brand/title words,
        # require that the section body also contains at least one non-brand
        # prompt word before including it.  This prevents "9. Midway Expansion
        # Catalog" from matching just because the user typed "midway".
        non_brand_match = header_match_tokens - _brand_words
        if not non_brand_match:
            start_pos = match.start()
            end_pos = header_matches[i + 1].start() if i + 1 < len(header_matches) else len(content)
            body_text = content[start_pos:end_pos].lower()
            body_words = set(re.findall(r'\b[a-zA-Z]{3,}\b', body_text)) - stop_words - _brand_words
            if not (prompt_words - _brand_words).intersection(body_words):
                print(f"  [Context Extractor] Skipping brand-only match: '{header_text}'")
                continue

        selected_indices.append(i)

    # ── Body-content scoring: include sections whose body contains prompt nouns
    # even if the header didn't match (catches new attractions not yet in headers)
    # Only fire on words that are not also high-frequency document terms — a word
    # that appears in the body of more than half the sections is too generic to
    # use as a discriminating signal (e.g. "attraction" in a game-design doc).
    _non_brand_prompt = prompt_words - _brand_words
    if _non_brand_prompt:
        # Build per-word section-body frequency so we can prune high-freq terms
        _body_freq: dict = {}
        for _i, _h, _b in _section_bodies:
            _bw = set(re.findall(r'\b[a-zA-Z]{3,}\b', _b.lower())) - stop_words
            for _w in _bw:
                _body_freq[_w] = _body_freq.get(_w, 0) + 1
        _total_sections = max(len(_section_bodies), 1)
        _high_freq_threshold = max(3, _total_sections // 6)
        _discriminating = {w for w in _non_brand_prompt if _body_freq.get(w, 0) <= _high_freq_threshold}

        if _discriminating:
            already = set(selected_indices)
            for i, _hdr, _body in _section_bodies:
                if i in already:
                    continue
                # Never body-match structural sections
                if _STRUCTURAL_HEADER_RE.search(_hdr):
                    continue
                _body_words = set(re.findall(r'\b[a-zA-Z]{3,}\b', _body.lower())) - stop_words
                if _discriminating.intersection(_body_words):
                    # Check blocked guard before adding
                    _hw = set(re.findall(r'\b[a-zA-Z]{3,}\b', _hdr.lower())) - stop_words
                    if blocked_words and blocked_words.intersection(_hw):
                        continue
                    selected_indices.append(i)
                    print(f"  [Context Extractor] Body-match: '{_hdr[:60]}'")

    selected_indices = sorted(set(selected_indices))

    # ── Parent-section expansion ──────────────────────────────────────────
    # When a matched section is a very short intro header (< 500 chars),
    # automatically include its immediate child sections so that mechanic
    # detail nested underneath isn't orphaned.
    expanded_indices = list(selected_indices)
    for i in selected_indices:
        start_pos = header_matches[i].start()
        end_pos = header_matches[i + 1].start() if i + 1 < len(header_matches) else len(content)
        section_len = end_pos - start_pos
        if section_len < 500:
            parent_level = len(header_matches[i].group(1))  # number of '#'
            # Add direct children (one level deeper) until a same-or-shallower header
            j = i + 1
            while j < len(header_matches):
                child_level = len(header_matches[j].group(1))
                if child_level <= parent_level:
                    break
                if child_level == parent_level + 1:
                    expanded_indices.append(j)
                j += 1

    selected_indices = sorted(set(expanded_indices))

    # ── phi3.5 Semantic Pruning Gate (GENERAL scope only) ─────────────────
    # When the keyword pass produced more than 4 candidates the list is
    # likely polluted with tangential sections.  Call phi3.5 with only the
    # candidate *titles* (not their bodies — keeping the payload tiny) and
    # ask it to return the subset that is genuinely needed.
    _SEMANTIC_PRUNE_THRESHOLD = 4
    if len(selected_indices) > _SEMANTIC_PRUNE_THRESHOLD:
        try:
            from ollama_client import call_ollama as _call_ollama
            from ollama_client import PRE_SUMMARIZER_MODEL as _SUMM_MODEL
            _candidate_titles = [
                f"{idx}: {header_matches[idx].group(2)}"
                for idx in selected_indices
            ]
            _titles_block = "\n".join(_candidate_titles)
            _prune_system = (
                "You are a relevance filter for a game-design document. "
                "You will receive a user request and a numbered list of GDD section titles. "
                "Return ONLY the numbers of sections that are directly necessary to implement "
                "the request — omit any section about a different named attraction or mini-game, "
                "narrative lore, onboarding, or meta-progression systems that are not yet implemented. "
                "ALWAYS keep sections about Economy, Dual-Currency, Streak Protocol, or Script Lifecycle "
                "because every attraction depends on them. "
                "Respond with a comma-separated list of numbers only, nothing else."
            )
            _prune_user = (
                f"## User Request\n{positive_intent}\n\n"
                f"## Candidate Section Numbers and Titles\n{_titles_block}"
            )
            _prune_result = _call_ollama(_prune_system, _prune_user,
                                         "Semantic Section Pruner", _SUMM_MODEL,
                                         skip_pre_summarizer=True)
            if _prune_result:
                _kept_nums = [int(x.strip()) for x in re.findall(r'\d+', _prune_result)
                              if x.strip().isdigit() and int(x.strip()) in selected_indices]
                if _kept_nums:
                    _pruned = sorted(set(_kept_nums))
                    _dropped = [header_matches[i].group(2) for i in selected_indices if i not in _pruned]
                    for _d in _dropped:
                        print(f"  [Semantic Pruner] Discarded: '{_d[:60]}'")
                    selected_indices = _pruned
                    print(f"  [Semantic Pruner] {len(selected_indices)} section(s) kept after semantic pruning")
        except Exception as _e:
            print(f"  [Semantic Pruner] Skipped (error: {_e})")

    # Build section texts from resolved indices
    selected_sections = []
    for i in selected_indices:
        start_pos = header_matches[i].start()
        end_pos = header_matches[i + 1].start() if i + 1 < len(header_matches) else len(content)
        selected_sections.append(content[start_pos:end_pos].strip())

    # If no sections matched at all, return the first section as baseline context
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
