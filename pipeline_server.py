#!/usr/bin/env python3
"""
Midway Pipeline — HTTP API Server
==================================
Wraps the pipeline as a callable REST API for Continue or any other tool.
Runs as a background server, accepts POST requests, streams results back.

Usage:
    python pipeline_server.py              # starts on http://localhost:8765
    python pipeline_server.py --port 9999  # custom port

Continue config (config.ts):
    models:
      - title: "Midway Pipeline"
        provider: "openai"  # compatible with any OpenAI-chat provider
        apiBase: "http://localhost:8765/v1"
        apiKey: "not-needed"
        model: "pipeline"
"""

import json
import sys
import os
import re
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

# Import the pipeline orchestrator
from pipeline import (
    run_pipeline,
    list_checkpoints,
    load_checkpoint,
    save_checkpoint,
    CHECKPOINT_DIR,
    ALL_DOMAINS,
    get_project_state,
    get_available_domains_text,
    get_unavailable_domains_text,
    build_director_prompt,
    OLLAMA_HOST,
    MODEL,
    DIRECTOR_MODEL,
    MAX_ITERATIONS,
    PROJECT_ROOT,
)

# ── Configuration ──────────────────────────────────────────────────────────
SERVER_PORT = 8765


# ── HTTP Server ────────────────────────────────────────────────────────────

class PipelineHandler(BaseHTTPRequestHandler):
    """HTTP handler that exposes the pipeline as an OpenAI-compatible chat API."""

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path in ("/v1/models", "/models"):
            self._send_json({
                "object": "list",
                "data": [{
                    "id": "pipeline",
                    "object": "model",
                    "created": int(datetime.now().timestamp()),
                    "owned_by": "midway",
                }]
            })

        elif path in ("/health", ""):
            self._send_json({
                "status": "ok",
                "pipeline": "Midway Pipeline Orchestrator",
                "ollama": OLLAMA_HOST,
                "model": MODEL,
                "director_model": DIRECTOR_MODEL,
                "max_iterations": MAX_ITERATIONS,
                "available_domains": [
                    key for key, domain in ALL_DOMAINS.items() if domain["ready"]
                ],
                "unavailable_domains": [
                    key for key, domain in ALL_DOMAINS.items() if not domain["ready"]
                ],
            })

        elif path == "/checkpoints":
            """List all saved checkpoints."""
            checkpoints = list_checkpoints()
            self._send_json({
                "checkpoints": [
                    {
                        "id": c.get("checkpoint_id"),
                        "phase": c.get("phase"),
                        "timestamp": c.get("timestamp"),
                    }
                    for c in checkpoints
                ],
                "count": len(checkpoints),
            })

        elif path.startswith("/checkpoints/"):
            """Get details for a specific checkpoint."""
            parts = path.split("/")
            if len(parts) >= 3:
                ckpt_id = parts[2]
                ckpt = load_checkpoint(ckpt_id)
                if ckpt:
                    self._send_json(ckpt)
                else:
                    self._send_error(404, f"Checkpoint '{ckpt_id}' not found")
            else:
                self._send_error(400, "Missing checkpoint ID")

        elif path == "/project-state":
            """Get the current project state summary."""
            self._send_json({
                "project_state": get_project_state(),
                "available_domains": get_available_domains_text(),
                "unavailable_domains": get_unavailable_domains_text(),
            })

        elif path == "/director-prompt":
            """Get the dynamically built Director prompt."""
            self._send_json({
                "director_prompt": build_director_prompt(),
            })

        else:
            self._send_error(404, "Not found")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        # OpenAI-compatible chat completions endpoint
        if path in ("/v1/chat/completions", "/chat/completions"):
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            try:
                req_data = json.loads(body)
            except json.JSONDecodeError:
                self._send_error(400, "Invalid JSON")
                return

            # Extract the user prompt from the last user message
            messages = req_data.get("messages", [])
            user_prompt = ""
            for msg in reversed(messages):
                if msg.get("role") == "user":
                    user_prompt = msg.get("content", "")
                    break

            if not user_prompt:
                self._send_error(400, "No user message found")
                return

            # Check for checkpoint_id in the request
            checkpoint_id = req_data.get("checkpoint_id", None)

            # Run the pipeline (returns a markdown string)
            result_text = run_pipeline(user_prompt, checkpoint_id)

            # Build response in OpenAI chat format
            response_text = (
                f"## Pipeline Results\n\n"
                f"{result_text}"
            )


            self._send_json({
                "id": "pipeline-" + datetime.now().strftime("%Y%m%d%H%M%S"),
                "object": "chat.completion",
                "created": int(datetime.now().timestamp()),
                "model": "pipeline",
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": response_text,
                    },
                    "finish_reason": "stop",
                }],
                "usage": {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                },
            })

        elif path.startswith("/checkpoints/"):
            """Restore from a checkpoint or save a new one."""
            parts = path.split("/")
            if len(parts) >= 4 and parts[3] == "restore":
                ckpt_id = parts[2]
                ckpt = load_checkpoint(ckpt_id)
                if ckpt:
                    self._send_json({
                        "status": "restored",
                        "checkpoint_id": ckpt_id,
                        "phase": ckpt.get("phase"),
                        "timestamp": ckpt.get("timestamp"),
                    })
                else:
                    self._send_error(404, f"Checkpoint '{ckpt_id}' not found")
            else:
                self._send_error(400, "Invalid checkpoint action. Use /checkpoints/<id>/restore")

        else:
            self._send_error(404, "Not found")

    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, indent=2).encode("utf-8"))

    def _send_error(self, status, message):
        self._send_json({"error": {"message": message, "type": "error"}}, status)

    def log_message(self, format, *args):
        sys.stderr.write(f"[PipelineServer] {args[0]} {args[1]} {args[2]}\n")


def main():
    port = SERVER_PORT
    if "--port" in sys.argv:
        idx = sys.argv.index("--port")
        if idx + 1 < len(sys.argv):
            port = int(sys.argv[idx + 1])

    server = HTTPServer(("0.0.0.0", port), PipelineHandler)
    print(f"Midway Pipeline API Server running on http://localhost:{port}")
    print(f"OpenAI-compatible endpoint: http://localhost:{port}/v1/chat/completions")
    print(f"Model list: http://localhost:{port}/v1/models")
    print(f"Health check: http://localhost:{port}/health")
    print(f"Checkpoints: http://localhost:{port}/checkpoints")
    print(f"Project state: http://localhost:{port}/project-state")
    print(f"Director prompt: http://localhost:{port}/director-prompt")
    print(f"\nAvailable domains: {[k for k, v in ALL_DOMAINS.items() if v['ready']]}")
    print(f"Unavailable domains: {[k for k, v in ALL_DOMAINS.items() if not v['ready']]}")
    print(f"\nTo use in Continue, add to config.ts:")
    print(f"""  {{
    title: "Midway Pipeline",
    provider: "openai",
    model: "pipeline",
    apiBase: "http://localhost:{port}/v1",
    apiKey: "not-needed",
  }}""")
    print(f"\nPress Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.server_close()


if __name__ == "__main__":
    main()
