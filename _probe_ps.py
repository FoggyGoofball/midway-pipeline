#!/usr/bin/env python3
"""Load phi3:14b at 4K and read its real memory footprint from /api/ps."""
import json
import time
import urllib.request
import urllib.error

HOST = "http://192.168.0.16:11434"


def call(path: str, data: dict | None = None) -> dict:
    req = urllib.request.Request(
        f"{HOST}{path}",
        data=json.dumps(data).encode() if data else None,
        headers={"Content-Type": "application/json"},
        method="POST" if data else "GET",
    )
    with urllib.request.urlopen(req, timeout=240) as r:
        return json.loads(r.read().decode())


# tags: on-disk size
tags = call("/api/tags")
for m in tags.get("models", []):
    n = m.get("name")
    if n in ("phi3:14b", "phi3.5:latest", "qwen3.5:9b"):
        print(f"{n:18} disk={m.get('size', 0) / 1e9:.2f} GB "
              f"quant={m.get('details', {}).get('quantization_level')}")

# load phi3:14b at 4K, keep resident, read ps
call("/api/chat", {
    "model": "phi3:14b",
    "messages": [{"role": "user", "content": "ok"}],
    "stream": False,
    "keep_alive": "5m",
    "options": {"num_ctx": 4096, "num_predict": 4},
})
time.sleep(2)
ps = call("/api/ps")
print(json.dumps(ps, indent=2))
