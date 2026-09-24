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


# ── External knowledge fallback (Google AI, anonymized) ──────────────────────
# The arbiter can outsource GENERAL questions (API conflicts / unknown concepts)
# to a Google AI answer.  Data is anonymized: never send exact code, file paths,
# line numbers, or project-specific identifiers — always generalize.

_GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)

#: Project-specific identifiers -> generic descriptors (replaced before any
#: query leaves the machine).
_ANONYMIZE = [
    ("MidwayPhysics", "a physics engine bridge API"),
    ("MidwayInput", "an input API"),
    ("AttractionConstants", "a shared constants module"),
    ("Midway to Nowhere", "a physics arcade game"),
]

_CODE_LINE_RE = re.compile(
    r"^\s*(?:local\b|function\b|if\b|then\b|else\b|elseif\b|for\b|while\b|"
    r"do\b|end\b|return\b|--|#\b)"
)

#: Common dotted abbreviations to PRESERVE (not code).
_DOTTED_DENYLIST = {"e.g", "i.e", "etc", "vs", "a.k.a", "w.r.t"}

#: Inline call pattern: `identifier(...)` whose argument list contains a digit,
#: comma, or quote — i.e. real code, not prose like "answer (briefly)".
_INLINE_CALL_RE = re.compile(r"\b[A-Za-z_]\w*\s*\((?:[^()\n]*?[\d,\"'])[^()\n]*\)")


def _scrub_dotted(m):
    tok = m.group(0)
    return tok if tok.lower().rstrip(".") in _DOTTED_DENYLIST else "a field access"


def generalize_query(raw: str) -> str:
    """Anonymize a question for external consumption.

    Strips fenced code, code-looking lines, inline call/field patterns, file
    paths, line-number references, and project-specific identifiers, leaving a
    general domain question.
    """
    if not raw:
        return ""
    text = raw
    # 1. Drop fenced code blocks entirely.
    text = re.sub(r"```[\s\S]*?```", " ", text)
    # 2. Drop code-looking lines.
    lines = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if _CODE_LINE_RE.match(s):
            continue
        lines.append(s)
    text = " ".join(lines)
    # 3. Strip file paths and line-number references.
    text = re.sub(r"\b[\w.-]+[\\/][\w.\\/-]*\.[A-Za-z0-9]+", "a source file", text)
    text = re.sub(r"\b[\w.-]+\.(?:lua|py|cpp|c|h|hpp|md)\b", "a source file", text)
    text = re.sub(r"\b(?:line|ln)\s*\d+\b", "", text, flags=re.IGNORECASE)
    # 4. Scrub namespace-qualified members (MidwayPhysics.Foo -> generic)
    #    BEFORE the bare-identifier pass, so the `.Foo` is consumed too.
    for ns, generic in _ANONYMIZE:
        text = re.sub(re.escape(ns) + r"\.[A-Za-z_]\w*", generic + " function",
                      text, flags=re.IGNORECASE)
    # 5. Scrub inline call patterns (Foo(0, 1, "x") -> a function call).
    text = _INLINE_CALL_RE.sub("a function call", text)
    # 6. Scrub dotted field chains (mods.heat -> a field access).
    text = re.sub(r"\b[a-z_]\w*(?:\.[a-z_]\w*)+\b", _scrub_dotted, text)
    # 7. Replace bare project identifiers.
    for ident, generic in _ANONYMIZE:
        text = re.sub(re.escape(ident), generic, text, flags=re.IGNORECASE)
    # 8. Collapse whitespace.
    return " ".join(text.split())


def web_lookup(raw_question: str) -> Tuple[bool, str]:
    """Answer a question via Google Gemini, with the query anonymized first.

    Returns ``(answered, answer)``.  ``(False, "")`` when GOOGLE_API_KEY is not
    set or the call fails.  NEVER sends raw code or project identifiers.
    """
    api_key = os.environ.get("GOOGLE_API_KEY", "").strip()
    if not api_key:
        return False, ""
    question = generalize_query(raw_question)
    if not question:
        return False, ""
    model = os.environ.get("MIDWAY_WEB_MODEL", "gemini-2.0-flash")
    prompt = (
        "You are a programming knowledge assistant. Answer the following GENERAL "
        "question in 2-4 sentences. Do not reference any proprietary identifiers "
        "or source code.\n\nQuestion: " + question
    )
    import json as _json
    import urllib.request as _ur
    body = _json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode("utf-8")
    req = _ur.Request(
        _GEMINI_ENDPOINT.format(model=model) + "?key=" + api_key,
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with _ur.urlopen(req, timeout=30) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
        parts = (data.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
        answer = " ".join((p.get("text") or "") for p in parts).strip()
        return (True, answer) if answer else (False, "")
    except Exception:
        return False, ""
