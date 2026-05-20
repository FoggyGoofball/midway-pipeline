"""
Ollama HTTP client — synchronous streaming and non-streaming calls to
the Ollama API for LLM inference. No async/await — purely synchronous.

Handles URL errors, JSON decode errors, and timeouts gracefully.
Yields tokens as they arrive from the NDJSON stream.

Directive D: Paging Protocol Integration
When a <PAGE_IN:"file"> or <PAGE_OUT:"concept"> token is emitted by the LLM,
this module catches it inline, gracefully closes the current stream (without
setting _stream_crashed = True), executes the page operation via the offload
store, builds a continuation payload, and auto-resumes streaming from where
it left off.
"""

from __future__ import annotations
import json
import sys
import time
import urllib.request
import urllib.error
from typing import Generator, Optional
from pathlib import Path

_active_model = None


# ═══════════════════════════════════════════════════════════════════════════
#  Directive D — Stream Resilience Helpers
# ═══════════════════════════════════════════════════════════════════════════

_retry_counter: dict = {"attempt": 0, "temperature": 0.5}
_stream_crashed: bool = False  # Directive D: flag set to True when socket drops
                                # Mesh/Reviewer should check this and discard output


def _cooldown_and_retry(
    exception: Exception,
    system: str, user: str, label: str, model: str,
    params: dict | None,
    messages: list[dict] | None = None,  # Directive B: stateful retry payload
    paging: 'PagingController | None' = None,  # Phase 7: Forward allocated_ctx sync
) -> Generator[str, None, None]:
    """Log critical warning, cooldown VRAM, decrement temperature, retry once.

    Called when a ConnectionResetError (WinError 10054) is caught during
    HTTP streaming from Ollama. Does NOT pass the truncated output to any
    downstream reviewer -- instead:
      1. Logs a critical warning with thermal/timeout context
      2. Triggers 5-second VRAM cooldown (time.sleep(5))
      3. Decrements LLM temperature by 0.1 (floor 0.1)
      4. Attempts a single automatic retry

    Args:
        paging: Optional PagingController reference for retry path context sync.
    """
    _retry_counter["attempt"] += 1

    # 1. Log critical warning
    print()
    print("=" * 60)
    print(f"  [CONNECTION_RESET] CRITICAL: Socket dropped during stream for '{label}'")
    print(f"  Exception: {exception}")
    print(f"  Probable cause: Payload too large / VRAM timeout / thermal throttle")
    print(f"  Retry attempt: {_retry_counter['attempt']}")
    print("=" * 60)

    # 2. VRAM cooldown
    cooldown = 5.0
    print(f"  [VRAM Cooldown] Sleeping for {cooldown}s to allow thermal dissipation...")
    time.sleep(cooldown)

    # 3. Decrement temperature (floor 0.1)
    current_temp = _retry_counter["temperature"]
    new_temp = max(0.1, current_temp - 0.1)
    _retry_counter["temperature"] = new_temp
    print(f"  [Retry] Decreased temperature from {current_temp} to {new_temp}")

    retry_params = dict(params or {})
    retry_params["temperature"] = new_temp

    # 4. Single automatic retry
    if _retry_counter["attempt"] >= 2:
        global _stream_crashed
        _stream_crashed = True
        msg = f"[FATAL] Socket dropped twice for '{label}' -- giving up."
        print(f"  {msg}")
        yield msg
        return

    print(f"  [Retry] Attempting automatic retry for '{label}' (attempt {_retry_counter['attempt']})...")
    try:
        if messages is not None:
            # ── Directive B: Stateful Retry — use mutated ActiveMessages array ──
            # When messages are provided (from paging.active_messages.to_payload()),
            # bypass call_ollama_streamed which would rebuild from raw system/user strings
            # and lose all mounted page state. Instead, stream directly with the preserved
            # messages array that includes the Ghost Buffer assistant entry and all
            # paged-in context.
            # Phase 7: Forward paging controller for allocated_ctx sync.
            yield from _stream_messages_payload(
                messages=messages,
                model=model,
                label=label,
                params=retry_params,
                paging=paging,
            )
        else:
            yield from call_ollama_streamed(system, user, label, model, params=retry_params)
    except (ConnectionResetError, ConnectionAbortedError) as e2:
        msg = f"[FATAL] Socket dropped again on retry for '{label}': {e2}. Giving up."
        print(f"  {msg}")
        yield msg
    except Exception as e2:
        msg = f"[RETRY ERROR] Non-socket exception on retry for '{label}': {e2}"
        print(f"  {msg}")
        yield msg





def _stream_messages_payload(
    messages: list[dict],
    model: str,
    label: str,
    params: dict | None = None,
    paging: 'PagingController | None' = None,
) -> Generator[str, None, None]:
    """Directive B: Stream Ollama response using a pre-built messages array.

    Used by _cooldown_and_retry during stateful retries to preserve the
    mutated ActiveMessages array (with Ghost Buffer assistant entries and
    paged-in context) instead of rebuilding from raw system/user strings.

    Args:
        messages: The full messages array to send.
        model: Model name to use.
        label: Human-readable label for console output.
        params: Optional Ollama options dict.
        paging: Optional PagingController reference so allocated_ctx is
                kept in sync with the actual num_ctx sent to Ollama.

    Yields:
        Content tokens from the Ollama response.
    """
    from datetime import datetime
    ts = datetime.now().strftime('%H:%M:%S')
    print(f"  [{ts}] [RETRY STREAM] [{label}] Resuming with stateful messages array...")

    ctx_size = resolve_ctx_size(model)

    # ── Phase 7: Sync paging controller's allocated_ctx with actual num_ctx ──
    # This is critical for retry paths: if the paging controller had a stale
    # allocated_ctx, PAGE_IN hard caps and resume payloads would use the wrong
    # context ceiling.
    if paging is not None:
        paging.allocated_ctx = ctx_size


    # Inject domain-specific temperature defaults if not already set
    resolved_params = dict(params or {})
    resolved_params.setdefault("temperature", 0.2)

    payload = {
        "model": model,
        "stream": True,
        "keep_alive": "0",
        "options": {
            "num_ctx": ctx_size,
            "num_predict": MAX_TOKENS,   # Full generation window — no premature cutoffs
            "use_mmap": True,
        },
        "messages": messages,

    }
    if resolved_params:
        payload["options"].update(resolved_params)

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA_HOST}/api/chat",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT) as resp:
            buf = b""
            while True:
                try:
                    chunk = resp.read(4096)
                except (ConnectionResetError, ConnectionAbortedError):
                    msg = f"[FATAL] Socket dropped on stateful retry for '{label}'. Giving up."
                    print(f"  {msg}")
                    yield msg
                    return
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if line.strip():
                        try:
                            obj = json.loads(line.decode("utf-8"))
                            token = obj.get("message", {}).get("content", "")
                            if not token:
                                continue
                            print(token, end="")
                            import sys as _sys
                            _sys.stdout.flush()
                            cb = _stream_callback
                            if cb is not None:
                                try:
                                    cb(token)
                                except Exception:
                                    pass
                            yield token
                        except json.JSONDecodeError:
                            pass
    except urllib.error.HTTPError as e:
        # Compute model size classification for OOM fallback via centralized resolver
        _oom_ctx = resolve_ctx_size(model)
        _is_large_oom = _oom_ctx >= OLLAMA_NUM_CTX_LARGE
        if e.code == 500 and _is_large_oom:
            from ollama_client import FALLBACK_REVIEWER_MODEL
            msg = f"[OOM Fallback] {model} OOM during stateful retry. Dropping."
            print(f"  {msg}")
            yield msg
            return
        msg = f"[SYSTEM ERROR: HTTP {e.code}] Stateful retry: {e.reason}"
        print(msg)
        yield msg
    except urllib.error.URLError as e:
        msg = f"[SYSTEM ERROR: OLLAMA TIMEOUT] Stateful retry: {e.reason}"
        print(msg)
        yield msg
    except Exception as e:
        msg = f"[ERROR] Stateful retry: {e}"
        print(msg)
        yield msg



def unload_model(model_name: str) -> bool:
    """Explicitly unload a model from VRAM by sending a blank chat request with keep_alive=0.

    Also unregisters the model from the VRAM Budget Tracker so subsequent
    admission checks reflect the freed capacity.

    Args:
        model_name: The model name to unload (e.g., 'qwen2.5-coder:7b').

    Returns:
        True if the request succeeded, False otherwise.
    """
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
            resp.read()  # consume response
        print(f"  [VRAM Flush] Unloaded model '{model_name}' (keep_alive=0)")
        # Also unregister from VRAM budget tracker
        _vram_unregister(model_name)
        return True
    except Exception as e:
        print(f"  [VRAM Flush] Failed to unload model '{model_name}': {e}")
        return False


# ── Streaming Callback Hook ────────────────────────────────────────────────
# Set by pipeline_stream.py to receive tokens without monkey-patching.
# Since pipeline.py uses `from ollama_client import call_ollama`, patching
# pipeline.call_ollama is a dead patch (Python import binding creates a local
# copy). Patching at the source level here guarantees ALL calls through
# call_ollama or call_ollama_streamed are captured.
_stream_callback = None

# ── Configuration ──────────────────────────────────────────────────────────
OLLAMA_HOST: str = "http://192.168.0.16:11434"

# ── Model Names ──────────────────────────────────────────────────────────────
CODER_MODEL: str = "qwen2.5-coder:7b"
REVIEWER_MODEL: str = "phi3:14b"
FALLBACK_REVIEWER_MODEL: str = "llama3.1:8b-instruct-q4_K_M"
PRE_SUMMARIZER_MODEL: str = "phi3.5:latest"  # 3.8B mini — compresses large context before phi3:14b review
LIBRARIAN_MODEL: str = "llama3.1:8b-instruct-q4_K_M"
SYNTAX_GATE_MODEL: str = "qwen2.5-coder:1.5b"
INTENT_CLASSIFIER_MODEL: str = "llama3.2:1b"
CHAT_MODEL: str = CODER_MODEL
EXECUTION_MODEL: str = CODER_MODEL
REASONING_MODEL: str = REVIEWER_MODEL
MODEL: str = EXECUTION_MODEL
DIRECTOR_MODEL: str = "llama3.1:8b-instruct-q4_K_M"

# ── Timeouts & Budget ──────────────────────────────────────────────────────
OLLAMA_TIMEOUT: int = 600
# ── VRAM-Guarded Context Window Sizes ────────────────────────────────────
# All values carefully tuned for 12GB unified memory (Steam Deck).
# Calculation basis (q4_K_M weights + q8_0 KV cache):
#   7B model:  ~4-5GB weights + ~2GB/16K ctx KV cache → ~6-7GB total at 24576
#               ~4-5GB weights + ~4GB/32K ctx KV cache → ~8-9GB total at 32768
#   9B model:  ~6-7GB weights + ~2GB/16K ctx KV cache → ~8-9GB total at 24576
#   14B model: ~8-9GB weights + ~2GB/16K ctx KV cache → ~10-11GB total at 16384
#   phi3.5:    ~9-10GB + ~2GB/16K ctx KV cache → ~11-12GB total at 16384
#
# Strategy: Large models (14B) get reduced context to save weight-room VRAM.
#           Medium models (9B) get an in-between window for breathing room.
# Context windows tuned for 12 GB unified memory (Steam Deck).
# phi3:14b was 10.2 GB at 16K (97% budget) → now 8K → ~9.0 GB (86%).
# qwen2.5-coder:7b was 7.4 GB at 32K → now 8K → ~5.5 GB.
# phi3.5 (3.8B) is the only model permitted 16K — it costs ~2.5 GB total.
OLLAMA_NUM_CTX: int = 8192          # Safe default for 7B/8B/1B on 12 GB unified RAM
OLLAMA_NUM_CTX_LARGE: int = 8192    # 14B models: weights alone ~8 GB; keep ctx tight
OLLAMA_NUM_CTX_UPPER_MID: int = 8192 # 9B models: same budget constraint
OLLAMA_NUM_CTX_MASSIVE: int = 16384  # phi3.5 3.8B pre-summarizer only — fits at 2.5 GB

# ── Centralized Model-to-Context Resolution ──────────────────────────────
# Single source of truth for all routing call sites. Eliminates 3 duplicate
# if/elif/else blocks that can drift apart during refactoring.
_MODEL_CTX_PRECEDENCE: list[tuple[str, int]] = [
    # Ordered: most-specific match first, fallback last
    ("phi3.5",     OLLAMA_NUM_CTX_MASSIVE),  # 16384 — 3.8B mini, ~2.5 GB total
    ("phi-3.5",    OLLAMA_NUM_CTX_MASSIVE),  # 16384
    ("phi-mini",   OLLAMA_NUM_CTX_MASSIVE),  # 16384
    ("phi3:14b",   OLLAMA_NUM_CTX_LARGE),    # 8192 — was 16384, cut to prevent 97% budget
    ("14b",        OLLAMA_NUM_CTX_LARGE),    # 8192
    ("9b",         OLLAMA_NUM_CTX_UPPER_MID), # 8192
    ("7b",         OLLAMA_NUM_CTX),          # 8192
    ("8b",         OLLAMA_NUM_CTX),          # 8192
    ("3b",         OLLAMA_NUM_CTX_MASSIVE),  # 16384 — phi-mini / small models
]



def resolve_ctx_size(model_name: str) -> int:
    """Centralized model-to-context-window resolution.

    Returns the safe num_ctx value for the given model name,
    using the precedence table above. Falls back to OLLAMA_NUM_CTX (32768).
    """

    model_lower = model_name.lower()
    for tag, ctx in _MODEL_CTX_PRECEDENCE:
        if tag in model_lower:
            return ctx
    return OLLAMA_NUM_CTX

MAX_TOKENS: int = 4096

# ── TPS Watchdog — Token Speed Monitor ─────────────────────────────────────
# Monitors streaming throughput and aborts with a diagnostic dump when
# token speed drops below the safety floor, indicating VRAM overload.
# Uses a rolling window of 5-second buckets to filter transient stalls
# (page-in token detection / stream resume) from genuine VRAM degradation.

_TPS_BASELINE: float = 2.0         # tok/s — below this = VRAM overload
_TPS_WINDOW_SEC: float = 5.0       # rolling window length in seconds
_TPS_WINDOW_TOKENS = 20            # minimum tokens in window to compute rate
                                    # Increased from 3 to 20 to prevent false
                                    # positives during model warm-up phase.
                                    # First 20 tokens arrive slowly due to
                                    # KV cache population; TPS recovers after.
_TPS_MIN_STREAM_SEC: float = 15.0  # Minimum streaming time (seconds) before
                                    # watchdog can fire. Prevents premature
                                    # abort during model warm-up when the
                                    # first 5-15s of streaming is slow.


class _TpsWatchdog:
    """Rolling-window token throughput monitor.

    Tracks token arrival timestamps over a sliding window.  On every
    token consumed, checks if the rolling throughput has dropped below
    _TPS_BASELINE.  If so, prints a comprehensive diagnostic dump and
    raises RuntimeError to abort the stream.
    """

    def __init__(self):
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

        # Warm-up guard: don't fire the watchdog until at least
        # _TPS_MIN_STREAM_SEC seconds have elapsed since the first token.
        # Prevents false positives during model warm-up phase when the
        # KV cache is still being populated (first ~15s of streaming).
        if self._first_token_time is not None:
            if now - self._first_token_time < _TPS_MIN_STREAM_SEC:
                return

        # Prune old entries outside the window
        cutoff = now - _TPS_WINDOW_SEC
        while self._timestamps and self._timestamps[0] < cutoff:
            self._timestamps.pop(0)

        if len(self._timestamps) < _TPS_WINDOW_TOKENS:
            return  # not enough data yet

        elapsed = self._timestamps[-1] - self._timestamps[0]
        if elapsed <= 0.0:
            return

        tps = (len(self._timestamps) - 1) / elapsed
        if tps < _TPS_BASELINE:
            # ── Diagnostic dump ───────────────────────────────────────
            elapsed_total = now - self._first_token_time if self._first_token_time else 0.0
            avg_tps = self._total_tokens / elapsed_total if elapsed_total > 0 else 0.0
            diag = (
                f"\n{'='*60}\n"
                f"  🚨 VRAM OVERRUN DETECTED — Token speed below {_TPS_BASELINE} tok/s\n"
                f"  Rolling window: {tps:.2f} tok/s (last {_TPS_WINDOW_SEC}s)\n"
                f"  Average speed:  {avg_tps:.2f} tok/s over {elapsed_total:.1f}s\n"
                f"  Total tokens:   {self._total_tokens}\n"
                f"  Window tokens:  {len(self._timestamps)}\n"
                f"{'='*60}\n"
            )
            print(diag, flush=True)
            # Re-raise with diagnostics appended
            raise RuntimeError(diag)

    def reset(self) -> None:
        """Clear all timing state."""
        self._timestamps.clear()
        self._total_tokens = 0
        self._first_token_time = None


# ── Module-Level Watchdog Sentinel ─────────────────────────────────────────
# Initialized by call_ollama_streamed before the first stream cycle.
# Checked by the TPS Watchdog hit() call inside _run_stream_cycle.
# Singleton pattern via module-level global allows recursive paging
# resume cycles to share the same monitoring instance.
_tps_watchdog: _TpsWatchdog | None = None

# ── VRAM Overrun Abort Flag ───────────────────────────────────────────────
# Set to True when TPS Watchdog fires. Checked by upstream callers
# (execute_task, run_tasks) to trigger a hard pipeline abort with full
# VRAM diagnostics dump. Reset per top-level call_ollama_streamed().
_vram_overrun_abort: bool = False
_vram_abort_diagnostics: str = ""


class VramOverrunError(Exception):
    """Raised when token speed drops below _TPS_BASELINE indicating VRAM overload.
    
    Intentionally NOT caught internally — propagates all the way up the call
    chain to execute_task() which performs the hard abort.
    """
    pass


def vram_overrun_abort() -> bool:
    """Check if a VRAM overrun was detected in the most recent LLM call.
    
    Returns:
        True if the TPS watchdog fired and a VRAM overrun was detected.
    """
    return _vram_overrun_abort


def get_vram_abort_diagnostics() -> str:
    """Return the full VRAM diagnostics from the most recent overrun."""
    return _vram_abort_diagnostics


def reset_vram_overrun_abort() -> None:
    """Reset the VRAM overrun abort flag for a fresh pipeline run."""
    global _vram_overrun_abort, _vram_abort_diagnostics
    _vram_overrun_abort = False
    _vram_abort_diagnostics = ""


# ── Paging Stateful Cache Bridge (Directive A: Key-Value State Cache) ─────
_last_paged_cache: Dict[str, str] = {}


def get_last_paged_cache() -> Dict[str, str]:
    """Return the paged_in_cache dict from the most recent streaming call.

    Returns a dict mapping filenames to extracted text chunks.
    Used by _helpers_exec.py to attach cached content to task objects
    for Pro-Mode Inheritance across sub-agent boundaries.
    """
    return dict(_last_paged_cache)


def _evict_previous_model(model: str) -> None:
    """Evict the previously active model from VRAM if different from the current one.

    One model at a time — always evicts the old model before loading the new one.
    Uses keep_alive=0 to tell Ollama to unload immediately.
    VRAM Budget Tracker is called in log-only mode for telemetry.
    """
    global _active_model

    if _active_model and _active_model != model:
        _do_evict(_active_model)

    # Log the new model load via VRAM Budget Tracker (advisory only — never blocks)
    from vram_budget import register_model
    target_ctx = resolve_ctx_size(model)
    register_model(model, target_ctx)
    _active_model = model



def _do_evict(model: str) -> None:
    """Send a keep_alive=0 request to force Ollama to unload a model from VRAM."""
    from vram_budget import unregister_model
    try:
        print(f"  [System] VRAM Flush: Evicting {model} from unified memory...")
        payload = json.dumps({
            "model": model,
            "keep_alive": 0,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{OLLAMA_HOST}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()
        unregister_model(model)
    except Exception:
        pass


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
    # Reset VRAM overrun abort flag for this fresh LLM call
    reset_vram_overrun_abort()

    use_model = model or MODEL
    _evict_previous_model(use_model)
    ctx_size = resolve_ctx_size(use_model)
    from datetime import datetime
    ts = datetime.now().strftime('%H:%M:%S')
    print(f"\n{'='*60}")
    print(f"  [{ts}] [START] [{label}] Calling Ollama ({use_model}) [STREAMING]...")
    print(f"{'='*60}")
    sys.stdout.flush()

    # ── Directive D: Paging Protocol initialization ────────────────────────
    # Set up a PagingController to intercept <PAGE_IN> and <PAGE_OUT> tokens
    # mid-stream. This will gracefully pause the current stream, execute the
    # page operation, and auto-resume with a continuation prompt.
    from paging_kernel import PagingController
    from offload_store import get_offload_store
    paging = PagingController(offload_store=get_offload_store())
    # ── Phase 7: Forward the active model's context allocation ──────────
    # Ensures the PagingController uses the correct context ceiling for
    # dynamic hard cap enforcement (handle_page_in) and resume payload
    # construction (build_resume_payload).
    paging.allocated_ctx = ctx_size
    if params:
        # Pop paging-related params that aren't Ollama options
        _project_root = params.pop("_project_root", None)
        if _project_root:
            paging.project_root = Path(_project_root)
    else:
        params = {}

    payload = {
        "model": use_model,
        "stream": True,
        "keep_alive": "0",
        "options": {
            "num_ctx": ctx_size,
            "num_predict": MAX_TOKENS,   # Full generation window — no premature cutoffs
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

    # ── Helper: Single stream read cycle (extracted for page+resume) ───────
    def _run_stream_cycle(payload_override: Optional[dict] = None,
                          cycle_label: str = label) -> Generator[str, None, None]:
        """Execute one streaming cycle, yielding tokens.

        If payload_override is provided, uses that instead of the default payload.
        This allows the paging kernel to inject a continuation payload after a
        page operation.
        """
        if payload_override:
            cycle_data = json.dumps(payload_override).encode("utf-8")
            cycle_req = urllib.request.Request(
                f"{OLLAMA_HOST}/api/chat",
                data=cycle_data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
        else:
            cycle_data = data
            cycle_req = req

        try:
            with urllib.request.urlopen(cycle_req, timeout=OLLAMA_TIMEOUT) as cycle_resp:
                cycle_buffer = b""
                while True:
                    try:
                        chunk = cycle_resp.read(4096)
                    except (ConnectionResetError, ConnectionAbortedError) as sock_err:
                        print(f"\n  [Stream] Socket dropped during read. Triggering retry...",
                              file=sys.stderr)
                        sys.stderr.flush()
                        # ── Directive B: Pass ActiveMessages state to retry ──
                        # Send the mutated messages array so retry preserves all
                        # mounted page state instead of rebuilding from raw strings.
                        # Phase 7: Forward paging controller for allocated_ctx sync.
                        yield from _cooldown_and_retry(
                            exception=sock_err,
                            system=system,
                            user=user,
                            label=cycle_label,
                            model=use_model,
                            params=params,
                            messages=paging.active_messages.to_payload() if paging.active_messages else None,
                            paging=paging,
                        )

                        return
                    if not chunk:
                        break
                    cycle_buffer += chunk
                    while b"\n" in cycle_buffer:
                        line, cycle_buffer = cycle_buffer.split(b"\n", 1)
                        if line.strip():
                            try:
                                obj = json.loads(line.decode("utf-8"))
                                token = obj.get("message", {}).get("content", "")
                                if not token:
                                    continue

                                # ── Directive D: Live Paging Detection ─────
                                # Feed the token to the paging controller.
                                # If it detects a complete <PAGE_IN> or
                                # <PAGE_OUT> token, we need to:
                                #   1. Gracefully stop yielding from this cycle
                                #   2. Execute the page operation
                                #   3. Create a continuation payload
                                #   4. Recursively call _run_stream_cycle with
                                #      the continuation payload
                                page_detected, page_info = paging.feed_token(token)
                                if page_detected:
                                    print(f"\n  [Paging Kernel] ⚡ Intercepted {page_info['type']}: "
                                          f"'{page_info['target']}'")
                                    sys.stdout.flush()

                                    # Execute the page operation
                                    paged_content = paging.execute_page(page_info)

                                    # Build the resume payload with the
                                    # paged content injected as context
                                    resume_payload = paging.build_resume_payload(system)

                                    # Set the model in the resume payload
                                    resume_payload["model"] = use_model

                                    page_failed = getattr(paging, "_last_page_failed", False)

                                    if page_info["type"] == "PAGE_IN":
                                        if page_failed:
                                            # Replace the generic continuation prompt with
                                            # an explicit failure notice so the LLM does not
                                            # re-emit the paging protocol instructions.
                                            _target = page_info.get("target", "unknown")
                                            for _msg in reversed(resume_payload["messages"]):
                                                if _msg.get("role") == "user":
                                                    _msg["content"] = (
                                                        f"[SYSTEM KERNEL: PAGE_IN failed — "
                                                        f"'{_target}' does not exist on disk. "
                                                        f"Do NOT retry this page request. "
                                                        f"Continue your task using only "
                                                        f"the context already available to you. "
                                                        f"Synthesize from what you know.]"
                                                    )
                                                    break
                                        elif paged_content:
                                            # Inject the loaded content as a system message
                                            # before the continuation prompt
                                            resume_payload["messages"].insert(
                                                -1,  # before the continuation prompt
                                                {"role": "system", "content": paged_content}
                                            )

                                    # If PAGE_OUT, the kernel message is already
                                    # in the continuation prompt

                                    print(f"  [Paging Kernel] 🔄 Auto-resuming stream with continuation prompt...")
                                    sys.stdout.flush()

                                    # Recursively continue from the resumed stream
                                    paging.reset_cycle()
                                    yield from _run_stream_cycle(
                                        payload_override=resume_payload,
                                        cycle_label=f"{cycle_label} (paged resume)",
                                    )
                                    return

                                # Normal token — print, callback, yield
                                print(token, end="")
                                sys.stdout.flush()
                                cb = _stream_callback
                                if cb is not None:
                                    try:
                                        cb(token)
                                    except Exception:
                                        pass

                                # ── TPS Watchdog: check every token ──────
                                # Located inside _run_stream_cycle so it
                                # catches tokens from ALL cycles, including
                                # recursive paging resume cycles.
                                # On VRAM overrun: print comprehensive VRAM
                                # diagnostics, set the global abort flag, and
                                # raise VramOverrunError to propagate up through
                                # the entire call chain for a hard pipeline halt.
                                global _tps_watchdog
                                if _tps_watchdog is not None:
                                    try:
                                        _tps_watchdog.hit()
                                    except RuntimeError as _vram_exc:
                                        global _stream_crashed, _vram_overrun_abort, _vram_abort_diagnostics
                                        _stream_crashed = True
                                        _vram_overrun_abort = True
                                        diag = str(_vram_exc)
                                        # Build comprehensive VRAM state dump
                                        from datetime import datetime as _dt
                                        _now_s = _dt.now().strftime('%H:%M:%S')
                                        _active = _active_model or "none"
                                        vram_dump = (
                                            f"\n\n{'‼'*40}\n"
                                            f"  🚨🚨 VRAM OVERRUN — HARD PIPELINE ABORT 🚨🚨\n"
                                            f"  [{_now_s}] Active model: {_active}\n"
                                            f"  Current cycle: {cycle_label}\n"
                                            f"  Context window: {ctx_size} tokens\n"
                                            f"{diag}"
                                            f"  🚨 Pipeline ABORTED — no further phases will execute.\n"
                                            f"  💡 Suggested actions:\n"
                                            f"    1. Check if a smaller model (e.g. qwen2.5-coder:7b) can be used\n"
                                            f"    2. Reduce context window with 'num_ctx' settings\n"
                                            f"    3. Close other GPU processes (browser, etc.)\n"
                                            f"    4. Consider upgrading VRAM or using model offloading\n"
                                            f"{'‼'*40}\n\n"
                                        )
                                        _vram_abort_diagnostics = vram_dump
                                        print(vram_dump, flush=True)
                                        raise VramOverrunError(diag)

                                yield token
                            except json.JSONDecodeError:
                                pass  # skip malformed lines
        except urllib.error.HTTPError as e:
            # Re-evaluate model size classification for OOM fallback via centralized resolver
            _oom_model = payload_override.get("model", "") if payload_override else use_model
            _oom_ctx = resolve_ctx_size(_oom_model)
            _is_large_oom = _oom_ctx >= OLLAMA_NUM_CTX_LARGE
            if e.code == 500 and _is_large_oom:
                print(f"\n  [OOM Fallback] {use_model} ran out of memory. Retrying with {FALLBACK_REVIEWER_MODEL}...")
                yield from call_ollama_streamed(system, user, label, FALLBACK_REVIEWER_MODEL, params)
                return
            msg = f"[SYSTEM ERROR: HTTP {e.code}] Could not reach Ollama at {OLLAMA_HOST}: {e.reason}"
            print(msg)
            yield msg
        except VramOverrunError:
            # VRAM overrun — DO NOT catch here. Propagate up to execute_task()
            # for a hard pipeline abort with full diagnostics.
            raise
        except urllib.error.URLError as e:
            # Network timeout / server unreachable — retry up to 3 times with
            # increasing backoff before giving up. Yielding the raw error string
            # would push it downstream as "code", corrupting every dependent task.
            _url_attempt = getattr(_run_stream_cycle, '_url_retry_count', 0) + 1
            _run_stream_cycle._url_retry_count = _url_attempt
            _MAX_URL_RETRIES = 3
            _RETRY_DELAYS = [10, 20, 40]  # seconds
            if _url_attempt <= _MAX_URL_RETRIES:
                _delay = _RETRY_DELAYS[_url_attempt - 1]
                print(f"\n  [Network Retry] ⚠ URLError for '{label}' (attempt {_url_attempt}/{_MAX_URL_RETRIES}): {e.reason}")
                print(f"  [Network Retry] Waiting {_delay}s before retry...")
                sys.stdout.flush()
                time.sleep(_delay)
                yield from _run_stream_cycle(payload_override=payload_override, cycle_label=cycle_label)
            else:
                _run_stream_cycle._url_retry_count = 0
                msg = f"[SYSTEM ERROR: OLLAMA TIMEOUT] Could not reach Ollama at {OLLAMA_HOST} after {_MAX_URL_RETRIES} retries: {e.reason}"
                print(f"\n  [Network Retry] ❌ All {_MAX_URL_RETRIES} retries exhausted for '{label}'. Task marked FAILED.")
                sys.stdout.flush()
                yield msg
        except Exception as e:
            msg = f"[ERROR] {e}"
            print(msg)
            yield msg

    # ── TPS Watchdog: Reset for top-level call ─────────────────────────────
    # Reset the singleton watchdog so it starts fresh for each top-level
    # call_ollama_streamed invocation.  The watchdog lives inside
    # _run_stream_cycle (at the actual token yield point) to catch ALL
    # tokens including those from recursive paging resume cycles.
    global _tps_watchdog
    _tps_watchdog = _TpsWatchdog()

    yield from _run_stream_cycle()

    # ── Directive A: Capture paged_in_cache for Pro-Mode Inheritance ──
    global _last_paged_cache
    _last_paged_cache = dict(paging.paged_in_cache)


def call_ollama(system: str, user: str, label: str, model: Optional[str] = None, params: Optional[dict] = None,
                skip_pre_summarizer: bool = False) -> str:
    """Call Ollama's /api/chat endpoint. Returns the full response text.

    Delegates to call_ollama_streamed() generator, collecting all yielded
    tokens into a single string. Fully backward compatible.

    ── Fix E: Hard floor guard ─────────────────────────────────────────
    Always resolves num_ctx for the target model and enforces a hard
    character ceiling at 75% of the model's context budget on the user
    payload. This is critical for non-streamed calls from the Lead
    Producer (Scope Gate), Director, GDD Librarian, and pipeline signal
    handlers, which don't pass through call_ollama_with_messages().

    ── Pre-Summarizer: phi3.5 compression pass ─────────────────────────
    When the target model is phi3:14b and the user payload exceeds 80% of
    phi3:14b's context window, phi3.5:latest (3.8B mini) runs first to
    compress the payload — discarding irrelevant context intelligently —
    before handing a compact summary to phi3:14b for deep reasoning.

    Args:
        system: System prompt text.
        user: User prompt text.
        label: Human-readable label for console output.
        model: Model name override (defaults to MODEL).
        params: Optional dict of Ollama options (e.g., {"temperature": 0.5}).

    Returns:
        Full response text from the model.
    """
    use_model = model or MODEL

    # ── Pre-Summarizer: compress large context before phi3:14b ──────
    # Fire when targeting phi3:14b and payload exceeds 80% of its 16384 ctx.
    # Skipped for review/fix loops, conflict resolution, tribunal, and final
    # approval — those must see the full unmodified context to avoid dropping
    # relevant code or critique data (caller passes skip_pre_summarizer=True).
    # Threshold: fire when user alone would consume >60% of phi3:14b's ctx
    # (leaving headroom for the system prompt in the remaining 40%).
    _presumm_char_threshold = int(OLLAMA_NUM_CTX_LARGE * 1.5 * 0.60)
    if not skip_pre_summarizer and "phi3:14b" in use_model and len(user) > _presumm_char_threshold:
        print(f"  [Pre-Summarizer] Context too large for {use_model} ({len(user)} chars > {_presumm_char_threshold}). "
              f"Compressing with {PRE_SUMMARIZER_MODEL} first...")
        _presumm_ctx = resolve_ctx_size(PRE_SUMMARIZER_MODEL)
        _presumm_system = (
            "You are an expert context compressor. "
            "You will be given a large context payload destined for a deep-reasoning model. "
            "Your job is to produce a compact, information-dense summary that:\n"
            "1. Retains ALL technically relevant facts, code structures, API names, constraints, and decisions.\n"
            "2. Discards preamble, redundant explanations, repeated boilerplate, and irrelevant background.\n"
            "3. Preserves any code blocks, function signatures, and error messages verbatim.\n"
            "4. Is written in clear, structured prose with bullet points where appropriate.\n"
            "Output ONLY the compressed summary — no meta-commentary."
        )
        _presumm_params = {"num_ctx": _presumm_ctx, "temperature": 0.1}
        _evict_previous_model(PRE_SUMMARIZER_MODEL)
        from datetime import datetime as _dt
        _ts = _dt.now().strftime('%H:%M:%S')
        print(f"\n{'='*60}")
        print(f"  [{_ts}] [START] [Pre-Summarizer ({label})] Calling Ollama ({PRE_SUMMARIZER_MODEL})...")
        print(f"  [VRAM Guard] num_ctx={_presumm_ctx}, user={len(user)} chars")
        print(f"{'='*60}")
        sys.stdout.flush()
        _summary_tokens: list[str] = []
        for _tok in call_ollama_streamed(_presumm_system, user, f"Pre-Summarizer ({label})", PRE_SUMMARIZER_MODEL, params=_presumm_params):
            _summary_tokens.append(_tok)
        user = "".join(_summary_tokens)
        _ts_end = _dt.now().strftime('%H:%M:%S')
        print(f"  [{_ts_end}] [END] [Pre-Summarizer ({label})] Compressed {len(''.join(_summary_tokens))} → {len(user)} chars.")
        sys.stdout.flush()

    # ── Fix E: Context collapse guard on combined system+user payload ──
    # Ollama returns HTTP 400 if system_tokens + user_tokens > num_ctx.
    # The guard must measure the total payload, not just user alone.
    # Chars-to-tokens ratio: ~1.5 chars/tok (conservative estimate).
    _e_model_ctx = resolve_ctx_size(use_model)
    _e_total_chars = len(system) + len(user)
    _e_max_total_chars = int(_e_model_ctx * 1.5)          # 100% of ctx in chars
    _e_reserved_system_chars = len(system)
    _e_max_user_chars = max(256, _e_max_total_chars - _e_reserved_system_chars)
    if len(user) > _e_max_user_chars:
        _e_overflow = user[_e_max_user_chars:]
        print(f"  [Context Collapse] combined payload was {_e_total_chars} chars "
              f"({int(_e_total_chars/1.5)} tok estimated), "
              f"model ctx={_e_model_ctx} tok — truncating user to {_e_max_user_chars} chars "
              f"(system={len(system)} chars reserved)")
        # Preserve overflow in OffloadStore so the LLM can PAGE_IN if needed.
        _e_overflow_note = ""
        if _e_overflow.strip():
            try:
                from offload_store import get_offload_store as _get_os
                _e_store = _get_os()
                import hashlib as _hl
                _e_oid = "ctx_overflow_" + _hl.md5((_e_overflow[:64]).encode()).hexdigest()[:8]
                _e_store.store_block(
                    block_id=_e_oid,
                    header=f"Overflow context ({len(_e_overflow)} chars) from call_ollama [{label}]",
                    body_lines=[_e_overflow],
                )
                _e_overflow_note = (
                    f"\n[📄 Context overflow preserved — {len(_e_overflow)} chars offloaded. "
                    f"Use `<invoke_kernel><action>PAGE_IN</action>"
                    f"<target>{_e_oid}</target></invoke_kernel>` to retrieve.]\n"
                )
            except Exception:
                pass
        user = user[:_e_max_user_chars] + (
            f"\n[... context collapsed at {int(_e_max_user_chars/1.5)} tk ...]"
        ) + _e_overflow_note

    _e_params = dict(params or {})
    _e_params.setdefault("num_ctx", _e_model_ctx)

    _evict_previous_model(use_model)
    from datetime import datetime
    ts = datetime.now().strftime('%H:%M:%S')
    print(f"\n{'='*60}")
    print(f"  [{ts}] [START] [{label}] Calling Ollama ({use_model})...")
    print(f"  [VRAM Guard] num_ctx={_e_model_ctx}, user={len(user)} chars")
    print(f"{'='*60}")
    sys.stdout.flush()
    full: list[str] = []
    for token in call_ollama_streamed(system, user, label, model, params=_e_params):
        full.append(token)
    result = "".join(full)
    ts_end = datetime.now().strftime('%H:%M:%S')
    print(f"  [{ts_end}] [END] [{label}] Execution complete.")
    sys.stdout.flush()
    return result


# ── Fatal-error sentinel detection ────────────────────────────────────────────
# All error paths in call_ollama_streamed yield a bracketed sentinel string
# instead of raising.  Callers that loop (execute_task, preflight retry,
# review/fix) MUST check this before treating output as valid code.
_FATAL_SENTINELS = (
    "[SYSTEM ERROR:",
    "[FATAL]",
    "[RETRY ERROR]",
    "[OOM Fallback]",
    "[ERROR]",
)

def is_fatal_ollama_error(text: str) -> bool:
    """Return True if text is an ollama error sentinel, not real model output."""
    if not text:
        return False
    stripped = text.strip()
    return any(stripped.startswith(s) for s in _FATAL_SENTINELS)


def call_ollama_with_messages(
    messages: List[Dict[str, str]],
    label: str,
    model: Optional[str] = None,
    params: Optional[dict] = None,
) -> str:
    """Call Ollama with a pre-built messages array (stateless context).

    Directive A (Hard Context Firewall):
    This function accepts an already-assembled messages list — typically
    built as [system, user] — and passes it directly to Ollama's /api/chat
    endpoint. NO history survives between calls because each invocation
    receives only the messages explicitly provided.

    The system prompt and user prompt are extracted from the messages array
    for backward compatibility with call_ollama_streamed() internals.

    Args:
        messages: Fully assembled messages list (e.g., [{"role":"system","content":"..."},
                  {"role":"user","content":"..."}]).
        label: Human-readable label for console output.
        model: Model name override (defaults to MODEL).
        params: Optional dict of Ollama options.

    Returns:
        Full response text from the model.
    """
    # Extract system and user for backward compat with streaming internals
    system_text = ""
    user_text = ""
    for msg in messages:
        if msg.get("role") == "system":
            system_text = msg.get("content", "")
        elif msg.get("role") == "user":
            user_text = msg.get("content", "")

    # ── Fix B: Hard floor guard — mandatory collapse ────────────────
    # Always resolve num_ctx for the target model and enforce a hard
    # character ceiling on the user payload. This prevents call sites
    # (like execute_task) from silently accumulating context past the
    # model's VRAM budget even when params are omitted.
    _model_ctx = resolve_ctx_size(model or MODEL)
    # Estimate token ceiling: use 1.5 char/token (conservative for code)
    _max_user_chars = int(_model_ctx * 1.5 * 0.75)  # 75% of context for user content
    if len(user_text) > _max_user_chars:
        print(f"  [Context Collapse] user_text was {len(user_text)} chars "
              f"({int(len(user_text)/1.5)} tok estimated), "
              f"model ctx={_model_ctx} tok — truncating to {_max_user_chars} chars "
              f"({int(_model_ctx*0.75)} tok)")
        user_text = user_text[:_max_user_chars] + (
            f"\n[... context collapsed at {int(_model_ctx*0.75)} tk ...]")

    # Inject num_ctx into params if not already present
    _b_params = dict(params or {})
    _b_params.setdefault("num_ctx", _model_ctx)

    use_model = model or MODEL
    _evict_previous_model(use_model)
    from datetime import datetime
    ts = datetime.now().strftime('%H:%M:%S')
    print(f"\n{'='*60}")
    print(f"  [{ts}] [START] [{label}] Calling Ollama ({use_model}) [stateless]...")
    print(f"  [VRAM Guard] num_ctx={_model_ctx}, user={len(user_text)} chars")
    print(f"{'='*60}")
    sys.stdout.flush()
    full: list[str] = []
    for token in call_ollama_streamed(system_text, user_text, label, model, params=_b_params):
        full.append(token)
    result = "".join(full)
    ts_end = datetime.now().strftime('%H:%M:%S')
    print(f"  [{ts_end}] [END] [{label}] Execution complete.")
    sys.stdout.flush()
    return result
