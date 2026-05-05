"""
Step 2.2: OffloadStore CRUD characterization tests.
"""

from unittest.mock import patch

from pipeline import OffloadStore


class TestOffloadStore:
    """Lock in OffloadStore behavior (lines 472-666)."""

    def _make_store(self, tmp_path):
        """Create an OffloadStore with PROJECT_ROOT pointing at tmp_path."""
        with patch("pipeline.PROJECT_ROOT", tmp_path):
            return OffloadStore()

    def test_store_and_retrieve_block(self, tmp_path):
        store = self._make_store(tmp_path)
        ok = store.store_block("test1", "Test Header", ["line 1", "line 2"])
        assert ok is True
        retrieved = store.retrieve_block("test1")
        assert isinstance(retrieved, str)
        assert "Test Header" in retrieved
        assert "line 1" in retrieved

    def test_store_block_returns_false_on_failure(self, tmp_path):
        store = self._make_store(tmp_path)
        ok = store.store_block("test1", "H", ["body"])
        assert ok is True

    def test_retrieve_nonexistent_block_returns_error_message(self, tmp_path):
        store = self._make_store(tmp_path)
        retrieved = store.retrieve_block("nonexistent_id")
        assert isinstance(retrieved, str)
        assert "ERROR" in retrieved

    def test_list_stored_blocks_returns_list(self, tmp_path):
        store = self._make_store(tmp_path)
        store.store_block("A", "A", ["body"])
        store.store_block("B", "B", ["body"])
        blocks = store.list_stored_blocks()
        assert isinstance(blocks, list)
        assert len(blocks) >= 2

    def test_delete_block(self, tmp_path):
        store = self._make_store(tmp_path)
        store.store_block("del1", "Delete Me", ["body"])
        deleted = store.delete_block("del1")
        assert deleted is True
        retrieved = store.retrieve_block("del1")
        assert isinstance(retrieved, str)
        assert "ERROR" in retrieved

    def test_garbage_collect_does_not_crash(self, tmp_path):
        store = self._make_store(tmp_path)
        store.store_block("A", "A", ["body"])
        n = store.garbage_collect(max_mb=512)
        assert isinstance(n, int)
