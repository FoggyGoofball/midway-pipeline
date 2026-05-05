"""
Memory ledger management — fingerprint normalization, TOC building, header
enforcement, ledger entry collection, and disk-write interceptor for agent
memory persistence.

No async/await — purely synchronous file I/O and regex processing.
"""

from __future__ import annotations
import json
import re
import tempfile
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from models import SignalType, MeshSignal


# ── Constants ────────────────────────────────────────────────────────────────
# Import domain registry constants needed for ledger operations.
# Lazy-imported to avoid circular imports at module level.
PROJECT_ROOT = Path(__file__).parent.resolve()
MEMORY_DIR = PROJECT_ROOT / "docs" / "memory"
_MAX_OUTPUT_CHARS = 4000
SESSION_TIMELINE_PATH = PROJECT_ROOT / "docs" / "memory" / "session_timeline.md"

BOILERPLATE_TITLES: set = {"Table of Contents", "Memory Bank", "Persistent memory bank"}

LEDGER_HEADER_PATTERN = re.compile(r'^#{2,4}\s*\[.*?\]', re.MULTILINE)


# ── LRU Doc Cache ───────────────────────────────────────────────────────────
_DOC_CACHE: Dict[str, Tuple[str, float]] = {}
_DOC_CACHE_TTL: int = 300
_DOC_CACHE_MAX: int = 8


def _get_doc_cached(rel_path: str) -> str:
    """Read a doc file with LRU caching. Returns content or empty string."""
    from time import time
    now = time()

    # Check cache
    if rel_path in _DOC_CACHE:
        content, ts = _DOC_CACHE[rel_path]
        if now - ts < _DOC_CACHE_TTL:
            return content

    # Load fresh
    full_path = PROJECT_ROOT / rel_path
    if not full_path.is_file():
        return ""
    try:
        content = full_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""

    # Evict oldest if at capacity
    if len(_DOC_CACHE) >= _DOC_CACHE_MAX:
        oldest_key = min(_DOC_CACHE.keys(), key=lambda k: _DOC_CACHE[k][1])
        del _DOC_CACHE[oldest_key]

    _DOC_CACHE[rel_path] = (content, now)
    return content


# ── Session Timeline ────────────────────────────────────────────────────────

def _normalize_fix_fingerprint(fix_input: str) -> str:
    """Compute a deterministic fingerprint of a 'fix' request BEFORE context truncation.

    This fingerprint is computed BEFORE _block_aware_collapse truncation happens
    inside call_ollama(). It normalizes out:
    - Cycle numbers (e.g., "(Cycle 2)" -> "(Cycle N)")
    - Collapsed/truncated code markers
    - Excessive blank lines
    """
    normalized = fix_input

    # 1. Normalize cycle numbers
    normalized = re.sub(r'\bCycle \d+\b', 'Cycle N', normalized)

    # 2. Strip collapsed/truncated markers
    normalized = re.sub(
        r'\[\.\.\.\s*(?:body|function|content|code)?\s*(?:collapsed|truncated)\s*\.\.\.\]',
        '',
        normalized,
    )
    normalized = re.sub(r'\[\.\.\. final truncation \.\.\.\]', '', normalized)
    normalized = re.sub(r'\[\.\.\.\s*\d+\s*block\(s\)?\s*dropped.*?\]', '', normalized)

    # 3. Normalize whitespace
    normalized = re.sub(r'\n{3,}', '\n\n', normalized)

    return normalized.strip()


def check_insanity_similarity(normalized: str, seen_set: set, threshold: float = 0.95) -> bool:
    """Check if normalized text is >threshold similar to any entry in seen_set.

    Uses difflib.SequenceMatcher for rapid similarity comparison (quick_ratio
    is O(n) and avoids the full O(n^2) matching). This is more robust than
    exact hash matching against stochastic LLM output that may produce
    semantically identical fix prompts with minor variations.

    Args:
        normalized: The normalized fix_input string.
        seen_set: Set of previously seen normalized strings.
        threshold: Similarity ratio threshold (default 0.95 = 95%).

    Returns:
        True if any entry exceeds the threshold similarity.
    """
    from difflib import SequenceMatcher
    for seen in seen_set:
        ratio = SequenceMatcher(None, normalized, seen).quick_ratio()
        if ratio > threshold:
            return True
    return False


def log_to_session_timeline(user_input: str, agent_assigned: str,
                            tools_accessed: str, final_output: str) -> None:
    """Append an entry to the session timeline in REVERSE chronological order.

    Newest entries are always at the TOP of the file so they appear first
    in the model's token budget. Uses a read-prepend-write pattern.
    """
    timestamp = datetime.now().isoformat()

    # Truncate final output to prevent unbounded file growth
    display_output = final_output[:_MAX_OUTPUT_CHARS]
    if len(final_output) > _MAX_OUTPUT_CHARS:
        display_output += "\n[... output truncated ...]"

    new_entry = (
        f"## Session Event — {timestamp}\n"
        f"**Agent Assigned:** {agent_assigned}\n"
        f"**User Input:** {user_input}\n"
        f"**Tools/Files Accessed:** {tools_accessed}\n"
        f"**Final Output:**\n"
        f"{display_output}\n"
        f"---\n"
    )

    # Ensure parent directory exists
    SESSION_TIMELINE_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Read existing content
    existing = ""
    if SESSION_TIMELINE_PATH.is_file():
        try:
            existing = SESSION_TIMELINE_PATH.read_text(encoding="utf-8")
        except Exception:
            existing = ""

    # Atomic write via temp file + rename
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=str(SESSION_TIMELINE_PATH.parent),
        prefix=".session_timeline_tmp_",
        suffix=".md",
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as tmp:
            tmp.write(new_entry + existing)
        os.replace(tmp_path, str(SESSION_TIMELINE_PATH))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    print(f"  [SessionTimeline] Logged: {agent_assigned} @ {timestamp}")


# ── Doc Format: Anchor-TOC Builder ──────────────────────────────────────────

def build_anchor_toc(doc_path: str) -> str:
    """Build a Table of Contents with line anchors for a doc file."""
    content = _get_doc_cached(doc_path)
    if not content:
        return f"(doc {doc_path} not found)"
    lines = content.splitlines()
    toc_lines = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#"):
            level = len(stripped.split("#")[0]) + 1
            title = stripped.lstrip("#").strip()
            anchor = f"{doc_path}#L{i+1}"
            toc_lines.append(f"{'  ' * (level-1)}- [{title}]({anchor})")
    return "\n".join(toc_lines[:20])


# ── Memory Ledger: Table of Contents Builder ───────────────────────────────

def _collect_ledger_entries(mem_file: Path) -> List[Tuple[bool, str]]:
    """Parse a ledger file into header-anchored chunks, then REVERSE so newest
    architectural decisions appear first in context.

    Returns (is_subsection, entry_text) pairs in REVERSE chronological order.
    """
    entries: List[Tuple[bool, str]] = []
    if not mem_file.is_file():
        return entries
    content = mem_file.read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines()

    # Phase 1: Split into logical chunks by header boundaries
    chunks: List[dict] = []
    current_chunk = None

    for ln in lines:
        stripped = ln.strip()
        if stripped.startswith("##") or stripped.startswith("###"):
            if current_chunk is not None:
                chunks.append(current_chunk)
            title = stripped.lstrip("#").strip()
            if title in BOILERPLATE_TITLES:
                current_chunk = None
                continue
            bm = re.search(r"\[(.*?)\]", stripped)
            if bm:
                anchor = bm.group(1).strip()
            else:
                anchor = title.lower().replace(" ", "-")
            is_sub = stripped.startswith("###")
            rel = mem_file.relative_to(PROJECT_ROOT).as_posix()
            current_chunk = {
                "header_line": stripped,
                "title": title,
                "anchor": anchor,
                "is_sub": is_sub,
                "rel": rel,
                "body_lines": [],
            }
        else:
            if current_chunk is not None:
                current_chunk["body_lines"].append(ln)

    if current_chunk is not None:
        chunks.append(current_chunk)

    # Phase 2: Reverse chunks — newest first
    for chunk in reversed(chunks):
        text = (
            f"  - [{chunk['title']}]({chunk['rel']}#{chunk['anchor']}) "
            f"-- use [FETCH:{chunk['rel']}#{chunk['anchor']}]\n"
        )
        entries.append((chunk["is_sub"], text))

    return entries


def ledger_toc(domain_key: str = None) -> str:
    """Build a Table of Contents for all memory ledger files in docs/memory/.

    Args:
        domain_key: If provided, the agent's own ledger gets full listing
                    (including ### subsections).

    Returns:
        Formatted markdown TOC string.
    """
    from domain_registry import ALL_DOMAINS

    mem_dir = MEMORY_DIR
    if not mem_dir.is_dir():
        return ""

    # Determine the agent's own ledger file
    own_ledger = ""
    if domain_key and domain_key in ALL_DOMAINS:
        own_ledger = ALL_DOMAINS[domain_key].get("ledger", "")

    HARD_LIMIT = 3000
    parts = ["## Memory Ledger Table of Contents\n"]

    ledger_files = sorted(
        mem_dir.glob("*_ledger.md"),
        key=lambda f: (
            0 if own_ledger and f.name == Path(own_ledger).name else 1,
            -f.stat().st_mtime,
        ),
    )

    for f in ledger_files:
        rel = f.relative_to(PROJECT_ROOT).as_posix()
        is_own = (rel == own_ledger)
        label = f"### {rel}"
        if is_own:
            label += " (YOUR LEDGER)"
        label += "\n"

        candidate = label
        if len("".join(parts)) + len(candidate) > HARD_LIMIT:
            parts.append(
                f"  - [... remaining ledgers omitted — use [FETCH] to retrieve ...]\n"
            )
            break

        parts.append(candidate)

        entries = _collect_ledger_entries(f)
        for is_sub, entry_text in entries:
            if is_sub and not is_own:
                continue
            if len("".join(parts)) + len(entry_text) > HARD_LIMIT:
                parts.append(
                    f"  - [... deeper subsections omitted — use [FETCH] to retrieve ...]\n"
                )
                break
            parts.append(entry_text)

    return "".join(parts)


# ── Rule-Breaker Accommodation: Synthetic Ledger Headers ────────────────────

def _generate_module_name(task_spec: str, agent_key: str) -> str:
    """Generate a descriptive module name from task spec and agent domain."""
    words = re.findall(r'[A-Za-z][A-Za-z0-9_]*', task_spec)
    stopwords = {
        'the', 'a', 'an', 'for', 'of', 'in', 'to', 'and', 'is', 'it',
        'be', 'are', 'was', 'were', 'been', 'this', 'that', 'with',
        'from', 'at', 'by', 'on', 'or', 'as', 'if', 'but',
    }
    significant = [w for w in words if w.lower() not in stopwords]
    name_words = significant[:5]
    if not name_words:
        return agent_key
    return '_'.join(name_words)


def ensure_ledger_header(output: str, task_spec: str, agent_key: str) -> str:
    """Detect if agent output contains a properly formatted ledger header.

    If missing, generate a synthetic header based on the task spec and
    agent domain, and prepend it.

    Args:
        output: Agent output text.
        task_spec: Task specification string.
        agent_key: Agent domain key.

    Returns:
        Possibly modified output with header prepended.
    """
    # Strip code blocks before checking for ledger headers
    stripped = re.sub(r'```.*?```', '', output, flags=re.DOTALL)
    if LEDGER_HEADER_PATTERN.search(stripped):
        return output

    task_clean = task_spec.strip().rstrip('.')
    module_name = _generate_module_name(task_clean, agent_key)
    synthetic_header = f"### [{module_name}]\n"
    modified = synthetic_header + output + "\n"
    print(f"  [LedgerGuard] ⚠ Synthetic header prepended for {agent_key}: [{module_name}]")
    return modified


def _append_to_ledger(output: str, agent_key: str, task_spec: str) -> None:
    """Persist agent output to its domain-specific ledger file on disk.

    Before writing, guarantees the output contains the mandatory
    ### [ModuleName] header via ensure_ledger_header().
    """
    from domain_registry import ALL_DOMAINS, resolve_agent_name

    domain = ALL_DOMAINS.get(resolve_agent_name(agent_key))
    if not domain:
        return
    ledger_rel = domain.get("ledger", "")
    if not ledger_rel:
        return

    safe_output = ensure_ledger_header(output, task_spec, agent_key)

    ledger_path = PROJECT_ROOT / ledger_rel
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with open(ledger_path, "a", encoding="utf-8") as f:
        f.write("\n" + safe_output.strip() + "\n")

    domain_name = domain.get("name", agent_key)
    if safe_output != output:
        print(f"  [LedgerWrite] ⚠ {domain_name}: missing header fixed → appended to {ledger_rel}")
    else:
        print(f"  [LedgerWrite] ✓ {domain_name}: entry appended to {ledger_rel}")
