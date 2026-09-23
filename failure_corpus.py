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
import re
import time
from pathlib import Path
from typing import List, Tuple

# -- Configuration ------------------------------------------------------------

_ENABLE_ENV = "MIDWAY_FAILURE_CORPUS"
_PATH_ENV = "MIDWAY_FAILURE_CORPUS_PATH"

_DEFAULT_CORPUS_DIR = Path(__file__).resolve().parent / "lora generator"

#: Corpus sources.  "initial" = deterministic/synthetic bootstrap (seeders,
#: contract generator); "genuine" = live-observed + user-driven (the runtime
#: post-processor with MIDWAY_FAILURE_CORPUS=1).  Keeping them separate lets a
#: retrain fold genuine data on top of the initial bootstrap.
SOURCES = ("initial", "genuine")

#: Do not grow the in-memory dedupe set unboundedly across a long run.
_MAX_SEEN_KEYS = 50_000

#: Known-bad output patterns.  A record whose "after" (the fixer's claimed
#: correct output) matches one of these is POISON — it would teach the model to
#: emit broken code.  Historical example: the old Fix #31 bug emitted
#: ``AttractionConstants.modifiers or {}.luck`` (missing parens around ``or {}``).
POISON_PATTERNS = [
    re.compile(r"\{\}\s*[.\[]"),   # `or {}.field` / `or {}[idx]`
]


def looks_poisoned(text: str) -> bool:
    """True when *text* matches a known-bad output pattern."""
    if not text:
        return False
    return any(p.search(text) for p in POISON_PATTERNS)


def is_enabled() -> bool:
    """Whether failure-corpus capture is active for this process."""
    return os.environ.get(_ENABLE_ENV, "0").strip().lower() in ("1", "true", "yes")


def corpus_path(source: str = "genuine") -> Path:
    """Resolve the corpus file path for a source.

    ``MIDWAY_FAILURE_CORPUS_PATH`` (a full path) overrides everything for
    backward compatibility.  Otherwise the path is
    ``lora generator/failure_corpus.<source>.jsonl``.
    """
    if _PATH_ENV in os.environ:
        return Path(os.environ[_PATH_ENV])
    if source not in SOURCES:
        source = "genuine"
    return _DEFAULT_CORPUS_DIR / f"failure_corpus.{source}.jsonl"


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

_seen_keys_by_source: dict = {}


def _load_existing_keys(source: str) -> set:
    """Load dedup keys for one corpus source (bounded, cached per process)."""
    keys = _seen_keys_by_source.get(source)
    if keys is not None:
        return keys
    keys = set()
    path = corpus_path(source)
    if path.is_file():
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
                        keys.add(key)
                        if len(keys) >= _MAX_SEEN_KEYS:
                            break
        except OSError:
            pass
    _seen_keys_by_source[source] = keys
    return keys


# -- Recording ----------------------------------------------------------------

def record_fix(fix_id: str, invariant_id: str,
               before: str, after: str,
               file_relpath: str = "", language: str = "lua",
               source: str = "genuine") -> bool:
    """Record one applied fix to the corpus (no-op when disabled).

    Args:
        fix_id:       e.g. ``"#6"`` or ``"G2"`` (matches post-processor labels).
        invariant_id: e.g. ``"I2"`` (the reviewer invariant this fix satisfies).
        before/after: whole-file content before/after this one fix.
        file_relpath: owning file, e.g. ``attractions/strongman/strongman.lua``.
        language:     target language tag.
        source:       ``"initial"`` (deterministic seeders) or ``"genuine"``
                      (live runtime / user-driven).

    Returns:
        True when a NEW record was appended, False when skipped (disabled,
        no delta, or already seen).
    """
    if not is_enabled():
        return False
    if source not in SOURCES:
        source = "genuine"
    keys = _load_existing_keys(source)

    before_snip, after_snip = extract_diff(before, after)
    if not before_snip and not after_snip:
        return False
    # Never record a fix whose claimed-correct output is known-bad: it would
    # poison the training set (the model would learn to emit broken code).
    if looks_poisoned(after_snip):
        return False
    key = _dedup_key(fix_id, before_snip)
    if key in keys:
        return False
    keys.add(key)

    record = {
        "run_id": time.strftime("%Y%m%d_%H%M%S", time.localtime()),
        "fix": fix_id,
        "invariant": invariant_id,
        "language": language,
        "file": file_relpath,
        "before": before_snip,
        "after": after_snip,
        "source": source,
        "dedup_key": key,
    }
    path = corpus_path(source)
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
