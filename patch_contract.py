"""patch_contract.py — Language-agnostic fix-output contract enforcement.

Why this exists
---------------
The model's fix output is UNTRUSTED.  Rather than persuading it not to
"waffle" (emit prose, delegation signals, or whole-file echoes instead of a
minimal patch), we validate the output against a machine-checkable contract
and reject + re-prompt with a deterministic, rule-specific correction.

Only TWO things are language-specific, and both are supplied by the caller:

  * ``syntax_check(text) -> (ok, stderr)``  -- e.g. ``luac -p``, a compiler,
    ``ast.parse``, ``tsc --noEmit``.
  * ``extract_hunks`` / ``apply_hunk``       -- the patch format (SEARCH/REPLACE,
    unified diff, ...).

Everything else — delegation signals, patch-size ratio, has-code, escalation —
is shared across languages.  Adding a new language/cartridge means supplying
one syntax checker and one patch format; no new prompt engineering.

This module is deliberately dependency-light so it can be imported without
pulling in the rest of the pipeline (no circular imports).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

# A hunk is a small dict with "search" and "replace" string keys.
Hunk = dict
ExtractHunks = Callable[[str], list[Hunk]]
ApplyHunk = Callable[[str, str, str], str]  # (file_text, search, replace) -> new text
SyntaxCheck = Callable[[str], tuple[bool, str]]
HasCode = Callable[[str], bool]


class Rule(str, Enum):
    """The deterministic reasons a fix output can be rejected.

    Each rule maps to a MACHINE-GENERATED correction message (see CORRECTIONS),
    so the re-prompt is produced by the pipeline, never hand-written per task.
    """
    DELEGATION = "delegation"      # output is a [DELEGATE]/[QUERY]/[CONF] signal, not code
    FORMAT = "format"              # no parseable patch hunks
    WHOLE_FILE = "whole_file"      # a hunk's SEARCH side is (almost) the entire file
    NO_MATCH = "no_match"          # hunks parsed but none applied to the current file
    NO_CODE = "no_code"            # patch adds no code-like content
    SYNTAX = "syntax"              # patched file fails the language checker


@dataclass(frozen=True)
class FixVerdict:
    """Result of validating a fix output against a contract.

    ``correction`` is the deterministic re-prompt text to send back to the
    agent when ``ok`` is False.  It is derived from the failed rule, so it is
    language- and attraction-agnostic.
    """
    ok: bool
    failed_rule: Optional[Rule] = None
    detail: str = ""

    @property
    def correction(self) -> str:
        return CORRECTIONS.get(self.failed_rule, "")


# ---------------------------------------------------------------------------
# Deterministic correction messages (rule -> re-prompt text)
# ---------------------------------------------------------------------------

CORRECTIONS: dict[Rule, str] = {
    Rule.DELEGATION: (
        "Your response was a delegation/query signal with no code. "
        "Do NOT emit [DELEGATE], [QUERY:...], or [CONF]. Emit exactly one "
        "SEARCH/REPLACE block that fixes the defect."
    ),
    Rule.FORMAT: (
        "Your response contained no parseable patch. Emit exactly one block:\n"
        "<<<<<<< SEARCH\n<exact current lines>\n=======\n<corrected lines>\n"
        ">>>>>>> REPLACE\nDo not add prose or commentary."
    ),
    Rule.WHOLE_FILE: (
        "Your SEARCH block covered almost the entire file. That is a whole-file "
        "rewrite, not a patch. Emit a MINIMAL hunk that changes only the lines "
        "implicated by the defect."
    ),
    Rule.NO_MATCH: (
        "Your SEARCH text did not match the current file, so the patch could not "
        "be applied. Copy the exact current lines from the file into SEARCH, then "
        "provide the corrected lines."
    ),
    Rule.NO_CODE: (
        "Your replacement contained no code. Provide concrete implementation lines, "
        "not comments, TODOs, or prose."
    ),
    Rule.SYNTAX: (
        "Your patched file failed the language syntax check. Fix the syntax error "
        "and re-emit the patch."
    ),
}


# ---------------------------------------------------------------------------
# Default patch format: SEARCH/REPLACE (canonical for this pipeline)
# ---------------------------------------------------------------------------

_SEARCH_REPLACE_RE = re.compile(
    r"<{5,9}\s*SEARCH\s*\n(.*?)\n\s*={5,9}\s*\n(.*?)(?=\n\s*>{5,9}\s*REPLACE|$)",
    re.DOTALL,
)


def extract_search_replace_hunks(model_out: str) -> list[Hunk]:
    """Parse SEARCH/REPLACE blocks into [{search, replace}, ...].

    Returns an empty list when the output is prose/free-form (i.e. "waffle").
    """
    if not model_out:
        return []
    hunks: list[Hunk] = []
    for m in _SEARCH_REPLACE_RE.finditer(model_out):
        search = m.group(1).strip("\n")
        replace = m.group(2).strip("\n")
        if search or replace:
            hunks.append({"search": search, "replace": replace})
    return hunks


def apply_hunk_exact(file_text: str, search: str, replace: str) -> str:
    """Exact-match apply of a single hunk.  Returns file_text unchanged on no match."""
    if not search:
        return file_text
    if search not in file_text:
        return file_text
    return file_text.replace(search, replace, 1)


# ---------------------------------------------------------------------------
# Default has-code heuristic (permissive, language-flavored by default)
# ---------------------------------------------------------------------------

_CODE_LINE_RE = re.compile(
    r"^\s*(?:"
    r"function\b|local\b|return\b|if\b|for\b|while\b|else\b|end\b|"
    r"def\b|class\b|import\b|let\b|const\b|var\b|"
    r"[A-Za-z_][\w.]*\s*[=:]"
    r")",
    re.MULTILINE,
)


def has_code_default(text: str) -> bool:
    """True if *text* contains a code fence or at least one code-like line.

    Permissive by design: it only exists to reject pure-prose/TODO output, not
    to police style.  Pass a stricter per-language callable if needed.
    """
    if not text:
        return False
    if "```" in text:
        return True
    return bool(_CODE_LINE_RE.search(text))


# ---------------------------------------------------------------------------
# PatchContract
# ---------------------------------------------------------------------------

@dataclass
class PatchContract:
    """A machine-checkable fix-output contract, independent of language.

    ``validate`` runs the rules in order and returns the FIRST failed rule so
    the caller can reject and re-prompt with a deterministic correction.
    """
    extract_hunks: ExtractHunks = extract_search_replace_hunks
    apply_hunk: ApplyHunk = apply_hunk_exact
    syntax_check: Optional[SyntaxCheck] = None
    has_code: HasCode = has_code_default
    delegation_signals: tuple[str, ...] = ("[DELEGATE", "[QUERY:", "[CONF")
    max_patch_ratio: float = 0.8   # SEARCH covering >= this fraction of file = whole-file echo
    context_window_lines: int = 28  # advisory: lines to show around the defect

    def validate(self, model_out: str, current_file: str) -> FixVerdict:
        """Run the validator chain; return the first failing rule (or ok)."""
        if not model_out or not model_out.strip():
            return FixVerdict(False, Rule.NO_CODE, "empty output")

        # 1. Delegation signals (fast reject, no parsing needed).
        for sig in self.delegation_signals:
            if sig in model_out:
                return FixVerdict(False, Rule.DELEGATION, f"signal: {sig}")

        # 2. Format: must parse into >=1 hunk.
        hunks = self.extract_hunks(model_out)
        if not hunks:
            return FixVerdict(False, Rule.FORMAT, "no parseable hunks")

        # 3. Whole-file echo: a hunk whose SEARCH spans ~the entire file.
        cur_lines = (current_file.count("\n") + 1) if current_file else 0
        for h in hunks:
            s = h.get("search", "") or ""
            if cur_lines and (s.count("\n") + 1) >= int(cur_lines * self.max_patch_ratio):
                return FixVerdict(
                    False, Rule.WHOLE_FILE,
                    f"search spans {s.count(chr(10)) + 1}/{cur_lines} lines",
                )

        # 4. Apply: at least one hunk must change the file.
        patched = current_file
        applied = 0
        for h in hunks:
            new = self.apply_hunk(patched, h.get("search", ""), h.get("replace", ""))
            if new != patched:
                patched = new
                applied += 1
        if not applied:
            return FixVerdict(False, Rule.NO_MATCH, "no hunk applied")

        # 5. Has code: the replacement must be substantive.
        if not self.has_code(model_out):
            return FixVerdict(False, Rule.NO_CODE, "no code-like content")

        # 6. Syntax: patched file must pass the language checker (if provided).
        if self.syntax_check is not None:
            ok, err = self.syntax_check(patched)
            if not ok:
                return FixVerdict(False, Rule.SYNTAX, err[:500])

        return FixVerdict(True)


    def reject_waffle(self, model_out: str) -> Optional[Rule]:
        """Run only the file-independent anti-waffle checks.

        Returns the first failed rule (DELEGATION / NO_CODE / FORMAT) or None if
        the output plausibly contains a patch.  Useful for early rejection at a
        point in the pipeline where the current file text is not yet available;
        the file-dependent checks (WHOLE_FILE / NO_MATCH / SYNTAX) run later via
        ``validate``.
        """
        if not model_out or not model_out.strip():
            return Rule.NO_CODE
        for sig in self.delegation_signals:
            if sig in model_out:
                return Rule.DELEGATION
        # Pure prose: no hunks AND no code-like content => waffle.
        if not self.extract_hunks(model_out) and not self.has_code(model_out):
            return Rule.NO_CODE
        return None


# ---------------------------------------------------------------------------
# Lua adapter (Midway cartridge)
# ---------------------------------------------------------------------------

def lua_syntax_check(luac_exe: Optional[str] = None) -> SyntaxCheck:
    """Return a syntax checker that runs ``luac -p`` on the text.

    ``luac_exe`` may be omitted; the canonical path is resolved lazily via the
    project's ``_luac_path`` helper so this module stays import-safe standalone.
    """
    def _check(text: str) -> tuple[bool, str]:
        import subprocess
        import tempfile
        import os as _os
        _exe = luac_exe
        if not _exe:
            try:
                from _luac_path import get_luac_exe
                _exe = get_luac_exe() or "luac"
            except Exception:
                _exe = "luac"
        _fd, _tmp = tempfile.mkstemp(suffix=".lua")
        try:
            with _os.fdopen(_fd, "w", encoding="utf-8") as _fh:
                _fh.write(text)
            _r = subprocess.run([_exe, "-p", _tmp],
                                capture_output=True, text=True, timeout=30)
        except FileNotFoundError:
            return True, ""  # luac absent: accept (caller's deterministic post-process handles it)
        except Exception as _e:
            return False, str(_e)
        finally:
            try:
                _os.unlink(_tmp)
            except Exception:
                pass
        return _r.returncode == 0, _r.stderr.strip()
    return _check


def for_lua(luac_exe: Optional[str] = None) -> PatchContract:
    """A PatchContract wired for the Midway Lua cartridge."""
    return PatchContract(
        syntax_check=lua_syntax_check(luac_exe),
        context_window_lines=28,
        max_patch_ratio=0.8,
    )
