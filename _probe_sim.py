import sys
sys.path.insert(0, ".")
from pathlib import Path
from runtime_sim import _extract_lua, _analyse_lua_text, _run_live_harness
import tempfile

FENCE = chr(96) * 3  # triple backtick, avoiding shell mangling
p = Path(r"C:\Users\Admin\source\repos\midway\attractions\strongman\strongman.lua")
txt = p.read_text(encoding="utf-8", errors="replace")

# Wrap in a markdown fence and extract (mimics the pipeline path)
wrapped = f"{FENCE}lua\n{txt}\n{FENCE}"
lua = _extract_lua(wrapped)
print("extracted starts with:", repr(lua[:40]))
print("has fence in extracted:", FENCE in lua)

print("=== static errors ===")
for e in _analyse_lua_text("strongman.lua", lua):
    print(" -", e)

d = Path(tempfile.mkdtemp(prefix="sim_"))
print("=== live errors ===")
for e in _run_live_harness("strongman.lua", lua, d):
    print(" -", e)
