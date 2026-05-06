#!/usr/bin/env python3
"""
Midway Pipeline — Generator-Based Streaming HTTP Server
=========================================================
Serves the generator-based pipeline as an SSE (Server-Sent Events)
HTTP endpoint at /stream.

Built on http.server (stdlib) — no external dependencies.

This replaces the old multi-thread + callback approach with a clean
generator-driven pattern. The pipeline runs in a background daemon
thread; tokens flow through a thread-safe Queue; the HTTP handler
polls the generator and writes SSE chunks to the response.

Usage:
    python pipeline_stream_server.py              # port 8765
    # then: curl http://localhost:8765?prompt=hello
"""

import json
import sys
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime
from pathlib import Path

# Add project root to sys.path for sibling imports
PROJECT_ROOT = Path(__file__).parent.resolve()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipeline_stream import stream_pipeline_generator

# ── Defaults ────────────────────────────────────────────────────────────────

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8765


# ── HTTP Request Handler ────────────────────────────────────────────────────

class StreamHandler(BaseHTTPRequestHandler):
    """HTTP handler that serves generator-based pipeline output as SSE."""

    # Silence default logging (we log manually)
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if parsed.path == "/health":
            self._serve_health()
        elif parsed.path == "/stream":
            self._serve_stream(params)
        elif parsed.path == "/v1/models":
            self._serve_models()
        elif parsed.path == "/" or parsed.path == "":
            self._serve_index()
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")

    def do_POST(self):
        """OpenAI-compatible POST /v1/chat/completions endpoint.

        Accepts the standard OpenAI chat completions request format:
        {
            "model": "pipeline",
            "messages": [{"role": "user", "content": "..."}],
            "stream": true
        }
        Returns SSE-streamed response tokens.
        """
        parsed = urlparse(self.path)

        if parsed.path not in ("/v1/chat/completions",):
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Not Found")
            return

        # Read request body
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        try:
            req = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Invalid JSON"}).encode("utf-8"))
            return

        # Extract prompt from messages
        messages = req.get("messages", [])
        prompt = ""
        for msg in messages:
            if msg.get("role") in ("user", "system"):
                content = msg.get("content", "")
                if content:
                    prompt = content

        if not prompt:
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "No user message found"}).encode("utf-8"))
            return

        stream_mode = req.get("stream", True)
        model = req.get("model", "pipeline")

        print(f"  [OpenAI POST] /v1/chat/completions — prompt='{prompt[:60]}...' stream={stream_mode}")

        if stream_mode:
            # SSE-stream the response
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

            for event_type, data in stream_pipeline_generator(prompt, None, None):
                try:
                    if event_type == "token":
                        chunk = {
                            "id": "pipeline-" + datetime.now().strftime("%Y%m%d%H%M%S"),
                            "object": "chat.completion.chunk",
                            "created": int(datetime.now().timestamp()),
                            "model": model,
                            "choices": [{"delta": {"content": data}, "index": 0, "finish_reason": None}],
                        }
                    elif event_type == "announce":
                        # Send announcements as a special chunk
                        chunk = {
                            "id": "pipeline-announce",
                            "object": "chat.completion.chunk",
                            "created": int(datetime.now().timestamp()),
                            "model": model,
                            "choices": [{"delta": {"content": f"\n{data}\n"}, "index": 0, "finish_reason": None}],
                        }
                    elif event_type == "done":
                        # Send the final chunk with finish_reason, then the
                        # [DONE] sentinel required by the OpenAI SSE protocol
                        # (the Continue extension hangs on "generating"
                        # without this termination signal).
                        chunk = {
                            "id": "pipeline-done",
                            "object": "chat.completion.chunk",
                            "created": int(datetime.now().timestamp()),
                            "model": model,
                            "choices": [{"delta": {}, "index": 0, "finish_reason": "stop"}],
                        }
                        done_sse = f"data: {json.dumps(chunk)}\n\n"
                        self.wfile.write(done_sse.encode("utf-8"))
                        self.wfile.write(b"data: [DONE]\n\n")
                        self.wfile.flush()
                        continue
                    elif event_type == "error":
                        chunk = {
                            "id": "pipeline-error",
                            "object": "chat.completion.chunk",
                            "created": int(datetime.now().timestamp()),
                            "model": model,
                            "choices": [{"delta": {"content": f"\n[ERROR: {data}]\n"}, "index": 0, "finish_reason": "stop"}],
                        }
                    elif event_type == "close":
                        break
                    elif event_type == "ping":
                        # SSE comment to keep connection alive
                        self.wfile.write(": ping\n\n".encode("utf-8"))
                        self.wfile.flush()
                        continue
                    else:
                        continue

                    sse_data = f"data: {json.dumps(chunk)}\n\n"
                    self.wfile.write(sse_data.encode("utf-8"))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    print("  [OpenAI POST] Client disconnected")
                    break

            print(f"  [OpenAI POST] Stream ended for '{prompt[:60]}...'")
        else:
            # Non-streaming: collect full response and return as JSON
            from pipeline import run_pipeline
            result = run_pipeline(prompt)
            response_body = {
                "id": "pipeline-" + datetime.now().strftime("%Y%m%d%H%M%S"),
                "object": "chat.completion",
                "created": int(datetime.now().timestamp()),
                "model": model,
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": result},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(response_body).encode("utf-8"))

    def _serve_models(self):
        """Serve the list of available models (OpenAI-compatible)."""
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        models_list = {
            "object": "list",
            "data": [
                {"id": "pipeline", "object": "model", "created": int(datetime.now().timestamp()), "owned_by": "midway"},
            ],
        }
        self.wfile.write(json.dumps(models_list).encode("utf-8"))

    def _serve_health(self):
        """Simple health check endpoint."""
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({
            "status": "ok",
            "timestamp": datetime.now().isoformat(),
        }).encode("utf-8"))

    def _serve_index(self):
        """Serve a minimal HTML page with instructions."""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"""<!DOCTYPE html>
<html><head><title>Midway Pipeline Stream</title></head><body>
<h1>Midway Pipeline Stream</h1>
<p>Use: <code>curl http://localhost:8765/stream?prompt=YOUR_PROMPT</code></p>
<p>Or open in browser: <a href="/stream?prompt=hello">/stream?prompt=hello</a></p>
</body></html>""")

    def _serve_stream(self, params: dict):
        """Generator-driven SSE stream handler.

        The pipeline runs via stream_pipeline_generator(), yielding
        (event_type, data) tuples. Each tuple is serialized as an SSE
        "data:" line. This eliminates all threading.Lock guards and
        the separate heartbeat daemon thread.

        SSE format:
            data: {"type":"token","content":"..."}
            data: {"type":"announce","content":"..."}
            data: {"type":"done"}
            data: {"type":"error","content":"..."}
        """
        prompt = params.get("prompt", [""])[0]
        if not prompt:
            self.send_response(400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Bad Request: missing 'prompt' parameter")
            return

        # Parse optional args
        checkpoint_id = params.get("checkpoint", [None])[0] or None
        session_id = params.get("session_id", [None])[0] or None

        print(f"  [StreamServer] Starting stream for '{prompt[:60]}...'")

        # ── SSE Response Headers ────────────────────────────────────
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")  # Nginx proxy support
        self.end_headers()

        # ── Iterate the generator, sending SSE events ──────────────
        # No threading.Lock involved — the generator serializes access.
        # No heartbeat daemon — the generator yields ("ping", None)
        # naturally during quiet periods.

        for event_type, data in stream_pipeline_generator(
            prompt, checkpoint_id, session_id,
        ):
            try:
                if event_type == "token":
                    sse = json.dumps({"type": "token", "content": data})
                elif event_type == "announce":
                    sse = json.dumps({"type": "announce", "content": data})
                elif event_type == "meta":
                    sse = json.dumps({"type": "meta", "data": data})
                elif event_type == "ping":
                    # SSE comment — prevents proxy / socket timeout
                    sse = ": ping"
                elif event_type == "error":
                    sse = json.dumps({"type": "error", "content": data})
                elif event_type == "done":
                    sse = json.dumps({"type": "done", "content": data})
                    self.wfile.write(f"data: {sse}\n\n".encode("utf-8"))
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                    continue
                elif event_type == "close":
                    break
                else:
                    continue

                self.wfile.write(f"data: {sse}\n\n".encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                # Client disconnected — stop the generator
                print("  [StreamServer] Client disconnected")
                break

        print(f"  [StreamServer] Stream ended for '{prompt[:60]}...'")


def create_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
    """Create and return an HTTPServer with the StreamHandler."""
    server = HTTPServer((host, port), StreamHandler)
    return server


def run_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
    """Run the streaming HTTP server (blocking call)."""
    server = create_server(host, port)
    print(f"\n{'='*60}")
    print(f"  Midway Pipeline Stream Server (Generator-Based)")
    print(f"  Listening on http://{host}:{port}")
    print(f"  SSE endpoint: http://{host}:{port}/stream?prompt=...")
    print(f"  Health check: http://{host}:{port}/health")
    print(f"{'='*60}\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  [StreamServer] Shutting down...")
        server.shutdown()


# ── Entry Point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(
        description="Midway Pipeline Stream Server (Generator-Based)")
    parser.add_argument("--host", type=str, default=DEFAULT_HOST,
                        help=f"Host to bind (default: {DEFAULT_HOST})")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"Port to listen on (default: {DEFAULT_PORT})")
    args = parser.parse_args()
    run_server(args.host, args.port)
