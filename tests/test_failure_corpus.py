"""
test_failure_corpus.py — unit tests for the initial/genuine corpus split.
"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import failure_corpus as fc


@pytest.fixture
def isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(fc, "_DEFAULT_CORPUS_DIR", tmp_path)
    monkeypatch.delenv("MIDWAY_FAILURE_CORPUS_PATH", raising=False)
    monkeypatch.setenv("MIDWAY_FAILURE_CORPUS", "1")
    monkeypatch.setattr(fc, "_seen_keys_by_source", {})
    return tmp_path


def test_corpus_path_routes_by_source(monkeypatch, tmp_path):
    monkeypatch.setattr(fc, "_DEFAULT_CORPUS_DIR", tmp_path)
    monkeypatch.delenv("MIDWAY_FAILURE_CORPUS_PATH", raising=False)
    assert fc.corpus_path("initial").name == "failure_corpus.initial.jsonl"
    assert fc.corpus_path("genuine").name == "failure_corpus.genuine.jsonl"
    # Unknown source falls back to genuine.
    assert fc.corpus_path("bogus").name == "failure_corpus.genuine.jsonl"


def test_record_fix_splits_sources(isolate):
    fc.record_fix("#6", "I2", "SpawnStaticBox(0,0,0,1,1,1)",
                  "MidwayPhysics.SpawnStaticBox(0,0,0,1,1,1)", "x.lua", source="initial")
    fc.record_fix("#8", "I2", "MidwayPhysics.GetBodyFromHandle(h)",
                  "-- removed", "y.lua", source="genuine")

    init = (isolate / "failure_corpus.initial.jsonl").read_text(encoding="utf-8")
    genu = (isolate / "failure_corpus.genuine.jsonl").read_text(encoding="utf-8")
    assert '"source": "initial"' in init
    assert '"source": "genuine"' in genu
    # No cross-contamination.
    assert "GetBodyFromHandle" not in init
    assert "SpawnStaticBox" not in genu


def test_record_fix_defaults_to_genuine(isolate):
    fc.record_fix("#9", "I2", "mods.heat", "MOD.heat", "z.lua")
    genu = (isolate / "failure_corpus.genuine.jsonl").read_text(encoding="utf-8")
    assert '"source": "genuine"' in genu


def test_record_fix_dedupes_within_source(isolate):
    ok1 = fc.record_fix("#6", "I2", "SpawnStaticBox(0,0,0,1,1,1)",
                        "MidwayPhysics.SpawnStaticBox(0,0,0,1,1,1)", "x.lua", source="initial")
    ok2 = fc.record_fix("#6", "I2", "SpawnStaticBox(0,0,0,1,1,1)",
                        "MidwayPhysics.SpawnStaticBox(0,0,0,1,1,1)", "x.lua", source="initial")
    assert ok1 is True
    assert ok2 is False  # duplicate within the same source is skipped


def test_looks_poisoned_detects_bad_wrap():
    assert fc.looks_poisoned("AttractionConstants.modifiers or {}.luck") is True
    assert fc.looks_poisoned("MOD or {}[1]") is True
    # The CORRECT parenthesized form is NOT poison.
    assert fc.looks_poisoned("(MOD or {}).luck") is False
    assert fc.looks_poisoned("local MOD = AttractionConstants.modifiers or {}") is False


def test_record_fix_rejects_poisoned_output(isolate):
    ok = fc.record_fix("#31", "I2",
                       "AttractionConstants.modifiers.luck",
                       "AttractionConstants.modifiers or {}.luck",
                       "x.lua", source="initial")
    assert ok is False
    # Nothing written to the corpus.
    assert not (isolate / "failure_corpus.initial.jsonl").exists()
