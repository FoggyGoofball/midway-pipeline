"""Tests for patch_contract.py — the language-agnostic fix-output contract.

These tests use a FAKE syntax checker (no luac dependency) to prove each
rejection rule fires deterministically, and a Lua-flavored contract to prove
the language seam works.
"""

import patch_contract as pc
from patch_contract import PatchContract, Rule, FixVerdict, extract_search_replace_hunks, has_code_default


def _ok_checker(text: str):
    return (True, "")


def _bad_checker(text: str):
    return (False, "syntax error near 'x'")


def _contract(syntax_check=_ok_checker) -> PatchContract:
    return PatchContract(syntax_check=syntax_check)


def _patch(search, replace):
    return f"<<<<<<< SEARCH\n{search}\n=======\n{replace}\n>>>>>>> REPLACE"


FILE = "function OnLoad()\n    local x = 1\n    return x\nend\n"


# ---------------------------------------------------------------------------
# Hunk extraction
# ---------------------------------------------------------------------------

class TestExtractHunks:
    def test_parses_one_block(self):
        hunks = extract_search_replace_hunks(_patch("local x = 1", "local x = 2"))
        assert len(hunks) == 1
        assert hunks[0]["search"] == "local x = 1"
        assert hunks[0]["replace"] == "local x = 2"

    def test_prose_returns_empty(self):
        assert extract_search_replace_hunks("I think you should change the variable.") == []


class TestHasCode:
    def test_code_line_detected(self):
        assert has_code_default("local x = 1\nif x then end") is True

    def test_pure_prose_rejected(self):
        assert has_code_default("please fix this variable") is False

    def test_fence_detected(self):
        assert has_code_default("here is the fix:\n```lua\nlocal x = 1\n```") is True


# ---------------------------------------------------------------------------
# Rejection rules
# ---------------------------------------------------------------------------

class TestRejections:
    def test_delegation_rejected(self):
        v = _contract().validate("[DELEGATE:other] do it yourself", FILE)
        assert v.ok is False and v.failed_rule is Rule.DELEGATION

    def test_format_rejected_on_prose(self):
        v = _contract().validate("just fix the nil reference please", FILE)
        assert v.ok is False and v.failed_rule is Rule.FORMAT

    def test_whole_file_rejected(self):
        out = _patch(FILE, FILE)  # SEARCH side is the entire file
        v = _contract().validate(out, FILE)
        assert v.ok is False and v.failed_rule is Rule.WHOLE_FILE

    def test_no_match_rejected(self):
        out = _patch("this line is not in the file", "x = 2")
        v = _contract().validate(out, FILE)
        assert v.ok is False and v.failed_rule is Rule.NO_MATCH

    def test_syntax_rejected(self):
        out = _patch("local x = 1", "local x = 2")
        v = _contract(syntax_check=_bad_checker).validate(out, FILE)
        assert v.ok is False and v.failed_rule is Rule.SYNTAX

    def test_clean_patch_accepted(self):
        out = _patch("local x = 1", "local x = 2")
        v = _contract().validate(out, FILE)
        assert v.ok is True and v.failed_rule is None


# ---------------------------------------------------------------------------
# Correction messages are deterministic and rule-specific
# ---------------------------------------------------------------------------

class TestCorrections:
    def test_each_rule_has_correction(self):
        for rule in Rule:
            assert pc.CORRECTIONS[rule], f"missing correction for {rule}"

    def test_verdict_exposes_correction(self):
        v = _contract().validate("[DELEGATE:foo]", FILE)
        assert "delegation" in v.correction.lower()


# ---------------------------------------------------------------------------
# Language seam: the Lua factory wires a syntax checker without importing the
# rest of the pipeline.
# ---------------------------------------------------------------------------

class TestLuaFactory:
    def test_for_lua_builds_contract(self):
        c = pc.for_lua(luac_exe="luac")
        assert c.syntax_check is not None
        # A genuinely valid Lua snippet passes the (real or missing) checker.
        ok, _ = c.syntax_check("local a = 1\nreturn a\n")
        assert ok is True
