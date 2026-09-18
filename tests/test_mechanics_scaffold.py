"""
test_mechanics_scaffold.py — Unit tests for the Mechanics Scaffold stage.

Covers the pure/deterministic pieces (parsing, validation, task-number
extraction) only — no LLM calls are made.
"""

import os

import pytest

from mechanics_scaffold import (
    _anchor_task_number,
    _count_call_args,
    _first_call_arg,
    _parse_scaffold,
    _validate_scaffold,
    _env_enabled,
)


# ---------------------------------------------------------------------------
# Task-number extraction
# ---------------------------------------------------------------------------

class TestAnchorTaskNumber:
    def test_canonical_marker(self):
        marker = "-- [TASK_7_INSERT_HOOK] -- modifier read"
        assert _anchor_task_number(marker) == "7"

    def test_extra_marker(self):
        marker = "-- [TASK_13_INSERT_HOOK] -- bonus task"
        assert _anchor_task_number(marker) == "13"

    def test_no_marker(self):
        assert _anchor_task_number("") is None
        assert _anchor_task_number("some plain text") is None


# ---------------------------------------------------------------------------
# Call argument counting
# ---------------------------------------------------------------------------

class TestCountCallArgs:
    def test_no_args(self):
        assert _count_call_args("Engine.GetStreak()") == 0

    def test_simple(self):
        assert _count_call_args("MoveKinematic(handle, lx, ly, lz, dt)") == 5

    def test_nested_table(self):
        # nested { ... } with commas must not inflate the count
        line = "CreatePool(\"puck_pool\", 2, 2, { shape = \"sphere\", radius = 0.3 })"
        assert _count_call_args(line) == 4


# ---------------------------------------------------------------------------
# First-argument extraction
# ---------------------------------------------------------------------------

class TestFirstCallArg:
    def test_handle(self):
        assert _first_call_arg("SpawnStaticBox(bell_handle, 0, 0, 0, 1, 1)") == "bell_handle"

    def test_number(self):
        assert _first_call_arg("SpawnStaticSphere(0, 1, 2, 0.5)") == ""

    def test_pool_name_string(self):
        assert _first_call_arg('CreatePool("puck_pool", 2, 2, {})') == ""


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

class TestParseScaffold:
    def test_well_formed(self):
        text = (
            "### Mechanic: Bell's Curse\n"
            "INTENT: between swings the bell shifts vertically by a luck-scaled offset\n"
            "PSEUDO:\n"
            "  if swing_complete then\n"
            "    offset = luck * SHIFT_MAX\n"
            "    bell.y += offset\n"
            "  end\n"
            "API:\n"
            "  MoveKinematic(bell_handle, lx, ly, lz, dt)\n"
        )
        s = _parse_scaffold(text, "8")
        assert s is not None
        assert s.name == "Bell's Curse"
        assert "swing_complete" in s.pseudo
        assert s.api_calls == ["MoveKinematic(bell_handle, lx, ly, lz, dt)"]
        assert s.task_id == "8"

    def test_pseudo_capped_at_12(self):
        text = (
            "### Mechanic: X\n"
            "INTENT: do a thing\n"
            "PSEUDO:\n" + "".join(f"  step{i}\n" for i in range(20)) +
            "API:\n  Engine.AwardTickets(1, 'WIN')\n"
        )
        s = _parse_scaffold(text, "8")
        assert s is not None
        assert len(s.pseudo.splitlines()) == 12

    def test_prose_only_rejected(self):
        s = _parse_scaffold("Here is a long rambling explanation with no structure.", "8")
        assert s is None

    def test_empty_rejected(self):
        assert _parse_scaffold("", "8") is None
        assert _parse_scaffold("   \n", "8") is None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _make_contract(physics=(), econ=()):
    """Build a real LuaContract from a minimal bridge contract dict."""
    from contract_validator import build_lua_contract
    bc = {}
    if physics:
        bc["midwayphysics_spawn_api"] = {f"{n}(...)": "" for n in physics}
    if econ:
        bc["economy_api"] = {f"{n}(...)": "" for n in econ}
    return build_lua_contract(bc)


class TestValidateScaffold:
    def test_valid_scaffold(self):
        contract = _make_contract(physics=["MoveKinematic"], econ=["AwardTickets"])
        s = _parse_scaffold(
            "### Mechanic: M\nINTENT: move it\nPSEUDO:\n  move\nAPI:\n  MoveKinematic(bell_handle, lx, ly, lz, dt)\n",
            "8",
        )
        assert _validate_scaffold(s, contract, set()) == []

    def test_phantom_api(self):
        contract = _make_contract(physics=["MoveKinematic"])
        s = _parse_scaffold(
            "### Mechanic: M\nINTENT: x\nPSEUDO:\n  x\nAPI:\n  MidwayPhysics.SetDensity(h, 3)\n",
            "8",
        )
        violations = _validate_scaffold(s, contract, set())
        assert any("not an approved bridge API" in v for v in violations)

    def test_arity_violation(self):
        # CreatePool is (3,4); 6 args is wrong.
        contract = _make_contract(physics=["CreatePool"])
        s = _parse_scaffold(
            "### Mechanic: M\nINTENT: pool\nPSEUDO:\n  pool\nAPI:\n  CreatePool(\"p\", 2, 2, {}, 9, 9)\n",
            "8",
        )
        violations = _validate_scaffold(s, contract, set())
        assert any("expects" in v for v in violations)

    def test_handle_as_spawn_first_arg(self):
        contract = _make_contract(physics=["SpawnStaticBox"])
        s = _parse_scaffold(
            "### Mechanic: M\nINTENT: x\nPSEUDO:\n  x\nAPI:\n  SpawnStaticBox(bell_handle, 0, 0, 0, 1, 1)\n",
            "8",
        )
        violations = _validate_scaffold(s, contract, {"bell_handle"})
        assert any("handle 'bell_handle'" in v for v in violations)


# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------

class TestEnvEnabled:
    def test_default_off(self, monkeypatch):
        monkeypatch.delenv("MIDWAY_MECHANICS_SCAFFOLD", raising=False)
        assert _env_enabled() is False

    def test_on(self, monkeypatch):
        monkeypatch.setenv("MIDWAY_MECHANICS_SCAFFOLD", "1")
        assert _env_enabled() is True

    def test_on_word(self, monkeypatch):
        monkeypatch.setenv("MIDWAY_MECHANICS_SCAFFOLD", "yes")
        assert _env_enabled() is True

    def test_off_word(self, monkeypatch):
        monkeypatch.setenv("MIDWAY_MECHANICS_SCAFFOLD", "0")
        assert _env_enabled() is False
