#!/usr/bin/env python3
"""Benchmark every Deck model at its assigned context window.

Measures:
  - cold TTFT (wall-clock request -> first token; includes model load/eviction)
  - generation speed (eval_count / eval_duration, load excluded)
Run sequentially; each model is evicted after its run (keep_alive=0).
"""
import json
import sys
import time
import urllib.request

HOST = "http://192.168.0.16:11434"

# model -> num_ctx (mirrors ollama_config.resolve_ctx_size)
MODELS = [
    ("qwen2.5-coder:7b", 32768),
    ("qwen2.5-coder:1.5b", 32768),
    ("phi3:14b", 32768),
    ("qwen3.5:9b", 65536),
    ("llama3.1:8b-instruct-q4_K_M", 65536),
    ("phi3.5:latest", 131072),
    ("llama3.2:1b", 131072),
]

PROMPT = (
    "Explain step by step how a pachinko machine works: the ball drop, "
    "the pins, the scoring pockets, and player strategy. Keep it clear."
)
NUM_PREDICT = 256


def bench(model: str, num_ctx: int) -> dict:
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": PROMPT}],
        "stream": True,
        "keep_alive": "0",
        "options": {"num_ctx": num_ctx, "num_predict": NUM_PREDICT},
    }).encode()
    req = urllib.request.Request(
        f"{HOST}/api/chat", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    t0 = time.time()
    first_ts = None
    eval_count = 0
    eval_duration = 0
    done = False
    try:
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
                    done = True
                    break
    except Exception as e:
        return {"model": model, "ctx": num_ctx, "error": str(e)}
    ttft = (first_ts - t0) if first_ts else 0.0
    gen_tps = (eval_count / (eval_duration / 1e9)) if eval_duration else 0.0
    return {
        "model": model, "ctx": num_ctx, "done": done,
        "ttft": ttft, "eval_count": eval_count, "gen_tps": gen_tps,
    }


def main() -> None:
    print(f"{'model':30} {'ctx':>7} {'ttft(s)':>8} {'tok':>5} {'gen_tok/s':>9}")
    print("-" * 66)
    for model, ctx in MODELS:
        r = bench(model, ctx)
        if "error" in r:
            print(f"{model:30} {ctx:>7}  ERROR: {r['error']}")
        else:
            print(
                f"{r['model']:30} {r['ctx']:>7} {r['ttft']:>8.1f} "
                f"{r['eval_count']:>5} {r['gen_tps']:>9.1f}"
            )
        sys.stdout.flush()


if __name__ == "__main__":
    main()
