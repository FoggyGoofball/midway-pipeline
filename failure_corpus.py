"""
failure_corpus.py — capture deterministic-fix deltas as future training data.
=============================================================================

When the deterministic post-processor rewrites something, that rewrite is a
*real observed failure mode* — far more valuable than synthetic examples.
This module records each fix's ``broken -> fixed`` delta so it can be
converted into LoRA training samples by ``failure_corpus_to_dataset.py``.

Design (see docs/LORA_GENERATION_DESIGN.md "runtime failure corpus"):
  - Gated behind ``MIDWAY_FAILURE_CORPUS=1`` (off by default, never in tests).
  - Writes one JSON record per applied fix to ``failure_corpus.jsonl``.
  - Deduplicated by a hash of ``(fix_id, normalized before)`` so the corpus
    only grows with NEW failure modes.
  - Captures a bounded before/after snippet around the first changed line
    (not the whole file), which is exactly what a SEARCH/REPLACE sample needs.

Language-agnostic: the corpus records a ``language`` tag (default ``"lua"``);
the converter turns records into ChatML for whichever language is tagged.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import time
from pathlib import Path
from typing import List, Tuple

# -- Configuration ------------------------------------------------------------

_ENABLE_ENV = "MIDWAY_FAILURE_CORPUS"
_PATH_ENV = "MIDWAY_FAILURE_CORPUS_PATH"

_DEFAULT_CORPUS_PATH = (
    Path(__file__).resolve().parent / "lora generator" / "failure_corpus.jsonl"
)

#: Do not grow the in-memory dedupe set unboundedly across a long run.
_MAX_SEEN_KEYS = 50_000


def is_enabled() -> bool:
    """Whether failure-corpus capture is active for this process."""
    return os.environ.get(_ENABLE_ENV, "0").strip().lower() in ("1", "true", "yes")


def corpus_path() -> Path:
    """Resolve the corpus file path (env-overridable)."""
    return Path(os.environ.get(_PATH_ENV, _DEFAULT_CORPUS_PATH))


# -- Diff extraction ----------------------------------------------------------

def extract_diff(before: str, after: str,
                 max_lines: int = 20, max_chars: int = 3000) -> Tuple[str, str]:
    """Extract a bounded before/after snippet around the first change.

    Returns ``(before_snippet, after_snippet)``.  When the inputs are equal,
    returns ``("", "")``.  The snippet includes two lines of surrounding
    context so the SEARCH side has a realistic anchor.
    """
    if before == after:
        return "", ""
    a = before.splitlines()
    b = after.splitlines()
    matcher = difflib.SequenceMatcher(None, a, b)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op not in ("replace", "delete", "insert"):
            continue
        ctx = 2
        i_start, i_end = max(0, i1 - ctx), min(len(a), i2 + ctx)
        j_start, j_end = max(0, j1 - ctx), min(len(b), j2 + ctx)
        if i_end - i_start > max_lines:
            i_end = i_start + max_lines
        if j_end - j_start > max_lines:
            j_end = j_start + max_lines
        return (
            "\n".join(a[i_start:i_end])[:max_chars],
            "\n".join(b[j_start:j_end])[:max_chars],
        )
    return "", ""


def _dedup_key(fix_id: str, before_snippet: str) -> str:
    norm = " ".join(before_snippet.split())
    return hashlib.sha1(f"{fix_id}|{norm}".encode("utf-8")).hexdigest()[:16]


# -- In-process dedupe state --------------------------------------------------

_seen_keys: set = set()
_keys_loaded: bool = False


def _load_existing_keys() -> None:
    """Load dedup keys from the existing corpus once per process (bounded)."""
    global _keys_loaded
    if _keys_loaded:
        return
    _keys_loaded = True
    path = corpus_path()
    if not path.is_file():
        return
    try:
        # Scan a bounded window (most recent 200k lines) to avoid a full read.
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                key = rec.get("dedup_key")
                if key:
                    _seen_keys.add(key)
                    if len(_seen_keys) >= _MAX_SEEN_KEYS:
                        break
    except OSError:
        pass


# -- Recording ----------------------------------------------------------------

def record_fix(fix_id: str, invariant_id: str,
               before: str, after: str,
               file_relpath: str = "", language: str = "lua") -> bool:
    """Record one applied fix to the corpus (no-op when disabled).

    Args:
        fix_id:       e.g. ``"#6"`` or ``"G2"`` (matches post-processor labels).
        invariant_id: e.g. ``"I2"`` (the reviewer invariant this fix satisfies).
        before/after: whole-file content before/after this one fix.
        file_relpath: owning file, e.g. ``attractions/strongman/strongman.lua``.
        language:     target language tag.

    Returns:
        True when a NEW record was appended, False when skipped (disabled,
        no delta, or already seen).
    """
    if not is_enabled():
        return False
    _load_existing_keys()

    before_snip, after_snip = extract_diff(before, after)
    if not before_snip and not after_snip:
        return False
    key = _dedup_key(fix_id, before_snip)
    if key in _seen_keys:
        return False
    _seen_keys.add(key)

    record = {
        "run_id": time.strftime("%Y%m%d_%H%M%S", time.localtime()),
        "fix": fix_id,
        "invariant": invariant_id,
        "language": language,
        "file": file_relpath,
        "before": before_snip,
        "after": after_snip,
        "dedup_key": key,
    }
    path = corpus_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True
    except OSError as exc:
        print(f"  [FailureCorpus] !! write failed: {exc}")
        return False


def read_corpus(path: Path = None) -> List[dict]:
    """Read all records from the corpus (for the converter / inspection)."""
    path = path or corpus_path()
    if not path.is_file():
        return []
    out: List[dict] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out
