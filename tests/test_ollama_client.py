"""
Step 2.7: Ollama client characterization tests (mock HTTP, never real API).
"""

import json
from unittest.mock import patch

from pipeline import call_ollama, call_ollama_streamed


class MockStreamResponse:
    """A urllib response-like object that yields NDJSON then EOF."""

    def __init__(self, ndjson_lines, status=200):
        self.status = status
        self._data = ("\n".join(ndjson_lines) + "\n").encode("utf-8")
        self._pos = 0

    def read(self, size=4096):
        if self._pos >= len(self._data):
            return b""
        chunk = self._data[self._pos:self._pos + size]
        self._pos += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def _ndjson_line(text, done=True):
    """Build a single NDJSON chat line as Ollama would emit."""
    return json.dumps({
        "message": {"content": text},
        "done": done,
    })


class TestCallOllama:
    """Lock in call_ollama behavior (lines 2098-2121). Mock only."""

    def test_call_ollama_returns_string(self):
        mock_resp = MockStreamResponse([_ndjson_line("Hello!")])
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = call_ollama("System prompt", "User message", "test")
            assert isinstance(result, str)
            assert "Hello!" in result

    def test_call_ollama_handles_urlerror(self):
        """Should return error message string on connection error."""
        with patch("urllib.request.urlopen", side_effect=Exception("Connection refused")):
            result = call_ollama("System", "User", "errortest")
            assert isinstance(result, str)
            assert "ERROR" in result

    def test_call_ollama_with_model_override(self):
        mock_resp = MockStreamResponse([_ndjson_line("Custom model response")])
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = call_ollama("System", "User", "test_model", model="qwen2.5-coder:7b")
            assert isinstance(result, str)
            assert "Custom model response" in result


class TestCallOllamaStreamed:
    """Lock in call_ollama_streamed behavior (lines 2027-2095). Mock only."""

    def test_streamed_yields_tokens(self):
        mock_resp = MockStreamResponse([_ndjson_line("Hello")])
        with patch("urllib.request.urlopen", return_value=mock_resp):
            tokens = list(call_ollama_streamed("System", "User", "test_stream"))
            assert len(tokens) > 0
            assert "Hello" in tokens[0]

    def test_streamed_handles_empty_chunks(self):
        mock_resp = MockStreamResponse([])  # No data
        with patch("urllib.request.urlopen", return_value=mock_resp):
            tokens = list(call_ollama_streamed("System", "User", "test_empty"))
            assert len(tokens) == 0
