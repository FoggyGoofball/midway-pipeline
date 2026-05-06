"""
Ollama HTTP client — synchronous streaming and non-streaming calls to
the Ollama API for LLM inference. No async/await — purely synchronous.

Handles URL errors, JSON decode errors, and timeouts gracefully.
Yields tokens as they arrive from the NDJSON stream.
"""

from __future__ import annotations
import json
import sys
import urllib.request
import urllib.error
from typing import Generator, Optional


# ── Configuration ──────────────────────────────────────────────────────────
OLLAMA_HOST: str = "http://192.168.0.16:11434"

# ── Model Names ──────────────────────────────────────────────────────────────
CODER_MODEL: str = "qwen2.5-coder:7b"
REVIEWER_MODEL: str = "phi3:14b"
FALLBACK_REVIEWER_MODEL: str = "llama3.1:8b-instruct-q4_K_M"
LIBRARIAN_MODEL: str = "llama3.1:8b-instruct-q4_K_M"
SYNTAX_GATE_MODEL: str = "qwen2.5-coder:1.5b"
INTENT_CLASSIFIER_MODEL: str = "llama3.2:1b"
CHAT_MODEL: str = CODER_MODEL
EXECUTION_MODEL: str = CODER_MODEL
REASONING_MODEL: str = REVIEWER_MODEL
MODEL: str = EXECUTION_MODEL
DIRECTOR_MODEL: str = "llama3.1:8b-instruct-q4_K_M"

# ── Timeouts & Budget ──────────────────────────────────────────────────────
OLLAMA_TIMEOUT: int = 420
OLLAMA_NUM_CTX: int = 32768
OLLAMA_NUM_CTX_LARGE: int = 16384
MAX_TOKENS: int = 12000


def call_ollama_streamed(
    system: str, user: str, label: str, model: Optional[str] = None, params: Optional[dict] = None
) -> Generator[str, None, None]:
    """Generator: call Ollama's /api/chat with streaming, yield tokens.

    Yields each content token as it arrives from the Ollama NDJSON stream.
    Handles errors by yielding an error message string and stopping.

    Payload features:
    - keep_alive: "0" — model unloads instantly after each call to free VRAM.
    - KV Cache q8_0 quantization: halves context memory footprint.

    Args:
        system: System prompt text.
        user: User prompt text.
        label: Human-readable label for console output.
        model: Model name override (defaults to MODEL).

    Yields:
        Individual tokens as strings from the Ollama response stream.
    """
    use_model = model or MODEL
    is_large_model = "14b" in use_model.lower()
    ctx_size = OLLAMA_NUM_CTX_LARGE if is_large_model else OLLAMA_NUM_CTX
    print(f"\n{'='*60}")
    print(f"  [{label}] Calling Ollama ({use_model}) [STREAMING]...")
    print(f"{'='*60}")
    sys.stdout.flush()
    payload = {
        "model": use_model,
        "stream": True,
        "keep_alive": "0",
        "options": {
            "num_ctx": ctx_size,
            "num_predict": MAX_TOKENS,
            "use_mmap": True,
        },
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }

    if params:
        payload["options"].update(params)
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA_HOST}/api/chat",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT) as resp:
            buffer = b""
            while True:
                chunk = resp.read(4096)
                if not chunk:
                    break
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    if line.strip():
                        try:
                            obj = json.loads(line.decode("utf-8"))
                            token = obj.get("message", {}).get("content", "")
                            if token:
                                print(token, end="")
                                sys.stdout.flush()
                                yield token
                        except json.JSONDecodeError:
                            pass  # skip malformed lines
    except urllib.error.HTTPError as e:
        if e.code == 500 and is_large_model:
            print(f"\n  [OOM Fallback] {use_model} ran out of memory. Retrying with {FALLBACK_REVIEWER_MODEL}...")
            yield from call_ollama_streamed(system, user, label, FALLBACK_REVIEWER_MODEL, params)
            return
        msg = f"[SYSTEM ERROR: HTTP {e.code}] Could not reach Ollama at {OLLAMA_HOST}: {e.reason}"
        print(msg)
        yield msg
    except urllib.error.URLError as e:
        msg = f"[SYSTEM ERROR: OLLAMA TIMEOUT] Could not reach Ollama at {OLLAMA_HOST}: {e.reason}"
        print(msg)
        yield msg
    except Exception as e:
        msg = f"[ERROR] {e}"
        print(msg)
        yield msg


def call_ollama(system: str, user: str, label: str, model: Optional[str] = None) -> str:
    """Call Ollama's /api/chat endpoint. Returns the full response text.

    Delegates to call_ollama_streamed() generator, collecting all yielded
    tokens into a single string. Fully backward compatible.

    Args:
        system: System prompt text.
        user: User prompt text.
        label: Human-readable label for console output.
        model: Model name override (defaults to MODEL).

    Returns:
        Full response text from the model.
    """
    print(f"\n{'='*60}")
    print(f"  [{label}] Calling Ollama ({model or MODEL})...")
    print(f"{'='*60}")
    sys.stdout.flush()
    full: list[str] = []
    for token in call_ollama_streamed(system, user, label, model):
        full.append(token)
    result = "".join(full)
    print()  # trailing newline
    sys.stdout.flush()
    return result
