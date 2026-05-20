"""
FETCH signal handler — parse [FETCH:filepath#anchor] tags and return the
content under the specified header with temporal read-depth tracking.

Also handles [READ_OFFLOADED:block_id] signals for restoring paged context.

No async/await — purely synchronous file I/O and regex parsing.
"""

from __future__ import annotations
import hashlib
import re
from pathlib import Path
from typing import Optional

from offload_store import get_offload_store
from token_budget import TokenBudget


# ── Constants ────────────────────────────────────────────────────────────────
# Use the env-var project root if set so FETCH targets resolve against the
# game project tree, not the pipeline repo itself.
import os as _os
PROJECT_ROOT = Path(_os.environ.get("MIDWAY_PROJECT_ROOT", Path(__file__).parent)).resolve()


def handle_fetch_signal(fetch_tag: str) -> str:
    """Parse [FETCH:filepath#anchor] tag, return content under that header.

    Uses non-greedy regex with lookahead to support nested brackets in
    file paths and anchors (e.g., [FETCH:docs/memory.md#[SubHeader]]).

    Includes temporal read-depth calculation for archive navigation.

    Args:
        fetch_tag: Raw FETCH tag string (e.g., "[FETCH:docs/memory/cpp_ledger.md#MemoryPool]").

    Returns:
        Formatted markdown content of the section, or empty string on error.
    """
    match = re.match(r"\[FETCH:(.*?)#(.*?)\]", fetch_tag.strip())
    if not match:
        print(f"  [FETCH] Invalid format: {fetch_tag[:80]}")
        return ""
    filepath = match.group(1).strip()
    anchor = match.group(2).strip()
    full_path = PROJECT_ROOT / filepath
    if not full_path.is_file():
        print(f"  [FETCH] File not found: {filepath}")
        return ""
    try:
        content = full_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        print(f"  [FETCH] Error reading {filepath}: {e}")
        return ""

    # Temporal Read-Depth Calculation
    temporal_depth = 0
    filename = Path(filepath).name
    if filename == "overflow_ledger.md":
        temporal_depth = 0
    else:
        vol_match = re.match(r"overflow_ledger_v(\d+)\.md$", filename)
        if vol_match:
            this_vol = int(vol_match.group(1))
            archive_dir = full_path.parent
            max_vol = 0
            for f in archive_dir.glob("overflow_ledger_v*.md"):
                m = re.search(r"_v(\d+)\.md$", f.name)
                if m:
                    v = int(m.group(1))
                    if v > max_vol:
                        max_vol = v
            temporal_depth = (max_vol + 1) - this_vol

    lines_ = content.splitlines()
    header_line = None
    header_start = -1
    for idx, ln in enumerate(lines_):
        stripped = ln.strip()
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip().lower()
            if anchor.lower() == title or anchor.lower() in title:
                header_start = idx
                header_line = stripped
                break
            bm = re.search(r"\[(.*?)\]", stripped)
            if bm and anchor.lower() == bm.group(1).strip().lower():
                header_start = idx
                header_line = stripped
                break
    if header_start == -1:
        print(f"  [FETCH] Header {anchor} not found in {filepath}")
        return ""
    hdr_lvl = header_line.split("#")[0].count("#") + 1

    # Reverse-chronological subsection fetch
    subsections: list[dict] = []
    current_sub: Optional[dict] = None
    content_lines: list[str] = []
    for j in range(header_start + 1, len(lines_)):
        ln = lines_[j]
        stripped = ln.strip()
        if stripped.startswith("#"):
            level = stripped.split("#")[0].count("#") + 1
            if level <= hdr_lvl:
                break
            if current_sub is not None:
                subsections.append(current_sub)
            current_sub = {"header": ln, "body_lines": []}
        else:
            if current_sub is not None:
                current_sub["body_lines"].append(ln)
            else:
                content_lines.append(ln)
    if current_sub is not None:
        subsections.append(current_sub)

    # Reverse: newest subsection first
    for sub in reversed(subsections):
        if content_lines:
            content_lines.append("")
        content_lines.append(sub["header"])
        content_lines.extend(sub["body_lines"])

    depth_line = f"\n**Chronological Read Depth:** {temporal_depth}\n" if temporal_depth > 0 else ""
    result = (
        "\n## Recalled Memory\n"
        f"**Source:** {filepath} > {header_line.strip()}\n"
        + depth_line
        + "\n"
        + "\n".join(content_lines)
    )
    print(f"  [FETCH] Recalled {len(content_lines)} lines from {filepath}#{anchor} (depth={temporal_depth})")
    return result


def read_offloaded_file(block_id: str) -> str:
    """Retrieve a previously offloaded context block from disk.

    Args:
        block_id: The identifier of the offloaded block.

    Returns:
        Full reconstructed text, or an error message string.
    """
    try:
        store = get_offload_store()
    except (NameError, ImportError) as e:
        return (
            "\n## Offloaded Context Retrieval: ERROR\n"
            f"**Block ID:** {block_id}\n"
            f"**Error:** OffloadStore not initialized: {e}\n"
        )
    return store.retrieve_block(block_id)


def handle_read_offloaded_signal(block_id: str, task_context: str = "",
                                  token_budget: Optional[TokenBudget] = None,
                                  task=None) -> str:
    """Process a [READ_OFFLOADED:<block_id>] signal with budget-aware paging.

    Args:
        block_id: Identifier of the offloaded block.
        task_context: Current task context text for potential page-out.
        token_budget: Token budget tracker for budget-aware retrieval.
        task: Optional Task object whose pinned_blocks set will be updated.

    Returns:
        Reconstructed content or error message.
    """
    # Pin this block to prevent it from being paged out again
    if task is not None:
        task.pinned_blocks.add(block_id)

    content = read_offloaded_file(block_id)
    if content.startswith("\n## Offloaded Context Retrieval: ERROR"):
        return content
    if token_budget is not None:
        estimated_tokens = len(content) // 3
        available = token_budget.hard_limit - token_budget.used
        if estimated_tokens > available:
            print(f"  [ReadOffloaded] !! Block needs ~{estimated_tokens} tokens, "
                  f"only {available} available. Paging out context...")
            if task_context and len(task_context) > 1000:
                freed = _page_out_context(
                    task_context, int((estimated_tokens - available) * 3),
                    token_budget, pinned_blocks=(task.pinned_blocks if task else set()))
                if freed > 0:
                    token_budget.used = max(0, token_budget.used - freed // 3)
            available = token_budget.hard_limit - token_budget.used
            if estimated_tokens > available:
                # CONTEXT_OVERFLOW: pinning prevented freeing enough space
                return (
                    "\n## Offloaded Context Retrieval: CONTEXT_OVERFLOW\n"
                    f"**Block ID:** {block_id}\n"
                    f"**Error:** Cannot free enough space — all non-pinned sections "
                    f"have been exhausted. Block needs ~{estimated_tokens} tokens "
                    f"but only {available} available.\n\n"
                    f"**Pinned blocks preventing page-out:** "
                    f"{', '.join(getattr(task, 'pinned_blocks', set())) if task else 'N/A'}\n"
                    f"**Preview (first 2000 chars):**\n"
                    f"{content[:2000]}\n\n[... CONTEXT_OVERFLOW -- increase budget or reduce pinned blocks ...]\n"
                )
    return content


def _page_out_context(context_text: str, needed_chars: int,
                       token_budget: Optional[TokenBudget] = None,
                       pinned_blocks: set = set()) -> int:
    """Page out sections of the active context to free space.

    Args:
        context_text: Current context text to page out from.
        needed_chars: Number of characters needed to free.
        token_budget: Token budget tracker.
        pinned_blocks: Set of block_id strings to skip (avoid infinite swap-loops).

    Returns:
        Number of characters freed.
    """
    if not context_text or needed_chars <= 0:
        return 0
    lines = context_text.splitlines(keepends=True)
    header_indices = []
    for i, ln in enumerate(lines):
        if re.match(r'^#{1,3}\s', ln.strip()):
            header_indices.append(i)
    if not header_indices:
        return 0
    freed = 0
    offloaded_count = 0
    for idx in reversed(header_indices):
        if freed >= needed_chars:
            break
        section_end = len(lines)
        for next_idx in header_indices:
            if next_idx > idx:
                section_end = next_idx
                break
        section_text = "".join(lines[idx:section_end])
        section_len = len(section_text)
        try:
            store = get_offload_store()
            header = lines[idx].strip() if idx < len(lines) else "(paged)"
            body = [l.rstrip("\n") for l in lines[idx + 1:section_end]]
            id_base = re.sub(r'[^a-zA-Z0-9]', '_', header[:60]).strip('_').lower()
            content_hash = hashlib.md5(section_text.encode("utf-8")).hexdigest()[:16]
            block_id = f"paged_{id_base}_{content_hash}"
            # Skip pinned blocks — prevents infinite swap-loops
            if block_id in pinned_blocks:
                continue
            store.store_block(block_id, header, body)
            offloaded_count += 1
            freed += section_len
        except Exception:
            continue
    if offloaded_count > 0:
        print(f"  [PageOut] Paged out {offloaded_count} section(s) ({freed} chars)")
    return freed
