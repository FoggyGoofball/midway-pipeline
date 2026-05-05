"""
Token budget manager — tracks and enforces token budget across pipeline execution.
Provides density-aware estimation and AST-aware/block-aware truncation.

No async/await — purely synchronous estimation and string manipulation.
"""

from __future__ import annotations
import re
from typing import List, Optional


MAX_TOKENS: int = 12000


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
                return len(text) // 2  # 1.5 char/token conservative
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
    def _block_aware_collapse(text: str, available_chars: int) -> str:
        """AST-aware / block-aware truncation.

        Instead of blindly keeping head+tail (which destroys function logic
        and agent reasoning in the middle), this method:
        1. Splits text into structural blocks (C++ functions, Lua blocks,
           Markdown headers, code fences)
        2. Preserves all block headers/signatures
        3. Collapses internal bodies with a [... collapsed ...] notice
        4. Drops the oldest blocks first (bottom of stack) when budget is tight

        Args:
            text: Text to potentially truncate.
            available_chars: Maximum character budget.

        Returns:
            Truncated text within character budget.
        """
        if len(text) <= available_chars:
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

        # Phase 2: Prune oldest blocks first (bottom of stack) —
        # but preserve the first header block (director's task breakdown)
        preserved_first = None
        if blocks and not blocks[0].get("is_fence_block"):
            preserved_first = blocks.pop(0)

        # Phase 3: Collapse from the end
        budget = available_chars
        output_lines: list[str] = []
        for block in reversed(blocks):
            block_text = block["header"] + "\n" + "".join(block["body_lines"])
            block_is_collapsible = (
                not block.get("is_fence_block")
                and len(block["body_lines"]) > 3
            )
            if block_is_collapsible:
                collapsed_len = len(block["header"]) + 40
                if len(output_lines) + collapsed_len <= budget:
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

        result = "".join(output_lines)
        if len(result) > available_chars:
            result = result[:available_chars] + "\n[... final truncation ...]"
        return result
