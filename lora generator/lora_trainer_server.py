#!/usr/bin/env python3
"""
lora_trainer_server.py
======================
LoRA Training HTTP Server — runs on the Steam Deck (inference server).

Provides a lightweight HTTP API to trigger LoRA fine-tuning remotely,
stream training logs as SSE (Server-Sent Events), and download the
trained adapter weights.

Usage
-----
  # On the Steam Deck:
  nohup python /path/to/lora_trainer_server.py &

  # Then from any machine on the LAN:
  curl -N http://192.168.0.16:8766/lora/train
  curl -N http://192.168.0.16:8766/lora/train?epochs=2&lr=1e-5
  curl http://192.168.0.16:8766/lora/status

Endpoints
---------
  POST /lora/train     — Start training (streams SSE logs back)
  GET  /lora/status    — Current training state as JSON
  GET  /lora/download  — Download trained adapter as zip
  POST /lora/stop      — Gracefully stop a running training
  GET  /health         — Health check
"""

import json
import os
import signal
import subprocess
import sys
import time
import threading
import zipfile
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from io import BytesIO
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Optional
from urllib.parse import urlparse, parse_qs

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
DATASET_PATH = SCRIPT_DIR / "paging_lora_dataset.jsonl"
TRAIN_SCRIPT = SCRIPT_DIR / "lora_fine_tune.py"
OUTPUT_DIR = SCRIPT_DIR / "lora_output"

# Defaults — overridable via query params on /lora/train
DEFAULT_EPOCHS = 3
DEFAULT_LR = 2e-5
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8766

# ---------------------------------------------------------------------------
# Global training state
# ---------------------------------------------------------------------------
_training_process: Optional[subprocess.Popen] = None
_training_log: list[str] = []
_training_lock = threading.Lock()
_training_start_time: Optional[float] = None
_training_status = {"running": False, "epoch": 0, "loss": 0.0, "eta_sec": 0}


def _log_line(line: str) -> None:
    """Capture a training log line with timestamp."""
    ts = datetime.now().strftime("%H:%M:%S")
    decorated = f"[{ts}] {line}"
    _training_log.append(decorated)
    print(decorated, flush=True)


def _parse_training_progress(line: str) -> Optional[dict]:
    """Try to extract epoch/loss info from an SFTTrainer log line.

    The SFTTrainer outputs lines like:
      {'loss': 0.8234, 'grad_norm': 0.12, 'learning_rate': 2e-05, 'epoch': 0.33}
    """
    try:
        if "{" in line and "loss" in line and "epoch" in line:
            # Extract JSON-like dict from line
            start = line.index("{")
            end = line.rindex("}") + 1
            data = json.loads(line[start:end])
            return {
                "loss": data.get("loss", 0.0),
                "epoch": data.get("epoch", 1.0),
                "learning_rate": data.get("learning_rate", 0.0),
            }
    except (ValueError, json.JSONDecodeError):
        pass
    return None


def _run_training(epochs: int, learning_rate: float, log_callback) -> int:
    """Launch the training script as a subprocess and stream logs.

    Args:
        epochs: Number of training epochs.
        learning_rate: Peak learning rate.
        log_callback: Callable(str) called for each log line.

    Returns:
        Exit code of the training subprocess.
    """
    global _training_start_time, _training_status

    if not DATASET_PATH.exists():
        _log_line(f"ERROR: Dataset not found at {DATASET_PATH}")
        log_callback(f"ERROR: Dataset missing at {DATASET_PATH}")
        return 1
    if not TRAIN_SCRIPT.exists():
        _log_line(f"ERROR: Training script not found at {TRAIN_SCRIPT}")
        log_callback(f"ERROR: Training script missing at {TRAIN_SCRIPT}")
        return 1

    _log_line(f"Starting training: epochs={epochs}, lr={learning_rate}")
    _log_line(f"Dataset: {DATASET_PATH}")
    _log_line(f"Output:  {OUTPUT_DIR}")

    env = os.environ.copy()
    env["LORA_OVERRIDE_EPOCHS"] = str(epochs)
    env["LORA_OVERRIDE_LR"] = str(learning_rate)

    _training_start_time = time.time()

    proc = subprocess.Popen(
        [sys.executable, str(TRAIN_SCRIPT)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        text=True,
        bufsize=1,
    )

    # --- Stream stdout line by line ---
    last_update = time.time()
    for raw_line in proc.stdout:  # type: ignore[union-attr]
        line = raw_line.rstrip()
        _log_line(line)
        log_callback(line)

        # Try to parse progress
        progress = _parse_training_progress(line)
        if progress:
            _training_status["epoch"] = round(progress["epoch"], 2)
            _training_status["loss"] = round(progress["loss"], 4)
            # Estimate ETA
            if _training_start_time:
                elapsed = time.time() - _training_start_time
                if progress["epoch"] > 0:
                    eta_per_epoch = elapsed / progress["epoch"]
                    remaining_epochs = epochs - progress["epoch"]
                    _training_status["eta_sec"] = int(eta_per_epoch * remaining_epochs)
            last_update = time.time()

    proc.wait()
    exit_code = proc.returncode
    _log_line(f"Training subprocess exited with code {exit_code}")
    log_callback(f"[EXIT CODE: {exit_code}]")

    # --- Check output ---
    if exit_code == 0 and OUTPUT_DIR.exists():
        adapter_file = OUTPUT_DIR / "adapter_model.safetensors"
        if adapter_file.exists():
            size_mb = adapter_file.stat().st_size / (1024 * 1024)
            _log_line(f"Adapter saved: {adapter_file} ({size_mb:.1f} MB)")
            _training_status["running"] = False
            _training_status["adapter_size_mb"] = round(size_mb, 1)
        else:
            _log_line("WARNING: adapter_model.safetensors not found in output dir")
    else:
        _log_line(f"Training failed or output directory not created.")

    return exit_code


# ---------------------------------------------------------------------------
# HTTP Handler
# ---------------------------------------------------------------------------

class LoRATrainingHandler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        pass  # suppress default request logging

    # -- CORS ----------------------------------------------------------------
    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    # -- GET routes -----------------------------------------------------------
    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if parsed.path == "/health":
            self._serve_health()
        elif parsed.path == "/lora/status":
            self._serve_status()
        elif parsed.path == "/lora/download":
            self._serve_download()
        elif parsed.path == "/lora/log":
            self._serve_log(params)
        elif parsed.path == "/" or parsed.path == "":
            self._serve_index()
        else:
            self.send_response(404)
            self._cors_headers()
            self.end_headers()
            self.wfile.write(b"Not Found")

    def _serve_health(self):
        self.send_response(200)
        self._cors_headers()
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        data = {
            "status": "ok",
            "service": "lora-trainer",
            "dataset_exists": DATASET_PATH.exists(),
            "training_available": not _training_status["running"],
            "timestamp": datetime.now().isoformat(),
        }
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def _serve_status(self):
        self.send_response(200)
        self._cors_headers()
        self.send_header("Content-Type", "application/json")
        self.end_headers()

        status = dict(_training_status)
        status["log_lines"] = len(_training_log)
        if _training_start_time and _training_status["running"]:
            status["elapsed_sec"] = int(time.time() - _training_start_time)
        # Estimate total time from last trained epoch
        if status.get("epoch", 0) > 0 and _training_start_time:
            elapsed = time.time() - _training_start_time
            status["total_estimate_sec"] = int(
                elapsed / status["epoch"] * DEFAULT_EPOCHS
            )
        status["log_tail"] = _training_log[-10:]  # last 10 lines

        self.wfile.write(json.dumps(status).encode("utf-8"))

    def _serve_download(self):
        """Zip and stream the trained adapter directory."""
        if not OUTPUT_DIR.exists():
            self.send_response(404)
            self._cors_headers()
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"No trained adapter found. Run /lora/train first.")
            return

        self.send_response(200)
        self._cors_headers()
        self.send_header("Content-Type", "application/zip")
        self.send_header(
            "Content-Disposition",
            f'attachment; filename="lora_output_{datetime.now().strftime("%Y%m%d_%H%M%S")}.zip"',
        )
        self.end_headers()

        buf = BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(OUTPUT_DIR):
                for fn in files:
                    fpath = Path(root) / fn
                    arcname = str(fpath.relative_to(OUTPUT_DIR))
                    zf.write(fpath, arcname)
        buf.seek(0)
        self.wfile.write(buf.read())

    def _serve_log(self, params):
        """Return full training log as text."""
        self.send_response(200)
        self._cors_headers()
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        lines = params.get("lines", [None])[0]
        if lines is not None:
            try:
                n = int(lines)
                self.wfile.write("\n".join(_training_log[-n:]).encode("utf-8"))
            except ValueError:
                self.wfile.write("\n".join(_training_log).encode("utf-8"))
        else:
            self.wfile.write("\n".join(_training_log).encode("utf-8"))

    def _serve_index(self):
        self.send_response(200)
        self._cors_headers()
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        html = (
            "<!DOCTYPE html><html><head><title>LoRA Trainer Server</title></head><body>"
            "<h1>LoRA Trainer Server</h1>"
            "<p>Endpoints:</p>"
            "<ul>"
            "<li><code>POST /lora/train</code> &mdash; Start training (SSE stream)</li>"
            "<li><code>GET /lora/status</code> &mdash; Training state as JSON</li>"
            "<li><code>GET /lora/download</code> &mdash; Download adapter as zip</li>"
            "<li><code>GET /lora/log</code> &mdash; View training log</li>"
            "<li><code>GET /health</code> &mdash; Health check</li>"
            "</ul>"
            "<p>Example:</p>"
            "<pre>curl -N http://192.168.0.16:8766/lora/train</pre>"
            "</body></html>"
        )
        self.wfile.write(html.encode("utf-8"))

    # -- POST routes ----------------------------------------------------------
    def do_POST(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if parsed.path == "/lora/train":
            self._serve_train(params)
        elif parsed.path == "/lora/stop":
            self._serve_stop()
        else:
            self.send_response(404)
            self._cors_headers()
            self.end_headers()
            self.wfile.write(b"Not Found")

    def _serve_stop(self):
        global _training_process, _training_status
        with _training_lock:
            if _training_process and _training_process.poll() is None:
                _training_process.terminate()
                _log_line("Training process terminated by /lora/stop request.")
                _training_status["running"] = False
                self.send_response(200)
                self._cors_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "stopped"}).encode("utf-8"))
            else:
                self.send_response(200)
                self._cors_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "no_running_process"}).encode("utf-8"))

    def _serve_train(self, params: dict):
        """Start training in a background thread, stream logs via SSE."""
        global _training_process, _training_status

        with _training_lock:
            if _training_status["running"]:
                self.send_response(409)
                self._cors_headers()
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"Training already in progress. Check /lora/status.")
                return

            # Parse optional parameters
            epochs = DEFAULT_EPOCHS
            lr = DEFAULT_LR
            try:
                if "epochs" in params:
                    epochs = int(params["epochs"][0])
                if "lr" in params:
                    lr = float(params["lr"][0])
            except (ValueError, IndexError):
                pass

            _training_status = {"running": True, "epoch": 0, "loss": 0.0, "eta_sec": 0}
            _training_log.clear()

        # -- SSE response headers --
        self.send_response(200)
        self._cors_headers()
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        # We'll collect log lines into a queue for the SSE thread
        log_queue = []
        log_lock = threading.Lock()

        def log_callback(line: str):
            with log_lock:
                log_queue.append(line)

        # Start training in a background thread
        def _train_thread():
            global _training_process, _training_status
            try:
                from lora_fine_tune import main as train_main
                # We CAN'T easily call main() directly because it uses
                # subprocess-level imports. Instead we run via subprocess
                # to get clean stdout isolation.
                pass
            except ImportError:
                pass

            # Get env overrides from query params
            env = os.environ.copy()
            env["LORA_OVERRIDE_EPOCHS"] = str(epochs)
            env["LORA_OVERRIDE_LR"] = str(lr)

            proc = subprocess.Popen(
                [sys.executable, str(TRAIN_SCRIPT)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
                text=True,
                bufsize=1,
            )
            with _training_lock:
                _training_process = proc

            for raw_line in proc.stdout:  # type: ignore[union-attr]
                line = raw_line.rstrip()
                _log_line(line)
                log_callback(line)

                # Parse progress
                progress = _parse_training_progress(line)
                if progress:
                    _training_status["epoch"] = round(progress["epoch"], 2)
                    _training_status["loss"] = round(progress["loss"], 4)
                    if _training_start_time and progress["epoch"] > 0:
                        elapsed = time.time() - _training_start_time
                        eta_per = elapsed / progress["epoch"]
                        remaining = epochs - progress["epoch"]
                        _training_status["eta_sec"] = int(eta_per * max(0, remaining))

            proc.wait()
            _training_status["running"] = False
            log_callback(f"[TRAINING COMPLETE — exit code {proc.returncode}]")

            # Check adapter output
            adapter_file = OUTPUT_DIR / "adapter_model.safetensors"
            if adapter_file.exists():
                size_mb = adapter_file.stat().st_size / (1024 * 1024)
                _training_status["adapter_size_mb"] = round(size_mb, 1)

        thread = threading.Thread(target=_train_thread, daemon=True)
        thread.start()

        # SSE stream loop: drain log queue and push events
        try:
            while thread.is_alive() or log_queue:
                # Drain all available log lines
                while log_queue:
                    with log_lock:
                        line = log_queue.pop(0)
                    # Send as SSE
                    event = json.dumps({"type": "log", "line": line})
                    try:
                        self.wfile.write(f"data: {event}\n\n".encode("utf-8"))
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        # Client disconnected
                        return

                    # If it's a progress line, also send a status event
                    progress = _parse_training_progress(line)
                    if progress:
                        status_event = json.dumps({
                            "type": "progress",
                            "loss": progress["loss"],
                            "epoch": round(progress["epoch"], 2),
                            "learning_rate": progress["learning_rate"],
                        })
                        try:
                            self.wfile.write(f"data: {status_event}\n\n".encode("utf-8"))
                            self.wfile.flush()
                        except (BrokenPipeError, ConnectionResetError):
                            return

                time.sleep(0.1)

            # Send final done event
            final = json.dumps({
                "type": "done",
                "exit_code": _training_process.returncode if _training_process else -1,
                "adapter_path": str(OUTPUT_DIR),
            })
            try:
                self.wfile.write(f"data: {final}\n\n".encode("utf-8"))
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

        except Exception as e:
            _log_line(f"SSE stream error: {e}")
            try:
                err = json.dumps({"type": "error", "message": str(e)})
                self.wfile.write(f"data: {err}\n\n".encode("utf-8"))
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except Exception:
                pass


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """HTTP server handling concurrent requests in separate threads."""
    daemon_threads = True
    allow_reuse_address = True
    block_on_close = False


def create_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), LoRATrainingHandler)


def run_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
    server = create_server(host, port)
    print(f"\n{'='*60}")
    print(f"  LoRA Training Server")
    print(f"  Listening on http://{host}:{port}")
    print(f"{'='*60}")
    print(f"  Dataset: {DATASET_PATH}")
    print(f"  Output:  {OUTPUT_DIR}")
    print(f"  Script:  {TRAIN_SCRIPT}")
    print(f"{'='*60}")
    print(f"  Start training:  curl -N http://{host}:{port}/lora/train")
    print(f"  Check status:    curl http://{host}:{port}/lora/status")
    print(f"  Download result: curl -O http://{host}:{port}/lora/download")
    print(f"{'='*60}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        global _training_process
        if _training_process and _training_process.poll() is None:
            _training_process.terminate()
        server.shutdown()


if __name__ == "__main__":
    # Parse optional --port and --host from command line
    port = DEFAULT_PORT
    host = DEFAULT_HOST
    for i, arg in enumerate(sys.argv[1:]):
        if arg == "--port" and i + 2 < len(sys.argv):
            try:
                port = int(sys.argv[i + 2])
            except (ValueError, IndexError):
                pass
        if arg == "--host" and i + 2 < len(sys.argv):
            host = sys.argv[i + 2]
    run_server(host, port)
