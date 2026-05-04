#!/usr/bin/env python3
"""
Midway to Nowhere — Mesh Consensus Pipeline Orchestrator
========================================================
Multi-agent mesh with full inter-agent communication, recursive sub-task
decomposition, dissent protocol (VETO/OBJECT/RECOURSE), double-check loops,
consensus gate, review-fix cycles, and failure report generation.

Usage:
    python pipeline.py "add a jackpot feature to the plinko attraction"
    python pipeline.py --checkpoint <id> "continue from checkpoint"

Output: streams to terminal + saves to pipeline_output_<timestamp>.md
"""

import json
import os
import re
import sys
import urllib.request
import urllib.error
import shutil
import subprocess
import textwrap
from datetime import datetime
from pathlib import Path
from collections import deque
import hashlib
import contextlib

# Import snapshot manager (optional — pipeline works without it)

try:
    from pipeline_snapshot import SnapshotManager
    HAS_SNAPSHOT = True
except ImportError:
    SnapshotManager = None
    HAS_SNAPSHOT = False

# ── Configuration ──────────────────────────────────────────────────────────
OLLAMA_HOST = "http://192.168.0.16:11434"

# ── Hybrid Model Registry (12GB VRAM Budget) ──────────────────────────────
# Primary (7-8B models for all real work — power over speed):
#   Coder — qwen2.5-coder:7b           : C++/Lua code generation
#   Reviewer — phi3:14b                : Lead Producer, review, consensus
#   Librarian — llama3.1:8b-instruct-q4_K_M : Read-only research
#
# Micro-models (1-1.5B for routing/syntax — no reasoning needed):
#   Syntax Gate — qwen2.5-coder:1.5b  : Pre-flight syntax pass/fail
#   Intent Classifier — llama3.2:1b    : Zero-shot MODIFICATION vs QUERY
CODER_MODEL = "qwen2.5-coder:7b"
REVIEWER_MODEL = "phi3:14b"                    # Corrected tag
FALLBACK_REVIEWER_MODEL = "llama3.1:8b-instruct-q4_K_M" # kept as fallback reference
LIBRARIAN_MODEL = "llama3.1:8b-instruct-q4_K_M"
SYNTAX_GATE_MODEL = "qwen2.5-coder:1.5b"       # Corrected tag
INTENT_CLASSIFIER_MODEL = "llama3.2:1b"        # Corrected tag
CHAT_MODEL = CODER_MODEL                       # Conversational responses use the coder (qwen2.5-coder:7b)
EXECUTION_MODEL = CODER_MODEL
REASONING_MODEL = REVIEWER_MODEL
MODEL = EXECUTION_MODEL          # default fallback for call_ollama when no model kwarg
DIRECTOR_MODEL = "llama3.1:8b-instruct-q4_K_M" # Decoupled from REVIEWER_MODEL to prevent confirmation bias
PROJECT_ROOT = Path(__file__).parent.resolve()
MAX_ITERATIONS = 3               # Steam Deck: 3 is industry standard for self-correction
MAX_CONSENSUS_ITERATIONS = 3
MAX_SUBTASKS_PER_AGENT = 5
REVIEW_MAX_ITERATIONS = 3        # Max review→fix→re-review cycles
SCOPE_FILE_LIMIT = 5             # Max files before Lead Producer deems "TOO_BROAD" (bumped for 32K ctx)
SCOPE_LINE_LIMIT = 400           # Max lines before Lead Producer deems "TOO_BROAD" (bumped for 32K ctx)
OLLAMA_TIMEOUT = 420
OLLAMA_NUM_CTX = 32768           # 32K context with KV cache q8_0 fits within 12GB alongside 7-8B models
MAX_TOKENS = 12000                # Client-side token packing ceiling (bumped for 32K ctx)
CHECKPOINT_DIR = PROJECT_ROOT / ".pipeline_checkpoints"
MEMORY_DIR = PROJECT_ROOT / "docs" / "memory"

# Standard ledger rule appended to every agent system prompt
LEDGER_MEMORY_RULE = (
    "\n\n---\n"
    "MEMORY LEDGER PROTOCOL:\n"
    "You have a persistent disk-based memory ledger at `docs/memory/<domain>_ledger.md`.\n"
    "Whenever you establish a new core loop, define a global variable, or finalize an "
    "architectural decision, you MUST output a markdown block to be appended to your ledger.\n"
    "Every entry MUST be indexed with a specific Markdown header (e.g., ### [ModuleName]).\n"
    "The orchestrator automatically writes these blocks to your ledger file.\n"
    "Use [FETCH:docs/memory/<domain>_ledger.md#<HeaderName>] to retrieve past decisions "
    "that have fallen out of your active context window.\n"
)

# ── Token Budget Tracker ────────────────────────────────────────────────────
# Steam Deck / 32K total context window enforcement

class TokenBudget:
    """Track and enforce token budget across pipeline execution.

    Industry standard pattern: measure before send, truncate before overflow.
    For qwen2.5-coder:7b, estimate 1 token ≈ 3 chars on average (code-heavy).
    """

    def __init__(self, hard_limit: int = MAX_TOKENS):
        self.hard_limit = hard_limit
        self.used = 0
        self.warnings = []

    def estimate_tokens(self, text: str) -> int:
        """Rough token estimate with density-aware safety margin.

        For normal code/text, uses 1 token per 3 chars (standard heuristic).
        For dense data (base64, hex arrays, Unicode) where alphanumeric
        density > 60%, uses a conservative 1.5 char/token ratio to prevent
        Ollama truncating the JSON request mid-stream (Token Budget Ratio Overflow).
        """
        sample = text[:2000]
        if sample:
            alpha_count = sum(1 for c in sample if c.isalnum())
            density = alpha_count / len(sample)
            if density > 0.60:
                return len(text) // 2  # 1.5 char/token conservative
        return len(text) // 3  # default: 1 token per 3 chars

    def check(self, text: str) -> bool:
        """Check if adding text would exceed budget. Returns True if safe."""
        estimated = self.estimate_tokens(text)
        return (self.used + estimated) < self.hard_limit

    @staticmethod
    def _block_aware_collapse(text: str, available_chars: int) -> str:
        """AST-aware / block-aware truncation.

        Instead of blindly keeping head+tail (which destroys function logic
        and agent reasoning in the middle), this method:
        1. Splits text into structural blocks (C++ functions, Lua blocks,
           Markdown headers, code fences)
        2. Preserves all block headers/signatures
        3. Collapses internal bodies with a [... collapsed ...] notice
        4. Drops the oldest blocks first (bottom of stack) when budget is tight

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
                    # Cannot fit at all — drop this block (oldest)
                    pass
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
                # Not even the header fits — drop this and all remaining older blocks
                # (We're iterating reversed, so these are the oldest blocks)
                dropped_count = 0
                for older_blk in blocks[:blocks.index(blk)]:
                    if not older_blk.get("_is_preamble"):
                        dropped_count += 1
                if dropped_count > 0:
                    # Insert a note about dropped blocks
                    if collapsed_blocks:
                        collapsed_blocks.insert(0, {
                            "header": None,
                            "body_lines": [self._build_offload_placeholder(
                                dropped_count,
                                blocks[:blocks.index(blk)]
                            )],
                            "_is_preamble": True,
                            "is_fence_block": False,
                        })
                # Offload all dropped blocks
                for older_blk in blocks[:blocks.index(blk)]:
                    if not older_blk.get("_is_preamble"):
                        self._offload_single_block(older_blk)
                self._offload_single_block(blk)
                break  # Stop: remaining blocks are too old to keep

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
        """Persist a single block to the offload store."""
        try:
            store = get_offload_store()
        except (NameError, ImportError):
            store = OffloadStore()
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
        success = store.store_block(block_id, header, body_lines)
        if success:
            print(f"  [OffloadStore] Offloaded block '{block_id}'")

    def _build_offload_placeholder(self, count: int, offloaded_blocks: list) -> str:
        """Build an actionable <OFFLOADED_CONTEXT> placeholder tag."""
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
            for preview_ln in ([header] if header else []) + body_lines[:9]:
                preview_lines.append(preview_ln.rstrip())
        preview = "\n".join(preview_lines[:10])
        block_list = "', '".join(block_ids)
        placeholder = (
            "\n<OFFLOADED_CONTEXT>\n"
            f"**{count} block(s) offloaded to disk -- context preserved losslessly.**\n\n"
            "Preview (first 10 lines):\n"
            f"```\n{preview}\n```\n\n"
            f"Offloaded block IDs: '{block_list}'\n"
            "To retrieve full content, use:\n"
            f"  [READ_OFFLOADED:{block_ids[0] if block_ids else 'block_id'}]\n"
            "</OFFLOADED_CONTEXT>\n"
        )
        return placeholder

    def add(self, text: str, label: str = "") -> str:
        """Add text to budget with block-aware truncation.

        Uses _block_aware_collapse to preserve function signatures and
        markdown headers even when truncating. Latest content is always
        preserved; oldest blocks are dropped first.

        Returns (possibly truncated) text.

        BLOCK-AWARE GUARANTEE: This method NEVER does raw head/tail slicing.
        All truncation goes through _block_aware_collapse which preserves
        structural boundaries (function signatures, markdown headers, code fences).
        """
        estimated = self.estimate_tokens(text)
        if self.used + estimated < self.hard_limit:
            self.used += estimated
            return text

        # ── Overflow Ledger Rotation ─────────────────────────────────
        overflow_path = PROJECT_ROOT / "docs" / "memory" / "overflow_ledger.md"
        # Overflow rotation threshold: 200 KB (bumped for 32K context budget)
        if overflow_path.is_file() and overflow_path.stat().st_size > 200 * 1024:
            archive_dir = overflow_path.parent
            existing = sorted(archive_dir.glob("overflow_ledger_v*.md"))
            highest_vol = 0
            for f in existing:
                vm = re.search(r"_v(\d+)\.md$", f.name)
                if vm:
                    vol = int(vm.group(1))
                    if vol > highest_vol:
                        highest_vol = vol
            new_vol = highest_vol + 1
            archive_name = f"overflow_ledger_v{new_vol}.md"
            archive_path = archive_dir / archive_name
            overflow_path.rename(archive_path)
            fresh_content = (
                f"### [Archive Link]\n"
                f"Continued from {archive_name}\n\n"
            )
            overflow_path.write_text(fresh_content, encoding="utf-8")
            print(f"  [Overflow Rotation] Rotated to {archive_name}. Active file reset.")

        # Block-aware collapse (STRICTLY block-aware — no blind head/tail fallback)
        available = self.hard_limit - self.used
        if available <= 100:
            self.warnings.append(f"[Budget] {label}: OVERFLOW — no room available")
            return f"\n[TOKEN BUDGET EXCEEDED: {label} truncated]\n"

        # Estimate chars-per-token ratio based on text characteristics
        code_indicators = sum(1 for c in text if c in "{}().;:#") / max(len(text), 1)
        chars_per_token = 3.0 if code_indicators > 0.05 else 4.0
        available_chars = int(available * chars_per_token)

        # ── BLOCK-AWARE COLLAPSE (ONLY truncation path) ──────────────
        truncated = self._block_aware_collapse(text, available_chars)
        self.used += available
        self.warnings.append(f"[Budget] {label}: truncated {estimated} → {available} tokens (block-aware)")
        return truncated

    def status(self) -> str:
        pct = (self.used / self.hard_limit) * 100
        warnings = ""
        if self.warnings:
            warnings = "\n" + "\n".join(self.warnings[-3:])
        return f"Token budget: {self.used}/{self.hard_limit} ({pct:.0f}%){warnings}"



# ---- Virtual Context: OffloadStore -------------------------------------------
# Disk-based overflow buffer -- inspired by OS memory paging.
# When _block_aware_collapse prunes old context blocks, they are serialized to
# offload_store/ rather than permanently deleted. Agents can retrieve them
# via [READ_OFFLOADED:<block_id>] signals.

class OffloadStore:
    """Disk-backed overflow buffer for pruned context blocks."""

    STORE_DIR = PROJECT_ROOT / "offload_store"

    def __init__(self):
        self.store_dir = self.STORE_DIR
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self.store_dir / "_index.json"
        self.index = self._load_index()
        self._max_mb = 512

    def _load_index(self) -> dict:
        if self._index_path.is_file():
            try:
                return json.loads(self._index_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save_index(self) -> None:
        try:
            self._index_path.write_text(
                json.dumps(self.index, indent=2, default=str), encoding="utf-8")
        except OSError as e:
            print(f"  [OffloadStore] !! Could not write index: {e}")

    def _block_path(self, block_id: str) -> Path:
        safe_id = re.sub(r'[^a-zA-Z0-9_\-]', '_', block_id)
        return self.store_dir / f"block_{safe_id}.json"

    def store_block(self, block_id: str, header: str,
                    body_lines: list, metadata: dict = None) -> bool:
        full_text = (header + "\n" + "\n".join(body_lines)) if header else "\n".join(body_lines)
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
            self.garbage_collect(self._max_mb)
            return True
        except OSError as e:
            print(f"  [OffloadStore] !! Failed to store block '{block_id}': {e}")
            return False

    def retrieve_block(self, block_id: str) -> str:
        path = self._block_path(block_id)
        if not path.is_file():
            return (
                "\n## Offloaded Context Retrieval: ERROR\n"
                f"**Block ID:** {block_id}\n"
                "**Error:** Block not found in offload store.\n"
            )
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return (
                "\n## Recalled Offloaded Context\n"
                f"**Block ID:** {block_id}\n"
                f"**Stored:** {data.get('timestamp', 'unknown')}\n"
                f"**Size:** {data.get('char_count', 0)} chars "
                f"({data.get('token_estimate', 0)} tokens)\n"
                f"**Header:** {data.get('header', '(no header)')[:120]}\n"
                f"---\n{data.get('full_text', '')}\n"
            )
        except (json.JSONDecodeError, KeyError, OSError) as e:
            return (
                "\n## Offloaded Context Retrieval: ERROR\n"
                f"**Block ID:** {block_id}\n"
                f"**Error:** Corrupt block data: {e}\n"
            )

    def delete_block(self, block_id: str) -> bool:
        path = self._block_path(block_id)
        try:
            if path.is_file():
                path.unlink()
            self.index.pop(block_id, None)
            self._save_index()
            return True
        except OSError as e:
            print(f"  [OffloadStore] !! Failed to delete block '{block_id}': {e}")
            return False

    def list_stored_blocks(self) -> list:
        return [
            {"block_id": bid,
             "header_preview": info.get("header", "")[:80],
             "char_count": info.get("char_count", 0),
             "token_estimate": info.get("token_estimate", 0),
             "timestamp": info.get("timestamp", "")}
            for bid, info in sorted(
                self.index.items(),
                key=lambda x: x[1].get("timestamp", ""),
                reverse=True,
            )
        ]

    def store_size(self) -> int:
        total = 0
        if self.store_dir.is_dir():
            for f in self.store_dir.glob("block_*.json"):
                try:
                    total += f.stat().st_size
                except OSError:
                    pass
        return total

    def garbage_collect(self, max_mb: int = 512) -> int:
        max_bytes = max_mb * 1024 * 1024
        current = self.store_size()
        if current <= max_bytes:
            return 0
        target = int(max_bytes * 0.8)
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
            print(f"  [OffloadStore] GC: evicted {evicted} blocks "
                  f"({current // 1024} KB -> {self.store_size() // 1024} KB)")
        return evicted


_OFFLOAD_STORE = OffloadStore()
def get_offload_store() -> OffloadStore:
    """Return the singleton OffloadStore instance."""
    return _OFFLOAD_STORE


# ── LRU Doc Cache ───────────────────────────────────────────────────────────
# Cache API doc reads in memory so redundant lookups don't hammer disk or
# blow context with repeated reads.

_DOC_CACHE = {}       # path -> (content, timestamp)
_DOC_CACHE_TTL = 300  # 5 minutes
_DOC_CACHE_MAX = 8    # max entries before eviction

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


# ── Session Timeline — Reverse-Chronological Audit Log ──────────────────────
# Every system action is logged here in reverse chronological order so the
# most recent entries appear first in the model's token budget window.
SESSION_TIMELINE_PATH = PROJECT_ROOT / "docs" / "memory" / "session_timeline.md"
_MAX_OUTPUT_CHARS = 4000            # bumped for 32K context budget

def _normalize_fix_fingerprint(fix_input: str) -> str:
    """Compute a deterministic fingerprint of a 'fix' request BEFORE context truncation.


    This fingerprint is computed BEFORE _block_aware_collapse truncation happens
    inside call_ollama(). It normalizes out:
    - Cycle numbers (e.g., "(Cycle 2)" -> "(Cycle N)")
    - Collapsed/truncated code markers (defense-in-depth against artifacts from
      previous context window collapses)
    - Excessive blank lines (formatting noise)

    The normalized fingerprint is the canonical input for the Insanity Detector's
    seen_code_hashes set. This prevents the "Pre-Truncation Hash Validation" bug
    where _block_aware_collapse inside call_ollama() corrupts the hash by mutating
    the input before the LLM sees it, causing false negatives in the circuit breaker.
    """
    normalized = fix_input

    # 1. Normalize cycle numbers so identical fix requests hash the same
    normalized = re.sub(r'\bCycle \d+\b', 'Cycle N', normalized)

    # 2. Strip collapsed/truncated markers that may have leaked from prior context
    normalized = re.sub(
        r'\[\.\.\.\s*(?:body|function|content|code)?\s*(?:collapsed|truncated)\s*\.\.\.\]',
        '',
        normalized,
    )
    normalized = re.sub(r'\[\.\.\. final truncation \.\.\.\]', '', normalized)
    normalized = re.sub(r'\[\.\.\.\s*\d+\s*block\(s\)?\s*dropped.*?\]', '', normalized)

    # 3. Normalize whitespace: collapse multiple blank lines to one
    normalized = re.sub(r'\n{3,}', '\n\n', normalized)

    return normalized.strip()


def log_to_session_timeline(user_input: str, agent_assigned: str,

                            tools_accessed: str, final_output: str) -> None:
    """Append an entry to the session timeline in REVERSE chronological order.

    Newest entries are always at the TOP of the file so they appear first
    in the model's token budget. Uses a read-prepend-write pattern with
    memory-efficient single-file I/O (never loads more than one file into RAM).
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

    # Read existing content (if any) — thread-safe via Queue in the
    # streaming layer; no Lock needed here since call_ollama_streamed
    # / the generator pattern ensures sequential access.
    existing = ""
    if SESSION_TIMELINE_PATH.is_file():
        try:
            existing = SESSION_TIMELINE_PATH.read_text(encoding="utf-8")
        except Exception:
            existing = ""

    # Write to a .tmp file first, then rename atomically. This prevents
    # partial writes if the process crashes mid-write (the target file
    # is never left in an incomplete state).
    import tempfile
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
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    print(f"  [SessionTimeline] Logged: {agent_assigned} @ {timestamp}")


# ── Doc Format: Anchor-TOC Builder ──────────────────────────────────────────
# Build cross-reference anchors so the model can cite exact source lines.
# Used by Code Documentarian persona.

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
    return "\n".join(toc_lines[:20])  # max 20 entries to save tokens


# ── Memory Ledger: Table of Contents Builder ───────────────────────────────
# Scans docs/memory/ and builds a TOC for each ledger file.
# Agents see what memory exists and use [FETCH] to pull specific sections.
# Smart truncation: never cuts mid-line, drops subsections first, then whole files.

BOILERPLATE_TITLES = {"Table of Contents", "Memory Bank", "Persistent memory bank"}

def _collect_ledger_entries(mem_file: Path) -> list:
    """Parse a ledger file into header-anchored chunks, then REVERSE so newest
    architectural decisions appear first in context. When token budget is tight,
    pruning drops the OLDEST entries at the bottom, preserving the most recent state.

    Returns (is_subsection, entry_text) pairs in REVERSE chronological order
    (newest first).
    """
    entries = []
    if not mem_file.is_file():
        return entries
    content = mem_file.read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines()

    # Phase 1: Split into logical chunks by header boundaries
    chunks = []  # list of {header_line, title, anchor, is_sub, body_lines}
    current_chunk = None

    for ln in lines:
        stripped = ln.strip()
        if stripped.startswith("##") or stripped.startswith("###"):
            # Close previous chunk
            if current_chunk is not None:
                chunks.append(current_chunk)
            title = stripped.lstrip("#").strip()
            if title in BOILERPLATE_TITLES:
                current_chunk = None
                continue
            # --- Explicit bracket-anchor extraction (no silent failures) ---
            bm = re.search(r"\[(.*?)\]", stripped)
            if bm:
                anchor = bm.group(1).strip()
            else:
                # Agent forgot brackets — fall back to slugified title
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

    # Flush last chunk
    if current_chunk is not None:
        chunks.append(current_chunk)

    # Phase 2: Reverse chunks — newest first (so token budget sees latest first)
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
                    (including ### subsections). Other ledgers show only ## headers.
    """
    mem_dir = MEMORY_DIR
    if not mem_dir.is_dir():
        return ""
    
    # Determine the agent's own ledger file
    own_ledger = ""
    if domain_key and domain_key in ALL_DOMAINS:
        own_ledger = ALL_DOMAINS[domain_key].get("ledger", "")
    
    HARD_LIMIT = 3000
    parts = ["## Memory Ledger Table of Contents\n"]
    
    # Sort: own ledger first (always top), then by last-modified timestamp (newest first),
    # so the absolute freshest ledger content appears earliest in context.
    ledger_files = sorted(mem_dir.glob("*_ledger.md"),
                          key=lambda f: (0 if own_ledger and f.name == Path(own_ledger).name else 1,
                                         -f.stat().st_mtime))
    
    for f in ledger_files:
        rel = f.relative_to(PROJECT_ROOT).as_posix()
        is_own = (rel == own_ledger)
        label = f"### {rel}"
        if is_own:
            label += " (YOUR LEDGER)"
        label += "\n"
        
        # Check if adding file header would exceed limit
        candidate = label
        if len("".join(parts)) + len(candidate) > HARD_LIMIT:
            parts.append(f"  - [... remaining ledgers omitted — use [FETCH] to retrieve ...]\n")
            break
        
        parts.append(candidate)
        
        entries = _collect_ledger_entries(f)
        for is_sub, entry_text in entries:
            # Skip subsections for non-own ledgers (save space)
            if is_sub and not is_own:
                continue
            if len("".join(parts)) + len(entry_text) > HARD_LIMIT:
                parts.append(f"  - [... deeper subsections omitted — use [FETCH] to retrieve ...]\n")
                break
            parts.append(entry_text)
    
    result = "".join(parts)
    return result


# ── Domain Availability ────────────────────────────────────────────────────
ALL_DOMAINS = {
    "C++": {
        "tag": "[C++]",
        "ready": True,
        "model": EXECUTION_MODEL,
        "ledger_rule": "MUST append signature to docs/internal_api_ledger.md",
        "description": "Engine architecture, physics integration, rendering, memory, Vicious Cycle seam, modifier system, object pools, booth lifecycle",
        "ledger": "docs/memory/cpp_ledger.md",
        "system_prompt": (
            "You are the C++17 systems engineer for 'Midway to Nowhere'. "
            "Write ONLY C++17. Use SDL2, OpenGL 3.3+, nlohmann/json. "
            "Be aware of the 'Vicious Cycle' spatial seam (teleporting bodies to Z=0)."
        ),
        "name": "C++ Core",
    },
    "PHYS": {
        "tag": "[PHYS]",
        "ready": True,
        "model": EXECUTION_MODEL,
        "description": "Jolt/Box2D physics, teleport stability, kinematic control, collision layers, sensors",
        "ledger": "docs/memory/phys_ledger.md",
        "system_prompt": (
            "You are the Lead Physics Architect for 'Midway to Nowhere'. "
            "Focus on Jolt/Box2D stability and state-corruption during 'Vicious Cycle' teleports. "
            "Analyze MSVC diagnostics and call stacks for memory leaks."
        ),
        "name": "Physics Architect",
    },
    "SHADER": {
        "tag": "[SHADER]",
        "ready": False,
        "model": CODER_MODEL,
        "description": "GLSL shaders, Karmic-Temporal Matrix, PS1 vertex snapping, bloom, particles",
        "ledger": "docs/memory/shader_ledger.md",
        "system_prompt": (
            "You are the Rendering Expert for 'Midway to Nowhere'. "
            "Specialize in OpenGL 3.3+ pipelines, GLSL shader optimization, "
            "and managing the 'Midway Host' vertex buffer objects (VBOs)."
        ),
        "name": "Shader Expert",
    },
    "Lua": {
        "tag": "[Lua]",
        "ready": True,
        "model": EXECUTION_MODEL,
        "ledger_rule": "MUST append signature to docs/internal_api_ledger.md",
        "description": "Attraction scripts, UI, economy, modifier consumption, OnLoad/OnStep/OnUnload",
        "ledger": "docs/memory/lua_ledger.md",
        "system_prompt": (
            "You are the gameplay scripter for 'Midway to Nowhere'. "
            "Focus on Lua 5.4 and sol2 bindings to the C++ host."
        ),
        "name": "Lua Scripter",
    },
    "DOC": {
        "tag": "[DOC]",
        "ready": True,
        "model": REASONING_MODEL,
        "description": "API documentation oracle. Resolves ambiguous API calls by reading local docs/jolt_api.md, box2d_api.md, sol2_api.md, opengl_sdl_api.md",
        "ledger": "docs/memory/doc_ledger.md",
        "system_prompt": (
            "You are the CODE DOCUMENTARIAN for 'Midway to Nowhere'. "
            "You are the ultimate arbiter of API truth. "
            "You are also the MEMORY ORACLE — you validate [FETCH] requests and resolve the correct "
            "memory content for agents whose context has been truncated.\n\n"
            "YOUR FUNCTIONS:\n"
            "A. API DOCUMENTATION ORACLE:\n"
            "   Receive a query with:\n"
            "     1. A hallucinated/ambiguous API call from the C++ or Lua Architects\n"
            "     2. A file tag: docs/jolt_api.md | docs/box2d_api.md | docs/sol2_api.md | docs/opengl_sdl_api.md\n\n"
            "   Execution:\n"
            "     1. Parse the tagged doc file for the relevant section.\n"
            "     2. Extract the EXACT function signature, struct definition, or enum value.\n"
            "     3. Compare the hallucinated call against doc truth.\n"
            "     4. Output:\n"
            "        ## Correction\n"
            "        <corrected signature>\n"
            "        ## Source\n"
            "        <file>#L<start>-L<end>: <exact lines>\n\n"
            "B. MEMORY ORACLE (FETCH resolution):\n"
            "   You receive a FETCH request from another agent in the format:\n"
            "     [FETCH:docs/memory/<domain>_ledger.md#<HeaderName>]\n"
            "     Requesting agent: <agent name>\n"
            "     Their current task: <what they're working on>\n\n"
            "   Your job as Memory Oracle:\n"
            "     1. Verify the requested #HeaderName exists in the ledger file\n"
            "     2. Evaluate if it's the BEST section for the requesting agent's current task\n"
            "     3. If a better section exists (different header or different ledger), "
            "select that instead and explain why\n"
            "     4. If the header is not found, search for the nearest match (case-insensitive)\n"
            "     5. If the requesting agent is from a different domain (e.g., C++ reading Lua "
            "ledger), flag it as cross-domain and verify the intent is appropriate\n"
            "     6. Only output the **content** under the resolved header, formatted as:\n"
            "        ## Recalled Memory\n"
            "        **Source:** <resolved_filepath> > <resolved_header>\n"
            "        **Chronological Read Depth:** <temporal_depth>\n"
            "        **Oracle note:** Apply the following QA Heuristic:\n"
            "           - Cross-reference the memory with `docs/memory/qa_ledger.md`.\n"
            "           - If the QA Ledger states a system is currently BROKEN, and this memory is from an older, higher-depth archive, flag as **[VALID RESCUE]**: 'This deep-history memory is a valid rescue for a currently broken system.'\n"
            "           - If the QA Ledger states a system is WORKING, and this memory alters it, flag as **[HIGH-RISK REGRESSION]**: 'Warning: Alters a system the user explicitly marked as working.'\n"
            "           - If no QA anchor exists, use standard flags: **[REVERSION RISK]** (if it contradicts active ledgers) or **[STABLE CORE CONCEPT]** (if foundational).\n\n"
            "        <extracted content>\n\n"
            "BREVITY RULES:\n"
            "- No game logic. No features. No examples. No commentary beyond the Memory Oracle note.\n"
            "- Only the corrected signature and the source justification (API mode)\n"
            "- Only the extracted memory content with oracle note (Memory Oracle mode)"
        ),
        "name": "Code Documentarian",
    },
    "CONF": {
        "tag": "[CONF]",
        "ready": True,
        "model": REASONING_MODEL,
        "description": "Conflict resolution mediator. Resolves VETO/OBJECT disputes between agents.",
        "ledger": "docs/memory/conf_ledger.md",
        "system_prompt": (
            "You are the CONFLICT RESOLUTION AGENT for 'Midway to Nowhere'. "
            "Your role is to mediate disputes between specialized agents. "
            "You do NOT write code. You do NOT implement features.\n\n"
            "When receiving a VETO or OBJECT signal:\n"
            "1. Read the original code (Agent B)\n"
            "2. Read the modified code (Agent A)\n"
            "3. Read the VETO justification\n"
            "4. Read the original feature request\n"
            "5. Read the relevant rule file\n\n"
            "Decision options:\n"
            "- SUSTAIN VETO: Agent B's original is more correct for the feature intent. "
            "Agent A's suggestions are appended as notes.\n"
            "- OVERRULE VETO: Agent A's change is technically correct AND preserves feature intent. "
            "Explain why the VETO is overruled.\n"
            "- COMPROMISE: Neither is fully correct. Generate a merged version that satisfies both concerns.\n\n"
            "TEMPORAL HEURISTIC TIE-BREAKER:\n"
            "If the dispute involves memory fetched from the archives:\n"
            "- Favor the agent whose logic aligns with an active ledger or a memory tagged by the Oracle as a **Stable Core Concept** or **[VALID RESCUE]**.\n"
            "- Overrule or sustain against any agent whose logic relies on a memory tagged as a **Reversion Risk** or **[HIGH-RISK REGRESSION]**.\n\n"
            "CRITICAL RULE: Preserve feature intent over technical purity. "
            "If Agent A's 'fix' strips gameplay mechanics, narrative flavor, or modifier interactions, sustain the VETO."
        ),
        "name": "Conflict Resolution",
    },
    "LIBRARIAN": {
        "tag": "[LIBRARIAN]",
        "ready": True,
        "model": REASONING_MODEL,
        "description": "Read-only research agent — answers queries about past decisions, architecture, and memory ledgers without modifying any code",
        "ledger": "docs/memory/librarian_ledger.md",
        "system_prompt": (
            "You are the LIBRARIAN for 'Midway to Nowhere'. "
            "You are a read-only research agent. You answer questions strictly "
            "by navigating the project's structured Markdown memory ledgers.\n\n"
            "IMPORTANT CONSTRAINTS:\n"
            "1. You MUST NOT attempt to modify any code or files.\n"
            "2. You MUST answer ONLY based on information found in the provided memory documents.\n"
            "3. If you cannot find the answer in the memory documents, say so honestly.\n"
            "4. You have access to a search_memory tool that gives you the Memory Ledger "
            "Table of Contents. Use it to identify which ledger sections are relevant, "
            "then use [FETCH:docs/memory/<file>.md#<HeaderName>] to retrieve specific entries.\n\n"
            "RESEARCH METHODOLOGY:\n"
            "- First, scan the Memory Ledger Table of Contents to identify relevant sections.\n"
            "- Then retrieve specific sections using [FETCH:path#anchor] tags.\n"
            "- Synthesize the findings into a clear answer.\n"
            "- Cite your sources (which ledger file and section header)."
        ),
        "name": "Librarian",
    },
}

# ── Agent Role String Normalization ──────────────────────────────────────────
# Maps conversational/variant role strings from the Director LLM to canonical
# ALL_DOMAINS keys. The Director is unpredictable and may output "C++ Core",
# "Physics", "the C++ engineer", "LUA", "cpp", etc. — when resolve_agent_name()
# can't find a match, the raw string falls through and produces a 404 Model
# Not Found error because ALL_DOMAINS.get(bad_name) returns None.
#
# This alias map is checked FIRST in resolve_agent_name(), before the existing
# direct/name/partial match logic, so even novel phrasing from the LLM maps
# to a valid domain key.
AGENT_ALIAS_MAP = {
    # C++ domain
    "c++": "C++",
    "c++ core": "C++",
    "c++ engineer": "C++",
    "c++ systems engineer": "C++",
    "cpp": "C++",
    "cpp core": "C++",
    "engine": "C++",
    "engine architect": "C++",
    "engine systems": "C++",
    "engine programmer": "C++",
    "systems engineer": "C++",
    "c++ programmer": "C++",
    # Physics domain
    "phys": "PHYS",
    "physics": "PHYS",
    "physics architect": "PHYS",
    "physics engineer": "PHYS",
    "jolt physics": "PHYS",
    "box2d": "PHYS",
    "physics systems": "PHYS",
    "lead physics": "PHYS",
    "physics programmer": "PHYS",
    "teleport physics": "PHYS",
    # Shader domain
    "shader": "SHADER",
    "shader expert": "SHADER",
    "glsl": "SHADER",
    "rendering": "SHADER",
    "rendering expert": "SHADER",
    "graphics": "SHADER",
    "graphics programmer": "SHADER",
    "opengl": "SHADER",
    "shader programmer": "SHADER",
    "render": "SHADER",
    # Lua domain
    "lua": "Lua",
    "lua scripter": "Lua",
    "lua gameplay": "Lua",
    "gameplay scripter": "Lua",
    "scripting": "Lua",
    "lua programmer": "Lua",
    "attractions script": "Lua",
    "attraction scripting": "Lua",
    "gameplay": "Lua",
    "lua engineer": "Lua",
    "lua developer": "Lua",
    # Doc domain
    "doc": "DOC",
    "documentarian": "DOC",
    "code documentarian": "DOC",
    "memory oracle": "DOC",
    "api oracle": "DOC",
    "documentation": "DOC",
    "api doc": "DOC",
    "code doc": "DOC",
    # Conflict domain
    "conf": "CONF",
    "conflict resolution": "CONF",
    "mediator": "CONF",
    "conflict mediator": "CONF",
    "conflict agent": "CONF",
    "dispute resolution": "CONF",
    # Reviewer domain
    "reviewer": "REVIEWER",
    "integration reviewer": "REVIEWER",
    "review": "REVIEWER",
    "lead reviewer": "REVIEWER",
    "code review": "REVIEWER",
    "integration review": "REVIEWER",
    "qa review": "REVIEWER",
    # Librarian domain
    "librarian": "LIBRARIAN",
    "research": "LIBRARIAN",
    "read only": "LIBRARIAN",
    "memory research": "LIBRARIAN",
    "memory librarian": "LIBRARIAN",
    "information retrieval": "LIBRARIAN",
    # Director domain
    "director": "DIRECTOR",
    "project director": "DIRECTOR",
    "lead producer": "DIRECTOR",
    "producer": "DIRECTOR",
    "task decomposer": "DIRECTOR",
    # Diagnostic domain
    "diagnostic": "DIAGNOSTIC",
    "diagnostic oracle": "DIAGNOSTIC",
    "qa": "DIAGNOSTIC",
    "qa oracle": "DIAGNOSTIC",
    "debug": "DIAGNOSTIC",
    "debug engineer": "DIAGNOSTIC",
    # Syntax Gate domain
    "syntax gate": "SYNTAX_GATE",
    "syntax": "SYNTAX_GATE",
    "preflight": "SYNTAX_GATE",
    "syntax checker": "SYNTAX_GATE",
    # Intent Classifier domain
    "intent classifier": "INTENT_CLASSIFIER",
    "intent": "INTENT_CLASSIFIER",
    "router": "INTENT_CLASSIFIER",
    "intent router": "INTENT_CLASSIFIER",
}

# Build PERSONA_MAP from ready domains only
PERSONA_MAP = {}
for key, domain in ALL_DOMAINS.items():
    if domain["ready"]:
        PERSONA_MAP[key] = {"system": domain["system_prompt"], "name": domain["name"]}
        PERSONA_MAP[key.lower()] = {"system": domain["system_prompt"], "name": domain["name"]}


# ── Reasoning Gate Configuration ────────────────────────────────────────────
# Intra-agent reasoning gate: after a coder agent produces output in Phase 4
# (Mesh Execution), a self-review step verifies the output before it reaches
# signal processing and disk-write. Only applies to domains where correctness
# is critical (C++ codegen, Lua scripting, Physics architecture).
REASONING_GATE_DOMAINS = {"C++", "Lua", "PHYS"}
REASONING_GATE_SYSTEM = (
    "You are a SELF-REVIEW REASONING GATE. "
    "Your role is to critically examine the following GENERATED OUTPUT in "
    "the context of the ORIGINAL TASK SPECIFICATION and identify any errors, "
    "misunderstandings, hallucinations, or missing pieces. "
    "You are not creating new code — you are auditing.\n\n"
    "Check specifically for:\n"
    "1. Hallucinated API calls or functions that do not exist\n"
    "2. Violations of project rules (C++, Lua, Physics)\n"
    "3. Logic errors that would cause crashes or undefined behavior\n"
    "4. Incomplete implementations that leave placeholder/TODO markers\n"
    "5. Off-target responses that do not address the task specification\n\n"
    "OUTPUT FORMAT:\n"
    "- If the output is CORRECT and fully addresses the task, start with: CONFIRMED\n"
    "  Then repeat the original output unchanged.\n"
    "- If the output has ERRORS that need fixing, start with: REVISED\n"
    "  Then output a corrected version of the output.\n"
    "- If the output is fundamentally wrong or hallucinated, start with: REVISED\n"
    "  Then output a replacement that correctly addresses the task.\n\n"
    "Be strict but fair. Minor style issues do NOT warrant a REVISED verdict."
)


# ── System Prompts ─────────────────────────────────────────────────────────

DIRECTOR_SYSTEM = (
    "You are the PROJECT DIRECTOR for 'Midway to Nowhere' game project. "
    "Your ONLY job: decompose feature requests into 1-5 tasks, each tagged with an available domain. "
    "Output ONLY the task list. NO code. NO explanations. NO commentary."
) + (
    "\n\n---\n"
    "MEMORY LEDGER PROTOCOL:\n"
    "Your assigned memory ledger: docs/memory/architecture_ledger.md\n"
    "Whenever you finalize a task decomposition or architectural decision, "
    "you MUST output a markdown block to be appended to your ledger.\n"
    "Every entry MUST be indexed with a specific Markdown header (e.g., ### [ModuleName]).\n"
    "Use [FETCH:docs/memory/architecture_ledger.md#<HeaderName>] to retrieve past decisions.\n"
)

REVIEW_SYSTEM = (
    "You are the INTEGRATION REVIEWER for 'Midway to Nowhere'. "
    "Your ONLY job: review generated code against engine rules and identify issues. "
    "Do NOT write code. Do NOT fix problems. "
    "End your review with **PASS** or **FAIL** on its own line."
) + LEDGER_MEMORY_RULE

REVIEW_PROMPT = (
    "Review the generated code below. Check for:\n"
    "1. Cross-domain issues: C++ bridge <-> Lua calls\n"
    "2. Rule compliance: Check against docs/rules_cpp.md, rules_lua.md, rules_phys.md, rules_shader.md\n"
    "3. Vicious Cycle consistency: C++ applies teleport, Lua does not decide\n"
    "4. Modifier system: All 9 values synced across all layers\n"
    "5. Error handling: No raw pointers, server-authoritative economy\n"
    "6. Temporal Drift: Ensure the code does not implement deprecated patterns flagged by the Oracle as a **Reversion Risk** or **[HIGH-RISK REGRESSION]**.\n\n"
    "OUTPUT FORMAT:\n"
    "## Integration Review\n"
    "### Issues\n"
    "- Issue 1: ...\n"
    "### Verdict\n"
    "**PASS** or **FAIL**\n\n"
    "End with **PASS** or **FAIL** on its own line."
)

FINAL_APPROVAL_SYSTEM = (
    "You are the PROJECT DIRECTOR for 'Midway to Nowhere'. "
    "Review the completed work and either APPROVE or request REVISIONS. "
    "Start your response with **APPROVED** or **REVISION REQUIRED**."
) + LEDGER_MEMORY_RULE

SELF_CORRECT_SYSTEM = (
    "You are a code reviewer examining your own previous output. "
    "Identify errors, bugs, or missing pieces, then produce an improved version. "
    "If no issues found, state 'NO ISSUES FOUND' and repeat your previous output unchanged."
)

ARCHITECT_FIX_SYSTEM = (
    "You are the domain architect for 'Midway to Nowhere'. "
    "The Integration Reviewer has identified issues in your code. "
    "Fix ALL reported issues and produce corrected code. "
    "Address every issue the Reviewer raised. "
    "If you believe an issue is a false positive, explain why."
) + LEDGER_MEMORY_RULE

LIBRARIAN_SYSTEM = (
    "You are the GDD LIBRARIAN for 'Midway to Nowhere'. "
    "Your ONLY job: given a feature request, identify which sections of the "
    "Game Design Document (GDD) are relevant. Output ONLY the section names "
    "and a 1-sentence summary of why each is relevant. NO code. NO commentary."
)

DIAGNOSTIC_ORACLE_SYSTEM = (
    "You are the DIAGNOSTIC ORACLE for 'Midway to Nowhere'. "
    "Your job is to help the user investigate, modify, or delete entries in the memory ledgers. "
    "You are in a multi-turn conversation. Be concise.\n\n"
    "RULES:\n"
    "1. If you need the user to clarify or guide you further, you MUST end your response with the exact tag: [AWAITING_INPUT].\n"
    "2. If the user tells you to modify or delete an entry, you must execute the change. Output the ENTIRE updated ledger enclosed in a ```markdown code block, and end your response with the exact tag: [DIAGNOSTIC_RESOLVED]. Do NOT output [AWAITING_INPUT] if you are resolving the issue."
)

INTENT_ROUTER_SYSTEM = (
    "You are the INTENT ROUTER for 'Midway to Nowhere'. "
    "Analyze the user's prompt and determine their primary goal.\n"
    "If the user is asking to build, add, fix, or modify game features and code, output exactly 'DEVELOPMENT'.\n"
    "If the user is asking to check logs, examine memory, review system status, debug an abstract issue, or fix a rule, output exactly 'DIAGNOSTIC'."
)

# ── Conversational Stream Bifurcation ──────────────────────────────────────
# Out-of-band chat layer. When the user asks a conversational question (not a
# MODIFICATION task, not a QUERY about ledgers), the pipeline routes directly
# to CHAT_SYSTEM. This bypasses the stringent rules_review.md consensus gate
# and LEDGER_MEMORY_RULE markdown requirements to prevent overzealous reviewer
# vetos on non-code output.
CHAT_SYSTEM = (
    "You are a knowledgeable game development assistant for 'Midway to Nowhere'. "
    "You answer questions conversationally about the codebase, architecture, and design. "
    "You may use the provided project context and file references to inform your answers.\n\n"
    "RULES:\n"
    "1. Respond conversationally — do NOT output code blocks unless specifically asked.\n"
    "2. Do NOT attempt to modify any files or write code.\n"
    "3. Do NOT output markdown ledgers or memory headers.\n"
    "4. If you need to reference code, explain it naturally.\n"
    "5. If you don't know the answer, say so honestly.\n"
    "6. Do NOT use [FETCH], [QUERY], or any mesh signals.\n"
    "7. Do NOT use [FILE_READ] or [FILE_LIST] tools.\n"
    "8. Do NOT include any ## Double-Check section.\n"
    "9. Your output will NOT be run through integration review — be helpful, not perfect."
)

# ── Chat Intent Detection — Fast-Path Regexes ─────────────────────────────────
# These patterns catch conversational prompts BEFORE the LLM classifier runs.
# This prevents a classification failure from sending a chat prompt through
# the full mesh pipeline (wasting the context budget on irrelevant code generation).
# Patterns match against lowercased, stripped input. Order matters: first match wins.
CHAT_PATTERNS = [
    # Greetings / preamble — no code intent
    r"^(hello|hi|hey|greetings|yo|sup)\b",
    r"^(what can you do|what do you do|how can you help)\b",
    # Requests for help/explanation (not code changes)
    r"(help|guide|explain|understand|walk me|tell me about|show me how)\s+(me|with|the|how|to|what)",
    r"can you (help|explain|tell|show|walk|guide)\s+me",
    r"how (do|does|can|would|should) (i|we|you|the)",
    r"what is (the|a|an|your|this)\b",
    # Non-technical / conversational
    r"(thanks|thank you|good|great|awesome|nice)\b",
    r"(just checking|just asking|just curious|by the way|btw)\b",
    r"(what'?s up|how'?s it going|how are you)",
]

SEARCH_MEMORY_SYSTEM = (
    "You are the Memory Ledger Navigator for 'Midway to Nowhere'. "
    "Your job is to compile and return a Table of Contents of all "
    "Markdown memory ledgers in docs/memory/. List each ledger file "
    "and its major sections with anchors."
)


def search_memory() -> str:
    """Search tool for the Librarian agent — reads the Memory Ledger Table of Contents.

    Returns a structured overview of all memory ledger files and their sections,
    exactly as a human would skim an index before retrieving a specific document.
    Lightweight: only reads file headers and section anchors — never loads full files.
    """
    toc = ledger_toc(domain_key="LIBRARIAN")
    if not toc:
        return "No memory ledger files found in docs/memory/."
    return toc



def is_likely_chat(user_prompt: str) -> bool:
    """Fast-path regex check -- catches conversational prompts BEFORE the LLM classifier runs.

    This prevents classification failures from sending a chat prompt through
    the full mesh pipeline, which wastes the context budget on irrelevant
    code generation and review loops.

    The patterns in CHAT_PATTERNS match against lowercased, stripped input.
    Returns True if any pattern matches -- the prompt will be routed to CHAT
    mode without running the LLM classifier.
    """
    prompt_lower = user_prompt.strip().lower()
    for pat in CHAT_PATTERNS:
        if re.search(pat, prompt_lower):
            return True
    return False



def is_likely_chat(user_prompt: str) -> bool:
    """Fast-path regex check -- catches conversational prompts BEFORE the LLM classifier runs.

    This prevents classification failures from sending a chat prompt through
    the full mesh pipeline, which wastes the context budget on irrelevant
    code generation and review loops.

    The patterns in CHAT_PATTERNS match against lowercased, stripped input.
    Returns True if any pattern matches -- the prompt will be routed to CHAT
    mode without running the LLM classifier.
    """
    prompt_lower = user_prompt.strip().lower()
    for pat in CHAT_PATTERNS:
        if re.search(pat, prompt_lower):
            return True
    return False


def classify_intent(user_prompt: str) -> str:
    """Zero-shot intent classification using the Director model (llama3.1:8b).

    Fast, lightweight inference — single LLM call, minimal context.
    Returns 'MODIFICATION', 'QUERY', or 'CHAT'.
    If the model response is ambiguous, defaults to MODIFICATION (safe path).
    """
    intent = call_ollama(
        INTENT_CLASSIFIER_SYSTEM,
        f"User prompt: '{user_prompt}'\n\nClassify as MODIFICATION, QUERY, or CHAT.",
        "Intent Classifier",
        DIRECTOR_MODEL,
    )
    intent_clean = intent.strip().upper()
    if "CHAT" in intent_clean:
        return "CHAT"
    if "QUERY" in intent_clean:
        return "QUERY"
    return "MODIFICATION"


MESH_AGENT_SYSTEM_EXTENSION = (
    "\n\n---\n"
    "MESH COMMUNICATION PROTOCOL:\n"
    "You may communicate with other agents by embedding signals in your output:\n"
    "- [QUERY:<target_agent>:<question>] — Ask another agent for information. "
    "The orchestrator will pause you, route the query, and inject the answer back.\n"
    "- [DELEGATE:<target_agent>:<sub_task_spec>] — Break off a sub-task (max 5 total). "
    "The orchestrator will execute it and return the result.\n"
    "- [RESULT:<summary>] — Summarize your output for other agents.\n"
    "- [APPROVE] — Signal you are satisfied with the current state.\n"
    "- [REVISE:<target_agent>:<reason>] — Request changes from another agent.\n"
    "- [VETO:<target_agent>:<reason>] — HARD BLOCK. Another agent modified your code "
    "in a way that breaks feature intent. This triggers conflict resolution.\n"
    "- [OBJECT:<target_agent>:<concern>] — Soft flag. Another agent's change has issues "
    "but is not blocking.\n"
    "- [RECOURSE:Director:<appeal>] — Appeal a VETO override to the Director.\n"
    "- [CONSULT:<target_agent>:<query>] — Request peer review.\n\n"
    "- [FETCH:<filepath>#<HeaderName>] — Recall context from your disk-based memory ledger. Does NOT count against iteration limit.\n"
    "DOUBLE-CHECK REQUIREMENT:\n"
    "At the end of your output, include:\n"
    "## Double-Check\n"
    "**Original prompt:** <truncated original request>\n"
    "**My output addresses:** <bullet points linking output back to prompt>\n"
    "**Unresolved items:** <anything from original prompt not yet addressed>\n"
    "If there are unresolved items, the orchestrator will give you another iteration."
)

# ── Signal Types ───────────────────────────────────────────────────────────

SIGNAL_PATTERNS = {
    "QUERY": r"\[QUERY:([^\]]+):([^\]]+)\]",
    "DELEGATE": r"\[DELEGATE:([^\]]+):([^\]]+)\]",
    "RESULT": r"\[RESULT:([^\]]+)\]",
    "APPROVE": r"\[APPROVE\]",
    "REVISE": r"\[REVISE:([^\]]+):([^\]]+)\]",
    "VETO": r"\[VETO:([^\]]+):([^\]]+)\]",
    "OBJECT": r"\[OBJECT:([^\]]+):([^\]]+)\]",
    "RECOURSE": r"\[RECOURSE:([^\]]+):([^\]]+)\]",
    "CONSULT": r"\[CONSULT:([^\]]+):([^\]]+)\]",
    "FETCH": r"\[FETCH:([^\]]+)#([^\]]+)\]",
}

# Multi-line double-check pattern: captures the remainder of the block after
# the three marked sections, allowing bullet items across lines.
DOUBLE_CHECK_PATTERN = (
    r"## Double-Check\s*\n"
    r"\*\*Original prompt:\*\*(.*?)(?:\n|$)"
    r"\s*\*\*My output addresses:\*\*(.*?)(?:\n|$)"
    r"\s*\*\*Unresolved items:\*\*(.*?)(?:\n##|\n---|\Z)"
)


# ── GDD Librarian ──────────────────────────────────────────────────────────

GDD_PATH = PROJECT_ROOT / "GDD" / "Midway_to_Nowhere_Master_GDD_v19.md"

GDD_SECTION_MAP = {
    "executive":       {"start": 24, "end": 33, "label": "1. Executive Summary & Core Architecture"},
    "technical":       {"start": 34, "end": 52, "label": "2. Technical Backbone: Custom C++ Engine"},
    "economy":         {"start": 53, "end": 57, "label": "3. Dual-Currency Economy & Streak Protocol"},
    "modifier":        {"start": 58, "end": 73, "label": "4. Global Identity Stats & Modifiers"},
    "loot":            {"start": 74, "end": 78, "label": "5. Augmentation & Bribery Loop"},
    "pacing":          {"start": 79, "end": 103, "label": "6. Pacing, Onboarding, Meta-Progression"},
    "concessions":     {"start": 104, "end": 108, "label": "7. Concessions & Utility Stalls"},
    "coin cascade":    {"start": 113, "end": 118, "label": "8. The Coin Cascade"},
    "plinko":          {"start": 119, "end": 124, "label": "8. Purgatorial Plinko"},
    "claw":            {"start": 125, "end": 130, "label": "8. The Claw of Condemnation"},
    "slingshot":       {"start": 131, "end": 136, "label": "8. The Slingshot Array"},
    "ring toss":       {"start": 137, "end": 142, "label": "8. The Ring Toss"},
    "crumbling":       {"start": 143, "end": 150, "label": "8. The Crumbling Façade"},
    "future":          {"start": 151, "end": 160, "label": "9. Midway Expansion Catalog"},
    "boss":            {"start": 161, "end": 244, "label": "10. Identity Loadouts & Boss Encounters"},
    "endgame":         {"start": 245, "end": 248, "label": "11. Terminal Wagers & True Endgame"},
    "narrative":       {"start": 249, "end": 331, "label": "12-13. Narrative & Origins"},
    "roadmap":         {"start": 332, "end": 336, "label": "14. Solo Developer Roadmap"},
}

KEYWORD_TO_SECTION = {
    "plinko": "plinko", "coin cascade": "coin cascade", "coin": "coin cascade",
    "cascade": "coin cascade", "crumbling": "crumbling", "facade": "crumbling",
    "claw": "claw", "slingshot": "slingshot", "ring toss": "ring toss",
    "ring": "ring toss", "economy": "economy", "token": "economy",
    "ticket": "economy", "currency": "economy", "modifier": "modifier",
    "stats": "modifier", "karma": "modifier", "boss": "boss", "bosses": "boss",
    "encounter": "boss", "narrative": "narrative", "story": "narrative",
    "lore": "narrative", "engine": "technical", "c++": "technical",
    "physics": "technical", "rendering": "technical", "shader": "technical",
    "lua": "technical", "pacing": "pacing", "onboarding": "pacing",
    "meta": "pacing", "concession": "concessions", "food": "concessions",
    "loot": "loot", "prize": "loot", "augment": "loot", "future": "future",
    "expansion": "future", "endgame": "endgame", "terminal": "endgame",
    "roadmap": "roadmap", "executive": "executive", "summary": "executive",
}


def get_project_state() -> str:
    """Reads docs/completed_features.md and docs/todo.md for project summary."""
    lines = ["## Current Project State\n"]
    completed_path = PROJECT_ROOT / "docs" / "completed_features.md"
    if completed_path.is_file():
        try:
            text = completed_path.read_text(encoding="utf-8", errors="replace")
            done_sections = re.findall(r"### ✅ (.+)", text)
            if done_sections:
                lines.append("### ✅ Implemented Systems")
                for s in done_sections[:15]:
                    lines.append(f"- {s}")
                if len(done_sections) > 15:
                    lines.append(f"- ... ({len(done_sections) - 15} more)")
                lines.append("")
        except Exception:
            pass
    todo_path = PROJECT_ROOT / "docs" / "todo.md"
    if todo_path.is_file():
        try:
            text = todo_path.read_text(encoding="utf-8", errors="replace")
            not_started = re.findall(r"\|\s*\d+\s*\|.*?\| ⬜ (?:Open|Next)\s*\|(.+?)\|", text)
            if not_started:
                lines.append("### ⬜ Not Yet Implemented")
                for s in not_started[:10]:
                    lines.append(f"- {s.strip()}")
                if len(not_started) > 10:
                    lines.append(f"- ... ({len(not_started) - 10} more)")
                lines.append("")
        except Exception:
            pass
    lines.append("### 🟢 Available Domains")
    for key, domain in ALL_DOMAINS.items():
        if domain["ready"]:
            lines.append(f"- {domain['tag']} {domain['description']}")
    lines.append("")
    lines.append("### 🔴 Unavailable Domains")
    for key, domain in ALL_DOMAINS.items():
        if not domain["ready"]:
            lines.append(f"- {domain['tag']} {domain['description']}")
    lines.append("")
    lines.append("### ❌ Does NOT Exist")
    lines.append("- No networking/multiplayer code at all")
    lines.append("- No Box2D physics integration")
    lines.append("- No audio engine (SoLoud not integrated)")
    lines.append("- No save/load system")
    lines.append("- No boss encounters")
    lines.append("- No prize/augment runtime loading")
    lines.append("- No Barker billboarding system")
    lines.append("")
    return "\n".join(lines)


def get_available_domains_text() -> str:
    parts = []
    for key, domain in ALL_DOMAINS.items():
        if domain["ready"]:
            parts.append(f"- {domain['tag']} {domain['description']}")
    return "\n".join(parts)


def get_unavailable_domains_text() -> str:
    parts = []
    for key, domain in ALL_DOMAINS.items():
        if not domain["ready"]:
            parts.append(f"- {domain['tag']} {domain['description']}")
    return "\n".join(parts)


def build_director_prompt() -> str:
    available = get_available_domains_text()
    unavailable = get_unavailable_domains_text()
    return (
        "Decompose this feature request into 1-5 tasks. "
        "Each task must have a domain tag and a short title.\n\n"
        "IMPORTANT: You may assign as FEW as 1 task or as MANY as 5. "
        "Only create tasks that are absolutely necessary.\n\n"
        "AVAILABLE DOMAINS (use ONLY these):\n"
        f"{available}\n\n"
        "UNAVAILABLE DOMAINS (do NOT use these):\n"
        f"{unavailable}\n\n"
        "RULES:\n"
        "- Do NOT use [NET] — there is no networking code in the project.\n"
        "- Do NOT use [SHADER] — shader effects are not yet implemented.\n"
        "- Do NOT assign [Lua] tasks that write network code.\n"
        "- Only assign a domain if the project actually has code for it.\n\n"
        "Order by dependency: [C++] first, then [PHYS], then [Lua].\n\n"
        "OUTPUT FORMAT (exactly):\n"
        "## Task Breakdown: [Feature Name]\n"
        "### Task 1: [DOMAIN] - [Short Title]\n"
        "### Task 2: [DOMAIN] - [Short Title]\n"
        "...\n\n"
        "CRITICAL: Do NOT write any code. Only list tasks."
    )


def curate_project_structure(prompt: str) -> str:
    """Scan the project directory tree for files relevant to the prompt."""
    prompt_lower = prompt.lower()
    relevant_dirs = set()
    dir_keywords = {
        "src": ["engine", "c++", "physics", "render", "shader", "attraction",
                "economy", "modifier", "model", "console", "debug"],
        "attractions": ["plinko", "crumbling", "coin cascade", "coin", "cascade",
                        "claw", "slingshot", "ring toss", "ring", "attraction",
                        "booth", "lua", "script"],
        "assets/shaders": ["shader", "glsl", "render", "bloom", "particle",
                           "vertex", "fragment", "karmic"],
        "assets/audio": ["audio", "sound", "music", "sfx"],
        "assets/textures": ["texture", "sprite", "ui"],
        "assets/meshes": ["mesh", "model", "3d"],
        "assets/fonts": ["font", "text"],
        "docs": ["rules", "docs", "documentation", "bridge", "contract",
                 "spec", "todo", "index"],
        "GDD": ["gdd", "design", "spec", "game design"],
        "resources": ["dialog", "json", "data"],
    }
    for keyword, dirs in dir_keywords.items():
        for d in dirs:
            if d in prompt_lower:
                relevant_dirs.add(keyword)
    relevant_dirs.add("src")
    relevant_dirs.add("docs")
    lines = ["## Project Structure (relevant directories)\n"]
    for d in sorted(relevant_dirs):
        full_path = PROJECT_ROOT / d
        if not full_path.is_dir():
            continue
        lines.append(f"### {d}/")
        count = 0
        for f in sorted(full_path.iterdir()):
            if f.is_file() and f.suffix in (".cpp", ".h", ".hpp", ".lua", ".py",
                                             ".json", ".md", ".glsl", ".vert",
                                             ".frag", ".txt", ".cfg", ".xml"):
                lines.append(f"  - {f.name}")
                count += 1
                if count >= 10:
                    if len(list(full_path.iterdir())) > 10:
                        lines.append(f"  - ... ({len(list(full_path.iterdir())) - 10} more)")
                    break
        for f in sorted(full_path.iterdir()):
            if f.is_dir():
                lines.append(f"  - {f.name}/")
        lines.append("")
    return "\n".join(lines)


def extract_gdd_sections(prompt: str) -> str:
    """GDD Librarian: extract only relevant GDD sections based on the prompt."""
    if not GDD_PATH.is_file():
        return ""
    prompt_lower = prompt.lower()
    selected_keys = set()
    for keyword, section_key in KEYWORD_TO_SECTION.items():
        if keyword in prompt_lower:
            selected_keys.add(section_key)
    selected_keys.add("executive")
    selected_keys.add("technical")
    try:
        lines = GDD_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return ""
    excerpts = []
    for key in sorted(selected_keys, key=lambda k: GDD_SECTION_MAP[k]["start"]):
        section = GDD_SECTION_MAP.get(key)
        if not section:
            continue
        start = section["start"] - 1
        end = min(section["end"], len(lines))
        if start >= end:
            continue
        section_lines = lines[start:end]
        excerpts.append(f"### {section['label']}\n" + "\n".join(section_lines))
    if not excerpts:
        return ""
    result = "## Relevant GDD Excerpts\n" + "\n\n".join(excerpts)
    print(f"  [GDD Librarian] Extracted {len(selected_keys)} section(s) from GDD")
    return result


def recursive_librarian(prompt: str, depth: int = 0, max_depth: int = 3) -> str:
    if depth >= max_depth:
        return ""
    result = extract_gdd_sections(prompt)
    if result and len(result) > 100:
        return result
    librarian_prompt = (
        f"Given this feature request: '{prompt}'\n\n"
        f"Which GDD sections would be most relevant? "
        f"Available sections: {', '.join(GDD_SECTION_MAP.keys())}\n"
        f"Output only the section names, comma-separated."
    )
    librarian_response = call_ollama(
        LIBRARIAN_SYSTEM,
        librarian_prompt,
        f"GDD Librarian (recursive depth {depth + 1})",
    )
    found_sections = []
    for key in GDD_SECTION_MAP.keys():
        if key.lower() in librarian_response.lower():
            found_sections.append(key)
    if found_sections:
        refined_prompt = prompt + " " + " ".join(found_sections)
        return recursive_librarian(refined_prompt, depth + 1, max_depth)
    return result


# ── Autonomous Intent Parsing: File Reference Extraction ─────────────────
# Pre-mesh interception step: parses the incoming natural language payload
# for explicit file references (e.g., "reference [src/physics.cpp]" or
# "edit [headers/render.h]"). Automatically fetches these files from the
# local directory and injects them as read-only blocks into the agent prompt.
# This eliminates reliance on IDE extensions (like Continue) for context.

FILE_REF_PATTERN = r'(?:reference|read|edit|examine|open|show)\s*\[([^\]]+)\]'

def parse_file_references(prompt: str) -> list:
    """Parse a prompt for explicit file references (reference [...] or edit [...]).
    
    Returns a list of file paths relative to PROJECT_ROOT.
    Performs path traversal security check to block `../../../etc/passwd` style attacks.
    """
    refs = []
    for match in re.finditer(FILE_REF_PATTERN, prompt, re.IGNORECASE):
        path = match.group(1).strip()
        # Block path traversal: resolve the path and verify it stays inside PROJECT_ROOT
        full_path = (PROJECT_ROOT / path).resolve()
        if not str(full_path).startswith(str(PROJECT_ROOT.resolve())):
            print(f"  [PathTraversal] ⛔ BLOCKED: '{path}' escapes PROJECT_ROOT")
            continue
        # Also block explicit ".." before resolution
        if ".." in path.split("/"):
            print(f"  [PathTraversal] ⛔ BLOCKED: '{path}' contains parent directory traversal")
            continue
        if full_path.is_file() or full_path.is_dir():
            refs.append(path)
    return list(dict.fromkeys(refs))  # deduplicate while preserving order


def fetch_referenced_files(refs: list) -> str:
    """Fetch referenced files and format them as read-only blocks.
    
    Args:
        refs: List of file/dir paths relative to PROJECT_ROOT
        
    Returns:
        Formatted string with file contents in code blocks, or empty string.
    """
    if not refs:
        return ""
    parts = ["## Referenced Files (read-only context)\n"]
    for path_str in refs:
        full_path = PROJECT_ROOT / path_str
        if full_path.is_file():
            try:
                content = full_path.read_text(encoding="utf-8", errors="replace")
                ext = full_path.suffix.lower()
                lang = {".cpp": "cpp", ".h": "cpp", ".hpp": "cpp",
                        ".lua": "lua", ".py": "python",
                        ".json": "json", ".md": "markdown",
                        ".glsl": "glsl", ".vert": "glsl", ".frag": "glsl",
                        ".txt": "text", ".cfg": "text", ".xml": "xml",
                        ".js": "javascript", ".ts": "typescript"}.get(ext, "")
                parts.append(f"### {path_str} (EXPLICIT REFERENCE)")
                parts.append("```" + lang)
                # Cap content to avoid blowing context
                if len(content) > 8000:
                    content = content[:8000] + "\n\n[... truncated at 8000 chars for context budget ...]"
                parts.append(content)
                parts.append("```\n")
                print(f"  [AutoRef] Injected referenced file: {path_str} ({len(content)} chars)")
            except Exception as e:
                parts.append(f"  *Could not read {path_str}: {e}*\n")
        elif full_path.is_dir():
            try:
                files = sorted(full_path.iterdir())
                parts.append(f"### {path_str}/ (directory listing)")
                parts.append("```")
                for f in files[:30]:
                    parts.append(f.name)
                if len(files) > 30:
                    parts.append(f"... ({len(files) - 30} more)")
                parts.append("```\n")
                print(f"  [AutoRef] Listed directory: {path_str} ({len(files)} entries)")
            except Exception as e:
                parts.append(f"  *Could not list {path_str}: {e}*\n")
    return "\n".join(parts)



# ── Pre-Mesh File Reference Injection ──────────────────────────────────────────
# Injects user-requested file references into agent prompts automatically.
# Called during Phase 2 (Project Context) to prep referenced files for all agents.

_REFERENCED_FILES_CACHE = ""   # global cache populated once per pipeline run

def set_referenced_files_cache(refs_block: str) -> None:
    global _REFERENCED_FILES_CACHE
    _REFERENCED_FILES_CACHE = refs_block

def get_referenced_files_cache() -> str:
    return _REFERENCED_FILES_CACHE

# ── Synchronous File-System Tools for Agent Reasoning ────────────────────
# Agents can invoke these via signal syntax during their reasoning loops to
# progressively disclose the codebase without blowing the context window.
# These are strictly synchronous to prevent RAM spikes.

AGENT_FILE_TOOLS_PROMPT = (
    "\n\n---\n"
    "PROGRESSIVE FILE DISCLOSURE TOOLS:\n"
    "You have access to synchronous file-system tools to read the codebase "
    "during your reasoning. To use them, embed one of these signals:\n"
    "- [FILE_READ:<relative_path>] — Read the contents of a file. "
    "Returns the full text of the file at the given path.\n"
    "- [FILE_LIST:<relative_dir>] — List the files in a directory. "
    "Returns a directory listing with file names.\n"
    "The orchestrator will execute the tool and inject the result back into "
    "your context before your next iteration. Use these to explore the "
    "codebase progressively without exceeding your context window.\n"
)


def handle_file_read(signal_content: str) -> str:
    """Synchronous file read tool for agent progressive disclosure.
    
    Parses [FILE_READ:<path>] and returns the file content wrapped
    in a markdown code block. Returns an error message if the file
    cannot be found or read.
    """
    path = signal_content.strip()
    full_path = PROJECT_ROOT / path
    if not full_path.is_file():
        return f"\n## File Tool Result: ERROR\n**Path:** {path}\n**Error:** File not found or is a directory.\n"
    try:
        content = full_path.read_text(encoding="utf-8", errors="replace")
        ext = full_path.suffix.lower()
        lang = {".cpp": "cpp", ".h": "cpp", ".hpp": "cpp",
                ".lua": "lua", ".py": "python",
                ".json": "json", ".md": "markdown",
                ".glsl": "glsl", ".vert": "glsl", ".frag": "glsl"}.get(ext, "")
        print(f"  [FileTool] FILE_READ: {path} ({len(content)} chars)")
        if len(content) > 6000:
            content = content[:6000] + "\n\n[... truncated at 6000 chars ...]"
        return f"\n## File Tool Result: {path}\n```{lang}\n{content}\n```\n"
    except Exception as e:
        return f"\n## File Tool Result: ERROR\n**Path:** {path}\n**Error:** {e}\n"


def handle_file_list(signal_content: str) -> str:
    """Synchronous directory listing tool for agent progressive disclosure.
    
    Parses [FILE_LIST:<dir>] and returns a directory listing.
    """
    path = signal_content.strip()
    full_path = PROJECT_ROOT / path
    if not full_path.is_dir():
        return f"\n## File Tool Result: ERROR\n**Path:** {path}\n**Error:** Directory not found.\n"
    try:
        files = sorted(full_path.iterdir())
        items = []
        for f in files:
            label = f.name
            if f.is_dir():
                label += "/"
            items.append(label)
        truncated = items[:50]
        if len(items) > 50:
            truncated.append(f"... ({len(items) - 50} more)")
        result = f"\n## File Tool Result: {path}/\n```\n" + "\n".join(truncated) + "\n```\n"
        print(f"  [FileTool] FILE_LIST: {path} ({len(items)} entries)")
        return result
    except Exception as e:
        return f"\n## File Tool Result: ERROR\n**Path:** {path}\n**Error:** {e}\n"


# ── Autonomous File Reading ────────────────────────────────────────────────

def find_relevant_files(prompt: str, persona: str) -> list:
    """Scan the project for files relevant to the given prompt and persona."""
    relevant = []
    prompt_lower = prompt.lower()
    persona_lower = persona.lower()
    keyword_map = {
        "plinko":       ["attractions/plinko/"],
        "crumbling":    ["attractions/crumblingfacade/"],
        "coin cascade": ["attractions/coin_cascade/"],
        "physics":      ["src/PhysicsManager.cpp", "src/PhysicsManager.h",
                         "src/MidwayPhysics.cpp", "src/MidwayPhysics.h"],
        "engine":       ["src/Engine.cpp", "src/Engine.h"],
        "attraction":   ["src/AttractionManager.cpp", "src/AttractionManager.h",
                         "attractions/_shared/"],
        "economy":      ["src/EconomyManager.cpp", "src/EconomyManager.h",
                         "economy_state.json"],
        "modifier":     ["src/ModifierState.h", "modifier_state.json"],
        "lua":          ["attractions/", "init.lua"],
        "shader":       ["assets/shaders/"],
        "render":       ["src/DebugRenderer.cpp", "src/DebugRenderer.h"],
        "model":        ["src/ModelManager.cpp", "src/ModelManager.h"],
        "dev console":  ["src/DevConsole.cpp", "src/DevConsole.h"],
        "dialog":       ["resources/dialog.json"],
        "gdd":          ["GDD/"],
        "jolt":         ["docs/jolt_api.md"],
        "box2d":        ["docs/box2d_api.md"],
        "sol2":         ["docs/sol2_api.md"],
        "opengl":       ["docs/opengl_sdl_api.md"],
        "sdl":          ["docs/opengl_sdl_api.md"],
    }
    rule_map = {
        "c++":    "docs/rules_cpp.md",
        "phys":   "docs/rules_phys.md",
        "shader": "docs/rules_shader.md",
        "lua":    "docs/rules_lua.md",
    }
    candidate_paths = set()
    for keyword, paths in keyword_map.items():
        if keyword in prompt_lower:
            for p in paths:
                candidate_paths.add(p)
    for key, rule_path in rule_map.items():
        if key in persona_lower:
            candidate_paths.add(rule_path)
    candidate_paths.add("docs/engine_lua_bridge_contract.md")
    candidate_paths.add("docs/rules_review.md")
    if not candidate_paths:
        candidate_paths = {
            "src/Engine.h", "src/AttractionManager.h",
            "docs/rules_cpp.md", "docs/rules_lua.md",
            "docs/engine_lua_bridge_contract.md",
        }
    for rel_path in sorted(candidate_paths):
        full_path = PROJECT_ROOT / rel_path
        if full_path.is_file():
            try:
                content = full_path.read_text(encoding="utf-8", errors="replace")
                if len(content) > 6000:
                    content = content[:6000] + "\n\n[... truncated ...]"
                relevant.append((rel_path, content))
                print(f"  Read: {rel_path} ({len(content)} chars)")
            except Exception as e:
                print(f"  Could not read {rel_path}: {e}")
        elif full_path.is_dir():
            count = 0
            for f in sorted(full_path.rglob("*")):
                if f.is_file() and f.suffix in (".lua", ".cpp", ".h", ".json", ".md", ".glsl", ".vert", ".frag"):
                    try:
                        content = f.read_text(encoding="utf-8", errors="replace")
                        if len(content) > 4000:
                            content = content[:4000] + "\n\n[... truncated ...]"
                        rel = f.relative_to(PROJECT_ROOT).as_posix()
                        relevant.append((rel, content))
                        count += 1
                        if count >= 3:
                            break
                    except Exception:
                        pass
            print(f"  Read {count} files from {rel_path}")
    return relevant


def format_file_context(files: list, domain_key: str = None) -> str:
    """Format discovered files into a context block for the model.
    
    Args:
        files: List of (path, content) tuples from find_relevant_files()
        domain_key: Agent domain key for ledger-aware TOC scoping.
    """
    if not files:
        return ""
    parts = ["## Relevant Project Files\n"]
    for path, content in files:
        ext = Path(path).suffix.lower()
        lang = {".cpp": "cpp", ".h": "cpp", ".hpp": "cpp",
                ".lua": "lua", ".py": "python",
                ".json": "json", ".md": "markdown",
                ".glsl": "glsl", ".vert": "glsl", ".frag": "glsl"}.get(ext, "")
        parts.append(f"### File: {path}")
        parts.append("```" + lang)
        parts.append(content)
        parts.append("```\n")
    toc = ledger_toc(domain_key)
    if toc:
        parts.insert(0, toc + "\n")
    return "\n".join(parts)


# ── Ollama API Call ────────────────────────────────────────────────────────

def call_ollama_streamed(system: str, user: str, label: str, model: str = None):
    """Generator: call Ollama's /api/chat with streaming, yield tokens.

    Yields each content token as it arrives from the Ollama NDJSON stream.
    Handles errors by yielding an error message string and stopping.

    Payload features:
    - keep_alive: "0" — model unloads instantly after each call to free VRAM
      on the Steam Deck's 12GB budget. Primary models are 7-8B, micro-models
      for syntax/routing only.
    - KV Cache q8_0 quantization (default in Ollama when VRAM constrained):
      halves context memory footprint, enables 32K context on 12GB VRAM.
    """
    use_model = model or MODEL
    print(f"\n{'='*60}")
    print(f"  [{label}] Calling Ollama ({use_model}) [STREAMING]...")
    print(f"{'='*60}")
    sys.stdout.flush()
    payload = {
        "model": use_model,
        "stream": True,
        "keep_alive": "0",
        "options": {
            "num_ctx": OLLAMA_NUM_CTX,         # 32768
            "num_predict": MAX_TOKENS,         # 12000
            "use_mmap": True,                  # memory-map model weights
            # KV cache q8_0 is default for Ollama when VRAM is constrained
            # This enables 32K context window on Steam Deck's 12GB
        },
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA_HOST}/api/chat",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT) as resp:
            buffer = b""
            while True:
                chunk = resp.read(4096)
                if not chunk:
                    break
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    if line.strip():
                        try:
                            obj = json.loads(line.decode("utf-8"))
                            token = obj.get("message", {}).get("content", "")
                            if token:
                                print(token, end="")
                                sys.stdout.flush()
                                yield token
                        except json.JSONDecodeError:
                            pass  # skip malformed lines
    except urllib.error.URLError as e:
        msg = f"[SYSTEM ERROR: OLLAMA TIMEOUT] Could not reach Ollama at {OLLAMA_HOST}: {e.reason}"
        print(msg)
        yield msg
    except Exception as e:
        msg = f"[ERROR] {e}"
        print(msg)
        yield msg


def call_ollama(system: str, user: str, label: str, model: str = None) -> str:
    """Call Ollama's /api/chat endpoint. Returns the full response text.

    Delegates to call_ollama_streamed() generator, collecting all yielded
    tokens into a single string. Fully backward compatible.

    Payload features:
    - keep_alive: "0" — model unloads instantly after each call to free VRAM
      on the Steam Deck's 12GB budget. Primary models are 7-8B, micro-models
      for syntax/routing only.
    - KV Cache q8_0 quantization (default in Ollama when VRAM constrained):
      halves context memory footprint, enables 32K context on 12GB VRAM.
    """
    print(f"\n{'='*60}")
    print(f"  [{label}] Calling Ollama ({model or MODEL})...")
    print(f"{'='*60}")
    sys.stdout.flush()
    full = []
    for token in call_ollama_streamed(system, user, label, model):
        full.append(token)
    result = "".join(full)
    print()  # trailing newline
    sys.stdout.flush()
    return result



# ── Signal Parsing ─────────────────────────────────────────────────────────

def extract_signals(text: str) -> list:
    """Extract all mesh communication signals from agent output."""
    signals = []
    for signal_type, pattern in SIGNAL_PATTERNS.items():
        for match in re.finditer(pattern, text, re.DOTALL):
            groups = match.groups()
            signal = {"type": signal_type, "match": match.group(0)}
            if signal_type == "APPROVE":
                signal["target"] = None
                signal["content"] = None
            elif signal_type == "RESULT":
                signal["target"] = None
                signal["content"] = groups[0].strip()
            else:
                signal["target"] = groups[0].strip()
                signal["content"] = groups[1].strip()
            signals.append(signal)
    return signals


def extract_double_check(text: str) -> dict:
    """Extract the double-check section from agent output.

    Uses a multi-line pattern that captures bullet-point content across lines
    rather than stopping at the first newline.
    """
    match = re.search(DOUBLE_CHECK_PATTERN, text, re.DOTALL)
    if match:
        return {
            "original_prompt": match.group(1).strip(),
            "addresses": match.group(2).strip(),
            "unresolved": match.group(3).strip(),
        }
    return None


def get_verdict(review_text: str) -> str:
    """Extract the verdict from a review output.

    Returns 'PASS', 'FAIL', or 'UNKNOWN'.
    FAIL is checked first (higher priority) to avoid false PASS on negative commentary.
    """
    # Check FAIL first — bold or bare
    if re.search(r"\*\*FAIL\*\*", review_text):
        return "FAIL"
    if re.search(r"(?m)^FAIL$", review_text):
        return "FAIL"
    # Then check PASS
    if re.search(r"\*\*PASS\*\*", review_text):
        return "PASS"
    if re.search(r"(?m)^PASS$", review_text):
        return "PASS"
    return "UNKNOWN"



# ── Memory Fetch: [FETCH] Recall Protocol ─────────────────────────────────
def handle_fetch_signal(fetch_tag: str) -> str:
    """Parse [FETCH:filepath#anchor] tag, return content under that header."""
    match = re.match(r"\[FETCH:([^#]+)#([^\]]+)\]", fetch_tag.strip())
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

    # ── Temporal Read-Depth Calculation ────────────────────────────────
    # Track how far back in archive history the fetch targets.
    # overflow_ledger.md (active) = depth 0.
    # overflow_ledger_v{N}.md = depth = (highest_vol + 1) - N
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
                header_start = idx; header_line = stripped; break
            bm = re.search(r"\[(.*?)\]", stripped)
            if bm and anchor.lower() == bm.group(1).strip().lower():
                header_start = idx; header_line = stripped; break
    if header_start == -1:
        print(f"  [FETCH] Header {anchor} not found in {filepath}")
        return ""
    hdr_lvl = header_line.split("#")[0].count("#") + 1
    content_lines = []
    # ── Reverse-chronological subsection fetch ──────────────
    # Collect sub-sections within the fetched header, then reverse them
    # so the newest architectural decision appears first.
    subsections = []  # list of (header, body_lines) tuples
    current_sub = None
    for j in range(header_start + 1, len(lines_)):
        ln = lines_[j]
        stripped = ln.strip()
        if stripped.startswith("#"):
            level = stripped.split("#")[0].count("#") + 1
            if level <= hdr_lvl:
                break
            # This is a sub-header within the fetched section
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
            content_lines.append("")  # spacing
        content_lines.append(sub["header"])
        content_lines.extend(sub["body_lines"])
    depth_line = f"\n**Chronological Read Depth:** {temporal_depth}\n" if temporal_depth > 0 else ""
    result = ("\n## Recalled Memory\n" + f"**Source:** {filepath} > {header_line.strip()}\n" + depth_line + "\n" + "\n".join(content_lines))
    print(f"  [FETCH] Recalled {len(content_lines)} lines from {filepath}#{anchor} (depth={temporal_depth})")
    return result


# ── Rule-Breaker Accommodation: Synthetic Ledger Headers ──────────────────────
# If an agent executes a system modification but forgets the ### [ModuleName] header,
# the orchestrator auto-generates and prepends a synthetic header based on the
# current active task string and domain.

LEDGER_HEADER_PATTERN = re.compile(r'^#{2,4}\s*\[.*?\]', re.MULTILINE)


# ---- Virtual Context: read_offloaded_file ------------------------------------
# Agent-accessible retrieval tool for offloaded context blocks.

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
                                  token_budget=None) -> str:
    """Process a [READ_OFFLOADED:<block_id>] signal with budget-aware paging."""
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
                    token_budget)
                if freed > 0:
                    token_budget.used = max(0, token_budget.used - freed // 3)
            available = token_budget.hard_limit - token_budget.used
            if estimated_tokens > available:
                return (
                    "\n## Offloaded Context Retrieval: WARNING\n"
                    f"**Block ID:** {block_id}\n"
                    f"**Note:** Block needs ~{estimated_tokens} tokens but only "
                    f"{available} available.\n\n"
                    f"**Preview (first 2000 chars):**\n"
                    f"{content[:2000]}\n\n[... truncated -- increase budget to retrieve full block ...]\n"
                )
    return content


def _page_out_context(context_text: str, needed_chars: int,
                       token_budget=None) -> int:
    """Page out sections of the active context to free space."""
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
            body = [l.rstrip("\n") for l in lines[idx+1:section_end]]
            id_base = re.sub(r'[^a-zA-Z0-9]', '_', header[:60]).strip('_').lower()
            content_hash = hashlib.md5(section_text.encode("utf-8")).hexdigest()[:8]
            block_id = f"paged_{id_base}_{content_hash}"
            store.store_block(block_id, header, body)
            offloaded_count += 1
            freed += section_len
        except Exception as e:
            continue
    if offloaded_count > 0:
        print(f"  [PageOut] Paged out {offloaded_count} section(s) ({freed} chars)")
    return freed


def ensure_ledger_header(output: str, task_spec: str, agent_key: str) -> str:
    """Detect if agent output contains a properly formatted ledger header
    (e.g., ### [ModuleName]). If missing, generate a synthetic header based
    on the task spec and agent domain, and prepend it.

    Strips code blocks before header checking to avoid false positives on
    commented-out headers inside code blocks (e.g., `// ### [DebugLog]`).
    
    This prevents formatting rule breakers from silently losing memory entries.
    Returns the (possibly modified) output.
    """
    # Strip code blocks before checking for ledger headers to prevent false
    # positives on e.g. `// ### [DebugLog]` inside C++ or Lua code fences
    stripped = re.sub(r'```.*?```', '', output, flags=re.DOTALL)
    if LEDGER_HEADER_PATTERN.search(stripped):
        # Agent followed the rules — no modification needed
        return output

    # Agent broke the formatting rule — generate synthetic header
    # Extract key domain identifier from task_spec
    task_clean = task_spec.strip().rstrip('.')
    # Build a descriptive module name from the task
    module_name = _generate_module_name(task_clean, agent_key)
    synthetic_header = f"### [{module_name}]\n"
    modified = synthetic_header + output + "\n"
    print(f"  [LedgerGuard] ⚠ Synthetic header prepended for {agent_key}: [{module_name}]")
    return modified


def _generate_module_name(task_spec: str, agent_key: str) -> str:
    """Generate a descriptive module name from task spec and agent domain."""
    # Take first 3-5 significant words from the task
    words = re.findall(r'[A-Za-z][A-Za-z0-9_]*', task_spec)
    # Filter out noise words
    stopwords = {'the', 'a', 'an', 'for', 'of', 'in', 'to', 'and', 'is', 'it',
                 'be', 'are', 'was', 'were', 'been', 'this', 'that', 'with',
                 'from', 'at', 'by', 'on', 'or', 'as', 'if', 'but'}
    significant = [w for w in words if w.lower() not in stopwords]
    # Take up to 5 key words
    name_words = significant[:5]
    if not name_words:
        return agent_key  # Fallback to agent key
    return '_'.join(name_words)


# ── Rule-Breaker Formatting Recovery: Disk-Write Interceptor ─────────────
# Before writing an agent's output to disk, actively check for the mandatory
# ### [ModuleName] syntax. If missing, prepend a synthetic header.
# This guarantees the regex parser in _collect_ledger_entries will find it
# later and properly index it into the Table of Contents.

def _append_to_ledger(output: str, agent_key: str, task_spec: str) -> None:
    """Persist agent output to its domain-specific ledger file on disk.
    
    Before writing, guarantees the output contains the mandatory
    ### [ModuleName] header via ensure_ledger_header(). If the agent
    broke the formatting rule, a synthetic header is prepended.
    
    Appends to the ledger file (preserving all prior entries) so
    _collect_ledger_entries can later parse the file into reverse-
    chronological chunks for token budgeting.
    
    RAM footprint: ~2KB peak for the single output being written.
    No full ledger is loaded into memory — uses append-mode open().
    """
    domain = ALL_DOMAINS.get(resolve_agent_name(agent_key))
    if not domain:
        return
    ledger_rel = domain.get("ledger", "")
    if not ledger_rel:
        return
    
    # Guarantee header enforcement before disk write
    safe_output = ensure_ledger_header(output, task_spec, agent_key)
    
    ledger_path = PROJECT_ROOT / ledger_rel
    
    # mkdir then append-mode write — no Lock needed since the generator
    # pattern ensures sequential access from the streaming layer.
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    # Append-mode write — never rewrite the full ledger, O(1) per entry
    with open(ledger_path, "a", encoding="utf-8") as f:
        f.write("\n" + safe_output.strip() + "\n")

    
    domain_name = domain.get("name", agent_key)
    if safe_output != output:
        print(f"  [LedgerWrite] ⚠ {domain_name}: missing header fixed → appended to {ledger_rel}")
    else:
        print(f"  [LedgerWrite] ✓ {domain_name}: entry appended to {ledger_rel}")


# ── Checkpoint System ──────────────────────────────────────────────────────

def save_checkpoint(checkpoint_id: str, phase: str, data: dict) -> None:
    """Save pipeline state to a checkpoint file."""
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    ckpt = {
        "checkpoint_id": checkpoint_id,
        "phase": phase,
        "timestamp": datetime.now().isoformat(),
        "data": data,
    }
    path = CHECKPOINT_DIR / f"{checkpoint_id}.json"
    path.write_text(json.dumps(ckpt, indent=2), encoding="utf-8")
    print(f"  [Checkpoint] Saved: {checkpoint_id} (phase: {phase})")


def load_checkpoint(checkpoint_id: str) -> dict:
    """Load a checkpoint by ID."""
    path = CHECKPOINT_DIR / f"{checkpoint_id}.json"
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def list_checkpoints() -> list:
    """List all saved checkpoints."""
    if not CHECKPOINT_DIR.is_dir():
        return []
    checkpoints = []
    for f in sorted(CHECKPOINT_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            checkpoints.append(data)
        except Exception:
            pass
    return checkpoints


# ── Mesh Consensus Engine ──────────────────────────────────────────────────

class Task:
    """A work item in the mesh queue."""
    def __init__(self, agent: str, spec: str, parent: str = None,
                 task_id: str = None, is_query: bool = False,
                 iteration: int = 0, context: str = ""):
        self.agent = agent
        self.spec = spec
        self.parent = parent
        self.task_id = task_id or f"{agent}_{datetime.now().strftime('%H%M%S%f')}"
        self.is_query = is_query
        self.iteration = iteration
        self.context = context
        self.output = ""
        self.signals = []
        self.double_check = None
        self.completed = False

    def __repr__(self):
        return f"Task({self.agent}, parent={self.parent}, query={self.is_query}, iter={self.iteration})"


def resolve_agent_name(name: str) -> str:
    """Resolve a signal target name to a domain key."""
    name_lower = name.lower().strip()
    # ── Alias Map Lookup (first priority) ─────────────────────────────
    # Check the AGENT_ALIAS_MAP before ALL_DOMAINS direct/name/partial
    # matching. This captures unpredictable conversational variants from
    # the Director LLM (e.g., "Physics", "cpp", "the C++ engineer").
    if name_lower in AGENT_ALIAS_MAP:
        return AGENT_ALIAS_MAP[name_lower]
    # Direct match
    for key in ALL_DOMAINS:
        if key.lower() == name_lower:
            return key
    # Name match
    for key, domain in ALL_DOMAINS.items():
        if domain["name"].lower() == name_lower:
            return key
    # Partial match (iterative — match generic keywords within domain names)
    for key, domain in ALL_DOMAINS.items():
        if name_lower in domain["name"].lower() or name_lower in key.lower():
            return key
    # ── Final fallback: AGENT_ALIAS_MAP reverse scan ──────────────────
    # If no direct/name/partial match, try substring matching against
    # alias map keys (handles "the C++ engineer" etc.)
    for alias, canonical_key in AGENT_ALIAS_MAP.items():
        if alias in name_lower or name_lower in alias:
            return canonical_key
    return name


def get_agent_system(agent_key: str) -> str:
    """Get the system prompt for an agent, with mesh extension."""
    domain = ALL_DOMAINS.get(agent_key)
    if not domain:
        return ""
    base = domain["system_prompt"]
    ledger_path = domain.get("ledger", "")
    ledger_note = ""
    if ledger_path:
        ledger_note = f"\n\nYour assigned memory ledger: {ledger_path}\n"
    if agent_key in ("DOC", "CONF"):
        return base + ledger_note + LEDGER_MEMORY_RULE  # Skip mesh, keep ledgers
    return base + ledger_note + MESH_AGENT_SYSTEM_EXTENSION + LEDGER_MEMORY_RULE


def execute_task(task: Task, user_prompt: str, director_output: str,
                 all_results: dict, file_context: str, gdd_context: str) -> str:
    """Execute a single task by calling the appropriate agent."""
    agent_key = resolve_agent_name(task.agent)
    domain = ALL_DOMAINS.get(agent_key)

    if not domain:
        return f"[ERROR] Unknown agent: {task.agent}"

    preferred_model = domain.get("model", EXECUTION_MODEL)
    system = get_agent_system(agent_key)

    # Build context
    context_parts = [
        f"## Original Feature Request\n{user_prompt}",
    ]

    if director_output:
        context_parts.append(f"## Director's Task Breakdown\n{director_output}")

    if file_context:
        context_parts.append(file_context)

    if gdd_context:
        context_parts.append(gdd_context)

    # ── Auto-Inject Referenced Files ──────────────────────────────────
    refs_block = get_referenced_files_cache()
    if refs_block:
        context_parts.append(refs_block)

    # Include parent context if this is a sub-task
    if task.parent and task.parent in all_results:
        context_parts.append(f"## Parent Task Context\n{all_results[task.parent]}")

    # Include previous iteration output for self-correction
    if task.iteration > 0 and task.output:
        context_parts.append(f"## Your Previous Output (iteration {task.iteration})\n{task.output}")
        if task.iteration >= MAX_ITERATIONS - 1:
            system = SELF_CORRECT_SYSTEM

    # Include any query results that were injected
    if task.context:
        context_parts.append(task.context)

    # The task spec
    context_parts.append(f"## Task Specification\n{task.spec}")

    user_message = "\n\n".join(context_parts)

    label = f"{domain['name']} (Task {task.task_id})"
    if task.is_query:
        label = f"[QUERY] {domain['name']} -> {task.parent}"

    output = call_ollama(system, user_message, label, preferred_model)

    # ── Intra-Agent Reasoning Gate ────────────────────────────
    # Self-review step: for code-generation domains (C++, Lua, PHYS),
    # have a reasoning model verify the output before it reaches
    # signal processing and disk-write. +1 LLM call per gated task.
    if agent_key in REASONING_GATE_DOMAINS:
        gate_prompt = (
            f"## Original Task Specification\n{task.spec}\n\n"
            f"## Generated Output\n{output}\n\n"
            f"Critically examine the above generated output against the task spec. "
            f"Is it correct, complete, and free of hallucinations?"
        )
        gate_output = call_ollama(
            REASONING_GATE_SYSTEM, gate_prompt,
            f"Reasoning Gate ({domain['name']})", REASONING_MODEL
        )
        # Extract CONFIRMED or REVISED output
        if "CONFIRMED" in gate_output:
            print(f"  [ReasoningGate] {domain['name']}: CONFIRMED — output accepted")
            log_to_session_timeline(
                user_input=f"Reasoning Gate ({domain['name']})",
                agent_assigned="ReasoningGate",
                tools_accessed="call_ollama (REASONING_MODEL)",
                final_output="CONFIRMED — output accepted as-is"
            )
        elif "REVISED" in gate_output:
            print(f"  [ReasoningGate] {domain['name']}: REVISED — output corrected")
            # Extract the revised version from gate output
            revised_match = re.search(
                r"(?:REVISED)\s*\n(.*)", gate_output, re.DOTALL
            )
            if revised_match:
                revised = revised_match.group(1).strip()
                if revised:
                    print(f"  [ReasoningGate] Applying revised output ({len(revised)} chars)")
                    output = revised
            log_to_session_timeline(
                user_input=f"Reasoning Gate ({domain['name']})",
                agent_assigned="ReasoningGate",
                tools_accessed="call_ollama (REASONING_MODEL)",
                final_output="REVISED — output was corrected by reasoning gate"
            )
        else:
            # If no clear verdict from gate, keep original output
            print(f"  [ReasoningGate] {domain['name']}: No clear verdict — keeping original")

    # ── Ledger Guard: auto-fix missing headers ────────────────
    output = ensure_ledger_header(output, task.spec, task.agent)
    task.output = output
    task.signals = extract_signals(output)
    task.double_check = extract_double_check(output)
    task.completed = True

    return output


def run_mesh_pipeline(user_prompt: str, checkpoint_id: str = None,
                       session_mgr=None) -> str:
    """Main mesh consensus pipeline orchestrator.

    Args:
        user_prompt:   The feature request to process.
        checkpoint_id: Optional checkpoint to resume from.
        session_mgr:   Optional SessionManager instance for session tracking.
    """
    # Track session phase if session manager is available
    if session_mgr:
        session_mgr.update_phase("SETUP")
    output_parts = []
    all_results = {}  # task_id -> output text
    all_approvals = {}  # agent_key -> bool
    all_vetos = []  # list of veto signals
    all_objects = []  # list of object signals
    consensus_iteration = 0
    snapshot = None

    # ── Phase 0: Setup ────────────────────────────────────────────────────
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    feature_slug = re.sub(r'[^a-z0-9]+', '_', user_prompt.lower())[:40].strip('_')
    run_id = f"{feature_slug}_{timestamp}"

    print(f"\n{'='*70}")
    print(f"  Midway Mesh Pipeline — Run: {run_id}")
    print(f"{'='*70}")

    output_parts.append(f"# Midway Mesh Pipeline — {run_id}\n")
    output_parts.append(f"**Request:** {user_prompt}\n")
    output_parts.append(f"**Started:** {datetime.now().isoformat()}\n---\n")

    # Initialize snapshot if available
    if HAS_SNAPSHOT:
        try:
            snapshot = SnapshotManager(run_id, user_prompt)
            output_parts.append(f"📸 Snapshot tracking enabled: {run_id}\n")
        except Exception as e:
            print(f"  [Snapshot] Init failed: {e}")
            snapshot = None

    # ── [CHAT_FORCED] Marker Detection ─────────────────────────────────
    # If the --chat CLI flag was used, the prompt is prefixed with [CHAT_FORCED].
    # Strip the marker and force CHAT intent regardless of classifier output.
    force_chat = False
    if user_prompt.startswith("[CHAT_FORCED]"):
        force_chat = True
        user_prompt = user_prompt[len("[CHAT_FORCED]"):].strip()
        print(f"  [CHAT_FORCED] Marker detected — forcing CHAT mode")

    # ── Zero-Shot Intent Classification (Director Model) ────────────────
    # Before any execution, classify the user's intent as MODIFICATION
    # (code changes) or QUERY (asking about past decisions/history).
    # QUERY intents bypass the coding mesh entirely and go to the Librarian.
    # If --chat flag was used, skip classification and go straight to CHAT.
    print(f"\n{'='*70}\n  Phase 0.03: Intent Classification — '{user_prompt[:60]}...'\n{'='*70}")
    if force_chat:
        intent = "CHAT"
    elif is_likely_chat(user_prompt):
        intent = "CHAT"
        print(f"  [FastPath] Regex matched -- routing to CHAT mode (bypassed LLM classifier)")
    else:
        intent = classify_intent(user_prompt)

    if intent == "CHAT":
        # ── Conversational Stream Bifurcation ────────────────────────────
        # Out-of-band chat layer. Routes directly to CHAT_SYSTEM/CHAT_MODEL,
        # bypassing the entire mesh (director, execution, review, consensus).
        # Supports --chat CLI flag to force CHAT mode.
        print(f"\n{'='*70}\n  💬 CHAT MODE — Conversational Response\n{'='*70}")
        output_parts.append("\n## 💬 Chat Response\n")

        # Provide project context summary for informed responses
        project_state = get_project_state()
        structure = curate_project_structure(user_prompt)
        chat_input = (
            f"## User Question\n{user_prompt}\n\n"
            f"## Project Context\n{project_state}\n\n"
            f"## Relevant Files\n{structure}\n\n"
            f"## Instructions\n"
            f"{CHAT_SYSTEM}\n\n"
            f"Respond conversationally to the user's question."
        )
        chat_output = call_ollama(
            CHAT_SYSTEM,
            chat_input,
            "Chat Assistant",
            CHAT_MODEL,
        )
        output_parts.append(chat_output + "\n")

        # Save output
        final_output = "\n".join(output_parts)
        output_path = PROJECT_ROOT / f"pipeline_output_{run_id}.md"
        try:
            output_path.write_text(final_output, encoding="utf-8")
            print(f"\n  Output saved to: {output_path}")
        except Exception as e:
            print(f"\n  Could not save output: {e}")

        # Session timeline
        log_to_session_timeline(
            user_input=user_prompt,
            agent_assigned="Chat Assistant",
            tools_accessed="chat_system, project_state, file_structure",
            final_output=chat_output,
        )

        print(f"\n{'='*70}\n  💬 Chat Complete\n{'='*70}")
        return final_output

    if intent == "QUERY":
        # Route directly to Librarian — no coding mesh, no director, no review
        print(f"\n{'='*70}\n  📚 LIBRARIAN MODE — Read-Only Research\n{'='*70}")
        output_parts.append("\n## 📚 Librarian Response\n")

        librarian_domain = ALL_DOMAINS.get("LIBRARIAN", {})
        system = librarian_domain.get("system_prompt", "")
        if system:
            # Provide memory TOC as context
            memory_toc = search_memory()
            librarian_input = (
                f"{system}\n\n"
                f"## Memory Ledger Table of Contents\n{memory_toc}\n\n"
                f"## User Query\n{user_prompt}\n\n"
                f"## Instructions\n"
                f"1. Use the Memory Ledger Table of Contents above to identify which "
                f"ledger sections are relevant to the user's question.\n"
                f"2. To retrieve specific entries, embed [FETCH:path#anchor] tags in your "
                f"reasoning. The orchestrator will resolve them.\n"
                f"3. Answer based ONLY on the provided memory documents. "
                f"Do NOT attempt to modify any code.\n"
                f"4. If the answer cannot be found in the memory documents, say so.\n"
                f"5. Cite your sources (which ledger file and section header)."
            )
            librarian_output = call_ollama(
                system + "\n\nYou are the Librarian — read-only research agent.",
                librarian_input,
                "Librarian",
                DIRECTOR_MODEL,
            )
            # Process any FETCH signals from the Librarian
            lib_signals = extract_signals(librarian_output)
            for sig in lib_signals:
                if sig["type"] == "FETCH":
                    fetched = handle_fetch_signal(sig["match"])
                    if fetched:
                        librarian_output += "\n\n" + fetched
                        # Re-process with fetched content (single depth only)
                        refetch_input = librarian_input + "\n\n## Added Context from Memory\n" + fetched
                        librarian_output = call_ollama(
                            system + "\n\nYou are the Librarian — read-only research agent.",
                            refetch_input,
                            "Librarian (with fetched context)",
                            DIRECTOR_MODEL,
                        )

            output_parts.append(librarian_output + "\n")
            final_output = "\n".join(output_parts)
            output_path = PROJECT_ROOT / f"pipeline_output_{run_id}.md"

            # ── Session Timeline Log ────────────────────────────────────
            log_to_session_timeline(
                user_input=user_prompt,
                agent_assigned="Librarian",
                tools_accessed="search_memory (TOC), fetch (memory ledgers)",
                final_output=librarian_output,
            )

            try:
                output_path.write_text(final_output, encoding="utf-8")
                print(f"\n  Output saved to: {output_path}")
            except Exception as e:
                print(f"\n  Could not save output: {e}")

            print(f"\n{'='*70}\n  📚 Librarian Complete\n{'='*70}")
            return final_output

    # ── Pre-Flight: Checkpoint & Intent Routing ────────────────────────
    ckpt = load_checkpoint(checkpoint_id) if checkpoint_id else None
    active_phase = ckpt.get("phase") if ckpt else None

    print(f"\n{'='*70}\n  Phase 0.05: Autonomous Intent Router\n{'='*70}")
    intent_prompt = f"Analyze this user request:\n'{user_prompt}'\n\nIs the user's primary intent 'DEVELOPMENT' or 'DIAGNOSTIC'? Output ONLY one of those two words."
    intent_eval = call_ollama(INTENT_ROUTER_SYSTEM, intent_prompt, "Intent Router", REASONING_MODEL)
    is_diagnostic = "DIAGNOSTIC" in intent_eval.upper()

    qa_file = PROJECT_ROOT / "docs" / "memory" / "qa_ledger.md"
    qa_content = qa_file.read_text(encoding="utf-8", errors="replace") if qa_file.is_file() else "No QA Ledger exists."

    if active_phase == "DIAGNOSTIC":
        # Resume an ongoing multi-turn diagnostic session
        print(f"  🔄 RESUMING DIAGNOSTIC SESSION: {checkpoint_id}")
        output_parts.append(f"\n## 🔄 Diagnostic Session Resumed\n")
        chat_history = ckpt["data"].get("chat_history", "")
        if len(chat_history) > 2000:
            chat_history = "...[earlier history truncated]...\n" + chat_history[-2000:]
        diag_prompt = f"Previous Chat:\n{chat_history}\n\nNew User Request: {user_prompt}\n\nCurrent QA Ledger:\n```markdown\n{qa_content}\n```\n"
        diag_output = call_ollama(DIAGNOSTIC_ORACLE_SYSTEM, diag_prompt, "Diagnostic Oracle", REASONING_MODEL)

        if "[AWAITING_INPUT]" in diag_output:
            save_checkpoint(checkpoint_id, "DIAGNOSTIC", {"chat_history": f"{chat_history}\nUser: {user_prompt}\nOracle: {diag_output}"})
            output_parts.append(f"\n{diag_output}\n\n*Diagnostic session suspended. Reply to continue.*")
            return "\n".join(output_parts)

        new_ledger_match = re.search(r"```markdown\n(.*?)\n```", diag_output, re.DOTALL)
        if "[DIAGNOSTIC_RESOLVED]" in diag_output and new_ledger_match:
            backup_path = CHECKPOINT_DIR / f"qa_ledger_backup_{timestamp}.md"
            if qa_file.is_file(): backup_path.write_text(qa_content, encoding="utf-8")
            qa_file.write_text(new_ledger_match.group(1).strip() + "\n", encoding="utf-8")
            output_parts.append("**QA Ledger successfully updated.**\n")
            ckpt_file = CHECKPOINT_DIR / f"{checkpoint_id}.json"
            if ckpt_file.is_file(): ckpt_file.rename(CHECKPOINT_DIR / f"{checkpoint_id}.archived.json")

        output_parts.append(f"\n{diag_output}\n")
        return "\n".join(output_parts)

    elif is_diagnostic:
        # User wants a fresh diagnostic session
        if active_phase == "BLOCKED":
            print(f"  [Intent Router] Ignored BLOCKED state to run diagnostic override.")
        print(f"  Phase 0.1: Diagnostic Protocol")
        output_parts.append("\n## 🧠 Diagnostic Protocol\n")
        diag_prompt = f"User Request: {user_prompt}\n\nCurrent QA Ledger:\n```markdown\n{qa_content}\n```\n"
        diag_output = call_ollama(DIAGNOSTIC_ORACLE_SYSTEM, diag_prompt, "Diagnostic Oracle", REASONING_MODEL)

        if "[AWAITING_INPUT]" in diag_output:
            save_checkpoint(run_id, "DIAGNOSTIC", {"chat_history": f"User: {user_prompt}\nOracle: {diag_output}"})
            output_parts.append(f"\n{diag_output}\n\n*Diagnostic session suspended. Reply to continue.*")
            return "\n".join(output_parts)

        new_ledger_match = re.search(r"```markdown\n(.*?)\n```", diag_output, re.DOTALL)
        if "[DIAGNOSTIC_RESOLVED]" in diag_output and new_ledger_match:
            backup_path = CHECKPOINT_DIR / f"qa_ledger_backup_{timestamp}.md"
            if qa_file.is_file(): backup_path.write_text(qa_content, encoding="utf-8")
            qa_file.write_text(new_ledger_match.group(1).strip() + "\n", encoding="utf-8")
            output_parts.append("**QA Ledger successfully updated.**\n")

        output_parts.append(f"\n{diag_output}\n")
        return "\n".join(output_parts)

    # ── Phase 0.2: QA Anchor Extraction ────────────────────────────────
    print(f"\n{'='*70}\n  Phase 0.2: QA Anchor Extraction\n{'='*70}")
    # Keyword pre-filter for affirmative/negative sentiments
    affirmative_keywords = ["works", "working", "succeeded", "success", "fixed", "great",
                            "perfect", "stable", "correct", "good", "fine", "intact",
                            "as expected", "properly", "functional", "solved"]
    negative_keywords = ["broken", "broke", "failed", "failure", "bug", "crashes",
                         "crashed", "not working", "doesn't work", "wrong", "incorrect",
                         "glitch", "glitchy", "corrupt", "corrupted", "error", "issue",
                         "problem", "malfunction", "regression", "degraded"]
    prompt_lower = user_prompt.lower()
    has_affirmative = any(kw in prompt_lower for kw in affirmative_keywords)
    has_negative = any(kw in prompt_lower for kw in negative_keywords)
    has_sentiment = has_affirmative or has_negative
    
    if has_sentiment:
        sentiment_hint = "WORKING" if has_affirmative and not has_negative else ("BROKEN" if has_negative and not has_affirmative else "AMBIGUOUS")
    else:
        sentiment_hint = "NONE"
    
    qa_extract_prompt = (
        f"Analyze this user prompt: '{user_prompt}'. "
        f"Does it contain explicit feedback about the current state "
        f"of the game's systems (e.g., stating a feature works perfectly, "
        f"or stating something is broken)?\n\n"
        f"**Affirmative keywords** (system is WORKING): works, working, "
        f"succeeded, success, fixed, great, perfect, stable, correct, good, "
        f"fine, intact, as expected, properly, functional, solved\n"
        f"**Negative keywords** (system is BROKEN): broken, broke, failed, "
        f"failure, bug, crashes, crashed, not working, doesn't work, wrong, "
        f"incorrect, glitch, glitchy, corrupt, corrupted, error, issue, "
        f"problem, malfunction, regression, degraded\n\n"
        f"**Pre-classified sentiment (keyword match):** {sentiment_hint}\n\n"
        f"If YES, extract it into a QA Anchor format:\n"
        f"### [QA Anchor: <System Name>]\n"
        f"**Status:** <WORKING or BROKEN>\n"
        f"**User Note:** <exact words>\n"
        f"**Timestamp:** {datetime.now().isoformat()}\n\n"
        f"If NO, output exactly 'NONE'."
    )
    qa_eval = call_ollama("You are a Lead Producer.", qa_extract_prompt, "QA Extraction", REASONING_MODEL)
    if "NONE" not in qa_eval.upper():
        qa_file = PROJECT_ROOT / "docs" / "memory" / "qa_ledger.md"
        qa_file.parent.mkdir(parents=True, exist_ok=True)
        with open(qa_file, "a", encoding="utf-8") as f:
            f.write(f"\n{qa_eval.strip()}\n")
        print(f"  [QA] Anchor saved to qa_ledger.md")

    # ── Resurrection Check ─────────────────────────────────────────────────
    # If resuming from a BLOCKED checkpoint, skip Phases 1-3 and reconstruct
    # state from the checkpoint data. Treat user_prompt as the manual fix.
    # DIAGNOSTIC is handled in Phase 0.05 above.
    _resumed_blocked = False
    if active_phase == "BLOCKED":
        _resumed_blocked = True
        ckpt_data = ckpt["data"]
        print(f"\n{'='*70}")
        print(f"  🔄 RESURRECTING BLOCKED PIPELINE: {checkpoint_id}")
        print(f"{'='*70}")
        output_parts.append(f"\n## 🔄 Pipeline Resurrected (Checkpoint: {checkpoint_id})\n")
        output_parts.append(f"**User fix provided:** {user_prompt}\n\n")

        # Reconstruct work_queue from serialized data
        work_queue = deque()
        task_map = {}
        for t_dict in ckpt_data.get("work_queue", []):
            task_obj = Task(
                agent=t_dict.get("agent", "C++"),
                spec=t_dict.get("spec", ""),
                parent=t_dict.get("parent"),
                task_id=t_dict.get("task_id"),
                iteration=t_dict.get("iteration", 0),
            )
            task_obj.completed = t_dict.get("completed", False)
            work_queue.append(task_obj)
            task_map[task_obj.task_id] = task_obj

        all_results = ckpt_data.get("all_results", {})
        processed_ids = set(all_results.keys())
        review_cycle = 0
        review_verdict = "UNKNOWN"
        review_output = ""

        # Inject user_prompt as a manual fix into the context of the
        # blocked task — we'll jump directly into Phase 6's fix cycle.
        director_output = ckpt_data.get("director_output", "")
        active_code_index = ckpt_data.get("active_code_index", "")
        gdd_context = ""

        print(f"  [Resurrection] Reconstructed {len(work_queue)} tasks, "
              f"{len(all_results)} results")
        print(f"  [Resurrection] User fix injected. Jumping to Review-Fix cycle.\n")

    if not _resumed_blocked:
        # ── Phase 0.5: Lead Producer (Scope Gate & Auto-Feeder) ───────────────
        blueprint_path = PROJECT_ROOT / "docs" / "project_blueprint.md"
        
        if not user_prompt:
            if blueprint_path.is_file():
                content = blueprint_path.read_text(encoding="utf-8")
                match = re.search(r"- \\[ \\] (Task \\d+: .+)", content)
                if match:
                    user_prompt = match.group(1)
                    print(f"  [Lead Producer] Auto-feeding next task: {user_prompt}")
                    new_content = content.replace(f"- [ ] {user_prompt}", f"- [x] {user_prompt}", 1)
                    blueprint_path.write_text(new_content, encoding="utf-8")
                else:
                    print("  [Lead Producer] Blueprint complete. Nothing to do.")
                    return "Blueprint complete."
            else:
                print("  [ERROR] No prompt provided and no blueprint found.")
                return "Failed to start."
        else:
            scope_prompt = f"Analyze this prompt: '{user_prompt}'. If it requires modifying >{SCOPE_FILE_LIMIT} files or writing >{SCOPE_LINE_LIMIT} lines, respond strictly with 'TOO_BROAD'. Otherwise respond 'NARROW'."
            scope_eval = call_ollama("You are a Lead Producer.", scope_prompt, "Scope Gate", REASONING_MODEL)
            
            if "TOO_BROAD" in scope_eval.upper():
                print(f"\n  [Lead Producer] Scope is TOO BROAD. Generating blueprint...")
                blueprint_prompt = f"Create a step-by-step markdown blueprint to accomplish: {user_prompt}. Format as a checklist: '- [ ] Task 1: ...'"
                blueprint = call_ollama("You are a Lead Producer.", blueprint_prompt, "Blueprint Generation", REASONING_MODEL)
                blueprint_path.parent.mkdir(exist_ok=True)
                blueprint_path.write_text(blueprint, encoding="utf-8")
                print(f"  [Lead Producer] Saved to docs/project_blueprint.md.")
                return "Blueprint created. Run pipeline with no prompt to execute Task 1."

        # ── Phase 1: GDD Librarian ────────────────────────────────────────────
        print(f"\n{'='*70}")
        print(f"  Phase 1: GDD Librarian")
        print(f"{'='*70}")
        output_parts.append("\n## Phase 1: GDD Librarian\n")

        gdd_context = recursive_librarian(user_prompt)
        if gdd_context:
            output_parts.append(gdd_context + "\n")
        else:
            output_parts.append("No relevant GDD sections found.\n")

        # ── Phase 2: Project State & File Context ─────────────────────────────
        print(f"\n{'='*70}")
        print(f"  Phase 2: Project Context")
        print(f"{'='*70}")
        output_parts.append("\n## Phase 2: Project Context\n")

        project_state = get_project_state()
        output_parts.append(project_state + "\n")

        structure = curate_project_structure(user_prompt)
        output_parts.append(structure + "\n")

        # ── Auto-Fetch Referenced Files ───────────────────────────────────────────
        refs = parse_file_references(user_prompt)
        refs_block = fetch_referenced_files(refs)
        set_referenced_files_cache(refs_block)
        if refs_block:
            output_parts.append("### Referenced Files (auto-injected)\n" + refs_block + "\n")
            print(f"  [AutoRef] {len(refs)} file reference(s) parsed and cached for all agents")

        # ── Phase 3: Director ─────────────────────────────────────────────────
        print(f"\n{'='*70}")
        print(f"  Phase 3: Director — Task Decomposition")
        print(f"{'='*70}")
        output_parts.append("\n## Phase 3: Director — Task Decomposition\n")

        director_prompt = build_director_prompt()
        director_input = f"{director_prompt}\n\n---\nUSER REQUEST:\n{user_prompt}"
        director_output = call_ollama(DIRECTOR_SYSTEM, director_input, "Director", DIRECTOR_MODEL)
        output_parts.append(director_output + "\n")

        # Parse tasks from Director output
        task_regex = r"### Task (\d+): \[([^\]]+)\] — (.+)"
        tasks = []
        for match in re.finditer(task_regex, director_output):
            tasks.append({
                "id": match.group(1),
                "domain": match.group(2).strip(),
                "title": match.group(3).strip(),
            })

        if not tasks:
            # Fallback: create a single default task
            tasks.append({"id": "1", "domain": "C++", "title": "Full Implementation"})
            print(f"  [Director] No tasks parsed, created default task")

        print(f"  [Director] Created {len(tasks)} task(s)")
    else:
        # Resurrected pipeline: skip Phases 1-3, use reconstructed state
        print(f"  [Resurrection] Skipping Phases 1-3 (restored from checkpoint).")
        output_parts.append("\n*Phases 1-3 skipped — pipeline resurrected from checkpoint.*\n")
        gdd_context = ""
        # Build a placeholder tasks list for the consensus check downstream
        tasks = [{"id": str(i), "domain": "RESUMED", "title": t.spec}
                 for i, t in enumerate(work_queue)] if work_queue else [
                     {"id": "1", "domain": "C++", "title": "Resumed Task"}
                 ]

    # ── Phase 4: Mesh Execution ───────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  Phase 4: Mesh Execution — {len(tasks)} Task(s)")
    print(f"{'='*70}")
    output_parts.append(f"\n## Phase 4: Mesh Execution ({len(tasks)} tasks)\n")

    # Build the work queue
    work_queue = deque()
    task_map = {}  # task_id -> Task object

    for t in tasks:
        task_obj = Task(
            agent=t["domain"],
            spec=t["title"],
            task_id=f"task_{t['id']}",
            parent=None,
        )
        work_queue.append(task_obj)
        task_map[task_obj.task_id] = task_obj

    # Process work queue (depth-first)
    processed_ids = set()
    query_results = {}  # query_id -> answer
    pending_queries = {}  # query_task_id -> original Task awaiting QUERY/CONSULT answer
    pending_fetches = {}  # query_task_id -> original Task awaiting DOC oracle answer

    while work_queue:
        task = work_queue.popleft()

        if task.task_id in processed_ids and not task.is_query:
            continue

        # Check for query results to inject
        context_extra = ""
        if task.is_query:
            # This is a query being answered — just execute it
            pass
        elif task.parent and task.parent in query_results:
            context_extra = f"## Answer from Query\n{query_results[task.parent]}"

        task.context = context_extra

        # Find relevant files for this task (ledger-aware: agent sees own TOC first)
        file_context = ""
        try:
            files = find_relevant_files(task.spec, task.agent)
            file_context = format_file_context(files, domain_key=task.agent)
        except Exception as e:
            print(f"  [FileReader] Error: {e}")

        # Execute the task
        output = execute_task(
            task, user_prompt, director_output,
            all_results, file_context, gdd_context,
        )

        all_results[task.task_id] = output
        processed_ids.add(task.task_id)

        # ── Disk-Write Interceptor: persist domain-agent output to ledger ──
        # Before signal processing, write the agent's (possibly header-fixed)
        # output to its domain-specific *ledger.md file on disk.
        # Queries (internal routing) are NOT written — only domain tasks.
        if not task.is_query and not task.parent:
            try:
                _append_to_ledger(output, task.agent, task.spec)
            except Exception as e:
                print(f"  [LedgerWrite] ⚠ Error appending to ledger: {e}")

        if task.is_query:
            query_results[task.parent] = output
            print(f"  [Query Tracker] Saved answer for parent task {task.task_id}")
            
            # If this was a DOC FETCH resolution, re-queue the original agent
            if task.task_id in pending_fetches:
                original_task = pending_fetches.pop(task.task_id)
                # Inject the DOC oracle's response as recalled memory
                original_task.context = (original_task.context or "") + "\n" + output
                original_task._fetch_depth = getattr(task, '_fetch_depth', 0)
                original_task.completed = False
                # Re-queue the original task WITHOUT incrementing iteration count
                work_queue.appendleft(original_task)
                print(f"  [FETCH] Injected DOC oracle response into {original_task.agent}, re-queued")

            # Re-queue parent task for QUERY/CONSULT
            if task.task_id in pending_queries:
                parent_task = pending_queries.pop(task.task_id)
                parent_task.context = (parent_task.context or "") + f"\n## Answer from {task.agent}:\n{output}\n"
                parent_task.completed = False
                work_queue.appendleft(parent_task)
                print(f"  [Query Tracker] Injected answer into {parent_task.agent}, re-queued")

        # Process signals from this task
        for signal in task.signals:
            stype = signal["type"]

            if stype == "QUERY":
                # Route query to target agent
                target = resolve_agent_name(signal["target"])
                query_task = Task(
                    agent=target,
                    spec=signal["content"],
                    task_id=f"query_{task.task_id}_{len(query_results)}",
                    parent=task.task_id,
                    is_query=True,
                )
                work_queue.appendleft(query_task)  # Priority: process before next task
                pending_queries[query_task.task_id] = task
                print(f"  [Signal] QUERY: {task.agent} -> {target}: {signal['content'][:60]}...")

            elif stype == "DELEGATE":
                # Create sub-task
                target = resolve_agent_name(signal["target"])
                sub_count = sum(1 for t in task_map.values()
                                if t.parent == task.task_id)
                if sub_count < MAX_SUBTASKS_PER_AGENT:
                    sub_task = Task(
                        agent=target,
                        spec=signal["content"],
                        task_id=f"sub_{task.task_id}_{sub_count + 1}",
                        parent=task.task_id,
                    )
                    work_queue.append(sub_task)
                    task_map[sub_task.task_id] = sub_task
                    print(f"  [Signal] DELEGATE: {task.agent} -> {target}: {signal['content'][:60]}...")
                else:
                    print(f"  [Signal] DELEGATE SKIPPED: {task.agent} already has {sub_count} sub-tasks (max {MAX_SUBTASKS_PER_AGENT})")

            elif stype == "VETO":
                all_vetos.append({
                    "from": task.agent,
                    "target": signal["target"],
                    "reason": signal["content"],
                    "task_id": task.task_id,
                })
                print(f"  [Signal] VETO: {task.agent} -> {signal['target']}: {signal['content'][:80]}...")

            elif stype == "OBJECT":
                all_objects.append({
                    "from": task.agent,
                    "target": signal["target"],
                    "concern": signal["content"],
                    "task_id": task.task_id,
                })
                print(f"  [Signal] OBJECT: {task.agent} -> {signal['target']}: {signal['content'][:80]}...")

            elif stype == "APPROVE":
                all_approvals[task.agent] = True
                print(f"  [Signal] APPROVE: {task.agent}")

            elif stype == "REVISE":
                target = resolve_agent_name(signal["target"])
                # Re-queue the target task for revision
                revise_task = Task(
                    agent=target,
                    spec=f"Revision requested by {task.agent}: {signal['content']}",
                    task_id=f"revise_{target}_{consensus_iteration}",
                    parent=task.task_id,
                    iteration=0,
                )
                work_queue.append(revise_task)
                print(f"  [Signal] REVISE: {task.agent} -> {target}: {signal['content'][:60]}...")

            elif stype == "RECOURSE":
                print(f"  [Signal] RECOURSE: {task.agent} -> Director: {signal['content'][:80]}...")
                # Director will handle this in the consensus phase

            elif stype == "CONSULT":
                target = resolve_agent_name(signal["target"])
                consult_task = Task(
                    agent=target,
                    spec=f"Consultation requested by {task.agent}: {signal['content']}",
                    task_id=f"consult_{task.task_id}_{len(query_results)}",
                    parent=task.task_id,
                    is_query=True,
                )
                work_queue.append(consult_task)
                pending_queries[consult_task.task_id] = task
                print(f"  [Signal] CONSULT: {task.agent} -> {target}: {signal['content'][:60]}...")

            elif stype == "FETCH":
                # Intercept [FETCH] signal: route through DOC oracle for reasoning.
                # DOC evaluates whether the requested header is optimal for the
                # requesting agent's current task, cross-references ledgers, and
                # resolves fuzzy matches. Then inject back WITHOUT iteration cost.
                
                # Track fetch depth to prevent infinite recursive loops
                fetch_depth = getattr(task, '_fetch_depth', 0)
                if fetch_depth >= 3:
                    max_depth_msg = (
                        "\n## Recalled Memory\n"
                        "**Source:** orchestrator\n"
                        "**Oracle note:** [FETCH ERROR: max recursion depth (3) reached. "
                        "Agent must synthesize from available context.]\n\n"
                    )
                    task.context = (task.context or "") + "\n" + max_depth_msg
                    task.completed = False
                    work_queue.appendleft(task)
                    print(f"  [FETCH] Max depth (3) reached for {task.agent}, returning error")
                    continue
                
                # Build the DOC oracle query: what memory does the agent need?
                fetch_target = signal.get("content", "")
                doc_query_spec = (
                    f"Memory Oracle: Resolve FETCH request\n"
                    f"Requesting agent: {task.agent}\n"
                    f"Their current task: {task.spec[:300]}\n"
                    f"Their fetch target: {fetch_target}\n"
                    f"---\n"
                    f"As Memory Oracle, evaluate if this fetch target is correct, "
                    f"cross-reference available ledgers, resolve the best section, "
                    f"and return the content formatted as ## Recalled Memory."
                )
                
                # Queue DOC to resolve the FETCH (NOT the original task yet)
                doc_fetch_task = Task(
                    agent="DOC",
                    spec=doc_query_spec,
                    task_id=f"doc_fetch_{task.task_id}_{fetch_depth}",
                    parent=task.task_id,
                    is_query=True,
                )
                doc_fetch_task._fetch_depth = fetch_depth + 1
                
                # Store original task — will be re-queued AFTER DOC answers
                pending_fetches[doc_fetch_task.task_id] = task
                
                work_queue.appendleft(doc_fetch_task)
                print(f"  [FETCH] Routed to DOC oracle (depth {fetch_depth+1}/3): {fetch_target[:80]}...")
            elif stype == "READ_OFFLOADED":
                block_id = signal.get("content", "")
                if not block_id:
                    print("  [ReadOffloaded] Empty block_id, skipping")
                    continue
                offloaded_content = read_offloaded_file(block_id)
                if budget is not None:
                    estimated_cost = len(offloaded_content) // 3
                    available = budget.hard_limit - budget.used
                    if estimated_cost > available:
                        print(f"  [ReadOffloaded] !! Block needs ~{estimated_cost} tokens, "
                              f"only {available} available. Paging out context...")
                        freed = _page_out_context(
                            task.context or "",
                            int((estimated_cost - available) * 3),
                            budget,
                        )
                        if freed > 0:
                            budget.used = max(0, budget.used - freed // 3)
                task.context = (task.context or "") + "\n" + offloaded_content
                task.completed = False
                work_queue.appendleft(task)
                print(f"  [ReadOffloaded] Injected block '{block_id}' into {task.agent}")


        # Check double-check for unresolved items
        if task.double_check and task.double_check["unresolved"]:
            unresolved = task.double_check["unresolved"].strip()
            if unresolved and unresolved.lower() not in ("none", "n/a", "nothing", ""):
                if task.iteration < MAX_ITERATIONS:
                    task.iteration += 1
                    task.completed = False
                    work_queue.appendleft(task)
                    print(f"  [Double-Check] {task.agent} has unresolved items, re-queuing (iter {task.iteration})")

        # Save snapshot after each task
        if snapshot:
            try:
                snapshot.save_proposal(
                    ALL_DOMAINS.get(resolve_agent_name(task.agent), {}).get("name", task.agent),
                    len(processed_ids),
                    f"output_{task.task_id}.md",
                    output,
                )
            except Exception as e:
                print(f"  [Snapshot] Save error: {e}")

    # ── Phase 5: Conflict Resolution ──────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  Phase 5: Conflict Resolution")
    print(f"{'='*70}")
    output_parts.append("\n## Phase 5: Conflict Resolution\n")

    conflict_resolutions = []
    for veto in all_vetos:
        target = resolve_agent_name(veto["target"])
        from_agent = resolve_agent_name(veto["from"])

        conflict_prompt = (
            f"## VETO Signal\n"
            f"**From:** {ALL_DOMAINS.get(from_agent, {}).get('name', from_agent)}\n"
            f"**Target:** {ALL_DOMAINS.get(target, {}).get('name', target)}\n"
            f"**Reason:** {veto['reason']}\n\n"
            f"## Original Feature Request\n{user_prompt}\n\n"
            f"## Director's Task Breakdown\n{director_output}\n\n"
        )

        # Include relevant outputs
        if veto["task_id"] in all_results:
            conflict_prompt += f"## Output that triggered VETO\n{all_results[veto['task_id']]}\n\n"

        conflict_output = call_ollama(
            ALL_DOMAINS["CONF"]["system_prompt"],
            conflict_prompt,
            f"Conflict Resolution: {veto['from']} vs {veto['target']}",
        )
        conflict_resolutions.append(conflict_output)
        output_parts.append(f"### VETO: {veto['from']} -> {veto['target']}\n{conflict_output}\n")

    for obj in all_objects:
        target = resolve_agent_name(obj["target"])
        from_agent = resolve_agent_name(obj["from"])

        object_prompt = (
            f"## OBJECT Signal\n"
            f"**From:** {ALL_DOMAINS.get(from_agent, {}).get('name', from_agent)}\n"
            f"**Target:** {ALL_DOMAINS.get(target, {}).get('name', target)}\n"
            f"**Concern:** {obj['concern']}\n\n"
            f"## Original Feature Request\n{user_prompt}\n\n"
        )

        if obj["task_id"] in all_results:
            object_prompt += f"## Output that triggered OBJECT\n{all_results[obj['task_id']]}\n\n"

        object_output = call_ollama(
            ALL_DOMAINS["CONF"]["system_prompt"],
            object_prompt,
            f"Conflict Resolution: {obj['from']} OBJECTS {obj['target']}",
        )
        conflict_resolutions.append(object_output)
        output_parts.append(f"### OBJECT: {obj['from']} -> {obj['target']}\n{object_output}\n")

    # ── Phase 6: Integration Review & Fix Loop ────────────────────────────
    print(f"\n{'='*70}")
    print(f"  Phase 6: Integration Review & Fix Loop")
    print(f"{'='*70}")
    output_parts.append("\n## Phase 6: Integration Review & Fix Loop\n")

    all_code_str = "\n\n".join([f"### {tid}\n{output}" for tid, output in all_results.items()])

    print("  [Pre-Flight] Running background compilers...")
    pre_flight_errors = ""

    # Platform-aware compilation check: use make on Linux/macOS, cmake on Windows
    try:
        if sys.platform == "win32":
            # On Windows, check for MSBuild or CMake build
            cmake_build = subprocess.run(
                ["cmake", "--build", "."],
                capture_output=True, text=True, cwd=PROJECT_ROOT,
                shell=True,
            )
            if cmake_build.returncode != 0:
                err_tail = "\n".join(cmake_build.stderr.splitlines()[-50:])
                pre_flight_errors += f"\n## C++ Compiler Errors:\n```\n{err_tail}\n```"
        else:
            make_process = subprocess.run(["make", "-j4"], capture_output=True, text=True, cwd=PROJECT_ROOT)
            if make_process.returncode != 0:
                err_tail = "\n".join(make_process.stderr.splitlines()[-50:])
                pre_flight_errors += f"\n## C++ Compiler Errors:\n```\n{err_tail}\n```"
    except Exception as e:
        pass

    for lf in PROJECT_ROOT.rglob("*.lua"):
        try:
            lua_proc = subprocess.run(["luac", "-p", str(lf)], capture_output=True, text=True)
            if lua_proc.returncode != 0:
                pre_flight_errors += f"\n## Lua Syntax Error in {lf.name}:\n```\n{lua_proc.stderr}\n```"
        except Exception:
            pass

    if pre_flight_errors:
        print("  [Pre-Flight] Syntax errors detected. Forcing Architect Fix Cycle.")
        fix_input = f"Your generated code failed local compilation/syntax checks. Fix the following errors:\n{pre_flight_errors}\n\nPreviously generated code:\n{all_code_str}"
        all_code_str = call_ollama(ARCHITECT_FIX_SYSTEM, fix_input, "Architect Syntax Fix", EXECUTION_MODEL)
        
        # Parse the fixed code blocks and update specific tasks
        for match in re.finditer(r"### (task_\d+)\n(.*?)(?=### task_\d+|\Z)", all_code_str, re.DOTALL):
            tid = match.group(1).strip()
            fixed_code = match.group(2).strip()
            if tid in all_results:
                all_results[tid] = fixed_code

    # Build conflict resolutions string to inject into review/fix prompts
    conflicts_str = ""
    if conflict_resolutions:
        conflicts_str = "## Conflict Resolutions (CRITICAL: Adhere to these compromises)\n" + "\n\n".join(conflict_resolutions) + "\n\n"

    # ── Insanity Detector: Pre-Truncation Fingerprint Hashing ──────────────
    # Initialize seen_code_hashes to detect infinite fix loops.
    # Hashing is done on the NORMALIZED INPUT fingerprint (pre-truncation)
    # rather than the LLM output. This ensures _block_aware_collapse inside
    # call_ollama() cannot corrupt the hash by mutating the context window.
    # See _normalize_fix_fingerprint() for details.
    seen_code_hashes = set()

    # ── Context Window Protection: Indexed Active Ledger ──
    active_ledger_path = PROJECT_ROOT / "docs" / "memory" / "active_run_ledger.md"
    active_ledger_content = ["## Active Run Code State\n"]
    active_toc = ["### Active Code Table of Contents\n"]
    
    for tid, output in all_results.items():
        header = f"### [{tid}]"
        active_ledger_content.append(f"{header}\n{output}\n")
        active_toc.append(f"- [{tid}](docs/memory/active_run_ledger.md#{tid}) -- use [FETCH:docs/memory/active_run_ledger.md#{tid}]")
        
    active_ledger_path.parent.mkdir(parents=True, exist_ok=True)
    active_ledger_path.write_text("\n".join(active_ledger_content), encoding="utf-8")
    active_code_index = "\n".join(active_toc) + "\n\n**INSTRUCTIONS:** The generated code is stored in the active ledger. You MUST use the `[FETCH:filepath#anchor]` tag to load the code for specific tasks to review them against our engine rules."

    review_output = ""
        
    review_verdict = "UNKNOWN"
    review_cycle = 0

    while review_cycle < REVIEW_MAX_ITERATIONS:
        review_cycle += 1
        print(f"\n  [Review-Fix] Cycle {review_cycle}/{REVIEW_MAX_ITERATIONS}")

        cycle_label = f"Integration Review (cycle {review_cycle})"

        review_input = (
            f"## Original Feature Request\n{user_prompt}\n\n"
            f"## Director's Task Breakdown\n{director_output}\n\n"
            f"{conflicts_str}"
            f"## Active Code Index\n{active_code_index}\n\n"
            f"{REVIEW_PROMPT}"
        )

        review_output = call_ollama(REVIEW_SYSTEM, review_input, cycle_label)
        
        # Process Reviewer FETCH signals
        fetch_signals = [s for s in extract_signals(review_output) if s["type"] == "FETCH"]
        if fetch_signals:
            print(f"  [Review-Fix] Reviewer fetching indexed memory...")
            fetched_content = handle_fetch_signal(fetch_signals[0]["match"])
            
            # Inject the fetched code into the active index prompt and loop WITHOUT incrementing the cycle count
            active_code_index += f"\n\n## Fetched Code Chunk\n{fetched_content}\n"
            output_parts.append(f"*(Reviewer fetched indexed code: {fetch_signals[0]['match']})*\n")
            continue 
        
        review_verdict = get_verdict(review_output)
        
        output_parts.append(f"### Review Cycle {review_cycle}\n{review_output}\n")

        print(f"  [Review-Fix] Verdict: {review_verdict}")

        if review_verdict == "PASS":
            print(f"  [Review-Fix] Passed on cycle {review_cycle}")
            break

        if review_verdict == "FAIL" and review_cycle < REVIEW_MAX_ITERATIONS:
            # Extract issues from review to give the architect context
            issues_match = re.search(
                r"### Issues\s*\n(.*?)(?=###|\Z)",
                review_output, re.DOTALL,
            )
            issues_text = issues_match.group(1).strip() if issues_match else review_output[:1000]

            # Call each domain architect to fix their code
            print(f"  [Review-Fix] Review failed — architect fixing...")
            fix_input = (
                f"## Original Feature Request\n{user_prompt}\n\n"
                f"## Review Issues (Cycle {review_cycle})\n{issues_text}\n\n"
                f"{conflicts_str}"
                f"## Active Code Index\n{active_code_index}\n\n"
                f"Fix ALL issues listed above. Produce corrected code for all domains. "
                f"Address every issue the Reviewer raised. "
                f"If you believe an issue is a false positive, explain why."
            )
            fix_output = call_ollama(ARCHITECT_FIX_SYSTEM, fix_input,
                                     f"Architect Fix (cycle {review_cycle})")
            output_parts.append(f"### Architect Fix Cycle {review_cycle}\n{fix_output}\n")

            # ── Insanity Detector: Pre-truncation fingerprint hash ──────────
            # Hash is computed on the NORMALIZED fix_input BEFORE call_ollama()
            # truncates it via _block_aware_collapse. This guarantees identical
            # fix requests hash identically regardless of context budget pressure
            # or collateral collapse artifacts injected by the truncator.
            finger_hash = hashlib.md5(
                _normalize_fix_fingerprint(fix_input).encode("utf-8")
            ).hexdigest()
            if finger_hash in seen_code_hashes:
                print(f"\n  [Insanity Detector] ⛔ Infinite fix loop detected! "
                      f"Fingerprint {finger_hash[:12]}... (same fix input repeated).")
                review_verdict = "BLOCKED"
                break
            seen_code_hashes.add(finger_hash)

            # Code is already updated per-task by the regex parser
            continue

        # If FAIL but we're out of cycles, break and report
        break

    # ── Phase 7: Consensus Gate ───────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  Phase 7: Consensus Gate")
    print(f"{'='*70}")
    output_parts.append("\n## Phase 7: Consensus Gate\n")

    review_passed = (review_verdict == "PASS")
    has_active_vetos = len(all_vetos) > 0
    has_recourses = any(s["type"] == "RECOURSE" for task_list in
                        [t.signals for t in task_map.values()] for s in task_list)

    consensus_checks = {
        "All tasks executed": len(processed_ids) >= len(tasks),
        "All sub-trees resolved": True,  # Work queue is empty
        "Double-check passed": all(
            not (t.double_check and t.double_check["unresolved"].strip().lower() not in ("none", "n/a", "", "nothing"))
            for t in task_map.values() if t.completed and t.double_check
        ),
        "No active VETOs": not has_active_vetos,
        "Review passed": review_passed,
        "No RECOURSE pending": not has_recourses,
    }

    all_checks_pass = all(consensus_checks.values())

    output_parts.append("### Consensus Checks\n")
    for check, passed in consensus_checks.items():
        status = "✅" if passed else "❌"
        output_parts.append(f"- {status} {check}\n")

    # ── Phase 8: Final Approval or Failure Report ─────────────────────────
    if review_verdict == "BLOCKED":
        # ── Suspend Protocol: Circuit Breaker Tripped ─────────────────────
        print(f"\n{'='*70}")
        print(f"  ⛔ CIRCUIT BREAKER TRIPPED — Suspending Pipeline")
        print(f"{'='*70}")
        output_parts.append("\n## ⛔ Pipeline Suspended (Circuit Breaker)\n")

        # Serialize suspended state
        suspend_state = {
            "work_queue": [
                {
                    "agent": t.agent,
                    "spec": t.spec,
                    "parent": t.parent,
                    "task_id": t.task_id,
                    "iteration": t.iteration,
                    "completed": t.completed,
                    "output": t.output,
                }
                for t in work_queue
            ],
            "all_results": all_results,
            "active_code_index": active_code_index,
            "director_output": director_output,
        }

        # Save checkpoint with BLOCKED phase
        save_checkpoint(run_id, "BLOCKED", suspend_state)

        # Append visible warning to output
        output_parts.append(
            "⚠️ **CIRCUIT BREAKER TRIPPED** ⚠️\n\n"
            "An infinite loop was detected. The pipeline has been suspended.\n\n"
            "Please manually correct the code below in your editor, then "
            "send me a prompt with your fix to resume.\n\n"
            f"_To resume, use checkpoint ID: `{run_id}`_\n"
        )
        output_parts.append(f"\n**Suspended code index:**\n\n```\n{active_code_index[:2000]}\n```\n")

    elif all_checks_pass:
        print(f"\n{'='*70}")
        print(f"  Phase 8: Final Approval")
        print(f"{'='*70}")
        output_parts.append("\n## Phase 8: Final Approval\n")

        final_input = (
            f"## Original Feature Request\n{user_prompt}\n\n"
            f"## Your Task Breakdown\n{director_output}\n\n"
            f"## Active Code Index\n{active_code_index}\n\n"
            f"## Integration Review\n{review_output}\n\n"
            f"Review the complete output. "
            f"State **APPROVED** if everything is satisfactory, "
            f"or **REVISION REQUIRED** with specific changes needed."
        )

        final_output = call_ollama(FINAL_APPROVAL_SYSTEM, final_input, "Director (Final Approval)", DIRECTOR_MODEL)
        output_parts.append(final_output + "\n")

        output_parts.append("\n---\n## ✅ Pipeline Complete\n")
        output_parts.append(f"**Tasks executed:** {len(processed_ids)}\n")
        output_parts.append(f"**Review reviews:** {review_cycle} cycle(s)\n")
        output_parts.append(f"**Review verdict:** {'PASS' if review_passed else 'FAIL'}\n")
        output_parts.append(f"**Status:** APPROVED\n")
        # Cleanup state: Task completed successfully
        if checkpoint_id:
            ckpt_file = CHECKPOINT_DIR / f"{checkpoint_id}.json"
            if ckpt_file.is_file(): 
                ckpt_file.rename(CHECKPOINT_DIR / f"{checkpoint_id}.archived.json")

    else:
        print(f"\n{'='*70}")
        print(f"  Phase 8: Failure Report")
        print(f"{'='*70}")
        output_parts.append("\n## Phase 8: Failure Report\n")

        failure_report = generate_failure_report(
            user_prompt, consensus_checks, all_vetos, all_objects,
            all_results, task_map, director_output,
        )
        output_parts.append(failure_report + "\n")

        # ── Phase 8b: Lead Producer Scope Post-Mortem ──────────────────────
        print(f"\n{'='*70}")
        print(f"  Phase 8b: Lead Producer — Scope Post-Mortem")
        print(f"{'='*70}")
        output_parts.append("\n## Phase 8b: Lead Producer Scope Post-Mortem\n")

        post_mortem_prompt = (
            f"## Original User Prompt\n{user_prompt}\n\n"
            f"## Director's Task Breakdown\n{director_output}\n\n"
            f"## Failure Report\n{failure_report}\n\n"
            f"Analyze the failure above. Determine:\n"
            f"1. **TOO_BROAD** — was the original prompt too wide for sub-agents "
            f"(requiring >{SCOPE_FILE_LIMIT} files or >{SCOPE_LINE_LIMIT} lines across multiple domains)?\n"
            f"2. **NARROW** — scope was fine, failure was technical "
            f"(model misinterpretation, real code bug, Ollama issue)?\n\n"
            f"If TOO_BROAD, suggest a narrower version of the prompt the user "
            f"could run instead.\n"
            f"If NARROW, recommend the user re-run with more specific constraints "
            f"or check Ollama status.\n\n"
            f"Start with **TOO_BROAD** or **NARROW** on its own line, "
            f"then explain your reasoning."
        )
        post_mortem_output = call_ollama(
            DIRECTOR_SYSTEM,
            post_mortem_prompt,
            "Lead Producer (Scope Post-Mortem)",
            REASONING_MODEL,
        )
        output_parts.append(post_mortem_output + "\n")

        # Cleanup state: Task failed (do not trap user in old checkpoint)
        if checkpoint_id:
            ckpt_file = CHECKPOINT_DIR / f"{checkpoint_id}.json"
            if ckpt_file.is_file(): 
                ckpt_file.rename(CHECKPOINT_DIR / f"{checkpoint_id}.archived.json")

    # ── Blueprint Step Chaining ─────────────────────────────────────────────
    if all_checks_pass:
        blueprint_path = PROJECT_ROOT / "docs" / "project_blueprint.md"
        if blueprint_path.is_file():
            try:
                bp_content = blueprint_path.read_text(encoding="utf-8")
                next_match = re.search(r"- \\[ \\] (Task \\d+: .+)", bp_content)
                if next_match:
                    next_step = next_match.group(1)
                    output_parts.append(
                        f"\n### Next Blueprint Step\n"
                        f"**{next_step}** — run with:\n"
                        f"`python pipeline.py \"{next_step}\"`\n"
                    )
                else:
                    output_parts.append("\n### Blueprint Complete ✅\n"
                                        "All tasks in the blueprint have been executed.\n")
            except Exception:
                pass

    # ── Save output ───────────────────────────────────────────────────────
    final_output = "\n".join(output_parts)
    output_path = PROJECT_ROOT / f"pipeline_output_{run_id}.md"
    try:
        output_path.write_text(final_output, encoding="utf-8")
        print(f"\n  Output saved to: {output_path}")
    except Exception as e:
        print(f"\n  Could not save output: {e}")

    # Save snapshot final
    if snapshot:
        try:
            snapshot.apply_proposals()
            print(f"  [Snapshot] Applied proposals")
        except Exception as e:
            print(f"  [Snapshot] Apply error: {e}")

    # ── Session Timeline Log (MODIFICATION flow) ───────────────────────
    agent_list = [ALL_DOMAINS.get(resolve_agent_name(t.agent), {}).get("name", t.agent)
                  for t in task_map.values() if t.completed]
    tools_accessed = ", ".join(sorted(set(
        [f"ledger_toc({t.agent})" for t in task_map.values()] +
        [f"file_context({t.agent})" for t in task_map.values()] +
        ["director", f"review (x{review_cycle})"] +
        (["conflict_resolution"] if all_vetos or all_objects else [])
    )))

    log_to_session_timeline(
        user_input=user_prompt,
        agent_assigned=", ".join(agent_list[:5]),
        tools_accessed=tools_accessed[:300],
        final_output=final_output,
    )

    print(f"\n{'='*70}")
    print(f"  Pipeline Complete — {'APPROVED' if all_checks_pass else ('SUSPENDED' if review_verdict == 'BLOCKED' else 'FAILED')}")
    print(f"{'='*70}")

    # ── Tag System Auto-Detection (Phase 8 post-processing) ──────────
    # Suggest [Stable Core Concept] or [Likely Regression] tags based on
    # session timeline history. Tags always use [Suggested] suffix.
    try:
        suggester = TagSuggester()
        tag_suggestions = suggester.analyze(SESSION_TIMELINE_PATH, run_id)
        if tag_suggestions:
            output_parts.append("\n## 🏷️ Tag Suggestions (Auto-Detected)\n")
            for tag in tag_suggestions:
                output_parts.append(f"- {tag}\n")
            # Also append to pipeline_master_checklist.md Tag System section
            checklist_path = PROJECT_ROOT / "docs" / "pipeline_master_checklist.md"
            if checklist_path.is_file():
                try:
                    checklist_content = checklist_path.read_text(encoding="utf-8")
                    tag_section_marker = "### Tag System (Phase 9 — Future)"
                    if tag_section_marker in checklist_content:
                        # Replace or append after the tag section
                        tag_block = "\n".join(f"  - {tag}" for tag in tag_suggestions)
                        tag_block_full = f"\n### Tag Suggestions (Run: {run_id[:20]})\n`{datetime.now().isoformat()}`\n{tag_block}\n"
                        if "### Tag Suggestions" in checklist_content:
                            # Replace existing suggestions block
                            checklist_content = re.sub(
                                r"### Tag Suggestions.*?\n(?:  - .*\n)*",
                                tag_block_full,
                                checklist_content,
                            )
                        else:
                            # Append after the Tag System section
                            checklist_content += f"\n{tag_block_full}\n"
                        checklist_path.write_text(checklist_content, encoding="utf-8")
                except Exception as e:
                    print(f"  [TagSuggester] Could not update checklist: {e}")
            # Update final_output to include suggestions
            final_output = "\n".join(output_parts)
            # Log tag suggestions to session timeline
            log_to_session_timeline(
                user_input=f"TagSuggester post-processing (run: {run_id[:40]})",
                agent_assigned="TagSuggester",
                tools_accessed=f"analyze({SESSION_TIMELINE_PATH.name}, {run_id[:20]}...)",
                final_output="\n".join(tag_suggestions) if tag_suggestions else "No tags suggested",
            )
            print(f"  [TagSuggester] {len(tag_suggestions)} tag(s) suggested")
    except Exception as e:
        print(f"  [TagSuggester] Error: {e}")
        import traceback
        traceback.print_exc()

    return final_output


def generate_failure_report(user_prompt: str, consensus_checks: dict,
                            vetos: list, objects: list,
                            all_results: dict, task_map: dict,
                            director_output: str) -> str:
    """Generate a curated failure report with suggested manual breakdown."""
    parts = []

    parts.append("## Pipeline Failure Report\n")
    parts.append(f"**Feature request:** {user_prompt}\n")
    parts.append(f"**Consensus iterations exhausted:** {MAX_CONSENSUS_ITERATIONS}\n\n")

    # Failed checks
    parts.append("### Failed Checks\n")
    for check, passed in consensus_checks.items():
        if not passed:
            parts.append(f"- ❌ {check}\n")
    parts.append("")

    # Blocking VETOs
    if vetos:
        parts.append("### Blocking VETOs\n")
        for v in vetos:
            from_name = ALL_DOMAINS.get(resolve_agent_name(v["from"]), {}).get("name", v["from"])
            target_name = ALL_DOMAINS.get(resolve_agent_name(v["target"]), {}).get("name", v["target"])
            parts.append(f"1. **{from_name}** VETO'd **{target_name}**\n")
            parts.append(f"   - Reason: {v['reason']}\n")
            if v["task_id"] in all_results:
                # Extract first 200 chars of the offending output
                offending = all_results[v["task_id"]][:200]
                parts.append(f"   - Offending output: {offending}...\n")
            parts.append("")

    # Blocking OBJECTs
    if objects:
        parts.append("### Unresolved OBJECTions\n")
        for o in objects:
            from_name = ALL_DOMAINS.get(resolve_agent_name(o["from"]), {}).get("name", o["from"])
            target_name = ALL_DOMAINS.get(resolve_agent_name(o["target"]), {}).get("name", o["target"])
            parts.append(f"1. **{from_name}** OBJECTed to **{target_name}**\n")
            parts.append(f"   - Concern: {o['concern']}\n")
            parts.append("")

    # Suggested manual decomposition
    parts.append("### Suggested Manual Decomposition\n")
    parts.append("To resolve this manually, break into these sub-tasks:\n")

    # Generate suggested /arch_* commands based on failed domains
    suggested_commands = []
    for v in vetos:
        target = resolve_agent_name(v["target"])
        domain = ALL_DOMAINS.get(target, {})
        tag = domain.get("tag", f"[{target}]")
        name = domain.get("name", target)
        suggested_commands.append(
            f"1. `/arch_{target.lower()}` \"{name}: {v['reason']}\""
        )
    for o in objects:
        target = resolve_agent_name(o["target"])
        domain = ALL_DOMAINS.get(target, {})
        tag = domain.get("tag", f"[{target}]")
        name = domain.get("name", target)
        suggested_commands.append(
            f"1. `/arch_{target.lower()}` \"{name}: {o['concern']}\""
        )

    if not suggested_commands:
        suggested_commands.append(
            "1. `/pipeline` \"Re-run the original prompt with more specific constraints\""
        )

    for cmd in suggested_commands[:5]:
        parts.append(f"{cmd}\n")

    parts.append("\n### Cross-Reference\n")
    parts.append("- docs/rules_cpp.md — C++ engine rules\n")
    parts.append("- docs/rules_lua.md — Lua scripting rules\n")
    parts.append("- docs/rules_phys.md — Physics integration rules\n")
    parts.append("- docs/rules_shader.md — Shader development rules\n")
    parts.append("- docs/engine_lua_bridge_contract.md — C++/Lua API contract\n")

    return "\n".join(parts)


# ── Mesh Work Queue API ─────────────────────────────────────────────────────
# These are the exports expected by pipeline_server.py for the REST API.

_MESH_TASK_REGISTRY = {}        # task_id -> task metadata
_MESH_RESULTS = {}              # task_id -> final output
_MESH_WORK_QUEUE = deque()      # pending tasks
_MESH_REGISTRY_LOCK = False     # simple flag-based coarsening to avoid race

def submit_mesh_task(task_type: str, payload: dict, priority: int = 0) -> str:
    """Submit a new mesh task to the global queue. Returns a task_id."""
    global _MESH_WORK_QUEUE
    task_id = f"mesh_{datetime.now().strftime('%H%M%S%f')}_{hash(str(payload)) % 10000}"
    _MESH_TASK_REGISTRY[task_id] = {
        "task_id": task_id,
        "task_type": task_type,
        "payload": payload,
        "priority": priority,
        "status": "queued",
        "created": datetime.now().isoformat(),
        "completed": None,
        "error": None,
    }
    _MESH_WORK_QUEUE.append(task_id)
    # Re-sort by priority: higher priority first
    sorted_list = sorted(
        list(_MESH_WORK_QUEUE),
        key=lambda tid: _MESH_TASK_REGISTRY.get(tid, {}).get("priority", 0),
        reverse=True,
    )
    _MESH_WORK_QUEUE.clear()
    _MESH_WORK_QUEUE.extend(sorted_list)
    print(f"  [MeshQueue] Submitted {task_id} (type={task_type}, priority={priority})")
    return task_id


def get_mesh_task_status(task_id: str) -> dict:
    """Get the status of a submitted mesh task."""
    return _MESH_TASK_REGISTRY.get(task_id)


def list_mesh_tasks() -> list:
    """List all mesh tasks in the registry."""
    return list(_MESH_TASK_REGISTRY.values())


def cancel_mesh_task(task_id: str) -> bool:
    """Cancel a queued mesh task. Returns True if cancelled."""
    global _MESH_WORK_QUEUE
    if task_id in _MESH_TASK_REGISTRY:
        entry = _MESH_TASK_REGISTRY[task_id]
        if entry["status"] in ("queued",):
            entry["status"] = "cancelled"
            entry["completed"] = datetime.now().isoformat()
            # Remove from work queue if present
            if task_id in _MESH_WORK_QUEUE:
                temp = [t for t in _MESH_WORK_QUEUE if t != task_id]
                _MESH_WORK_QUEUE.clear()
                _MESH_WORK_QUEUE.extend(temp)
            print(f"  [MeshQueue] Cancelled {task_id}")
            return True
    return False


def get_mesh_work_queue() -> list:
    """Get the current work queue as a list of task IDs with metadata."""
    return [
        {"task_id": tid, **{k: v for k, v in _MESH_TASK_REGISTRY.get(tid, {}).items() if k != "payload"}}
        for tid in _MESH_WORK_QUEUE
    ]


def get_mesh_results() -> list:
    """Get all completed mesh results."""
    return [
        {"task_id": tid, "output": output, "completed": _MESH_TASK_REGISTRY.get(tid, {}).get("completed")}
        for tid, output in _MESH_RESULTS.items()
    ]


# ── Signal Type Definitions ─────────────────────────────────────────────────

from enum import Enum

class SignalType(Enum):
    """Enum of all mesh communication signal types."""
    QUERY = "QUERY"
    DELEGATE = "DELEGATE"
    RESULT = "RESULT"
    APPROVE = "APPROVE"
    REVISE = "REVISE"
    VETO = "VETO"
    OBJECT = "OBJECT"
    RECOURSE = "RECOURSE"
    CONSULT = "CONSULT"


class MeshSignal:
    """A parsed mesh signal with typed fields."""
    def __init__(self, signal_type: SignalType, target: str = None,
                 content: str = None, source: str = None):
        self.type = signal_type
        self.target = target
        self.content = content
        self.source = source

    def to_dict(self) -> dict:
        return {
            "type": self.type.value,
            "target": self.target,
            "content": self.content,
            "source": self.source,
        }

    def __repr__(self):
        return f"MeshSignal({self.type.value}, target={self.target})"


def parse_signal(text: str, source: str = None) -> list:
    """Parse a text for mesh signals, returning MeshSignal objects.

    This is the structured export version of extract_signals().
    Used by pipeline_server.py's /mesh/detect-edits and /mesh/resolve-conflict endpoints.
    """
    signals = extract_signals(text)
    return [
        MeshSignal(
            signal_type=SignalType(s["type"]),
            target=s.get("target"),
            content=s.get("content"),
            source=source,
        )
        for s in signals
    ]


# ── Cross-Agent Edit Detection ──────────────────────────────────────────────

class ConsensusResult:
    """Result of a conflict resolution or consensus check."""
    def __init__(self, verdict: str, merged_code: str = "",
                 explanation: str = "", warnings: list = None):
        self.verdict = verdict          # "SUSTAIN" | "OVERRULE" | "COMPROMISE"
        self.merged_code = merged_code
        self.explanation = explanation
        self.warnings = warnings or []

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "merged_code": self.merged_code,
            "explanation": self.explanation,
            "warnings": self.warnings,
        }

    def __repr__(self):
        return f"ConsensusResult({self.verdict})"


def detect_cross_agent_edits(agent_a_code: str, agent_b_code: str) -> list:
    """Detect conflicting edits between two agents' code outputs.

    Returns a list of conflict dicts with file, line ranges, and descriptions.
    Uses simple line-by-line diff. In production, would use `difflib`.
    """
    import difflib
    conflicts = []

    a_lines = agent_a_code.splitlines()
    b_lines = agent_b_code.splitlines()

    # Use difflib to find differing regions
    differ = difflib.Differ()
    diff = list(differ.compare(a_lines, b_lines))

    conflict_start = None
    conflict_lines_a = []
    conflict_lines_b = []

    for i, line in enumerate(diff):
        if line.startswith("- ") or line.startswith("+ "):
            if conflict_start is None:
                conflict_start = i
            if line.startswith("- "):
                conflict_lines_a.append((i, line[2:]))
            else:
                conflict_lines_b.append((i, line[2:]))
        else:
            if conflict_start is not None:
                # We ended a diff region — check if both sides modified
                if conflict_lines_a and conflict_lines_b:
                    conflicts.append({
                        "region_start": conflict_start,
                        "region_end": i,
                        "agent_a_lines": [
                            {"line": ln, "content": ct}
                            for ln, ct in conflict_lines_a
                        ],
                        "agent_b_lines": [
                            {"line": ln, "content": ct}
                            for ln, ct in conflict_lines_b
                        ],
                        "description": f"Conflict at lines {conflict_lines_a[0][0]}-{conflict_lines_a[-1][0]} (A) vs {conflict_lines_b[0][0]}-{conflict_lines_b[-1][0]} (B)",
                    })
                conflict_start = None
                conflict_lines_a = []
                conflict_lines_b = []

    # Handle final region
    if conflict_lines_a and conflict_lines_b:
        conflicts.append({
            "region_start": conflict_start,
            "region_end": len(diff),
            "agent_a_lines": [
                {"line": ln, "content": ct}
                for ln, ct in conflict_lines_a
            ],
            "agent_b_lines": [
                {"line": ln, "content": ct}
                for ln, ct in conflict_lines_b
            ],
            "description": f"Final conflict at lines {conflict_lines_a[0][0]}-{conflict_lines_a[-1][0]} (A) vs {conflict_lines_b[0][0]}-{conflict_lines_b[-1][0]} (B)",
        })

    return conflicts


def resolve_conflict(agent_a_code: str, agent_b_code: str,
                     veto_justification: str,
                     feature_request: str) -> ConsensusResult:
    """Resolve a conflict between two agents' code using the CONF agent.

    Returns a ConsensusResult with verdict and merged code.
    """
    prompt = (
        f"## Original Feature Request\n{feature_request}\n\n"
        f"## VETO Justification\n{veto_justification}\n\n"
        f"## Agent A Code\n{agent_a_code}\n\n"
        f"## Agent B Code\n{agent_b_code}\n\n"
        f"Resolve this conflict. Output one of:\n"
        f"- **SUSTAIN** VETO: Agent B's original is more correct\n"
        f"- **OVERRULE** VETO: Agent A's change is technically correct\n"
        f"- **COMPROMISE** with a merged version\n\n"
        f"Then output the final merged code under ## Merged Code."
    )

    result_text = call_ollama(
        ALL_DOMAINS["CONF"]["system_prompt"],
        prompt,
        "Conflict Resolution (API)",
    )

    # Parse verdict
    verdict = "COMPROMISE"  # default
    if "SUSTAIN" in result_text.upper():
        verdict = "SUSTAIN"
    elif "OVERRULE" in result_text.upper():
        verdict = "OVERRULE"

    # Extract merged code block if present
    merged_code = ""
    code_match = re.search(
        r"## Merged Code\s*\n```(?:\w+)?\s*\n(.*?)```",
        result_text, re.DOTALL,
    )
    if code_match:
        merged_code = code_match.group(1).strip()

    return ConsensusResult(
        verdict=verdict,
        merged_code=merged_code or result_text,
        explanation=result_text[:500],  # first 500 chars as summary
    )


# ── Overload: generate_failure_report for REST API ──────────────────────────
# pipeline_server.py calls generate_failure_report(task_id, error_details)
# while the internal pipeline uses generate_failure_report(...) with 7 args.
# We add an overload wrapper that accepts the 2-arg REST signature.

def _generate_failure_report_rest(task_id: str, error_details: str) -> str:
    """Generate a failure report from the REST API (2-arg signature)."""
    task_data = _MESH_TASK_REGISTRY.get(task_id, {})
    return (
        f"## Pipeline Failure Report (Mesh API)\n\n"
        f"**Task ID:** {task_id}\n"
        f"**Task Type:** {task_data.get('task_type', 'unknown')}\n"
        f"**Error Details:** {error_details}\n\n"
        f"### Suggested Action\n"
        f"1. Check Ollama is running at {OLLAMA_HOST}\n"
        f"2. Verify the model '{EXECUTION_MODEL}' is pulled\n"
        f"3. Re-run with more specific constraints\n"
        f"4. Check docs/ for relevant API references\n\n"
        f"### Cross-Reference\n"
        f"- docs/rules_cpp.md\n"
        f"- docs/rules_lua.md\n"
        f"- docs/rules_phys.md\n"
        f"- docs/rules_shader.md\n"
        f"- docs/engine_lua_bridge_contract.md\n"
    )


# ── Progressive Output Support ──────────────────────────────────────────────
# For streaming / progressive updates during long pipeline runs.

PROGRESS_LISTENERS = []  # list of callbacks receiving (phase, status, detail)

def register_progress_listener(callback):
    """Register a callback for progressive output updates.
    callback(phase: str, status: str, detail: str) -> None
    """
    PROGRESS_LISTENERS.append(callback)

def _emit_progress(phase: str, status: str, detail: str = ""):
    """Emit a progress update to all registered listeners."""
    for cb in PROGRESS_LISTENERS:
        try:
            cb(phase, status, detail)
        except Exception:
            pass


# ── TagSuggester: Post-Pipeline Tag Auto-Detection ─────────────────────────
# After pipeline completion (Phase 8), analyzes the session timeline to suggest
# [Stable Core Concept] or [Likely Regression] tags based on run history.
# Tags always use [Suggested] suffix — never auto-applied as authoritative.

class TagSuggester:
    """Analyzes session timeline history to suggest stability/regression tags.

    Detection logic:
    - Stable: 3+ consecutive APPROVED runs on same code area, zero vetos
    - Regression: 2+ fix cycles exhausted on same code area, any BLOCKED trip
    """

    @staticmethod
    def _extract_run_data(timeline_content: str) -> list:
        """Parse session timeline into structured run entries."""
        runs = []
        # Split on ## Session Event markers
        blocks = re.split(r'(?=## Session Event)', timeline_content)
        for block in blocks:
            if not block.strip():
                continue
            entry = {
                "agent": "",
                "verdict": "UNKNOWN",
                "area": "",
                "has_veto": False,
                "is_blocked": False,
                "fix_cycles": 0,
            }
            # Extract agent
            agent_m = re.search(r'\*\*Agent Assigned:\*\*\s*(.+)', block)
            if agent_m:
                entry["agent"] = agent_m.group(1).strip()
            # Extract area from user input
            input_m = re.search(r'\*\*User Input:\*\*\s*(.+)', block)
            if input_m:
                entry["area"] = input_m.group(1).strip()[:80]
            # Check for APPROVED or BLOCKED
            if "APPROVED" in block:
                entry["verdict"] = "APPROVED"
            elif "BLOCKED" in block or "CIRCUIT BREAKER" in block:
                entry["verdict"] = "BLOCKED"
                entry["is_blocked"] = True
            elif "FAIL" in block or "REVISION REQUIRED" in block:
                entry["verdict"] = "FAIL"
            # Check for VETO signals
            if "[VETO:" in block or "VETO" in block:
                entry["has_veto"] = True
            # Count fix cycles
            cycle_matches = re.findall(r'Review Cycle \d+|Architect Fix Cycle \d+|review.*cycle', block, re.IGNORECASE)
            entry["fix_cycles"] = len(cycle_matches)
            runs.append(entry)
        return runs

    def analyze(self, session_timeline_path: Path, run_id: str) -> list:
        """Analyze session timeline and return tag suggestion strings."""
        suggestions = []
        if not session_timeline_path.is_file():
            return suggestions

        try:
            content = session_timeline_path.read_text(encoding="utf-8")
        except Exception:
            return suggestions

        runs = self._extract_run_data(content)

        # Group runs by approximate area (first 60 chars of user input)
        area_runs = {}
        for r in runs:
            area_key = r["area"][:60]
            if area_key not in area_runs:
                area_runs[area_key] = []
            area_runs[area_key].append(r)

        for area, area_entries in area_runs.items():
            if not area:
                continue

            # ── Stable Detection: 3+ consecutive APPROVED, zero vetos ──
            approved_count = 0
            has_veto = False
            for r in reversed(area_entries):  # newest first
                if r["verdict"] == "APPROVED":
                    approved_count += 1
                else:
                    break  # stop at first non-APPROVED
                if r["has_veto"]:
                    has_veto = True

            if approved_count >= 3 and not has_veto:
                suggestions.append(
                    self.suggest_stable(area[:40], run_id)
                )

            # ── Regression Detection: 2+ fix cycles, any BLOCKED ──
            total_fix_cycles = sum(r["fix_cycles"] for r in area_entries)
            any_blocked = any(r["is_blocked"] for r in area_entries)
            if total_fix_cycles >= 2 and any_blocked:
                suggestions.append(
                    self.suggest_regression(area[:40], run_id)
                )

        return suggestions

    @staticmethod
    def suggest_stable(area: str, run_ids: str) -> str:
        return (
            f"[Stable Core Concept — Suggested] (run_ids: {run_ids[:60]}, "
            f"area: {area[:60]}, 3+ consecutive approvals, zero vetos)"
        )

    @staticmethod
    def suggest_regression(area: str, run_ids: str) -> str:
        return (
            f"[Likely Regression — Suggested] (run_ids: {run_ids[:60]}, "
            f"area: {area[:60]}, 2+ fix cycles exhausted, BLOCKED trip detected)"
        )


# ── Entry Point ────────────────────────────────────────────────────────────

# ── Session Segmentation Import ───────────────────────────────────────────
try:
    from pipeline_session import SessionManager, get_or_create_session
    HAS_SESSION_MANAGER = True
except ImportError:
    HAS_SESSION_MANAGER = False


def run_pipeline(user_prompt: str, checkpoint_id: str = None,
                 session_id: str = None) -> str:
    """Main entry point. Returns the full pipeline output as a string.

    Args:
        user_prompt:  The feature request prompt.
        checkpoint_id: Optional checkpoint ID to resume from.
        session_id:   Optional session segment ID for tracking continuity.
                      Auto-generated if not provided (when SessionManager
                      is available).
    """
    # ── Session Segmentation: auto-create or resume session ──────────────
    session_mgr = None
    if HAS_SESSION_MANAGER:
        session_mgr = get_or_create_session(
            user_prompt=user_prompt,
            session_id=session_id,
        )
        session_mgr.set_model(EXECUTION_MODEL)
        if session_id:
            print(f"  [Session] Resumed session: {session_id}")
        else:
            print(f"  [Session] Started new session: {session_mgr.session_id}")

    _emit_progress("init", "started", f"Processing: {user_prompt[:60]}...")
    result = run_mesh_pipeline(user_prompt, checkpoint_id, session_mgr)
    _emit_progress("complete", "done")

    if session_mgr:
        session_mgr.mark_completed()

    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Midway Mesh Pipeline Orchestrator")
    parser.add_argument("prompt", nargs="?", default=None,
                        help="Feature request prompt")
    parser.add_argument("--checkpoint", type=str, default=None,
                        help="Resume from a checkpoint ID")
    parser.add_argument("--list-checkpoints", action="store_true",
                        help="List all saved checkpoints")
    parser.add_argument("--session-id", type=str, default=None,
                        help="Session ID for continuity tracking (auto-generated if absent)")
    parser.add_argument("--chat", action="store_true",
                        help="Force conversational CHAT mode (bypasses intent classification)")

    args = parser.parse_args()

    if args.chat:
        # Force CHAT mode: inject marker that is detected by run_mesh_pipeline
        # to bypass intent classification entirely.
        chat_prompt = f"[CHAT_FORCED] {args.prompt}" if args.prompt else "[CHAT_FORCED] Hello"
        result = run_pipeline(chat_prompt, args.checkpoint, args.session_id)
        print("\n" + result)
        sys.exit(0)

    if args.list_checkpoints:
        checkpoints = list_checkpoints()
        if checkpoints:
            print("Saved checkpoints:")
            for c in checkpoints:
                print(f"  {c.get('checkpoint_id')}: {c.get('phase')} ({c.get('timestamp')})")
        else:
            print("No checkpoints found.")
        sys.exit(0)

    if not args.prompt:
        parser.print_help()
        sys.exit(1)

    result = run_pipeline(args.prompt, args.checkpoint, args.session_id)
    print("\n" + result)
