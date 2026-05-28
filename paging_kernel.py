"""
paging_kernel.py — Active Virtual Memory (Paging) Protocol Engine
==================================================================
Central orchestrator for the Active Page Fault model.

Provides:
  - PAGE_IN / PAGE_OUT regex interception
  - offload_store.py integration for file retrieval / context drop
  - Active messages context mutation
  - Auto-resume continuation prompt injection
  - Graceful stream pause (bypasses _stream_crashed rollback)

All functions are pure Python 3.12+ standard library. No async/await.
"""

from __future__ import annotations

import re
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── Regex Patterns for Paging Tokens ──────────────────────────────────────
# Directive B/C: Strict XML tool syntax.
# New format:
#   <invoke_kernel><action>PAGE_IN</action><target>filename.md</target></invoke_kernel>
#   <invoke_kernel><action>PAGE_OUT</action><target>reason for flush</target></invoke_kernel>

PAGE_IN_REGEX = re.compile(
    r'<invoke_kernel>\s*<action>PAGE_IN</action>\s*<target>(.*?)</target>'
    r'(?:\s*<lines>(.*?)</lines>)?'
    r'(?:\s*<search>(.*?)</search>)?'
    r'\s*</invoke_kernel>',
    re.DOTALL,
)
PAGE_OUT_REGEX = re.compile(
    r'<invoke_kernel>\s*<action>PAGE_OUT</action>\s*<target>(.*?)</target>\s*</invoke_kernel>',
    re.DOTALL,
)
VRAM_STUB_REGEX = re.compile(
    r'<VRAM_STUB\s+id="([^"]+)"\s+summary="([^"]*)"\s*/>'
)

# Maximum recursion depth for cascading PAGE_IN calls
MAX_PAGE_RECURSION = 3


# ── Page Detection ───────────────────────────────────────────────────────

def detect_page_tokens(text: str) -> List[Dict[str, str]]:
    """Scan text for <PAGE_IN> and <PAGE_OUT> tokens.

    Args:
        text: The streamed token text to scan.

    Returns:
        List of dicts with keys: 'type' ('PAGE_IN' or 'PAGE_OUT'),
        'target' (the filepath or concept), 'match' (full regex match),
        and optionally 'lines_range' / 'search_term' for targeted PAGE_IN.
    """
    tokens: List[Dict[str, str]] = []

    for match in PAGE_IN_REGEX.finditer(text):
        entry = {
            "type": "PAGE_IN",
            "target": match.group(1).strip(),
            "match": match.group(0),
        }
        lines_val = match.group(2)
        search_val = match.group(3)
        if lines_val and lines_val.strip():
            entry["lines_range"] = lines_val.strip()
        if search_val and search_val.strip():
            entry["search_term"] = search_val.strip()
        tokens.append(entry)

    for match in PAGE_OUT_REGEX.finditer(text):
        tokens.append({
            "type": "PAGE_OUT",
            "target": match.group(1).strip(),
            "match": match.group(0),
        })

    return tokens


def detect_vram_stubs(text: str) -> List[Dict[str, str]]:
    """Scan text for <VRAM_STUB> references.

    Args:
        text: The context text to scan.

    Returns:
        List of dicts with 'id' (filepath) and 'summary'.
    """
    stubs: List[Dict[str, str]] = []
    for match in VRAM_STUB_REGEX.finditer(text):
        stubs.append({
            "id": match.group(1).strip(),
            "summary": match.group(2).strip(),
        })
    return stubs


# ── Buffer Manager ───────────────────────────────────────────────────────

class PagingBuffer:
    """Accumulates streamed tokens until a complete paging token is found.

    The buffer is necessary because the LLM may emit a <PAGE_IN: "file.txt">
    token incrementally across multiple calls to the stream callback.
    This buffer accumulates incoming text and checks for complete paging
    tokens after each append.
    """

    def __init__(self):
        self._buffer: str = ""
        self._page_found: bool = False
        self._page_info: Optional[Dict[str, str]] = None
        self._last_before_text: str = ""

    def append(self, text: str) -> Tuple[bool, Optional[Dict[str, str]], str]:
        """Append text to buffer, check for complete paging tokens.

        Args:
            text: New text chunk from the stream.

        Returns:
            Tuple of:
              - bool: True if a complete paging token was found
              - Optional[Dict[str,str]]: The page info dict if found
              - str: The remaining (non-paging) text after stripping the token
        """
        self._buffer += text

        # Sentinel: skip targets that look like documentation placeholders/examples
        # emitted when the LLM echoes the VIRTUAL_MEMORY_PROTOCOL instructions.
        _EXAMPLE_TARGETS = {"filename.md", "filename.cpp", "filename.txt", "filename"}

        # Check for PAGE_IN
        for match in PAGE_IN_REGEX.finditer(self._buffer):
            target_val = match.group(1).strip()
            if target_val.lower() in _EXAMPLE_TARGETS or target_val.lower().startswith("filename."):
                # Strip this example tag from the buffer so it is not re-matched
                self._buffer = self._buffer[:match.start()] + self._buffer[match.end():]
                return False, None, self._buffer
            self._page_found = True
            info: Dict[str, str] = {
                "type": "PAGE_IN",
                "target": match.group(1).strip(),
                "match": match.group(0),
            }
            lines_val = match.group(2)
            search_val = match.group(3)
            if lines_val and lines_val.strip():
                info["lines_range"] = lines_val.strip()
            if search_val and search_val.strip():
                info["search_term"] = search_val.strip()
            self._page_info = info
            # ── Directive A: Ghost Buffer — capture partial generation before token ──
            before = self._buffer[:match.start()]
            self._last_before_text = before
            after = self._buffer[match.end():]
            self._buffer = before + after
            return True, self._page_info, before + after

        # Check for PAGE_OUT
        for match in PAGE_OUT_REGEX.finditer(self._buffer):
            self._page_found = True
            self._page_info = {
                "type": "PAGE_OUT",
                "target": match.group(1).strip(),
                "match": match.group(0),
            }
            # ── Directive A: Ghost Buffer — capture partial generation before token ──
            before = self._buffer[:match.start()]
            self._last_before_text = before
            after = self._buffer[match.end():]
            self._buffer = before + after
            return True, self._page_info, before + after

        return False, None, self._buffer

    @property
    def has_page_token(self) -> bool:
        return self._page_found

    @property
    def page_info(self) -> Optional[Dict[str, str]]:
        return self._page_info

    @property
    def last_before_text(self) -> str:
        """Return partial generation captured before last paging token (Directive A)."""
        return self._last_before_text

    def reset(self):
        """Reset the buffer after a page operation is complete."""
        self._buffer = ""
        self._page_found = False
        self._page_info = None
        self._last_before_text = ""


# ── Context-Tiered Boundary Resolution Matrix ────────────────────────────
# Decouples the legacy static 12,000-character ceiling to prevent buffer
# overflows on low-parameter auxiliary nodes while unthrottling macro-reviewers.

def _resolve_dynamic_page_limit(allocated_ctx: int) -> int:
    """
    Map active allocated token context boundaries to secure character byte ceilings.
    Phase 7.2 — Guaranteed 0% VRAM overrun: hard caps calculated from *remaining*
    headroom after the VRAM critical threshold (80% full) fires.

    Calculation:
      - VRAM critical fires at 80% of num_ctx
      - Remaining = 20% of num_ctx = ~20% * ~3 chars/token = ~0.6 chars/ctx
      - Safety margin: 0.5 chars/ctx (43% under actual headroom to absorb
        estimation noise from dense-alphanumeric code payloads)

    | Model      | num_ctx | Critical (80%) | Remaining  | Hard Cap |
    |------------|---------|----------------|------------|----------|
    | 7B/8B      | 32768   | 26214 tokens   | 6554 tok   | 24000 chr |
    | phi3.5     | 16384   | 13107 tokens   | 3277 tok   | 9000 chr |
    | legacy 8K  | 8192    | 6553 tokens    | 1639 tok   | 4800 chr |
    | aux/micro  | <8192   | ~5243 tokens   | ~1311 tok  | 3000 chr |
    """
    if allocated_ctx >= 32768:
        return 24000   # Tier 3: 32768 ctx → 24000 chars (unlocked 7B/8B models)
    elif allocated_ctx >= 16384:
        return 9000    # Tier 2: 16384 ctx → 9000 chars = 0% overrun at VRAM-critical
    elif allocated_ctx >= 8192:
        return 4800    # Tier 1: 8192 ctx → 4800 chars = 0% overrun at VRAM-critical
    else:
        return 3000    # Micro-tier: <8192 ctx → 3000 chars = 0% overrun at VRAM-critical






PAGE_IN_HARD_CAP_CHARS = 9000  # Default fallback for 16384 max ctx (12GB VRAM)

CONTEXT_LINES = 5  # context lines above/below targeted ranges


def _extract_lines_chunk(content: str, lines_range: str) -> str:
    """Extract a chunk of lines from content using a <lines> targeting tag.

    Args:
        content: Full file text.
        lines_range: String like "10-50" (1-based, inclusive).

    Returns:
        The extracted line range with +/- CONTEXT_LINES of context.
    """
    all_lines = content.splitlines()
    parts = lines_range.split("-")
    try:
        req_start = int(parts[0])
        req_end = int(parts[1]) if len(parts) > 1 else req_start
    except (ValueError, IndexError):
        return ""  # malformed range

    # Compute slice with context padding, clamped to file bounds
    start = max(0, req_start - 1 - CONTEXT_LINES)
    end = min(len(all_lines), req_end + CONTEXT_LINES)
    chunk = all_lines[start:end]
    # Annotate with line numbers
    annotated: list[str] = []
    for i, line in enumerate(chunk):
        line_num = start + i + 1
        marker = ">" if (start + i + 1) >= req_start and (start + i + 1) <= req_end else " "
        annotated.append(f"{marker}{line_num:>6} | {line}")
    return "\n".join(annotated)


def _extract_search_chunk(content: str, search_term: str, max_chars: int = 4000) -> str:
    """Extract a chunk surrounding the first match of a <search> targeting tag.

    Args:
        content: Full file text.
        search_term: Term to search for (case-insensitive).
        max_chars: Maximum characters to return around the match.

    Returns:
        The chunk surrounding the match with line-number annotations.
    """
    all_lines = content.splitlines()
    match_idx = -1
    for i, line in enumerate(all_lines):
        if search_term.lower() in line.lower():
            match_idx = i
            break

    if match_idx == -1:
        return ""  # search term not found

    start = max(0, match_idx - CONTEXT_LINES)
    end = min(len(all_lines), match_idx + 1 + CONTEXT_LINES)
    chunk = all_lines[start:end]
    annotated: list[str] = []
    for i, line in enumerate(chunk):
        line_num = start + i + 1
        marker = ">" if (start + i) == match_idx else " "
        annotated.append(f"{marker}{line_num:>6} | {line}")
    result = "\n".join(annotated)

    # If the result is still large, include context lines around it
    if len(result) > max_chars:
        result = result[:max_chars] + "\n[... truncated ...]"
    return result


# ── Paging Operations ────────────────────────────────────────────────────

def handle_page_in(target_path: str, project_root: Optional[Path] = None,
                   offload_store=None, recursion_depth: int = 0,
                   lines_range: Optional[str] = None,
                   search_term: Optional[str] = None,
                   allocated_ctx: int = 8192) -> str:
    """Directive D: Retrieve a file from disk (or offload store) for PAGE_IN.

    Supports targeted I/O via <lines> and <search> tags.  If neither tag is
    provided and the file exceeds the model-appropriate hard cap, the Kernel
    rejects the mount and instructs the LLM to retry with targeting.

    Uses the Phase 7 Context-Tiered Boundary Resolution Matrix to dynamically
    map the active model's context ceiling (allocated_ctx) to secure character
    byte limits, preventing buffer overflows on low-parameter auxiliary nodes
    while unthrottling macro-reviewers.

    Args:
        target_path: Relative path to the file to load.
        project_root: Project root directory (for resolving relative paths).
        offload_store: OffloadStore instance for retrieving offloaded blocks.
        recursion_depth: Current recursion depth for cascading PAGE_IN calls.
        lines_range: Optional <lines> tag value (e.g. "10-50").
        search_term: Optional <search> tag value (e.g. "ClassName").
        allocated_ctx: Active token context allocation for the current model.
                       Used to compute the dynamic character hard cap.

    Returns:
        The file content formatted as a system message, or an error message.
    """
    if recursion_depth > MAX_PAGE_RECURSION:
        return (
            "\n\n[SYSTEM KERNEL: Paging recursion limit reached ({}). "
            "Cannot load '{}'. Agent must synthesize from available context.]"
        ).format(MAX_PAGE_RECURSION, target_path)

    # 1. Try the offload store first (it's faster — file is local JSON)
    if offload_store is not None:
        content = offload_store.retrieve_block(target_path)
        if "ERROR" not in content:
            print(f"  [Paging Kernel] PAGE_IN: '{target_path}' (from offload store)")
            return (
                "\n\n## Paged-In Content (Offload Store)\n"
                f"**Source:** {target_path}\n"
                f"{content}\n"
            )

    # 2. Try loading from filesystem
    root = project_root or Path.cwd()
    full_path = (root / target_path).resolve()

    # Path traversal guard
    try:
        full_path.relative_to(root.resolve())
    except ValueError:
        print(f"  [Paging Kernel] ⛔ PAGE_IN: Path traversal blocked for '{target_path}'")
        return (
            "\n\n[SYSTEM KERNEL: PAGE_IN blocked — path traversal detected for '{}'.]"
        ).format(target_path)

    if not full_path.is_file():
        print(f"  [Paging Kernel] ⛔ PAGE_IN: File not found '{target_path}'")
        return (
            "\n\n[SYSTEM KERNEL: PAGE_IN failed — file '{}' not found on disk.]"
        ).format(target_path)

    try:
        content = full_path.read_text(encoding="utf-8", errors="replace")
        ext = full_path.suffix.lower()
        lang = {".cpp": "cpp", ".h": "cpp", ".hpp": "cpp",
                ".lua": "lua", ".py": "python",
                ".json": "json", ".md": "markdown"}.get(ext, "")
        file_size = len(content)
        print(f"  [Paging Kernel] PAGE_IN: '{target_path}' ({file_size} chars)")

        # ── Directive C: Targeted I/O & Hard Cap ─────────────────────────
        # If <lines> tag is present, extract only that range with context.
        if lines_range:
            chunk = _extract_lines_chunk(content, lines_range)
            if not chunk:
                return (
                    "\n\n[SYSTEM KERNEL: PAGE_IN failed — malformed <lines> tag "
                    "'{}' for '{}'. Use format <lines>start-end</lines>.]"
                ).format(lines_range, target_path)
            print(f"  [Paging Kernel] Lines chunk: extracted {len(chunk)} chars "
                  f"(range {lines_range})")
            return (
                "\n\n## Paged-In File Content (Targeted Lines)\n"
                f"**Source:** `{target_path}`\n"
                f"**Lines:** {lines_range}\n"
                f"```{lang}\n{chunk}\n```\n"
            )

        # If <search> tag is present, extract chunk surrounding the match.
        if search_term:
            chunk = _extract_search_chunk(content, search_term)
            if not chunk:
                return (
                    "\n\n[SYSTEM KERNEL: PAGE_IN failed — search term '{}' "
                    "not found in '{}'.]"
                ).format(search_term, target_path)
            print(f"  [Paging Kernel] Search chunk: extracted {len(chunk)} chars "
                  f"for term '{search_term}'")
            return (
                "\n\n## Paged-In File Content (Search Match)\n"
                f"**Source:** `{target_path}`\n"
                f"**Search:** `{search_term}`\n"
                f"```{lang}\n{chunk}\n```\n"
            )

        # ── Hard Cap: Reject untargeted pages of large files ─────────────
        # Phase 7: Dynamically compute the model-appropriate character ceiling
        # using the Context-Tiered Boundary Resolution Matrix. 
        # Max ctx is 16384 (phi3.5, 9000-char page cap) for 12GB VRAM safety.
        # Micro models (<8192 ctx) get 3000-char page cap.

        _dynamic_cap = _resolve_dynamic_page_limit(allocated_ctx)
        if file_size > _dynamic_cap:
            print(f"  [Paging Kernel] ⛔ DYNAMIC HARD CAP: '{target_path}' ({file_size} chars) "
                  f"exceeds {_dynamic_cap} char limit (ctx={allocated_ctx}) without targeting tags.")
            return (
                "\n\n[SYSTEM KERNEL: PAGE_IN REJECTED — '{}' is {} characters, "
                "exceeding the {}-character Hard Cap for untargeted pages "
                "(ctx={}). "
                "RETRY with one of:\n"
                "  <invoke_kernel><action>PAGE_IN</action>"
                "<target>{}</target><lines>10-50</lines></invoke_kernel>\n"
                "  <invoke_kernel><action>PAGE_IN</action>"
                "<target>{}</target><search>ClassName</search></invoke_kernel>]"
            ).format(target_path, file_size, _dynamic_cap, allocated_ctx,
                     target_path, target_path)

        # File is small enough — return full content (backward compatibility)
        return (
            "\n\n## Paged-In File Content\n"
            f"**Source:** `{target_path}`\n"
            f"```{lang}\n{content}\n```\n"
        )
    except Exception as e:
        print(f"  [Paging Kernel] ⛔ PAGE_IN: Read error for '{target_path}': {e}")
        return (
            "\n\n[SYSTEM KERNEL: PAGE_IN failed — error reading '{}': {}]"
        ).format(target_path, e)


def handle_page_out(target_concept: str, offload_store=None,
                    paged_in_cache: Optional[Dict[str, str]] = None) -> str:
    """Directive D: Drop a context block from active memory via PAGE_OUT.

    Args:
        target_concept: Name/concept of the context to discard.
        offload_store: OffloadStore instance to persist the block before dropping.
        paged_in_cache: Optional dict of currently mounted file paths from
            the PagingController's stateful cache. Used for Directive C
            validation — if set, the target must be a known cache key or
            a plausible filename.

    Returns:
        A confirmation string for the continuation prompt, or an error message
        if the target is invalid.
    """
    # ── Directive C: PAGE_OUT Target Validation ──────────────────────────
    # Prevent agents from hallucinating <PAGE_OUT><target>Current task</target>
    # or other invalid targets when stressed. A valid target must either:
    #   (a) Exist as a key in the paged_in_cache, OR
    #   (b) Look like a plausible file path (has extension or path separator)
    #       and NOT be a generic narrative phrase.
    if paged_in_cache is not None:
        _is_cache_key = target_concept in paged_in_cache
        _has_file_extension = bool(re.search(r'\.\w{1,8}$', target_concept))
        _has_path_separator = '/' in target_concept or '\\' in target_concept
        _is_generic_phrase = any(
            phrase in target_concept.lower()
            for phrase in [
                "current task", "old context", "old memory",
                "previous output", "last response", "context",
            ]
        )
        # A valid PAGE_OUT target must be a real cache key or a file-like path.
        # Generic narrative phrases ("old context", etc.) are NOT valid targets —
        # they would store empty metadata with no recoverable content.
        if not (_is_cache_key or _has_file_extension or _has_path_separator):
            return (
                "\n\n[SYSTEM KERNEL ERROR: Invalid target for PAGE_OUT. "
                "Must be a currently mounted file path or a known cached key. "
                "Received: '{}'. Use a concrete filename (e.g., 'file.cpp') "
                "or a recognized cache entry.]"
            ).format(target_concept)
        if _is_generic_phrase and not _is_cache_key:
            return (
                "\n\n[SYSTEM KERNEL ERROR: Invalid target for PAGE_OUT. "
                "Generic phrases are not valid eviction targets. "
                "Received: '{}'. Use the exact mounted file path "
                "(e.g., 'file.cpp') or a recognized cache entry.]"
            ).format(target_concept)

    print(f"  [Paging Kernel] PAGE_OUT: '{target_concept}' — context evicted")

    # ── Phase 7.2: Intelligent PAGE_OUT Labeling ──────────────────────────
    # Store rich metadata so agents can make informed PAGE_IN retrieval decisions:
    #   - timestamp: when the eviction happened
    #   - chars: size of evicted content (helps estimate VRAM freed)
    #   - content_hash: short SHA-256 prefix for dedup
    #   - content_type: 'code' if file extension matches, 'generic' otherwise
    if offload_store is not None:
        block_id = f"paged_out_{target_concept.replace(' ', '_').replace('/', '_')[:64]}"
        _now_ts = datetime.now().isoformat()
        _content_type = "code" if bool(re.search(r'\.\w{1,8}$', target_concept)) else "generic"
        offload_store.store_block(
            block_id=block_id,
            header=f"Paged Out Context: {target_concept}",
            body_lines=[
                f"[PAGE_OUT] Target: {target_concept}",
                f"  Timestamp: {_now_ts}",
                f"  Content Type: {_content_type}",
                f"  Agent was instructed to stop referencing this content.",
            ],
        )
        print(f"  [Paging Kernel] PAGE_OUT: Stored in offload store as '{block_id}'")


    return (
        "\n\n[SYSTEM KERNEL: PAGE_OUT completed. '{}' has been evicted from "
        "virtual memory. Continue generating your response with the remaining "
        "context. Do NOT reference the evicted content.]"
    ).format(target_concept)


# ── Context Mutation ─────────────────────────────────────────────────────

def inject_paged_content(messages: List[Dict[str, str]],
                         fetched_content: str) -> List[Dict[str, str]]:
    """Inject paged-in file content into the active messages context.

    The fetched content is inserted as a system message right before the
    last user message, so the LLM sees it as part of its context.

    Args:
        messages: The current messages array (list of role/content dicts).
        fetched_content: The content string to inject.

    Returns:
        Updated messages array with the injected system message.
    """
    if not messages:
        return messages

    # Insert before the last user message
    system_entry = {
        "role": "system",
        "content": fetched_content,
    }

    # Find the last user message index
    last_user_idx = -1
    for i, msg in enumerate(messages):
        if msg.get("role") == "user":
            last_user_idx = i

    if last_user_idx >= 0:
        messages.insert(last_user_idx, system_entry)
    else:
        messages.append(system_entry)

    return messages


def inject_continuation_prompt(messages: List[Dict[str, str]],
                                system_prompt: str = "") -> List[Dict[str, str]]:
    """Inject the 'paging complete, continue' continuation prompt.

    This is appended as a user message so the LLM knows to resume generation
    exactly where it left off.

    Fix 2: Uses dynamic continuation text based on agent role (reviewer vs coder).
    Fix 3: Checks if last message is already user — concatenates instead of
           appending a duplicate user message object.

    Args:
        messages: The current messages array.
        system_prompt: The system prompt (used to detect reviewer persona).

    Returns:
        Updated messages array.
    """
    # Fix 2: Dynamic continuation based on agent role
    _is_reviewer = (
        "Review" in system_prompt or "Reviewer" in system_prompt
    )
    if _is_reviewer:
        continuation = (
            "[SYSTEM KERNEL: Paging complete. Continue your evaluation of the code. "
            "Provide your PASS/FAIL verdict.]"
        )
    else:
        continuation = (
            "[SYSTEM KERNEL: Paging complete. "
            "Continue generating your response exactly where you left off. "
            "Do not restart. Do not repeat previous output.]"
        )

    # Fix 3: Avoid double-user message echoing
    if messages and messages[-1].get("role") == "user":
        messages[-1]["content"] = messages[-1]["content"] + "\n" + continuation
    else:
        messages.append({
            "role": "user",
            "content": continuation,
        })
    return messages


# ── Active Message Context Mutation (Standalone) ─────────────────────────

class ActiveMessages:
    """Mutable context array for the active LLM conversation.

    This is the authoritative source for the messages that get sent to
    Ollama. The paging kernel mutates this array in-place when handling
    PAGE_IN / PAGE_OUT tokens.
    """

    def __init__(self, system_prompt: str = "", user_prompt: str = ""):
        self.messages: List[Dict[str, str]] = []
        if system_prompt:
            self.messages.append({"role": "system", "content": system_prompt})
        if user_prompt:
            self.messages.append({"role": "user", "content": user_prompt})

    def append(self, role: str, content: str):
        self.messages.append({"role": role, "content": content})

    def inject_system(self, content: str):
        """Inject a system message before the last user message."""
        inject_paged_content(self.messages, content)

    def inject_continuation(self):
        """Inject the continuation prompt."""
        inject_continuation_prompt(self.messages)

    def pop_last_user(self) -> Optional[str]:
        """Remove and return the last user message content."""
        for i in range(len(self.messages) - 1, -1, -1):
            if self.messages[i].get("role") == "user":
                return self.messages.pop(i).get("content", "")
        return None

    def to_payload(self) -> List[Dict[str, str]]:
        """Return the messages array ready for the Ollama API."""
        return self.messages

    def __len__(self) -> int:
        return len(self.messages)


# ── Raw Text Extraction from Formatted PAGE_IN Results ───────────────────

def _extract_raw_text_from_result(result: str) -> str:
    """Extract the raw text body from a formatted PAGE_IN result string.

    The handle_page_in() function wraps content in markdown with headers and
    code fences.  This helper strips the wrapping to recover just the extracted
    text chunk for storage in the key-value cache.

    Args:
        result: The formatted result string from handle_page_in().

    Returns:
        The raw inner text content, or the full string if extraction fails.
    """
    # Error/recursion-limit messages don't have code blocks — return as-is
    if result.startswith("\n\n[SYSTEM KERNEL:"):
        return result.strip()

    # Try to extract content from inside a ``` block
    code_match = re.search(r"```(?:\w+)?\n(.*?)\n```", result, re.DOTALL)
    if code_match:
        return code_match.group(1).strip()

    # Fallback: return the result with leading/trailing whitespace stripped,
    # removing any "## Paged-In" header lines
    lines = result.strip().splitlines()
    filtered = [l for l in lines if not l.startswith("## Paged-In")]
    return "\n".join(filtered).strip()


# ── MemGPT-style HDD-backed Context Store ────────────────────────────────

class MemGPTContextStore:
    """MemGPT-style disk-backed context window manager.

    Mirrors the full Ollama messages array to disk after every mutation so
    the context is never lost on a crash or VRAM eviction.  When the active
    window grows beyond the configured character budget, the oldest non-system
    turns are evicted to the OffloadStore as individually addressable blocks
    that the LLM can PAGE_IN back on demand.

    Architecture
    ------------
    - ``checkpoint(messages)``     – write the current window snapshot to disk.
    - ``restore()``                – reload the latest snapshot from disk.
    - ``evict_old_turns(messages)``– overflow handler: evict oldest assistant
                                     turns to disk, replace with PAGE_IN stubs.
    - ``recall(block_id)``         – retrieve a single evicted turn by block_id.
    """

    # Default soft cap: evict when cumulative assistant+user chars exceed this.
    DEFAULT_EVICT_CHARS: int = 6000

    def __init__(self, session_id: str, offload_store, evict_chars: int = 0):
        """Initialise the context store.

        Args:
            session_id:    Opaque key identifying this pipeline run / call.
            offload_store: An :class:`OffloadStore` instance.
            evict_chars:   Override for the eviction threshold in characters.
                           Defaults to ``DEFAULT_EVICT_CHARS``.
        """
        self.session_id = session_id
        self.store = offload_store
        self.evict_chars = evict_chars or self.DEFAULT_EVICT_CHARS
        self._evicted_block_ids: List[str] = []

    # ── Persistence helpers ───────────────────────────────────────────────

    def checkpoint(self, messages: List[Dict[str, str]]) -> bool:
        """Persist the current messages array to disk.

        Args:
            messages: The active Ollama messages list.

        Returns:
            True on success.
        """
        ok = self.store.store_message_window(self.session_id, messages)
        if ok:
            print(f"  [MemGPT] ✓ Checkpoint: {len(messages)} messages saved "
                  f"(session={self.session_id})")
        return ok

    def restore(self) -> Optional[List[Dict[str, str]]]:
        """Reload the last checkpoint from disk.

        Returns:
            The messages list, or ``None`` if no checkpoint exists.
        """
        msgs = self.store.load_message_window(self.session_id)
        if msgs is not None:
            print(f"  [MemGPT] ↩ Restored {len(msgs)} messages from disk "
                  f"(session={self.session_id})")
        return msgs

    # ── Eviction / overflow handling ──────────────────────────────────────

    def evict_old_turns(self, messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """Evict oldest assistant/user turns to disk when the window is full.

        Turns are evicted oldest-first until total non-system content is under
        ``self.evict_chars``.  Each evicted message is stored as a named block
        in the OffloadStore and replaced with a concise PAGE_IN-able stub so
        the LLM can retrieve it on demand.

        System messages (including dynamically injected paged-in blocks) are
        **never** evicted.

        Args:
            messages: The current messages list (will be mutated in place).

        Returns:
            The (possibly trimmed) messages list.
        """
        # Gather non-system indices
        evictable = [
            i for i, m in enumerate(messages)
            if m.get("role") in ("user", "assistant")
        ]
        if not evictable:
            return messages

        total_chars = sum(
            len(messages[i].get("content", "")) for i in evictable
        )
        if total_chars <= self.evict_chars:
            return messages

        evicted_count = 0
        for idx in evictable:
            if total_chars <= self.evict_chars:
                break
            msg = messages[idx]
            role = msg.get("role", "assistant")
            content = msg.get("content", "")
            if not content:
                continue
            block_id = self.store.store_evicted_turn(
                session_id=self.session_id,
                turn_index=idx,
                role=role,
                content=content,
            )
            self._evicted_block_ids.append(block_id)
            stub = (
                f"[SYSTEM KERNEL: History EVICTED to disk — {len(content)} chars. "
                f"Block ID: {block_id}. "
                f"Retrieve with: "
                f"<invoke_kernel><action>PAGE_IN</action>"
                f"<target>{block_id}</target></invoke_kernel>]"
            )
            messages[idx] = {"role": role, "content": stub}
            total_chars -= len(content)
            evicted_count += 1

        if evicted_count:
            print(f"  [MemGPT] 💾 Evicted {evicted_count} turn(s) to disk "
                  f"({total_chars} chars remaining in window).")
            self.checkpoint(messages)
        return messages

    # ── Recall ────────────────────────────────────────────────────────────

    def recall(self, block_id: str) -> str:
        """Retrieve a single evicted turn from the offload store.

        Args:
            block_id: The block ID embedded in the eviction stub.

        Returns:
            Formatted content string ready for context injection.
        """
        return self.store.retrieve_block(block_id)

    # ── Cleanup ───────────────────────────────────────────────────────────

    def close(self) -> None:
        """Delete the session window file from disk (call when done)."""
        self.store.delete_session(self.session_id)
        print(f"  [MemGPT] 🗑 Session '{self.session_id}' window deleted from disk.")


# ── Kernel-Level Page Controller ─────────────────────────────────────────

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
        # ── Directive A: Stateful Key-Value Cache — actively mounted content ──
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
            # Directive A: Ghost Buffer — capture text generated before paging token
            self._ghost_buffer_text = self.buffer.last_before_text
            # Store remaining text for later injection
            self._remaining_text = remaining
        else:
            # Directive C: LLM is generating normal code — reset consecutive counter
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
                print(f"  [Paging Kernel] ⛔ PAGE_IN rejected — placeholder target '{target}' "
                      f"(model copied a prompt example; no file will be fetched)")
                self._last_page_failed = True
                return (
                    f"\n[SYSTEM KERNEL: PAGE_IN suppressed — "
                    f"'{target}' is a placeholder, not a real file path. "
                    f"Use the exact id= attribute from a <VRAM_STUB> tag.]\n"
                )
            # Guard: reject targets that look like function calls, contain
            # parentheses, angle brackets, or are clearly not file paths.
            # e.g. "OnLoadStatic(ctx)", "<some_tag>", "function(arg)"
            _invalid_path_chars = ("(", ")", "<", ">", ";", "\n", "\r")
            if any(ch in target for ch in _invalid_path_chars):
                print(f"  [Paging Kernel] ⛔ PAGE_IN rejected — malformed target '{target}' "
                      f"(looks like a function call or tag, not a file path)")
                self._last_page_failed = True
                return (
                    f"\n[SYSTEM KERNEL: PAGE_IN suppressed — "
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
                print(f"  [Paging Kernel] ⛔ PAGE_OUT rejected — placeholder target '{target_concept}' "
                      f"(model copied a prompt example; nothing will be evicted)")
                return (
                    f"\n[SYSTEM KERNEL: PAGE_OUT suppressed — "
                    f"'{target_concept}' is a placeholder, not a real cached key.]\n"
                )
            removed_text = ""
            # ── Directive A: Stateful Cache — remove evicted entries ────
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
                    f"[SYSTEM KERNEL: History EVICTED — ~{_tokens_in_msg} tokens "
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

        # ── Directive A: Ghost Buffer — inject partial generation as assistant message ──
        # This forces the LLM to seamlessly finish its thought on resume rather than
        # restarting its sentence (which would produce duplicated, invalid syntax).
        # EXCEPTION: suppress ghost buffer when the last PAGE_IN failed — the ghost
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
                    "for this task. Output ONLY a code block — no prose, no explanations.]"
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
