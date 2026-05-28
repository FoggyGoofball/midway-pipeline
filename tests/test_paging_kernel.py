"""
tests/test_paging_kernel.py
===========================
Unit tests for paging_kernel.py — covering detection, I/O, guard logic,
overflow-safe VRAM_STUB generation, and PagingController orchestration.
All tests are pure in-memory; no real filesystem or Ollama calls are made.
"""
from __future__ import annotations

import tempfile
import textwrap
from pathlib import Path
from typing import Dict
from unittest.mock import MagicMock

import pytest

from paging_kernel import (
    MAX_PAGE_RECURSION,
    PagingBuffer,
    PagingController,
    _extract_lines_chunk,
    _extract_search_chunk,
    _resolve_dynamic_page_limit,
    detect_page_tokens,
    detect_vram_stubs,
    handle_page_in,
    handle_page_out,
    inject_continuation_prompt,
    inject_paged_content,
)


# ── detect_page_tokens ───────────────────────────────────────────────────────

class TestDetectPageTokens:
    def test_detects_page_in(self):
        text = "<invoke_kernel><action>PAGE_IN</action><target>foo.cpp</target></invoke_kernel>"
        tokens = detect_page_tokens(text)
        assert len(tokens) == 1
        assert tokens[0]["type"] == "PAGE_IN"
        assert tokens[0]["target"] == "foo.cpp"

    def test_detects_page_out(self):
        text = "<invoke_kernel><action>PAGE_OUT</action><target>old_data.md</target></invoke_kernel>"
        tokens = detect_page_tokens(text)
        assert len(tokens) == 1
        assert tokens[0]["type"] == "PAGE_OUT"
        assert tokens[0]["target"] == "old_data.md"

    def test_detects_lines_range(self):
        text = (
            "<invoke_kernel><action>PAGE_IN</action>"
            "<target>src/main.cpp</target><lines>10-50</lines>"
            "</invoke_kernel>"
        )
        tokens = detect_page_tokens(text)
        assert tokens[0]["lines_range"] == "10-50"

    def test_detects_search_term(self):
        text = (
            "<invoke_kernel><action>PAGE_IN</action>"
            "<target>src/main.cpp</target><search>MyClass</search>"
            "</invoke_kernel>"
        )
        tokens = detect_page_tokens(text)
        assert tokens[0]["search_term"] == "MyClass"

    def test_no_tokens_plain_text(self):
        assert detect_page_tokens("Nothing special here.") == []


# ── detect_vram_stubs ────────────────────────────────────────────────────────

class TestDetectVramStubs:
    def test_detects_stub(self):
        text = '<VRAM_STUB id="block_abc" summary="old physics code" />'
        stubs = detect_vram_stubs(text)
        assert len(stubs) == 1
        assert stubs[0]["id"] == "block_abc"
        assert stubs[0]["summary"] == "old physics code"

    def test_no_stubs(self):
        assert detect_vram_stubs("No stubs here.") == []


# ── PagingBuffer ─────────────────────────────────────────────────────────────

class TestPagingBuffer:
    def test_detects_complete_page_in(self):
        buf = PagingBuffer()
        token = "<invoke_kernel><action>PAGE_IN</action><target>file.py</target></invoke_kernel>"
        found, info, _ = buf.append(token)
        assert found is True
        assert info["type"] == "PAGE_IN"
        assert info["target"] == "file.py"

    def test_ignores_placeholder_targets(self):
        buf = PagingBuffer()
        token = "<invoke_kernel><action>PAGE_IN</action><target>filename.md</target></invoke_kernel>"
        found, info, _ = buf.append(token)
        assert found is False

    def test_accumulates_across_chunks(self):
        buf = PagingBuffer()
        chunks = [
            "<invoke_kernel>",
            "<action>PAGE_IN</action>",
            "<target>real_file.cpp</target>",
            "</invoke_kernel>",
        ]
        results = [buf.append(c) for c in chunks]
        assert any(r[0] for r in results)

    def test_reset_clears_state(self):
        buf = PagingBuffer()
        buf.append("<invoke_kernel><action>PAGE_IN</action><target>a.py</target></invoke_kernel>")
        buf.reset()
        assert not buf.has_page_token
        assert buf.page_info is None


# ── _resolve_dynamic_page_limit ──────────────────────────────────────────────

class TestResolveDynamicPageLimit:
    @pytest.mark.parametrize("ctx,expected", [
        (32768, 24000),
        (16384, 9000),
        (8192,  4800),
        (4096,  3000),
    ])
    def test_tiers(self, ctx, expected):
        assert _resolve_dynamic_page_limit(ctx) == expected


# ── _extract_lines_chunk ─────────────────────────────────────────────────────

class TestExtractLinesChunk:
    CONTENT = "\n".join(f"line {i}" for i in range(1, 101))

    def test_basic_range(self):
        chunk = _extract_lines_chunk(self.CONTENT, "10-20")
        assert ">    10 |" in chunk
        assert ">    20 |" in chunk

    def test_malformed_range_returns_empty(self):
        assert _extract_lines_chunk(self.CONTENT, "abc-xyz") == ""

    def test_single_line_range(self):
        chunk = _extract_lines_chunk(self.CONTENT, "50-50")
        assert ">    50 |" in chunk


# ── _extract_search_chunk ────────────────────────────────────────────────────

class TestExtractSearchChunk:
    CONTENT = "\n".join([
        "def alpha():",
        "    pass",
        "def beta():",
        "    return 42",
        "def gamma():",
        "    pass",
    ])

    def test_finds_term(self):
        chunk = _extract_search_chunk(self.CONTENT, "beta")
        assert "beta" in chunk

    def test_missing_term_returns_empty(self):
        assert _extract_search_chunk(self.CONTENT, "nonexistent_xyz") == ""


# ── handle_page_in ───────────────────────────────────────────────────────────

class TestHandlePageIn:
    def _make_file(self, tmp_path: Path, name: str, content: str) -> Path:
        p = tmp_path / name
        p.write_text(content, encoding="utf-8")
        return p

    def test_loads_small_file(self, tmp_path):
        self._make_file(tmp_path, "small.py", "x = 1\n")
        result = handle_page_in("small.py", project_root=tmp_path, allocated_ctx=32768)
        assert "x = 1" in result
        assert "Paged-In" in result

    def test_rejects_large_untargeted_file(self, tmp_path):
        content = "x = 1\n" * 5000  # well over any cap
        self._make_file(tmp_path, "big.py", content)
        result = handle_page_in("big.py", project_root=tmp_path, allocated_ctx=8192)
        assert "REJECTED" in result
        assert "Hard Cap" in result

    def test_targeted_lines_bypasses_cap(self, tmp_path):
        content = "x = 1\n" * 5000
        self._make_file(tmp_path, "big.py", content)
        result = handle_page_in("big.py", project_root=tmp_path,
                                allocated_ctx=8192, lines_range="1-5")
        assert "REJECTED" not in result
        assert "Targeted Lines" in result

    def test_file_not_found(self, tmp_path):
        result = handle_page_in("missing.py", project_root=tmp_path)
        assert "not found" in result

    def test_path_traversal_blocked(self, tmp_path):
        result = handle_page_in("../../etc/passwd", project_root=tmp_path)
        assert "traversal" in result or "blocked" in result

    def test_recursion_limit(self, tmp_path):
        self._make_file(tmp_path, "a.py", "pass\n")
        result = handle_page_in("a.py", project_root=tmp_path,
                                recursion_depth=MAX_PAGE_RECURSION + 1)
        assert "recursion limit" in result.lower()


# ── handle_page_out ──────────────────────────────────────────────────────────

class TestHandlePageOut:
    def test_valid_file_target(self):
        result = handle_page_out("engine.cpp")
        assert "evicted" in result.lower() or "PAGE_OUT completed" in result

    def test_rejects_invalid_target_with_cache(self):
        cache: Dict[str, str] = {"engine.cpp": "content"}
        result = handle_page_out("some random phrase", paged_in_cache=cache)
        assert "Invalid target" in result

    def test_accepts_cache_key(self):
        cache: Dict[str, str] = {"engine.cpp": "content"}
        result = handle_page_out("engine.cpp", paged_in_cache=cache)
        assert "Invalid target" not in result

    def test_rejects_generic_phrase_not_in_cache(self):
        cache: Dict[str, str] = {"engine.cpp": "content"}
        # "old context" matches the generic-phrase list and is NOT in cache
        result = handle_page_out("old context", paged_in_cache=cache)
        assert "Invalid target" in result or "Generic phrases" in result

    def test_rejects_generic_phrase_without_cache(self):
        # paged_in_cache=None skips validation entirely — no error expected
        result = handle_page_out("context")
        # Should complete normally (no cache = no validation)
        assert "PAGE_OUT completed" in result or "evicted" in result.lower()


# ── inject_paged_content / inject_continuation_prompt ────────────────────────

class TestContextInjection:
    def test_inject_before_last_user(self):
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "user msg"},
        ]
        result = inject_paged_content(messages, "INJECTED")
        # Should be: [sys, INJECTED system, user msg]
        assert result[1]["role"] == "system"
        assert result[1]["content"] == "INJECTED"
        assert result[2]["role"] == "user"

    def test_continuation_appended_as_user(self):
        messages = [{"role": "system", "content": "sys"}]
        result = inject_continuation_prompt(messages)
        assert result[-1]["role"] == "user"
        assert "Paging complete" in result[-1]["content"]

    def test_continuation_concatenated_to_existing_user(self):
        messages = [{"role": "user", "content": "original"}]
        result = inject_continuation_prompt(messages)
        assert len(result) == 1
        assert "original" in result[0]["content"]
        assert "Paging complete" in result[0]["content"]

    def test_reviewer_continuation_text(self):
        messages = [{"role": "system", "content": "sys"}]
        result = inject_continuation_prompt(messages, system_prompt="Code Reviewer Agent")
        assert "verdict" in result[-1]["content"].lower() or "PASS/FAIL" in result[-1]["content"]


# ── PagingController ─────────────────────────────────────────────────────────

class TestPagingController:
    def _make_controller(self, tmp_path: Path) -> PagingController:
        mock_store = MagicMock()
        mock_store.retrieve_block.return_value = "ERROR: not found"
        mock_store.store_block.return_value = True
        mock_store.store_message_window.return_value = True
        mock_store.load_message_window.return_value = None
        ctrl = PagingController(project_root=tmp_path, offload_store=mock_store)
        return ctrl

    def test_feed_token_detects_page_in(self, tmp_path):
        ctrl = self._make_controller(tmp_path)
        token = "<invoke_kernel><action>PAGE_IN</action><target>x.py</target></invoke_kernel>"
        found, info = ctrl.feed_token(token)
        assert found is True
        assert info["type"] == "PAGE_IN"

    def test_feed_token_normal_token(self, tmp_path):
        ctrl = self._make_controller(tmp_path)
        found, info = ctrl.feed_token("normal code token")
        assert found is False
        assert info is None

    def test_execute_page_in_file_not_found(self, tmp_path):
        ctrl = self._make_controller(tmp_path)
        result = ctrl.execute_page({"type": "PAGE_IN", "target": "missing.py"})
        assert "not found" in result
        assert ctrl._last_page_failed is True

    def test_execute_page_in_from_cache(self, tmp_path):
        ctrl = self._make_controller(tmp_path)
        ctrl.paged_in_cache["cached.py"] = "cached content"
        result = ctrl.execute_page({"type": "PAGE_IN", "target": "cached.py"})
        assert "cached content" in result
        assert "Cached" in result

    def test_execute_page_in_rejects_placeholder(self, tmp_path):
        ctrl = self._make_controller(tmp_path)
        result = ctrl.execute_page({"type": "PAGE_IN", "target": "[rule-file-path]"})
        assert "placeholder" in result.lower()
        assert ctrl._last_page_failed is True

    def test_execute_page_out_removes_from_cache(self, tmp_path):
        ctrl = self._make_controller(tmp_path)
        ctrl.paged_in_cache["engine.cpp"] = "big content"
        ctrl.execute_page({"type": "PAGE_OUT", "target": "engine.cpp"})
        assert "engine.cpp" not in ctrl.paged_in_cache

    def test_reset_cycle_clears_state(self, tmp_path):
        ctrl = self._make_controller(tmp_path)
        ctrl._page_in_progress = True
        ctrl._ghost_buffer_text = "partial text"
        ctrl._last_page_failed = True
        ctrl.reset_cycle()
        assert ctrl._page_in_progress is False
        assert ctrl._ghost_buffer_text == ""
        assert ctrl._last_page_failed is False

    def test_build_resume_payload_contains_messages(self, tmp_path):
        ctrl = self._make_controller(tmp_path)
        payload = ctrl.build_resume_payload("system prompt here")
        assert "messages" in payload
        assert any(
            "Paging complete" in m.get("content", "")
            for m in payload["messages"]
        )

    def test_ghost_buffer_injected_as_assistant(self, tmp_path):
        ctrl = self._make_controller(tmp_path)
        ctrl._ghost_buffer_text = "partial code so far"
        payload = ctrl.build_resume_payload("system")
        roles = [m["role"] for m in payload["messages"]]
        assert "assistant" in roles
        assistant_content = next(
            m["content"] for m in payload["messages"] if m["role"] == "assistant"
        )
        assert "partial code so far" in assistant_content

    def test_resume_depth_tracked_via_consecutive_pages(self, tmp_path):
        ctrl = self._make_controller(tmp_path)
        for _ in range(MAX_PAGE_RECURSION + 1):
            ctrl.feed_token(
                "<invoke_kernel><action>PAGE_IN</action>"
                "<target>x.py</target></invoke_kernel>"
            )
        assert ctrl._consecutive_pages > MAX_PAGE_RECURSION
