#!/usr/bin/env python3
"""Isolate the phi3:14b runner crash."""
import json
import urllib.request
import urllib.error

HOST = "http://192.168.0.16:11434"


def chat(model: str, options: dict | None, label: str) -> str:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "say ok"}],
        "stream": False,
        "keep_alive": "0",
        "options": options or {},
    }
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{HOST}/api/chat", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=240) as r:
            j = json.loads(r.read().decode())
        return f"OK ({j.get('eval_count', 0)} tok)"
    except urllib.error.HTTPError as e:
        return f"FAIL: {e.read().decode()[:160]}"
    except Exception as e:
        return f"ERR: {type(e).__name__} {str(e)[:160]}"


print("default (no options): ", chat("phi3:14b", {}, "default"), flush=True)
print("num_ctx=4096:          ", chat("phi3:14b", {"num_ctx": 4096, "num_predict": 4}, "4096"), flush=True)
print("num_ctx=8192 q8_0 KV:  ", chat("phi3:14b", {"num_ctx": 8192, "num_predict": 4, "kv_cache_type": "q8_0"}, "8192q8"), flush=True)
