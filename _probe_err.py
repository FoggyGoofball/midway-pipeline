#!/usr/bin/env python3
"""Probe the two failing models and print the server error body."""
import json
import urllib.request
import urllib.error

HOST = "http://192.168.0.16:11434"
CASES = [("phi3:14b", 32768), ("phi3.5:latest", 131072)]

for m, c in CASES:
    print(f"==== {m} @ {c} ====")
    body = json.dumps({
        "model": m,
        "messages": [{"role": "user", "content": "hi"}],
        "stream": False,
        "keep_alive": "0",
        "options": {"num_ctx": c, "num_predict": 8},
    }).encode()
    req = urllib.request.Request(
        f"{HOST}/api/chat", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        r = urllib.request.urlopen(req, timeout=180)
        print("OK:", r.read().decode()[:600])
    except urllib.error.HTTPError as e:
        print("HTTP", e.code, "->", e.read().decode()[:800])
    except Exception as e:
        print(type(e).__name__, str(e)[:600])
