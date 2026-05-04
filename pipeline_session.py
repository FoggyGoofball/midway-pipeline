#!/usr/bin/env python3
"""
Midway Pipeline — Chat Session Segmentation
============================================
Provides a SessionManager that auto-generates session IDs, stores session
metadata (model, checkpoint state, phase) alongside the session timeline,
and integrates with both pipeline.py and pipeline_stream_server.py.

Session ID Format: pipeline_YYYYMMDD_HHMMSS_<short_hash>

Usage (standalone):
    from pipeline_session import SessionManager
    mgr = SessionManager()
    session_id = mgr.get_or_create_session(user_prompt="add plinko jackpot")

Integration points:
    - pipeline.py:         accept optional session_id, auto-generate if None
    - pipeline_stream_server.py:  auto-detect session from request metadata
"""

import json
import hashlib
import re
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.resolve()
SESSION_DIR = PROJECT_ROOT / "docs" / "memory"
SESSION_INDEX_PATH = SESSION_DIR / "session_index.json"


class SessionManager:
    """Manages pipeline session segmentation with auto-ID and metadata.

    Session IDs are deterministic from prompt content + timestamp (truncated
    to second granularity) so re-runs of the same prompt within the same
    second produce distinct IDs via hash extension.

    Metadata fields:
        - session_id:      unique session identifier
        - start_time:      ISO-8601 timestamp
        - user_prompt:     first 200 chars of user input
        - model:           Ollama model used (e.g., "qwen2.5-coder:7b")
        - phase:           current pipeline phase or "COMPLETED"
        - checkpoint_id:   associated checkpoint (if any)
        - token_estimate:  rough token count from initial prompt
        - status:          "active" | "completed" | "failed" | "checkpointed"
        - message_count:   number of turn exchanges in this session
    """

    # ── Default field values for new sessions ────────────────────────────
    DEFAULT_METADATA = {
        "start_time": None,
        "user_prompt": "",
        "model": "",
        "phase": "INIT",
        "checkpoint_id": None,
        "token_estimate": 0,
        "status": "active",
        "message_count": 0,
    }

    def __init__(self, session_id: str = None, user_prompt: str = ""):
        self._index = self._load_index()
        self._dirty = False

        if session_id and session_id in self._index:
            # Resume an existing session
            self.session_id = session_id
            self.metadata = self._index[session_id]
            self._ensure_metadata_defaults()
        else:
            # Create a new session
            self.session_id = session_id or self._generate_id(user_prompt)
            self.metadata = dict(self.DEFAULT_METADATA)
            self.metadata["session_id"] = self.session_id
            self.metadata["start_time"] = datetime.now().isoformat()
            self.metadata["user_prompt"] = (user_prompt or "")[:200]
            self._index[self.session_id] = self.metadata
            self._dirty = True
            self._flush()

    # ── Public API ──────────────────────────────────────────────────────

    def update_phase(self, phase: str) -> None:
        """Update the current pipeline phase."""
        self.metadata["phase"] = phase
        self._dirty = True
        self._flush()

    def update_checkpoint(self, checkpoint_id: str) -> None:
        """Associate a checkpoint with this session."""
        self.metadata["checkpoint_id"] = checkpoint_id
        self.metadata["status"] = "checkpointed"
        self._dirty = True
        self._flush()

    def update_status(self, status: str) -> None:
        """Set session status: active|completed|failed|checkpointed."""
        self.metadata["status"] = status
        self._dirty = True
        self._flush()

    def increment_message_count(self) -> None:
        """Increment turn counter for multi-turn diagnostic sessions."""
        self.metadata["message_count"] = self.metadata.get("message_count", 0) + 1
        self._dirty = True
        self._flush()

    def set_model(self, model: str) -> None:
        """Record the Ollama model used."""
        self.metadata["model"] = model
        self._dirty = True
        self._flush()

    def get_metadata_json(self) -> str:
        """Return metadata as a compact JSON string (for SSE events)."""
        return json.dumps(self.metadata, default=str, ensure_ascii=False)

    def list_recent_sessions(self, limit: int = 10) -> list:
        """Return the most recent N session metadata entries."""
        sorted_sessions = sorted(
            self._index.values(),
            key=lambda m: m.get("start_time", ""),
            reverse=True,
        )
        return sorted_sessions[:limit]

    def mark_completed(self) -> None:
        """Mark the session as completed and recalculate token estimate."""
        self.update_status("completed")
        self.update_phase("COMPLETED")
        self._dirty = True
        self._flush()

    def mark_failed(self) -> None:
        """Mark the session as failed."""
        self.update_status("failed")
        self._dirty = True
        self._flush()

    # ── Private Helpers ─────────────────────────────────────────────────

    def _load_index(self) -> dict:
        """Load the session index JSON file."""
        if SESSION_INDEX_PATH.is_file():
            try:
                return json.loads(SESSION_INDEX_PATH.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _flush(self) -> None:
        """Persist the index to disk."""
        if not self._dirty:
            return
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
        try:
            SESSION_INDEX_PATH.write_text(
                json.dumps(self._index, indent=2, default=str),
                encoding="utf-8",
            )
        except OSError:
            pass  # Non-critical — index is advisory

    def _ensure_metadata_defaults(self) -> None:
        """Fill in any missing default fields for resumed sessions."""
        for key, default_val in self.DEFAULT_METADATA.items():
            if key not in self.metadata:
                self.metadata[key] = default_val
                self._dirty = True
        if self._dirty:
            self._flush()

    @staticmethod
    def _generate_id(user_prompt: str = "") -> str:
        """Generate a unique session ID.

        Uses timestamp + short hash of prompt to create deterministic but
        unique IDs that are human-readable.
        """
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        hash_input = (user_prompt + datetime.now().isoformat()).encode("utf-8")
        short_hash = hashlib.sha256(hash_input).hexdigest()[:8]
        return f"pipeline_{ts}_{short_hash}"

    @classmethod
    def list_active_sessions(cls, limit: int = 10) -> list:
        """Return metadata for all active (incomplete) sessions.

        Active sessions are those with status != "completed" and
        status != "failed". Entries are sorted by start_time descending.
        """
        index = cls._load_index_static()
        active = [
            meta for meta in index.values()
            if meta.get("status") not in ("completed", "failed")
        ]
        sorted_active = sorted(
            active,
            key=lambda m: m.get("start_time", ""),
            reverse=True,
        )
        for meta in sorted_active:
            meta["latest_activity"] = meta.get("start_time", "")
        return sorted_active[:limit]

    @staticmethod
    def _load_index_static() -> dict:
        """Load the session index without requiring an instance."""
        if SESSION_INDEX_PATH.is_file():
            try:
                return json.loads(SESSION_INDEX_PATH.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    @staticmethod
    def extract_session_id_from_request(req_data: dict) -> str | None:
        """Extract an existing session_id from a chat request body.

        Checks all standard locations:
        - req_data.get("session_id")
        - messages[0].get("session_id")
        - custom metadata field "midway_session_id" in the last user message
        """
        sid = req_data.get("session_id")
        if sid:
            return sid

        messages = req_data.get("messages", [])
        for msg in messages:
            candidate = msg.get("session_id") or msg.get("midway_session_id")
            if candidate:
                return str(candidate)

        return None


# ── Standalone Helper ─────────────────────────────────────────────────────

def get_or_create_session(
    user_prompt: str = "",
    session_id: str = None,
) -> SessionManager:
    """Convenience function: return existing or new SessionManager.

    Used by pipeline_stream_server.py to auto-detect session continuity.
    """
    return SessionManager(session_id=session_id, user_prompt=user_prompt)


if __name__ == "__main__":
    # Quick self-test
    import sys

    print("=== SessionManager Self-Test ===")

    # Test 1: Create a new session
    mgr1 = SessionManager(user_prompt="add plinko jackpot")
    print(f"Created session: {mgr1.session_id}")
    print(f"Metadata: {mgr1.get_metadata_json()}")
    assert mgr1.metadata["status"] == "active"
    assert mgr1.metadata["phase"] == "INIT"
    print("  ✓ New session created with defaults")

    # Test 2: Update phase and checkpoint
    mgr1.update_phase("EXECUTION")
    mgr1.update_checkpoint("ckpt_001")
    assert mgr1.metadata["phase"] == "EXECUTION"
    assert mgr1.metadata["checkpoint_id"] == "ckpt_001"
    assert mgr1.metadata["status"] == "checkpointed"
    print("  ✓ Phase & checkpoint updated")

    # Test 3: Mark completed
    mgr1.increment_message_count()
    mgr1.mark_completed()
    assert mgr1.metadata["status"] == "completed"
    assert mgr1.metadata["message_count"] == 1
    print("  ✓ Completed status set")

    # Test 4: Resume existing session
    mgr2 = SessionManager(session_id=mgr1.session_id)
    assert mgr2.metadata["status"] == "completed"
    print("  ✓ Session resumed from index")

    # Test 5: List recent sessions
    recent = mgr1.list_recent_sessions(limit=5)
    assert len(recent) >= 1
    print(f"  ✓ {len(recent)} recent session(s) found")

    # Test 6: Extract session_id from request
    fake_request = {
        "session_id": "pipeline_20260503_123456_abcdef12",
        "messages": [{"role": "user", "content": "hello"}],
    }
    extracted = SessionManager.extract_session_id_from_request(fake_request)
    assert extracted == "pipeline_20260503_123456_abcdef12"
    print("  ✓ session_id extraction from request works")

    # Test 7: Extract from custom field
    fake_request2 = {
        "messages": [
            {"role": "user", "content": "hello", "midway_session_id": "custom_sid_001"}
        ]
    }
    extracted2 = SessionManager.extract_session_id_from_request(fake_request2)
    assert extracted2 == "custom_sid_001"
    print("  ✓ custom midway_session_id extraction works")

    print("\n=== All tests passed ===")
    sys.exit(0)
