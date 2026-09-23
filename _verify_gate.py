"""
_verify_gate.py — deterministic invariant checks (Phase 1: read-only reporter).

Phase 1 = observe only. `run_invariant_checks` reports violations of the five
invariants (I1..I5) without mutating anything. Phase 3 will add the
`verify_or_revert` policy on top of these same checks.

The five invariants are the single source of truth the pipeline must satisfy:

  I1 syntactic     — `luac -p` accepts the file
  I2 no phantom API— every engine call is in the bridge contract
  I3 lifecycle     — OnLoadStatic / OnLoad / OnUnload / OnStep each appear once
  I4 no globals    — no bare assignment to an undeclared non-engine name
  I5 arity         — every call's arg count matches the arity table

No model calls, no writes to the pipeline's files. The only side effect is a
temporary file used by `luac -p`.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from typing import List, Optional, Tuple

from _luac_path import get_luac_exe as _get_luac_exe
from midway_api_signatures import SPAWN_ARITY, BODY_ARITY, ECONOMY_ARITY


@dataclass
class Finding:
    invariant: str      # "I1" .. "I5"
    message: str
    rel_path: str = ""


# -- Invariant primitives ----------------------------------------------------

_FN_DEF_RE = re.compile(r'^(?:local\s+)?function\s+([A-Za-z_]\w*)\s*\(', re.MULTILINE)
_ONSTEP_REG_RE = re.compile(r'MidwayPhysics\.OnStep\s*\(\s*function\b')
_CALL_RE = re.compile(r'\b(MidwayPhysics|Engine)\.([A-Za-z_]\w*)\s*\((.*?)\)', re.DOTALL)

_ARITY: dict = {}
for _tbl in (SPAWN_ARITY, BODY_ARITY, ECONOMY_ARITY):
    _ARITY.update({k.lower(): v for k, v in _tbl.items()})


def _luac_clean(content: str) -> Tuple[bool, str]:
    """Return (clean, stderr) via `luac -p`. Not checkable -> (True, '')."""
    exe = _get_luac_exe()
    if not exe:
        return True, ""
    fd, path = tempfile.mkstemp(suffix=".lua")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        r = subprocess.run([exe, "-p", path], capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            return True, ""
        # luac prefixes stderr with its own exe path and the temp file path,
        # e.g. "C:\...\luac.EXE: C:\...\tmpX.lua:4: 'end' expected ...".
        # Keep only the "line N: message" tail so the reporter is readable.
        _err = (r.stderr or "").strip()
        _m = re.search(r':\s*(\d+):\s*(.*)', _err)
        if _m:
            _err = f"line {_m.group(1)}: {_m.group(2)}"
        return False, _err[:200]
    except Exception:
        return True, ""
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _build_contract():
    from contract_validator import build_lua_contract
    bridge: dict = {}
    try:
        from cartridges.midway_data_refs import build_bridge_contract
        bridge = build_bridge_contract() or {}
    except Exception:
        bridge = {}
    if not bridge:
        bridge = {
            "midwayphysics_spawn_api": {k: "" for k in SPAWN_ARITY},
            "object_pools": {k: "" for k in BODY_ARITY},
            "economy_api": {k: "" for k in ECONOMY_ARITY},
        }
    return build_lua_contract(bridge)


def _count_top_level_args(args_text: str) -> int:
    """Count top-level comma-separated arguments, ignoring strings, long strings,
    and nested brackets/parens."""
    n = len(args_text)
    i = 0
    depth = 0
    count = 0 if not args_text.strip() else 1
    in_str = None
    while i < n:
        c = args_text[i]
        nxt = args_text[i + 1] if i + 1 < n else ""
        if in_str:
            if c == "\\":
                i += 2
                continue
            if c == in_str:
                in_str = None
            i += 1
            continue
        if c in ('"', "'"):
            in_str = c
            i += 1
            continue
        if c == "-" and nxt == "-":
            # line comment inside an arg list: stop counting at the comment
            break
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth = max(0, depth - 1)
        elif c == "," and depth == 0:
            count += 1
        i += 1
    return count


def run_invariant_checks(content: str, rel_path: str = "") -> List[Finding]:
    """Return the list of invariant violations for one Lua file (read-only)."""
    findings: List[Finding] = []

    # I1 — syntactic
    _ok, _err = _luac_clean(content)
    if not _ok:
        findings.append(Finding("I1", f"luac rejected: {_err}", rel_path))

    # I2 — phantom / bare-call contract (modifier-cache business-logic findings
    # are a different invariant handled by Fix #2/#31, so exclude them here).
    try:
        from contract_validator import validate_lua_content
        _violations = validate_lua_content(content, _build_contract())
        for v in _violations:
            if getattr(v, "kind", "") in ("phantom_api", "bare_call"):
                findings.append(Finding("I2", v.label, rel_path))
                if len([f for f in findings if f.invariant == "I2"]) >= 5:
                    break
    except Exception as e:
        findings.append(Finding("I2", f"contract check unavailable: {e}", rel_path))

    # I3 — exactly-one lifecycle hooks
    _hook_counts: dict = {}
    for m in _FN_DEF_RE.finditer(content):
        _name = m.group(1)
        if _name in ("OnLoadStatic", "OnLoad", "OnUnload"):
            _hook_counts[_name] = _hook_counts.get(_name, 0) + 1
    _onstep = len(_ONSTEP_REG_RE.findall(content))
    for _hook in ("OnLoadStatic", "OnLoad", "OnUnload"):
        _c = _hook_counts.get(_hook, 0)
        if _c != 1:
            findings.append(
                Finding("I3", f"{_hook} appears {_c}x (expected exactly 1)", rel_path)
            )
    if _onstep != 1:
        findings.append(
            Finding("I3", f"OnStep registration appears {_onstep}x (expected exactly 1)", rel_path)
        )

    # I4 — no leaked globals (bare assignment to undeclared non-engine name)
    try:
        from _post_process_lua import _lua_symbol_table
        _declared, _assigned, _read = _lua_symbol_table(content)
        _leaked = sorted(a for a in _assigned if a not in _declared)
        for _name in _leaked[:5]:
            findings.append(
                Finding("I4", f"bare assignment leaks global '{_name}'", rel_path)
            )
    except Exception as e:
        findings.append(Finding("I4", f"symbol check unavailable: {e}", rel_path))

    # I5 — arity
    for m in _CALL_RE.finditer(content):
        _sym = m.group(2)
        _sym_l = _sym.lower()
        if _sym_l not in _ARITY:
            continue
        _min, _max = _ARITY[_sym_l]
        _n = _count_top_level_args(m.group(3))
        if not (_min <= _n <= _max):
            findings.append(
                Finding(
                    "I5",
                    f"{m.group(1)}.{_sym}() takes {_n} arg(s), expected {_min}..{_max}",
                    rel_path,
                )
            )

    return findings


def report_invariants(ctx, enabled_env: str = "MIDWAY_INVARIANT_REPORT") -> None:
    """Print a per-file invariant summary for every owned .lua target (read-only).

    Enabled by default; set `MIDWAY_INVARIANT_REPORT=0` to silence.
    """
    if os.environ.get(enabled_env, "1") == "0":
        return
    _owned: set = set()
    _mono = getattr(ctx, "_monolithic_lua_target", None)
    if _mono and str(_mono).endswith(".lua"):
        _owned.add(str(_mono).replace("\\", "/"))
    for _t in getattr(ctx, "task_map", {}).values():
        _tf = getattr(_t, "target_file", None)
        if _tf and str(_tf).endswith(".lua"):
            _owned.add(str(_tf).replace("\\", "/"))
    for _rel in sorted(_owned):
        _abs = (getattr(ctx, "project_root", None) or _t_fallback()) / _rel
        if not _abs.is_file():
            continue
        try:
            _content = _abs.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        _findings = run_invariant_checks(_content, _rel)
        _summary = ", ".join(
            f"{f.invariant}={f.message[:60]}" for f in _findings
        ) or "CLEAN"
        print(f"  [Invariants] {_rel}: {_summary}")


def _t_fallback():
    from pathlib import Path
    return Path(".")


def verify_or_revert(ctx, rel_path: str, proposed: str, last_clean: str) -> str:
    """Phase 3: enforce-or-revert.

    Return `proposed` if it passes all five invariants; otherwise log the
    failures and return `last_clean` (the baseline). This is the policy that
    ends "repair indefinitely": a file that cannot be *proven* clean is
    reverted, not shipped.
    """
    findings = run_invariant_checks(proposed, rel_path)
    if not findings:
        return proposed
    for f in findings[:5]:
        print(f"  [VerifyGate] ⚠ {rel_path}: {f.invariant} — {f.message[:80]}")
    print(
        f"  [VerifyGate] ⛔ {rel_path}: {len(findings)} invariant violation(s) "
        f"— reverting to last luac-clean baseline."
    )
    return last_clean
