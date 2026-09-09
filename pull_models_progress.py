#!/usr/bin/env python3
"""
pull_models_progress.py — Stream model pulls from the Steam Deck's Ollama with
live progress, so a human can monitor the transfers in the terminal.

Drives Ollama's /api/pull endpoint (stream=true) from THIS PC — no SSH needed.
Re-running is safe: Ollama resumes partial downloads and skips already-present
models.

Usage:
    python pull_models_progress.py
"""

from __future__ import annotations

import json
import sys
import urllib.request

HOST = "http://192.168.0.16:11434"

# The 5 models the Midway pipeline needs (qwen3.5:9b + qwen2.5-coder:7b are
# already present on the Deck).
MODELS = [
    "phi3:14b",
    "llama3.1:8b-instruct-q4_K_M",
    "phi3.5:latest",
    "qwen2.5-coder:1.5b",
    "llama3.2:1b",
]


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def pull_one(model: str) -> bool:
    print(f"\n=== pulling {model} ===", flush=True)
    payload = json.dumps({"model": model, "stream": True}).encode("utf-8")
    req = urllib.request.Request(
        f"{HOST}/api/pull",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    last = None
    try:
        with urllib.request.urlopen(req, timeout=None) as resp:
            for raw in resp:
                try:
                    obj = json.loads(raw)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                status = obj.get("status", "")
                total = obj.get("total")
                completed = obj.get("completed")
                if total and completed is not None:
                    pct = completed / total * 100.0
                    msg = f"  {model}: {human(completed)} / {human(total)}  ({pct:5.1f}%)"
                else:
                    msg = f"  {model}: {status}"
                if msg != last:
                    print(msg, flush=True)
                    last = msg
        print(f"  OK  {model}", flush=True)
        return True
    except Exception as e:  # noqa: BLE001 — report and continue to next model
        print(f"  FAILED {model}: {e}", flush=True)
        return False


def main() -> int:
    print("Monitoring Ollama pulls on " + HOST, flush=True)
    print("(watch this terminal — Ctrl+C aborts cleanly)", flush=True)
    results = {m: pull_one(m) for m in MODELS}
    print("\n================ SUMMARY ================", flush=True)
    for m, ok in results.items():
        print(f"  {'OK     ' if ok else 'FAILED '} {m}", flush=True)
    print("=========================================", flush=True)
    print("ALL_PULLS_DONE", flush=True)
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nAborted by user.", flush=True)
        sys.exit(130)
