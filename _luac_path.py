"""_luac_path.py — Resolve the luac (Lua 5.4 compiler) executable path once.

All deterministic luac syntax gates call bare ``subprocess.run(["luac", ...])``,
which silently SKIPS when the server was launched from a hidden / NoProfile
shell whose PATH dropped the per-user Lua bin entry.  This helper returns the
FULL absolute path to luac.exe so callers never depend on PATH being correct.
"""

from __future__ import annotations

import os
import shutil


def get_luac_exe() -> str:
    """Return the absolute path to luac.exe, or '' if not found.

    Resolves via shutil.which first, then falls back to well-known install
    locations.  Returning the full path means ``subprocess.run([exe, ...])``
    works even when the server process's PATH is missing the Lua bin directory.
    """
    _found = shutil.which("luac")
    if _found:
        return os.path.abspath(_found)
    _candidates = [
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Lua", "bin", "luac.exe"),
        os.path.join(os.environ.get("USERPROFILE", ""), "AppData", "Local", "Programs", "Lua", "bin", "luac.exe"),
        r"C:\Users\Admin\AppData\Local\Programs\Lua\bin\luac.exe",
        r"C:\Program Files\Lua\bin\luac.exe",
    ]
    for _c in _candidates:
        if _c and os.path.isfile(_c):
            return os.path.abspath(_c)
    return ""
