#!/usr/bin/env python3
"""
Eradicate three mid-stream synchronization vulnerabilities:
  A: Partial-Generation Amnesia  → Ghost Buffer injection as assistant message
  B: Retry Payload Desync         → Pass ActiveMessages to _cooldown_and_retry
  C: False-Positive Recursion     → _consecutive_pages vs total _page_cycle_count

Pure Python 3.12+ — NO external dependencies.  Reads both files, applies patches,
writes them back in-place.
"""

import re
import sys

# ═══════════════════════════════════════════════════════════════════════════
#  Patch A + C:  paging_kernel.py
# ═══════════════════════════════════════════════════════════════════════════

def patch_paging_kernel(path: str = "paging_kernel.py") -> bool:
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()

    original = text  # for change detection

    # ── 1. PagingBuffer.__init__: add _last_before_text ──────────────────
    text = text.replace(
        "        self._buffer: str = \"\"\n        self._page_found: bool = False\n        self._page_info: Optional[Dict[str, str]] = None\n",
        "        self._buffer: str = \"\"\n        self._page_found: bool = False\n        self._page_info: Optional[Dict[str, str]] = None\n        self._last_before_text: str = \"\"\n"
    )

    # ── 2. PagingBuffer.append(PAGE_IN): capture before_text in _last_before_text ──
    # Replace the block that writes self._page_info = info for PAGE_IN
    old_page_in_block = (
        "            self._page_info = info\n"
        "            # Strip token from buffer, keep surrounding text\n"
        "            before = self._buffer[:match.start()]\n"
        "            after = self._buffer[match.end():]\n"
        "            self._buffer = before + after\n"
        "            return True, self._page_info, before + after\n"
    )
    new_page_in_block = (
        "            self._page_info = info\n"
        "            # ── Directive A: Ghost Buffer — capture partial generation before token ──\n"
        "            before = self._buffer[:match.start()]\n"
        "            self._last_before_text = before\n"
        "            after = self._buffer[match.end():]\n"
        "            self._buffer = before + after\n"
        "            return True, self._page_info, before + after\n"
    )
    text = text.replace(old_page_in_block, new_page_in_block)

    # ── 3. PagingBuffer.append(PAGE_OUT): same capture ──
    old_page_out_block = (
        "            before = self._buffer[:match.start()]\n"
        "            after = self._buffer[match.end():]\n"
        "            self._buffer = before + after\n"
        "            return True, self._page_info, before + after\n"
    )
    new_page_out_block = (
        "            # ── Directive A: Ghost Buffer — capture partial generation before token ──\n"
        "            before = self._buffer[:match.start()]\n"
        "            self._last_before_text = before\n"
        "            after = self._buffer[match.end():]\n"
        "            self._buffer = before + after\n"
        "            return True, self._page_info, before + after\n"
    )
    text = text.replace(old_page_out_block, new_page_out_block)

    # ── 4. Add last_before_text property to PagingBuffer ──
    old_props = (
        "    @property\n"
        "    def page_info(self) -> Optional[Dict[str, str]]:\n"
        "        return self._page_info\n"
        "\n"
        "    def reset(self):\n"
    )
    new_props = (
        "    @property\n"
        "    def page_info(self) -> Optional[Dict[str, str]]:\n"
        "        return self._page_info\n"
        "\n"
        "    @property\n"
        "    def last_before_text(self) -> str:\n"
        "        \"\"\"Return partial generation captured before last paging token (Directive A).\"\"\"\n"
        "        return self._last_before_text\n"
        "\n"
        "    def reset(self):\n"
    )
    text = text.replace(old_props, new_props)

    # ── 5. PagingBuffer.reset(): clear _last_before_text ──
    text = text.replace(
        "        self._buffer = \"\"\n        self._page_found = False\n        self._page_info = None",
        "        self._buffer = \"\"\n        self._page_found = False\n        self._page_info = None\n        self._last_before_text = \"\""
    )

    # ── 6. PagingController.__init__: add _consecutive_pages and _ghost_buffer_text ──
    text = text.replace(
        "        self._page_cycle_count: int = 0\n        self._page_in_progress: bool = False",
        "        self._page_cycle_count: int = 0\n        self._consecutive_pages: int = 0  # Directive C: smart recursion counter\n        self._ghost_buffer_text: str = \"\"  # Directive A: captured partial generation\n        self._page_in_progress: bool = False"
    )

    # ── 7. feed_token(): reset _consecutive_pages on normal tokens, increment on pages,
    #        capture ghost buffer text ──
    old_feed = (
        "        found, info, remaining = self.buffer.append(token)\n"
        "        if found:\n"
        "            self._page_cycle_count += 1\n"
        "            self._page_in_progress = True\n"
        "            self._continuation_pending = True\n"
        "            # Store remaining text for later injection\n"
        "            self._remaining_text = remaining\n"
        "        return found, info\n"
    )
    new_feed = (
        "        found, info, remaining = self.buffer.append(token)\n"
        "        if found:\n"
        "            self._page_cycle_count += 1\n"
        "            self._consecutive_pages += 1\n"
        "            self._page_in_progress = True\n"
        "            self._continuation_pending = True\n"
        "            # Directive A: Ghost Buffer — capture text generated before paging token\n"
        "            self._ghost_buffer_text = self.buffer.last_before_text\n"
        "            # Store remaining text for later injection\n"
        "            self._remaining_text = remaining\n"
        "        else:\n"
        "            # Directive C: LLM is generating normal code — reset consecutive counter\n"
        "            self._consecutive_pages = 0\n"
        "        return found, info\n"
    )
    text = text.replace(old_feed, new_feed)

    # ── 8. execute_page(): use _consecutive_pages instead of _page_cycle_count for recursion ──
    text = text.replace(
        "                recursion_depth=self._page_cycle_count,",
        "                recursion_depth=self._consecutive_pages,  # Directive C: track consecutive only"
    )

    # ── 9. build_resume_payload(): inject ghost buffer as assistant message before continuation ──
    old_build_last = (
        "        # Append the continuation prompt\n"
        "        messages.append({\n"
        "            \"role\": \"user\",\n"
        "            \"content\": (\n"
        "                \"[SYSTEM KERNEL: Paging complete. \"\n"
        "                \"Continue generating your response exactly where you left off. \"\n"
        "                \"Do not restart. Do not repeat previous output.]\"\n"
        "            ),\n"
        "        })\n"
        "\n"
        "        payload = {\n"
        "            \"model\": \"\",  # caller must set this\n"
        "            \"stream\": True,\n"
        "            \"keep_alive\": \"0\",\n"
        "            \"options\": {\n"
        "                \"num_ctx\": 32768,\n"
        "                \"num_predict\": 12000,\n"
        "                \"use_mmap\": True,\n"
        "            },\n"
        "            \"messages\": messages,\n"
        "        }\n"
        "        return payload\n"
    )
    new_build_last = (
        "        # ── Directive A: Ghost Buffer — inject partial generation as assistant message ──\n"
        "        # This forces the LLM to seamlessly finish its thought on resume rather than\n"
        "        # restarting its sentence (which would produce duplicated, invalid syntax).\n"
        "        if self._ghost_buffer_text:\n"
        "            messages.append({\n"
        "                \"role\": \"assistant\",\n"
        "                \"content\": self._ghost_buffer_text,\n"
        "            })\n"
        "\n"
        "        # Append the continuation prompt\n"
        "        messages.append({\n"
        "            \"role\": \"user\",\n"
        "            \"content\": (\n"
        "                \"[SYSTEM KERNEL: Paging complete. \"\n"
        "                \"Continue generating your response exactly where you left off. \"\n"
        "                \"Do not restart. Do not repeat previous output.]\"\n"
        "            ),\n"
        "        })\n"
        "\n"
        "        payload = {\n"
        "            \"model\": \"\",  # caller must set this\n"
        "            \"stream\": True,\n"
        "            \"keep_alive\": \"0\",\n"
        "            \"options\": {\n"
        "                \"num_ctx\": 32768,\n"
        "                \"num_predict\": 12000,\n"
        "                \"use_mmap\": True,\n"
        "            },\n"
        "            \"messages\": messages,\n"
        "        }\n"
        "        return payload\n"
    )
    text = text.replace(old_build_last, new_build_last)

    # ── 10. reset_cycle(): clear ghost buffer ──
    text = text.replace(
        "        self.buffer.reset()\n        self._page_in_progress = False\n        self._continuation_pending = False\n        self._remaining_text = \"\"",
        "        self.buffer.reset()\n        self._page_in_progress = False\n        self._continuation_pending = False\n        self._remaining_text = \"\"\n        self._ghost_buffer_text = \"\""
    )

    if text == original:
        print("  [paging_kernel.py]  ⚠ No changes applied — patterns may have shifted.")
        return False

    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    print("  [paging_kernel.py]  ✓ Patches A + C applied.")
    return True


# ═══════════════════════════════════════════════════════════════════════════
#  Patch B:  ollama_client.py
# ═══════════════════════════════════════════════════════════════════════════

def patch_ollama_client(path: str = "ollama_client.py") -> bool:
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()

    original = text

    # ── 1. _cooldown_and_retry: add optional messages parameter ──────────
    old_sig = (
        "def _cooldown_and_retry(\n"
        "    exception: Exception,\n"
        "    system: str, user: str, label: str, model: str, params: dict | None\n"
        ") -> Generator[str, None, None]:"
    )
    new_sig = (
        "def _cooldown_and_retry(\n"
        "    exception: Exception,\n"
        "    system: str, user: str, label: str, model: str,\n"
        "    params: dict | None,\n"
        "    messages: list[dict] | None = None,  # Directive B: stateful retry payload\n"
        ") -> Generator[str, None, None]:"
    )
    text = text.replace(old_sig, new_sig)

    # ── 2. In _cooldown_and_retry: when messages provided, retry with messages
    #     instead of calling call_ollama_streamed(system, user, ...) ──
    old_retry_call = (
        "    print(f\"  [Retry] Attempting automatic retry for '{label}' (attempt {_retry_counter['attempt']})...\")\n"
        "    try:\n"
        "        yield from call_ollama_streamed(system, user, label, model, params=retry_params)\n"
        "    except (ConnectionResetError, ConnectionAbortedError) as e2:\n"
        "        msg = f\"[FATAL] Socket dropped again on retry for '{label}': {e2}. Giving up.\"\n"
        "        print(f\"  {msg}\")\n"
        "        yield msg\n"
        "    except Exception as e2:\n"
        "        msg = f\"[RETRY ERROR] Non-socket exception on retry for '{label}': {e2}\"\n"
        "        print(f\"  {msg}\")\n"
        "        yield msg\n"
    )
    new_retry_call = (
        "    print(f\"  [Retry] Attempting automatic retry for '{label}' (attempt {_retry_counter['attempt']})...\")\n"
        "    try:\n"
        "        if messages is not None:\n"
        "            # ── Directive B: Stateful Retry — use mutated ActiveMessages array ──\n"
        "            # When messages are provided (from paging.active_messages.to_payload()),\n"
        "            # bypass call_ollama_streamed which would rebuild from raw system/user strings\n"
        "            # and lose all mounted page state. Instead, stream directly with the preserved\n"
        "            # messages array that includes the Ghost Buffer assistant entry and all\n"
        "            # paged-in context.\n"
        "            yield from _stream_messages_payload(\n"
        "                messages=messages,\n"
        "                model=model,\n"
        "                label=label,\n"
        "                params=retry_params,\n"
        "            )\n"
        "        else:\n"
        "            yield from call_ollama_streamed(system, user, label, model, params=retry_params)\n"
        "    except (ConnectionResetError, ConnectionAbortedError) as e2:\n"
        "        msg = f\"[FATAL] Socket dropped again on retry for '{label}': {e2}. Giving up.\"\n"
        "        print(f\"  {msg}\")\n"
        "        yield msg\n"
        "    except Exception as e2:\n"
        "        msg = f\"[RETRY ERROR] Non-socket exception on retry for '{label}': {e2}\"\n"
        "        print(f\"  {msg}\")\n"
        "        yield msg\n"
    )
    text = text.replace(old_retry_call, new_retry_call)

    # ── 3. Add _stream_messages_payload helper function ──
    # Insert after _cooldown_and_retry (before unload_model)
    insert_point = "\n\n\ndef unload_model(model_name: str) -> bool:\n"
    helper_fn = (
        "\n\n\ndef _stream_messages_payload(\n"
        "    messages: list[dict],\n"
        "    model: str,\n"
        "    label: str,\n"
        "    params: dict | None = None,\n"
        ") -> Generator[str, None, None]:\n"
        '    """Directive B: Stream Ollama response using a pre-built messages array.\n'
        "\n"
        "    Used by _cooldown_and_retry during stateful retries to preserve the\n"
        "    mutated ActiveMessages array (with Ghost Buffer assistant entries and\n"
        "    paged-in context) instead of rebuilding from raw system/user strings.\n"
        "\n"
        "    Args:\n"
        "        messages: The full messages array to send.\n"
        "        model: Model name to use.\n"
        "        label: Human-readable label for console output.\n"
        "        params: Optional Ollama options dict.\n"
        "\n"
        "    Yields:\n"
        "        Content tokens from the Ollama response.\n"
        "    \"\"\"\n"
        "    from datetime import datetime\n"
        "    ts = datetime.now().strftime('%H:%M:%S')\n"
        "    print(f\"  [{ts}] [RETRY STREAM] [{label}] Resuming with stateful messages array...\")\n"
        "\n"
        "    is_large_model = \"14b\" in model.lower()\n"
        "    ctx_size = OLLAMA_NUM_CTX_LARGE if is_large_model else OLLAMA_NUM_CTX\n"
        "\n"
        "    payload = {\n"
        '        \"model\": model,\n'
        '        \"stream\": True,\n'
        '        \"keep_alive\": \"0\",\n'
        '        \"options\": {\n'
        '            \"num_ctx\": ctx_size,\n'
        '            \"num_predict\": MAX_TOKENS,\n'
        '            \"use_mmap\": True,\n'
        "        },\n"
        '        \"messages\": messages,\n'
        "    }\n"
        "    if params:\n"
        '        payload[\"options\"].update(params)\n'
        "\n"
        "    data = json.dumps(payload).encode(\"utf-8\")\n"
        "    req = urllib.request.Request(\n"
        "        f\"{OLLAMA_HOST}/api/chat\",\n"
        "        data=data,\n"
        '        headers={\"Content-Type\": \"application/json\"},\n'
        '        method=\"POST\",\n'
        "    )\n"
        "\n"
        "    try:\n"
        "        with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT) as resp:\n"
        '            buf = b""\n'
        "            while True:\n"
        "                try:\n"
        "                    chunk = resp.read(4096)\n"
        "                except (ConnectionResetError, ConnectionAbortedError):\n"
        "                    msg = f\"[FATAL] Socket dropped on stateful retry for '{label}'. Giving up.\"\n"
        "                    print(f\"  {msg}\")\n"
        "                    yield msg\n"
        "                    return\n"
        "                if not chunk:\n"
        "                    break\n"
        "                buf += chunk\n"
        '                while b\"\\n\" in buf:\n'
        '                    line, buf = buf.split(b\"\\n\", 1)\n'
        "                    if line.strip():\n"
        "                        try:\n"
        "                            obj = json.loads(line.decode(\"utf-8\"))\n"
        '                            token = obj.get(\"message\", {}).get(\"content\", \"\")\n'
        "                            if not token:\n"
        "                                continue\n"
        "                            print(token, end=\"\")\n"
        "                            import sys as _sys\n"
        "                            _sys.stdout.flush()\n"
        "                            cb = _stream_callback\n"
        "                            if cb is not None:\n"
        "                                try:\n"
        "                                    cb(token)\n"
        "                                except Exception:\n"
        "                                    pass\n"
        "                            yield token\n"
        "                        except json.JSONDecodeError:\n"
        "                            pass\n"
        "    except urllib.error.HTTPError as e:\n"
        "        if e.code == 500 and is_large_model:\n"
        "            from ollama_client import FALLBACK_REVIEWER_MODEL\n"
        "            msg = f\"[OOM Fallback] {model} OOM during stateful retry. Dropping.\"\n"
        "            print(f\"  {msg}\")\n"
        "            yield msg\n"
        "            return\n"
        '        msg = f"[SYSTEM ERROR: HTTP {e.code}] Stateful retry: {e.reason}"\n'
        "        print(msg)\n"
        "        yield msg\n"
        "    except urllib.error.URLError as e:\n"
        '        msg = f"[SYSTEM ERROR: OLLAMA TIMEOUT] Stateful retry: {e.reason}"\n'
        "        print(msg)\n"
        "        yield msg\n"
        "    except Exception as e:\n"
        '        msg = f"[ERROR] Stateful retry: {e}"\n'
        "        print(msg)\n"
        "        yield msg\n"
    )
    text = text.replace(insert_point, helper_fn + insert_point)

    # ── 4. _run_stream_cycle: pass paging.active_messages.to_payload() to _cooldown_and_retry ──
    old_socket_catch = (
        "                    except (ConnectionResetError, ConnectionAbortedError) as sock_err:\n"
        "                        print(f\"\\n  [Stream] Socket dropped during read. Triggering retry...\",\n"
        "                              file=sys.stderr)\n"
        "                        sys.stderr.flush()\n"
        "                        yield from _cooldown_and_retry(\n"
        "                            exception=sock_err,\n"
        "                            system=system,\n"
        "                            user=user,\n"
        "                            label=cycle_label,\n"
        "                            model=use_model,\n"
        "                            params=params,\n"
        "                        )\n"
        "                        return\n"
    )
    new_socket_catch = (
        "                    except (ConnectionResetError, ConnectionAbortedError) as sock_err:\n"
        "                        print(f\"\\n  [Stream] Socket dropped during read. Triggering retry...\",\n"
        "                              file=sys.stderr)\n"
        "                        sys.stderr.flush()\n"
        "                        # ── Directive B: Pass ActiveMessages state to retry ──\n"
        "                        # Send the mutated messages array so retry preserves all\n"
        "                        # mounted page state instead of rebuilding from raw strings.\n"
        "                        yield from _cooldown_and_retry(\n"
        "                            exception=sock_err,\n"
        "                            system=system,\n"
        "                            user=user,\n"
        "                            label=cycle_label,\n"
        "                            model=use_model,\n"
        "                            params=params,\n"
        "                            messages=paging.active_messages.to_payload() if paging.active_messages else None,\n"
        "                        )\n"
        "                        return\n"
    )
    text = text.replace(old_socket_catch, new_socket_catch)

    if text == original:
        print("  [ollama_client.py] ⚠ No changes applied — patterns may have shifted.")
        return False

    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    print("  [ollama_client.py] ✓ Patch B applied.")
    return True


# ═══════════════════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("  Paging Kernel Security Patches")
    print("=" * 60)
    ok1 = patch_paging_kernel()
    ok2 = patch_ollama_client()
    print()
    if ok1 and ok2:
        print("✓ All patches applied successfully.")
    elif ok1 or ok2:
        print("⚠ Partial success — one file was patched.")
    else:
        print("✗ No patches were applied. Patterns may have shifted.")
        sys.exit(1)
