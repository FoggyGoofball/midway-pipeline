"""
test_planning_docs.py — Conversational plan-save / plan-load.

Verifies:
  1. save_chat_plan persists a planning-oriented chat answer under docs/plans/.
  2. save_chat_plan skips questions / non-planning prompts / short answers.
  3. get_planning_docs returns the saved plans and "" when none exist.
"""

import pytest

from _helpers_io import save_chat_plan, get_planning_docs, _planning_slug


class TestSaveChatPlan:
    def test_saves_planning_answer(self, tmp_path):
        prompt = "plan out the death's door mechanic for skeeball"
        answer = "1. Detect drain on ball loss.\n2. Hook into scoring.\n" + ("detail " * 60)
        path = save_chat_plan(prompt, answer, tmp_path)
        assert path
        assert (tmp_path / "docs" / "plans").is_dir()
        saved = (tmp_path / "docs" / "plans").glob("*.md")
        assert list(saved)

    def test_skips_question(self, tmp_path):
        prompt = "what is the plan for the strongman?"
        answer = "The plan is to ..." + ("detail " * 60)
        assert save_chat_plan(prompt, answer, tmp_path) == ""

    def test_skips_non_planning_prompt(self, tmp_path):
        prompt = "tell me a joke about physics"
        answer = "Why did the chicken cross..." + ("detail " * 60)
        assert save_chat_plan(prompt, answer, tmp_path) == ""

    def test_skips_short_answer(self, tmp_path):
        prompt = "plan a feature"
        assert save_chat_plan(prompt, "do it", tmp_path) == ""

    def test_slug_derivation(self):
        assert _planning_slug("plan out the deaths door mechanic") == "deaths_door_mechanic"


class TestGetPlanningDocs:
    def test_empty_when_none(self, tmp_path):
        assert get_planning_docs(tmp_path) == ""

    def test_returns_saved_plans(self, tmp_path):
        save_chat_plan("plan the echo strikes feature", "1. Detect hits.\n" + ("detail " * 60), tmp_path)
        docs = get_planning_docs(tmp_path)
        assert "Planning Docs (user-authored)" in docs
        assert "Echo" in docs or "echo" in docs.lower()
