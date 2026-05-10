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
) -> Generator[str, None, None]:
    """Log critical warning, cooldown VRAM, decrement temperature, retry once.

    Called when a ConnectionResetError (WinError 10054) is caught during
    HTTP streaming from Ollama. Does NOT pass the truncated output to any
    downstream reviewer -- instead:
      1. Logs a critical warning with thermal/timeout context
      2. Triggers 5-second VRAM cooldown (time.sleep(5))
      3. Decrements LLM temperature by 0.1 (floor 0.1)
      4. Attempts a single automatic retry
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
            yield from _stream_messages_payload(
                messages=messages,
                model=model,
                label=label,
                params=retry_params,
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

    Yields:
        Content tokens from the Ollama response.
    """
    from datetime import datetime
    ts = datetime.now().strftime('%H:%M:%S')
    print(f"  [{ts}] [RETRY STREAM] [{label}] Resuming with stateful messages array...")

    is_large_model = "14b" in model.lower()
    ctx_size = OLLAMA_NUM_CTX_LARGE if is_large_model else OLLAMA_NUM_CTX

    payload = {
        "model": model,
        "stream": True,
        "keep_alive": "0",
        "options": {
            "num_ctx": ctx_size,
            "num_predict": MAX_TOKENS,
            "use_mmap": True,
        },
        "messages": messages,
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
        if e.code == 500 and is_large_model:
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

    Args:
        model_name: The model name to unload (e.g., 'qwen2.5-coder:7b').

    Returns:
        True if the request succeeded, False otherwise.
    """
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
    """Evict the previously active model from VRAM if different from the current one."""
    global _active_model
    if _active_model and _active_model != model:
        try:
            print(f"  [System] VRAM Flush: Evicting {_active_model} from unified memory...")
            payload = json.dumps({
                "model": _active_model,
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
        except Exception:
            pass
    _active_model = model


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
    _evict_previous_model(use_model)
    is_large_model = "14b" in use_model.lower()
    ctx_size = OLLAMA_NUM_CTX_LARGE if is_large_model else OLLAMA_NUM_CTX
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
                        yield from _cooldown_and_retry(
                            exception=sock_err,
                            system=system,
                            user=user,
                            label=cycle_label,
                            model=use_model,
                            params=params,
                            messages=paging.active_messages.to_payload() if paging.active_messages else None,
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

                                    # If PAGE_IN, inject the loaded content
                                    if page_info["type"] == "PAGE_IN" and paged_content:
                                        # Inject as system message before continuation
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

    # Run the stream cycle
    yield from _run_stream_cycle()

    # ── Directive A: Capture paged_in_cache for Pro-Mode Inheritance ──
    global _last_paged_cache
    _last_paged_cache = dict(paging.paged_in_cache)


def call_ollama(system: str, user: str, label: str, model: Optional[str] = None, params: Optional[dict] = None) -> str:
    """Call Ollama's /api/chat endpoint. Returns the full response text.

    Delegates to call_ollama_streamed() generator, collecting all yielded
    tokens into a single string. Fully backward compatible.

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
    _evict_previous_model(use_model)
    from datetime import datetime
    ts = datetime.now().strftime('%H:%M:%S')
    print(f"\n{'='*60}")
    print(f"  [{ts}] [START] [{label}] Calling Ollama ({use_model})...")
    print(f"{'='*60}")
    sys.stdout.flush()
    full: list[str] = []
    for token in call_ollama_streamed(system, user, label, model, params=params):
        full.append(token)
    result = "".join(full)
    ts_end = datetime.now().strftime('%H:%M:%S')
    print(f"  [{ts_end}] [END] [{label}] Execution complete.")
    sys.stdout.flush()
    return result


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

    use_model = model or MODEL
    _evict_previous_model(use_model)
    from datetime import datetime
    ts = datetime.now().strftime('%H:%M:%S')
    print(f"\n{'='*60}")
    print(f"  [{ts}] [START] [{label}] Calling Ollama ({use_model}) [stateless]...")
    print(f"{'='*60}")
    sys.stdout.flush()
    full: list[str] = []
    for token in call_ollama_streamed(system_text, user_text, label, model, params=params):
        full.append(token)
    result = "".join(full)
    ts_end = datetime.now().strftime('%H:%M:%S')
    print(f"  [{ts_end}] [END] [{label}] Execution complete.")
    sys.stdout.flush()
    return result
