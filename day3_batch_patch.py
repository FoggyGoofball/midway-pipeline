#!/usr/bin/env python3
"""Day 3 batch patch — verify all 9 tasks, apply any missing edits via regex/line surgery."""

from __future__ import annotations
import re, sys

PREFIX = r"c:\Users\Admin\source\repos\midway-pipeline"

# ── Verifier ────────────────────────────────────────────────────────────────
def check(name: str, path: str, pattern: str) -> bool:
    path = f"{PREFIX}\\{path}"
    with open(path, encoding="utf-8") as f:
        content = f.read()
    found = bool(re.search(pattern, content))
    mark = "✓" if found else "✗"
    print(f"  [{mark}] {name}")
    return found

print("=" * 60)
print("  Day 3 — Batch Verification & Patch")
print("=" * 60)

checks = [
    ("T1: pro_mode in PipelineContext", "models.py", r"pro_mode:\s*bool\s*=\s*False"),
    ("T2: MATH_HEAVY in build_director_prompt", "_pipeline_helpers.py", r"MATH_HEAVY"),
    ("T3: MATH_HEAVY gate in run_fetches", "mesh_loops.py", r"MATH_HEAVY.*detected|math.*pro.?mode", re.IGNORECASE),
    ("T4: MATH_EVAL in SignalType", "signals.py", r"MATH_EVAL"),
    ("T5: subprocess MATH_EVAL handler", "mesh_loops.py", r"subprocess.*MATH_EVAL|MATH_EVAL.*subprocess"),
    ("T6: pro_mode TDD injection in domain_registry", "domain_registry.py", r"pro_mode"),
    ("T7: Pro Mode test compilation in mesh_finalize", "mesh_finalize.py", r"pro_mode.*test|test.*pro_mode"),
    ("T8: Multi-draft generation (3 temps)", "mesh_loops.py", r"temperatures\s*=\s*\[0.2,\s*0.5,\s*0.8\]"),
    ("T9: Tribunal merge agent", "mesh_loops.py", r"Tribunal"),
    ("S1: ollama_client params dict", "ollama_client.py", r"def call_ollama\(.*params.*None"),
    ("S2: execute_task ollama_params", "_pipeline_helpers.py", r"def execute_task\(.*ollama_params.*None"),
]

all_ok = True
for name, path, pattern, *flags in checks:
    flag_val = flags[0] if flags else 0
    ok = bool(re.search(pattern, open(f"{PREFIX}\\{path}", encoding="utf-8").read(), flag_val))
    print(f"  [{'✓' if ok else '✗'}] {name}")
    if not ok:
        all_ok = False

if all_ok:
    print("\n✅ ALL 11 CHECKS PASSED — Day 3 complete!")
    sys.exit(0)

print("\n⚠️  Some checks failed. Applying patches...")

# ── Apply missing patches ──────────────────────────────────────────────────

def patch(path: str, old: str, new: str) -> bool:
    full = f"{PREFIX}\\{path}"
    with open(full, encoding="utf-8") as f:
        content = f.read()
    if old not in content:
        print(f"  ⚠️  SEARCH string not found in {path}, skipping")
        return False
    content = content.replace(old, new, 1)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  ✓ Patched {path}")
    return True

# T2: MATH_HEAVY sensor in build_director_prompt
patch("_pipeline_helpers.py",
    'CRITICAL: Do NOT write any code. Only list tasks.',
    'CRITICAL: Do NOT write any code. Only list tasks.\n\n'
    'MATH SENSOR: If the user\'s request involves dense 3D math, quaternions, '
    'or complex physics algorithms, you MUST append the exact string [MATH_HEAVY] '
    'to the very end of your output.'
)

# T3: MATH_HEAVY gate in mesh_loops.py Phase 3
mesh_path = f"{PREFIX}\\mesh_loops.py"
with open(mesh_path, encoding="utf-8") as f:
    mesh = f.read()

if "[MATH_HEAVY]" not in mesh or "pro_mode" not in mesh or "Pro Mode" not in mesh:
    # Find the director output section and inject after it
    inject_point = (
        "    # Parse tasks from Director output\n"
        "\n"
        "    task_regex = r\"### Task (\\d+): \\[([^\\]]+)\\] — (.+)\""
    )
    pro_mode_gate = """
    # ── Pro Mode: MATH_HEAVY Gate ──────────────────────────────────────
    if "[MATH_HEAVY]" in ctx.director_output:
        print(f"\\n{'='*50}")
        print(f"  MATH_HEAVY DETECTED — Complex 3D math / physics request")
        print(f"{'='*50}")
        warning_msg = (
            "It looks like a lot of complex math needs to be calculated. "
            "Should we turn on Pro Mode for rigorous test-driven consensus? [y/N]: "
        )
        user_input = input(f"  {warning_msg}").strip().lower()
        if user_input in ("y", "yes"):
            ctx.pro_mode = True
            print(f"  [Pro Mode] ENABLED — TDD guardrails, multi-draft consensus, and test compilation active.")
        else:
            print(f"  [Pro Mode] Declined — continuing in standard mode.")

    """ + inject_point

    if inject_point in mesh:
        mesh = mesh.replace(inject_point, pro_mode_gate)
        with open(mesh_path, "w", encoding="utf-8") as f:
            f.write(mesh)
        print("  ✓ Patched mesh_loops.py with Pro Mode MATH_HEAVY gate")
    else:
        print("  ⚠️  Could not find anchor for T3 in mesh_loops.py")

# T7: Pro Mode test compilation in mesh_finalize.py
finalize_path = f"{PREFIX}\\mesh_finalize.py"
with open(finalize_path, encoding="utf-8") as f:
    finalize = f.read()

if "pro_mode" not in finalize or "compile.*test" not in finalize:
    # Find compiler command logic and inject Pro Mode gate
    compile_block = "    # ── Compile the engine ───────────────────────────────────────────────"
    pro_mode_compile = """    # ── Pro Mode: Compile & Execute Unit Tests ──────────────────────────
    if ctx.pro_mode:
        print(f"  [Pro Mode] Pro Mode enabled — compiling and running tests...")
        # Try to compile and execute the test suite binary
        test_build_cmd = f"cd /d {build_dir} && cmake --build . --target run_tests 2>&1 || cmake --build . --target test_suite 2>&1"
        test_result = subprocess.run(test_build_cmd, shell=True, capture_output=True, text=True, timeout=120)
        if test_result.returncode != 0:
            print(f"  [Pro Mode][VETO] Tests failed — feeding stderr back to agent")
            veto_signal = {
                "type": "VETO",
                "from": "COMPILER",
                "target": resolve_agent_name(task.agent) if 'task' in dir() else "Coder",
                "content": f"Unit tests failed:\\n{test_result.stderr[:2000]}",
            }
            ctx.all_vetos.append(veto_signal)
            ctx.test_failures.append({
                "stderr": test_result.stderr,
                "stdout": test_result.stdout,
                "task_id": task.task_id if 'task' in dir() else "",
            })
        else:
            print(f"  [Pro Mode] All tests passed!")
    """ + compile_block

    if compile_block in finalize:
        finalize = finalize.replace(compile_block, pro_mode_compile)
        with open(finalize_path, "w", encoding="utf-8") as f:
            f.write(finalize)
        print("  ✓ Patched mesh_finalize.py with Pro Mode test compilation gate")
    else:
        print("  ⚠️  Could not find compile anchor in mesh_finalize.py")

# ── Final re-verify ────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("  Final Verification")
print("=" * 60)
all_ok = True
for name, path, pattern, *flags in checks:
    flag_val = flags[0] if flags else 0
    try:
        ok = bool(re.search(pattern, open(f"{PREFIX}\\{path}", encoding="utf-8").read(), flag_val))
    except:
        ok = False
    print(f"  [{'✓' if ok else '✗'}] {name}")
    if not ok:
        all_ok = False

if all_ok:
    print("\n✅ ALL CHECKS PASSED — Day 3 complete!")
else:
    print("\n⚠️  Some checks still failing — review above")
