#!/usr/bin/env python3
"""Virtual Context Management patcher for pipeline.py (ASCII-safe version)."""

import re, json, shutil, hashlib
from pathlib import Path
from datetime import datetime

P = Path("pipeline.py")
BACKUP = Path("pipeline.py.virtual_context_backup3")

def main():
    # Backup
    shutil.copy2(str(P), str(BACKUP))
    print(f"Backup: {BACKUP}")
    
    src = P.read_text(encoding="utf-8")
    mods = 0
    
    # ============================================================
    # PATCH 1: OffloadStore class (before LRU Doc Cache)
    # ============================================================
    # Find the actual anchor text (uses Unicode box-drawing chars)
    anchor_search = "LRU Doc Cache"
    if anchor_search in src:
        idx = src.index(anchor_search)
        # Find the beginning of that line
        while idx > 0 and src[idx-1] != '\n':
            idx -= 1
    else:
        print("ERROR: Cannot find LRU Doc Cache anchor")
        return False
    
    offload_code = r'''
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
'''
    src = src[:idx] + offload_code + "\n\n" + src[idx:]
    mods += 1
    print("Patch 1 OK: OffloadStore class inserted")
    
    # ============================================================
    # PATCH 2: Replace _block_aware_collapse body
    # ============================================================
    # Replace the old truncation notice in _block_aware_collapse
    # Find: "[... X block(s) dropped" pattern
    old_drop = "[... {dropped_count} block(s) dropped"
    if old_drop in src:
        # Replace the destructive pruning section with offloading + placeholder
        # We replace the entire break block (lines ~293-309 originally)
        old_section = (
            '                    if collapsed_blocks:\n'
            '                        collapsed_blocks.insert(0, {\n'
            '                            "header": None,\n'
            '                            "body_lines": [f"\\n[... {dropped_count} block(s) dropped'
        )
        if old_section in src:
            # More precise: find and replace the specific block
            new_section = (
                '                    if collapsed_blocks:\n'
                '                        collapsed_blocks.insert(0, {\n'
                '                            "header": None,\n'
                '                            "body_lines": [self._build_offload_placeholder(\n'
                '                                dropped_count,\n'
                '                                blocks[:blocks.index(blk)]\n'
                '                            )],\n'
                '                            "_is_preamble": True,\n'
                '                            "is_fence_block": False,\n'
                '                        })\n'
                '                # Offload all dropped blocks\n'
                '                for older_blk in blocks[:blocks.index(blk)]:\n'
                '                    if not older_blk.get("_is_preamble"):\n'
                '                        self._offload_single_block(older_blk)\n'
                '                self._offload_single_block(blk)'
            )
            src = src.replace(old_section, new_section)
            mods += 1
            print("Patch 2 OK: Replaced destructive pruning with offloading")
        else:
            # More flexible search
            # Find the block that has "dropped -- oldest context pruned"
            for pattern in [
                'dropped',
                'oldest context pruned',
                'Block(s) dropped'
            ]:
                if pattern in src:
                    # Find the lines around it
                    lines = src.split('\n')
                    for i, ln in enumerate(lines):
                        if 'dropped' in ln.lower() and 'pruned' in ln.lower():
                            lines[i] = ln.replace(
                                'oldest context pruned',
                                'offloaded to disk -- use [READ_OFFLOADED] to retrieve'
                            )
                    # Also find the body_lines insert and add offload calls
                    src = '\n'.join(lines)
                    mods += 1
                    print("Patch 2 OK: Updated pruning messages (line-based)")
                    break
            else:
                print("Patch 2 WARN: Could not find pruning message")
    else:
        print("Patch 2 WARN: drop pattern not found, may already be patched")
    
    # ============================================================
    # PATCH 3: Add _offload_single_block and _build_offload_placeholder methods
    # ============================================================
    # Find the end of _block_aware_collapse and add new methods before 'def add('
    def_add = '    def add(self, text: str, label: str = "") -> str:'
    if def_add in src:
        idx = src.index(def_add)
        new_methods = r'''
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

'''
        src = src[:idx] + new_methods + src[idx:]
        mods += 1
        print("Patch 3 OK: Added _offload_single_block and _build_offload_placeholder")
    else:
        print("Patch 3 FAIL: Could not find 'def add('")
    
    # ============================================================
    # PATCH 4: Add read_offloaded_file function and handlers
    # ============================================================
    # Insert after handle_fetch_signal
    fetch_def = "def handle_fetch_signal(fetch_tag: str) -> str:"
    if fetch_def in src:
        # Find the end of this function (next def or section comment)
        idx = src.index(fetch_def)
        # Find next '\ndef ' or section
        rest = src[idx + len(fetch_def):]
        next_def = rest.find("\ndef ")
        next_section = rest.find("\n# ---")
        if next_def > 0 and (next_section < 0 or next_def < next_section):
            end = idx + len(fetch_def) + next_def
        elif next_section > 0:
            end = idx + len(fetch_def) + next_section
        else:
            end = len(src)
        
        read_funcs = r'''

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

'''
        src = src[:end] + read_funcs + src[end:]
        mods += 1
        print("Patch 4 OK: Added read_offloaded_file and paging functions")
    else:
        print("Patch 4 FAIL: Could not find handle_fetch_signal")
    
    # ============================================================
    # PATCH 5: Add READ_OFFLOADED to SIGNAL_PATTERNS
    # ============================================================
    if "READ_OFFLOADED" not in src:
        # Find the closing of SIGNAL_PATTERNS
        sp_start = src.find("SIGNAL_PATTERNS = {")
        if sp_start > 0:
            sp_end = src.find("\n}", sp_start)
            if sp_end > sp_start:
                # Check if FETCH entry exists
                fetch_entry = '    "FETCH": r"\\[FETCH:([^\\]]+)#([^\\]]+)\\"],'
                if fetch_entry in src[sp_start:sp_end]:
                    new_entry = (
                        '    "READ_OFFLOADED": r"\\[READ_OFFLOADED:([^\\]]+)\\"],\n}')
                    src = src[:sp_end] + ',\n' + new_entry + src[sp_end+2:]
                    mods += 1
                    print("Patch 5 OK: Added READ_OFFLOADED to SIGNAL_PATTERNS")
                else:
                    print("Patch 5 WARN: FETCH entry not found in SIGNAL_PATTERNS")
            else:
                print("Patch 5 FAIL: Could not parse SIGNAL_PATTERNS")
        else:
            print("Patch 5 FAIL: SIGNAL_PATTERNS not found")
    else:
        print("Patch 5 SKIP: READ_OFFLOADED already in SIGNAL_PATTERNS")
    
    # ============================================================
    # PATCH 6: Add READ_OFFLOADED handler in mesh loop
    # ============================================================
    handler_code = '''
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
                task.context = (task.context or "") + "\\n" + offloaded_content
                task.completed = False
                work_queue.appendleft(task)
                print(f"  [ReadOffloaded] Injected block '{block_id}' into {task.agent}")
'''
    
    # Find the FETCH handler section
    fetch_handler = '                print(f"  [FETCH] Routed to DOC oracle (depth {fetch_depth+1}/3): {fetch_target[:80]}...")'
    if fetch_handler in src and "elif stype == \"READ_OFFLOADED\":" not in src:
        idx = src.index(fetch_handler) + len(fetch_handler)
        src = src[:idx] + handler_code + src[idx:]
        mods += 1
        print("Patch 6 OK: Added READ_OFFLOADED handler in mesh loop")
    elif "elif stype == \"READ_OFFLOADED\":" in src:
        print("Patch 6 SKIP: Handler already exists")
    else:
        # Try alternate anchor
        alt = "                continue\n\n        # Check double-check for unresolved items"
        if alt in src and "elif stype == \"READ_OFFLOADED\":" not in src:
            idx = src.index(alt)
            src = src[:idx] + handler_code + src[idx:]
            mods += 1
            print("Patch 6 OK: Added READ_OFFLOADED handler (alt anchor)")
        else:
            print("Patch 6 FAIL: Could not find insertion point for handler")
    
    # ============================================================
    # PATCH 7: Extend MESH_AGENT_SYSTEM_EXTENSION with READ_OFFLOADED tool
    # ============================================================
    fetch_line = '"- [FETCH:<filepath>#<HeaderName>] -- Recall context from your disk-based memory ledger.'
    if fetch_line in src and "[READ_OFFLOADED:<block_id>]" not in src:
        ext = '\n    "- [READ_OFFLOADED:<block_id>] -- Retrieve offloaded context blocks from disk. '
        ext += 'Use the <OFFLOADED_CONTEXT> preview tags to find block IDs.\\n"'
        idx = src.index(fetch_line) + len(fetch_line)
        src = src[:idx] + ext + src[idx:]
        mods += 1
        print("Patch 7 OK: Extended agent prompt with READ_OFFLOADED tool")
    elif "[READ_OFFLOADED:<block_id>]" in src:
        print("Patch 7 SKIP: Already in agent prompt")
    else:
        print("Patch 7 FAIL: Could not find FETCH line in agent prompt")
    
    # Write result
    P.write_text(src, encoding="utf-8")
    print(f"\nApplied {mods}/7 patches to pipeline.py")
    
    # Create offload_store directory
    store_dir = Path("offload_store")
    store_dir.mkdir(parents=True, exist_ok=True)
    (store_dir / ".gitkeep").write_text("")
    print("Created offload_store/ directory")
    
    # Add to .gitignore
    gitignore = Path(".gitignore")
    if gitignore.is_file():
        gi = gitignore.read_text(encoding="utf-8")
        for pat in ["offload_store/", "offload_store/*.json"]:
            if pat not in gi:
                gi += f"\n{pat}"
        gitignore.write_text(gi, encoding="utf-8")
        print("Updated .gitignore")
    
    return True

if __name__ == "__main__":
    success = main()
    if success:
        print("SUCCESS: Virtual Context Management system installed.")
        print(f"Backup: {BACKUP}")
    else:
        print("FAILED: See errors above.")
