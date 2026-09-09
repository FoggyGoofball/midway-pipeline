#!/usr/bin/env python3
"""Find the real working context ceiling for phi3:14b and phi3.5."""
import json
import urllib.request
import urllib.error

HOST = "http://192.168.0.16:11434"


def show(model: str) -> None:
    body = json.dumps({"name": model}).encode()
    req = urllib.request.Request(
        f"{HOST}/api/show", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        j = json.loads(r.read().decode())
    d = j.get("details", {})
    print(f"{model}: family={d.get('family')} params={d.get('parameter_size')} "
          f"quant={d.get('quantization_level')}")


def try_chat(model: str, ctx: int) -> str:
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "say ok"}],
        "stream": False,
        "keep_alive": "0",
        "options": {"num_ctx": ctx, "num_predict": 4},
    }).encode()
    req = urllib.request.Request(
        f"{HOST}/api/chat", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=240) as r:
            j = json.loads(r.read().decode())
        return f"OK ({j.get('eval_count', 0)} tok)"
    except urllib.error.HTTPError as e:
        return f"FAIL: {e.read().decode()[:120]}"
    except Exception as e:
        return f"ERR: {type(e).__name__} {str(e)[:120]}"


show("phi3:14b")
show("phi3.5:latest")
print()
for model, sizes in [
    ("phi3:14b", [8192, 16384, 24576]),
    ("phi3.5:latest", [16384, 32768]),
]:
    for s in sizes:
        print(f"{model:16} @ {s:>6}: {try_chat(model, s)}", flush=True)
