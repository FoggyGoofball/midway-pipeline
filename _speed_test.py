#!/usr/bin/env python3
"""Quick generation-speed benchmark for an Ollama model (excludes TTFT)."""
import json
import time
import urllib.request

HOST = "http://192.168.0.16:11434"
MODEL = "qwen2.5-coder:7b"
PROMPT = (
    "Write a detailed explanation of how a pachinko machine works, including "
    "the physics of the falling balls, the scoring zones, and player strategy. "
    "Make it at least 150 words."
)


def main() -> None:
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": PROMPT}],
        "stream": True,
        "keep_alive": "10m",
        "options": {"num_ctx": 32768, "num_predict": 512},
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{HOST}/api/chat", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    t0 = time.time()
    first_ts = None
    eval_count = 0
    eval_duration = 0
    done = False
    with urllib.request.urlopen(req, timeout=600) as resp:
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
                done = True
                break
    ttft = (first_ts - t0) if first_ts else 0.0
    gen_tps = (eval_count / (eval_duration / 1e9)) if eval_duration else 0.0
    print(f"model={MODEL}")
    print(f"eval_count={eval_count} tokens  done={done}")
    print(f"ttft={ttft:.1f}s")
    print(f"generation={gen_tps:.1f} tok/s")


if __name__ == "__main__":
    main()
