"""
test_pipeline_stream.py — Regression: the telemetry wrapper in pipeline_stream
must forward the `messages` kwarg added for true turn-separation.
"""

import pytest


def test_instrumented_stream_forwards_messages(monkeypatch):
    import pipeline
    import pipeline_stream
    import ollama_client as oc

    captured = {}

    def fake_original_streamed(system, user, label, model=None, params=None, messages=None):
        captured["messages"] = messages
        yield "hello"

    # The worker captures the "original" streamed function before wrapping it.
    monkeypatch.setattr(oc, "call_ollama_streamed", fake_original_streamed)

    def fake_run_pipeline(prompt, checkpoint_id, session_id):
        # This exercises the wrapped (instrumented) call_ollama_streamed, which
        # must accept and forward the `messages` kwarg or raise TypeError.
        out = oc.call_ollama("sys", "user", "test")
        assert "hello" in out, "call_ollama did not receive the streamed token"
        return out

    monkeypatch.setattr(pipeline, "run_pipeline", fake_run_pipeline)

    events = list(pipeline_stream.stream_pipeline_generator("prompt", None, None))
    event_types = [t for t, _ in events]
    assert "done" in event_types
    assert "error" not in event_types, [d for t, d in events if t == "error"]
    # The stateless call_ollama path passes messages=None through the wrapper.
    assert captured.get("messages") is None

