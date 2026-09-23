"""
_tombstones.py — durable failure signatures ("tombstones") for the deterministic
pipeline.

Each tombstone records a failure pattern witnessed in real runs, with a concise
"why this approach is wrong" and the correct form. Three consumers:

  1. Preflight guards  — TOMBSTONE_GUARDS feeds `_preflight_static` so a task
     whose output hits a tombstone gets a hard, specific pre-flight error.
  2. Fix-prompt block — `render_recent_tombstones()` renders a bounded ring
     buffer of recently-hit tombstones as concise negative constraints for the
     fix agent (do NOT repeat the same failed approach).
  3. LoRA corpus       — the durable store that will eventually become training
     samples (deferred; see docs/LORA_GENERATION_DESIGN.md).

Design principle: specific + stable + bounded beats verbose + regenerated. Each
entry is a one-line prohibition plus a one-line reason.
"""

from __future__ import annotations

import re
from typing import List, Tuple

# (domain_filter, compiled_regex, short_label, why_it_is_wrong)
# domain_filter: None = all domains, otherwise only that agent.
TOMBSTONE_GUARDS: List[Tuple] = [
    (
        None,
        re.compile(r'<<<<<<<|=======|>>>>>>>'),
        "SEARCH/REPLACE scaffolding leaked into code",
        "apply the patch — never write the <<<<<<< SEARCH / ======= / >>>>>>> REPLACE markers into the file; they are not Lua.",
    ),
    (
        "Lua",
        re.compile(r'\b(?:end|then|else|if|do|while|for|function|local|return|repeat|until){2,}\b', re.IGNORECASE),
        "keywords concatenated (e.g. `endend`, `thenif`)",
        "each keyword is its own token; never run two block keywords together — split onto properly-indented lines.",
    ),
    (
        "Lua",
        re.compile(r'"[A-Za-z_]\w*"\s*:'),
        "JSON table syntax in Lua",
        "Lua tables use `key = value`, never JSON `\"key\":`.",
    ),
    (
        "Lua",
        re.compile(r'\b\w+:(?:MidwayPhysics|Engine)\.\w+\s*\('),
        "engine method called with `:`",
        "engine APIs are flat: call `MidwayPhysics.Method(handle, ...)`, never `handle:MidwayPhysics.Method(...)`.",
    ),
    (
        None,
        re.compile(r'^\s*\d+\s*\|', re.MULTILINE),
        "line-number gutters pasted into code",
        "do not paste ` 123 |` editor gutters into the source — emit plain code.",
    ),
    (
        None,
        re.compile(r'^```', re.MULTILINE),
        "markdown code fences inside a source file",
        "the file body is raw code only — no ``` fences.",
    ),
]


def tombstone_lines_from_text(text: str) -> List[str]:
    """Return `- ✗ <label> — <why>` lines for tombstones whose label appears in `text`."""
    if not text:
        return []
    out: List[str] = []
    seen: set = set()
    for _d, _p, _label, _why in TOMBSTONE_GUARDS:
        if _label in text and _label not in seen:
            seen.add(_label)
            out.append(f"- ✗ {_label} — {_why}")
    return out


def push_recent_tombstones(ctx, lines: List[str], max_entries: int = 3) -> None:
    """Append tombstone lines to ctx's bounded ring buffer (deduped, last-N kept)."""
    if not lines:
        return
    _buf = getattr(ctx, '_recent_tombstones', None)
    if _buf is None:
        _buf = []
        ctx._recent_tombstones = _buf
    for _ln in lines:
        if _ln not in _buf:
            _buf.append(_ln)
    if len(_buf) > max_entries:
        _buf[:] = _buf[-max_entries:]


def render_recent_tombstones(ctx) -> str:
    """Render the bounded tombstone block ('' when empty)."""
    _buf = getattr(ctx, '_recent_tombstones', None)
    if not _buf:
        return ""
    return (
        "## RECENT TOMBSTONES (approaches that already failed — do NOT repeat them):\n"
        + "\n".join(_buf)
    )
