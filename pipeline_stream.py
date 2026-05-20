#!/usr/bin/env python3
"""
Midway Pipeline — Generator-Based Sequential Streaming Layer
==============================================================
Replaces the old thread + callback + monkey-patch approach with a clean
generator-based pattern. The pipeline runs in a background daemon thread
that pushes tokens/events into a thread-safe queue. An outer generator
(yield_tokens) pulls from the queue, yielding (event_type, data) tuples.

Features added:
- Intercepts builtins.input to broadcast interactive y/n prompts to VS Code.
- Measures exact Time-To-First-Token (TTFT) and Tokens Per Minute (TPM).
"""

import json
import queue
import threading
import sys
import time
import builtins
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.resolve()

# ── Internal queue worker ───────────────────────────────────────────────────

def _run_pipeline_worker(prompt: str, checkpoint_id: str,
                         session_id: str, event_queue: queue.Queue):
    """Run the full pipeline in a daemon thread, pushing events into the Queue."""
    import pipeline as _pipeline

    # Reset pipeline state via PipelineContext singleton
    _pipeline._CTX.reset_state()

    # Register progress listener for phase announcements
    def _phase_listener(phase: str, status: str, detail: str = ""):
        if status == "started":
            msg = f"\n**▶ {phase}:** {detail}\n"
        elif status == "done":
            msg = f"\n**✓ {phase} completed.**\n"
        elif status == "error":
            msg = f"\n**✗ {phase} ERROR:** {detail}\n"
        else:
            msg = f"\n**{phase}:** {detail}\n"
        try:
            event_queue.put(("announce", msg))
        except Exception:
            pass
    _pipeline.register_progress_listener(_phase_listener)

    # ── Shared Telemetry State ──────────────────────────────────────────────
    class TelemetryState:
        start_time = None
        first_token_time = None
        token_count = 0
        model_name = "unknown"
        label = "unknown"

    telemetry = TelemetryState()

    # ── Intercept Interactive Prompts (Y/N visibility in Continue) ──────────
    _original_input = builtins.input
    def _stream_aware_input(prompt_text=""):
        # Push announcement to the stream so the VS Code user sees the prompt
        msg = (
            f"\n\n⚠️ **TERMINAL INPUT REQUIRED:** {prompt_text}\n"
            f"*(Please switch to the terminal window running the Midway server to type your response)*\n\n"
        )
        event_queue.put(("announce", msg))
        return _original_input(prompt_text)
    
    builtins.input = _stream_aware_input

    # ── Wrap Generator to Measure TTFT & TPM/TPS Telemetry ──────────────────
    import ollama_client as _ollama_client
    _original_call_streamed = getattr(_ollama_client, 'call_ollama_streamed', None)

    def _instrumented_call_streamed(system: str, user: str, label: str, model=None, params=None):
        telemetry.start_time = time.time()
        telemetry.first_token_time = None
        telemetry.token_count = 0
        telemetry.model_name = str(model or getattr(_ollama_client, 'MODEL', 'unknown'))
        telemetry.label = str(label)

        try:
            gen = _original_call_streamed(system, user, label, model, params)
            for tok in gen:
                yield tok
        finally:
            # Broadcast telemetry upon completion or interruption
            total_elapsed = time.time() - telemetry.start_time if telemetry.start_time else 0.0
            if telemetry.start_time and telemetry.first_token_time and telemetry.token_count > 0:
                gen_duration = time.time() - telemetry.first_token_time
                ttft = telemetry.first_token_time - telemetry.start_time
                streaming_tps = telemetry.token_count / gen_duration if gen_duration > 0 else 0.0
                tpm = streaming_tps * 60.0
                # Effective TPS includes TTFT — gives a true speed metric including model load time
                effective_tps = telemetry.token_count / total_elapsed if total_elapsed > 0 else 0.0

                # Flag extreme TTFT
                ttft_flag = " ⚠️ EXTREME" if ttft > 10.0 else ""
                # Flag low effective TPS (below 2.0 tok/s across total time)
                low_tps_flag = " 🚨 SLOW" if effective_tps < 2.0 else ""

                stat_msg = (
                    f"\n[⚡ **Telemetry:** `{telemetry.model_name}` | TTFT: **{ttft:.2f}s**{ttft_flag} | "
                    f"Speed: **{streaming_tps:.1f} tok/s** ({tpm:.0f} TPM) | "
                    f"Effective: **{effective_tps:.1f} tok/s** | Tokens: {telemetry.token_count}]\n"
                )
                event_queue.put(("announce", stat_msg))
                print(
                    f"  [Telemetry] {telemetry.label} ({telemetry.model_name}) — "
                    f"TTFT: {ttft:.2f}s{ttft_flag} | "
                    f"Speed: {streaming_tps:.1f} tok/s ({tpm:.0f} TPM) | "
                    f"Effective: {effective_tps:.1f} tok/s{low_tps_flag}",
                    flush=True,
                )

    if _original_call_streamed:
        _ollama_client.call_ollama_streamed = _instrumented_call_streamed

    # Hook token callback
    _original_callback = getattr(_ollama_client, '_stream_callback', None)
    
    def _token_callback(token: str):
        if telemetry.first_token_time is None:
            telemetry.first_token_time = time.time()
        telemetry.token_count += 1
        event_queue.put(("token", token))

    _ollama_client._stream_callback = _token_callback

    # ── Execute Pipeline Worker ─────────────────────────────────────────────
    try:
        result = _pipeline.run_pipeline(prompt, checkpoint_id, session_id)
        event_queue.put(("done", result))
    except Exception as e:
        # Push error to stream queue
        event_queue.put(("error", str(e)))
        import traceback
        event_queue.put(("error", traceback.format_exc()))
    finally:
        # Restore monkey-patches safely
        builtins.input = _original_input
        if _original_call_streamed:
            _ollama_client.call_ollama_streamed = _original_call_streamed
        if hasattr(_ollama_client, '_stream_callback'):
            _ollama_client._stream_callback = _original_callback
        
        event_queue.put(("close", None))


# ── Public Generator API ────────────────────────────────────────────────────

def stream_pipeline_generator(prompt: str, checkpoint_id: str = None,
                              session_id: str = None):
    """Generator polling a thread-safe Queue sequentially."""
    event_queue = queue.Queue()

    worker = threading.Thread(
        target=_run_pipeline_worker,
        args=(prompt, checkpoint_id, session_id, event_queue),
        daemon=True,
    )
    worker.start()

    yield ("meta", {
        "id": f"pipeline-{datetime.now().strftime('%Y%m%d%H%M%S')}",
        "object": "chat.completion.chunk",
        "created": int(datetime.now().timestamp()),
        "model": "pipeline",
    })

    PING_INTERVAL = 1.0  # seconds

    while True:
        try:
            event_type, data = event_queue.get(timeout=PING_INTERVAL)
        except queue.Empty:
            yield ("ping", None)
            continue

        yield (event_type, data)

        if event_type == "close":
            break
        if event_type == "done":
            worker.join(timeout=5.0)
            yield ("close", None)
            break
        if event_type == "error":
            continue


# ── Standalone CLI (for testing) ──────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python pipeline_stream.py <prompt>")
        sys.exit(1)

    prompt = sys.argv[1]
    print("\n" + "="*60)
    print("  Pipeline Streaming Test (Generator)")
    print("="*60)

    for event_type, data in stream_pipeline_generator(prompt):
        if event_type == "token":
            print(data, end="", flush=True)
        elif event_type == "announce":
            print(data, end="", flush=True)
        elif event_type == "meta":
            print(f"\n[Meta: {json.dumps(data, indent=2)}]")
        elif event_type == "ping":
            pass
        elif event_type == "error":
            print(f"\n[ERROR] {data}")
        elif event_type == "done":
            print(f"\n\n{'='*60}")
            print("  Pipeline complete.")
            print(f"{'='*60}")

    print("\n")