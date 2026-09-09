#!/usr/bin/env python3
"""Inspect each Deck model's native context + KV geometry, and compute the
max context that fits inside the 12 GB VRAM budget using the cost table."""
import json
import urllib.request

HOST = "http://192.168.0.16:11434"
MODELS = [
    "qwen2.5-coder:7b",
    "qwen3.5:9b",
    "phi3:14b",
    "phi3.5:latest",
    "llama3.1:8b-instruct-q4_K_M",
    "qwen2.5-coder:1.5b",
    "llama3.2:1b",
]

# cost table: base_GB@8K, per_K_GB_above_8K (from vram_budget._MODEL_COST_TABLE)
COST = {
    "qwen2.5-coder:1.5b": (1.3, 0.025),
    "qwen2.5-coder:7b":   (5.28, 0.055),
    "qwen3.5:9b":         (6.1, 0.075),
    "phi3:14b":           (8.22, 0.095),
    "phi3.5":             (2.36, 0.047),
    "llama3.1:8b":        (5.50, 0.063),
    "llama3.2:1b":        (1.2, 0.025),
    "qwen3.5:14b":        (8.5, 0.075),
}
BUDGET_GB = 12.0


def get(model: str) -> dict:
    body = json.dumps({"name": model}).encode()
    req = urllib.request.Request(
        f"{HOST}/api/show", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode())


def cost_key(model: str) -> tuple:
    lower = model.lower()
    for k in COST:
        if k in lower:
            return COST[k]
    return (0.0, 0.0)


print(f"{'model':28} {'native_ctx':>10} {'kv/tok(KB)':>11} {'vram_max':>9} {'effective':>9}")
print("-" * 74)
for m in MODELS:
    try:
        info = get(m).get("model_info", {})
    except Exception as e:
        print(f"{m:28} ERROR {e}")
        continue
    ctx = None
    for k, v in info.items():
        if k.endswith("context_length"):
            ctx = int(v)
    layers = info.get(next((k for k in info if k.endswith("block_count")), ""))
    kvh = info.get(next((k for k in info if k.endswith("head_count_kv")), ""))
    keylen = info.get(next((k for k in info if k.endswith("attention.key_length")), ""))
    layers = int(layers) if layers else 0
    kvh = int(kvh) if kvh else 0
    keylen = int(keylen) if keylen else 0
    # q8_0 KV = 1 byte/element, 2 (K+V) * layers * kv_heads * key_len
    kv_bytes = 2 * layers * kvh * keylen
    kv_kb = kv_bytes / 1024.0
    base, per_k = cost_key(m)
    if per_k > 0:
        vram_max = int((BUDGET_GB - base) / per_k * 1024.0) + 8192
    else:
        vram_max = 32768
    native = ctx if ctx else 0
    effective = min(native, vram_max) if native else vram_max
    print(f"{m:28} {native:>10} {kv_kb:>10.1f} {vram_max:>9} {effective:>9}")
