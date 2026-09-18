"""
mechanics_scaffold.py -- Mechanics Scaffold (between Architect JSON and coder)
=============================================================================

Design doc: docs/MECHANICS_SCAFFOLD_PLAN.md

Premise: the coder fails because it invents game logic AND API usage
simultaneously.  This stage asks the already-resident reasoning model to reason
each mechanic in a bounded, annotated, machine-checkable 3-field template
(INTENT + PSEUDO + API) and bind it to an exact approved bridge signature.
The coder then *translates* a known shape instead of inventing from blank.

Flow (behind MIDWAY_MECHANICS_SCAFFOLD=1, default off):
    ctx.tasks_list (anchors finalized) + ctx.attraction_design
        -> generate one MechanicScaffold per logic-bearing Lua anchor task
        -> validate against contract_validator + midway_api_signatures
        -> re-prompt once on failure (bounded)
        -> store on ctx.scaffolds (keyed by task number)
    _helpers_exec.execute_task injects the scaffold above the anchor hook.

Every function degrades gracefully: no design, no bridge contract, or an
Ollama error means the pipeline continues exactly as before (no scaffolds).
"""

from __future__ import annotations

import os
import re
from typing import Dict, List, Optional, Tuple

from models import MechanicScaffold, PipelineContext

# -- Task-number extraction ---------------------------------------------------
_TASK_NUM_RE = re.compile(r"TASK_(\d+)_INSERT_HOOK")

# Anchors whose hooks are structural (no real "logic" to reason about) are
# skipped.  This matches the design doc's open question #4: geometry/state
# skeleton hooks have nothing for the scaffold to bind.
_SKIP_HOOK_KEYWORDS = (
    "permanent geometry",
    "module-level state",
    "shared constants table",
)


def _anchor_task_number(anchor_marker: str) -> Optional[str]:
    """Return the numeric task id from an anchor marker, e.g. "3". """
    if not anchor_marker:
        return None
    m = _TASK_NUM_RE.search(anchor_marker)
    return m.group(1) if m else None


# -- System prompt ------------------------------------------------------------

SCAFFOLD_SYSTEM = (
    "You are the MECHANICS SCAFFOLDER for 'Midway to Nowhere', a Lua-based arcade "
    "game whose attractions are single Lua files wired to the MidwayPhysics bridge. "
    "There is NO C++ class hierarchy. Handles are Lua-local userdata values RETURNED "
    "by Spawn*/CreatePool calls; a handle is NEVER the first argument to a Spawn* or "
    "Pool* call.\n\n"
    "For the ONE task you are given, produce a short reasoning scaffold in EXACTLY "
    "this 3-field format:\n\n"
    "### Mechanic: <short name>\n"
    "INTENT: <ONE sentence -- what this mechanic does and why>\n"
    "PSEUDO:\n"
    "  <3 to 12 lines of language-neutral pseudocode>\n"
    "API:\n"
    "  <one approved call per line, exact signature>\n\n"
    "HARD RULES:\n"
    "1. INTENT is ONE sentence, never a paragraph.\n"
    "2. PSEUDO is at most 12 lines and may use ONLY approved API names (see the "
    "list below) or plain words -- never invented API names.\n"
    "3. API: lists the EXACT MidwayPhysics.X / Engine.X call(s) you will use, "
    "copied verbatim (name + argument names) from the approved list below. "
    "Do NOT change the argument list, order, or count.\n"
    "4. Output NOTHING but the scaffold. Do NOT write Lua code. Do NOT add "
    "prose, headings, or commentary after the API: section.\n"
    "5. A Spawn*/CreatePool/PoolAcquire first argument is ALWAYS world "
    "coordinates (numbers) or a pool NAME string -- never a handle.\n"
)

# Worked example injected into every scaffold prompt (keeps the model on-format).
SCAFFOLD_EXAMPLE = (
    "WORKED EXAMPLE:\n"
    "### Mechanic: Bell's Curse\n"
    "INTENT: between swings the bell shifts vertically by a luck-scaled offset\n"
    "PSEUDO:\n"
    "  if swing_complete then\n"
    "    offset = luck * SHIFT_MAX\n"
    "    bell.y += offset\n"
    "  end\n"
    "API:\n"
    "  MoveKinematic(bell_handle, lx, ly, lz, dt)\n"
)


# -- Prompt builder -----------------------------------------------------------

def _build_scaffold_prompt(
    task: dict,
    design_block: str,
    approved_apis: str,
) -> str:
    """Build the user prompt for a single anchor task's scaffold."""
    _title = (task.get("title") or "").strip()
    _spec = (task.get("spec") or "").strip()
    _anchor = (task.get("anchor_marker") or "").strip()
    _parts = [
        f"## Task to scaffold\nTitle: {_title or '(untitled)'}",
    ]
    if _spec:
        _parts.append(f"Task spec:\n{_spec}")
    if _anchor:
        _parts.append(f"Anchor hook you are reasoning about:\n{_anchor}")
    if design_block:
        _parts.append(f"\n## Attraction Design Reference\n{design_block}")
    _parts.append(f"\n{SCAFFOLD_EXAMPLE}")
    _parts.append(f"\n## Approved API list (copy signatures VERBATIM from here)\n{approved_apis}")
    _parts.append(
        "\nProduce ONLY the 3-field scaffold for THIS task now. "
        "Reason in PSEUDO, bind in API, then stop."
    )
    return "\n".join(_parts)


# -- Parsing ------------------------------------------------------------------

_MECHANIC_RE = re.compile(r"^#{2,3}\s*Mechanic\s*:\s*(.+?)\s*$", re.MULTILINE)
_INTENT_RE = re.compile(r"^INTENT\s*:\s*(.+?)\s*$", re.MULTILINE)
_PSEUDO_RE = re.compile(r"^PSEUDO\s*:\s*(.*?)(?=^\s*API\s*:|\Z)", re.MULTILINE | re.DOTALL)
_API_RE = re.compile(r"^API\s*:\s*(.*?)(?=\Z)", re.MULTILINE | re.DOTALL)
# Matches a namespaced call: Namespace.Symbol(
_NS_CALL_RE = re.compile(r"\b([A-Za-z_]\w*)\s*\.\s*([A-Za-z_]\w*)\s*\(")
# Matches a bare call: Symbol(  (not preceded by a dot/word char)
_BARE_CALL_RE = re.compile(r"(?<![.\w])([A-Za-z_]\w*)\s*\(")


def _match_call(line: str):
    """Return (namespace_or_empty, symbol) for the first call on a line, or None."""
    _m = _NS_CALL_RE.search(line)
    if _m:
        return _m.group(1), _m.group(2)
    _m = _BARE_CALL_RE.search(line)
    if _m:
        return "", _m.group(1)
    return None


def _clean_pseudo(raw: str) -> str:
    """Strip bullets/indent and trailing markers from PSEUDO lines."""
    lines: List[str] = []
    for _ln in raw.splitlines():
        _s = _ln.strip()
        if not _s:
            continue
        # Strip common list markers: "-", "*", "1." prefixes.
        _s = re.sub(r"^[-*]\s+", "", _s)
        _s = re.sub(r"^\d+[.)]\s+", "", _s)
        if _s.upper().startswith("API"):
            break
        lines.append(_s)
    # Cap at 12 lines (the format's hard bound).
    return "\n".join(lines[:12])


def _extract_api_calls(raw: str) -> List[str]:
    """Extract the API: column as a list of clean call lines."""
    calls: List[str] = []
    for _ln in raw.splitlines():
        _s = _ln.strip()
        if not _s or _s.startswith("#"):
            continue
        _s = re.sub(r"^[-*]\s+", "", _s)
        _s = re.sub(r"^\d+[.)]\s+", "", _s)
        if _match_call(_s):
            calls.append(_s)
    return calls


def _parse_scaffold(text: str, task_id: str) -> Optional[MechanicScaffold]:
    """Parse a raw scaffold response into a MechanicScaffold. Returns None when
    the response has no recognizable INTENT/PSEUDO/API structure."""
    if not text or not text.strip():
        return None

    _name = ""
    _m = _MECHANIC_RE.search(text)
    if _m:
        _name = _m.group(1).strip()

    _intent = ""
    _m = _INTENT_RE.search(text)
    if _m:
        _intent = _m.group(1).strip()

    _pseudo = ""
    _m = _PSEUDO_RE.search(text)
    if _m:
        _pseudo = _clean_pseudo(_m.group(1))

    _api_raw = ""
    _m = _API_RE.search(text)
    if _m:
        _api_raw = _m.group(1)
    _api_calls = _extract_api_calls(_api_raw)

    # Reject prose-only responses (no INTENT, no API) — nothing to bind.
    if not _intent and not _api_calls:
        return None

    return MechanicScaffold(
        name=_name or f"task {task_id}",
        intent=_intent,
        pseudo=_pseudo,
        api_calls=_api_calls,
        task_id=str(task_id),
        raw=text.strip(),
    )


# -- Validation ---------------------------------------------------------------

def _get_bridge_contract(ctx: PipelineContext) -> dict:
    """Resolve the raw bridge-contract dict from the mounted cartridge."""
    _build_fn = getattr(ctx, "_cartridge_build_bridge_contract", None)
    _bc = {}
    if callable(_build_fn):
        try:
            _bc = _build_fn() or {}
        except Exception:
            _bc = {}
    return _bc if isinstance(_bc, dict) else {}


def _validate_scaffold(
    scaffold: MechanicScaffold,
    lua_contract,
    handle_names: set,
) -> List[str]:
    """Validate a scaffold's API column. Returns a list of violation strings
    (empty == valid)."""
    violations: List[str] = []

    from midway_api_signatures import SPAWN_ARITY, BODY_ARITY, ECONOMY_ARITY
    _arity = {**SPAWN_ARITY, **BODY_ARITY, **ECONOMY_ARITY}
    _spawn_or_pool = {
        name for name in _arity
        if name.startswith("Spawn") or name.startswith("CreatePool")
    }

    _approved = getattr(lua_contract, "approved_calls", frozenset()) or frozenset()
    _bare_ns = getattr(lua_contract, "bare_name_to_namespace", {}) or {}

    for _line in scaffold.api_calls:
        _hit = _match_call(_line)
        if not _hit:
            continue
        _ns, _sym = _hit
        if _ns:
            _full = f"{_ns.lower()}.{_sym.lower()}"
        else:
            # Bare call: resolve the namespace via the contract's bare-name map.
            _canonical_ns = _bare_ns.get(_sym.lower(), "")
            if not _canonical_ns:
                violations.append(
                    f"API line '{_line.strip()}' references bare call {_sym}(...) "
                    f"with no known namespace."
                )
                continue
            _full = f"{_canonical_ns.lower()}.{_sym.lower()}"

        # Rule 1: symbol must exist in the bridge contract.
        if _full not in _approved:
            violations.append(
                f"API line '{_line.strip()}' references {_ns}.{_sym} "
                f"({_full}), which is not an approved bridge API."
            )
            continue

        # Rule 2: arity must match the single source of truth.
        _arity_key = None
        for _k in _arity:
            if _k.lower() == _sym.lower():
                _arity_key = _k
                break
        if _arity_key is not None:
            _lo, _hi = _arity[_arity_key]
            _n_args = _count_call_args(_line)
            if _n_args < _lo or _n_args > _hi:
                violations.append(
                    f"API line '{_line.strip()}' passes {_n_args} arg(s) to "
                    f"{_sym}, but the contract expects {_lo}-{_hi}."
                )

        # Rule 3: Spawn*/Pool first-arg must never be a declared handle name.
        if _sym in _spawn_or_pool:
            _first = _first_call_arg(_line)
            if _first and _first in handle_names:
                violations.append(
                    f"API line '{_line.strip()}' passes handle '{_first}' as the "
                    f"first argument to {_sym}; the first arg must be world "
                    f"coordinates (numbers) or a pool name string."
                )

    return violations


def _count_call_args(line: str) -> int:
    """Count comma-separated args inside the first '(' ... ')' of a call line."""
    _open = line.find("(")
    if _open == -1:
        return 0
    _close = line.rfind(")")
    if _close <= _open:
        return 0
    _inner = line[_open + 1:_close].strip()
    if not _inner:
        return 0
    # Split on top-level commas only (respect nested parens/brackets).
    _depth = 0
    _count = 1
    for _ch in _inner:
        if _ch in "([{":
            _depth += 1
        elif _ch in ")]}":
            _depth = max(0, _depth - 1)
        elif _ch == "," and _depth == 0:
            _count += 1
    return _count


def _first_call_arg(line: str) -> str:
    """Return the first argument token (identifier) of a call line, or ''."""
    _open = line.find("(")
    if _open == -1:
        return ""
    _close = line.rfind(")")
    _inner = line[_open + 1:_close] if _close > _open else line[_open + 1:]
    _first = _inner.split(",", 1)[0].strip()
    # Return only a bare identifier (handles are identifiers, numbers are not).
    _m = re.match(r"^([A-Za-z_]\w*)\s*$", _first)
    return _m.group(1) if _m else ""


# -- Generation ---------------------------------------------------------------

def _generate_scaffold_for_task(
    ctx: PipelineContext,
    task: dict,
    task_id: str,
    design_block: str,
    approved_apis: str,
    lua_contract,
    handle_names: set,
) -> Optional[MechanicScaffold]:
    """Generate + validate one scaffold, re-prompting once on failure."""
    from pipeline import REASONING_MODEL, call_ollama
    from ollama_extras import is_fatal_ollama_error

    _prompt = _build_scaffold_prompt(task, design_block, approved_apis)

    for _attempt in (1, 2):
        try:
            _out = call_ollama(
                SCAFFOLD_SYSTEM,
                _prompt,
                f"Mechanics Scaffold (task {task_id}, attempt {_attempt})",
                REASONING_MODEL,
                params={"num_predict": 1024},
                skip_pre_summarizer=True,
            )
        except Exception as _e:
            print(f"  [Scaffold] ⚠ Ollama call failed for task {task_id}: {_e}")
            return None
        if is_fatal_ollama_error(_out):
            print(f"  [Scaffold] ⚠ Fatal Ollama error for task {task_id}: {_out[:160]}")
            return None

        _scaffold = _parse_scaffold(_out, task_id)
        if _scaffold is None:
            if _attempt == 1:
                _prompt += (
                    "\n\nYour previous answer was prose-only. Re-read the format "
                    "and output ONLY the 3-field scaffold (### Mechanic / INTENT / "
                    "PSEUDO / API)."
                )
                continue
            print(f"  [Scaffold] ⚠ Task {task_id}: unparseable scaffold after 2 attempts.")
            return None

        _violations = _validate_scaffold(_scaffold, lua_contract, handle_names)
        if not _violations:
            return _scaffold

        if _attempt == 1:
            _prompt += (
                "\n\nYour scaffold failed deterministic validation:\n"
                + "\n".join(f"- {v}" for v in _violations)
                + "\nFix ONLY the API: lines and re-output the scaffold."
            )
            continue
        print(f"  [Scaffold] ⚠ Task {task_id}: still invalid after re-prompt "
              f"({len(_violations)} violation(s)); using as-is: {_violations[0][:120]}")
        return _scaffold

    return None


# -- Main entry point ----------------------------------------------------------

def run_mechanics_scaffold(ctx: PipelineContext) -> PipelineContext:
    """Generate validated mechanics scaffolds for logic-bearing Lua anchor tasks.

    No-ops (returns ctx unchanged) unless ALL of:
      - MIDWAY_MECHANICS_SCAFFOLD is set to a truthy value
      - ctx.attraction_design is present
      - ctx.tasks_list contains Lua tasks with finalized anchor markers
    """
    if not _env_enabled():
        return ctx

    _design = getattr(ctx, "attraction_design", None)
    if _design is None:
        print("  [Scaffold] ℹ No attraction design present — skipping.")
        return ctx

    _lua_tasks = [
        t for t in (ctx.tasks_list or [])
        if isinstance(t, dict) and (t.get("target_file") or "").endswith(".lua")
        and (t.get("anchor_marker") or "").strip()
    ]
    if not _lua_tasks:
        print("  [Scaffold] ℹ No Lua anchor tasks found — skipping.")
        return ctx

    # Build the shared validation surfaces once.
    from contract_validator import build_lua_contract
    from _review_helpers import build_fix_bridge_snippet

    _lua_contract = build_lua_contract(_get_bridge_contract(ctx))
    _approved_apis = build_fix_bridge_snippet(ctx) or ""
    _handle_names = {
        str(getattr(_h, "name", "")).strip()
        for _h in (getattr(_design, "handles", None) or [])
        if getattr(_h, "name", "") and str(getattr(_h, "name", "")).strip()
    }
    _design_block = _design.to_context_block()

    print(f"\n{'='*60}")
    print(f"  Mechanics Scaffold: reasoning {len(_lua_tasks)} Lua task(s) → bounded scaffold")
    print(f"{'='*60}")

    _generated = 0
    for _t in _lua_tasks:
        _anchor = (_t.get("anchor_marker") or "").strip()
        _task_id = _anchor_task_number(_anchor)
        if _task_id is None:
            continue
        _hook_lower = _anchor.lower()
        if any(_kw in _hook_lower for _kw in _SKIP_HOOK_KEYWORDS):
            print(f"  [Scaffold] ⏭ Task {_task_id}: structural hook — skipped.")
            continue

        _scaffold = _generate_scaffold_for_task(
            ctx, _t, _task_id, _design_block, _approved_apis,
            _lua_contract, _handle_names,
        )
        if _scaffold is not None:
            ctx.scaffolds[_task_id] = _scaffold
            _generated += 1
            print(f"  [Scaffold] ✓ Task {_task_id}: '{_scaffold.name}' "
                  f"({len(_scaffold.api_calls)} API call(s)).")

    print(f"  [Scaffold] Done: {_generated} scaffold(s) stored on ctx.scaffolds.")
    return ctx


def _env_enabled() -> bool:
    """Truthy check for the MIDWAY_MECHANICS_SCAFFOLD feature flag (default off)."""
    _v = os.getenv("MIDWAY_MECHANICS_SCAFFOLD", "")
    return _v.strip().lower() in ("1", "true", "yes", "on")


def get_scaffold_for_task(ctx: PipelineContext, anchor_marker: str) -> Optional[MechanicScaffold]:
    """Look up the scaffold for a task by its anchor marker.  Used by
    _helpers_exec.execute_task to inject the scaffold into the coder prompt."""
    _task_id = _anchor_task_number(anchor_marker or "")
    if _task_id is None:
        return None
    return (ctx.scaffolds or {}).get(_task_id)
