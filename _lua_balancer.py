"""
_lua_balancer.py -- Deterministic Lua block-balance repair.

Fixes the mechanical syntax errors that a per-task LLM fix cycle cannot
reliably repair: a stray ``end`` (block depth goes negative) or a missing
``end`` at EOF (depth stays positive). Pure Python, no model call, and
idempotent -- it only removes provably-surplus closers and appends provably
missing ones.

Keyword accounting (Lua 5.4):
    openers : function, if, do, repeat
    closers : end, until

``do`` covers ``for``/``while`` loops (which always require ``do``) and bare
``do`` blocks, so counting ``do`` once avoids the classic for..do double
count. ``if`` closes with ``end`` (``then``/``elseif``/``else`` add no
depth), and ``repeat`` closes with ``until``. All of these are reserved
keywords, so they can never appear as identifiers.
"""

from __future__ import annotations

import re
from typing import List, Tuple

_OPEN_RE = re.compile(r'\bfunction\b|\bif\b|\bdo\b|\brepeat\b')
_CLOSE_RE = re.compile(r'\bend\b|\buntil\b')

# Never attempt to balance text that is still carrying diff markers -- that
# is a patch artifact, not Lua source, and balancing it could corrupt the file.
_CONFLICT_MARKERS = ("<<<<<<<", "=======", ">>>>>>>")


def _mask_noise(text: str) -> str:
    """Return a copy of ``text`` with comments and string literals blanked.

    Character positions are preserved (only non-newline chars are blanked) so
    token offsets found on the masked copy map 1:1 back to the original.
    Handles ``--`` line comments, ``--[[ ]]`` / ``--[=[ ]=]`` long comments,
    ``\"...\"`` / ``'...'`` strings, and ``[[ ]]`` / ``[=[ ]=]`` long strings.
    """
    chars = list(text)
    n = len(chars)
    i = 0
    while i < n:
        c = chars[i]
        # Line comment: -- ... (possibly a long comment --[[ ... ]] or --[=[ ... ]=]).
        if c == '-' and i + 1 < n and chars[i + 1] == '-':
            j = i + 2
            if j < n and chars[j] == '[':
                # Long comment: --[[ ]] (level 0) or --[=[ ]=] (level N).
                eq = 0
                k = j + 1
                while k < n and chars[k] == '=':
                    eq += 1
                    k += 1
                if k < n and chars[k] == '[':
                    close = ']' + ('=' * eq) + ']'
                    end = text.find(close, k + 1)
                    stop = end + len(close) if end != -1 else n
                    for x in range(i, min(stop, n)):
                        if chars[x] != '\n':
                            chars[x] = ' '
                    i = stop
                    continue
            # Ordinary line comment: blank to end of line.
            while i < n and chars[i] != '\n':
                chars[i] = ' '
                i += 1
            continue
        # Long string: [[ ]] (level 0) or [=[ ]=] (level N).
        if c == '[' and i + 1 < n:
            if chars[i + 1] == '[':
                eq = 0
                body_start = i + 2
                close = ']]'
            elif chars[i + 1] == '=':
                eq = 0
                k = i + 1
                while k < n and chars[k] == '=':
                    eq += 1
                    k += 1
                if k < n and chars[k] == '[':
                    body_start = k + 1
                    close = ']' + ('=' * eq) + ']'
                else:
                    i += 1
                    continue
            else:
                i += 1
                continue
            end = text.find(close, body_start)
            stop = end + len(close) if end != -1 else n
            for x in range(i, min(stop, n)):
                if chars[x] != '\n':
                    chars[x] = ' '
            i = stop
            continue
        # Short string: "..." or '...'
        if c in ('"', "'"):
            quote = c
            j = i + 1
            while j < n:
                if chars[j] == '\\':
                    j += 2
                    continue
                if chars[j] == quote:
                    break
                j += 1
            stop = j + 1 if j < n else n
            for x in range(i, min(stop, n)):
                if chars[x] != '\n':
                    chars[x] = ' '
            i = stop
            continue
        i += 1
    return ''.join(chars)


def balance_lua_blocks(text: str) -> Tuple[str, List[str]]:
    """Remove surplus ``end``/``until`` and append missing ``end`` tokens.

    Returns ``(fixed_text, actions)`` where ``actions`` is a list of
    human-readable descriptions of every change made (empty when the file was
    already balanced and nothing changed).
    """
    if not text:
        return text, []
    if any(m in text for m in _CONFLICT_MARKERS):
        return text, []

    masked = _mask_noise(text)
    tokens: List[Tuple[int, int, str]] = []
    for m in _OPEN_RE.finditer(masked):
        tokens.append((m.start(), m.end(), 'open'))
    for m in _CLOSE_RE.finditer(masked):
        tokens.append((m.start(), m.end(), 'close'))
    tokens.sort(key=lambda t: t[0])

    depth = 0
    surplus: List[Tuple[int, int]] = []
    for start, end, kind in tokens:
        if kind == 'open':
            depth += 1
        else:
            depth -= 1
            if depth < 0:
                surplus.append((start, end))
                depth = 0

    actions: List[str] = []
    if surplus:
        out = list(text)
        for start, end in sorted(surplus, reverse=True):
            actions.append(f"removed surplus '{text[start:end]}' near char {start}")
            for x in range(start, end):
                out[x] = ' '
        text = ''.join(out)

    if depth > 0:
        if not text.endswith('\n'):
            text += '\n'
        text += 'end\n' * depth
        actions.append(f"appended {depth} missing 'end' block closer(s)")

    return text, actions
