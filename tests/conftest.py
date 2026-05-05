"""
Shared fixtures for characterization tests.

All tests must:
  1. Never call a real LLM (mock all HTTP)
  2. Never modify the real filesystem outside temp dirs
  3. Lock in existing behavior before refactoring
"""

import json
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure the pipeline package is importable
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def tmp_project_root(tmp_path):
    """Create a temporary project root with minimal structure."""
    docs_memory = tmp_path / "docs" / "memory"
    docs_memory.mkdir(parents=True, exist_ok=True)
    gdd_file = tmp_path / "GDD" / "Midway_to_Nowhere_Master_GDD_v19.md"
    gdd_file.parent.mkdir(parents=True, exist_ok=True)
    gdd_file.write_text("# GDD Placeholder\n\nTest content.\n")
    checkpoints = tmp_path / ".pipeline_checkpoints"
    checkpoints.mkdir(exist_ok=True)
    return tmp_path


@pytest.fixture
def mock_ollama_response():
    """Return a factory that creates a mock urllib response for call_ollama."""
    def _make(status=200, data_dict=None):
        if data_dict is None:
            data_dict = {"response": "Mocked LLM response", "done": True}
        response_data = json.dumps(data_dict).encode("utf-8")
        mock_resp = type("MockResponse", (), {
            "status": status,
            "read": lambda self, *args: response_data,
            "__enter__": lambda self: self,
            "__exit__": lambda self, *a: None,
        })()
        return mock_resp
    return _make


@pytest.fixture
def patch_root():
    """Returns a context manager to patch pipeline.PROJECT_ROOT."""
    @contextmanager
    def _patch(temp_root):
        with patch("pipeline.PROJECT_ROOT", temp_root):
            yield
    return _patch
