"""
test_verify_gate.py — unit tests for the Phase 1 read-only invariant checks.
"""

import pytest

from _verify_gate import Finding, run_invariant_checks, verify_or_revert


def _findings(content: str):
    return run_invariant_checks(content, "test.lua")


def _kinds(findings):
    return {f.invariant for f in findings}


CLEAN = """function OnLoadStatic()
    SpawnSharedBooth()
end

function OnLoad()
    MidwayPhysics.OnStep(function(dt)
        local mods = AttractionConstants.modifiers
    end)
end

function OnUnload()
end
"""


class TestInvariantI1Syntax:
    def test_clean_passes(self):
        assert "I1" not in _kinds(_findings(CLEAN))

    def test_broken_flags(self):
        src = "function OnLoad()\n    if x then\nend\n"
        assert "I1" in _kinds(_findings(src))


class TestInvariantI2Contract:
    def test_phantom_api_flags(self):
        src = "function OnLoad()\n    MidwayPhysics.Frobnicate(h)\nend\n"
        assert "I2" in _kinds(_findings(src))

    def test_clean_has_no_i2(self):
        assert "I2" not in _kinds(_findings(CLEAN))


class TestInvariantI3Lifecycle:
    def test_missing_hook_flags(self):
        src = "function OnLoad()\n    MidwayPhysics.OnStep(function(dt) end)\nend\n"
        kinds = _kinds(_findings(src))
        assert "I3" in kinds

    def test_duplicate_hook_flags(self):
        src = (
            "function OnLoad()\nend\n"
            "function OnLoad()\nend\n"
            "function OnLoadStatic()\n    SpawnSharedBooth()\nend\n"
            "function OnUnload()\nend\n"
            "MidwayPhysics.OnStep(function(dt) end)\n"
        )
        assert "I3" in _kinds(_findings(src))

    def test_clean_has_no_i3(self):
        assert "I3" not in _kinds(_findings(CLEAN))


class TestInvariantI4Globals:
    def test_leaked_global_flags(self):
        src = "function OnLoad()\n    score = 0\nend\n"
        assert "I4" in _kinds(_findings(src))

    def test_clean_has_no_i4(self):
        assert "I4" not in _kinds(_findings(CLEAN))


class TestInvariantI5Arity:
    def test_wrong_arity_flags(self):
        src = "function OnLoad()\n    MidwayPhysics.SetMass(h)\nend\n"
        assert "I5" in _kinds(_findings(src))

    def test_clean_has_no_i5(self):
        assert "I5" not in _kinds(_findings(CLEAN))


class TestFindingShape:
    def test_finding_fields(self):
        findings = _findings("function OnLoad()\n    score = 0\nend\n")
        assert findings
        assert isinstance(findings[0], Finding)
        assert findings[0].invariant
        assert findings[0].message
        assert findings[0].rel_path == "test.lua"


class TestVerifyOrRevert:
    def test_keeps_clean(self):
        out = verify_or_revert(None, "t.lua", CLEAN, "BASELINE")
        assert out == CLEAN

    def test_reverts_broken(self):
        broken = "function OnLoad()\n    if x then\nend\n"
        out = verify_or_revert(None, "t.lua", broken, "BASELINE")
        assert out == "BASELINE"
