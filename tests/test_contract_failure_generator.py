"""
test_contract_failure_generator.py — unit tests for contract-derived failure modes.

Verifies the signature parser handles the contract's shapes and that the derived
mutations trigger their INTENDED fixer fix.
"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SEEDER = REPO / "lora generator"
if str(SEEDER) not in sys.path:
    sys.path.insert(0, str(SEEDER))
sys.path.insert(0, str(REPO))

from _post_process_lua import post_process_lua  # noqa: E402
import contract_failure_generator as cfg  # noqa: E402


BASE = "function OnLoad()\n    MidwayPhysics.OnStep(function(dt) end)\nend\nfunction OnUnload() end\n"


def test_parse_signature_basic():
    name, params = cfg.parse_signature("SpawnStaticBox(lx, ly, lz, w, h, d) -> handle")
    assert name == "SpawnStaticBox"
    assert params == ["lx", "ly", "lz", "w", "h", "d"]


def test_parse_signature_optional_mass():
    name, params = cfg.parse_signature("SpawnDynamicBox(lx, ly, lz, w, h, d [, mass]) -> handle")
    assert name == "SpawnDynamicBox"
    assert params == ["lx", "ly", "lz", "w", "h", "d"]


def test_parse_signature_namespaced():
    name, params = cfg.parse_signature('MidwayInput.IsActionDown("fire") -> bool')
    assert name == "MidwayInput.IsActionDown"
    assert params == ['"fire"']


def test_derive_is_large():
    mutations = cfg.derive_contract_mutations()
    assert len(mutations) > 50, f"expected >50 derived mutations, got {len(mutations)}"


def test_bare_spawn_prefixed():
    m = cfg.derive_contract_mutations()
    broken = m["bare_SpawnStaticBox"](BASE)
    fixed = post_process_lua(broken)
    assert "SpawnStaticBox(" in broken
    assert "MidwayPhysics.SpawnStaticBox(" in fixed


def test_arity_over_truncated():
    m = cfg.derive_contract_mutations()
    broken = m["arity_over_SpawnStaticBox"](BASE)
    fixed = post_process_lua(broken)
    assert "SpawnStaticBox(0, 0, 0, 0, 0, 0, 9)" in broken
    assert "SpawnStaticBox(0, 0, 0, 0, 0, 0, 9)" not in fixed


def test_arity_under_padded():
    m = cfg.derive_contract_mutations()
    broken = m["arity_under_ApplyImpulse"](BASE)
    fixed = post_process_lua(broken)
    assert "ApplyImpulse(0, 0, 0)" in broken
    assert "ApplyImpulse(0, 0, 0, 0)" in fixed


def test_phantom_neutralized():
    m = cfg.derive_contract_mutations()
    broken = m["phantom_SpawnStaticBox"](BASE)
    fixed = post_process_lua(broken)
    assert "SpawnStaticBoxX" in broken
    assert "SpawnStaticBoxX(0)" not in fixed or "--" in fixed


def test_modifier_lowercase_canonicalized():
    m = cfg.derive_contract_mutations()
    key = next(k for k in m if k.startswith("mods_lower_"))
    broken = m[key](BASE)
    fixed = post_process_lua(broken)
    # The lowercase `mods.X` access is gone after canonicalization.
    assert "mods." in broken
    assert "mods." not in fixed
