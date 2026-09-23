from pathlib import Path

def edit(path, pairs, check_moji=True):
    p = Path(path)
    text = p.read_text(encoding='utf-8')
    moji = text.count('\u0097')
    for old, new in pairs:
        c = text.count(old)
        assert c == 1, f'{path}: count={c} for {old[:80]!r}'
        text = text.replace(old, new, 1)
    if check_moji:
        assert text.count('\u0097') == moji, f'{path}: mojibake changed'
    p.write_text(text, encoding='utf-8')
    b = p.read_bytes()
    print(f'  OK {path}: crlf={b.count(bytes([13,10]))} moji={b.count(bytes([0xc2,0x97]))}')

# ── Fix 1a: add the Ollama recovery watchdog helper ────────────────────────
WAIT_HELPER = (
    'def _wait_for_ollama(label: str, max_wait: float = 300.0, poll_interval: float = 15.0) -> bool:\n'
    '    """Block and poll /api/tags until the Ollama server is reachable again.\n'
    '\n'
    '    The Steam Deck Wi-Fi / Ollama runner periodically goes offline mid-run\n'
    '    (WinError 10060 / socket drops) and can stay down for minutes.  Burning\n'
    '    the retry budget into a dead server just cascades task failures, so we\n'
    '    wait for recovery before retrying instead.\n'
    '\n'
    '    Returns True when reachable, False after max_wait elapses.\n'
    '    Tune via MIDWAY_OLLAMA_WAIT_SECONDS / MIDWAY_OLLAMA_POLL_SECONDS.\n'
    '    """\n'
    '    import os as _os_wait\n'
    '    _max = float(_os_wait.environ.get("MIDWAY_OLLAMA_WAIT_SECONDS", str(max_wait)) or max_wait)\n'
    '    _poll = float(_os_wait.environ.get("MIDWAY_OLLAMA_POLL_SECONDS", str(poll_interval)) or poll_interval)\n'
    '    _deadline = time.time() + _max\n'
    '    _announced = False\n'
    '    while time.time() < _deadline:\n'
    '        try:\n'
    '            with urllib.request.urlopen(f"{OLLAMA_HOST}/api/tags", timeout=5.0) as _resp:\n'
    '                _resp.read(1)\n'
    '            print(f"\\n  [Ollama Watchdog] OK: {label}: Ollama is reachable again.")\n'
    '            sys.stdout.flush()\n'
    '            return True\n'
    '        except Exception:\n'
    '            if not _announced:\n'
    '                print(f"\\n  [Ollama Watchdog] WAIT: {label}: Ollama unreachable - waiting for it "\n'
    '                      f"to come back (up to {_max:.0f}s, polling every {_poll:.0f}s)...")\n'
    '                _announced = True\n'
    '            sys.stdout.flush()\n'
    '            time.sleep(_poll)\n'
    '    print(f"\\n  [Ollama Watchdog] FAIL: {label}: Ollama still unreachable after {_max:.0f}s.")\n'
    '    sys.stdout.flush()\n'
    '    return False\n'
    '\n'
    '\n'
)

edit('ollama_client.py', [
    # 1a: insert helper before _cooldown_and_retry
    (
        '_last_model_call_ts: float = 0.0  # wall-clock of the last streamed call (cooldown pacing)\n'
        '\n'
        '\n'
        'def _cooldown_and_retry(',
        '_last_model_call_ts: float = 0.0  # wall-clock of the last streamed call (cooldown pacing)\n'
        '\n'
        '\n'
        + WAIT_HELPER
        + 'def _cooldown_and_retry(',
    ),
    # 1c: wait for Ollama recovery inside _cooldown_and_retry
    (
        '    # 2. VRAM cooldown\n'
        '    cooldown = 5.0\n'
        '    print(f"  [VRAM Cooldown] Sleeping for {cooldown}s to allow thermal dissipation...")\n'
        '    time.sleep(cooldown)',
        '    # 2. Wait for Ollama to come back.  A socket drop mid-stream is usually\n'
        '    #    a server outage on the Steam Deck (WinError 10060), not a transient\n'
        '    #    blip - retrying immediately would just hit the same dead server.\n'
        '    if not _wait_for_ollama(label, max_wait=120.0, poll_interval=10.0):\n'
        '        msg = f"[FATAL] Ollama unreachable after recovery wait for \'{label}\' -- giving up."\n'
        '        print(f"  {msg}")\n'
        '        yield msg\n'
        '        return\n'
        '\n'
        '    # 3. VRAM cooldown\n'
        '    cooldown = 5.0\n'
        '    print(f"  [VRAM Cooldown] Sleeping for {cooldown}s to allow thermal dissipation...")\n'
        '    time.sleep(cooldown)',
    ),
    # 1b: replace fixed-backoff URLError retries with wait-for-recovery
    (
        '            _url_attempt = getattr(_run_stream_cycle, \'_url_retry_count\', 0) + 1\n'
        '            _run_stream_cycle._url_retry_count = _url_attempt\n'
        '            _MAX_URL_RETRIES = 3\n'
        '            _RETRY_DELAYS = [10, 20, 40]  # seconds\n'
        '            if _url_attempt <= _MAX_URL_RETRIES:\n'
        '                _delay = _RETRY_DELAYS[_url_attempt - 1]\n'
        '                print(f"\\n  [Network Retry] \u26a0 URLError for \'{label}\' (attempt {_url_attempt}/{_MAX_URL_RETRIES}): {e.reason}")\n'
        '                print(f"  [Network Retry] Waiting {_delay}s before retry...")\n'
        '                sys.stdout.flush()\n'
        '                time.sleep(_delay)\n'
        '                yield from _run_stream_cycle(payload_override=payload_override, cycle_label=cycle_label)\n'
        '            else:\n'
        '                _run_stream_cycle._url_retry_count = 0\n'
        '                msg = f"[SYSTEM ERROR: OLLAMA TIMEOUT] Could not reach Ollama at {OLLAMA_HOST} after {_MAX_URL_RETRIES} retries: {e.reason}"\n'
        '                print(f"\\n  [Network Retry] \u274c All {_MAX_URL_RETRIES} retries exhausted for \'{label}\'. Task marked FAILED.")\n'
        '                sys.stdout.flush()\n'
        '                yield msg',
        '            _url_attempt = getattr(_run_stream_cycle, \'_url_retry_count\', 0) + 1\n'
        '            _run_stream_cycle._url_retry_count = _url_attempt\n'
        '            _MAX_URL_RETRIES = 3\n'
        '            if _url_attempt <= _MAX_URL_RETRIES:\n'
        '                print(f"\\n  [Network Retry] \u26a0 URLError for \'{label}\' (attempt {_url_attempt}/{_MAX_URL_RETRIES}): {e.reason}")\n'
        '                sys.stdout.flush()\n'
        '                # Wait for Ollama to come back instead of a fixed 10/20/40s\n'
        '                # backoff.  The Steam Deck can stay offline for minutes; a\n'
        '                # blind retry just hits the same dead server and cascades.\n'
        '                if _wait_for_ollama(label):\n'
        '                    yield from _run_stream_cycle(payload_override=payload_override, cycle_label=cycle_label)\n'
        '                    return\n'
        '            _run_stream_cycle._url_retry_count = 0\n'
        '            msg = f"[SYSTEM ERROR: OLLAMA TIMEOUT] Could not reach Ollama at {OLLAMA_HOST}: {e.reason}"\n'
        '            print(f"\\n  [Network Retry] \u274c Ollama unreachable after recovery wait for \'{label}\'. Task marked FAILED.")\n'
        '            sys.stdout.flush()\n'
        '            yield msg',
    ),
])

# ── Fix 2: arg-count message formatting (invisible \x96 separator -> '..') ──
edit('_preflight_static.py', [
    # Replace the mojibake separator in ALL arg-count messages with '..'.
    # This is a global replace of an exact 3-occurrence pattern, so we bypass
    # the edit() count==1 assertion and do it manually here.
], check_moji=False)

# The arg-count separator is a single mojibake char (U+0096) between
# {_min_exp} and {_max_exp}; it rendered as '7!=66' in the logs. Replace
# every occurrence with '..'.
p = Path('_preflight_static.py')
t = p.read_text(encoding='utf-8')
sep = '{_min_exp}' + chr(0x96) + '{_max_exp}'
n = t.count(sep)
assert n >= 1, f'separator not found, count={n}'
t = t.replace(sep, '{_min_exp}..{_max_exp}')
p.write_text(t, encoding='utf-8')
b = p.read_bytes()
print(f'  OK _preflight_static.py: replaced {n} arg-count separator(s); crlf={b.count(bytes([13,10]))} moji={b.count(bytes([0xc2,0x97]))}')

print('ALL_EDITS_OK')
