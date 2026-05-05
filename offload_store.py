"""
Offload store — disk-backed overflow buffer for pruned context blocks.
Stores and retrieves context blocks that have been paged out of the
active token budget to free space for new content.

No async/await — purely synchronous JSON file I/O.
"""

from __future__ import annotations
import json
import hashlib
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


class OffloadStore:
    """Disk-backed overflow buffer for pruned context blocks.

    Stores blocks as individual JSON files in a designated store directory.
    Provides store, retrieve, list, delete, and garbage collection operations.
    """

    def __init__(self, store_dir: str = None, index_ttl: int = 300):
        if store_dir is None:
            from pathlib import Path
            # Default to midway-pipeline/offload_store relative to this file
            self.store_dir = Path(__file__).parent / "offload_store"
        else:
            self.store_dir = Path(store_dir)
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.index: Dict[str, Dict[str, Any]] = {}
        self.index_ttl = index_ttl
        self._index_loaded: float = 0
        self._index_path = self.store_dir / ".index_cache.json"

    def _load_index(self):
        """Lazy-load index from disk with TTL."""
        now = time.time()
        if now - self._index_loaded < self.index_ttl:
            return
        if self._index_path.is_file():
            try:
                self.index = json.loads(
                    self._index_path.read_text(encoding="utf-8")
                )
            except (json.JSONDecodeError, OSError):
                self.index = {}
        else:
            self.index = {}
        self._index_loaded = now

    def _save_index(self):
        """Persist index to disk."""
        self._index_path.write_text(
            json.dumps(self.index, indent=2), encoding="utf-8"
        )

    def _block_path(self, block_id: str) -> Path:
        return self.store_dir / f"block_{block_id}.json"

    def store_block(self, block_id: str, header: str, body_lines: list) -> bool:
        """Store a context block to disk.

        Args:
            block_id: Unique identifier for the block.
            header: Header text for the block.
            body_lines: List of body text lines.

        Returns:
            True if stored successfully, False on failure.
        """
        try:
            self._load_index()
            full_text = "\n".join(body_lines) if isinstance(body_lines, list) else body_lines
            info = {
                "header": header,
                "char_count": len(full_text),
                "token_estimate": len(full_text) // 3,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "full_text": full_text,
            }
            path = self._block_path(block_id)
            path.write_text(json.dumps(info, indent=2), encoding="utf-8")
            self.index[block_id] = {k: v for k, v in info.items() if k != "full_text"}
            self._save_index()
            return True
        except OSError as e:
            print(f"  [OffloadStore] !! Failed to store block '{block_id}': {e}")
            return False

    def retrieve_block(self, block_id: str) -> str:
        """Retrieve a stored context block from disk.

        Args:
            block_id: The identifier of the offloaded block.

        Returns:
            Full reconstructed text (markdown formatted), or an error message.
        """
        path = self._block_path(block_id)
        if not path.is_file():
            return (
                "\n## Offloaded Context Retrieval: ERROR\n"
                f"**Block ID:** {block_id}\n"
                f"**Error:** Block not found.\n"
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
        """Delete a stored block.

        Args:
            block_id: The identifier of the block to delete.

        Returns:
            True if deleted, False on failure.
        """
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

    def list_stored_blocks(self) -> List[Dict[str, Any]]:
        """List all stored blocks with metadata.

        Returns:
            List of dicts with block_id, header_preview, char_count,
            token_estimate, and timestamp.
        """
        self._load_index()
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
        """Calculate total disk size of stored blocks in bytes."""
        total = 0
        if self.store_dir.is_dir():
            for f in self.store_dir.glob("block_*.json"):
                try:
                    total += f.stat().st_size
                except OSError:
                    pass
        return total

    def garbage_collect(self, max_mb: int = 512) -> int:
        """Remove oldest blocks until total size is under max_mb.

        Args:
            max_mb: Maximum disk usage in MB.

        Returns:
            Number of blocks evicted.
        """
        max_bytes = max_mb * 1024 * 1024
        current = self.store_size()
        if current <= max_bytes:
            return 0
        target = int(max_bytes * 0.8)
        self._load_index()
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


# Singleton instance
_OFFLOAD_STORE = OffloadStore()


def get_offload_store() -> OffloadStore:
    """Return the singleton OffloadStore instance."""
    return _OFFLOAD_STORE
