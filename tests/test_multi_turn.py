"""
test_multi_turn.py — True multi-turn turn-separation.

Verifies that call_ollama_with_messages forwards the FULL messages array
(preserving user/assistant alternation) instead of collapsing it to a single
system+user pair.
"""

import pytest


def test_with_messages_preserves_multi_turn_array(monkeypatch):
    import ollama_client as oc

    captured = {}

    def fake_guard(system, user, label, model, params, messages=None):
        captured["messages"] = messages
        captured["system"] = system
        captured["user"] = user
        return "ok"

    monkeypatch.setattr(oc, "_stream_with_repetition_guard", fake_guard)
    monkeypatch.setattr(oc, "_evict_previous_model", lambda m: None)

    msgs = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "turn 1"},
        {"role": "assistant", "content": "resp 1"},
        {"role": "user", "content": "turn 2"},
    ]
    out = oc.call_ollama_with_messages(msgs, "Planner (iterative)")
    assert out == "ok"
    # The full array survives — assistant turn is NOT collapsed into user.
    assert captured["messages"] == msgs
    # Backward-compat extraction still surfaces the LAST user/system.
    assert captured["user"] == "turn 2"
    assert captured["system"] == "SYS"


def test_planning_builds_true_multi_turn_messages(monkeypatch):
    """The planner sends a real user/assistant alternation array."""
    from planning import run_planning_turn, list_active_drafts

    responses = iter([
        "## Plan\n- A\n\n## Open Questions\n- Q?\n",
        "## Plan\n- A\n[PLAN_FINALIZED]\n",
    ])
    captured = {}

    def fake_call(msgs, lbl):
        captured["messages"] = msgs
        return next(responses)

    import tempfile
    import pathlib
    d = pathlib.Path(tempfile.mkdtemp())

    run_planning_turn("plan out the deaths door mechanic", "sess", d, call_func=fake_call)
    roles = [m["role"] for m in captured["messages"]]
    assert roles[0] == "system"
    assert "user" in roles
    # second turn should include the prior assistant message
    run_planning_turn("carry it out, looks good", "sess", d, call_func=fake_call)
    roles2 = [m["role"] for m in captured["messages"]]
    assert "assistant" in roles2
    assert roles2.count("user") >= 2
