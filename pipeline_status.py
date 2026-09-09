#!/usr/bin/env python3
"""
pipeline_status.py — Liveness probe for the Midway pipeline.

Answers one question: "is a generation ACTIVE right now, or has the pipeline
stalled?"

How it works
------------
Ollama's /api/ps endpoint reports each loaded model with an `expires_at`
timestamp.  While a request is actively streaming, Ollama pins that timestamp
to "now + keep_alive" so it ADVANCES in lockstep with the wall clock.  Once
generation stops, `expires_at` freezes and the gap to "now" shrinks.

So we sample /api/ps twice, a few seconds apart, and compare drift:
    expiry_delta ~= wall_delta  ->  ACTIVE  (generating)
    expiry_delta ~= 0           ->  IDLE    (loaded, but no request in flight)

The probe also checks:
  * whether Ollama is reachable at all, and
  * whether the local stream server port is listening.

Usage:
  python pipeline_status.py               # two samples, 5s apart, then verdict
  python pipeline_status.py --interval 8  # longer sampling window
  python pipeline_status.py --once        # single snapshot (no drift check)
  python pipeline_status.py --watch       # repeat every interval until Ctrl+C

Exit codes:
  0  ACTIVE    — Ollama reachable and a generation is in flight.
  1  OFFLINE   — Ollama is unreachable (pipeline cannot call the LLM).
  2  STALLED   — a model is loaded but no generation is active.
  3  IDLE      — Ollama reachable but no model loaded (between phases).
  4  ERROR     — unexpected failure.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

DEFAULT_HOST = os.environ.get("OLLAMA_HOST", "http://192.168.0.16:11434")
STREAM_SERVER_PORT = 8765


def _get_json(url: str, timeout: float = 10.0):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _parse_ts(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def snapshot(host: str) -> dict:
    """Return {model_name: expires_at(datetime)} for currently loaded models."""
    data = _get_json(f"{host}/api/ps")
    out: dict = {}
    for m in data.get("models", []):
        name = m.get("name", "?")
        exp = m.get("expires_at")
        out[name] = _parse_ts(exp) if exp else None
    return out


def port_listening(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def fmt_ttl(exp: datetime | None, now: datetime) -> str:
    if exp is None:
        return "?"
    secs = int((exp - now).total_seconds())
    return "expired" if secs < 0 else f"{secs}s"


def run_check(args) -> int:
    # 1. Local stream server
    local_up = port_listening("127.0.0.1", args.server_port)
    print(f"[Local]  Stream server on :{args.server_port}: "
          f"{'LISTENING' if local_up else 'NOT LISTENING'}")

    # 2. Ollama reachability
    try:
        ver = _get_json(f"{args.host}/api/version")
        print(f"[Ollama] Reachable at {args.host}  (v{ver.get('version', '?')})")
    except Exception as e:
        print(f"[Ollama] UNREACHABLE at {args.host}: {e}")
        print("VERDICT: OFFLINE — the pipeline cannot call the LLM.")
        return 1

    if args.once:
        snap = snapshot(args.host)
        if not snap:
            print("VERDICT: IDLE — no models loaded (between phases or not started).")
            return 3
        now = datetime.now(timezone.utc)
        for name, exp in snap.items():
            print(f"  loaded: {name}  (unloads in ~{fmt_ttl(exp, now)})")
        print("VERDICT: snapshot taken — run without --once for ACTIVE/STALLED detection.")
        return 0

    # 3. Two samples, compare expiry drift.
    t0 = datetime.now(timezone.utc)
    s0 = snapshot(args.host)
    time.sleep(args.interval)
    t1 = datetime.now(timezone.utc)
    s1 = snapshot(args.host)

    wall_delta = (t1 - t0).total_seconds()
    print(f"\n[Sample] {args.interval:.0f}s apart; wall-clock delta = {wall_delta:.1f}s")

    if not s1:
        print("VERDICT: IDLE — no models loaded right now (between phases, or "
              "stalled before any LLM call).")
        return 3

    any_active = False
    now = datetime.now(timezone.utc)
    for name, exp1 in s1.items():
        exp0 = s0.get(name)
        ttl = fmt_ttl(exp1, now)
        if exp0 is not None and exp1 is not None:
            expiry_delta = (exp1 - exp0).total_seconds()
            ratio = expiry_delta / wall_delta if wall_delta else 0.0
            active = ratio >= 0.5
            any_active = any_active or active
            state = "ACTIVE (generating)" if active else "IDLE (loaded, no request)"
            print(f"  {name}: {state}  | expiry drift {expiry_delta:.1f}s / "
                  f"{wall_delta:.1f}s | unloads in ~{ttl}")
        elif name not in s0:
            # Appeared during the sampling window -> just got loaded/used.
            any_active = True
            print(f"  {name}: just loaded during window (recent activity) | unloads in ~{ttl}")
        else:
            print(f"  {name}: just evicted during window | was loaded, now gone")

    if any_active:
        print("VERDICT: ACTIVE — generation is in flight. The pipeline is running.")
        return 0
    print("VERDICT: STALLED — a model is loaded but no generation is active.")
    return 2


def _enable_ansi() -> None:
    """Enable ANSI escape sequences on Windows consoles (clear-screen redraw)."""
    if os.name == "nt":
        try:
            os.system("")
        except Exception:
            pass


def _watch_status(args) -> tuple[str, list[str]]:
    """One drift-checked sample -> (status, detail_lines)."""
    lines: list[str] = []
    local_up = port_listening("127.0.0.1", args.server_port)
    lines.append(f"Stream server :{args.server_port}: {'LISTENING' if local_up else 'NOT LISTENING'}")

    try:
        ver = _get_json(f"{args.host}/api/version")
        lines.append(f"Ollama: reachable (v{ver.get('version', '?')})")
    except Exception as e:
        lines.append(f"Ollama: UNREACHABLE — {e}")
        return "OFFLINE", lines

    t0 = datetime.now(timezone.utc)
    s0 = snapshot(args.host)
    time.sleep(args.interval)
    t1 = datetime.now(timezone.utc)
    s1 = snapshot(args.host)
    wall_delta = (t1 - t0).total_seconds()

    if not s1:
        lines.append("Models: (none loaded)")
        return "IDLE", lines

    any_active = False
    now = datetime.now(timezone.utc)
    for name, exp1 in s1.items():
        exp0 = s0.get(name)
        ttl = fmt_ttl(exp1, now)
        if exp0 is not None and exp1 is not None:
            expiry_delta = (exp1 - exp0).total_seconds()
            ratio = expiry_delta / wall_delta if wall_delta else 0.0
            active = ratio >= 0.5
            any_active = any_active or active
            state = "ACTIVE" if active else "IDLE (loaded, no request)"
            lines.append(f"{name}: {state} — drift {expiry_delta:.1f}s/{wall_delta:.1f}s — unloads in ~{ttl}")
        elif name not in s0:
            any_active = True
            lines.append(f"{name}: just loaded — unloads in ~{ttl}")
        else:
            lines.append(f"{name}: just evicted")

    return ("ACTIVE" if any_active else "STALLED"), lines


def _draw_frame(now: str, status: str, lines: list[str], change_log: list[str]) -> None:
    buf: list[str] = []
    buf.append("\x1b[2J\x1b[H")  # clear screen + home cursor -> single live display
    buf.append("═" * 62)
    buf.append("  MIDWAY PIPELINE MONITOR  —  live (Ctrl+C to quit)")
    buf.append("═" * 62)
    buf.append("")
    buf.append(f"  STATUS: {status}    (updated {now})")
    buf.append("")
    for ln in lines:
        buf.append(f"    {ln}")
    buf.append("")
    buf.append("─" * 62)
    buf.append("  Change Log — status transitions only")
    buf.append("─" * 62)
    if change_log:
        for entry in change_log[-18:]:
            buf.append(f"    {entry}")
    else:
        buf.append("    (no transitions yet)")
    sys.stdout.write("\n".join(buf) + "\n")
    sys.stdout.flush()


def watch(args) -> int:
    """Live single-display monitor with a transition-only change log."""
    _enable_ansi()
    change_log: list[str] = []
    prev_status: str | None = None
    log_path = Path(__file__).resolve().parent / "pipeline_status.log"
    try:
        while True:
            status, lines = _watch_status(args)
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if status != prev_status:
                entry = f"{now}  {'START' if prev_status is None else prev_status} → {status}"
                change_log.append(entry)
                try:
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(entry + "\n")
                except Exception:
                    pass
                prev_status = status
            _draw_frame(now, status, lines, change_log)
            time.sleep(max(0.0, args.interval))
    except KeyboardInterrupt:
        sys.stdout.write("\nStopped. Change log written to " + str(log_path) + "\n")
        sys.stdout.flush()
        return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Midway pipeline liveness probe")
    ap.add_argument("--host", default=DEFAULT_HOST.rstrip("/"), help="Ollama base URL")
    ap.add_argument("--interval", type=float, default=5.0, help="seconds between samples")
    ap.add_argument("--once", action="store_true", help="single snapshot, no drift detection")
    ap.add_argument("--watch", action="store_true", help="live single-display monitor with a transition log")
    ap.add_argument("--server-port", type=int, default=STREAM_SERVER_PORT,
                    help="local stream server port (0 to skip local check)")
    args = ap.parse_args()

    if args.watch:
        return watch(args)
    return run_check(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(4)
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: {e}")
        sys.exit(4)
