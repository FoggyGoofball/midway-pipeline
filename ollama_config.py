"""
ollama_config.py -- Immutable constants, context-window sizing, TPS watchdog
class, and VramOverrunError extracted from ollama_client.py.

All public names are re-exported by ollama_client.py so existing callers are
unaffected.
"""

from __future__ import annotations
import time

# -- Configuration
OLLAMA_HOST: str = "http://192.168.0.16:11434"

# -- Model Names
CODER_MODEL: str = "qwen2.5-coder:7b"
REVIEWER_MODEL: str = "phi3:14b"
FALLBACK_REVIEWER_MODEL: str = "llama3.1:8b-instruct-q4_K_M"
PRE_SUMMARIZER_MODEL: str = "phi3.5:latest"
LIBRARIAN_MODEL: str = "llama3.1:8b-instruct-q4_K_M"
SYNTAX_GATE_MODEL: str = "qwen2.5-coder:1.5b"
INTENT_CLASSIFIER_MODEL: str = "llama3.2:1b"
CHAT_MODEL: str = CODER_MODEL
EXECUTION_MODEL: str = CODER_MODEL
REASONING_MODEL: str = REVIEWER_MODEL
MODEL: str = EXECUTION_MODEL
DIRECTOR_MODEL: str = "llama3.1:8b-instruct-q4_K_M"

# -- Timeouts & VRAM-Guarded Context Window Sizes
# All values calibrated for 12 GB unified memory (Steam Deck).
OLLAMA_TIMEOUT: int = 600
OLLAMA_NUM_CTX: int = 32768         # 7B/8B models -- ~6-7 GB at q8_0 KV
OLLAMA_NUM_CTX_LARGE: int = 16384   # 14B models   -- ~9.0 GB at 16K (75% budget)
OLLAMA_NUM_CTX_UPPER_MID: int = 16384  # 9B models
OLLAMA_NUM_CTX_MASSIVE: int = 16384    # phi3.5 3.8B pre-summarizer

# -- Centralized Model-to-Context Resolution
_MODEL_CTX_PRECEDENCE: list[tuple[str, int]] = [
    ("phi3.5",     OLLAMA_NUM_CTX_MASSIVE),
    ("phi-3.5",    OLLAMA_NUM_CTX_MASSIVE),
    ("phi-mini",   OLLAMA_NUM_CTX_MASSIVE),
    ("phi3:14b",   OLLAMA_NUM_CTX_LARGE),
    ("14b",        OLLAMA_NUM_CTX_LARGE),
    ("9b",         OLLAMA_NUM_CTX_UPPER_MID),
    ("7b",         OLLAMA_NUM_CTX),
    ("8b",         OLLAMA_NUM_CTX),
    ("3b",         OLLAMA_NUM_CTX_MASSIVE),
]


def resolve_ctx_size(model_name: str) -> int:
    """Return the safe num_ctx value for the given model name."""
    model_lower = model_name.lower()
    for tag, ctx in _MODEL_CTX_PRECEDENCE:
        if tag in model_lower:
            return ctx
    return OLLAMA_NUM_CTX


MAX_TOKENS: int = 4096

# -- TPS Watchdog constants
_TPS_BASELINE: float = 2.0         # tok/s floor
_TPS_WINDOW_SEC: float = 5.0       # rolling window length
_TPS_WINDOW_TOKENS = 20            # min tokens in window before rate computed
_TPS_MIN_STREAM_SEC: float = 15.0  # warm-up guard -- don't fire before this


class _TpsWatchdog:
    """Rolling-window token throughput monitor.

    Raises RuntimeError (caught upstream as VramOverrunError) when token
    speed drops below _TPS_BASELINE, indicating VRAM overload.
    """

    def __init__(self) -> None:
        self._timestamps: list[float] = []
        self._total_tokens = 0
        self._first_token_time: float | None = None

    def hit(self) -> None:
        """Record one token arrival and check throughput."""
        now = time.time()
        self._timestamps.append(now)
        if self._first_token_time is None:
            self._first_token_time = now
        self._total_tokens += 1

        # Warm-up guard
        if self._first_token_time is not None:
            if now - self._first_token_time < _TPS_MIN_STREAM_SEC:
                return

        cutoff = now - _TPS_WINDOW_SEC
        while self._timestamps and self._timestamps[0] < cutoff:
            self._timestamps.pop(0)

        if len(self._timestamps) < _TPS_WINDOW_TOKENS:
            return

        elapsed = self._timestamps[-1] - self._timestamps[0]
        if elapsed <= 0.0:
            return

        tps = (len(self._timestamps) - 1) / elapsed
        if tps < _TPS_BASELINE:
            elapsed_total = now - self._first_token_time if self._first_token_time else 0.0
            avg_tps = self._total_tokens / elapsed_total if elapsed_total > 0 else 0.0
            diag = (
                "\n" + "=" * 60 + "\n"
                f"  \U0001f6a8 VRAM OVERRUN DETECTED \u2014 Token speed below {_TPS_BASELINE} tok/s\n"
                f"  Rolling window: {tps:.2f} tok/s (last {_TPS_WINDOW_SEC}s)\n"
                f"  Average speed:  {avg_tps:.2f} tok/s over {elapsed_total:.1f}s\n"
                f"  Total tokens:   {self._total_tokens}\n"
                f"  Window tokens:  {len(self._timestamps)}\n"
                + "=" * 60 + "\n"
            )
            print(diag, flush=True)
            raise RuntimeError(diag)

    def reset(self) -> None:
        """Clear all timing state."""
        self._timestamps.clear()
        self._total_tokens = 0
        self._first_token_time = None


class VramOverrunError(Exception):
    """Raised when token speed drops below _TPS_BASELINE indicating VRAM overload.

    Intentionally NOT caught internally -- propagates up to execute_task()
    for a hard pipeline abort.
    """
    pass
