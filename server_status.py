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
import threading
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
    "last_telemetry": None,    # dict
    "last_error": None,        # str
    "run_count": 0,
}

_log_lines: Deque[str] = deque(maxlen=_LOG_MAX_LINES)
_log_pending: str = ""


# -- Pipeline state ----------------------------------------------------------

def set_running(prompt: str = "") -> None:
    with _lock:
        _state["running"] = True
        _state["prompt"] = prompt
        _state["started_at"] = time.time()
        _state["finished_at"] = None
        _state["phase"] = "init"
        _state["detail"] = f"Processing: {prompt[:60]}..." if prompt else ""
        _state["last_error"] = None


def set_idle() -> None:
    with _lock:
        _state["running"] = False
        _state["finished_at"] = time.time()
        _state["phase"] = "complete"
        _state["detail"] = ""


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
    _log_lines.append(line.rstrip("\r"))


def get_logs(n: int = 60) -> list:
    with _lock:
        tail = list(_log_lines)[-n:]
        if _log_pending:
            tail.append(_log_pending)
        return tail


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
            "last_telemetry": _state["last_telemetry"],
            "last_error": _state["last_error"],
            "run_count": _state["run_count"],
            "logs": _tail,
            "server_time": datetime.now().isoformat(),
        }
