"""Verification script for all 5 fixes"""
from pathlib import Path
import re

root = Path("C:/Users/Admin/source/repos/midway-pipeline")
txt1 = (root / "pipeline.py").read_text("utf-8")
txt2 = (root / "_pipeline_helpers.py").read_text("utf-8")
txt3 = (root / "pipeline_stream_server.py").read_text("utf-8")
txt4 = (root / "mesh_loops.py").read_text("utf-8")

print("="*60)
print("PASS/FAIL VERIFICATION - 5 Critical Fixes")
print("="*60)

# PASS 1: No parent.parent for PROJECT_ROOT
count_pp = 0
for t in [txt1, txt2, txt3]:
    count_pp += t.count('.parent.parent')
print(f"\n[Pass 1] .parent.parent in PROJECT_ROOT: {count_pp} (should be 0)")
print(f"  pipeline.py uses MIDWAY_PROJECT_ROOT: {'MIDWAY_PROJECT_ROOT' in txt1}")
print(f"  _pipeline_helpers.py uses MIDWAY_PROJECT_ROOT: {'MIDWAY_PROJECT_ROOT' in txt2}")
print(f"  pipeline_stream_server.py uses MIDWAY_PROJECT_ROOT: {'MIDWAY_PROJECT_ROOT' in txt3}")

# PASS 2: Phase 1+2 before Scope Gate
p1_pos = txt4.find("Phase 1: GDD Librarian")
p2_pos = txt4.find("Phase 2: Project Context")
scope_pos = txt4.find("Phase 0.5: Lead Producer")
print(f"\n[Pass 2] Phase 1 (GDD) before Scope Gate: {p1_pos < scope_pos}")
print(f"  Phase 2 (Proj State) before Scope Gate: {p2_pos < scope_pos}")

# PASS 3: blueprint references get_unavailable_domains_text
print(f"\n[Pass 3] Has get_unavailable_domains_text(): {'get_unavailable_domains_text()' in txt4}")
print(f"  Has anti-hallucination instruction: {'Do NOT include tasks for unavailable domains' in txt4}")

# PASS 4: Scope Gate uses regex verdict
verdict_regex = re.search(r're\.search\(r".*VERDICT.*TOO_BROAD', txt4)
print(f"\n[Pass 4] Uses re.search for [VERDICT: TOO_BROAD]: {verdict_regex is not None}")
print(f"  Uses if verdict_match: instead of 'TOO_BROAD' in: {'if verdict_match:' in txt4}")
still_has_old = 'if "TOO_BROAD" in scope_eval.upper()' in txt4
print(f"  OLD pattern removed: {not still_has_old}")

# PASS 5: No return ctx after blueprint save
idx = txt4.find("blueprint_path.write_text(blueprint")
if idx > 0:
    window = txt4[idx:idx+300]
    has_return = "return ctx" in window
    print(f"\n[Pass 5] return ctx after blueprint save: {has_return} (should be False)")
else:
    print("\n[Pass 5] Could not locate blueprint save")
print(f"  Has relaxed auto-feed regex: {'(?:Task' in txt4}")
print(f"  Has continuous execution fall-through: {'Auto-feeding first task' in txt4}")
