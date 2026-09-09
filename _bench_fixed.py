#!/usr/bin/env python3
"""Re-benchmark the two corrected models at their working ceilings."""
import json
import sys
import time
import urllib.request

HOST = "http://192.168.0.16:11434"
MODELS = [("phi3:14b", 4096), ("phi3.5:latest", 32768)]
PROMPT = (
    "Explain step by step how a pachinko machine works: the ball drop, "
    "the pins, the scoring pockets, and player strategy. Keep it clear."
)


def bench(model, num_ctx):
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": PROMPT}],
        "stream": True,
        "keep_alive": "0",
        "options": {"num_ctx": num_ctx, "num_predict": 256},
    }).encode()
    req = urllib.request.Request(
        f"{HOST}/api/chat", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    t0 = time.time()
    first_ts = None
    eval_count = 0
    eval_duration = 0
    with urllib.request.urlopen(req, timeout=1200) as resp:
        for raw in resp:
            try:
                obj = json.loads(raw)
            except Exception:
                continue
            if obj.get("message", {}).get("content", "") and first_ts is None:
                first_ts = time.time()
            if obj.get("done"):
                eval_count = obj.get("eval_count", 0)
                eval_duration = obj.get("eval_duration", 0)
                break
    ttft = (first_ts - t0) if first_ts else 0.0
    tps = (eval_count / (eval_duration / 1e9)) if eval_duration else 0.0
    print(f"{model:16} ctx={num_ctx:<6} ttft={ttft:5.1f}s  tok={eval_count:<4} gen={tps:5.1f} tok/s")
    sys.stdout.flush()


for m, c in MODELS:
    bench(m, c)
