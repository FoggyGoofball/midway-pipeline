"""Probe: does FIM (fill-in-the-middle) infilling work on our coder models?

For a chosen anchor, split the strongman skeleton into a prefix (everything up
to the anchor marker line) and a suffix (everything after it), then ask the
model to fill the gap via Ollama /api/generate with `prompt`=prefix and
`suffix`=suffix.  This is the mode we have NEVER exercised on the coder models
— the past rejections were all conversational /api/chat behaviour.

Usage:
    python _probe_fim.py <model> [task_num] [--run]

Default is a DRY RUN: it only prints the request shape (no model load), so it
can be prepared while the Deck is busy with a live pipeline run.  Pass --run to
actually call Ollama (evicts whatever model is currently loaded).
"""
import json
import subprocess
import sys
import tempfile
import os
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
OLLAMA = "http://192.168.0.16:11434"

TASK_HINTS = {
    4: "-- Fill this gap with the object pool creation code: call MidwayPhysics.CreatePool('puck_pool', 4, 4, pool_params).",
    7: "-- Fill this gap with the per-frame modifier read: local MOD = AttractionConstants.modifiers, apply heat/luck/sleight_of_hand effects.",
    8: "-- Fill this gap with the scoring tick: read modifiers, then Engine.AwardTickets(n, label) scaled by Engine.GetStreak().",
    11: "-- Fill this gap with cleanup code: MidwayPhysics.DestroyBody(...) for each spawned handle, plus a diagnostic print.",
}

TASK_EXAMPLES = {
    4: (
        "-- EXAMPLE of a completed anchor fill (copy the shape, not the values):\n"
        "    local pool_params = { radius = 0.3, mass = 1.0, restitution = 0.5 }\n"
        "    MidwayPhysics.CreatePool('puck_pool', 4, 4, pool_params)"
    ),
    8: (
        "-- EXAMPLE of a completed anchor fill:\n"
        "    local streak = Engine.GetStreak()\n"
        "    Engine.AwardTickets(10 * (1 + streak * 0.5), 'Strongman Score')"
    ),
}


def _bridge_comment_block() -> str:
    """Render the approved MidwayPhysics/Engine API surface as Lua comments."""
    sys.path.insert(0, str(ROOT))
    from midway_api_signatures import SPAWN_ARITY, BODY_ARITY, ECONOMY_ARITY

    def _fmt(name, rng):
        lo, hi = rng
        if lo == hi:
            return f"{name} ({lo} args)"
        return f"{name} ({lo}-{hi} args)"

    lines = ["-- ── APPROVED MIDWAYPHYSICS SPAWN APIS ───────────────────────────"]
    for n, r in SPAWN_ARITY.items():
        lines.append(f"-- MidwayPhysics.{_fmt(n, r)}")
    lines.append("-- ── APPROVED MIDWAYPHYSICS BODY/POOL APIS ──────────────────────")
    for n, r in BODY_ARITY.items():
        lines.append(f"-- MidwayPhysics.{_fmt(n, r)}")
    lines.append("-- ── APPROVED ENGINE ECONOMY APIS ───────────────────────────────")
    for n, r in ECONOMY_ARITY.items():
        lines.append(f"-- Engine.{_fmt(n, r)}")
    return "\n".join(lines)


def _skeleton():
    sys.path.insert(0, str(ROOT))
    from _build_skeleton import build_skeleton
    return build_skeleton("strongman")


def split_at_anchor(skeleton: str, task_num: int):
    marker = f"-- [TASK_{task_num}_INSERT_HOOK]"
    lines = skeleton.splitlines()
    idx = next((i for i, ln in enumerate(lines) if marker in ln), None)
    if idx is None:
        raise SystemExit(f"anchor {marker!r} not found in skeleton")
    prefix = "\n".join(lines[:idx]) + "\n"
    suffix = "\n".join(lines[idx + 1:]) + "\n"
    return prefix, suffix


def run_fim(model: str, prefix: str, suffix: str, num_predict: int = 800, num_gpu=None) -> str:
    opts = {"temperature": 0.2, "num_predict": num_predict, "num_ctx": 8192}
    if num_gpu is not None:
        opts["num_gpu"] = num_gpu
    payload = json.dumps({
        "model": model,
        "prompt": prefix,
        "suffix": suffix,
        "stream": False,
        "options": opts,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA}/api/generate", data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=900) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("response", "")


def main():
    args = sys.argv[1:]
    model = next((a for a in args if not a.startswith("--") and not a.isdigit()), "qwen2.5-coder:7b")
    task = next((int(a) for a in args if a.isdigit()), 4)
    do_run = "--run" in args
    num_gpu = 0 if "--cpu" in args else None

    prefix, suffix = split_at_anchor(_skeleton(), task)
    hint = TASK_HINTS.get(task, f"-- Fill this gap with the TASK_{task} implementation.")
    example = TASK_EXAMPLES.get(task, "")
    context = _bridge_comment_block()
    if example:
        context += "\n" + example
    context += "\n" + hint
    prefix = prefix.rstrip("\n") + "\n" + context + "\n"

    print(f"model={model}  task=TASK_{task}")
    print(f"prefix_chars={len(prefix)}  suffix_chars={len(suffix)}")
    if not do_run:
        print("(dry run — no model loaded)\nPREFIX tail:")
        print("\n".join(prefix.splitlines()[-5:]))
        print("\nSUFFIX head:")
        print("\n".join(suffix.splitlines()[:4]))
        return

    middle = run_fim(model, prefix, suffix, num_gpu=num_gpu)
    full = prefix + middle + suffix
    print(f"=== INFILL ({len(middle)} chars) ===")
    print(middle)
    for bad in ("<<<<<<<", ">>>>>>>", "```", "### "):
        if bad in middle:
            print(f"  WARNING: infill contains {bad!r} (format leakage)")

    try:
        sys.path.insert(0, str(ROOT))
        from _luac_path import get_luac_exe
        luac = get_luac_exe() or "luac"
    except Exception:
        luac = "luac"
    fd, tmp = tempfile.mkstemp(suffix=".lua")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(full)
        r = subprocess.run([luac, "-p", tmp], capture_output=True, text=True, timeout=30)
    finally:
        try:
            os.unlink(tmp)
        except Exception:
            pass
    print(f"\nluac rc={r.returncode}")
    if r.returncode != 0:
        print(r.stderr.strip())


if __name__ == "__main__":
    main()
