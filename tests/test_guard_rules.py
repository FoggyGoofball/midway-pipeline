"""
test_guard_rules.py — unit tests for derive_guard_rules + rule_extractor.

Verifies the contract-derived rules carry arbiter metadata (objection +
oracle question) and that the retroactive rule extractor proposes flagged
candidates from a broken->fixed delta.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SEEDER = REPO / "lora generator"
if str(SEEDER) not in sys.path:
    sys.path.insert(0, str(SEEDER))
sys.path.insert(0, str(REPO))

import contract_failure_generator as cfg  # noqa: E402
from rule_extractor import propose_rule  # noqa: E402


def test_derive_guard_rules_large():
    rules = cfg.derive_guard_rules()
    assert len(rules) > 50, f"expected >50 derived rules, got {len(rules)}"
    for r in rules.values():
        assert r.objection, f"{r.name} missing objection"
        assert r.category in {"bare", "arity_over", "arity_under", "phantom", "mods_lower"}


def test_guard_rules_have_questions_except_mods():
    rules = cfg.derive_guard_rules()
    assert rules["bare_SpawnStaticBox"].question
    assert rules["phantom_SpawnStaticBox"].question
    assert rules["arity_over_SpawnStaticBox"].question
    mods_key = next(k for k in rules if k.startswith("mods_lower_"))
    assert rules[mods_key].question is None


def test_derive_contract_mutations_backcompat():
    m = cfg.derive_contract_mutations()
    assert "bare_SpawnStaticBox" in m
    assert callable(m["bare_SpawnStaticBox"])


def test_rule_extractor_detects_bare_call():
    broken = "function OnLoad()\n    local _ = SpawnStaticBox(0,0,0,1,1,1)\nend\n"
    fixed = "function OnLoad()\n    local _ = MidwayPhysics.SpawnStaticBox(0,0,0,1,1,1)\nend\n"
    p = propose_rule(broken, fixed)
    assert p.needs_review is True
    assert p.category == "bare_call"


def test_rule_extractor_detects_phantom():
    broken = "MidwayPhysics.SpawnStaticBoxX(0)\n"
    fixed = "-- MidwayPhysics.SpawnStaticBoxX(0) neutralized\n"
    p = propose_rule(broken, fixed)
    assert p.needs_review is True
    assert p.category == "phantom_api"


def test_rule_extractor_unknown_flags_review():
    broken = "completely: bogus syntax !!!\n"
    fixed = "completely: bogus syntax\n"
    p = propose_rule(broken, fixed)
    assert p.needs_review is True
    assert p.category == "unknown"
