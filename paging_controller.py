"""
paging_controller.py  Kernel-Level Page Controller
====================================================
Extracted from paging_kernel.py to keep individual files under 1 000 lines.

Contains:
  - PagingController  high-level orchestrator for the full PAGE_IN/PAGE_OUT cycle.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from paging_kernel import (
    PAGE_IN_REGEX, PAGE_OUT_REGEX, VRAM_STUB_REGEX,
    MAX_PAGE_RECURSION,
    detect_page_tokens, detect_vram_stubs,
    PagingBuffer, ActiveMessages, MemGPTContextStore,
    handle_page_in, handle_page_out,
    inject_paged_content, inject_continuation_prompt,
)


class PagingController:
    """High-level controller that orchestrates the full page cycle.

    Handles:
      - Detecting paging tokens in stream output
      - Executing PAGE_IN (file load + context injection)
      - Executing PAGE_OUT (context eviction)
      - Gracefully pausing the current stream (no crash flag set)
      - Building the auto-resume payload for Ollama
    """

    def __init__(self, project_root: Optional[Path] = None,
                 offload_store=None):
        import uuid as _uuid
        self.buffer = PagingBuffer()
        self.project_root = project_root
        self.offload_store = offload_store
        self.active_messages: Optional[ActiveMessages] = None
        self._page_cycle_count: int = 0
        self._consecutive_pages: int = 0  # Directive C: smart recursion counter
        self._ghost_buffer_text: str = ""  # Directive A: captured partial generation
        self._page_in_progress: bool = False
        self._continuation_pending: bool = False
        # ── Phase 7: Dynamic context tier for payload-aware paging ──────────
        self.allocated_ctx: int = 8192  # Updated by call_ollama_streamed per model
        # ── Directive A: Stateful Key-Value Cache  actively mounted content ──
        self.paged_in_cache: Dict[str, str] = {}
        # Flag set when the most recent execute_page call failed (file not found, etc.)
        self._last_page_failed: bool = False
        # ── MemGPT: disk-backed context store for this controller session ──
        _session_id = _uuid.uuid4().hex[:12]
        self.memgpt: MemGPTContextStore = MemGPTContextStore(
            session_id=_session_id,
            offload_store=offload_store,
        )
        print(f"  [MemGPT] Context store initialised (session={_session_id})")
    def feed_token(self, token: str) -> Tuple[bool, Optional[Dict[str, str]]]:
        """Feed a streamed token into the paging detector.

        Args:
            token: A single token (or chunk) from the LLM stream.

        Returns:
            Tuple of:
              - bool: True if a page operation should be triggered
              - Optional[Dict[str,str]]: The page info for the operation
        """
        found, info, remaining = self.buffer.append(token)
        if found:
            self._page_cycle_count += 1
            self._consecutive_pages += 1
            self._page_in_progress = True
            self._continuation_pending = True
            # Directive A: Ghost Buffer  capture text generated before paging token
            self._ghost_buffer_text = self.buffer.last_before_text
            # Store remaining text for later injection
            self._remaining_text = remaining
        else:
            # Directive C: LLM is generating normal code  reset consecutive counter
            self._consecutive_pages = 0
        return found, info

    def execute_page(self, page_info: Dict[str, str]) -> str:
        """Execute a detected page operation with stateful cache tracking.

        Args:
            page_info: Dict with 'type' and 'target' keys.  May also contain
                       'lines_range' and/or 'search_term' for targeted PAGE_IN.

        Returns:
            The content string to inject (for PAGE_IN) or a confirmation
            message (for PAGE_OUT).
        """
        if page_info["type"] == "PAGE_IN":
            target = page_info["target"]
            # Guard: reject bracket-wrapped placeholder targets that the model
            # may copy verbatim from prompt examples, e.g. "[rule-file-path]".
            if target.startswith("[") and target.endswith("]"):
                print(f"  [Paging Kernel] ⛔ PAGE_IN rejected  placeholder target '{target}' "
                      f"(model copied a prompt example; no file will be fetched)")
                self._last_page_failed = True
                return (
                    f"\n[SYSTEM KERNEL: PAGE_IN suppressed  "
                    f"'{target}' is a placeholder, not a real file path. "
                    f"Use the exact id= attribute from a <VRAM_STUB> tag.]\n"
                )
            # Guard: reject targets that look like function calls, contain
            # parentheses, angle brackets, or are clearly not file paths.
            # e.g. "OnLoadStatic(ctx)", "<some_tag>", "function(arg)"
            _invalid_path_chars = ("(", ")", "<", ">", ";", "\n", "\r")
            if any(ch in target for ch in _invalid_path_chars):
                print(f"  [Paging Kernel] ⛔ PAGE_IN rejected  malformed target '{target}' "
                      f"(looks like a function call or tag, not a file path)")
                self._last_page_failed = True
                return (
                    f"\n[SYSTEM KERNEL: PAGE_IN suppressed  "
                    f"'{target}' is not a valid file path. "
                    f"Do NOT retry. Synthesize from the context already available.]\n"
                )
            # ── MemGPT lookup priority ─────────────────────────────────
            # 1. In-memory key-value cache (fastest)
            # 2. MemGPT OffloadStore (evicted turns + PAGE_OUT blocks)
            # 3. Filesystem (project files)

            # 1. Memory cache hit
            if target in self.paged_in_cache:
                cached_text = self.paged_in_cache[target]
                print(f"  [Paging Kernel] 📋 Cache HIT: '{target}' ({len(cached_text)} chars cached)")
                return (
                    "\n\n## Paged-In File Content (Cached)\n"
                    f"**Source:** `{target}`\n"
                    f"```\n{cached_text}\n```\n"
                )

            # 2. MemGPT offload store (evicted turns / PAGE_OUT blocks)
            recalled = self.memgpt.recall(target)
            if "ERROR" not in recalled:
                self.paged_in_cache[target] = recalled
                print(f"  [MemGPT] 🔁 PAGE_IN '{target}' recalled from disk store "
                      f"({len(recalled)} chars)")
                return (
                    "\n\n## Paged-In Content (MemGPT Archival Memory)\n"
                    f"**Block ID:** `{target}`\n"
                    f"{recalled}\n"
                )

            # Execute the page-in operation (reads filesystem / offload store)
            # Phase 7: Forward the active model's context allocation for
            # dynamic hard cap enforcement via Context-Tiered Boundary Resolution.
            result = handle_page_in(
                target_path=target,
                project_root=self.project_root,
                offload_store=self.offload_store,
                recursion_depth=self._consecutive_pages,
                lines_range=page_info.get("lines_range"),
                search_term=page_info.get("search_term"),
                allocated_ctx=self.allocated_ctx,
            )

            # Detect failure: handle_page_in returns a KERNEL error string
            self._last_page_failed = "not found" in result or "blocked" in result or "failed" in result

            # Extract the raw text chunk from the formatted result and cache it.
            extracted_text = _extract_raw_text_from_result(result)
            if extracted_text and not self._last_page_failed:
                self.paged_in_cache[target] = extracted_text
                # Also persist to MemGPT store so future PAGE_INs are instant
                self.memgpt.store.store_block(
                    block_id=f"pagein_{self.memgpt.session_id}_{target.replace('/', '_').replace(' ', '_')[:48]}",
                    header=f"PAGE_IN result: {target}",
                    body_lines=[extracted_text],
                )
                print(f"  [Paging Kernel] 📋 Cache STORE: +{target} "
                      f"({len(extracted_text)} chars, "
                      f"now {len(self.paged_in_cache)} files cached)")

            # Checkpoint window after successful PAGE_IN
            if not self._last_page_failed and self.active_messages:
                self.memgpt.checkpoint(self.active_messages.messages)

            return result

        elif page_info["type"] == "PAGE_OUT":
            target_concept = page_info["target"]
            # Guard: reject bracket-wrapped placeholder targets.
            if target_concept.startswith("[") and target_concept.endswith("]"):
                print(f"  [Paging Kernel] ⛔ PAGE_OUT rejected  placeholder target '{target_concept}' "
                      f"(model copied a prompt example; nothing will be evicted)")
                return (
                    f"\n[SYSTEM KERNEL: PAGE_OUT suppressed  "
                    f"'{target_concept}' is a placeholder, not a real cached key.]\n"
                )
            removed_text = ""
            # ── Directive A: Stateful Cache  remove evicted entries ────
            if target_concept in self.paged_in_cache:
                removed_text = self.paged_in_cache.pop(target_concept)
                print(f"  [Paging Kernel] 📋 Cache EVICT: -{target_concept} "
                      f"({len(removed_text)} chars evicted, "
                      f"now {len(self.paged_in_cache)} files cached)")
            else:
                print(f"  [Paging Kernel] 📋 Cache: '{target_concept}' "
                      f"not in cache ({len(self.paged_in_cache)} files cached)")

            # ── MemGPT: persist evicted content to disk via context store ──
            if removed_text:
                import uuid as _uuid2
                block_id = (
                    f"pageout_{self.memgpt.session_id}_"
                    f"{target_concept.replace('/', '_').replace(' ', '_')[:48]}"
                )
                self.memgpt.store.store_block(
                    block_id=block_id,
                    header=f"PAGE_OUT: {target_concept}",
                    body_lines=[removed_text],
                )
                self.memgpt._evicted_block_ids.append(block_id)
                print(f"  [MemGPT] 💾 PAGE_OUT '{target_concept}' persisted as block '{block_id}'")
            # Also checkpoint the current active window to disk
            if self.active_messages:
                self.memgpt.checkpoint(self.active_messages.messages)

            return handle_page_out(
                target_concept=target_concept,
                offload_store=self.offload_store,
                paged_in_cache=self.paged_in_cache,
            )
        return ""

    def _aggressive_history_truncation(self, messages: List[Dict[str, str]],
                                        max_assistant_chars: int = 6000) -> List[Dict[str, str]]:
        """Directive C: Hard truncation fallback for bloated message arrays.

        When the messages array exceeds safe token thresholds during a PAGE_OUT,
        aggressively drop the oldest assistant messages in the array to prevent
        LLM backend hangs during context pruning.

        Strategy:
        1. Compute total char count of all assistant messages.
        2. If it exceeds max_assistant_chars, drop the OLDEST assistant messages
           (positive round-robin: drop oldest first, keep newest).
        3. Insert a labeled stub noting exactly what was dropped so the resume
           prompt gives the agent coherent awareness of evicted context.

        Args:
            messages: The messages list to truncate.
            max_assistant_chars: Max cumulative chars allowed for assistant messages.

        Returns:
            Truncated messages list.
        """
        assistant_indices = [
            i for i, m in enumerate(messages)
            if m.get("role") == "assistant"
        ]
        if not assistant_indices:
            return messages

        total_assistant_chars = sum(
            len(messages[i].get("content", "")) for i in assistant_indices
        )

        if total_assistant_chars <= max_assistant_chars:
            return messages

        # ── Hard truncation: drop oldest assistant messages ────────────────
        dropped_count = 0
        dropped_chars = 0
        remaining = list(messages)

        # Scan from oldest to newest, dropping assistant messages until under threshold
        for idx in assistant_indices:
            if total_assistant_chars <= max_assistant_chars:
                break
            content = remaining[idx].get("content", "")
            # Phase 7.2: Labeled stub with metadata on what was dropped
            dropped_chars += len(content)
            total_assistant_chars -= len(content)
            _chars_in_msg = len(content)
            _tokens_in_msg = _chars_in_msg // 3  # rough estimate
            remaining[idx] = {
                "role": "assistant",
                "content": (
                    f"[SYSTEM KERNEL: History EVICTED  ~{_tokens_in_msg} tokens "
                    f"dropped to free VRAM. Total freed across this cycle: "
                    f"~{dropped_chars // 3} tokens in {dropped_count + 1} messages.]"
                )
            }
            dropped_count += 1

        print(f"  [Paging Kernel] ⚡ Aggressive truncation: dropped {dropped_count} assistant "
              f"messages ({dropped_chars} chars) to prevent LLM hang during PAGE_OUT.")
        return remaining


    def build_resume_payload(self, system_prompt: str) -> Dict[str, Any]:
        """Build the Ollama API payload for the auto-resume call.

        After a page operation completes, the kernel constructs a new
        Ollama request that includes:
          1. The original system prompt
          2. The paged-in content (if PAGE_IN)
          3. The continuation prompt telling the model to resume

        Directive C: Before building, aggressively truncate bloated assistant
        history to prevent LLM backend timeouts during PAGE_OUT.

        Args:
            system_prompt: The original system prompt text.

        Returns:
            Dict ready to be JSON-serialized for the Ollama /api/chat endpoint.
        """
        messages = [{"role": "system", "content": system_prompt}]

        if self.active_messages:
            for msg in self.active_messages.messages:
                # Only skip the root system_prompt, NOT dynamically injected
                # system messages (paged-in context blocks). Static guard against
                # silent context stripping that previously discarded every system
                # message including PAGE_IN content.
                if msg["role"] == "system" and msg["content"] == system_prompt:
                    continue
                messages.append(msg)

        # ── MemGPT: evict overflow turns to disk instead of silently dropping ──
        # Oldest non-system turns are persisted as individually addressable
        # blocks; each replaced with a PAGE_IN-able stub so the model can
        # recall them on demand.  Also checkpoints the trimmed window.
        messages = self.memgpt.evict_old_turns(messages)

        # ── Directive A: Ghost Buffer  inject partial generation as assistant message ──
        # This forces the LLM to seamlessly finish its thought on resume rather than
        # restarting its sentence (which would produce duplicated, invalid syntax).
        # EXCEPTION: suppress ghost buffer when the last PAGE_IN failed  the ghost
        # text ends immediately before the <invoke_kernel> tag and re-injecting it
        # causes the LLM to re-emit the paging protocol instructions verbatim.
        has_ghost = bool(self._ghost_buffer_text) and not self._last_page_failed
        if has_ghost:
            messages.append({
                "role": "assistant",
                "content": self._ghost_buffer_text,
            })

        # Determine the appropriate continuation prompt based on agent role.
        # Previously the coder prompt was hardcoded, which destroyed reviewer persona
        # and caused the reviewer to produce code output instead of evaluation.
        _is_reviewer = (
            "Review" in system_prompt or "Reviewer" in system_prompt
        )
        if has_ghost:
            if _is_reviewer:
                continuation_text = (
                    "[SYSTEM KERNEL: Paging complete. Continue your evaluation of the code. "
                    "Provide your PASS/FAIL verdict.]"
                )
            else:
                continuation_text = (
                    "[SYSTEM KERNEL: Paging complete. "
                    "Continue generating your response exactly where you left off. "
                    "Do not restart. Do not repeat previous output.]"
                )
        else:
            if _is_reviewer:
                continuation_text = (
                    "[SYSTEM KERNEL: Paging complete. The requested file content has been "
                    "injected above as a system message. Now evaluate the code. "
                    "Provide your PASS/FAIL verdict.]"
                )
            else:
                continuation_text = (
                    "[SYSTEM KERNEL: Paging complete. The requested file content has been "
                    "injected above as a system message. Now produce your complete code output "
                    "for this task. Output ONLY a code block  no prose, no explanations.]"
                )

        # Fix double-user message echoing: if the last message is already a "user"
        # message, concatenate the continuation_text to it instead of appending
        # a duplicate "user" message object.
        if messages and messages[-1].get("role") == "user":
            messages[-1]["content"] = messages[-1]["content"] + "\n" + continuation_text
        else:
            messages.append({"role": "user", "content": continuation_text})

        # Phase 7: Use the active model's context allocation for the resume
        # payload. 7B/8B models: 32768 ctx (24000-char page cap); phi3.5/14B: 16384
        # ctx (9000-char page cap); legacy 8K: 4800-char cap. allocated_ctx is set
        # by call_ollama_streamed from resolve_ctx_size() before the first stream.

        _resume_ctx = self.allocated_ctx
        payload = {
            "model": "",  # caller must set this
            "stream": True,
            "keep_alive": "0",
            "options": {
                "num_ctx": _resume_ctx,
                # Throttle output buffer pre-allocation during active context resumption loops
                "num_predict": 4096,
                "use_mmap": True,
            },
            "messages": messages,
        }
        return payload

    def reset_cycle(self):
        """Reset page cycle state after auto-resume begins."""
        self.buffer.reset()
        self._page_in_progress = False
        self._continuation_pending = False
        self._remaining_text = ""
        self._ghost_buffer_text = ""
        self._last_page_failed = False

    @property
    def page_cycle_count(self) -> int:
        return self._page_cycle_count

    @property
    def is_paging(self) -> bool:
        return self._page_in_progress

    @property
    def has_continuation(self) -> bool:
        return self._continuation_pending
