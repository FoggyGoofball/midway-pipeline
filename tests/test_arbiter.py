"""
test_arbiter.py — unit tests for the local supreme arbiter (arbiter.py).

Covers question-tag parsing, the deterministic oracle's factual answers
(API existence, arity/signature, scope), and the clarification-log formatter.
"""

import pytest

from arbiter import (
    extract_questions,
    answer_oracle_question,
    build_clarification_block,
    strip_thinking,
    generalize_query,
    web_lookup,
    _signature_for,
)


class TestExtractQuestions:
    def test_extracts_oracle_and_coder_questions(self):
        text = (
            "[QUESTION:oracle:Is `Engine.AwardTickets` a real API?]\n"
            "[QUESTION:coder:Why did you spawn the puck in OnStep?]"
        )
        qs = extract_questions(text)
        assert qs == [
            ("oracle", "Is `Engine.AwardTickets` a real API?"),
            ("coder", "Why did you spawn the puck in OnStep?"),
        ]

    def test_no_questions_returns_empty(self):
        assert extract_questions("[MERGE:Tribunal:ok]") == []
        assert extract_questions("") == []

    def test_question_target_case_insensitive(self):
        qs = extract_questions("[QUESTION:Oracle:does SpawnStaticBox exist?]")
        assert qs == [("oracle", "does SpawnStaticBox exist?")]


class TestSignatureFor:
    def test_known_spawn_signature(self):
        sig = _signature_for("SpawnStaticBox")
        assert sig is not None and sig.startswith("SpawnStaticBox(")

    def test_namespaced_lookup(self):
        sig = _signature_for("MidwayPhysics.SpawnStaticBox")
        assert sig is not None and sig.startswith("SpawnStaticBox(")

    def test_unknown_returns_none(self):
        assert _signature_for("Engine.Puck") is None
        assert _signature_for("TotallyFakeAPI") is None


class TestOracleApiExistence:
    def test_known_api_yes(self):
        ok, ans = answer_oracle_question("Does Engine.AwardTickets exist?")
        assert ok and ans.startswith("YES")

    def test_phantom_api_no(self):
        ok, ans = answer_oracle_question("Is MidwayInput.SetActionState a real API?")
        assert ok and ans.startswith("NO")


class TestOracleArity:
    def test_spawn_arity(self):
        ok, ans = answer_oracle_question("How many arguments does SpawnStaticBox take?")
        assert ok and "SpawnStaticBox(" in ans

    def test_apply_impulse_arity(self):
        ok, ans = answer_oracle_question("What is the signature of ApplyImpulse?")
        assert ok and "ApplyImpulse" in ans


class TestOracleScope:
    CODE = (
        "local mallet = nil\n"
        "local SLOT_ID = 0\n"
        "function OnLoad()\n"
        "    local puck = MidwayPhysics.SpawnDynamicSphere(0, 0, 0, 1)\n"
        "end\n"
        "function OnUnload()\n"
        "    MidwayPhysics.DestroyBody(mallet)\n"
        "end\n"
    )

    def test_module_scope(self):
        ok, ans = answer_oracle_question("Where is mallet declared?", self.CODE)
        assert ok and "module" in ans and "FUNCTION" not in ans

    def test_function_scope(self):
        ok, ans = answer_oracle_question("Is puck declared at module scope?", self.CODE)
        assert ok and "FUNCTION" in ans

    def test_unknown_name_routes_to_coder(self):
        ok, _ = answer_oracle_question("Where is zzz_not_declared declared?", self.CODE)
        assert ok is False


class TestOracleSyntax:
    def test_valid_lua(self):
        ok, ans = answer_oracle_question("Is the file valid Lua?", "local x = 1\nprint(x)\n")
        assert ok and "VALID" in ans

    def test_invalid_lua(self):
        ok, ans = answer_oracle_question("Does the file compile?", "local x = \n")
        assert ok and "INVALID" in ans


class TestClarificationBlock:
    def test_formats_log(self):
        log = [("Q1?", "A1."), ("Q2?", "A2.")]
        block = build_clarification_block(log)
        assert "Q1: Q1?" in block and "A1: A1." in block
        assert "Q2: Q2?" in block and "A2: A2." in block

    def test_empty_log(self):
        assert build_clarification_block([]) == ""


class TestStripThinking:
    def test_strips_think_block(self):
        text = "<think>\nlet me reason\n</think>\n\n[MERGE:Tribunal:acceptable]"
        assert strip_thinking(text) == "[MERGE:Tribunal:acceptable]"

    def test_strips_multiline_think(self):
        text = "<think>line1\nline2</think>[REJECT:Tribunal:bad]"
        assert strip_thinking(text) == "[REJECT:Tribunal:bad]"

    def test_no_think_unchanged(self):
        assert strip_thinking("[MERGE:Tribunal:ok]") == "[MERGE:Tribunal:ok]"

    def test_empty(self):
        assert strip_thinking("") == ""


class TestGeneralizeQuery:
    def test_strips_code_block_paths_and_identifiers(self):
        raw = (
            "Fix this:\n```lua\nlocal x = MidwayPhysics.SpawnStaticBox(0,0,0,1,1,1)\n```\n"
            "in attractions/strongman/strongman.lua line 62"
        )
        out = generalize_query(raw)
        assert "```" not in out
        assert "SpawnStaticBox" not in out
        assert "MidwayPhysics" not in out
        assert ".lua" not in out
        assert "line 62" not in out

    def test_anonymizes_project_identifiers(self):
        out = generalize_query("Is MidwayPhysics.GetBodyFromHandle a real API?")
        assert "MidwayPhysics" not in out
        assert "physics engine bridge API" in out

    def test_scrubs_inline_call(self):
        out = generalize_query("Call SpawnStaticBox(0, 0, 0, 1, 1, 1) to build a box")
        assert "SpawnStaticBox" not in out
        assert "0, 0" not in out

    def test_scrubs_inline_dotted_field(self):
        out = generalize_query("use mods.heat for the tuning value")
        assert "mods.heat" not in out

    def test_preserves_common_abbreviation(self):
        out = generalize_query("is the arity correct, e.g. six args?")
        assert "e.g" in out

    def test_empty(self):
        assert generalize_query("") == ""


class TestWebLookup:
    def test_no_key_returns_unanswered(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        ok, ans = web_lookup("what is a static box in a physics engine?")
        assert ok is False
        assert ans == ""
