#!/usr/bin/env python3
"""
Midway Pipeline — Generator-Based Streaming HTTP Server
=========================================================
Serves the generator-based pipeline as an SSE (Server-Sent Events)
HTTP endpoint at /stream, plus an OpenAI-compatible /v1/chat/completions
endpoint so that Continue can connect natively.

Built on http.server (stdlib) — no external dependencies.
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

# Enforce local directory import precedence
LOCAL_DIR = Path(__file__).resolve().parent
if str(LOCAL_DIR) in sys.path:
    sys.path.remove(str(LOCAL_DIR))
sys.path.insert(0, str(LOCAL_DIR))

PROJECT_ROOT = Path(os.getenv("MIDWAY_PROJECT_ROOT", LOCAL_DIR.with_name("midway")))
if str(PROJECT_ROOT) in sys.path:
    sys.path.remove(str(PROJECT_ROOT))
sys.path.append(str(PROJECT_ROOT))

from pipeline_stream import stream_pipeline_generator
from pipeline_snapshot import SnapshotManager
import time

# ── Defaults ────────────────────────────────────────────────────────────────

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8765

class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """HTTP server handling concurrent requests in separate threads."""
    daemon_threads = True
    allow_reuse_address = True
    block_on_close = False

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Requested-With",
    "Access-Control-Max-Age": "86400",
}

def _add_cors_headers(handler):
    for key, value in CORS_HEADERS.items():
        handler.send_header(key, value)


class StreamHandler(BaseHTTPRequestHandler):
    """HTTP handler serving generator-based pipeline output as SSE."""

    def log_message(self, format, *args):
        pass

    def do_OPTIONS(self):
        self.send_response(204)
        _add_cors_headers(self)
        self.end_headers()

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
        self.send_response(200)
        _add_cors_headers(self)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({
            "status": "ok",
            "timestamp": datetime.now().isoformat(),
        }).encode("utf-8"))

    def _serve_models(self):
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
        self.send_response(200)
        _add_cors_headers(self)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"""<!DOCTYPE html>
<html><head><title>Midway Pipeline Stream</title></head><body>
<h1>Midway Pipeline Stream</h1>
<p>Use: <code>curl http://localhost:8765/stream?prompt=YOUR_PROMPT</code></p>
</body></html>""")

    def _serve_stream(self, params: dict):
        prompt = params.get("prompt", [""])[0]
        if not prompt:
            self.send_response(400)
            _add_cors_headers(self)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Bad Request: missing 'prompt' parameter")
            return

        checkpoint_id = params.get("checkpoint", [None])[0] or None
        session_id = params.get("session_id", [None])[0] or None

        print(f"  [StreamServer] Starting stream for '{prompt[:60]}...'")

        self.send_response(200)
        _add_cors_headers(self)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        try:
            for event_type, data in stream_pipeline_generator(prompt, checkpoint_id, session_id):
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
                        # Explicitly log error trace to terminal console
                        print(f"  [Pipeline Error] {data}", flush=True)
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
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError) as conn_err:
                    print(f"  [StreamServer] Client disconnected: {conn_err}")
                    print("  [StreamServer] CRITICAL: Stream socket dropped -- "
                          "truncated data will NOT be forwarded to downstream reviewers.")
                    try:
                        snap = SnapshotManager()
                        snap.revert_all()
                        print("  [StreamServer] Rolled back to pre-task snapshot")
                    except Exception as snap_err:
                        print(f"  [StreamServer] Snapshot rollback failed: {snap_err}")
                    break
        except Exception as gen_exc:
            print(f"  [StreamServer] Generator crashed: {gen_exc}")
            import traceback
            traceback.print_exc()
            try:
                err_sse = json.dumps({"type": "error", "content": f"Pipeline crashed: {gen_exc}"})
                self.wfile.write(f"data: {err_sse}\n\n".encode("utf-8"))
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except Exception:
                pass

        print(f"  [StreamServer] Stream ended for '{prompt[:60]}...'")

    # ── POST route: OpenAI-compatible /v1/chat/completions ────────────

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path not in ("/v1/chat/completions",):
            self.send_response(404)
            _add_cors_headers(self)
            self.end_headers()
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        try:
            req = json.loads(body.decode("utf-8"))
        except Exception:
            self.send_response(400)
            _add_cors_headers(self)
            self.end_headers()
            return

        messages = req.get("messages", [])
        prompt = ""
        for msg in messages:
            if msg.get("role") in ("user", "system"):
                if msg.get("content", ""):
                    prompt = msg.get("content")

        if not prompt:
            self.send_response(400)
            _add_cors_headers(self)
            self.end_headers()
            return

        stream_mode = req.get("stream", True)
        model = req.get("model", "pipeline")

        print(f"  [OpenAI POST] /v1/chat/completions — prompt='{prompt[:60]}...' stream={stream_mode}")

        raw_id = hashlib.md5((prompt + str(time.time())).encode()).hexdigest()[:12]
        completion_id = f"chatcmpl-{raw_id}"
        created_ts = int(time.time())

        if stream_mode:
            try:
                self.send_response(200)
                _add_cors_headers(self)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()

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
                                # Log error trace explicitly to the terminal console
                                print(f"  [Pipeline Error] {data}", flush=True)
                                chunk = {
                                    "id": completion_id,
                                    "object": "chat.completion.chunk",
                                    "created": created_ts,
                                    "model": model,
                                    "choices": [{"delta": {"content": f"\n[ERROR: {data}]\n"}, "index": 0, "finish_reason": "stop"}],
                                }
                            elif event_type == "ping":
                                # Upgrade keep-alive ping: Send harmless empty delta chunks to guarantee active JSON parser reset
                                ping_chunk = {
                                    "id": completion_id,
                                    "object": "chat.completion.chunk",
                                    "created": created_ts,
                                    "model": model,
                                    "choices": [{"delta": {}, "index": 0, "finish_reason": None}],
                                }
                                self.wfile.write(f"data: {json.dumps(ping_chunk)}\n\n".encode("utf-8"))
                                self.wfile.flush()
                                continue
                            elif event_type == "close":
                                break
                            else:
                                continue

                            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode("utf-8"))
                            self.wfile.flush()
                        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError) as conn_err:
                            print(f"  [OpenAI POST] Client disconnected: {conn_err}")
                            print("  [StreamServer] CRITICAL: Stream socket dropped -- possible "
                                  "payload overflow or thermal timeout. Truncated data will NOT "
                                  "be forwarded to Integration Review.")
                            try:
                                snap = SnapshotManager()
                                snap.revert_all()
                                print("  [StreamServer] Rolled back to pre-task snapshot")
                            except Exception as snap_err:
                                print(f"  [StreamServer] Snapshot rollback failed: {snap_err}")
                            return
                except Exception as e:
                    print(f"  [OpenAI POST] Generator exception: {e}")
                    import traceback
                    traceback.print_exc()
                finally:
                    try:
                        self.wfile.write(b"data: [DONE]\n\n")
                        self.wfile.flush()
                    except Exception:
                        pass
            except Exception as e:
                print(f"  [OpenAI POST] UNEXPECTED ERROR: {e}")
        else:
            try:
                from pipeline import run_pipeline
                result = run_pipeline(prompt)
                res = {
                    "id": completion_id, "object": "chat.completion",
                    "created": created_ts, "model": model,
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": result}, "finish_reason": "stop"}],
                }
                self.send_response(200)
                _add_cors_headers(self)
                self.end_headers()
                self.wfile.write(json.dumps(res).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                _add_cors_headers(self)
                self.end_headers()


def create_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
    return ThreadingHTTPServer((host, port), StreamHandler)

def run_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
    server = create_server(host, port)
    print(f"\n{'='*60}")
    print(f"  Midway Pipeline Stream Server (Hardened)")
    print(f"  Listening on http://{host}:{port}")
    print(f"{'='*60}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()

if __name__ == "__main__":
    run_server()