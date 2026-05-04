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
        elif parsed.path == "/" or parsed.path == "":
            self._serve_index()
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")

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
