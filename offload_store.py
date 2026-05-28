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

    def _generate_keywords(self, text: str, max_keywords: int = 8) -> list:
        """Auto-generate keyword summary array from text content.

        Extracts capitalized/emphasized terms, code-language identifiers,
        and significant tokens to produce a targeted searchable summary.
        Sub-agents use these keywords to locate offloaded blocks via
        read_offloaded_file() without blind file retrieval.

        Args:
            text: The full text content to analyze.
            max_keywords: Maximum number of keywords to generate.

        Returns:
            List of keyword strings.
        """
        import re as _re
        keywords = set()

        # Extract markdown headers (## or ### sections)
        headers = _re.findall(r'^#{2,3}\s+(.+)', text, _re.MULTILINE)
        for h in headers:
            # Take first 1-2 significant words from each header
            words = h.split()[:3]
            for w in words:
                w_clean = w.strip('*[]():;,!').lower()
                if len(w_clean) > 3:
                    keywords.add(w_clean)

        # Extract capitalized proper nouns / identifiers
        for match in _re.finditer(r'\b[A-Z][a-zA-Z]{2,}\b', text):
            keywords.add(match.group(0).lower())
            if len(keywords) >= max_keywords * 2:
                break

        # Extract code-like tokens (snake_case, camelCase, UPPER_CASE)
        for match in _re.finditer(r'\b[a-z]+_[a-z]+\b|\b[A-Z][a-z]+[A-Z]\w*\b|\b[A-Z]{2,}(?:_[A-Z]+)*\b', text):
            keywords.add(match.group(0).lower())
            if len(keywords) >= max_keywords * 2:
                break

        # Extract file extensions
        for match in _re.finditer(r'\b(\.[a-z]{2,4})\b', text):
            keywords.add(match.group(1))

        # Prioritize and truncate
        sorted_kw = sorted(keywords, key=lambda x: (-text.lower().count(x), len(x)))
        return sorted_kw[:max_keywords]

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
            content_hash = hashlib.sha256(full_text.encode("utf-8")).hexdigest()[:16]
            keywords = self._generate_keywords(full_text)
            info = {
                "header": header,
                "char_count": len(full_text),
                "token_estimate": len(full_text) // 3,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "full_text": full_text,
                "content_hash": content_hash,
                "keywords": keywords,
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

    # ── MemGPT-style session window persistence ───────────────────────────────

    def _session_path(self, session_id: str) -> "Path":
        return self.store_dir / f"session_{session_id}.json"

    def store_message_window(self, session_id: str,
                             messages: "List[Dict[str, Any]]") -> bool:
        """Persist a full Ollama messages array to disk for a session.

        The entire list is written atomically as a single JSON file.  Callers
        may checkpoint as often as needed; each call overwrites the previous
        snapshot for the same session_id.

        Args:
            session_id: Opaque string key (e.g. ``uuid4().hex[:12]``).
            messages:   List of ``{role, content}`` dicts.

        Returns:
            True on success, False on I/O error.
        """
        path = self._session_path(session_id)
        try:
            payload = {
                "session_id": session_id,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "message_count": len(messages),
                "messages": messages,
            }
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            return True
        except OSError as exc:
            print(f"  [OffloadStore] !! store_message_window failed for '{session_id}': {exc}")
            return False

    def load_message_window(self, session_id: str) -> "Optional[List[Dict[str, Any]]]":
        """Load a persisted messages array from disk.

        Args:
            session_id: The same key used in ``store_message_window``.

        Returns:
            The messages list, or ``None`` if not found / corrupt.
        """
        path = self._session_path(session_id)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data.get("messages")
        except (json.JSONDecodeError, OSError) as exc:
            print(f"  [OffloadStore] !! load_message_window failed for '{session_id}': {exc}")
            return None

    def delete_session(self, session_id: str) -> bool:
        """Remove a persisted session window from disk.

        Args:
            session_id: The session key to delete.

        Returns:
            True if removed or not present, False on error.
        """
        path = self._session_path(session_id)
        try:
            if path.is_file():
                path.unlink()
            return True
        except OSError as exc:
            print(f"  [OffloadStore] !! delete_session failed for '{session_id}': {exc}")
            return False

    def store_evicted_turn(self, session_id: str, turn_index: int,
                           role: str, content: str) -> str:
        """Persist a single evicted message turn to the offload store.

        Returns the block_id so the caller can embed a PAGE_IN hint stub.

        Args:
            session_id:  The owning session.
            turn_index:  Original position in the messages array (for ordering).
            role:        ``'user'`` or ``'assistant'``.
            content:     The original message content.

        Returns:
            The block_id string for the stored turn.
        """
        block_id = f"turn_{session_id}_{turn_index:04d}"
        header = f"Evicted turn [{turn_index}] ({role}) from session {session_id}"
        self.store_block(
            block_id=block_id,
            header=header,
            body_lines=[content],
        )
        return block_id


# Singleton instance
_OFFLOAD_STORE = OffloadStore()


def get_offload_store() -> OffloadStore:
    """Return the singleton OffloadStore instance."""
    return _OFFLOAD_STORE
