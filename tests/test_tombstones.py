"""
test_tombstones.py — unit tests for the failure-signature ("tombstone") catalogue.
"""

import re
from types import SimpleNamespace

from _tombstones import (
    TOMBSTONE_GUARDS,
    push_recent_tombstones,
    render_recent_tombstones,
    tombstone_lines_from_text,
)


class TestTombstoneCatalogue:
    def test_all_guards_are_compiled_regexes(self):
        assert TOMBSTONE_GUARDS
        for _domain, _pat, _label, _why in TOMBSTONE_GUARDS:
            assert hasattr(_pat, "search"), f"{_label}: pattern not compiled"
            assert _label and _why

    def test_labels_are_unique(self):
        labels = [l for _, _, l, _ in TOMBSTONE_GUARDS]
        assert len(labels) == len(set(labels))


class TestTombstoneDetection:
    def test_detects_search_markers(self):
        lines = tombstone_lines_from_text("SEARCH/REPLACE scaffolding leaked into code\nfoo")
        assert any("SEARCH/REPLACE scaffolding" in ln for ln in lines)

    def test_detects_json_table(self):
        lines = tombstone_lines_from_text("JSON table syntax in Lua")
        assert any("JSON table syntax" in ln for ln in lines)

    def test_no_hits(self):
        assert tombstone_lines_from_text("clean code, nothing to see") == []

    def test_empty(self):
        assert tombstone_lines_from_text("") == []
        assert tombstone_lines_from_text(None) == []


class TestRecentTombstoneRingBuffer:
    def _ctx(self):
        return SimpleNamespace()

    def test_empty_render(self):
        assert render_recent_tombstones(self._ctx()) == ""

    def test_push_and_render(self):
        ctx = self._ctx()
        push_recent_tombstones(ctx, ["- ✗ A — why a", "- ✗ B — why b"])
        out = render_recent_tombstones(ctx)
        assert "A" in out and "B" in out

    def test_dedupes(self):
        ctx = self._ctx()
        push_recent_tombstones(ctx, ["- ✗ A — why"])
        push_recent_tombstones(ctx, ["- ✗ A — why"])
        assert len(ctx._recent_tombstones) == 1

    def test_bounded_to_last_n(self):
        ctx = self._ctx()
        for i in range(6):
            push_recent_tombstones(ctx, [f"- ✗ t{i} — why"])
        assert len(ctx._recent_tombstones) == 3
        # last 3 kept
        assert ctx._recent_tombstones[0].startswith("- ✗ t3")
        assert ctx._recent_tombstones[-1].startswith("- ✗ t5")
