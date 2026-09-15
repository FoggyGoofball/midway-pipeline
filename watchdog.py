#!/usr/bin/env python3
"""
watchdog.py — deterministic pipeline degradation watcher.

Runs as a daemon thread INSIDE the pipeline stream server (started from
pipeline_stream_server.run_server) and polls the shared in-memory
server_status store every few seconds.  When it sees an obvious degradation
signature it pushes a notification to the phone via ntfy.py.

It is 100% rule-based: it never loads a model and never touches Ollama, so it
runs freely in tandem with the reasoning/coding models (zero VRAM cost).

Signals (each rate-limited so it can't spam the phone):
  - Extreme TTFT           ttft > MIDWAY_WATCHDOG_TTFT            (default 100s)
  - TPS collapse           effective_tps < MIDWAY_WATCHDOG_MIN_EFFECTIVE_TPS
                                                                    (default 0.5)
  - VRAM overrun           "VRAM_OVERRUN" / abort guard in the recent log
  - Stall                  running but no phase/log progress for
                           MIDWAY_WATCHDOG_STALL_MINUTES            (default 20m)
  - Error burst            >=3 error lines in the recent log tail
  - New pipeline error     server_status.last_error changed

Environment:
  MIDWAY_NTFY_TOPIC            required for phone alerts (see ntfy.py)
  MIDWAY_WATCHDOG_INTERVAL     seconds between checks       (default 30)
  MIDWAY_WATCHDOG_COOLDOWN     seconds between repeats of one kind (default 900)
  MIDWAY_WATCHDOG_TTFT         TTFT threshold in seconds    (default 100)
  MIDWAY_WATCHDOG_MIN_EFFECTIVE_TPS  collapse threshold     (default 0.5)
  MIDWAY_WATCHDOG_STALL_MINUTES     stall threshold         (default 20)
"""

from __future__ import annotations

import os
import sys
import threading
import time

try:
    import server_status
except Exception:  # pragma: no cover - server_status is always present in-repo
    server_status = None

try:
    import ntfy
except Exception:  # pragma: no cover - ntfy is always present in-repo
    ntfy = None

INTERVAL = float(os.environ.get("MIDWAY_WATCHDOG_INTERVAL", "30"))
COOLDOWN = float(os.environ.get("MIDWAY_WATCHDOG_COOLDOWN", "900"))
TTFT_THRESHOLD = float(os.environ.get("MIDWAY_WATCHDOG_TTFT", "100"))
MIN_EFFECTIVE_TPS = float(os.environ.get("MIDWAY_WATCHDOG_MIN_EFFECTIVE_TPS", "0.5"))
STALL_MINUTES = float(os.environ.get("MIDWAY_WATCHDOG_STALL_MINUTES", "20"))

_ERROR_MARKERS = ("ERROR", "[Pipeline Error]", "Traceback", "⛔", "❌", "✗")
_VRAM_MARKERS = ("VRAM_OVERRUN", "VRAM Abort Guard", "VRAM overrun")


class _State:
    def __init__(self) -> None:
        self.primed = False
        self.last_tel_ts = None
        self.last_progress = None
        self.last_progress_time = time.time()
        self.last_error = None
        self.notified = {}  # kind -> epoch seconds


_state = _State()
_started = False
_lock = threading.Lock()


def _alert(kind: str, title: str, message: str, tags: str = "", priority: str = "3") -> None:
    last = _state.notified.get(kind, 0.0)
    if time.time() - last < COOLDOWN:
        return
    _state.notified[kind] = time.time()
    # Always surface the flag in the dashboard console, even without ntfy.
    print(f"  [Watchdog] ⚠ {title} — {message}", flush=True)
    if ntfy is not None and ntfy.configured():
        ntfy.notify(title, message, priority=priority, tags=tags)


def _scan_logs(logs):
    err_count = 0
    vram = False
    for line in logs or []:
        if any(m in line for m in _ERROR_MARKERS):
            err_count += 1
        if not vram and any(m in line for m in _VRAM_MARKERS):
            vram = True
    return err_count, vram


def _tick() -> None:
    if server_status is None:
        return
    try:
        snap = server_status.snapshot()
    except Exception:
        return

    logs = snap.get("logs") or []
    running = bool(snap.get("running"))
    tel = snap.get("last_telemetry")
    last_error = snap.get("last_error")
    sig = (snap.get("phase"), snap.get("detail"), len(logs), logs[-1] if logs else "")

    # First tick only establishes a baseline so pre-existing state doesn't
    # immediately fire every alert on startup.
    if not _state.primed:
        _state.last_tel_ts = tel.get("ts") if tel else None
        _state.last_error = last_error
        _state.last_progress = sig
        _state.last_progress_time = time.time()
        _state.primed = True
        return

    # -- telemetry flags (only evaluate fresh telemetry) ---------------------
    if tel and tel.get("ts") != _state.last_tel_ts:
        _state.last_tel_ts = tel.get("ts")
        ttft = tel.get("ttft")
        if isinstance(ttft, (int, float)) and ttft > TTFT_THRESHOLD:
            _alert("ttft_extreme", "⚠ Midway: extreme TTFT",
                   f"TTFT {ttft:.1f}s on '{tel.get('label', '?')}' ({tel.get('model', '?')})",
                   tags="warning", priority="4")
        eff = tel.get("effective_tps")
        if isinstance(eff, (int, float)) and running and eff < MIN_EFFECTIVE_TPS:
            _alert("tps_collapse", "🚨 Midway: TPS collapse",
                   f"Effective {eff:.1f} tok/s on '{tel.get('label', '?')}' — possible VRAM thrash",
                   tags="rotating_light", priority="4")

    # -- log-scan flags (VRAM overrun + error burst) -------------------------
    err_count, vram = _scan_logs(logs)
    if vram:
        _alert("vram_overrun", "⛔ Midway: VRAM overrun",
               "Pipeline aborted during task execution (VRAM_OVERRUN).",
               tags="skull", priority="5")
    if err_count >= 3:
        _alert("error_burst", "❌ Midway: error burst",
               f"{err_count} error lines in the recent log.",
               tags="x", priority="4")

    # -- new pipeline error ---------------------------------------------------
    if last_error and last_error != _state.last_error:
        _state.last_error = last_error
        _alert("error", "❌ Midway: pipeline error", last_error[:400],
               tags="x", priority="4")
    elif not last_error:
        _state.last_error = None

    # -- stall detection (running but no progress) ---------------------------
    if sig != _state.last_progress:
        _state.last_progress = sig
        _state.last_progress_time = time.time()
    if running and (time.time() - _state.last_progress_time) > STALL_MINUTES * 60:
        _alert("stall", "⏳ Midway: no progress",
               f"No phase/log change for {int(STALL_MINUTES)}m in phase '{snap.get('phase')}'.",
               tags="hourglass", priority="3")
        _state.last_progress_time = time.time()  # don't re-alert until progress resumes


def _loop() -> None:
    time.sleep(5)  # let the server finish booting before the first baseline tick
    while True:
        try:
            _tick()
        except Exception:  # noqa: BLE001 - a watchdog must never die
            pass
        time.sleep(INTERVAL)


def start() -> None:
    """Start the watchdog as a daemon thread.  Idempotent — safe to call
    multiple times (e.g. from run_server on every restart)."""
    global _started
    with _lock:
        if _started:
            return
        _started = True
    if ntfy is not None and ntfy.configured():
        print(f"  [Watchdog] started (ntfy -> {ntfy.SERVER}/{ntfy.TOPIC}, "
              f"interval {INTERVAL:.0f}s)", flush=True)
    else:
        print("  [Watchdog] started (ntfy NOT configured — set MIDWAY_NTFY_TOPIC "
              "to get phone alerts)", flush=True)
    threading.Thread(target=_loop, daemon=True, name="midway-watchdog").start()


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["test"]:
        if ntfy is None or not ntfy.configured():
            print("  [Watchdog] MIDWAY_NTFY_TOPIC is not set — cannot send a test.")
            return 1
        err = ntfy.notify("🧪 Midway watchdog test",
                          "Watchdog + ntfy are wired up correctly.",
                          priority="3", tags="tada", blocking=True)
        if err:
            print(f"  [Watchdog] test failed: {err}")
            return 1
        print("  [Watchdog] test notification sent — check your phone.")
        return 0

    print("  [Watchdog] running in foreground (Ctrl+C to stop).")
    print("             NOTE: standalone mode can only see state from within THIS")
    print("             process — normally the server starts it as a thread.")
    start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
