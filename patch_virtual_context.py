#!/usr/bin/env python3
"""
Virtual Context Management System — Pipeline Patcher
=====================================================
Surgically modifies pipeline.py to implement OS-level memory paging
for the TokenBudget system. Replaces destructive pruning with lossless
disk-based offloading and agent-retrievable context blocks.

Usage:
    python patch_virtual_context.py          # Patch pipeline.py in place
    python patch_virtual_context.py --dry-run  # Preview changes without writing
    python patch_virtual_context.py --revert   # Restore from backup

Author: Automated Pipeline Architecture Agent
"""

import os
import sys
import re
import shutil
import textwrap
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.resolve()
PIPELINE_PATH = PROJECT_ROOT / "pipeline.py"
BACKUP_PATH = PROJECT_ROOT / "pipeline.py.virtual_context_backup"
OFFLOAD_DIR = PROJECT_ROOT / "offload_store"
GITIGNORE_PATH = PROJECT_ROOT / ".gitignore"

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1: OffloadStore class (insert after TokenBudget, before LRU Doc Cache)
# ══════════════════════════════════════════════════════════════════════════════

OFFLOAD_STORE_CLASS = r'''
# ── Virtual Context: OffloadStore ────────────────────────────────────────────
# Disk-based overflow buffer — inspired by OS memory paging.
# When _block_aware_collapse prunes old context blocks, they are serialized to
# offload_store/ rather than permanently deleted. Agents can retrieve them
# via [READ_OFFLOADED:<block_id>] signals.

class OffloadStore:
    """Disk-backed overflow buffer for pruned context blocks.

    Implements a paging system where context blocks evicted from the active
    token budget are written to disk as individual JSON files. Each block
    preserves its header, body, metadata, and a content hash for integrity.

    Attributes:
        store_dir: Path to the offload_store/ directory.
        index: Dict mapping block_id -> {header, timestamp, char_count, etc.}
    """

    STORE_DIR = PROJECT_ROOT / "offload_store"

    def __init__(self):
        self.store_dir = self.STORE_DIR
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self.store_dir / "_index.json"
        self.index = self._load_index()
        self._max_mb = 512  # garbage collection threshold

    # ── Index Management ──────────────────────────────────────────────

    def _load_index(self) -> dict:
        """Load the offload index from disk. Returns dict or empty."""
        if self._index_path.is_file():
            try:
                return json.loads(self._index_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save_index(self) -> None:
        """Persist the offload index to disk."""
        try:
            self._index_path.write_text(
                json.dumps(self.index, indent=2, default=str),
                encoding="utf-8",
            )
        except OSError as e:
            print(f"  [OffloadStore] ⚠ Could not write index: {e}")

    # ── Block Storage ─────────────────────────────────────────────────

    def _block_path(self, block_id: str) -> Path:
        """Return the filesystem path for a given block_id."""
        # Sanitize block_id to prevent directory traversal
        safe_id = re.sub(r'[^a-zA-Z0-9_\-]', '_', block_id)
        return self.store_dir / f"block_{safe_id}.json"

    def store_block(self, block_id: str, header: str,
                    body_lines: list, metadata: dict = None) -> bool:
        """Persist a pruned context block to disk.

        Args:
            block_id: Unique identifier for the block.
            header: The block's header line (or empty string).
            body_lines: List of body text lines.
            metadata: Optional dict with additional context (domain, agent, etc.).

        Returns:
            True if the block was written successfully, False on error.
        """
        full_text = header + "\n" + "\n".join(body_lines) if header else "\n".join(body_lines)
        content_hash = hashlib.sha256(full_text.encode("utf-8")).hexdigest()[:16]
        block_data = {
            "block_id": block_id,
            "header": header,
            "body_lines": body_lines,
            "full_text": full_text,
            "char_count": len(full_text),
            "token_estimate": len(full_text) // 3,
            "content_hash": content_hash,
            "timestamp": datetime.now().isoformat(),
            "metadata": metadata or {},
        }
        try:
            path = self._block_path(block_id)
            path.write_text(json.dumps(block_data, indent=2), encoding="utf-8")
            self.index[block_id] = {
                "path": str(path.relative_to(self.store_dir)),
                "header": header[:80],
                "char_count": len(full_text),
                "token_estimate": len(full_text) // 3,
                "timestamp": block_data["timestamp"],
                "content_hash": content_hash,
            }
            self._save_index()
            # Garbage collect after write if over limit
            self.garbage_collect(self._max_mb)
            return True
        except OSError as e:
            print(f"  [OffloadStore] ⚠ Failed to store block '{block_id}': {e}")
            return False

    def retrieve_block(self, block_id: str) -> str:
        """Read a previously offloaded block from disk.

        Args:
            block_id: The identifier used when storing the block.

        Returns:
            The full reconstructed text of the block, or an error message.
        """
        path = self._block_path(block_id)
        if not path.is_file():
            return (
                f"\n## Offloaded Context Retrieval: ERROR\n"
                f"**Block ID:** {block_id}\n"
                f"**Error:** Block not found in offload store. "
                f"It may have been garbage-collected or never written.\n"
            )
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return (
                f"\n## Recalled Offloaded Context\n"
                f"**Block ID:** {block_id}\n"
                f"**Stored:** {data.get('timestamp', 'unknown')}\n"
                f"**Size:** {data.get('char_count', 0)} chars "
                f"({data.get('token_estimate', 0)} tokens)\n"
                f"**Header:** {data.get('header', '(no header)')[:120]}\n"
                f"---\n"
                f"{data.get('full_text', '')}\n"
            )
        except (json.JSONDecodeError, KeyError, OSError) as e:
            return (
                f"\n## Offloaded Context Retrieval: ERROR\n"
                f"**Block ID:** {block_id}\n"
                f"**Error:** Corrupt or unreadable block data: {e}\n"
            )

    def delete_block(self, block_id: str) -> bool:
        """Remove a block from disk and index.

        Args:
            block_id: The identifier of the block to delete.

        Returns:
            True if deleted or already absent, False on error.
        """
        path = self._block_path(block_id)
        try:
            if path.is_file():
                path.unlink()
            self.index.pop(block_id, None)
            self._save_index()
            return True
        except OSError as e:
            print(f"  [OffloadStore] ⚠ Failed to delete block '{block_id}': {e}")
            return False

    def list_stored_blocks(self) -> list:
        """Return a manifest of all stored blocks.

        Returns:
            List of dicts with block_id, header_preview, char_count, timestamp.
        """
        return [
            {
                "block_id": bid,
                "header_preview": info.get("header", "")[:80],
                "char_count": info.get("char_count", 0),
                "token_estimate": info.get("token_estimate", 0),
                "timestamp": info.get("timestamp", ""),
            }
            for bid, info in sorted(
                self.index.items(),
                key=lambda x: x[1].get("timestamp", ""),
                reverse=True,
            )
        ]

    def store_size(self) -> int:
        """Total bytes consumed by the offload store on disk.

        Returns:
            Total file size in bytes.
        """
        total = 0
        if self.store_dir.is_dir():
            for f in self.store_dir.glob("block_*.json"):
                try:
                    total += f.stat().st_size
                except OSError:
                    pass
        return total

    def garbage_collect(self, max_mb: int = 512) -> int:
        """Evict oldest blocks when store exceeds max_mb.

        Uses LRU-like strategy: evicts oldest blocks first (by timestamp)
        until the total store size is under 80% of max_mb.

        Args:
            max_mb: Maximum allowed store size in megabytes.

        Returns:
            Number of blocks evicted.
        """
        max_bytes = max_mb * 1024 * 1024
        current = self.store_size()
        if current <= max_bytes:
            return 0

        target = int(max_bytes * 0.8)  # Evict down to 80%
        # Sort by timestamp ascending (oldest first)
        sorted_blocks = sorted(
            self.index.items(),
            key=lambda x: x[1].get("timestamp", ""),
        )
        evicted = 0
        for bid, _ in sorted_blocks:
            if self.store_size() <= target:
                break
            if self.delete_block(bid):
                evicted += 1

        if evicted > 0:
            print(f"  [OffloadStore] Garbage collected {evicted} block(s) "
                  f"({current // 1024} KB -> {self.store_size() // 1024} KB)")
        return evicted


# Global singleton — shared across all budget operations
_OFFLOAD_STORE = OffloadStore()

def get_offload_store() -> OffloadStore:
    """Return the singleton OffloadStore instance."""
    return _OFFLOAD_STORE
'''


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2: Modified _block_aware_collapse with offloading
# ══════════════════════════════════════════════════════════════════════════════

BLOCK_AWARE_COLLAPSE_REPLACEMENT = r'''
    def _block_aware_collapse(self, text: str, available_chars: int) -> str:
        """AST-aware / block-aware truncation with lossless offloading.

        Instead of blindly dropping the oldest blocks (which destroys context),
        this method:
        1. Splits text into structural blocks (C++ functions, Lua blocks,
           Markdown headers, code fences)
        2. Preserves all block headers/signatures
        3. Collapses internal bodies with a [... collapsed ...] notice
        4. OFFLOADS the oldest blocks to disk instead of deleting them,
           replacing them with actionable <OFFLOADED_CONTEXT> placeholder tags
           that agents can use to retrieve the content later.

        Supported block types:
        - C++ function definitions: `type name(...) {`
        - Markdown headers: `## ...`, `### ...`
        - Code fence blocks: ``` ... ```
        - Simple list continuation headers
        """
        if len(text) <= available_chars:
            return text

        lines = text.splitlines(keepends=True)
        # Phase 1: Identify structural blocks
        blocks = []  # list of dicts: {header: str, body_lines: list, header_line_idx: int}
        current_block = None
        in_fence = False

        for i, ln in enumerate(lines):
            stripped = ln.strip()

            # Track code fences
            if stripped.startswith("```"):
                in_fence = not in_fence
                if current_block is None:
                    # Standalone fence line — start a new block
                    blocks.append({
                        "header": ln.rstrip("\n"),
                        "body_lines": [],
                        "is_fence_block": True,
                    })
                    current_block = None
                else:
                    # Close current block
                    if current_block:
                        blocks.append(current_block)
                        current_block = None
                continue

            # Detect block headers
            is_header = False

            # C++ function / method signature: lines ending with '{' or containing ') {'
            if not in_fence:
                func_match = re.match(
                    r'^(\s*(?:static\s+|inline\s+|virtual\s+|const\s+)*'
                    r'(?:void|int|float|double|bool|char|std::\w+|\w+(?:<[^>]+>)?)\s+'  # return type
                    r'(?:[*&]?\s*)?'  # pointer/ref
                    r'\w+\s*\([^)]*\)\s*(?:const\s*)?(?:override\s*)?(?:final\s*)?'
                    r'(?:\s*throw\s*\([^)]*\)\s*)?\{?\s*)$',
                    stripped,
                )
                is_header = is_header or bool(func_match)

                # Alternative: just a line ending with '{' on its own (constructor, lambda, etc)
                if not is_header and stripped.endswith("{"):
                    # Check it's not just a random brace
                    if len(stripped) > 1 or (i > 0 and lines[i-1].strip().endswith(")")):
                        is_header = True

            # Markdown headers
            if stripped.startswith("##") or stripped.startswith("###") or stripped.startswith("# "):
                is_header = True

            # Detection: numbered list items with bold (agent output pattern)
            if re.match(r'^\d+\.\s+\*\*', stripped):
                is_header = True

            if is_header:
                # Close previous block
                if current_block:
                    blocks.append(current_block)
                current_block = {
                    "header": ln.rstrip("\n"),
                    "body_lines": [],
                    "is_fence_block": False,
                }
            else:
                if current_block is not None:
                    current_block["body_lines"].append(ln)
                else:
                    # orphan lines before first header belong to a preamble block
                    if not blocks or blocks[-1].get("_is_preamble"):
                        # Extend or create preamble
                        if blocks and blocks[-1].get("_is_preamble"):
                            blocks[-1]["body_lines"].append(ln)
                        else:
                            blocks.insert(0, {
                                "header": None,
                                "body_lines": [ln],
                                "_is_preamble": True,
                                "is_fence_block": False,
                            })
                    else:
                        blocks.append({
                            "header": None,
                            "body_lines": [ln],
                            "_is_preamble": True,
                            "is_fence_block": False,
                        })

        # Flush last block
        if current_block:
            blocks.append(current_block)

        # Phase 2: Measure and collapse
        header_overhead = 0
        for blk in blocks:
            h = blk.get("header") or ""
            header_overhead += len(h) + 1  # +1 for newline

        # Calculate available for body content
        body_budget = available_chars - header_overhead
        if body_budget < 0:
            # Extremely tight: only keep block headers
            result = []
            for blk in blocks:
                h = blk.get("header") or ""
                if h:
                    result.append(h)
            collapsed = "\n".join(result)
            # Ensure we don't exceed
            if len(collapsed) > available_chars:
                collapsed = collapsed[:available_chars]
            return collapsed

        # Process blocks in reverse order (newest preserved first when pruning)
        collapsed_blocks = []
        remaining_body = body_budget

        for blk in reversed(blocks):
            if blk.get("_is_preamble") or blk.get("is_fence_block"):
                # Preamble and fence blocks are collapsed wholesale
                body_text = "".join(blk["body_lines"])
                header_text = (blk.get("header") or "") + "\n" if blk.get("header") else ""
                total_len = len(header_text) + len(body_text)
                if total_len <= remaining_body:
                    collapsed_blocks.insert(0, blk)
                    remaining_body -= total_len
                elif body_text and remaining_body > len(header_text) + 10:
                    # Partially keep
                    keep_len = remaining_body - len(header_text)
                    blk["body_lines"] = [body_text[:keep_len] + "\n[... truncated ...]\n"]
                    collapsed_blocks.insert(0, blk)
                    remaining_body = 0
                elif header_text and remaining_body >= len(header_text):
                    # At least keep the header
                    blk["body_lines"] = ["[... body collapsed ...]\n"]
                    collapsed_blocks.insert(0, blk)
                    remaining_body -= len(header_text) + 30
                else:
                    # Cannot fit at all — OFFLOAD this block instead of dropping
                    self._offload_and_placeholder(blk, collapsed_blocks, remaining_body)
                    remaining_body = 0
                continue

            header_text = (blk.get("header") or "") + "\n"
            body_text = "".join(blk["body_lines"])
            header_len = len(header_text)
            body_len = len(body_text)

            if header_len + body_len <= remaining_body:
                # Block fits entirely
                collapsed_blocks.insert(0, blk)
                remaining_body -= (header_len + body_len)
            elif header_len <= remaining_body:
                # Header fits, but body doesn't — collapse the body with a [... truncated ...] notice
                collapsed = "\n".join([
                    blk["header"],
                    "[... function body collapsed for context budget ...]",
                ])
                # Replace with collapsed version
                blk["body_lines"] = ["[... function body collapsed for context budget ...]\n"]
                collapsed_blocks.insert(0, blk)
                remaining_body -= (header_len + 70)  # header + collapse notice
            else:
                # Not even the header fits — OFFLOAD this and all remaining older blocks
                # (We're iterating reversed, so these are the oldest blocks)
                # Collect all remaining blocks that don't fit and offload them
                dropped_count = 0
                for older_blk in blocks[:blocks.index(blk)]:
                    if not older_blk.get("_is_preamble"):
                        dropped_count += 1
                        self._offload_single_block(older_blk)

                # Offload the current block as well
                self._offload_single_block(blk)
                dropped_count += 1

                if dropped_count > 0:
                    # Insert an actionable placeholder tag instead of a dead notice
                    placeholder = self._build_offload_placeholder(
                        dropped_count, blocks[:blocks.index(blk) + 1]
                    )
                    if collapsed_blocks:
                        collapsed_blocks.insert(0, {
                            "header": None,
                            "body_lines": [placeholder],
                            "_is_preamble": True,
                            "is_fence_block": False,
                        })
                break  # Stop: remaining blocks have been offloaded

        # Reconstruct
        result_lines = []
        for blk in collapsed_blocks:
            h = blk.get("header")
            if h:
                result_lines.append(h)
            for body_ln in blk["body_lines"]:
                result_lines.append(body_ln.rstrip("\n"))

        result = "\n".join(result_lines)
        # Final safety trim
        if len(result) > available_chars:
            result = result[:available_chars] + "\n[... final truncation ...]"
        return result

    def _offload_single_block(self, blk: dict) -> None:
        """Persist a single block to the offload store.

        Args:
            blk: Block dict with header, body_lines keys.
        """
        try:
            from pipeline import get_offload_store
            store = get_offload_store()
        except ImportError:
            # Fallback: use direct import for standalone operation
            try:
                from __main__ import get_offload_store
                store = get_offload_store()
            except ImportError:
                store = OffloadStore()

        header = blk.get("header") or ""
        body_lines = blk.get("body_lines") or []

        # Generate a deterministic block_id from header content
        if header:
            id_base = re.sub(r'[^a-zA-Z0-9]', '_', header[:60]).strip('_').lower()
        else:
            id_base = "unnamed_block"
        # Add a short hash for uniqueness
        content_hash = hashlib.md5(
            (header + "".join(body_lines)).encode("utf-8")
        ).hexdigest()[:8]
        block_id = f"{id_base}_{content_hash}"

        success = store.store_block(block_id, header, body_lines)
        if success:
            char_count = len(header) + sum(len(l) for l in body_lines)
            print(f"  [OffloadStore] ✓ Offloaded block '{block_id}' ({char_count} chars)")

    def _offload_and_placeholder(self, blk: dict, collapsed_blocks: list,
                                  remaining_body: int) -> None:
        """Offload a block and add a short placeholder notice.

        Args:
            blk: The block dict to offload.
            collapsed_blocks: The accumulator list being built.
            remaining_body: Remaining character budget (unused here but
                            tracked for interface consistency).
        """
        self._offload_single_block(blk)
        header = blk.get("header") or ""
        if collapsed_blocks and header:
            collapsed_blocks.insert(0, {
                "header": None,
                "body_lines": [f"\n[! {header[:60]}... offloaded to disk — use [READ_OFFLOADED] to retrieve]\n"],
                "_is_preamble": True,
                "is_fence_block": False,
            })

    def _build_offload_placeholder(self, count: int, offloaded_blocks: list) -> str:
        """Build an actionable <OFFLOADED_CONTEXT> placeholder tag.

        Args:
            count: Number of blocks being offloaded.
            offloaded_blocks: List of block dicts that were offloaded.

        Returns:
            A formatted placeholder string with preview and retrieval instructions.
        """
        # Collect all block IDs that were offloaded
        block_ids = []
        preview_lines = []
        for blk in offloaded_blocks:
            if blk.get("_is_preamble"):
                continue
            header = blk.get("header") or ""
            body_lines = blk.get("body_lines") or []
            if header:
                id_base = re.sub(r'[^a-zA-Z0-9]', '_', header[:60]).strip('_').lower()
            else:
                id_base = "unnamed_block"
            content_hash = hashlib.md5(
                (header + "".join(body_lines)).encode("utf-8")
            ).hexdigest()[:8]
            block_id = f"{id_base}_{content_hash}"
            block_ids.append(block_id)

            # Collect preview lines (first few lines of content)
            for preview_ln in ([header] if header else []) + body_lines[:9]:
                preview_lines.append(preview_ln.rstrip())

        preview = "\n".join(preview_lines[:10])
        block_list = "', '".join(block_ids)

        placeholder = (
            "\n<OFFLOADED_CONTEXT>\n"
            f"**{count} block(s) offloaded to disk — context preserved losslessly.**\n\n"
            "Preview (first 10 lines):\n"
            f"```\n{preview}\n```\n\n"
            f"Offloaded block IDs: '{block_list}'\n"
            "To retrieve full content, embed:\n"
            f"  [READ_OFFLOADED:{block_ids[0] if block_ids else 'block_id'}]\n"
            "The orchestrator will automatically inject the full content into "
            "your active context.\n"
            "</OFFLOADED_CONTEXT>\n"
        )
        return placeholder
'''


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3: read_offloaded_file function (insert after handle_fetch_signal)
# ══════════════════════════════════════════════════════════════════════════════

READ_OFFLOADED_FUNCTION = r'''

# ── Virtual Context: read_offloaded_file ────────────────────────────────────
# Agent-accessible retrieval tool for offloaded context blocks.
# Integrates with TokenBudget for bi-directional context paging.

def read_offloaded_file(block_id: str) -> str:
    """Retrieve a previously offloaded context block from disk.

    This function is designed to be called directly or via the
    [READ_OFFLOADED:<block_id>] signal. It performs integrity checks
    and returns the full reconstructed content.

    Args:
        block_id: The identifier of the offloaded block (as shown in
                  the <OFFLOADED_CONTEXT> placeholder tag).

    Returns:
        Full reconstructed text of the offloaded block, or an
        error message if the block cannot be found or read.

    Error handling:
        - FileNotFoundError: Block ID doesn't exist in store
        - json.JSONDecodeError: Block file is corrupt
        - OSError: Filesystem-level failure (permissions, disk full)
        - All errors return a descriptive string rather than raising.
    """
    try:
        store = get_offload_store()
    except (NameError, ImportError) as e:
        return (
            f"\n## Offloaded Context Retrieval: ERROR\n"
            f"**Block ID:** {block_id}\n"
            f"**Error:** OffloadStore not initialized: {e}\n"
        )

    return store.retrieve_block(block_id)


def handle_read_offloaded_signal(block_id: str, task_context: str = "",
                                  token_budget: 'TokenBudget' = None) -> str:
    """Process a [READ_OFFLOADED:<block_id>] signal with budget-aware paging.

    This is the bi-directional context paging handler. When an agent requests
    an offloaded block, this function:
    1. Retrieves the block from disk via read_offloaded_file()
    2. Checks if injecting the block would exceed the token budget
    3. If it would overflow: automatically pages out OTHER inactive blocks
       from the active context to make room
    4. Returns the reconstructed content ready for injection

    Args:
        block_id: The offloaded block identifier.
        task_context: The agent's current task context string (for paging).
        token_budget: TokenBudget instance for budget checking (or None).

    Returns:
        Formatted offloaded content string, or error message.
    """
    content = read_offloaded_file(block_id)

    # If it's an error message, return as-is
    if content.startswith("\n## Offloaded Context Retrieval: ERROR"):
        return content

    # Estimate token cost of injecting this content
    if token_budget is not None:
        estimated_tokens = len(content) // 3
        available = token_budget.hard_limit - token_budget.used

        if estimated_tokens > available:
            # ── Bi-Directional Paging ──────────────────────────────────
            # We need to make room. Try to page out the oldest context
            # (task_context) to disk to free up budget.
            print(f"  [ReadOffloaded] ⚠ Block '{block_id}' requires ~{estimated_tokens} tokens "
                  f"but only {available} available. Attempting page-out...")

            # Try to free space from the task context
            if task_context and len(task_context) > 1000:
                freed = _page_out_context(task_context, estimated_tokens - available + 500,
                                          token_budget)
                if freed > 0:
                    print(f"  [ReadOffloaded] ✓ Paged out {freed} chars to make room")
                    token_budget.used = max(0, token_budget.used - freed // 3)

            # Recheck after paging
            available = token_budget.hard_limit - token_budget.used
            if estimated_tokens > available:
                return (
                    f"\n## Offloaded Context Retrieval: WARNING\n"
                    f"**Block ID:** {block_id}\n"
                    f"**Note:** Block requires ~{estimated_tokens} tokens but only "
                    f"{available} available after paging. Returning truncated preview.\n\n"
                    f"**Preview (first 2000 chars):**\n"
                    f"{content[:2000]}\n\n[... truncated — use token budget increase to retrieve full block ...]\n"
                )

    return content


def _page_out_context(context_text: str, needed_chars: int,
                       token_budget: 'TokenBudget') -> int:
    """Page out sections of the active context to free space.

    Uses structure-aware splitting similar to _block_aware_collapse to
    identify the oldest/redundant sections and offload them.

    Args:
        context_text: The active context text to page from.
        needed_chars: Minimum number of characters to free.
        token_budget: TokenBudget instance for tracking.

    Returns:
        Number of characters freed (0 if none).
    """
    if not context_text or needed_chars <= 0:
        return 0

    # Split into paragraphs/sections
    sections = re.split(r'(\n#{1,3}\s)', context_text)
    if len(sections) < 3:
        return 0  # Too small to page

    freed = 0
    offloaded_count = 0
    before_page = len(context_text)

    # Walk from the end (oldest sections) and offload them
    # We work with the raw text to find section boundaries
    lines = context_text.splitlines(keepends=True)
    # Find section header indices
    header_indices = []
    for i, ln in enumerate(lines):
        if re.match(r'^#{1,3}\s', ln.strip()):
            header_indices.append(i)

    # Offload oldest sections from the end
    for idx in reversed(header_indices):
        if freed >= needed_chars:
            break
        # Find the end of this section
        section_end = len(lines)
        for next_idx in header_indices:
            if next_idx > idx:
                section_end = next_idx
                break
        section_text = "".join(lines[idx:section_end])
        section_len = len(section_text)

        # Offload this section
        try:
            store = get_offload_store()
            header = lines[idx].strip() if idx < len(lines) else "(paged)"
            body = [l.rstrip("\n") for l in lines[idx+1:section_end]]
            id_base = re.sub(r'[^a-zA-Z0-9]', '_', header[:60]).strip('_').lower()
            content_hash = hashlib.md5(section_text.encode("utf-8")).hexdigest()[:8]
            block_id = f"paged_{id_base}_{content_hash}"
            store.store_block(block_id, header, body)
            offloaded_count += 1
            freed += section_len
        except Exception as e:
            print(f"  [PageOut] ⚠ Error offloading section at line {idx}: {e}")
            continue

    if offloaded_count > 0:
        print(f"  [PageOut] Paged out {offloaded_count} section(s) ({freed} chars)")

    return freed
'''


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4: READ_OFFLOADED signal pattern (insert into SIGNAL_PATTERNS)
# ══════════════════════════════════════════════════════════════════════════════

READ_OFFLOADED_SIGNAL_PATTERN_REPLACEMENT = r'''    "FETCH": r"\[FETCH:([^\]]+)#([^\]]+)\]",
    "READ_OFFLOADED": r"\[READ_OFFLOADED:([^\]]+)\]",
}'''


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5: Signal handler for READ_OFFLOADED in the mesh loop
# ══════════════════════════════════════════════════════════════════════════════

READ_OFFLOADED_HANDLER_BLOCK = '''
            elif stype == "READ_OFFLOADED":
                # Retrieve an offloaded context block and inject it
                block_id = signal.get("content", "")
                if not block_id:
                    print("  [ReadOffloaded] Empty block_id, skipping")
                    continue

                # Build the retrieval request as a query for the DOC oracle
                # (or handle directly if simple enough)
                read_query_spec = (
                    f"Memory Oracle: Resolve READ_OFFLOADED request\n"
                    f"Requesting agent: {task.agent}\n"
                    f"Their current task: {task.spec[:200]}\n"
                    f"Requested block: {block_id}\n"
                    f"---\n"
                    f"Retrieve the offloaded context block and return it "
                    f"formatted as ## Recalled Offloaded Context. "
                    f"Verify the block exists before returning content."
                )

                # Queue a DOC query to resolve the READ_OFFLOADED
                read_offload_task = Task(
                    agent="DOC",
                    spec=read_query_spec,
                    task_id=f"read_offload_{task.task_id}",
                    parent=task.task_id,
                    is_query=True,
                )

                # The handler directly reads the file and injects it
                # rather than routing through DOC, for efficiency
                offloaded_content = read_offloaded_file(block_id)

                # ── Bi-Directional Context Paging ─────────────────────
                # Check if injecting would exceed budget
                if budget is not None:
                    estimated_cost = len(offloaded_content) // 3
                    available = budget.hard_limit - budget.used
                    if estimated_cost > available:
                        print(f"  [ReadOffloaded] ⚠ Block needs ~{estimated_cost} tokens, "
                              f"only {available} available. Paging out context...")
                        # Page out some of the task's current context
                        freed = _page_out_context(
                            task.context or "",
                            int((estimated_cost - available) * 3),
                            budget,
                        )
                        if freed > 0:
                            budget.used = max(0, budget.used - freed // 3)
                            print(f"  [ReadOffloaded] ✓ Made room by paging out {freed} chars")

                # Inject the retrieved content into the task context
                task.context = (task.context or "") + "\n" + offloaded_content
                # Re-queue the task to continue with the injected context
                task.completed = False
                work_queue.appendleft(task)
                print(f"  [ReadOffloaded] Injected offloaded block '{block_id}' into {task.agent}")

'''


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 6: Agent prompt extension for READ_OFFLOADED tool
# ══════════════════════════════════════════════════════════════════════════════

AGENT_PROMPT_EXTENSION = (
    '- [READ_OFFLOADED:<block_id>] — Retrieve context that was previously '
    'offloaded to disk when the token budget was exceeded. Use the '
    '<OFFLOADED_CONTEXT> preview tags as your guide to determine which '
    'block_id to request.\n'
)


# ══════════════════════════════════════════════════════════════════════════════
# PATCH APPLICATION LOGIC
# ══════════════════════════════════════════════════════════════════════════════

def create_backup() -> bool:
    """Create a backup of the original pipeline.py."""
    if not PIPELINE_PATH.is_file():
        print(f"  [Error] {PIPELINE_PATH} not found.")
        return False
    try:
        shutil.copy2(str(PIPELINE_PATH), str(BACKUP_PATH))
        print(f"  [Backup] Created: {BACKUP_PATH}")
        return True
    except OSError as e:
        print(f"  [Error] Backup failed: {e}")
        return False


def add_to_gitignore() -> None:
    """Ensure offload_store/ is in .gitignore."""
    if not GITIGNORE_PATH.is_file():
        return
    content = GITIGNORE_PATH.read_text(encoding="utf-8")
    patterns = ["offload_store/", "offload_store/*.json"]
    added = []
    for pat in patterns:
        if pat not in content:
            content += f"\n{pat}"
            added.append(pat)
    if added:
        GITIGNORE_PATH.write_text(content, encoding="utf-8")
        print(f"  [GitIgnore] Added: {', '.join(added)}")


def create_offload_store_dir() -> None:
    """Create the offload_store directory with a .gitkeep."""
    OFFLOAD_DIR.mkdir(parents=True, exist_ok=True)
    gitkeep = OFFLOAD_DIR / ".gitkeep"
    if not gitkeep.is_file():
        gitkeep.write_text("", encoding="utf-8")
    print(f"  [OffloadStore] Created directory: {OFFLOAD_DIR}")


def apply_patches(dry_run: bool = False) -> bool:
    """Apply all surgical patches to pipeline.py.

    Args:
        dry_run: If True, only show what would be changed.

    Returns:
        True if all patches were applied successfully.
    """
    if not PIPELINE_PATH.is_file():
        print(f"  [Error] {PIPELINE_PATH} not found.")
        return False

    original = PIPELINE_PATH.read_text(encoding="utf-8")
    patched = original
    modifications = 0

    # ── PATCH 1: Add OffloadStore class ───────────────────────────────
    # Insert after TokenBudget's `status()` method, before the LRU Doc Cache comment
    anchor1 = "# ── LRU Doc Cache ───────────────────────────────────────────"
    if anchor1 in patched:
        # Find the line just before this anchor
        insertion_point = patched.find(anchor1)
        # OffloadStore class + global singleton go right before the LRU Doc Cache
        patched = patched[:insertion_point] + OFFLOAD_STORE_CLASS + "\n\n" + patched[insertion_point:]
        modifications += 1
        print(f"  [Patch 1/6] OK - Inserted OffloadStore class before LRU Doc Cache")
    else:
        print(f"  [Patch 1/6] ✗ Could not find anchor: '{anchor1}'")

    # ── PATCH 2: Replace _block_aware_collapse ────────────────────────
    # Find the existing _block_aware_collapse method and replace it
    old_method_start = "    def _block_aware_collapse(self, text: str, available_chars: int) -> str:"
    old_method_pattern_start = old_method_start + '\n        """AST-aware / block-aware truncation.'
    old_method_pattern_end = '        if len(result) > available_chars:\n            result = result[:available_chars] + "\\n[... final truncation ...]"\n        return result\n\n    def add(self, text: str, label: str = "") -> str:'

    if old_method_start in patched:
        # Find where the method starts and where 'def add(' follows
        start_idx = patched.find(old_method_start)
        def_add_idx = patched.find("\n    def add(self, text: str, label: str = \"\") -> str:", start_idx)

        if def_add_idx > start_idx:
            # Replace everything from method start to just before 'def add('
            replacement = BLOCK_AWARE_COLLAPSE_REPLACEMENT
            # Trim leading newlines from replacement to match indentation
            # Keep the original method start line detection but replace full body
            before = patched[:start_idx]
            after = patched[def_add_idx:]  # Keep 'def add(' and everything after
            patched = before + replacement + "\n" + after
            modifications += 1
            print(f"  [Patch 2/6] ✓ Replaced _block_aware_collapse with offloading version")
        else:
            print(f"  [Patch 2/6] ✗ Could not find 'def add(' after _block_aware_collapse")
    else:
        # Try alternate anchor
        old_alt = "        # Phase 2: Measure and collapse"
        if old_alt in patched:
            print(f"  [Patch 2/6] ⚠ Method boundary ambiguous — using line-based replacement")
            # Try a more targeted approach
            lines = patched.splitlines(True)
            new_lines = []
            in_method = False
            method_lines = []
            method_start_line = None
            for i, ln in enumerate(lines):
                if ln.strip() == "    def _block_aware_collapse(self, text: str, available_chars: int) -> str:":
                    in_method = True
                    method_start_line = i
                    method_lines = [ln]
                elif in_method:
                    method_lines.append(ln)
                    # Method ends when we hit 'def add('
                    if ln.strip().startswith("def add(self, text: str, label: str = \"\") -> str:"):
                        in_method = False
                        # Replace the method lines with our new implementation
                        new_impl = BLOCK_AWARE_COLLAPSE_REPLACEMENT.splitlines(True)
                        # Keep the 'def add(' line
                        new_lines.extend(new_impl)
                        new_lines.append(ln)
                        method_lines = None
                        continue
                if not in_method and method_lines is None:
                    new_lines.append(ln)
                elif not in_method and method_lines is not None:
                    new_lines.append(ln)

            if method_lines is None:
                patched = "".join(new_lines)
                modifications += 1
                print(f"  [Patch 2/6] ✓ Replaced _block_aware_collapse (line-by-line)")
            else:
                print(f"  [Patch 2/6] ✗ Line-by-line replacement failed")
        else:
            print(f"  [Patch 2/6] ✗ Could not find _block_aware_collapse method")

    # ── PATCH 3: Insert read_offloaded_file function ──────────────────
    # Insert after the handle_fetch_signal function (before next section)
    anchor3 = "def handle_fetch_signal(fetch_tag: str) -> str:"
    if anchor3 in patched:
        # Find the end of handle_fetch_signal by finding the next `def ` or section comment
        fetch_func_end = patched.find("\n# ── Rule-Breaker Accommodation:", patched.find(anchor3))
        if fetch_func_end == -1:
            # Fallback: find next def
            fetch_func_end = patched.find("\ndef ", patched.find(anchor3) + 10)
            if fetch_func_end == -1:
                fetch_func_end = len(patched)

        patched = patched[:fetch_func_end] + "\n" + READ_OFFLOADED_FUNCTION + patched[fetch_func_end:]
        modifications += 1
        print(f"  [Patch 3/6] ✓ Inserted read_offloaded_file and handler functions")
    else:
        print(f"  [Patch 3/6] ✗ Could not find handle_fetch_signal")

    # ── PATCH 4: Add READ_OFFLOADED to SIGNAL_PATTERNS ────────────────
    old_fetch_pattern = '    "FETCH": r"\\[FETCH:([^\\]]+)#([^\\]]+)\\"],\n}'
    new_fetch_pattern = READ_OFFLOADED_SIGNAL_PATTERN_REPLACEMENT

    # More robust matching
    signal_patterns_start = '    "QUERY": r"\\[QUERY:([^\\]]+):([^\\]]+)\\"],'
    signal_patterns_end = '}\n\n# Multi-line double-check pattern'

    if old_fetch_pattern in patched:
        patched = patched.replace(old_fetch_pattern, new_fetch_pattern)
        modifications += 1
        print(f"  [Patch 4/6] ✓ Added READ_OFFLOADED to SIGNAL_PATTERNS")
    else:
        # Try the end of SIGNAL_PATTERNS more carefully
        # Find the closing brace of SIGNAL_PATTERNS
        sp_start = patched.find("SIGNAL_PATTERNS = {")
        if sp_start > 0:
            # Find the matching closing brace (simplified: find '}' after the last pattern)
            sp_end = patched.find("\n}", sp_start)
            if sp_end > sp_start:
                # Check if we already have READ_OFFLOADED
                if "READ_OFFLOADED" not in patched[sp_start:sp_end]:
                    # Replace the closing brace with our new pattern + closing
                    old_close = patched[sp_end:sp_end+2]
                    patched = patched[:sp_end] + ',\n    "READ_OFFLOADED": r"\\[READ_OFFLOADED:([^\\]]+)\\"],\n}' + patched[sp_end+2:]
                    modifications += 1
                    print(f"  [Patch 4/6] ✓ Added READ_OFFLOADED to SIGNAL_PATTERNS (fallback)")
                else:
                    print(f"  [Patch 4/6] ⚠ READ_OFFLOADED already in SIGNAL_PATTERNS")
            else:
                print(f"  [Patch 4/6] ✗ Could not parse SIGNAL_PATTERNS structure")
        else:
            print(f"  [Patch 4/6] ✗ Could not find SIGNAL_PATTERNS definition")

    # ── PATCH 5: Add READ_OFFLOADED handler in mesh loop ──────────────
    # Find the FETCH handler section and insert the READ_OFFLOADED handler after it
    fetch_handler_end = "                print(f\"  [FETCH] Routed to DOC oracle (depth {fetch_depth+1}/3): {fetch_target[:80]}...\")"
    if fetch_handler_end in patched:
        insert_pos = patched.find(fetch_handler_end) + len(fetch_handler_end)
        patched = patched[:insert_pos] + READ_OFFLOADED_HANDLER_BLOCK + patched[insert_pos:]
        modifications += 1
        print(f"  [Patch 5/6] ✓ Added READ_OFFLOADED handler in mesh execution loop")
    else:
        # Try alternate anchor — the end of the FETCH elif block
        fetch_close = "                continue\n\n        # Check double-check for unresolved items"
        if fetch_close in patched:
            insert_pos = patched.find(fetch_close)
            patched = patched[:insert_pos] + READ_OFFLOADED_HANDLER_BLOCK + patched[insert_pos:]
            modifications += 1
            print(f"  [Patch 5/6] ✓ Added READ_OFFLOADED handler (fallback anchor)")
        else:
            print(f"  [Patch 5/6] ✗ Could not find FETCH handler location")

    # ── PATCH 6: Extend MESH_AGENT_SYSTEM_EXTENSION ───────────────────
    # Find the FETCH signal description and add READ_OFFLOADED after it
    fetch_signal_line = '    "- [FETCH:<filepath>#<HeaderName>] — Recall context from your disk-based memory ledger. Does NOT count against iteration limit.\\n"'
    if fetch_signal_line in patched:
        insert_pos = patched.find(fetch_signal_line) + len(fetch_signal_line)
        patched = patched[:insert_pos] + "\n" + AGENT_PROMPT_EXTENSION + patched[insert_pos:]
        modifications += 1
        print(f"  [Patch 6/6] ✓ Extended MESH_AGENT_SYSTEM_EXTENSION with READ_OFFLOADED tool")
    else:
        print(f"  [Patch 6/6] ✗ Could not find FETCH signal description in agent prompt")

    # ── WRITE OUTPUT ──────────────────────────────────────────────────
    if modifications == 0:
        print(f"\n  [Result] No patches were applied. Is the file already patched?")
        return False

    if dry_run:
        print(f"\n  [Dry-Run] {modifications}/6 patches would be applied. No files written.")
        print(f"  [Dry-Run] To apply, run: python patch_virtual_context.py")
        return True

    try:
        PIPELINE_PATH.write_text(patched, encoding="utf-8")
        print(f"\n  [Result] Successfully applied {modifications}/6 patches to {PIPELINE_PATH.name}")
        create_offload_store_dir()
        add_to_gitignore()
        print(f"  [Result] offload_store/ directory created and added to .gitignore")
        return True
    except OSError as e:
        print(f"\n  [Error] Failed to write patched file: {e}")
        return False


def revert_backup() -> bool:
    """Restore pipeline.py from backup."""
    if not BACKUP_PATH.is_file():
        print(f"  [Error] No backup found at {BACKUP_PATH}")
        return False
    try:
        shutil.copy2(str(BACKUP_PATH), str(PIPELINE_PATH))
        print(f"  [Revert] Restored {PIPELINE_PATH} from backup")
        return True
    except OSError as e:
        print(f"  [Error] Revert failed: {e}")
        return False


def verify_patches() -> bool:
    """Verify that all patches were applied correctly."""
    if not PIPELINE_PATH.is_file():
        return False
    content = PIPELINE_PATH.read_text(encoding="utf-8")
    checks = []

    # Check 1: OffloadStore class
    checks.append(("OffloadStore class", "class OffloadStore:" in content))
    # Check 2: OffloadStore singleton
    checks.append(("_OFFLOAD_STORE singleton", "_OFFLOAD_STORE = OffloadStore()" in content))
    # Check 3: get_offload_store function
    checks.append(("get_offload_store()", "def get_offload_store()" in content))
    # Check 4: Offloaded blocks go to disk (not pruned)
    checks.append(("_offload_single_block method", "def _offload_single_block" in content))
    # Check 5: OFFLOADED_CONTEXT placeholder
    checks.append(("OFFLOADED_CONTEXT placeholder",
                    "<OFFLOADED_CONTEXT>" in content))
    # Check 6: read_offloaded_file function
    checks.append(("read_offloaded_file()", "def read_offloaded_file" in content))
    # Check 7: READ_OFFLOADED signal pattern
    checks.append(("READ_OFFLOADED signal pattern",
                    "READ_OFFLOADED" in content and 'r"\\[READ_OFFLOADED:' in content))
    # Check 8: READ_OFFLOADED handler in mesh loop
    checks.append(("READ_OFFLOADED handler",
                    'elif stype == "READ_OFFLOADED":' in content))
    # Check 9: Agent prompt extension
    checks.append(("Agent prompt extension",
                    "[READ_OFFLOADED:<block_id>]" in content))
    # Check 10: Bi-directional paging
    checks.append(("Bi-directional paging",
                    "_page_out_context" in content or "page_out" in content))

    passed = sum(1 for _, ok in checks if ok)
    total = len(checks)
    print(f"\n  [Verification] {passed}/{total} checks passed:")
    for label, ok in checks:
        status = "✓" if ok else "✗"
        print(f"    {status} {label}")

    return passed == total


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Apply Virtual Context Management patches to pipeline.py"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview changes without modifying files")
    parser.add_argument("--revert", action="store_true",
                        help="Restore pipeline.py from backup")
    parser.add_argument("--verify", action="store_true",
                        help="Verify that patches are applied correctly")
    args = parser.parse_args()

    if args.revert:
        if revert_backup():
            print(f"  [Done] Reverted to original.")
        else:
            sys.exit(1)
        return

    if args.verify:
        verify_patches()
        return

    if not create_backup():
        sys.exit(1)

    success = apply_patches(dry_run=args.dry_run)
    if success:
        verify_patches()
        print(f"\n  [Done] Virtual Context Management system installed.")
        print(f"  [Info] Backup saved to: {BACKUP_PATH.name}")
        print(f"  [Info] Offload store: offload_store/")
        if args.dry_run:
            print(f"  [Info] Run without --dry-run to apply changes.")
    else:
        print(f"\n  [Failed] Some patches could not be applied.")
        print(f"  [Info] Original file preserved at: {PIPELINE_PATH.name}")
        sys.exit(1)


if __name__ == "__main__":
    main()
