"""
Step 2.11: TagSuggester characterization tests.
"""

from pathlib import Path
from unittest.mock import patch

from pipeline import TagSuggester


class TestTagSuggester:
    """Lock in TagSuggester behavior (lines 4289-4402)."""

    def test_analyze_returns_list_when_file_missing(self, tmp_path):
        """analyze expects a pathlib.Path; returns [] if file doesn't exist."""
        suggester = TagSuggester()
        missing_path = tmp_path / "nonexistent.md"
        result = suggester.analyze(missing_path, "test_run_001")
        assert isinstance(result, list)
        assert len(result) == 0

    def test_analyze_returns_list_when_file_exists(self, tmp_path):
        """analyze parses timeline content and returns tag suggestions."""
        timeline = tmp_path / "session_timeline.md"
        timeline.write_text(
            "# Pipeline Session Timeline\n\n"
            "## Run: run_001\n"
            "**Area:** C++ Physics\n"
            "**Verdict:** PASS\n"
        )
        suggester = TagSuggester()
        result = suggester.analyze(timeline, "test_run_001")
        assert isinstance(result, list)

    def test_suggest_stable_returns_string(self, tmp_path):
        """suggest_stable returns a formatted string, not list."""
        mem_dir = tmp_path / "docs" / "memory"
        mem_dir.mkdir(parents=True)
        with (
            patch("pipeline.MEMORY_DIR", mem_dir),
            patch("pipeline.PROJECT_ROOT", tmp_path),
        ):
            suggester = TagSuggester()
            result = suggester.suggest_stable("C++", ["run_1", "run_2"])
            assert isinstance(result, str)
            assert "Stable Core Concept" in result

    def test_suggest_regression_returns_string(self, tmp_path):
        """suggest_regression returns a formatted string, not list."""
        mem_dir = tmp_path / "docs" / "memory"
        mem_dir.mkdir(parents=True)
        with (
            patch("pipeline.MEMORY_DIR", mem_dir),
            patch("pipeline.PROJECT_ROOT", tmp_path),
        ):
            suggester = TagSuggester()
            result = suggester.suggest_regression("C++", ["run_1", "run_2"])
            assert isinstance(result, str)
            assert "Regression" in result
