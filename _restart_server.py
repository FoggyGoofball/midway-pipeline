#!/usr/bin/env python3
"""
_restart_server.py — Detached launcher for the pipeline stream server.

Spawned by the /api/restart endpoint (with DETACHED_PROCESS), it waits a
moment for the old server to fully exit, then starts a fresh
``pipeline_stream_server.py`` in its own console window.  This lets the
dashboard remotely kill + restart the server without taking the React dev
server (or anything else) down with it.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

# Windows process-creation flags.
CREATE_NEW_CONSOLE = 0x00000010
CREATE_NEW_PROCESS_GROUP = 0x00000200


def main() -> None:
    root = Path(__file__).resolve().parent
    time.sleep(2.0)  # give the old server time to release :8765
    flags = CREATE_NEW_CONSOLE | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        [sys.executable, "pipeline_stream_server.py"],
        cwd=str(root),
        creationflags=flags,
        close_fds=True,
    )


if __name__ == "__main__":
    main()
