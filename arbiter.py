"""
arbiter.py — the local supreme arbiter for the appellate debate.

The Tribunal (a TRUE reasoning model — DeepSeek-R1-Distill-Qwen-7B by default)
can, during its analysis, emit clarification questions instead of an immediate
MERGE/REJECT verdict:

    [QUESTION:oracle:<factual question>]     -> answered DETERMINISTICALLY (no LLM)
    [QUESTION:coder:<design-intent question>] -> answered by the coder (one call)

This lets the arbiter interrogate the code — verify scope, API existence, and
arity against ground truth — before rendering a binding verdict.  Factual
questions are routed to a deterministic oracle so a 7B model cannot be lied to.

Self-contained: imports only stdlib + api_namespace_registry (no pipeline
imports), so it is unit-testable in isolation.
"""

from __future__ import annotations

import re
import os
import subprocess
import tempfile
from typing import Dict, List, Optional, Tuple

try:
    import api_namespace_registry as _reg
except Exception:  # pragma: no cover
    _reg = None

# [QUESTION:target:the question text]
_QUESTION_RE = re.compile(r"\[QUESTION\s*:\s*(\w+)\s*:\s*([^\]]+)\]", re.IGNORECASE)

# R1-style reasoning models wrap their chain-of-thought in <think>...</think>
# (Ollama's deepseek-r1 also uses <｜end▁of▁thinking｜>...  blocks).  Strip both so the
# verdict parser never matches text inside the model's private reasoning.
_THINK_RE = re.compile(r"<think[\s\S]*?</think>", re.IGNORECASE)

# Function-body extraction, same shape as preflight C11 / Fix #32.
_FN_RE = re.compile(
    r"^(?:local\s+)?function\s+(\w+)\s*\([^)]*\)(.*?)^end\b",
    re.DOTALL | re.MULTILINE,
)

# Local declaration (only names that are actually declared `local NAME`).
_LOCAL_RE = re.compile(r"^\s*local\s+([A-Za-z_]\w*)\b", re.MULTILINE)

_SIG_CACHE: Optional[Dict[str, str]] = None


def _load_signatures() -> Dict[str, str]:
    """Build a {lowercased_name: full_signature} map from the bridge contract.

    Keys look like ``"SpawnStaticBox(lx, ly, lz, w, h, d) -> handle"``.  Both
    the bare name and the namespace-qualified name (``MidwayPhysics.SpawnStaticBox``)
    resolve to the same signature string.
    """
    global _SIG_CACHE
    if _SIG_CACHE is not None:
        return _SIG_CACHE
    sigs: Dict[str, str] = {}
    try:
        from cartridges.midway_data_refs import build_bridge_contract
        c = build_bridge_contract()
        sections = (
            ("midwayphysics_spawn_api", "MidwayPhysics"),
            ("object_pools", ""),
            ("economy_api", "Engine"),
            ("input_api", "MidwayInput"),
        )
        for section, ns in sections:
            for key in c.get(section, {}):
                if "(" not in key:
                    continue
                name = key.split("(", 1)[0].strip()
                if not name:
                    continue
                sigs[name.lower()] = key
                if ns:
                    sigs[f"{ns}.{name}".lower()] = key
    except Exception:  # pragma: no cover - contract import may be heavy/fail
        pass
    _SIG_CACHE = sigs
    return sigs


def extract_questions(text: str) -> List[Tuple[str, str]]:
    """Extract ``[(target, question), ...]`` from a tribunal response."""
    if not text:
        return []
    return [(m.group(1).lower(), m.group(2).strip()) for m in _QUESTION_RE.finditer(text or "")]


def strip_thinking(text: str) -> str:
    """Remove R1-style chain-of-thought blocks so verdict parsing sees only the
    model's actual answer, not its private reasoning."""
    if not text:
        return ""
    out = _THINK_RE.sub("", text or "")
    # Ollama's deepseek-r1 template may also wrap the answer in `  response`/`  answer`.
    out = re.sub(r"^  response\s*", "", out, flags=re.IGNORECASE)
    out = re.sub(r"^  answer\s*", "", out, flags=re.IGNORECASE)
    return out.strip()


def build_clarification_block(log: List[Tuple[str, str]]) -> str:
    """Render the accumulated Q&A log for inclusion in the next round's prompt."""
    if not log:
        return ""
    lines = ["## Clarification Log (from this debate)", ""]
    for i, (q, a) in enumerate(log, 1):
        lines.append(f"Q{i}: {q}")
        lines.append(f"A{i}: {a}")
        lines.append("")
    return "\n".join(lines)


def _find_local_declarations(name: str, code: str) -> List[Tuple[str, int]]:
    """Return ``[(enclosing_function_or_'module', line_number), ...]`` for every
    ``local <name>`` declaration in *code*."""
    if not code or not name:
        return []
    spans = [(m.group(1), m.start(2), m.end(2)) for m in _FN_RE.finditer(code)]
    results: List[Tuple[str, int]] = []
    for m in _LOCAL_RE.finditer(code):
        if m.group(1) != name:
            continue
        pos = m.start()
        line = code.count("\n", 0, pos) + 1
        owner = "module"
        for fn_name, s, e in spans:
            if s <= pos < e:
                owner = fn_name
                break
        results.append((owner, line))
    return results


def _signature_for(symbol: str) -> Optional[str]:
    sigs = _load_signatures()
    key = (symbol or "").strip().lower()
    # Strip a namespace prefix so MidwayPhysics.SpawnStaticBox == SpawnStaticBox.
    if "." in key:
        key = key.rsplit(".", 1)[-1]
    return sigs.get(key)


def _luac_ok(code: str) -> Optional[bool]:
    """Run the Lua 5.4 compiler on *code*.  None when luac is unavailable."""
    luac = os.environ.get("MIDWAY_LUAC_PATH", "")
    if not luac and os.name == "nt":
        candidates = [
            r"C:\Users\Admin\AppData\Local\Programs\Lua\bin\luac.EXE",
        ]
        for cand in candidates:
            if os.path.isfile(cand):
                luac = cand
                break
    if not luac or not os.path.isfile(luac):
        return None
    fd, tmp = tempfile.mkstemp(suffix=".lua")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(code or "")
        r = subprocess.run([luac, "-p", tmp], capture_output=True, text=True, timeout=30)
        return r.returncode == 0
    except Exception:
        return None
    finally:
        try:
            os.unlink(tmp)
        except Exception:
            pass


def _api_existence_answer(symbol: str) -> Optional[str]:
    """Answer "is <symbol> a known API?" from the registry + contract.  None
    when the symbol could not be identified."""
    if not symbol:
        return None
    sig = _signature_for(symbol)
    if sig is not None:
        return f"YES — {symbol} is a known API: {sig}"
    # Namespaced members not in the contract: consult the registry.
    if _reg is not None and "." in symbol:
        ns, _, member = symbol.partition(".")
        for cand_ns, members in _reg.get_namespaces().items():
            if cand_ns.lower() == ns.lower():
                if member in members:
                    return f"YES — {symbol} is a known {cand_ns} member."
                return f"NO — {ns} has no member '{member}'. Known: {', '.join(sorted(members))}"
    # Bare symbol: is it a registry global?
    if _reg is not None:
        globals_ = _reg.get_globals()
        if symbol in globals_:
            return f"YES — {symbol} is a known global."
        qualified = _reg.bare_name_to_qualified()
        if symbol in qualified:
            return f"YES — {symbol} resolves to {qualified[symbol]}."
    return None


def answer_oracle_question(question: str, code: Optional[str] = None) -> Tuple[bool, str]:
    """Best-effort deterministic answer to a factual tribunal question.

    Returns ``(answered, answer)``.  ``answered=False`` means the oracle cannot
    answer deterministically — the caller should route the question to the coder.
    """
    q = (question or "").strip()
    ql = q.lower()
    if not ql:
        return False, ""

    # ── Syntax / compile validity ─────────────────────────────────────────
    if any(k in ql for k in ("syntax", "compile", "valid lua", "luac", "parse")):
        if code is None:
            return False, ""
        ok = _luac_ok(code)
        if ok is None:
            return False, "luac is not available on this host."
        if ok:
            return True, "The file is syntactically VALID (luac -p returned 0)."
        # Re-run to capture the error line.
        luac = os.environ.get("MIDWAY_LUAC_PATH", r"C:\Users\Admin\AppData\Local\Programs\Lua\bin\luac.EXE")
        fd, tmp = tempfile.mkstemp(suffix=".lua")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(code or "")
            r = subprocess.run([luac, "-p", tmp], capture_output=True, text=True, timeout=30)
            return True, f"The file is INVALID Lua: {r.stderr.strip()[:300]}"
        except Exception:
            return True, "The file is INVALID Lua (luac -p returned non-zero)."
        finally:
            try:
                os.unlink(tmp)
            except Exception:
                pass

    # ── API existence ─────────────────────────────────────────────────────
    if any(k in ql for k in ("exist", "real api", "valid api", "known api", "phantom", "is a method")):
        syms = re.findall(r"\b((?:MidwayPhysics|MidwayInput|Engine)\.\w+)\b", q)
        if not syms:
            # bare PascalCase/camelCase symbol near the question words
            syms = re.findall(r"\b([A-Z][A-Za-z0-9_]*|[a-z][A-Za-z0-9_]*)\b", q)
        seen = set()
        answers = []
        for s in syms:
            if s.lower() in seen:
                continue
            seen.add(s.lower())
            ans = _api_existence_answer(s)
            if ans:
                answers.append(ans)
        if answers:
            return True, "\n".join(answers)
        return False, ""

    # ── Arity / signature ─────────────────────────────────────────────────
    if any(k in ql for k in ("arg", "arity", "parameter", "signature", "take")):
        syms = re.findall(r"\b((?:MidwayPhysics|MidwayInput|Engine)\.\w+)\b", q)
        if not syms:
            syms = re.findall(r"\b(?:CreatePool|PoolAcquire|PoolReturn|PoolFree|PoolTotal|PoolCullBelow|"
                              r"Spawn[A-Za-z]+|ApplyImpulse|ApplyAngularImpulse|SetLinearVelocity|"
                              r"SetMass|MoveKinematic|GetPosition|DestroyBody)\b", q)
        answers = []
        for s in syms:
            sig = _signature_for(s)
            if sig is not None:
                answers.append(f"{s}: {sig}")
        if answers:
            return True, "\n".join(answers)
        return False, ""

    # ── Scope / declaration location ──────────────────────────────────────
    if any(k in ql for k in ("scope", "declared", "module", "hoist", "where is", "out of scope", "in scope")):
        # Candidate names: words in the question that are actually declared.
        declared = set(_LOCAL_RE.findall(code or ""))
        candidates = [w for w in re.findall(r"\b[A-Za-z_]\w*\b", q) if w in declared]
        if not candidates:
            return False, ""
        parts = []
        for name in candidates:
            decls = _find_local_declarations(name, code or "")
            if not decls:
                continue
            owners = ", ".join(f"{owner} (line {line})" for owner, line in decls)
            scope = "MODULE scope" if any(o == "module" for o, _ in decls) else "FUNCTION scope"
            parts.append(f"`{name}` is declared at {owners} → {scope}.")
        if parts:
            return True, "\n".join(parts)
        return False, ""

    return False, ""
