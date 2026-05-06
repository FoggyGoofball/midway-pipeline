#!/usr/bin/env python3
"""
Midway Pipeline — Generator-Based Sequential Streaming Layer
==============================================================
Replaces the old thread + callback + monkey-patch approach with a clean
generator-based pattern. The pipeline runs in a background daemon thread
that pushes tokens/events into a thread-safe queue. An outer generator
(yield_tokens) pulls from the queue, yielding (event_type, data) tuples.

This eliminates:
- threading.Lock guards (Queue is thread-safe)
- Global _stream_token_callback state
- Monkey-patch / restore cycle for call_ollama
- Separate heartbeat daemon thread (pings from the polling loop)

Usage:
    from pipeline_stream import stream_pipeline_generator
    for event_type, data in stream_pipeline_generator(prompt, ...):
        if event_type == "token":
            ...
"""

import json
import queue
import threading
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.resolve()

# ── Internal queue worker ───────────────────────────────────────────────────

def _run_pipeline_worker(prompt: str, checkpoint_id: str,
                         session_id: str, event_queue: queue.Queue):
    """Run the full pipeline in a daemon thread, pushing events into the Queue.

    Event format: (event_type: str, data: any)
    - ("token", text)       — an LLM token
    - ("announce", text)    — a phase announcement from _emit_progress
    - ("meta", dict)        — structured metadata
    - ("error", text)       — a pipeline error
    - ("done", None)        — pipeline complete
    """
    import pipeline as _pipeline

    # ── Reset pipeline state via PipelineContext singleton ──────────
    # Replaces five legacy module-level .clear() calls with the
    # authoritative single-method reset covering doc_cache, mesh
    # registries, work queue, progress listeners, and all other
    # runtime accumulators (all_results_dict, processed_ids, etc.).
    _pipeline._CTX.reset_state()

    # ── Register progress listener for phase announcements ──────────
    def _phase_listener(phase: str, status: str, detail: str = ""):
        """Relay phase announcements into the event queue."""
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

    # ── Patch call_ollama to stream into the queue ──────────────────
    # We create a closure over call_ollama_streamed that pushes each
    # token to the event queue and collects them into the full string.
    _original_call_ollama = _pipeline.call_ollama

    def _streaming_call_ollama(system: str, user: str, label: str,
                                model: str = None) -> str:
        """Streaming version that pushes tokens to event_queue."""
        full = []
        for token in _pipeline.call_ollama_streamed(system, user, label, model):
            full.append(token)
            try:
                event_queue.put(("token", token))
            except Exception:
                pass
        result = "".join(full)
        # Ensure clean newline after streaming output
        sys.stdout.write("\n")
        sys.stdout.flush()
        return result

    _pipeline.call_ollama = _streaming_call_ollama

    try:
        result = _pipeline.run_pipeline(prompt, checkpoint_id, session_id)
        event_queue.put(("done", result))
    except Exception as e:
        event_queue.put(("error", str(e)))
        import traceback
        event_queue.put(("error", traceback.format_exc()))
    finally:
        # Restore original call_ollama
        _pipeline.call_ollama = _original_call_ollama
        # Signal termination
        event_queue.put(("close", None))


# ── Public Generator API ────────────────────────────────────────────────────

def stream_pipeline_generator(prompt: str, checkpoint_id: str = None,
                              session_id: str = None):
    """Generator that yields (event_type, data) tuples from a pipeline run.

    The pipeline runs in a background daemon thread. This generator polls
    a thread-safe Queue, allowing the HTTP handler to iterate tokens
    sequentially without threads or locks.

    Event types yielded:
        ("meta", dict)      — initial session metadata
        ("token", str)      — individual LLM content token
        ("announce", str)   — phase announcement text
        ("ping", None)      — keep-alive signal (every ~1s during quiet)
        ("error", str)      — error message
        ("done", str)       — final full output string (last event)
        ("close", None)     — signals the generator to stop

    Yields:
        Tuple of (event_type: str, data: any)
    """
    event_queue = queue.Queue()

    # Spawn the pipeline in a daemon thread
    worker = threading.Thread(
        target=_run_pipeline_worker,
        args=(prompt, checkpoint_id, session_id, event_queue),
        daemon=True,
    )
    worker.start()

    # Yield initial metadata
    yield ("meta", {
        "id": f"pipeline-{datetime.now().strftime('%Y%m%d%H%M%S')}",
        "object": "chat.completion.chunk",
        "created": int(datetime.now().timestamp()),
        "model": "pipeline",
    })

    # Poll loop — inherently sequential, no Lock needed
    PING_INTERVAL = 1.0  # seconds
    last_ping = datetime.now().timestamp()

    while True:
        try:
            event_type, data = event_queue.get(timeout=PING_INTERVAL)
        except queue.Empty:
            # Timeout — send a keep-alive ping to prevent socket timeout
            yield ("ping", None)
            continue

        yield (event_type, data)

        if event_type == "close":
            break
        if event_type == "done":
            # After done, wait for worker to exit, then close
            worker.join(timeout=5.0)
            yield ("close", None)
            break
        if event_type == "error":
            # Keep processing after error — might get close
            continue


# ── Standalone CLI (for testing) ──────────────────────────────────────────

if __name__ == "__main__":
    import sys
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
            pass  # silent
        elif event_type == "error":
            print(f"\n[ERROR] {data}")
        elif event_type == "done":
            print(f"\n\n{'='*60}")
            print("  Pipeline complete.")
            print(f"{'='*60}")

    print("\n")
