"""
patch_payloads.py — String payload constants for patch_virtual_context.py
=========================================================================
Extracted to keep patch_virtual_context.py under 1 000 lines.

These are the raw Python/text strings that are *inserted into* pipeline.py
by the patcher.  They contain no import-time side-effects; importing this
module is safe even when pipeline.py does not yet have virtual-context code.

Public names (imported by patch_virtual_context.py):
    OFFLOAD_STORE_CLASS
    BLOCK_AWARE_COLLAPSE_REPLACEMENT
    READ_OFFLOADED_FUNCTION
    READ_OFFLOADED_SIGNAL_PATTERN_REPLACEMENT
    READ_OFFLOADED_HANDLER_BLOCK
    AGENT_PROMPT_EXTENSION
"""

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1: OffloadStore class + singleton
# Inserted into pipeline.py after TokenBudget, before LRU Doc Cache.
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
        safe_id = re.sub(r'[^a-zA-Z0-9_\-]', '_', block_id)
        return self.store_dir / f"block_{safe_id}.json"

    def store_block(self, block_id: str, header: str,
                    body_lines: list, metadata: dict = None) -> bool:
        """Persist a pruned context block to disk."""
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
            self.garbage_collect(self._max_mb)
            return True
        except OSError as e:
            print(f"  [OffloadStore] ⚠ Failed to store block '{block_id}': {e}")
            return False

    def retrieve_block(self, block_id: str) -> str:
        """Read a previously offloaded block from disk."""
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
        """Remove a block from disk and index."""
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
        """Return a manifest of all stored blocks."""
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
        """Total bytes consumed by the offload store on disk."""
        total = 0
        if self.store_dir.is_dir():
            for f in self.store_dir.glob("block_*.json"):
                try:
                    total += f.stat().st_size
                except OSError:
                    pass
        return total

    def garbage_collect(self, max_mb: int = 512) -> int:
        """Evict oldest blocks when store exceeds max_mb."""
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
# Replaces the existing method inside TokenBudget in pipeline.py.
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
        """
        if len(text) <= available_chars:
            return text

        lines = text.splitlines(keepends=True)
        blocks = []
        current_block = None
        in_fence = False

        for i, ln in enumerate(lines):
            stripped = ln.strip()

            if stripped.startswith("```"):
                in_fence = not in_fence
                if current_block is None:
                    blocks.append({
                        "header": ln.rstrip("\n"),
                        "body_lines": [],
                        "is_fence_block": True,
                    })
                    current_block = None
                else:
                    if current_block:
                        blocks.append(current_block)
                        current_block = None
                continue

            is_header = False

            if not in_fence:
                func_match = re.match(
                    r'^(\s*(?:static\s+|inline\s+|virtual\s+|const\s+)*'
                    r'(?:void|int|float|double|bool|char|std::\w+|\w+(?:<[^>]+>)?)\s+'
                    r'(?:[*&]?\s*)?'
                    r'\w+\s*\([^)]*\)\s*(?:const\s*)?(?:override\s*)?(?:final\s*)?'
                    r'(?:\s*throw\s*\([^)]*\)\s*)?\{?\s*)$',
                    stripped,
                )
                is_header = is_header or bool(func_match)
                if not is_header and stripped.endswith("{"):
                    if len(stripped) > 1 or (i > 0 and lines[i-1].strip().endswith(")")):
                        is_header = True

            if stripped.startswith("##") or stripped.startswith("###") or stripped.startswith("# "):
                is_header = True
            if re.match(r'^\d+\.\s+\*\*', stripped):
                is_header = True

            if is_header:
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
                    if not blocks or blocks[-1].get("_is_preamble"):
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

        if current_block:
            blocks.append(current_block)

        header_overhead = sum(len(blk.get("header") or "") + 1 for blk in blocks)
        body_budget = available_chars - header_overhead
        if body_budget < 0:
            result = "\n".join(blk.get("header") or "" for blk in blocks if blk.get("header"))
            return result[:available_chars] if len(result) > available_chars else result

        collapsed_blocks = []
        remaining_body = body_budget

        for blk in reversed(blocks):
            if blk.get("_is_preamble") or blk.get("is_fence_block"):
                body_text = "".join(blk["body_lines"])
                header_text = (blk.get("header") or "") + "\n" if blk.get("header") else ""
                total_len = len(header_text) + len(body_text)
                if total_len <= remaining_body:
                    collapsed_blocks.insert(0, blk)
                    remaining_body -= total_len
                elif body_text and remaining_body > len(header_text) + 10:
                    keep_len = remaining_body - len(header_text)
                    blk["body_lines"] = [body_text[:keep_len] + "\n[... truncated ...]\n"]
                    collapsed_blocks.insert(0, blk)
                    remaining_body = 0
                elif header_text and remaining_body >= len(header_text):
                    blk["body_lines"] = ["[... body collapsed ...]\n"]
                    collapsed_blocks.insert(0, blk)
                    remaining_body -= len(header_text) + 30
                else:
                    self._offload_and_placeholder(blk, collapsed_blocks, remaining_body)
                    remaining_body = 0
                continue

            header_text = (blk.get("header") or "") + "\n"
            body_text = "".join(blk["body_lines"])
            header_len = len(header_text)
            body_len = len(body_text)

            if header_len + body_len <= remaining_body:
                collapsed_blocks.insert(0, blk)
                remaining_body -= (header_len + body_len)
            elif header_len <= remaining_body:
                blk["body_lines"] = ["[... function body collapsed for context budget ...]\n"]
                collapsed_blocks.insert(0, blk)
                remaining_body -= (header_len + 70)
            else:
                dropped_count = 0
                for older_blk in blocks[:blocks.index(blk)]:
                    if not older_blk.get("_is_preamble"):
                        dropped_count += 1
                        self._offload_single_block(older_blk)
                self._offload_single_block(blk)
                dropped_count += 1
                if dropped_count > 0:
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
                break

        result_lines = []
        for blk in collapsed_blocks:
            h = blk.get("header")
            if h:
                result_lines.append(h)
            for body_ln in blk["body_lines"]:
                result_lines.append(body_ln.rstrip("\n"))

        result = "\n".join(result_lines)
        if len(result) > available_chars:
            result = result[:available_chars] + "\n[... final truncation ...]"
        return result

    def _offload_single_block(self, blk: dict) -> None:
        """Persist a single block to the offload store."""
        try:
            from pipeline import get_offload_store
            store = get_offload_store()
        except ImportError:
            try:
                from __main__ import get_offload_store
                store = get_offload_store()
            except ImportError:
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
            char_count = len(header) + sum(len(l) for l in body_lines)
            print(f"  [OffloadStore] ✓ Offloaded block '{block_id}' ({char_count} chars)")

    def _offload_and_placeholder(self, blk: dict, collapsed_blocks: list,
                                  remaining_body: int) -> None:
        """Offload a block and add a short placeholder notice."""
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
        return (
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
'''


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3: read_offloaded_file + handle_read_offloaded_signal + _page_out_context
# Inserted into pipeline.py after handle_fetch_signal.
# ══════════════════════════════════════════════════════════════════════════════

READ_OFFLOADED_FUNCTION = r'''

# ── Virtual Context: read_offloaded_file ────────────────────────────────────

def read_offloaded_file(block_id: str) -> str:
    """Retrieve a previously offloaded context block from disk."""
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
    """Process a [READ_OFFLOADED:<block_id>] signal with budget-aware paging."""
    content = read_offloaded_file(block_id)
    if content.startswith("\n## Offloaded Context Retrieval: ERROR"):
        return content
    if token_budget is not None:
        estimated_tokens = len(content) // 3
        available = token_budget.hard_limit - token_budget.used
        if estimated_tokens > available:
            print(f"  [ReadOffloaded] ⚠ Block '{block_id}' requires ~{estimated_tokens} tokens "
                  f"but only {available} available. Attempting page-out...")
            if task_context and len(task_context) > 1000:
                freed = _page_out_context(task_context, estimated_tokens - available + 500,
                                          token_budget)
                if freed > 0:
                    print(f"  [ReadOffloaded] ✓ Paged out {freed} chars to make room")
                    token_budget.used = max(0, token_budget.used - freed // 3)
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
    """Page out sections of the active context to free space."""
    if not context_text or needed_chars <= 0:
        return 0
    sections = re.split(r'(\n#{1,3}\s)', context_text)
    if len(sections) < 3:
        return 0
    freed = 0
    offloaded_count = 0
    lines = context_text.splitlines(keepends=True)
    header_indices = [i for i, ln in enumerate(lines) if re.match(r'^#{1,3}\s', ln.strip())]
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
            print(f"  [PageOut] ⚠ Error offloading section at line {idx}: {e}")
            continue
    if offloaded_count > 0:
        print(f"  [PageOut] Paged out {offloaded_count} section(s) ({freed} chars)")
    return freed
'''


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4: READ_OFFLOADED entry added to SIGNAL_PATTERNS dict
# ══════════════════════════════════════════════════════════════════════════════

READ_OFFLOADED_SIGNAL_PATTERN_REPLACEMENT = r'''    "FETCH": r"\[FETCH:([^\]]+)#([^\]]+)\]",
    "READ_OFFLOADED": r"\[READ_OFFLOADED:([^\]]+)\]",
}'''


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5: READ_OFFLOADED signal handler (inserted into mesh loop)
# ══════════════════════════════════════════════════════════════════════════════

READ_OFFLOADED_HANDLER_BLOCK = '''
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
                        print(f"  [ReadOffloaded] ⚠ Block needs ~{estimated_cost} tokens, "
                              f"only {available} available. Paging out context...")
                        freed = _page_out_context(
                            task.context or "",
                            int((estimated_cost - available) * 3),
                            budget,
                        )
                        if freed > 0:
                            budget.used = max(0, budget.used - freed // 3)
                            print(f"  [ReadOffloaded] ✓ Made room by paging out {freed} chars")
                task.context = (task.context or "") + "\n" + offloaded_content
                task.completed = False
                work_queue.appendleft(task)
                print(f"  [ReadOffloaded] Injected offloaded block '{block_id}' into {task.agent}")

'''


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 6: Agent prompt extension for READ_OFFLOADED tool
# ══════════════════════════════════════════════════════════════════════════════

AGENT_PROMPT_EXTENSION = (
    '    "- [READ_OFFLOADED:<block_id>] — Retrieve context that was previously '
    'offloaded to disk when the token budget was exceeded. Use the '
    '<OFFLOADED_CONTEXT> preview tags as your guide to determine which '
    'block_id to request.\\n"\n'
)
