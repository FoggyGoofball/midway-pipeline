"""
Step 2.9: Checkpoint save/load round-trip characterization tests.
"""

from unittest.mock import patch

from pipeline import save_checkpoint, load_checkpoint, list_checkpoints


class TestCheckpoint:
    """Lock in checkpoint system behavior (lines 2473-2509)."""

    def test_save_and_load_round_trip(self, tmp_path):
        ckpt_dir = tmp_path / ".pipeline_checkpoints"
        with patch("pipeline.CHECKPOINT_DIR", ckpt_dir):
            data = {"phase": "test", "results": {"key": "value"}, "counter": 42}
            save_checkpoint("test_cp_1", "TESTING", data)
            loaded = load_checkpoint("test_cp_1")
            assert loaded is not None
            assert loaded["phase"] == "TESTING"
            assert loaded["data"]["results"]["key"] == "value"
            assert loaded["data"]["counter"] == 42

    def test_load_nonexistent(self, tmp_path):
        ckpt_dir = tmp_path / ".pipeline_checkpoints"
        with patch("pipeline.CHECKPOINT_DIR", ckpt_dir):
            result = load_checkpoint("nonexistent_checkpoint_id")
            assert result is None

    def test_list_checkpoints(self, tmp_path):
        ckpt_dir = tmp_path / ".pipeline_checkpoints"
        with patch("pipeline.CHECKPOINT_DIR", ckpt_dir):
            save_checkpoint("cp_a", "A", {"d": 1})
            save_checkpoint("cp_b", "B", {"d": 2})
            cp_list = list_checkpoints()
            assert len(cp_list) >= 2
            ids = [c["checkpoint_id"] for c in cp_list]
            assert "cp_a" in ids
            assert "cp_b" in ids

    def test_overwrite_checkpoint(self, tmp_path):
        ckpt_dir = tmp_path / ".pipeline_checkpoints"
        with patch("pipeline.CHECKPOINT_DIR", ckpt_dir):
            save_checkpoint("overwrite_test", "FIRST", {"val": 1})
            save_checkpoint("overwrite_test", "SECOND", {"val": 2})
            loaded = load_checkpoint("overwrite_test")
            assert loaded["phase"] == "SECOND"
            assert loaded["data"]["val"] == 2
