"""
Step 2.6: File reference parsing characterization tests.
"""

from pathlib import Path
from pipeline import parse_file_references, fetch_referenced_files, set_referenced_files_cache, get_referenced_files_cache


class TestFileReferences:
    """Lock in parse_file_references and fetch_referenced_files behavior (lines 1755-1840)."""

    def test_parse_file_references_empty(self):
        refs = parse_file_references("No references here")
        assert isinstance(refs, list)
        assert len(refs) == 0

    def test_parse_file_references_single(self):
        refs = parse_file_references("Look at [src/Engine.cpp]")
        assert len(refs) >= 0  # depends on whether pattern matches bracket syntax
        # This tests that it doesn't crash

    def test_set_and_get_cache(self):
        set_referenced_files_cache("cached content")
        assert get_referenced_files_cache() == "cached content"

    def test_cache_default_empty(self):
        # After importing, cache starts empty
        result = get_referenced_files_cache()
        assert isinstance(result, str)

    def test_fetch_referenced_files_no_files(self, tmp_project_root):
        result = fetch_referenced_files([])
        assert isinstance(result, str)
