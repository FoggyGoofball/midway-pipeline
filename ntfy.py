#!/usr/bin/env python3
"""
ntfy.py — one-shot push notifications to ntfy.sh (or a self-hosted ntfy server).

Used by watchdog.py so the Pixel phone gets a native notification the moment
the pipeline degrades.  Purely an HTTP POST — no app to build, no model, no
VRAM.

Configuration (environment variables):
  MIDWAY_NTFY_TOPIC     required — the topic to publish to, e.g. "midway-abc123"
  MIDWAY_NTFY_SERVER    optional — default https://ntfy.sh (use your own host
                        if you self-host ntfy)

Usage:
  python ntfy.py test                     # send a test notification
  python ntfy.py "your custom message"    # send an arbitrary message

Sends happen on a daemon thread with a hard timeout and a direct (no-proxy)
connection, so a slow or unreachable ntfy server can never wedge the pipeline
(same pattern as server_status.probe_ollama).
"""

from __future__ import annotations

import json
import os
import sys
import threading
import urllib.request

SERVER = os.environ.get("MIDWAY_NTFY_SERVER", "https://ntfy.sh").rstrip("/")
# Default topic baked in so notifications work regardless of how the server is
# launched (bat, dashboard Start button via Vite, or plain `python ...`).
# MIDWAY_NTFY_TOPIC still overrides it.  Treat this as a password — if the repo
# is ever made public, change it and set MIDWAY_NTFY_TOPIC to the new value.
TOPIC = os.environ.get("MIDWAY_NTFY_TOPIC", "midway-f4a5ec27").strip()


def configured() -> bool:
    """True when a topic is set (i.e. notifications can actually be sent)."""
    return bool(TOPIC)


def fetch_messages(since: str = "", timeout: float = 5.0) -> list:
    """Poll the ntfy JSON feed for messages newer than *since* (a message ID
    or Unix timestamp).  Returns a list of message dicts.  Never raises — a
    network failure just yields an empty list so the command listener can keep
    polling forever."""
    if not TOPIC:
        return []
    url = f"{SERVER}/{TOPIC}/json"
    if since:
        url += f"?since={since}"
    try:
        req = urllib.request.Request(url, method="GET")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _send_sync(title: str, message: str, priority: str, tags: str) -> str:
    url = f"{SERVER}/{TOPIC}"
    data = message.encode("utf-8")
    headers = {
        "Title": title[:250],
        "Priority": str(priority),
    }
    if tags:
        headers["Tags"] = tags
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    # ProxyHandler({}) forces a DIRECT connection — a configured system proxy
    # could otherwise hang the request indefinitely.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=5.0) as resp:
        return resp.read().decode("utf-8", "replace")


def notify(title: str, message: str = "", priority: str = "3",
           tags: str = "", blocking: bool = False):
    """Send a notification.  Non-blocking by default: the HTTP call runs on a
    daemon thread.  When *blocking* is True, waits up to ~6s and returns None
    on success or an error string on failure."""
    if not TOPIC:
        return "MIDWAY_NTFY_TOPIC is not set"
    result = {}

    def _work() -> None:
        try:
            _send_sync(title, message, priority, tags)
        except Exception as e:  # noqa: BLE001 - the notifier must never raise
            result["error"] = str(e)

    t = threading.Thread(target=_work, daemon=True)
    t.start()
    if blocking:
        t.join(timeout=6.0)
        if result.get("error"):
            return result["error"]
    return None


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not TOPIC:
        print("MIDWAY_NTFY_TOPIC is not set. Set it first, e.g. (PowerShell):")
        print("  set MIDWAY_NTFY_TOPIC=midway-YourSecretTopic")
        print("then run:  python ntfy.py test")
        return 1

    if argv and argv[0].lower() == "test":
        title = "Midway pipeline test"
        message = "Test notification — ntfy is wired up correctly."
        tags = "tada"
    else:
        title = "Midway pipeline"
        message = " ".join(argv).strip() or "Notification from the Midway pipeline"
        tags = ""

    err = notify(title, message, priority="3", tags=tags, blocking=True)
    if err:
        print(f"  [ntfy] send failed: {err}")
        return 1
    print(f"  [ntfy] sent -> {SERVER}/{TOPIC}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
