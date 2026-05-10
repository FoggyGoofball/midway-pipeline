"""
_finalize_preflight.py — Pre-Flight Checks & Architect Fix
===========================================================
Extracted from mesh_finalize.py — handles background compilation,
syntax validation, and the Architect Syntax Fix invocation loop.

Exported:
    _run_preflight_checks(ctx) -> PipelineContext
"""

from __future__ import annotations

import re
import subprocess
import sys
from typing import Any, Dict, List, Optional

from models import PipelineContext
from _pipeline_helpers import atomic_write_text
from pipeline import (
    PROJECT_ROOT, ARCHITECT_FIX_SYSTEM, EXECUTION_MODEL,
    call_ollama,
)


# ──────────────────────────────────────────────────────────────────────
#  Pre-Flight Checks: Compilation & Syntax Validation
# ──────────────────────────────────────────────────────────────────────

def _run_preflight_checks(ctx: PipelineContext) -> PipelineContext:
    """
    Phase 6 pre-flight: run background compilers (C++ / Make), Lua syntax
    checks, and Pro Mode unit test compilation/execution. If errors are
    detected, invoke the Architect Syntax Fix cycle to patch them.
    """
    print("  [Pre-Flight] Running background compilers...")
    ctx.pre_flight_errors = ""

    # Platform-aware compilation check
    try:
        if sys.platform == "win32":
            cmake_build = subprocess.run(
                ["cmake", "--build", "."],
                capture_output=True, text=True, cwd=ctx.project_root,
                shell=True, timeout=30,
            )
            if cmake_build.returncode != 0:
                err_tail = "\n".join(cmake_build.stderr.splitlines()[-50:])
                ctx.pre_flight_errors += f"\n## C++ Compiler Errors:\n```\n{err_tail}\n```"
                # Circuit Breaker: increment retry count for each task on build failure
                for tid in list(ctx.all_results_dict.keys()):
                    ctx.retry_counts[tid] = ctx.retry_counts.get(tid, 0) + 1
                    print(f"  [Circuit Breaker] {tid} retry_count incremented to {ctx.retry_counts[tid]} (build failure)")
        else:
            make_process = subprocess.run(
                ["make", "-j4"], capture_output=True, text=True,
                cwd=ctx.project_root, timeout=30,
            )
            if make_process.returncode != 0:
                err_tail = "\n".join(make_process.stderr.splitlines()[-50:])
                ctx.pre_flight_errors += f"\n## C++ Compiler Errors:\n```\n{err_tail}\n```"
                # Circuit Breaker: increment retry count for each task on build failure
                for tid in list(ctx.all_results_dict.keys()):
                    ctx.retry_counts[tid] = ctx.retry_counts.get(tid, 0) + 1
                    print(f"  [Circuit Breaker] {tid} retry_count incremented to {ctx.retry_counts[tid]} (build failure)")
    except subprocess.TimeoutExpired:
        ctx.pre_flight_errors += "\n## Compiler Timeout:\n```\nC++ build timed out after 30s\n```\n"
    except Exception:
        pass

    # ── Pro Mode: Unit Test Compilation & Execution ────────────────────
    if ctx.pro_mode:
        print("  [Pro Mode] Compiling and executing test suite...")
        test_build_dir = ctx.project_root / "build" / "tests"
        test_binary = test_build_dir / "run_tests"
        if sys.platform == "win32":
            test_binary = test_build_dir / "run_tests.exe"

        # Build test target if cmake project is configured
        test_build_ok = True
        try:
            test_build_proc = subprocess.run(
                ["cmake", "--build", ".", "--target", "run_tests"],
                capture_output=True, text=True, cwd=ctx.project_root,
                shell=True, timeout=60,
            )
            if test_build_proc.returncode != 0:
                test_build_ok = False
                err_tail = "\n".join(test_build_proc.stderr.splitlines()[-50:])
                ctx.pre_flight_errors += (
                    f"\n## Test Suite Compilation Errors:\n```\n{err_tail}\n```\n"
                )
                print(f"  [Pro Mode] ⚠ Test suite failed to compile — treating as [VETO]")
        except (subprocess.TimeoutExpired, Exception) as e:
            test_build_ok = False
            ctx.pre_flight_errors += (
                f"\n## Test Suite Compilation Exception:\n```\n{e}\n```\n"
            )

        if test_build_ok and test_binary.is_file():
            try:
                test_run = subprocess.run(
                    [str(test_binary)], capture_output=True, text=True,
                    timeout=60,
                )
                if test_run.returncode != 0:
                    test_stderr = test_run.stderr.strip() or test_run.stdout.strip()
                    ctx.pre_flight_errors += (
                        f"\n## Unit Test Failures:\n```\n{test_stderr[:2000]}\n```\n"
                    )
                    print(f"  [Pro Mode] ⛔ Tests FAILED — feeding errors back to domain agents")
                else:
                    print(f"  [Pro Mode] ✅ All unit tests passed!")
            except subprocess.TimeoutExpired:
                ctx.pre_flight_errors += (
                    "\n## Unit Test Timeout:\n```\nTest binary timed out after 60s\n```\n"
                )
            except Exception as e:
                ctx.pre_flight_errors += (
                    f"\n## Unit Test Execution Error:\n```\n{e}\n```\n"
                )

    for lf in ctx.project_root.rglob("*.lua"):
        try:
            lua_proc = subprocess.run(
                ["luac", "-p", str(lf)], capture_output=True, text=True, timeout=30,
            )
            if lua_proc.returncode != 0:
                ctx.pre_flight_errors += (
                    f"\n## Lua Syntax Error in {lf.name}:\n```\n{lua_proc.stderr}\n```"
                )
        except subprocess.TimeoutExpired:
            ctx.pre_flight_errors += (
                f"\n## Lua Syntax Error in {lf.name}:\n```\nluac timed out after 30s\n```\n"
            )
        except Exception:
            pass

    # ── Architect Syntax Fix Cycle ─────────────────────────────────────
    if ctx.pre_flight_errors:
        print("  [Pre-Flight] Syntax errors detected. Forcing Architect Fix Cycle.")
        all_code_str = "\n\n".join(
            [f"### {tid}\n{output}" for tid, output in ctx.all_results_dict.items()]
        )
        fix_input = (
            f"Your generated code failed local compilation/syntax checks. "
            f"Fix the following errors:\n{ctx.pre_flight_errors}\n\n"
            f"Previously generated code:\n{all_code_str}"
        )
        all_code_str = call_ollama(
            ARCHITECT_FIX_SYSTEM, fix_input, "Architect Syntax Fix", EXECUTION_MODEL
        )

        # Parse the fixed code blocks and update specific tasks
        for match in re.finditer(
            r"### (task_\d+)\n(.*?)(?=### task_\d+|\Z)", all_code_str, re.DOTALL
        ):
            tid = match.group(1).strip()
            fixed_code = match.group(2).strip()
            if tid in ctx.all_results_dict:
                ctx.all_results_dict[tid] = fixed_code

    return ctx
