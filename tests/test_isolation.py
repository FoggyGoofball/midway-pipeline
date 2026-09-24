"""
test_isolation.py — unit tests for strict-isolation write redirection helpers.
"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import _helpers_io as hio


def test_get_isolation_path_maps_to_sandbox(monkeypatch, tmp_path):
    monkeypatch.setattr(hio, "PROJECT_ROOT", tmp_path)
    p = hio.get_isolation_path(tmp_path / "attractions" / "strongman" / "strongman.lua")
    assert ".sandbox" in p.parts
    assert p.name == "strongman.lua"


def test_get_isolation_path_outside_project_unchanged(monkeypatch, tmp_path):
    monkeypatch.setattr(hio, "PROJECT_ROOT", tmp_path)
    outside = Path("C:/somewhere/else/file.lua")
    assert hio.get_isolation_path(outside) == outside


def test_isolation_flag_reads_module_state(monkeypatch):
    monkeypatch.setattr(hio, "_STRICT_ISOLATION", True)
    assert hio.is_isolation_active() is True
    monkeypatch.setattr(hio, "_STRICT_ISOLATION", False)
    assert hio.is_isolation_active() is False
