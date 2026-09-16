#!/usr/bin/env python3
"""
server_status.py — Thread-safe live status store for the pipeline stream server.

Two consumers read from here:

  1. The HTTP server (``/api/status``, ``/api/ollama``, ``/api/logs``) to
     power the phone dashboard.
  2. ``pipeline_stream.py``'s worker thread, which updates the pipeline
     phase / telemetry as the run progresses.

The stream server also installs a stdout/stderr tee that feeds every printed
line into :func:`log_text`, so the dashboard can tail the exact console log
(sans ANSI colours).
"""

from __future__ import annotations

import json
import os
import re
import threading
import queue
import time
import urllib.error
import urllib.request
from collections import deque
from datetime import datetime, timezone
from typing import Deque, Optional

try:
    from term_color import strip_ansi
except Exception:  # pragma: no cover - term_color is always present in-repo
    def strip_ansi(text: str) -> str:
        return text

try:
    from ollama_config import OLLAMA_HOST
except Exception:  # pragma: no cover
    OLLAMA_HOST = "http://127.0.0.1:11434"


_LOG_MAX_LINES = 800
_LOG_MAX_LINE_CHARS = 2000

# A line already opening with its own [HH:MM:SS] timestamp (many pipeline
# prints do) should not get a second one prepended.
_TS_RE = re.compile(r'^\s*\[\d{2}:\d{2}:\d{2}\]')

_lock = threading.Lock()

_state = {
    "running": False,
    "phase": "idle",
    "detail": "",
    "prompt": "",
    "started_at": None,        # epoch seconds
    "finished_at": None,       # epoch seconds
    "current_model": "",
    "current_label": "",
    "current_task": "",        # e.g. "task_1"
    "last_telemetry": None,    # dict
    "last_error": None,        # str
    "run_count": 0,
    "stop_requested": False,
    "awaiting_input": False,
    "input_prompt": "",
}

_log_lines: Deque[str] = deque(maxlen=_LOG_MAX_LINES)
_log_pending: str = ""

# Response channel for interactive input() prompts: the pipeline worker thread
# blocks on get() while the dashboard submits the user's answer via /api/input.
_input_response_queue: "queue.Queue[str]" = queue.Queue()


# -- On-disk run log ---------------------------------------------------------
# A single fixed file that mirrors the console and is truncated at the start of
# each pipeline run.  Between runs it stays open in append mode so a previous
# run's log survives a server restart until the next run overwrites it.  It is
# flushed after every write so a mid-run crash still leaves the full output on
# disk for post-mortem analysis.
_LOG_FILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pipeline_run.log")
_log_file_handle = None


def _open_log_file(mode: str = "a") -> None:
    global _log_file_handle
    try:
        _log_file_handle = open(_LOG_FILE_PATH, mode, encoding="utf-8", errors="replace", buffering=1)
    except Exception:  # pragma: no cover - disk logging is best-effort
        _log_file_handle = None


def _write_log_file(text: str) -> None:
    if not text or _log_file_handle is None:
        return
    try:
        _log_file_handle.write(text)
        _log_file_handle.flush()
    except Exception:  # pragma: no cover
        pass


_open_log_file("a")  # capture server startup; set_running() truncates per run


# -- Pipeline state ----------------------------------------------------------

def set_running(prompt: str = "") -> None:
    with _lock:
        _open_log_file("w")  # overwrite the run log at the start of each run
        _state["running"] = True
        _state["prompt"] = prompt
        _state["started_at"] = time.time()
        _state["finished_at"] = None
        _state["phase"] = "init"
        _state["detail"] = f"Processing: {prompt[:60]}..." if prompt else ""
        _state["last_error"] = None
        _state["stop_requested"] = False
        _state["current_task"] = ""
        _state["awaiting_input"] = False
        _state["input_prompt"] = ""


def set_idle() -> None:
    with _lock:
        _state["running"] = False
        _state["finished_at"] = time.time()
        _state["phase"] = "complete"
        _state["detail"] = ""
        _state["stop_requested"] = False
        _state["current_task"] = ""


def set_phase(phase: str, status: str, detail: str = "") -> None:
    with _lock:
        _state["phase"] = phase
        _state["detail"] = detail
        if status == "started":
            _state["running"] = True
            if not _state["started_at"]:
                _state["started_at"] = time.time()
        elif status in ("done", "error"):
            _state["running"] = status != "done"


def set_current_call(model: str, label: str) -> None:
    with _lock:
        _state["current_model"] = model or ""
        _state["current_label"] = label or ""


def set_current_task(task_id: str) -> None:
    with _lock:
        _state["current_task"] = task_id or ""


def set_telemetry(telemetry: dict) -> None:
    with _lock:
        telemetry = dict(telemetry)
        telemetry["ts"] = datetime.now().isoformat()
        _state["last_telemetry"] = telemetry
        _state["current_model"] = telemetry.get("model", _state["current_model"])
        _state["current_label"] = telemetry.get("label", _state["current_label"])


def set_error(error: str) -> None:
    with _lock:
        _state["last_error"] = error[:2000]


def is_running() -> bool:
    with _lock:
        return bool(_state["running"])


def request_stop() -> None:
    """Flag the active run for cooperative stop (checked between tasks)."""
    with _lock:
        _state["stop_requested"] = True


def stop_requested() -> bool:
    """True once the user has asked the active run to stop."""
    with _lock:
        return bool(_state["stop_requested"])


def set_awaiting_input(prompt: str = "") -> None:
    """Mark the run as blocked on interactive input, exposing the prompt to the
    dashboard so the user can answer from the phone instead of the terminal."""
    with _lock:
        _state["awaiting_input"] = True
        _state["input_prompt"] = prompt or ""
    # Drain any stale response left from a previous prompt.
    try:
        while True:
            _input_response_queue.get_nowait()
    except queue.Empty:
        pass


def clear_awaiting_input() -> None:
    with _lock:
        _state["awaiting_input"] = False
        _state["input_prompt"] = ""


def is_awaiting_input() -> bool:
    with _lock:
        return bool(_state["awaiting_input"])


def get_input_prompt() -> str:
    with _lock:
        return _state["input_prompt"]


def submit_input_response(text: str) -> bool:
    """Deliver a dashboard response to the blocking input() call.

    Returns True if the pipeline was actually awaiting input (so a late or
    duplicate submission can be surfaced as such)."""
    with _lock:
        was_awaiting = bool(_state["awaiting_input"])
        _state["awaiting_input"] = False
        _state["input_prompt"] = ""
    if was_awaiting:
        _input_response_queue.put(text or "")
    return was_awaiting


def wait_for_input_response(timeout: Optional[float] = None) -> Optional[str]:
    """Block until the dashboard submits a response (or timeout, returning None)."""
    try:
        return _input_response_queue.get(timeout=timeout)
    except queue.Empty:
        return None


def bump_run_count() -> None:
    with _lock:
        _state["run_count"] += 1


# -- Console log tee ---------------------------------------------------------

def log_text(text: str) -> None:
    """Append console output *text* to the ring buffer (ANSI stripped)."""
    global _log_pending
    if not text:
        return
    clean = strip_ansi(text)
    _write_log_file(clean)  # mirror to disk (best-effort, outside the lock)
    with _lock:
        clean = _log_pending + clean
        while "\n" in clean:
            line, clean = clean.split("\n", 1)
            _append_line(line)
        _log_pending = clean


def _append_line(line: str) -> None:
    # Cap absurdly long streamed-token lines so a single generation doesn't
    # blow out the dashboard; the console still shows the full text.
    if len(line) > _LOG_MAX_LINE_CHARS:
        line = line[:_LOG_MAX_LINE_CHARS] + " …"
    line = line.rstrip("\r")
    # Prepend a real wall-clock timestamp so every dashboard log line is
    # attributable (telemetry lines otherwise carry none).
    if not _TS_RE.match(line):
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {line}"
    _log_lines.append(line)


def get_logs(n: int = 60) -> list:
    with _lock:
        tail = list(_log_lines)[-n:]
        if _log_pending:
            tail.append(_log_pending)
        return tail


def get_logfile(n: int = 0) -> list:
    """Read the on-disk run log and return its lines (last *n* when n > 0).

    The file is flushed after every write, so a separate read handle always
    sees the current contents — including everything up to a mid-run crash."""
    try:
        with open(_LOG_FILE_PATH, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except Exception:
        return []
    if n and n > 0:
        lines = lines[-n:]
    return lines


# -- Ollama probe ------------------------------------------------------------

def probe_ollama() -> dict:
    """Query Ollama /api/version + /api/ps. Never raises and NEVER blocks for
    more than ~4s, because it is called from an HTTP handler on every poll.

    The probe runs in a daemon thread and is joined with a hard timeout, so a
    half-dead Ollama host (Steam Deck asleep / busy) can't wedge the dashboard.
    A ProxyHandler({}) opener forces a DIRECT connection to the LAN host —
    otherwise a configured system proxy could hang the request indefinitely.
    """
    result = {
        "host": OLLAMA_HOST,
        "reachable": False,
        "version": None,
        "models": [],
        "error": None,
        "models_error": None,
    }
    collected: dict = {}

    def _work() -> None:
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(f"{OLLAMA_HOST}/api/version", timeout=2.0) as resp:
                ver = json.loads(resp.read().decode("utf-8"))
            collected["version"] = ver.get("version")
            collected["reachable"] = True
        except Exception as e:
            collected["error"] = _short_reason(e)
            return

        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(f"{OLLAMA_HOST}/api/ps", timeout=2.0) as resp:
                ps = json.loads(resp.read().decode("utf-8"))
            now = datetime.now(timezone.utc)
            models = []
            for m in ps.get("models", []):
                ttl_s = None
                exp = m.get("expires_at")
                if exp:
                    try:
                        dt = datetime.fromisoformat(exp)
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        ttl_s = int((dt - now).total_seconds())
                    except Exception:
                        pass
                models.append({
                    "name": m.get("name", "?"),
                    "size": m.get("size"),
                    "size_vram": m.get("size_vram"),
                    "ttl_s": ttl_s,
                })
            collected["models"] = models
        except Exception as e:
            collected["models_error"] = _short_reason(e)

    worker = threading.Thread(target=_work, daemon=True)
    worker.start()
    worker.join(timeout=4.0)
    if worker.is_alive():
        result["error"] = "probe timed out"
        return result
    result.update(collected)
    return result


def _short_reason(e: BaseException) -> str:
    try:
        if isinstance(e, urllib.error.HTTPError):
            return f"HTTP {e.code} {e.reason}"
        if isinstance(e, urllib.error.URLError):
            return f"URLError: {e.reason}"
    except Exception:
        pass
    return str(e)[:300]


# -- Snapshot for /api/status ------------------------------------------------

def snapshot(log_lines: int = 60) -> dict:
    """Return a JSON-serialisable view of the current state."""
    with _lock:
        started = _state["started_at"]
        elapsed = None
        if started:
            end = _state["finished_at"] or time.time()
            elapsed = round(max(0.0, end - started), 1)
        # Build the log tail INLINE — calling get_logs() here would re-acquire
        # _lock (a non-reentrant Lock) and deadlock the whole server.
        _tail = list(_log_lines)[-log_lines:]
        if _log_pending:
            _tail = _tail + [_log_pending]
        return {
            "running": _state["running"],
            "phase": _state["phase"],
            "detail": _state["detail"],
            "prompt": _state["prompt"],
            "started_at": datetime.fromtimestamp(started).isoformat() if started else None,
            "elapsed_s": elapsed,
            "current_model": _state["current_model"],
            "current_label": _state["current_label"],
            "current_task": _state["current_task"],
            "last_telemetry": _state["last_telemetry"],
            "last_error": _state["last_error"],
            "run_count": _state["run_count"],
            "stop_requested": _state["stop_requested"],
            "awaiting_input": _state["awaiting_input"],
            "input_prompt": _state["input_prompt"],
            "logs": _tail,
            "server_time": datetime.now().isoformat(),
        }
