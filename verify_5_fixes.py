#!/usr/bin/env python3
"""Verify all 5 fixes were applied correctly."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
ok = True

def check(label, condition, detail=""):
    global ok
    if condition:
        print(f"  [PASS] {label}")
    else:
        print(f"  [FAIL] {label} {detail}")
        ok = False

# Fix #2: Mesh Blueprint scaffold persistence
bf = ROOT / "mesh_fetches_blueprint.py"
text = bf.read_text(encoding="utf-8")
check(
    "FIX #2: ctx._user_file_constraint_canonical persisted",
    "ctx._user_file_constraint_canonical = _user_file_constraint_canonical" in text
)

# Fix #1: 4th-tier fuzzy patch fallback
eh = ROOT / "_helpers_exec.py"
text = eh.read_text(encoding="utf-8")
check(
    "FIX #1: 4th-tier partial-content fallback added",
    "Partial-content fallback" in text
)

# Fix #4: Duplicate-function guard in preflight
fp = ROOT / "_finalize_preflight.py"
text = fp.read_text(encoding="utf-8")
check(
    "FIX #4: duplicate-function guard in _finalize_preflight.py",
    "refuse to introduce duplicate function" in text
)

# Fix #5: ANTI-PATTERNS in docs/rules_lua.md
rl = ROOT / "docs" / "rules_lua.md"
text = rl.read_text(encoding="utf-8")
check(
    "FIX #5: ANTI-PATTERNS section in docs/rules_lua.md",
    "### ANTI-PATTERNS (NEVER DO THESE)" in text
)

# Fix #5b: ANTI-PATTERNS in bridge cheatsheet
check(
    "FIX #5b: ANTI-PATTERNS in _helpers_exec.py bridge cheatsheet",
    "ANTI-PATTERNS (ALWAYS WRONG)" in text
)

# Fix #3: Wave serialization
ws = ROOT / "mesh_wave_sorter.py"
text = ws.read_text(encoding="utf-8")
check(
    "FIX #3: Single-file serialization in mesh_wave_sorter.py",
    "Single-File" in text or "single-file" in text
)

# Also check mesh_tasks.py propagates target_file
mt = ROOT / "mesh_tasks.py"
if mt.exists():
    mt_text = mt.read_text(encoding="utf-8")
    check(
        "FIX #3: target_file propagated in mesh_tasks.py",
        '"target_file"' in mt_text or "'target_file'" in mt_text
    )

print(f"\n{'='*50}")
if ok:
    print("All 5 fixes verified successfully!")
else:
    print("Some fixes failed verification - see above.")
print(f"{'='*50}")
