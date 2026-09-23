"""
test_failure_mode_seeder.py — unit tests for the on-demand failure-mode seeder.

Verifies every deterministic mutation produces a REAL fixer delta and that the
targeted failure signature is actually transformed (not just noise from the
base file's own lifecycle injections).
"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SEEDER_DIR = REPO / "lora generator"
if str(SEEDER_DIR) not in sys.path:
    sys.path.insert(0, str(SEEDER_DIR))
sys.path.insert(0, str(REPO))

from _post_process_lua import post_process_lua  # noqa: E402
import failure_mode_seeder as fs  # noqa: E402


BASE = (
    "function OnLoad()\n"
    "    MidwayPhysics.OnStep(function(dt) end)\n"
    "end\n"
    "function OnUnload()\n"
    "end\n"
)


def _fix(mutation_name: str) -> str:
    broken = fs.MUTATIONS[mutation_name](BASE)
    return post_process_lua(broken)


def test_all_mutations_produce_a_delta():
    for name in fs.MUTATIONS:
        broken = fs.MUTATIONS[name](BASE)
        fixed = post_process_lua(broken)
        assert fixed != broken, f"mutation {name!r} produced no delta"


def test_bare_namespace_prefixed():
    broken = fs.m_bare_namespace(BASE)
    fixed = post_process_lua(broken)
    assert "local base = SpawnStaticBox" in broken
    assert "local base = MidwayPhysics.SpawnStaticBox" in fixed


def test_phantom_api_neutralized():
    fixed = _fix("phantom_api")
    assert "GetBodyFromHandle" not in fixed


def test_phantom_engine_neutralized():
    fixed = _fix("phantom_engine")
    assert "ModifyPuckState" not in fixed


def test_modifier_standalone_guarded():
    broken = fs.m_modifier_standalone(BASE)
    fixed = post_process_lua(broken)
    assert "AttractionConstants.modifiers" in broken
    assert "AttractionConstants.modifiers or {}" in fixed


def test_modifier_lowercase_canonicalized():
    broken = fs.m_modifier_lowercase(BASE)
    fixed = post_process_lua(broken)
    assert "mods.heat" in broken
    assert "mods.heat" not in fixed


def test_arity_over_truncated():
    broken = fs.m_arity_over(BASE)
    fixed = post_process_lua(broken)
    # 7-arg SpawnStaticBox must no longer appear with 7 args.
    assert "SpawnStaticBox(0, 0, 0, 1, 1, 1, 9)" in broken
    assert "SpawnStaticBox(0, 0, 0, 1, 1, 1, 9)" not in fixed


def test_arity_under_padded():
    broken = fs.m_arity_under(BASE)
    fixed = post_process_lua(broken)
    assert "ApplyImpulse(h, 0, 1)" in broken
    assert "ApplyImpulse(h, 0, 1)" not in fixed


def test_duplicate_underscore_split():
    broken = fs.m_duplicate_underscore(BASE)
    fixed = post_process_lua(broken)
    assert "local _ = MOD.heat, _ = MOD.luck" in broken
    assert "local _ = MOD.heat, _ = MOD.luck" not in fixed


def test_bare_expression_wrapped():
    broken = fs.m_bare_expression(BASE)
    fixed = post_process_lua(broken)
    assert "MOD.heat" in broken
    assert "local _ = MOD.heat" in fixed


def test_json_colon_table_fixed():
    broken = fs.m_json_colon_table(BASE)
    fixed = post_process_lua(broken)
    assert '"radius": 1' in broken
    assert "radius = 1" in fixed


def test_broken_local_neutralized():
    broken = fs.m_broken_local(BASE)
    fixed = post_process_lua(broken)
    assert "local lx)" in broken
    assert "local lx)" not in fixed


def test_roblox_neutralized():
    broken = fs.m_roblox(BASE)
    fixed = post_process_lua(broken)
    assert "local v = Vector3.new" in broken
    assert "-- [roblox removed]" in fixed
    # The ACTIVE statement is gone (the call text only survives inside the comment).
    assert "\nlocal v = Vector3.new" not in fixed


def test_prompt_catalog_nonempty():
    assert len(fs.PROMPT_TEMPLATES) >= 5
    for name, prompt in fs.PROMPT_TEMPLATES.items():
        assert prompt.strip()
