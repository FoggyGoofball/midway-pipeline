#!/usr/bin/env python3
"""
Midway Pipeline — Generator-Based Streaming HTTP Server
=========================================================
Serves the generator-based pipeline as an SSE (Server-Sent Events)
HTTP endpoint at /stream, plus an OpenAI-compatible /v1/chat/completions
endpoint so that Continue can connect natively.

Built on http.server (stdlib) — no external dependencies.

Usage:
    python pipeline_stream_server.py              # port 8765
    # then: curl http://localhost:8765/stream?prompt=hello
    # then: curl -X POST http://localhost:8765/v1/chat/completions -H 'Content-Type: application/json' -d '{"model":"pipeline","messages":[{"role":"user","content":"hello"}]}'
"""

import json
import sys
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs
from datetime import datetime
from pathlib import Path
import hashlib
import time
import uuid

# ── Module Import Precedence ─────────────────────────────────────────────
# Enforce local directory at sys.path[0] so Python imports from
# midway-pipeline first, not the game repository (..\midway).
# Keep PROJECT_ROOT accessible for file operations but NOT at top priority.
LOCAL_DIR = Path(__file__).resolve().parent
if str(LOCAL_DIR) in sys.path:
    sys.path.remove(str(LOCAL_DIR))
sys.path.insert(0, str(LOCAL_DIR))

PROJECT_ROOT = Path(os.getenv("MIDWAY_PROJECT_ROOT", LOCAL_DIR.with_name("midway")))
if str(PROJECT_ROOT) in sys.path:
    sys.path.remove(str(PROJECT_ROOT))
sys.path.append(str(PROJECT_ROOT))

from pipeline_stream import stream_pipeline_generator

# ── Defaults ────────────────────────────────────────────────────────────────

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8765

# ── Threading HTTPServer ─────────────────────────────────────────────────────
# Standard library's HTTPServer is single-threaded — blocking!
# ThreadingMixIn spawns a new thread for each request so concurrent
# requests (e.g., a new chat while one is still streaming) don't deadlock.

class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """HTTP server that handles each request in a separate thread."""
    daemon_threads = True
    allow_reuse_address = True
    block_on_close = False


# ── CORS Helper ──────────────────────────────────────────────────────────────

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Requested-With",
    "Access-Control-Max-Age": "86400",
}

def _add_cors_headers(handler):
    """Add CORS headers to a response."""
    for key, value in CORS_HEADERS.items():
        handler.send_header(key, value)


# ── HTTP Request Handler ────────────────────────────────────────────────────

class StreamHandler(BaseHTTPRequestHandler):
    """HTTP handler that serves generator-based pipeline output as SSE."""

    # Silence default logging (we log manually)
    def log_message(self, format, *args):
        pass

    # ── OPTIONS (CORS preflight) ──────────────────────────────────────

    def do_OPTIONS(self):
        """Handle CORS preflight requests (required by VS Code webview)."""
        self.send_response(204)
        _add_cors_headers(self)
        self.end_headers()

    # ── GET routes ────────────────────────────────────────────────────

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
            _add_cors_headers(self)
            self.end_headers()
            self.wfile.write(b"Not Found")

    def _serve_health(self):
        """Simple health check endpoint."""
        self.send_response(200)
        _add_cors_headers(self)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({
            "status": "ok",
            "timestamp": datetime.now().isoformat(),
        }).encode("utf-8"))

    def _serve_models(self):
        """Serve the list of available models (OpenAI-compatible)."""
        self.send_response(200)
        _add_cors_headers(self)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        models_list = {
            "object": "list",
            "data": [
                {"id": "pipeline", "object": "model",
                 "created": int(datetime.now().timestamp()), "owned_by": "midway"},
            ],
        }
        self.wfile.write(json.dumps(models_list).encode("utf-8"))

    def _serve_index(self):
        """Serve a minimal HTML page with instructions."""
        self.send_response(200)
        _add_cors_headers(self)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"""<!DOCTYPE html>
<html><head><title>Midway Pipeline Stream</title></head><body>
<h1>Midway Pipeline Stream</h1>
<p>Use: <code>curl http://localhost:8765/stream?prompt=YOUR_PROMPT</code></p>
<p>Or POST: <code>curl -X POST http://localhost:8765/v1/chat/completions -H 'Content-Type: application/json' -d '{"model":"pipeline","messages":[{"role":"user","content":"hello"}]}'</code></p>
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
            _add_cors_headers(self)
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
        _add_cors_headers(self)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")  # Nginx proxy support
        self.end_headers()

        # ── Iterate the generator, sending SSE events ──────────────
        try:
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
                    print("  [StreamServer] Client disconnected")
                    break
        except Exception as gen_exc:
            # ── Directive D: Shield against generator crashes ──────────
            # If the pipeline generator itself raises (e.g., Ollama socket
            # dropping and exhausting retries), send an error and [DONE]
            # instead of letting the client hang.
            print(f"  [StreamServer] Generator crashed: {gen_exc}")
            import traceback
            traceback.print_exc()
            try:
                err_sse = json.dumps({"type": "error",
                    "content": f"Pipeline generator crashed: {gen_exc}"})
                self.wfile.write(f"data: {err_sse}\n\n".encode("utf-8"))
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except Exception:
                pass

        print(f"  [StreamServer] Stream ended for '{prompt[:60]}...'")

    # ── POST route: OpenAI-compatible /v1/chat/completions ────────────

    def do_POST(self):
        """OpenAI-compatible POST /v1/chat/completions endpoint.

        Accepts the standard OpenAI chat completions request format:
        {
            "model": "pipeline",
            "messages": [{"role": "user", "content": "..."}],
            "stream": true
        }
        Returns SSE-streamed response tokens.

        ✅ FIX: outer try/except catches ALL unexpected exceptions and
              ensures the response always terminates properly so Continue
              never hangs on "generating" forever.
        """
        parsed = urlparse(self.path)

        if parsed.path not in ("/v1/chat/completions",):
            self.send_response(404)
            _add_cors_headers(self)
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
            _add_cors_headers(self)
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
            _add_cors_headers(self)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "No user message found"}).encode("utf-8"))
            return

        stream_mode = req.get("stream", True)
        model = req.get("model", "pipeline")

        print(f"  [OpenAI POST] /v1/chat/completions — prompt='{prompt[:60]}...' stream={stream_mode}")

        # ── Generate stable completion identity ──────────────────────
        raw_id = hashlib.md5((prompt + str(time.time())).encode()).hexdigest()[:12]
        completion_id = f"chatcmpl-{raw_id}"
        created_ts = int(time.time())

        if stream_mode:
            # ── SSE-stream the response ─────────────────────────────
            # ✅ FIX: The outer try/except catches any exception from
            #       the entire streaming process (including generator
            #       crashes) and ensures [DONE] is ALWAYS sent.
            try:
                self.send_response(200)
                _add_cors_headers(self)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()

                # ── Initial Role Chunk ──────────────────────────────
                role_chunk = {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "created": created_ts,
                    "model": model,
                    "choices": [{"delta": {"role": "assistant"}, "index": 0, "finish_reason": None}],
                }
                self.wfile.write(f"data: {json.dumps(role_chunk)}\n\n".encode("utf-8"))
                self.wfile.flush()

                try:
                    for event_type, data in stream_pipeline_generator(prompt, None, None):
                        try:
                            if event_type == "token":
                                chunk = {
                                    "id": completion_id,
                                    "object": "chat.completion.chunk",
                                    "created": created_ts,
                                    "model": model,
                                    "choices": [{"delta": {"content": data}, "index": 0, "finish_reason": None}],
                                }
                            elif event_type == "announce":
                                chunk = {
                                    "id": completion_id,
                                    "object": "chat.completion.chunk",
                                    "created": created_ts,
                                    "model": model,
                                    "choices": [{"delta": {"content": f"\n{data}\n"}, "index": 0, "finish_reason": None}],
                                }
                            elif event_type == "done":
                                # Final chunk with finish_reason="stop"
                                chunk = {
                                    "id": completion_id,
                                    "object": "chat.completion.chunk",
                                    "created": created_ts,
                                    "model": model,
                                    "choices": [{"delta": {}, "index": 0, "finish_reason": "stop"}],
                                }
                                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode("utf-8"))
                                self.wfile.flush()
                                continue
                            elif event_type == "error":
                                chunk = {
                                    "id": completion_id,
                                    "object": "chat.completion.chunk",
                                    "created": created_ts,
                                    "model": model,
                                    "choices": [{"delta": {"content": f"\n[ERROR: {data}]\n"}, "index": 0, "finish_reason": "stop"}],
                                }
                            elif event_type == "close":
                                break
                            elif event_type == "ping":
                                self.wfile.write(": ping\n\n".encode("utf-8"))
                                self.wfile.flush()
                                continue
                            else:
                                continue

                            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode("utf-8"))
                            self.wfile.flush()
                        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                            print("  [OpenAI POST] Client disconnected")
                            return
                except Exception as e:
                    # Generator raised an exception — log it, send stop chunk
                    print(f"  [OpenAI POST] Generator exception: {e}")
                    import traceback
                    traceback.print_exc()
                    try:
                        err_chunk = {
                            "id": completion_id,
                            "object": "chat.completion.chunk",
                            "created": created_ts,
                            "model": model,
                            "choices": [{"delta": {}, "index": 0, "finish_reason": "stop"}],
                        }
                        self.wfile.write(f"data: {json.dumps(err_chunk)}\n\n".encode("utf-8"))
                        self.wfile.flush()
                    except Exception:
                        pass
                finally:
                    # ✅ CRITICAL: ALWAYS send [DONE], even after exceptions.
                    # Without this, Continue hangs on "generating" forever.
                    try:
                        self.wfile.write(b"data: [DONE]\n\n")
                        self.wfile.flush()
                    except Exception:
                        pass

            except Exception as e:
                # Outer shield — catches anything that goes wrong before
                # or during header sending
                print(f"  [OpenAI POST] UNEXPECTED ERROR: {e}")
                import traceback
                traceback.print_exc()
                try:
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                except Exception:
                    pass
        else:
            # Non-streaming: collect full response and return as JSON
            try:
                from pipeline import run_pipeline
                result = run_pipeline(prompt)
                response_body = {
                    "id": completion_id,
                    "object": "chat.completion",
                    "created": created_ts,
                    "model": model,
                    "choices": [{
                        "index": 0,
                        "message": {"role": "assistant", "content": result},
                        "finish_reason": "stop",
                    }],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                }
                self.send_response(200)
                _add_cors_headers(self)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(response_body).encode("utf-8"))
            except Exception as e:
                print(f"  [OpenAI POST] Non-stream error: {e}")
                self.send_response(500)
                _add_cors_headers(self)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "error": {"message": str(e), "type": "pipeline_error"}
                }).encode("utf-8"))


# ═══════════════════════════════════════════════════════════════════════════

def create_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
    """Create and return a ThreadingHTTPServer with the StreamHandler.

    ✅ FIX: Uses ThreadingHTTPServer instead of HTTPServer so each
          request runs in its own thread. This prevents a single
          long-running pipeline request from blocking the server.
    """
    server = ThreadingHTTPServer((host, port), StreamHandler)
    return server


def run_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
    """Run the streaming HTTP server (blocking call)."""
    server = create_server(host, port)
    print(f"\n{'='*60}")
    print(f"  Midway Pipeline Stream Server (Generator-Based)")
    print(f"  Listening on http://{host}:{port}")
    print(f"  SSE endpoint: http://{host}:{port}/stream?prompt=...")
    print(f"  OpenAI endpoint: POST http://{host}:{port}/v1/chat/completions")
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
