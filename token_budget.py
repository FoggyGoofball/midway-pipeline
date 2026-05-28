"""
Token budget manager — tracks and enforces token budget across pipeline execution.
Provides density-aware estimation and AST-aware/block-aware truncation.

No async/await — purely synchronous estimation and string manipulation.
"""

from __future__ import annotations
import re
from typing import List, Optional, Tuple


MAX_TOKENS: int = 12000

# ── Directive C: Kernel Interrupt Thresholds ────────────────────────────────
# These constants define the VRAM critical thresholds per model.
# When the estimated token payload exceeds the threshold, a Kernel warning
# is prepended to the prompt telling the agent to <PAGE_OUT>.
VRAM_CRITICAL_RATIO: float = 0.80
# ── Phase 7: VRAM-Guarded Model Token Limits ─────────────────────────────
# Synchronized with ollama_client.py OLLAMA_NUM_CTX/*_LARGE/*_MASSIVE values
# after unlocking VRAM context limits.
#
# Threshold = 80% of context window (VRAM_CRITICAL_RATIO = 0.80), giving
# headroom for output tokens and KV cache overhead.
# 
# Model VRAM budgets at these ctx sizes (q4_K_M + q8_0 KV cache):
#   7B at 32768: ~8-9GB (unlocked from 8192)
#   8B at 32768: ~8-9GB (unlocked from 8192)
#   14B at 16384: ~10-11GB (was 9-10GB at 8192)
#   phi3.5 at 16384: ~11-12GB (tight — was exceeding at 32768)
MODEL_TOKEN_LIMITS: dict = {
    # Thresholds = 80% of context window
    "qwen3.5:9b":          (6553, 8192),   # 80% of 8K
    "qwen2.5-coder:7b":    (26214, 32768), # 80% of 32K — unlocked
    "qwen2.5-coder:1.5b":  (6553, 8192),
    # Pre-summarizer: 3.8B mini — larger window is fine; it's ~2.5 GB
    "phi3.5":              (13107, 16384),  # 80% of 16K
    "phi-3.5":             (13107, 16384),  # format alias
    # phi3:14b bumped from 8K to 16K
    "phi3:14b":            (13107, 16384),  # 80% of 16K — was 8192
    "llama3.1:8b":         (26214, 32768),  # 80% of 32K — unlocked
    "llama3.2:1b":         (6553, 8192),
    "qwen3.5:14b":         (6553, 8192),
}




class TokenBudget:
    """Track and enforce token budget across pipeline execution.

    Industry standard pattern: measure before send, truncate before overflow.
    For qwen2.5-coder:7b, estimate 1 token ≈ 3 chars on average (code-heavy).
    """

    def __init__(self, hard_limit: int = MAX_TOKENS):
        self.hard_limit: int = hard_limit
        self.used: int = 0
        self.warnings: List[str] = []

    def estimate_tokens(self, text: str) -> int:
        """Rough token estimate with density-aware safety margin.

        For normal code/text, uses 1 token per 3 chars (standard heuristic).
        For dense data (base64, hex arrays, Unicode) where alphanumeric
        density > 60%, uses a conservative 1.5 char/token ratio to prevent
        Ollama truncating the JSON request mid-stream.

        Args:
            text: Text to estimate token count for.

        Returns:
            Estimated token count.
        """
        sample = text[:2000]
        if sample:
            alpha_count = sum(1 for c in sample if c.isalnum())
            density = alpha_count / len(sample)
            if density > 0.60:
                return int(len(text) * 2 // 3)  # ~1.5 chars/token (conservative for dense data)
        return len(text) // 3  # default: 1 token per 3 chars

    def check(self, text: str) -> bool:
        """Check if adding text would exceed budget. Returns True if safe.

        Args:
            text: Text to check against remaining budget.

        Returns:
            True if adding text stays within budget, False otherwise.
        """
        estimated = self.estimate_tokens(text)
        return (self.used + estimated) < self.hard_limit

    @staticmethod
    def get_model_threshold(model_name: str) -> Tuple[int, int]:
        """Get the VRAM critical threshold and context window for a model.

        Args:
            model_name: Name of the LLM model (e.g., 'qwen2.5-coder:7b').

        Returns:
            Tuple of (critical_token_threshold, context_window).
        """
        for key, (threshold, window) in MODEL_TOKEN_LIMITS.items():
            if key in model_name.lower():
                return threshold, window
        return 6000, 8192  # conservative default

    @staticmethod
    def check_vram_critical(system_prompt: str, user_prompt: str,
                            model_name: str) -> Optional[str]:
        """Directive C: Check if token payload exceeds 80% of context window.

        If the combined system + user prompt exceeds the model's VRAM critical
        threshold, prepend a Kernel warning instructing the agent to PAGE_OUT.

        Args:
            system_prompt: The system prompt text.
            user_prompt: The user prompt text.
            model_name: Model name to determine context window.

        Returns:
            A VRAM critical warning string if over threshold, None if safe.
        """
        combined = system_prompt + user_prompt
        estimated = TokenBudget.estimate_tokens_to(combined)
        threshold, context_window = TokenBudget.get_model_threshold(model_name)

        if estimated > threshold:
            pct = (estimated / context_window) * 100 if context_window > 0 else 0
            print(f"  [Kernel Interrupt] ⚠ VRAM critical: ~{estimated} tokens ({pct:.0f}% of {context_window})")
            return (
                "\n\n[SYSTEM KERNEL: VRAM critical (~{:.0f}% of {} context). "
                "You MUST emit <invoke_kernel><action>PAGE_OUT</action>"
                "<target>old context or topic</target></invoke_kernel> "
                "to free virtual memory before generating code. "
                "If you see <VRAM_STUB> pointers in your context, you may "
                "<invoke_kernel><action>PAGE_IN</action>"
                "<target>filename.md</target></invoke_kernel> "
                "the specific files you need, but only after freeing space first.]"
            ).format(pct, context_window)

        return None

    @staticmethod
    def estimate_tokens_to(text: str) -> int:
        """Estimate token count for text. Uses density-aware heuristic."""
        return TokenBudget.estimate_tokens_static(text)

    @staticmethod
    def estimate_tokens_static(text: str) -> int:
        """Density-aware token estimation (static version)."""
        sample = text[:2000]
        if sample:
            alpha_count = sum(1 for c in sample if c.isalnum())
            density = alpha_count / len(sample)
            if density > 0.60:
                return int(len(text) * 2 // 3)
        return len(text) // 3

    @staticmethod
    def _block_aware_collapse(text: str, available_chars: int,
                               core_memory_table: dict = None) -> str:
        """AST-aware / block-aware truncation.

        Instead of blindly keeping head+tail (which destroys function logic
        and agent reasoning in the middle), this method:
        1. Splits text into structural blocks (C++ functions, Lua blocks,
           Markdown headers, code fences)
        2. Preserves all block headers/signatures
        3. Collapses internal bodies with a [... collapsed ...] notice
        4. Drops the oldest blocks first (bottom of stack) when budget is tight
        5. EXCLUDES core_memory_table serialization from character-count evictions

        Args:
            text: Text to potentially truncate.
            available_chars: Maximum character budget.
            core_memory_table: Optional dict of core memory entries that must
                survive eviction (not counted in available_chars budget).

        Returns:
            Truncated text within character budget.
        """
        # Phase 0: Extract and preserve core_memory_table from eviction budget
        core_memory_block = ""
        core_serialized = ""
        if core_memory_table:
            core_lines = []
            for key, value in core_memory_table.items():
                core_lines.append(f"[CORE_MEMORY] {key}: {value}")
            core_serialized = "\n".join(core_lines)
            core_memory_block = (
                "\n## Core Memory Table (Phase I — MemGPT Protected)\n"
                f"{core_serialized}\n"
            )
            # Increase effective budget by the size of the core memory block
            available_chars += len(core_memory_block)

        if len(text) <= available_chars:
            if core_memory_block:
                return text + core_memory_block
            return text

        lines = text.splitlines(keepends=True)
        # Phase 1: Identify structural blocks
        blocks: list[dict] = []
        current_block = None
        in_fence = False

        for i, ln in enumerate(lines):
            stripped = ln.strip()

            # Track code fences
            if stripped.startswith("```"):
                in_fence = not in_fence
                if current_block is None:
                    blocks.append({
                        "header": ln.rstrip("\n"),
                        "body_lines": [],
                        "is_fence_block": True,
                    })
                    current_block = None
                else:
                    if current_block:
                        blocks.append(current_block)
                        current_block = None
                continue

            # Detect block headers
            is_header = False

            # C++ function / method signature
            if not in_fence:
                func_match = re.match(
                    r'^(\s*(?:static\s+|inline\s+|virtual\s+|const\s+)*'
                    r'(?:void|int|float|double|bool|char|std::\w+|\w+(?:<[^>]+>)?)\s+'
                    r'(?:[*&]?\s*)?'
                    r'\w+\s*\([^)]*\)\s*(?:const\s*)?(?:override\s*)?(?:final\s*)?'
                    r'(?:\s*throw\s*\([^)]*\)\s*)?\{?\s*)$',
                    stripped,
                )
                is_header = is_header or bool(func_match)

                if not is_header and stripped.endswith("{"):
                    if len(stripped) > 1 or (i > 0 and lines[i-1].strip().endswith(")")):
                        is_header = True

                # Lua function declarations (all three common forms):
                #   function Name(...)
                #   local function Name(...)
                #   Name = function(...)   /   Name.method = function(...)
                if not is_header:
                    lua_match = re.match(
                        r'^(?:local\s+)?function\s+[\w:.]+\s*\('
                        r'|^[\w.]+\s*=\s*function\s*\(',
                        stripped,
                    )
                    is_header = is_header or bool(lua_match)

                # Python def / class
                if not is_header:
                    py_match = re.match(r'^(?:async\s+)?def\s+\w+\s*\(|^class\s+\w+', stripped)
                    is_header = is_header or bool(py_match)

            # Markdown headers
            if stripped.startswith("##") or stripped.startswith("###") or stripped.startswith("# "):
                is_header = True

            if re.match(r'^\d+\.\s+\*\*', stripped):
                is_header = True

            if is_header:
                if current_block is not None:
                    blocks.append(current_block)
                current_block = {
                    "header": ln.rstrip("\n"),
                    "body_lines": [],
                    "is_fence_block": False,
                }
            else:
                if current_block is not None:
                    current_block["body_lines"].append(ln)

        if current_block is not None:
            blocks.append(current_block)

        # Phase 1.5: Build a pinned Context Index (TOC) from all block headers.
        # This tiny block is inserted at position-0 in the final output and is
        # NEVER evicted, so the agent always has a complete symbol map even after
        # severe context collapse.  Cap at 500 chars to guarantee survival.
        _toc_lines: list[str] = []
        for _b in blocks:
            _h = _b["header"].strip()
            if _h and not _b.get("is_fence_block"):
                _toc_lines.append(f"  • {_h}")
        _toc_block = ""
        if _toc_lines:
            _toc_body = "\n".join(_toc_lines)
            if len(_toc_body) > 480:
                _toc_body = _toc_body[:480] + "\n  … (truncated)"
            _toc_block = f"## Context Index\n{_toc_body}\n"

        # Phase 2: Prune oldest blocks first (bottom of stack) —
        # but preserve the first header block (director's task breakdown)
        preserved_first = None
        if blocks and not blocks[0].get("is_fence_block"):
            preserved_first = blocks.pop(0)

        # Phase 3: Collapse from the end, offloading bodies to OffloadStore
        import hashlib
        budget = available_chars
        output_lines: list[str] = []
        offload_count = 0
        _evicted_symbols: list[str] = []
        for block in reversed(blocks):
            block_text = block["header"] + "\n" + "".join(block["body_lines"])
            block_is_collapsible = (
                not block.get("is_fence_block")
                and len(block["body_lines"]) > 3
            )
            if block_is_collapsible:
                collapsed_len = len(block["header"]) + 120  # room for VRAM_STUB tag
                if len(output_lines) + collapsed_len <= budget:
                    # Offload the full body to OffloadStore before collapsing
                    body_full = "".join(block["body_lines"])
                    body_hash = hashlib.sha256(body_full.encode("utf-8")).hexdigest()[:12]
                    block_id = f"collapsed_{body_hash}"
                    # Extract a human-readable symbol name from the header for the stub tag.
                    _raw_header = block["header"].strip()
                    _sym_match = re.search(
                        r'(?:function\s+|def\s+|class\s+)([\w:.]+)'
                        r'|^([\w:.]+)\s*[=(]',
                        _raw_header,
                    )
                    _symbol_name = (
                        (_sym_match.group(1) or _sym_match.group(2)).strip()
                        if _sym_match else _raw_header[:40].replace('"', "'")
                    )
                    try:
                        from offload_store import get_offload_store
                        _store = get_offload_store()
                        _store.store_block(
                            block_id=block_id,
                            header=block["header"][:120],
                            body_lines=[body_full],
                        )
                        offload_count += 1
                        _evicted_symbols.append(_symbol_name)
                        output_lines.insert(0, block["header"])
                        _summary = _raw_header[:80].replace('"', "'")
                        output_lines.insert(1, (
                            f'<VRAM_STUB id="{block_id}" '
                            f'symbol="{_symbol_name}" '
                            f'summary="{_summary}" '
                            f'total_chars="{len(body_full)}" />'
                        ))
                    except Exception:
                        # Fallback: collapse without offload
                        output_lines.insert(0, block["header"])
                        output_lines.insert(1, "[... body collapsed ...]")
                else:
                    break
            else:
                if len(output_lines) + len(block_text) <= budget:
                    output_lines.insert(0, block_text)
                else:
                    break

        # Re-insert the preserved first block (the director's breakdown)
        if preserved_first:
            first_text = preserved_first["header"] + "\n" + "".join(preserved_first["body_lines"])
            output_lines.insert(0, first_text)

        # Pin the Context Index at the very top so it is never evicted.
        if _toc_block:
            output_lines.insert(0, _toc_block)

        # Append offload inventory notice if any blocks were offloaded
        if offload_count > 0:
            _sym_list = ", ".join(_evicted_symbols[:8])
            if len(_evicted_symbols) > 8:
                _sym_list += f" … (+{len(_evicted_symbols) - 8} more)"
            output_lines.append(
                f"\n\n[SYSTEM: {offload_count} block(s) offloaded to VRAM — "
                f"evicted symbols: {_sym_list}. "
                f"To restore a body, use: "
                f'<invoke_kernel><action>PAGE_IN</action>'
                f'<target>FILE_PATH</target><search>SYMBOL_NAME</search></invoke_kernel>. '
                f"The ## Context Index above lists all symbol names.]"
            )

        result = "".join(output_lines)
        if len(result) > available_chars:
            result = result[:available_chars] + "\n[... final truncation ...]"
        return result
