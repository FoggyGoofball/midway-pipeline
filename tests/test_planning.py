"""
test_planning.py — Multi-turn conversational planning.

Verifies:
  1. Planning-intent and approval detection (incl. multi-workstream phrasing).
  2. A two-turn dialogue: draft -> clarify -> approve -> publish -> cleanup.
  3. Draft resolution falls back to the single active draft (no session_id).
"""

import pytest

from planning import (
    is_planning_intent,
    is_plan_approval,
    run_planning_turn,
    resolve_active_draft,
    list_active_drafts,
    plans_dir,
)


def _mock_planner(responses):
    state = {"calls": []}

    def call(sys_, usr_, lbl_):
        state["calls"].append((sys_, usr_, lbl_))
        return responses[min(state["i"] if "i" in state else 0, len(responses) - 1)]

    # simpler: consume sequentially
    def seq(sys_, usr_, lbl_):
        state["calls"].append((sys_, usr_, lbl_))
        idx = len(state["calls"]) - 1
        return responses[min(idx, len(responses) - 1)]

    return seq, state


class TestIntent:
    def test_multi_workstream_planning_phrase(self):
        assert is_planning_intent(
            "I'm thinking about moving forward with the next phase, "
            "how should we go about adding sol2 and the billboarding mechanic for the barker?"
        )

    def test_simple_plan(self):
        assert is_planning_intent("plan out the deaths door mechanic")

    def test_build_not_planning(self):
        assert not is_planning_intent("build the strongman striker")

    def test_approval_phrases(self):
        assert is_plan_approval("looks good, carry it out")
        assert is_plan_approval("approved, ship it")


class TestMultiTurn:
    def test_two_turn_conversation_publishes_and_cleans_up(self, tmp_path):
        responses = [
            "## Plan\n- Workstream A: sol2 binding layer\n- Workstream B: barker billboarding\n\n"
            "## Open Questions\n- Which workstream first?\n",
            "## Plan\n- Workstream B first, then A\n[PLAN_FINALIZED]\n",
        ]
        call, state = _mock_planner(responses)

        # Turn 1: new planning request -> draft with questions
        resp1, done1 = run_planning_turn(
            "how should we go about adding sol2 and billboarding?",
            "", tmp_path, call_func=call,
        )
        assert done1 is False
        assert "Open Questions" in resp1
        assert len(list_active_drafts(tmp_path)) == 1

        # Turn 2: refinement + approval -> finalized and published
        resp2, done2 = run_planning_turn(
            "prioritise billboarding first. looks good",
            "", tmp_path, call_func=call,
        )
        assert done2 is True
        assert list_active_drafts(tmp_path) == []
        published = list(plans_dir(tmp_path).glob("*_PLAN.md"))
        assert published
        text = published[0].read_text(encoding="utf-8")
        assert "Workstream B first" in text

    def test_continuation_without_session_id(self, tmp_path):
        # Seed a draft, then a short refinement continues it via the
        # single-active-draft fallback.
        run_planning_turn("plan out the echo strikes mechanic", "", tmp_path,
                          call_func=lambda s, u, l: "## Plan\n- detect hits\n\n- How many strikes?\n")
        draft = resolve_active_draft(tmp_path, "", "three strikes please")
        assert draft is not None
        assert draft["slug"]

    def test_no_active_draft_returns_none(self, tmp_path):
        assert resolve_active_draft(tmp_path, "", "build the thing") is None
