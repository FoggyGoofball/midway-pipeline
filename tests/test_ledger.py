"""
Steps 2.4, 2.13, 2.14: Memory ledger + fingerprint normalization tests.
"""

from pathlib import Path
from unittest.mock import patch

from pipeline import (
    _normalize_fix_fingerprint,
    _collect_ledger_entries,
    ledger_toc,
    ensure_ledger_header,
    _generate_module_name,
    _append_to_ledger,
)


class TestFixFingerprint:
    """Step 2.13: Lock in _normalize_fix_fingerprint behavior (lines 675-708)."""

    def test_normalize_collapses_excess_blank_lines(self):
        """3+ newlines become 2."""
        result = _normalize_fix_fingerprint("a\n\n\n\nb")
        assert "\n\n\n" not in result

    def test_normalize_strips_outer_whitespace(self):
        result = _normalize_fix_fingerprint("  hello  ")
        assert result == "hello"

    def test_normalize_cycle_number(self):
        result = _normalize_fix_fingerprint("Fix bug (Cycle 3)")
        assert "Cycle N" in result
        assert "Cycle 3" not in result

    def test_normalize_collapsed_markers(self):
        result = _normalize_fix_fingerprint("before [... content collapsed ...] after")
        assert "[... content collapsed ...]" not in result
        assert isinstance(result, str)

    def test_normalize_strips_trailing_truncation_marker(self):
        result = _normalize_fix_fingerprint("code here\n[... final truncation ...]")
        assert "[... final truncation ...]" not in result

    def test_normalize_strips_block_dropped_marker(self):
        """Regex matches 'block(s)' with literal parens."""
        result = _normalize_fix_fingerprint("text\n[... 5 block(s) dropped ...]")
        assert "[... 5 block(s) dropped" not in result

    def test_normalize_identity_for_clean_input(self):
        result = _normalize_fix_fingerprint("int x = 1;\nreturn x;")
        assert result == "int x = 1;\nreturn x;"


class TestLedgerEntries:
    """Step 2.4: Lock in _collect_ledger_entries behavior (lines 802-863)."""

    def test_collect_ledger_entries_nonexistent(self, tmp_path):
        non_existent = tmp_path / "docs" / "memory" / "C++_ledger.md"
        entries = _collect_ledger_entries(non_existent)
        assert isinstance(entries, list)
        assert len(entries) == 0

    def test_collect_ledger_entries_with_content(self, tmp_path):
        # File must be inside PROJECT_ROOT for relative_to() call
        project_root = tmp_path
        mem_dir = project_root / "docs" / "memory"
        mem_dir.mkdir(parents=True)
        ledger_file = mem_dir / "C++_ledger.md"
        ledger_file.write_text("""
# C++ Ledger

### [PhysicsSystem]
Created physics solver.

### [RenderCore]
Added render pipeline.
""")
        # _collect_ledger_entries was migrated to ledger.py; patch ledger.PROJECT_ROOT
        # so that relative_to() works within the temp directory.
        with patch("ledger.PROJECT_ROOT", project_root):
            entries = _collect_ledger_entries(ledger_file)
            assert isinstance(entries, list)
            assert len(entries) >= 2


class TestLedgerTOC:
    """Step 2.4: Lock in ledger_toc behavior (lines 865-917)."""

    def test_ledger_toc_returns_empty_when_no_memory_dir(self, tmp_path):
        with patch("pipeline.MEMORY_DIR", tmp_path / "docs" / "memory"):
            result = ledger_toc()
            assert isinstance(result, str)

    def test_ledger_toc_with_domain_and_ledger_files(self, tmp_path):
        mem_dir = tmp_path / "docs" / "memory"
        mem_dir.mkdir(parents=True)
        cpp_ledger = mem_dir / "C++_ledger.md"
        cpp_ledger.write_text("## [PhysicsSystem]\nCreated physics solver.\n")
        lua_ledger = mem_dir / "Lua_ledger.md"
        lua_ledger.write_text("## [BoothUI]\nAdded booth UI.\n")

        with (
            patch("pipeline.MEMORY_DIR", mem_dir),
            patch("pipeline.PROJECT_ROOT", tmp_path),
            patch("pipeline.ALL_DOMAINS", {"C++": {"ledger": "docs/memory/C++_ledger.md"}}),
        ):
            result = ledger_toc(domain_key="C++")
            assert isinstance(result, str)
            assert len(result) > 0


class TestLedgerHeader:
    """Step 2.14: Lock in ensure_ledger_header behavior (lines 2379-2421)."""

    def test_ensure_ledger_header_adds_missing(self):
        output = "Some agent output without a header"
        result = ensure_ledger_header(output, "Add physics", "C++")
        assert "### " in result

    def test_generate_module_name(self):
        name = _generate_module_name("Add physics system", "agent_cpp")
        assert isinstance(name, str)
        assert len(name) > 0

    def test_append_to_ledger_no_crash(self, tmp_path):
        with patch("pipeline.MEMORY_DIR", tmp_path / "docs" / "memory"):
            _append_to_ledger("### [Test]\nSome content", "C++", "test spec")
