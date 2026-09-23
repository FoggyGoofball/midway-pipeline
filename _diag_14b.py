"""Diagnose why qwen2.5-coder:14b-instruct-q4_K_M returns 500 on the Deck.

Tries several configs and prints the FULL error body on failure:
  1. default (GPU auto)
  2. num_gpu=0  (CPU-only) -> if this loads, the 500 is VRAM OOM
"""
import json
import urllib.request
import urllib.error

OLLAMA = "http://192.168.0.16:11434"
MODEL = "qwen2.5-coder:14b-instruct-q4_K_M"


def try_generate(label, options):
    payload = json.dumps({
        "model": MODEL, "prompt": "hi", "stream": False, "options": options,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA}/api/generate", data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    print(f"=== {label} ===")
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            body = resp.read().decode("utf-8", "replace")
            print("OK:", body[:160])
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            pass
        print(f"HTTP {e.code}; body={body[:400] if body else '(empty)'}")
    except Exception as e:
        print(f"EXC: {type(e).__name__}: {e}")


try_generate("default (gpu auto), ctx=512", {"num_ctx": 512, "num_predict": 4})
try_generate("num_gpu=0 (CPU only), ctx=512", {"num_ctx": 512, "num_predict": 4, "num_gpu": 0})
try_generate("num_gpu=0, mmap on, ctx=256", {"num_ctx": 256, "num_predict": 4, "num_gpu": 0})
