#!/usr/bin/env python3
"""Fix #5b: Add ANTI-PATTERNS to the bridge cheatsheet in _helpers_exec.py.
This script reads the file, finds the exact line, and appends anti-patterns."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
fp = ROOT / "_helpers_exec.py"
text = fp.read_text(encoding="utf-8")

if "ANTI-PATTERNS (ALWAYS WRONG)" in text:
    print("[FIX #5b] Already applied")
else:
    # Find the Lifecycle line using the \x97 character (em dash from Windows-1252)
    lines = text.splitlines(True)
    for i, line in enumerate(lines):
        if 'Lifecycle: OnLoadStatic()' in line and 'bare globals' in line:
            indent = line[:len(line) - len(line.lstrip())]
            block = (
                f'{indent}"Lifecycle: OnLoadStatic() / OnLoad() / OnUnload() \u2014 bare globals, no return.\\n"\n'
                f'{indent}"ANTI-PATTERNS (ALWAYS WRONG):\\n"\n'
                f'{indent}"  - DO NOT put SpawnDynamic* calls at module root level (crashes engine)\\n"\n'
                f'{indent}"  - DO NOT define function OnStep(dt) at module level; use MidwayPhysics.OnStep(function(dt)...end)\\n"\n'
                f'{indent}"  - DO NOT create duplicate OnLoadStatic() / OnLoad() functions\\n"\n'
                f'{indent}"  - DO NOT cache AttractionConstants.modifiers at module level; read inside OnStep\\n"\n'
            )
            lines[i] = block
            with open(fp, 'w', encoding='utf-8') as f:
                f.writelines(lines)
            print("[FIX #5b] Applied successfully")
            break
    else:
        print("[FIX #5b] FAIL: Could not find Lifecycle line")
        for i, line in enumerate(lines):
            if 'Lifecycle' in line:
                print(f"  Line {i+1}: {repr(line)}")
