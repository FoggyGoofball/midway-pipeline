"""
ollama_extras.py -- Standalone Ollama utilities extracted from ollama_client.py.

  unload_model()          -- explicitly flush a model from VRAM via keep_alive=0
  _FATAL_SENTINELS        -- tuple of bracketed error prefixes
  is_fatal_ollama_error() -- detect sentinel error strings in model output

These functions have no circular dependency on ollama_client.py.
All names are re-exported by ollama_client.py for backward compatibility.
"""

from __future__ import annotations
import json
import urllib.request
import urllib.error


def unload_model(model_name: str) -> bool:
    """Explicitly unload a model from VRAM (keep_alive=0) and deregister it.

    Args:
        model_name: The model name to unload (e.g. 'qwen2.5-coder:7b').

    Returns:
        True if the request succeeded, False otherwise.
    """
    from ollama_config import OLLAMA_HOST
    from vram_budget import unregister_model as _vram_unregister
    payload = json.dumps({
        "model": model_name,
        "keep_alive": "0",
        "messages": [{"role": "user", "content": ""}],
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA_HOST}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
        print(f"  [VRAM Flush] Unloaded model '{model_name}' (keep_alive=0)")
        _vram_unregister(model_name)
        return True
    except Exception as e:
        print(f"  [VRAM Flush] Failed to unload model '{model_name}': {e}")
        return False


# -- Fatal-error sentinel detection -----------------------------------------------
# All error paths in call_ollama_streamed yield a bracketed sentinel string
# instead of raising. Callers that loop MUST check this before treating
# output as valid code.
_FATAL_SENTINELS = (
    "[SYSTEM ERROR:",
    "[FATAL]",
    "[RETRY ERROR]",
    "[OOM Fallback]",
    "[ERROR]",
)


def is_fatal_ollama_error(text: str) -> bool:
    """Return True if *text* is an ollama error sentinel, not real model output."""
    if not text:
        return False
    stripped = text.strip()
    return any(stripped.startswith(s) for s in _FATAL_SENTINELS)
