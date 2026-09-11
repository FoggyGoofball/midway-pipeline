"""
_helpers_exec.py — Base Layer & LLM Logic for the mesh consensus pipeline.
Contains: globals/config, intent classification, GDD librarian,
project state, director prompt, task execution.

No async/await — purely synchronous.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from token_budget import TokenBudget


# -- Configuration re-exports (set by pipeline.py at import time) -----------

PROJECT_ROOT: Path = Path(os.getenv("MIDWAY_PROJECT_ROOT", Path(__file__).resolve().parent.with_name("midway")))
MAX_ITERATIONS = 3
MAX_CONSENSUS_ITERATIONS = 3
MAX_SUBTASKS_PER_AGENT = 5
REVIEW_MAX_ITERATIONS = 3
SCOPE_FILE_LIMIT = 5
SCOPE_LINE_LIMIT = 400
_ALL_DOMAINS = {}  # populated by pipeline.py init


def _init_config(project_root: Path, all_domains: dict, **kwargs):
    """Initialize configuration from pipeline.py module-level constants."""
    global PROJECT_ROOT, _ALL_DOMAINS, MAX_ITERATIONS, MAX_CONSENSUS_ITERATIONS
    global MAX_SUBTASKS_PER_AGENT, REVIEW_MAX_ITERATIONS, SCOPE_FILE_LIMIT, SCOPE_LINE_LIMIT
    PROJECT_ROOT = project_root
    _ALL_DOMAINS = all_domains
    for k, v in kwargs.items():
        if k in globals():
            globals()[k] = v


# -- Intent Classification --------------------------------------------------

from _prompts import INTENT_CLASSIFIER_SYSTEM as _INTENT_CLASSIFIER_SYSTEM_BASE
# Use the canonical definition from _prompts; keep the local name for callers.
INTENT_CLASSIFIER_SYSTEM = _INTENT_CLASSIFIER_SYSTEM_BASE

# Deterministic intent classification regexes (LLM classifier removed for
# latency).  Order matters: modification verbs take priority.
_MODIFICATION_PATTERNS = [
    r"\b(add|create|implement|fix|repair|modify|change|build|generate|write|remove|delete|update|refactor|extend|integrate|wire|hook|expose|register|port|migrate|convert|rename|optimize|upgrade|patch)\w*\b",
]
_QUERY_PATTERNS = [
    r"\b(where is|where are|which file|what file|find|locate|search|grep|look up|look for)\b",
]
_INFORMATIONAL_PATTERNS = [
    r"\b(explain|describe|summarize|overview|walk me through|tell me about|how does|how do|what is|what are|how is|how are|document|understand)\b",
]


def classify_intent(user_prompt: str, call_ollama_func=None, director_model: str = "") -> str:
    """Deterministic intent classification (no LLM call).

    ``call_ollama_func`` and ``director_model`` are retained for backward
    compatibility with existing call sites but are no longer consulted.
    """
    prompt = user_prompt.lower().strip()

    # Chat fast-path.  Lazy import avoids a module-load cycle with
    # _helpers_text (which imports _helpers_exec at load time).
    from _helpers_text import is_likely_chat
    if is_likely_chat(prompt):
        return "CHAT"

    # Modification verbs take priority so a build request is never
    # under-routed into a read-only path.  Unmatched prompts fall back to
    # MODIFICATION (the previous LLM classifier's default).
    for _pat in _MODIFICATION_PATTERNS:
        if re.search(_pat, prompt):
            return "MODIFICATION"
    for _pat in _QUERY_PATTERNS:
        if re.search(_pat, prompt):
            return "QUERY"
    for _pat in _INFORMATIONAL_PATTERNS:
        if re.search(_pat, prompt):
            return "INFORMATIONAL"
    return "MODIFICATION"


# -- GDD Librarian ----------------------------------------------------------

def recursive_librarian(user_prompt: str,
                        _extract_gdd_sections_func=None,
                        _gdd_section_map=None,
                        _keyword_to_section=None) -> str:
    """Query the Librarian for all relevant GDD sections to build full context.

    Note:
        The last three parameters are deprecated (kept for backward compat).
        Only ``user_prompt`` is used; the function delegates directly to
        ``context_extractor.extract_project_context()``.
    """
    from context_extractor import extract_project_context
    sections_text = extract_project_context(user_prompt)
    return sections_text or ""


# -- Project State ----------------------------------------------------------

def get_project_state(project_root: Path = None, all_domains: dict = None) -> str:
    """Reads docs/completed_features.md and docs/todo.md for project summary."""
    pr = project_root or PROJECT_ROOT
    domains = all_domains or _ALL_DOMAINS
    lines = ["## Current Project State\n"]
    completed_path = pr / "docs" / "completed_features.md"
    if completed_path.is_file():
        try:
            text = completed_path.read_text(encoding="utf-8", errors="replace")
            done_sections = re.findall(r"### ✅ (.+)", text)
            if done_sections:
                lines.append("### ✅ Implemented Systems")
                for s in done_sections[:15]:
                    lines.append(f"- {s}")
                if len(done_sections) > 15:
                    lines.append(f"- ... ({len(done_sections) - 15} more)")
                lines.append("")
        except Exception:
            pass
    todo_path = pr / "docs" / "todo.md"
    if todo_path.is_file():
        try:
            text = todo_path.read_text(encoding="utf-8", errors="replace")
            lines.append("### Project TODO List (Unimplemented)")
            for line in text.splitlines():
                # Keep headers, table formatting, and non-done items
                if "✅ Done" not in line and line.strip():
                    lines.append(line)
            lines.append("")
        except Exception:
            pass
    lines.append("### 🟢 Available Domains")
    for key, domain in domains.items():
        if domain["ready"]:
            lines.append(f"- {domain['tag']} {domain['description']}")
    lines.append("")
    lines.append("### 🔴 Unavailable Domains")
    for key, domain in domains.items():
        if not domain["ready"]:
            lines.append(f"- {domain['tag']} {domain['description']}")
    lines.append("")
    lines.append("### ❌ Does NOT Exist")
    lines.append("- No networking/multiplayer code at all")
    lines.append("- No Box2D physics integration")
    lines.append("- No audio engine (SoLoud not integrated)")
    lines.append("- No save/load system")
    lines.append("- No boss encounters")
    lines.append("- No prize/augment runtime loading")
    lines.append("- No Barker billboarding system")
    lines.append("")
    return "\n".join(lines)


def get_available_domains_text(all_domains: dict = None) -> str:
    domains = all_domains or _ALL_DOMAINS
    parts = []
    for key, domain in domains.items():
        if domain["ready"]:
            parts.append(f"- {domain['tag']} {domain['description']}")
    return "\n".join(parts)


def get_unavailable_domains_text(all_domains: dict = None) -> str:
    domains = all_domains or _ALL_DOMAINS
    parts = []
    for key, domain in domains.items():
        if not domain["ready"]:
            parts.append(f"- {domain['tag']} {domain['description']}")
    return "\n".join(parts)


# -- Per-Task GDD Distiller --------------------------------------------------

_GDD_DISTILL_THRESHOLD: int = 4000   # chars — below this, no model call needed
_GDD_DISTILL_TARGET: int = 3000      # desired output size in chars

# Cache distilled GDD slices so repeated fix cycles / retries for the same
# task don't re-invoke the phi3.5 pre-summarizer with identical input.
_GDD_DISTILL_CACHE: Dict[tuple, str] = {}
_GDD_DISTILL_CACHE_MAX: int = 32


def _distill_gdd_for_task(task_spec: str, gdd_text: str,
                          attraction_name: str = "") -> str:
    """Compress an oversized GDD extract to only the content relevant to task_spec.

    Uses the phi3.5 pre-summarizer (fast, 16K context) to produce a tight,
    task-focused slice of the GDD.  Only called when ``gdd_text`` exceeds
    ``_GDD_DISTILL_THRESHOLD`` chars so the fast path (small extracts) has
    zero overhead.

    Args:
        task_spec:       The task's spec string (used as the relevance query).
        gdd_text:        Full GDD extract to compress.
        attraction_name: Optional name of the target attraction (e.g. "skeeball").
                         When provided, the distiller is instructed to discard
                         sections about other named attractions.

    Returns:
        Compressed GDD text, or the original text if compression fails or
        the model is unavailable.
    """
    _cache_key = (attraction_name, task_spec, gdd_text)
    _cached = _GDD_DISTILL_CACHE.get(_cache_key)
    if _cached is not None:
        return _cached

    try:
        from ollama_client import call_ollama as _call_ollama
        from ollama_client import PRE_SUMMARIZER_MODEL as _SUMM_MODEL
    except ImportError:
        return gdd_text

    _attraction_clause = ""
    if attraction_name:
        _attraction_clause = (
            f"The task is implementing the '{attraction_name}' attraction. "
            f"DISCARD any paragraphs that describe a different named attraction or mini-game "
            f"(e.g. Plinko, Ring Toss, Coin Cascade, Slingshot Array) unless they contain "
            f"a mechanic rule that also applies to '{attraction_name}'. "
        )

    system = (
        "You are a precise context distiller. "
        "You will receive a task specification and a large block of game-design document (GDD) text. "
        + _attraction_clause +
        "Extract and return ONLY the paragraphs, rules, values, and examples that are directly "
        f"relevant to implementing the task. Omit unrelated sections entirely. "
        f"Keep output under {_GDD_DISTILL_TARGET} characters. "
        "Output plain text — no commentary, no headings invented by you."
    )
    user = (
        f"## Task Specification\n{task_spec}\n\n"
        f"## GDD Content to Distill\n{gdd_text}"
    )

    result = _call_ollama(system, user, "GDD Distiller", _SUMM_MODEL,
                          skip_pre_summarizer=True)
    if result and len(result.strip()) > 100:
        _label = attraction_name or task_spec[:40]
        print(f"  [GDD Distiller] Compressed {len(gdd_text)} -> {len(result)} chars "
              f"for '{_label}'")
        _final = result.strip()
    else:
        _final = gdd_text

    _GDD_DISTILL_CACHE[_cache_key] = _final
    if len(_GDD_DISTILL_CACHE) > _GDD_DISTILL_CACHE_MAX:
        _GDD_DISTILL_CACHE.pop(next(iter(_GDD_DISTILL_CACHE)))
    return _final


# -- SEARCH/REPLACE Block Extractor (Step 4: Merge Abolition) ----------------

def _normalize_for_fuzzy_match(text: str) -> str:
    """Normalize text for fuzzy matching by stripping leading/trailing
    whitespace per line, normalizing indentation, and removing blank lines.

    This allows SEARCH blocks generated by small LLMs (e.g. qwen2.5-coder:7b)
    to match even when they have extra indentation, trailing spaces, or
    missing/extra blank lines compared to the target file.
    """
    lines = text.splitlines()
    # Strip leading/trailing whitespace from each line, and drop any copied
    # line-number prefix ("77 |", "80 >", "12|") that small coders paste from
    # the staging block.  These prefixes never exist on disk and are the #1
    # cause of "SEARCH block not found" on otherwise-correct edits.
    _line_no_prefix_re = re.compile(r'^\s*\d+\s*[|>]\s?')
    stripped = []
    for line in lines:
        s = _line_no_prefix_re.sub('', line).strip()
        if s:
            stripped.append(s)
    return "\n".join(stripped)


def _fuzzy_apply_patch(file_content: str, search_text: str, replace_text: str) -> str:
    """Apply a SEARCH/REPLACE patch with fuzzy matching fallback.

    Attempts exact match first. If that fails, normalizes both the SEARCH block
    and the file content by stripping per-line whitespace, indentation differences,
    and blank lines, then tries matching in the normalized space.

    When a fuzzy match is found, the replacement is applied to the original
    (non-normalized) content so formatting is preserved as much as possible.
    """
    # ── Fuzzy match logging ────────────────────────────────────────────────
    # Log each stage so we can diagnose brittle SEARCH/REPLACE failures
    # without having to trace through every fallback path manually.
    import logging as _fl_logging
    _fl_logger = _fl_logging.getLogger("fuzzy_apply_patch")
    if not _fl_logger.handlers:
        _fl_handler = _fl_logging.StreamHandler()
        _fl_handler.setFormatter(_fl_logging.Formatter(
            "  [FuzzyPatch] %(levelname)s: %(message)s"
        ))
        _fl_logger.addHandler(_fl_handler)
        _fl_logger.setLevel(_fl_logging.DEBUG)

    # 1. Try exact match first (fast path)
    if search_text in file_content:
        _fl_logger.debug("Exact match succeeded.")
        return file_content.replace(search_text, replace_text, 1)

    _fl_logger.debug("Exact match failed  trying fuzzy (normalize+slide)...")

    # 2. Try normalizing around indentation/blank lines
    search_normalized = _normalize_for_fuzzy_match(search_text)
    if not search_normalized:
        _fl_logger.warning("Search text empty after normalization.")
        return file_content  # empty search after normalization - can't match

    file_lines = file_content.splitlines()
    search_lines = search_normalized.splitlines()
    n_search = len(search_lines)

    # Slide a window across the file, normalizing each window for comparison
    _fuzzy_match_found = False
    for i in range(len(file_lines) - n_search + 1):
        window = file_lines[i:i + n_search]
        window_normalized = _normalize_for_fuzzy_match("\n".join(window))
        if window_normalized == search_normalized:
            _fl_logger.debug(f"Fuzzy (normalize+slide) matched at line {i+1}.")
            _fuzzy_match_found = True
            original_window = "\n".join(window)
            return file_content.replace(original_window, replace_text, 1)

    # 3. Try matching against the normalized file content if sliding window failed
    # This is useful when the SEARCH block has fewer lines than the match target
    # (e.g. the model omitted blank lines or comment-only lines)
    if not _fuzzy_match_found:
        _fl_logger.debug("Fuzzy (normalize+slide) failed  trying normalized-content match...")
        file_normalized = _normalize_for_fuzzy_match(file_content)
        if search_normalized in file_normalized:
            # Can't do a precise line-level replace, but we can try one more approach:
            # find the line index where the normalized search starts
            file_norm_lines = file_normalized.splitlines()
            for i in range(len(file_norm_lines) - n_search + 1):
                if file_norm_lines[i:i + n_search] == search_lines:
                    # Map back to original line content at this position
                    # by finding the corresponding non-blank lines in the original
                    orig_line_idx = 0
                    orig_nonblank = 0
                    for idx, line in enumerate(file_lines):
                        if line.strip():
                            if orig_nonblank == i:
                                orig_line_idx = idx
                                break
                            orig_nonblank += 1
                    # Take the same number of non-blank lines from the original
                    orig_window = []
                    count = 0
                    for idx in range(orig_line_idx, len(file_lines)):
                        orig_window.append(file_lines[idx])
                        if file_lines[idx].strip():
                            count += 1
                            if count >= n_search:
                                break
                    if orig_window:
                        original_window_str = "\n".join(orig_window)
                        if original_window_str in file_content:
                            _fl_logger.debug(f"Normalized-content match at nonblank line {i+1}.")
                            return file_content.replace(original_window_str, replace_text, 1)

    # Level-4 fallback removed: it aggressively skipped comment lines and blank
    # lines from the SEARCH block, which caused false-positive matches that
    # replaced the wrong window and corrupted the target file.  The three
    # levels above (exact, normalize+slide, normalized-content) handle all
    # real-world SEARCH/REPLACE variation safely.  If they all fail, the patch
    # is too divergent and should be rejected rather than risking corruption.

    # No match found - log and return unchanged
    _fl_logger.warning(
        f"No match found after all fallbacks. "
        f"Search preview: {search_text[:80].replace(chr(10), chr(32))!r}"
    )
    return file_content



def _extract_search_replace_blocks(output: str) -> list[dict[str, str]]:
    """Parse `<<<<<<< SEARCH` ... `=======` ... `>>>>>>> REPLACE` blocks from output.

    Returns a list of dicts, each with ``search`` and ``replace`` keys.
    The parser is resilient to whitespace variation around the delimiters
    but requires the exact delimiter strings.

    Handles multiple blocks per output, including blocks that have empty
    SEARCH sections (new file insertions).
    """
    blocks: list[dict[str, str]] = []
    # Pattern: optional whitespace, <<<<<<< SEARCH, content, =======, content, >>>>>>> REPLACE.
    # Delimiters are lenient ({5,7}) because small coder models often emit
    # 5-char "<<<<<" markers instead of the canonical 7-char conflict markers.
    pattern = r'<{5,7}\s*SEARCH\s*\n(.*?)\n\s*={5,7}\s*\n(.*?)\n\s*>{5,7}\s*REPLACE'
    for match in re.finditer(pattern, output, re.DOTALL):
        blocks.append({
            "search": match.group(1),
            "replace": match.group(2),
        })
    return blocks

def _unwrap_json_code(output: str) -> str:
    """Unwrap deepseek-style JSON-wrapped code into plain code / SEARCH-REPLACE text.

    deepseek-coder-v2 (and other chat-tuned models) wrap their answer in a
    ```json fence with one of several shapes:
        {"response": {"code_block": "..."}}
        {"implementation": {"code": [...]}}
        {"code": [{"search": ..., "replace": ...}]}
        {"SEARCH": ..., "REPLACE": ...}
    The downstream _extract_search_replace_blocks() / anchor-splice only
    understand plain text, so such output is otherwise treated as "no
    SEARCH/REPLACE" (worse: written verbatim as a bogus full-file scaffold).

    Returns the original string unchanged when it is not JSON-wrapped.
    """
    import json as _json
    _text = output.strip()
    _fence = re.match(r'^```(?:json)?\s*\n?(.*?)\n?\s*```$', _text, re.DOTALL)
    if _fence:
        _text = _fence.group(1).strip()
    _start = _text.find('{')
    if _start == -1:
        return output
    _end = _text.rfind('}')
    if _end <= _start:
        return output
    try:
        _obj = _json.loads(_text[_start:_end + 1])
    except Exception:
        return output

    _parts: list[str] = []
    _seen_searches = set()

    def _collect(v) -> None:
        if isinstance(v, str):
            if v.strip():
                _parts.append(v)
        elif isinstance(v, list):
            for _item in v:
                _collect(_item)
        elif isinstance(v, dict):
            _s = v.get("search") or v.get("SEARCH") or ""
            _r = v.get("replace") or v.get("REPLACE") or ""
            if _s or _r:
                if isinstance(_r, list):
                    _r = "\n".join(str(x) for x in _r)
                _s = _s if isinstance(_s, str) else str(_s)
                _key = (_s[:80], str(_r)[:80])
                if _key not in _seen_searches:
                    _seen_searches.add(_key)
                    _parts.append(f"<<<<<<< SEARCH\n{_s}\n=======\n{_r}\n>>>>>>> REPLACE")
                return
            for _k in ("code_block", "code", "implementation", "response"):
                if _k in v:
                    _collect(v[_k])

    _collect(_obj)
    if not _parts:
        return output
    _joined = "\n".join(_parts)
    return _joined if _joined.strip() != output.strip() else output



# -- Anchor Splice Fallback: deterministic merge when LLM refuses SEARCH/REPLACE ----
# When the LLM outputs a full file instead of a SEARCH/REPLACE block, this
# function extracts the game-specific logic from the LLM output and splices
# it into the skeleton at the anchor marker.  The lifecycle invariants
# (OnLoadStatic/OnLoad/OnUnload/SLOT_ID) are NEVER overwritten — they were
# written by Phase A and are structurally guaranteed.
#
# Strategy:
#   1. Find the anchor marker in the skeleton file
#   2. Find the same anchor marker (or nearest lifecycle context) in the LLM output
#   3. Extract text between that anchor and the next anchor marker in the LLM output
#   4. Splice that text into the skeleton at the anchor marker position

_ANCHOR_SPLICE_PATTERN = re.compile(
    r'--\s*\[TASK_(\d+)_INSERT_HOOK\]\s*--?\s*(.*?)(?=\n\s*--\s*\[TASK_\d+_INSERT_HOOK\]|\nend|\Z)',
    re.DOTALL
)


def _anchor_splice_fallback(
    file_content: str,
    llm_output: str,
    anchor_marker: str,
    target_path: str = "",
) -> str:
    """Merge game-specific code from *llm_output* into *file_content* at *anchor_marker*.

    Args:
        file_content: Current on-disk skeleton file content (with anchor markers).
        llm_output: Full-file LLM output that contains code to merge.
        anchor_marker: The anchor marker line this task was assigned.
        target_path: For logging, the file path.

    Returns:
        Merged file content with LLM's implementation spliced in at the anchor.
        Returns original *file_content* unchanged if merge cannot be determined.
    """
    # 1. Find the anchor in the skeleton
    if anchor_marker not in file_content:
        print(f"  [Anchor Splice] ⚠ Anchor '{anchor_marker[:60]}' not found in skeleton "
              f"for {target_path} — cannot merge")
        return file_content

    # 2. Find the same anchor in the LLM output
    if anchor_marker not in llm_output:
        print(f"  [Anchor Splice] ⚠ Anchor '{anchor_marker[:60]}' not found in LLM output "
              f"for {target_path} — cannot merge")
        return file_content

    # 3. Extract text between this anchor and the NEXT anchor (or end) in LLM output
    llm_anchor_idx = llm_output.index(anchor_marker)
    llm_rest = llm_output[llm_anchor_idx + len(anchor_marker):]

    # Find the next anchor marker in the LLM output — we splice everything
    # between this anchor and the next one (or end of file)
    _next_anchor_match = re.search(
        r'--\s*\[TASK_\d+_INSERT_HOOK\]', llm_rest
    )
    if _next_anchor_match:
        # Extract text between this anchor and the next
        _splice_text = llm_rest[:_next_anchor_match.start()].rstrip()
    else:
        # No next anchor — take everything after this anchor to end of file
        _splice_text = llm_rest.rstrip()

    # 4. Build the replacement: the LLM's implementation + re-inserted anchor
    _replacement = _splice_text + "\n" + anchor_marker.strip()

    # 5. Apply the replacement to the skeleton
    _merged = file_content.replace(anchor_marker, _replacement, 1)
    if _merged != file_content:
        print(f"  [Anchor Splice] ✅ Merged LLM code into {target_path} at anchor '{anchor_marker[:60]}' "
              f"(inserted {len(_splice_text)} chars)")
    else:
        print(f"  [Anchor Splice] ⚠ Merge produced no change for {target_path}")
    return _merged


# -- Task Execution ----------------------------------------------------------

# Declarative order of the SHARED (byte-identical) context blocks.  These must
# stay in this order and must NEVER contain task-specific content, or Ollama's
# KV-cache prefix is broken and every task re-prefills the whole prompt.
_SHARED_BLOCK_LABELS = (
    "feature_request",
    "director_breakdown",
    "bridge_cheatsheet",
    "referenced_files",
)

# Upper bound for the shared Director block (identical across tasks).
_DIRECTOR_SHARED_CAP = 8000  # chars


def _cap_shared_block(text: str, cap: int) -> str:
    """Truncate a shared block to *cap* chars, identically across tasks.

    Capping is applied to the SAME bytes every task so the KV-cache prefix
    stays reusable while remaining bounded for VRAM.
    """
    if not text:
        return ""
    if len(text) <= cap:
        return text
    return text[:cap] + "\n[... shared block truncated identically across tasks ...]"


def execute_task(task, user_prompt: str, director_output: str,

                 all_results: dict, file_context: str, gdd_context: str,
                 sibling_context: str = "",
                 ollama_params: Optional[dict] = None,
                 shared_file_context: str = "") -> str:
    """Execute a single task by calling the appropriate agent.

    Args:
        sibling_context: Aggregated outputs of previously completed sibling
            tasks (same parent or top-level), so agents see peer work.
        ollama_params: Optional dict of Ollama options (e.g., {"temperature": 0.5}).
    """
    # Local imports to avoid circular dependencies
    from domain_registry import resolve_agent_name, ALL_DOMAINS, get_agent_system
    from file_references import get_referenced_files_cache
    from signals import extract_signals, extract_double_check
    from ledger import ensure_ledger_header
    from models import Task

    agent_key = resolve_agent_name(task.agent)
    domain = ALL_DOMAINS.get(agent_key)

    # Fallback: look up domain from the class-based cartridge registry on _CTX
    if not domain:
        try:
            from pipeline import _CTX
            ctx_dr = getattr(_CTX, 'domain_registry', None) if _CTX else None
            if ctx_dr and agent_key in ctx_dr:
                domain = ctx_dr[agent_key]
        except ImportError:
            pass

    if not domain:
        return f"[ERROR] Unknown agent: {task.agent}"

    preferred_model = domain.get("model", "qwen2.5-coder:7b")
    # Anchor-patch tasks get a lean system prompt (domain rules + sandbox
    # only): the mesh/ledger/virtual-memory protocols are LoRA-dependent and
    # unused by untrained models, and the staging block carries the
    # SEARCH/REPLACE format instructions instead.  Lean is constant per domain,
    # so the KV-cache prefix stays byte-identical across that domain's tasks.
    _anchor_mode = bool(getattr(task, 'anchor_marker', None))
    system = get_agent_system(agent_key, lean=_anchor_mode)

    # Build context.  Ordering matters for performance: the SHARED blocks
    # (feature request, director breakdown, bridge cheatsheet, referenced files)
    # form a byte-identical prefix across every task of the same domain so
    # Ollama's resident KV cache is reused instead of re-prefilling ~26k
    # tokens per task.  The TASK-SPECIFIC blocks go LAST in _task_parts.
    _shared_parts = [
        f"## Original Feature Request\n{user_prompt}",
    ]

    # FULL Director breakdown  byte-identical for every task, so it stays in
    # the shared prefix (cached once).  Capped identically to bound VRAM.
    if director_output:
        _director_block = _cap_shared_block(director_output, _DIRECTOR_SHARED_CAP)
        _shared_parts.append(f"## Director's Task Breakdown\n{_director_block}")

    _task_parts: list = []

    if file_context:
        _task_parts.append(file_context)

    if gdd_context:
        # -- Task-specific GDD re-extraction --------------------------------
        # The global gdd_context blob was built from the high-level user
        # prompt and may not contain the specific rules/values this task
        # needs (e.g. skeeball scoring bands vs. sound cue tables).
        # Re-query context_extractor with the task spec so the agent gets a
        # task-focused slice rather than the head of an unrelated GDD block.
        # Forward scope_mode and attraction_name so the deterministic fast path
        # fires for NEW_ATTRACTION / MODIFY_ATTRACTION tasks.
        _task_spec_query = getattr(task, "spec", "") or user_prompt
        if _task_spec_query and len(_task_spec_query) > 20:
            try:
                from context_extractor import extract_project_context as _epc
                import os as _os_ec, re as _re_ec
                _tf_ec = getattr(task, "target_file", None) or ""
                _attr_ec = _os_ec.path.splitext(_os_ec.path.basename(_tf_ec))[0] if _tf_ec else ""
                _attr_ec = _re_ec.sub(r'[^\w]+', ' ', _attr_ec).strip()
                # Derive scope_mode from the live pipeline context so that
                # MODIFY_ATTRACTION tasks are not mis-classified as NEW_ATTRACTION.
                # Fallback: NEW_ATTRACTION when a target file exists, GENERAL otherwise.
                try:
                    from pipeline import _CTX as _exec_ctx_ec
                    _scope_ec = getattr(_exec_ctx_ec, '_scope_mode', None) or (
                        "NEW_ATTRACTION" if _attr_ec else "GENERAL"
                    )
                except Exception:
                    _scope_ec = "NEW_ATTRACTION" if _attr_ec else "GENERAL"
                _task_gdd = _epc(_task_spec_query,
                                 scope_mode=_scope_ec,
                                 attraction_name=_attr_ec)
                if _task_gdd and len(_task_gdd.strip()) > 100:
                    print(f"  [GDD Re-extract] Task '{getattr(task, 'task_id', '?')}': "
                          f"{len(gdd_context)} -> {len(_task_gdd)} chars "
                          f"(task-keyed extract, scope={_scope_ec}, attraction='{_attr_ec}')")
                    gdd_context = _task_gdd
            except Exception:
                pass  # fall through to existing global blob

        # Distill with phi3.5 when still oversized; otherwise block-collapse.
        # Derive the attraction name from task.target_file so the distiller
        # can explicitly discard unrelated attraction modules.
        _GDD_CAP = 3000
        if len(gdd_context) > _GDD_DISTILL_THRESHOLD:
            import os as _os, re as _re
            _tf = getattr(task, "target_file", None) or ""
            _attr_name = _os.path.splitext(_os.path.basename(_tf))[0] if _tf else ""
            # Normalise slug: "skeeball" not "skeeball_lua"
            _attr_name = _re.sub(r'[^\w]+', ' ', _attr_name).strip()
            gdd_context = _distill_gdd_for_task(_task_spec_query, gdd_context,
                                                attraction_name=_attr_name)
        if len(gdd_context) > _GDD_CAP:
            from token_budget import TokenBudget as _TB
            gdd_context = _TB._block_aware_collapse(gdd_context, _GDD_CAP)
        _task_parts.append(gdd_context)

    # -- Internal API Ledger: inject live confirmed symbol list -------------
    # Prevents downstream agents from hallucinating function names that were
    # never registered, and makes new registrations from prior tasks visible.
    try:
        from ledger import read_internal_api_ledger
        _live_api = read_internal_api_ledger(max_chars=3000)
        if _live_api:
            _task_parts.append(_live_api)
    except Exception:
        pass

    # -- Compact Bridge Contract Cheatsheet --------------------------------
    # Injected into EVERY scripter call so the approved API names survive
    # even when context collapses under VRAM pressure.
    # Consolidated: delegate to the shared builder in _finalize_review so the
    # three former inline copies are replaced by a single maintained renderer.
    # Falls back gracefully if _CTX is not yet populated or cartridge is absent.
    try:
        from pipeline import _CTX as _exec_ctx
        if _exec_ctx is not None:
            # Memoize the bridge snippet per run + domain so the cheatsheet is
            # byte-identical across tasks (KV-prefix reuse guarantee).
            _shared_cache = getattr(_exec_ctx, '_shared_block_cache', None)
            if _shared_cache is None:
                _shared_cache = {}
                _exec_ctx._shared_block_cache = _shared_cache
            _cheat_key = f"bridge_snippet:{agent_key}"
            if _cheat_key not in _shared_cache:
                from _finalize_review import build_fix_bridge_snippet as _bfbs_exec
                _shared_cache[_cheat_key] = _bfbs_exec(_exec_ctx)
            _base = _shared_cache[_cheat_key]
            if _base:
                _subst_guide = (
                    "Substitution quick-ref (common wrong -> correct):\n"
                    "  SpawnDynamicBall -> SpawnDynamicSphere(lx,ly,lz,radius[,mass])\n"
                    "  RemoveBody/ReleaseHandle/DestroyEntity -> DestroyBody(handle)\n"
                    "  CheckCollision -> IsSensorTriggered(handle) -> bool\n"
                    "  GetLinearVelocity -> GetVelocity(handle) -> vx,vy,vz\n"
                    "  SetPosition/Teleport -> no approved equivalent; use MoveKinematic(h,lx,ly,lz,dt)\n"
                    "  ApplyForce/AddForce -> ApplyImpulse(handle,ix,iy,iz)\n"
                    "  table.clear(t) -> for k in pairs(t) do t[k]=nil end  (Lua 5.1 compat)\n"
                    "Lifecycle: OnLoadStatic() / OnLoad() / OnUnload() - bare globals, no return.\n"
                    "ANTI-PATTERNS (ALWAYS WRONG):\n"
                    "  - DO NOT put SpawnDynamic* calls at module root level (crashes engine)\n"
                    "  - DO NOT define function OnStep(dt) at module level; use MidwayPhysics.OnStep(function(dt)...end)\n"
                    "  - DO NOT create duplicate OnLoadStatic() / OnLoad() functions\n"
                    "  - DO NOT cache AttractionConstants.modifiers at module level; read inside OnStep\n"
                    "Step: MidwayPhysics.OnStep(function(dt) ... end) - call inside OnLoad.\n"
                )
                _cheatsheet = (
                    "## ⚡ Bridge API Cheatsheet (exhaustive - use ONLY these names)\n"
                    + _base
                    + _subst_guide
                )
                _shared_parts.append(_cheatsheet)
    except Exception:
        pass

    # Auto-Inject Referenced Files
    refs_block = get_referenced_files_cache()
    if refs_block:
        _shared_parts.append(refs_block)

    # Stable reference-file context (memoized per domain upstream).  It is
    # byte-identical across tasks, so it belongs in the shared KV-cache prefix,
    # not the per-task tail.  The per-task delta files arrive via file_context.
    if shared_file_context:
        _shared_parts.append(shared_file_context)

    # -- Directive A: Stateless Parent Context ------------------------------
    # Parent context is stripped to code artifacts only to prevent linear
    # conversational bloat from bleeding across tasks.
    if task.parent and task.parent in all_results:
        from _helpers_text import strip_to_code_artifacts
        parent_output = all_results[task.parent]
        parent_clean = strip_to_code_artifacts(parent_output, fallback_truncation=800)
        _task_parts.append(f"## Parent Task Context (code artifacts only)\n{parent_clean}")

    # -- Directive A: Stateless Sibling Context -----------------------------
    # Already stripped by run_tasks() before being passed as sibling_context.
    # Accept as-is - it has already been code-artifact-sanitized upstream.
    if sibling_context:
        _task_parts.append(sibling_context)

    # -- Step 3: Stateful Patch Execution (Staging File Baseline) -----------
    # Inject the current on-disk state of the task's target_file so that
    # the agent sees the real file built by previous tasks in the chain.
    # The system prompt instructs Task 2+ agents to output ONLY SEARCH/REPLACE
    # blocks targeting the TODO placeholders left by Task 1's scaffold.
    # LIVE_FILE_CAP is now computed from model context rather than hardcoded.
    # This ensures the coder (qwen3.5:9b @ 16384 tok ctx) gets a proportional
    # file cap, while smaller aux models get proportionally less.
    from ollama_client import resolve_ctx_size as _resolve_live_ctx
    _live_model_ctx = _resolve_live_ctx(preferred_model)
    # LIVE_FILE_CAP uses density-aware 2 chars/tok and caps at 40% of the
    # total message budget (not 80% of model ctx).  At the old 3 chars/tok
    # * 0.80, a 32K model got a 78K-char ceiling — enough for the *entire*
    # context budget — leaving zero room for system prompt, cheatsheet,
    # economy mandate, sibling context, etc.  At 2 chars/tok * 0.40, a 32K
    # model gets ~26K chars for file content, which still fits a scaffold
    # comfortably while leaving room for all other overheads.
    LIVE_FILE_CAP = int(_live_model_ctx * 2 * 0.40)
    if task.target_file:
        _staging_path = None
        try:
            from pipeline import _CTX as _stage_ctx
            if _stage_ctx and hasattr(_stage_ctx, 'project_root'):
                _staging_path = _stage_ctx.project_root / task.target_file
        except Exception:
            _staging_path = None

        if _staging_path and _staging_path.is_file():
            try:
                _live_content = _staging_path.read_text(encoding="utf-8", errors="replace")
                if _live_content.strip():
                    if len(_live_content) > LIVE_FILE_CAP:
                        print(f"  [Staging File] {task.target_file} is {len(_live_content)} chars, "
                              f"truncating to {LIVE_FILE_CAP}")
                        _live_content = _live_content[:LIVE_FILE_CAP] + (
                            f"\n--- [truncated at {LIVE_FILE_CAP} chars] ---"
                        )

                    # -- Fix D: Anchor-aware staging block -----------------------
                    # When the task has an anchor_marker, the scaffold file contains
                    # a unique deterministic line like "-- [TASK_4_INSERT_HOOK]".
                    # The agent must SEARCH for that EXACT line and REPLACE it with
                    # its implementation PLUS a re-inserted version of the anchor
                    # (so subsequent tasks can find theirs).
                    #
                    # CRITICAL: To prevent context saturation, the anchor path
                    # does NOT dump the entire growing file into context.
                    # Instead it shows ONLY the anchor line with ~5 lines of
                    # surrounding context.  The model doesn't need to re-read
                    # 5K+ chars of already-written boilerplate every wave.
                    # It only needs to find and replace its single anchor line.
                    # This was the root cause of the "code duplication across
                    # waves" failure mode (Task 4 and Task 5 both re-emitted
                    # OnLoadStatic/OnLoad/OnUnload because the full file context
                    # encouraged the model to "rewrite the whole thing").
                    _anchor_marker = getattr(task, 'anchor_marker', None)
                    if _anchor_marker:
                        # Extract only the anchor line with ~5 lines of context
                        _anchor_context_lines: list[str] = []
                        _file_lines = _live_content.splitlines()
                        _anchor_found = False
                        for _li, _line in enumerate(_file_lines):
                            if _anchor_marker in _line:
                                _anchor_found = True
                                # 3 lines above
                                _start = max(0, _li - 3)
                                # 2 lines below
                                _end = min(len(_file_lines), _li + 3)
                                for _si in range(_start, _end):
                                    _prefix = f"{_si+1:4d} | " if _si != _li else f"{_si+1:4d} > "
                                    _anchor_context_lines.append(_prefix + _file_lines[_si])
                                break
                        if _anchor_found:
                            _anchor_context = "\n".join(_anchor_context_lines)
                            _context_note = (
                                f"\n(Showing only the ~7 lines around anchor marker. "
                                f"Full file is {len(_live_content)} chars / {len(_file_lines)} lines.)\n"
                            )
                        else:
                            # Anchor not found — some prior task consumed it without
                            # re-inserting.  Show a minimal snippet plus the raw anchor.
                            _first_line = _file_lines[0] if _file_lines else ""
                            _last_line = _file_lines[-1] if _file_lines else ""
                            _anchor_context = (
                                f"File starts: {_first_line}\n"
                                f"[... {len(_file_lines)} lines ...]\n"
                                f"File ends:   {_last_line}\n"
                            )
                            _context_note = (
                                f"\n⚠️ ANCHOR '{_anchor_marker}' NOT FOUND in file. "
                                f"The orchestrator will re-inject it before patch application. "
                                f"Full file is {len(_live_content)} chars / {len(_file_lines)} lines.\n"
                            )
                        # -- Shared handle contract: pin the exact handle names from
                        # the Architect design so tasks don't each invent their own
                        # (malletHandles vs mallet_kinematic vs bellHandle).
                        _design_handle_contract = ""
                        try:
                            _design_obj = getattr(_stage_ctx, 'attraction_design', None)
                            _design_handles = getattr(_design_obj, 'handles', None) if _design_obj else None
                            _handle_names = [getattr(_h, 'name', '') for _h in (_design_handles or [])]
                            _handle_names = [n.strip() for n in _handle_names if n and n.strip()]
                        except Exception:
                            _handle_names = []
                        if _handle_names:
                            _design_handle_contract = (
                                "\n## SHARED HANDLE CONTRACT (MANDATORY)\n"
                                "These are the ONLY handle variable names for this attraction.\n"
                                "They are declared at module scope by Task 1; reference these exact\n"
                                "names. Do NOT invent new handle names (no malletHandles, bellHandle, etc.).\n"
                                + "".join(f"- `{n}`\n" for n in _handle_names)
                            )
                        _stage_block = (
                            f"\n\n## ⚡ CURRENT ON-DISK STATE: {task.target_file}\n"
                            f"Relevant region around your anchor marker:\n"
                            f"```\n{_anchor_context}\n```{_context_note}"
                            f"## ANCHOR PATCH MODE (MANDATORY)\n"
                            f"Your marker line: `{_anchor_marker}`\n"
                            f"(The `NN |` / `NN >` prefixes above are line-number hints ONLY - never copy them into your SEARCH block.)\n"
                            f"The orchestrator wraps your code in the correct lifecycle function; "
                            f"you only fill this anchor.\n"
                            f"- Do NOT redefine `function OnLoadStatic/OnLoad/OnStep/OnUnload`.\n"
                            f"- Do NOT copy other tasks' code, other anchors, or surrounding file content.\n"
                            f"- SEARCH is exactly the one anchor line; REPLACE is your "
                            f"implementation (a few lines) + the anchor re-inserted at the end.\n"
                            f"{_design_handle_contract}"
                            f"Output EXACTLY ONE block:\n"
                            f"<<<<<<< SEARCH\n"
                            f"    {_anchor_marker}\n"
                            f"=======\n"
                            f"    <your implementation for this task>\n"
                            f"    {_anchor_marker}\n"
                            f">>>>>>> REPLACE\n"
                        )

                    else:
                        _stage_block = (
                            f"\n\n## ⚡ CURRENT ON-DISK STATE: {task.target_file}\n"
                            f"This is the file as it exists right now, built by previous tasks. "
                            f"You MUST read this baseline before generating any changes.\n"
                            f"```\n{_live_content}\n```\n"
                            f"## OUTPUT FORMAT MANDATE\n"
                            f"You are in ITERATIVE PATCH MODE. Do NOT output the entire file. "
                            f"Output ONLY SEARCH/REPLACE blocks:\n"
                            f"<<<<<<< SEARCH\n"
                            f"[exact lines to replace from the current on-disk state above]\n"
                            f"=======\n"
                            f"[new replacement content]\n"
                            f">>>>>>> REPLACE\n"
                            f"IMPORTANT: Your SEARCH block MUST include 3+ lines of surrounding context "
                            f"ABOVE and BELOW the lines you actually want to change. "
                            f"Do NOT search for just 2-3 specific lines in isolation - "
                            f"include the enclosing function signature, comment headers, and trailing "
                            f"`end` keyword. The extra context ensures the patcher can still find your "
                            f"target even if a previous task added or removed a few lines nearby, "
                            f"or if indentation shifted. "
                            f"If you are adding new code inside an existing hook, SEARCH for the full "
                            f"hook definition (from `function` to `end`) and REPLACE it with "
                            f"the expanded version containing your additions."
                        )
                    _task_parts.append(_stage_block)
                    print(f"  [Staging File] Injected {len(_stage_block)}-char staging block for "
                          f"{task.target_file} (full file {len(_live_content)} chars) into '{task.agent}' prompt")
            except Exception as e:
                print(f"  [Staging File] Error reading {task.target_file}: {e}")

    # -- Directive A: Stateless Iteration Output ----------------------------

    # Previous iteration output is stripped to code artifacts to prevent the
    # agent's own conversational prose from compounding the context window.
    if task.iteration > 0 and task.output:
        from _helpers_text import strip_to_code_artifacts
        iter_clean = strip_to_code_artifacts(task.output, fallback_truncation=600)
        _task_parts.append(f"## Your Previous Output (iteration {task.iteration})\n{iter_clean}")
        if task.iteration >= MAX_ITERATIONS - 1:
            from _prompts import SELF_CORRECT_SYSTEM
            system = SELF_CORRECT_SYSTEM

    # -- Fix D: Model-aware task.context cap ---------------------------
    # task.context grows from query results, pro-test injection, and
    # iteration output. Without a cap it can bloat to 50K+ chars across
    # 3 iterations and cause VRAM OOM at <1 tok/s.
    # Cap is now model-aware: 30% of the total message budget so that
    # 7B/8B models (32768 ctx -> ~36K-char budget) get ~11K chars of
    # context while small aux models stay conservatively bounded.
    # Uses block-aware collapse so structural blocks are preserved.
    # IMPORTANT: Code is denser than prose. Use 2 chars/token (realistic code
    # heuristic) instead of 3 chars/token. At 3 chars/tok, a 32K model gets a
    # 54K-char ceiling which translates to 36K tokens at 1.5 chars/tok (dense
    # code) — exceeding the context window by 12%. The 0.55 factor reserves
    # 55% for user content, ~15% for system prompt, and ~30% for output tokens
    # and KV cache overhead.
    from ollama_client import resolve_ctx_size
    _MODEL_CTX = resolve_ctx_size(preferred_model)
    _TOTAL_MSG_CHAR_LIMIT: int = int(_MODEL_CTX * 2 * 0.55)
    _CONTEXT_CHAR_LIMIT: int = max(4000, int(_TOTAL_MSG_CHAR_LIMIT * 0.30))
    if task.context:
        if len(task.context) > _CONTEXT_CHAR_LIMIT:
            print(f"  [Context Truncation] task.context was {len(task.context)} chars, "
                  f"collapsing to {_CONTEXT_CHAR_LIMIT} (model ctx={_MODEL_CTX} tok, 30% share)")
            task.context = TokenBudget._block_aware_collapse(
                task.context, _CONTEXT_CHAR_LIMIT
            )
        _task_parts.append(task.context)

    # The task spec
    _task_parts.append(f"## Task Specification\n{task.spec}")

    context_parts = _shared_parts + _task_parts
    user_message = "\n\n".join(context_parts)

    # -- KV-cache guardrail ------------------------------------------------
    # The shared prefix must be byte-identical across tasks of the same domain
    # or Ollama's prefix cache stops reusing and every task re-prefills the
    # full prompt.  Hash (system + shared blocks) and warn on any drift, and
    # log the shared/tail split so cache reuse can be eyeballed per wave.
    try:
        import hashlib as _hashlib_guard
        from pipeline import _CTX as _guard_ctx
        if _guard_ctx is not None:
            _shared_prefix_text = "\n\n".join(_shared_parts)
            _prefix_payload = f"{system}\n\n{_shared_prefix_text}"
            _prefix_hash = _hashlib_guard.sha256(
                _prefix_payload.encode("utf-8", errors="replace")
            ).hexdigest()[:12]
            _prev_hash = getattr(_guard_ctx, '_last_shared_prefix_hash', None)
            _prev_key = getattr(_guard_ctx, '_last_shared_prefix_key', None)
            if _prev_hash is not None and _prev_key == agent_key and _prev_hash != _prefix_hash:
                print(f"  [KV Cache] \u26a0 Shared prefix drift for '{agent_key}' "
                      f"(prev={_prev_hash} now={_prefix_hash}) \u2014 cache reuse reduced.")
            _guard_ctx._last_shared_prefix_hash = _prefix_hash
            _guard_ctx._last_shared_prefix_key = agent_key
            _will_truncate = len(user_message) > _TOTAL_MSG_CHAR_LIMIT
            print(f"  [KV Cache] shared prefix \u2248 {len(_shared_prefix_text)} chars; "
                  f"tail \u2248 {len(user_message) - len(_shared_prefix_text) - 2} chars; "
                  f"{'WILL truncate (breaks prefix)' if _will_truncate else 'no truncation'}")
    except Exception:
        pass

    # -- Fix D: Model-Aware Total user_message char ceiling ----------
    # _MODEL_CTX and _TOTAL_MSG_CHAR_LIMIT are already resolved above.
    # 55% of context budget at 3 chars/token (code heuristic).
    # 55% (not 65%) reserves 45% for system prompt, output tokens,
    # and KV cache overhead.
    if len(user_message) > _TOTAL_MSG_CHAR_LIMIT:
        print(f"  [Context Truncation] user_message was {len(user_message)} chars, "
              f"truncating to {_TOTAL_MSG_CHAR_LIMIT} chars "
              f"(model ctx={_MODEL_CTX} tok @ 55%, 3 chars/tok)")

        # -- Preserve overflow in OffloadStore before truncating -----
        # The truncated portion is saved to the OffloadStore under a
        # task-specific block_id so the LLM can <PAGE_IN> it if needed.
        _overflow_text = user_message[_TOTAL_MSG_CHAR_LIMIT:]
        if _overflow_text.strip():
            try:
                from offload_store import get_offload_store
                _store = get_offload_store()
                _overflow_id = f"context_overflow_{task.task_id}"
                _store.store_block(
                    block_id=_overflow_id,
                    header=f"Overflow context for {task.task_id} "
                           f"({len(_overflow_text)} chars truncated)",
                    body_lines=[_overflow_text],
                )
                _overflow_note = (
                    f"\n---\n[📄 Context Overflow Preserved] "
                    f"An additional {len(_overflow_text)} chars of context were truncated "
                    f"to fit within the model's {_MODEL_CTX}-token context window. "
                    f"Use <PAGE_IN> to load the offloaded block:\n"
                    f"`<invoke_kernel><action>PAGE_IN</action>"
                    f"<target>{_overflow_id}</target></invoke_kernel>`\n"
                    f"---\n"
                )
            except Exception:
                _overflow_note = ""
        else:
            _overflow_note = ""

        # Keep the task spec (last appended part) by collapsing earlier context
        # using _block_aware_collapse so structural blocks (function signatures,
        # section headers) are preserved with VRAM_STUB pointers to the OffloadStore.
        _spec_marker = f"## Task Specification\n{task.spec}"
        _spec_idx = user_message.find(_spec_marker)
        if _spec_idx > 0:
            _before_spec = user_message[:_spec_idx]
            _allowed = max(0, _TOTAL_MSG_CHAR_LIMIT - len(_spec_marker) - 50)
            # Use block-aware collapse instead of flat head/tail truncation
            # to preserve structural blocks and generate VRAM_STUB pointers
            # for offloaded content (future paging LoRA training target).
            _before_spec = TokenBudget._block_aware_collapse(
                _before_spec,
                _allowed,
                core_memory_table={"task_spec": task.spec}  # never evict task spec
            ) + (
                f"\n[... context truncated at {_allowed} chars; "
                f"overflow preserved in OffloadStore as '{_overflow_id}' ...]\n"
            )
            user_message = _before_spec + "\n\n" + _spec_marker + _overflow_note
        else:
            user_message = user_message[:_TOTAL_MSG_CHAR_LIMIT] + (
                f"\n[... total context ceiling reached at {_TOTAL_MSG_CHAR_LIMIT} chars; "
                f"overflow preserved in OffloadStore ...]"
            ) + _overflow_note

    label = f"{domain['name']} (Task {task.task_id})"
    if task.is_query:
        label = f"[QUERY] {domain['name']} -> {task.parent}"

    # -- Directive C: Kernel Interrupt - VRAM critical check --------------
    # Before sending the prompt to the LLM, check if the combined token
    # payload exceeds 80% of the model's safe context window.
    # If it does, inject a [SYSTEM KERNEL: VRAM critical] warning into the
    # user message, instructing the agent to <PAGE_OUT> before generating.
    # (TokenBudget is imported at module level — no need to re-import here)
    vram_warning = TokenBudget.check_vram_critical(system, user_message, preferred_model)
    if vram_warning:
        print(f"  [Kernel Interrupt] ⚠ Appending VRAM critical warning to '{label}'")
        user_message = user_message + "\n\n" + vram_warning

    # -- Directive A: Hard Context Firewall (Absolute Statelessness) ---------
    # NO history survives between tasks. A brand new messages array is built
    # fresh for every single task invocation: [System Prompt, User Prompt].
    # No .pop(), no pruning, no accumulation - explicit zero-state per call.
    from ollama_client import (
        call_ollama_with_messages, get_last_paged_cache,
        VramOverrunError, vram_overrun_abort, get_vram_abort_diagnostics,
        is_fatal_ollama_error,
    )
    messages: list = []
    messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user_message})
    try:
        output = call_ollama_with_messages(messages, label, preferred_model, params=ollama_params)
    except VramOverrunError:
        # VRAM overrun - hard abort. Mark task as failed and return a
        # diagnostic message that will propagate up through run_tasks.
        _vram_diag = get_vram_abort_diagnostics()
        _abort_msg = (
            f"\n\n[VRAM OVERRUN - PIPELINE ABORTED]\n"
            f"Task '{task.task_id}' ({task.agent}) was aborted due to VRAM overrun.\n"
            f"The TPS watchdog detected token speed below 2.0 tok/s.\n"
            f"Diagnostics:\n{_vram_diag}\n"
            f"[END OF VRAM ABORT MESSAGE]\n"
        )
        print(_abort_msg, flush=True)
        task.output = _abort_msg
        task.signals = []
        task.double_check = None
        task.completed = True
        return _abort_msg

    # If Ollama returned a fatal error sentinel (timeout, socket drop, OOM)
    # treat the task as failed immediately - do NOT pass the sentinel string
    # downstream as code, and do NOT loop back into another model call.
    if is_fatal_ollama_error(output):
        _fatal_msg = (
            f"[TASK FAILED - OLLAMA ERROR]\n"
            f"Task '{task.task_id}' ({task.agent}) received a fatal error from Ollama:\n"
            f"{output.strip()}\n"
            f"[END OF OLLAMA ERROR]\n"
        )
        print(f"  ⛔ {_fatal_msg.splitlines()[0]}", flush=True)
        task.output = _fatal_msg
        task.signals = []
        task.double_check = None
        task.completed = True
        return _fatal_msg

    # -- Directive A: Capture paged_in_cache for Pro-Mode Inheritance --
    task.paged_files_cache = get_last_paged_cache()
    if task.paged_files_cache:
        print(f"  [Paging Kernel] 📋 Task '{task.task_id}' paged_files_cache: "
              f"{len(task.paged_files_cache)} files, "
              f"{sum(len(v) for v in task.paged_files_cache.values())} total chars")

    # -- Fix D: Pre-flight Anchor Verification --------------------------------
    # Before applying SEARCH/REPLACE patches, verify that ALL expected anchor
    # markers are still present in the target file.  If a previous task consumed
    # an anchor marker without re-inserting it (which would cause the next task's
    # SEARCH block to fail), inject the missing anchor at the correct lifecycle
    # location.  This self-healing mechanism prevents the "SEARCH block not found"
    # drift that causes the fix-loop death spiral.
    _preflight_anchor_healed = False
    if task.target_file and getattr(task, 'anchor_marker', None):
        try:
            from _anchors import verify_and_reinject_anchor, CANONICAL_ANCHORS
            from pipeline import _CTX as _pf_ctx
            if _pf_ctx and hasattr(_pf_ctx, 'project_root'):
                _pf_path = _pf_ctx.project_root / task.target_file
                if _pf_path.is_file():
                    _pf_content = _pf_path.read_text(encoding="utf-8")
                    # Check if THIS task's anchor still exists
                    if task.anchor_marker not in _pf_content:
                        _pf_content = verify_and_reinject_anchor(
                            _pf_content, task.anchor_marker, CANONICAL_ANCHORS
                        )
                        if task.anchor_marker in _pf_content:
                            _pf_path.write_text(_pf_content, encoding="utf-8")
                            _preflight_anchor_healed = True
                            print(f"  [Anchor Verifier] 🔧 Re-injected missing anchor "
                                  f"'{task.anchor_marker[:60]}' into {task.target_file}")
        except Exception as _pf_e:
            print(f"  [Anchor Verifier] ⚠ Error during pre-flight check: {_pf_e}")

    # -- Step 4: Real-time SEARCH/REPLACE Application (Merge Abolition) -
    # Instead of accumulating outputs for a Phase 5 merge, apply the
    # SEARCH/REPLACE block(s) from this task's output to the target file
    # immediately.  If the output has no SEARCH/REPLACE blocks and this
    # is a new file (first task in chain), write the full output as the
    # initial scaffold.
    if task.target_file:
        _apply_target = None
        try:
            from pipeline import _CTX as _apply_ctx
            if _apply_ctx and hasattr(_apply_ctx, 'project_root'):
                _apply_target = _apply_ctx.project_root / task.target_file
        except Exception:
            _apply_target = None

        if _apply_target:
            _unwrapped_output = _unwrap_json_code(output)
            if _unwrapped_output != output:
                print(f"  [JSON Unwrap] {task.task_id}: output was JSON-wrapped; extracted code for patching")
                output = _unwrapped_output
            _blocks = _extract_search_replace_blocks(output)

            # -- Anchor Guard: validate SEARCH block targets the task's assigned anchor ---
            # Without this gate, the 8B model frequently hallucinates an anchor marker
            # belonging to a DIFFERENT task. It then patches the wrong hook, corrupting
            # the file and cascading into duplicate functions and orphaned hooks.
            # The guard checks every SEARCH block for the task's anchor_marker BEFORE
            # the patch is written. If any block is missing the anchor, a targeted
            # re-prompt tells the model to fix its SEARCH block and try again.
            _anchor_marker_guard = getattr(task, 'anchor_marker', None)
            if _anchor_marker_guard and _blocks:
                # Token-only comparison: only ``-- [TASK_N_INSERT_HOOK]`` is
                # authoritative.  The trailing title is a hint that may differ
                # between the canonical anchor list and the skeleton template,
                # so full-line matching previously caused false rejections.
                import re as _re_guard
                _token_m = _re_guard.search(r"--\s*\[TASK_\d+_INSERT_HOOK\]", _anchor_marker_guard)
                _guard_token = _token_m.group(0) if _token_m else _anchor_marker_guard.strip()
                _blocks_have_correct_anchor = True
                for _idx_g, _block_g in enumerate(_blocks):
                    _search_g = _block_g.get("search", "")
                    if _guard_token not in _search_g:
                        _blocks_have_correct_anchor = False
                        print(f"  [Anchor Guard] ⛔ {task.task_id} SEARCH block {_idx_g+1} "
                              f"targets wrong anchor. Expected '{_guard_token}' "
                              f"but found '{_search_g[:80]}'. Re-prompting...")
                        break
                if not _blocks_have_correct_anchor:
                    _fix_prompt = (
                        f"## Your Task\n{getattr(task, 'spec', user_prompt)}\n\n"
                        f"## Correction Required\n"
                        f"Your previous output targeted the WRONG anchor marker in the file.\n"
                        f"The EXACT line you must SEARCH for (and then REPLACE) is:\n\n"
                        f"  `{_anchor_marker_guard}`\n\n"
                        f"That is the anchor assigned to your task. REPLACE it with your "
                        f"REAL implementation code, and re-emit the SAME marker line at the "
                        f"end of the replacement so the next task can still find it.\n\n"
                        f"Do NOT search for any other anchor.  Do NOT emit placeholder text "
                        f"or empty stubs — write the actual code for your task.\n"
                        f"Output ONLY a valid SEARCH/REPLACE block:\n"
                        f"<<<<<<< SEARCH\n"
                        f"    {_anchor_marker_guard}\n"
                        f"=======\n"
                        f"    <your real implementation code>\n"
                        f"    {_anchor_marker_guard}\n"
                        f">>>>>>> REPLACE"
                    )
                    try:
                        _fix_msgs = [
                            {"role": "system", "content": (
                                "You are a precise SEARCH/REPLACE fixer. "
                                "Given a task and the correct anchor marker, output ONLY "
                                "a valid SEARCH/REPLACE block targeting that exact anchor."
                            )},
                            {"role": "user", "content": _fix_prompt},
                        ]
                        from ollama_client import resolve_ctx_size as _guard_ctx
                        _guard_model = getattr(task, 'agent_model', preferred_model)
                        _guard_output = call_ollama_with_messages(
                            _fix_msgs, f"Anchor Guard Fix ({task.task_id})", _guard_model
                        )
                        if _guard_output and len(_guard_output.strip()) > 50:
                            print(f"  [Anchor Guard] ✅ Re-prompt produced corrected patch for {task.task_id}")
                            output = _guard_output
                            # Re-extract blocks from the corrected output
                            _blocks = _extract_search_replace_blocks(output)
                        else:
                            print(f"  [Anchor Guard] ⚠ Re-prompt returned empty output for {task.task_id} "
                                  f"- proceeding with original (will likely fail)")
                    except Exception as _guard_e:
                        print(f"  [Anchor Guard] ⚠ Re-prompt failed: {_guard_e}")

            if _blocks:
                # Apply each SEARCH/REPLACE block in sequence to the file
                try:
                    if _apply_target.is_file():
                        _file_content = _apply_target.read_text(encoding="utf-8", errors="replace")
                    else:
                        _file_content = ""
                    _patches_applied = 0
                    for _block in _blocks:
                        _search_text = _block.get("search", "")
                        _replace_text = _block.get("replace", "")
                        # Use fuzzy matching (exact -> normalized -> sliding window)
                        _new_content = _fuzzy_apply_patch(_file_content, _search_text, _replace_text)
                        if _new_content != _file_content:
                            _file_content = _new_content
                            _patches_applied += 1
                        else:
                            # Soft failure - log it but don't crash the pipeline
                            _search_preview = _search_text[:80].replace("\n", "\\n")
                            print(f"  [Real-time Patch] ⚠ SEARCH block not found in "
                                  f"{task.target_file}: '{_search_preview}...'  "
                                  f"(block {_patches_applied + 1} of {len(_blocks)} skipped)")
                              
                    if _patches_applied > 0:
                        _apply_target.parent.mkdir(parents=True, exist_ok=True)
                        # Use staging-aware write when possible
                        try:
                            from _helpers_io import atomic_write_text as _staging_write
                            _staging_write(_apply_target, _file_content)
                        except Exception:
                            _apply_target.write_text(_file_content, encoding="utf-8")
                        print(f"  [Real-time Patch] ✅ Applied {_patches_applied}/{len(_blocks)} "
                              f"SEARCH/REPLACE block(s) to {task.target_file}")

                        # -- Fix D: Post-patch Anchor Integrity Check ---------
                        # After applying all SEARCH/REPLACE blocks, verify that
                        # every canonical anchor marker still exists in the file.
                        # If the consuming task failed to re-insert its anchor,
                        # inject it back so the next task in the chain can still
                        # find its target.  This prevents the cascading failure
                        # where one SEARCH/REPLACE round silently deletes the
                        # anchor for the next task, which then fails with
                        # "SEARCH block not found", triggering the fix-loop.
                        if task.target_file.endswith('.lua'):
                            try:
                                from _anchors import (
                                    CANONICAL_ANCHORS,
                                    verify_and_reinject_anchor,
                                    get_all_anchor_tasks,
                                )
                                _all_markers = {a[4] for a in get_all_anchor_tasks()}
                                _found = set()
                                _missing = set()
                                for _marker in _all_markers:
                                    if _marker in _file_content:
                                        _found.add(_marker)
                                _missing = _all_markers - _found
                                if _missing:
                                    _healed = False
                                    for _lost_marker in sorted(_missing, reverse=True):
                                        _new_content = verify_and_reinject_anchor(
                                            _file_content, _lost_marker, CANONICAL_ANCHORS
                                        )
                                        if _lost_marker in _new_content:
                                            _file_content = _new_content
                                            _healed = True
                                            print(f"  [Anchor Verifier] 🔧 Post-patch re-injected "
                                                  f"'{_lost_marker}' into {task.target_file}")
                                    if _healed:
                                        _apply_target.write_text(_file_content, encoding="utf-8")
                            except Exception as _post_e:
                                print(f"  [Anchor Verifier] ⚠ Post-patch check error: {_post_e}")
                    elif not _apply_target.is_file() and len(_blocks) == 0:
                        # No blocks at all and file doesn't exist - write full output
                        _apply_target.parent.mkdir(parents=True, exist_ok=True)
                        try:
                            from _helpers_io import atomic_write_text as _staging_write
                            _staging_write(_apply_target, output)
                        except Exception:
                            _apply_target.write_text(output, encoding="utf-8")
                        print(f"  [Real-time Patch] ✅ Wrote full file scaffold to {task.target_file} "
                              f"({len(output)} chars - no SEARCH/REPLACE blocks)")
                except Exception as e:
                    print(f"  [Real-time Patch] ⛔ Error applying patches to {task.target_file}: {e}")
            elif not _apply_target.is_file():
                # No SEARCH/REPLACE blocks and file doesn't exist - write full output
                # (Task 1 scaffold path)
                try:
                    _apply_target.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        from _helpers_io import atomic_write_text as _staging_write
                        _staging_write(_apply_target, output)
                    except Exception:
                        _apply_target.write_text(output, encoding="utf-8")
                    print(f"  [Real-time Patch] ✅ Wrote full file to {task.target_file} "
                          f"({len(output)} chars) - initial scaffold")
                except Exception as e:
                    print(f"  [Real-time Patch] ⛔ Error writing {task.target_file}: {e}")
            else:
                # -- Anchor Splice Fallback: file exists but no SEARCH/REPLACE blocks --
                # The LLM likely output a full file instead of a SEARCH/REPLACE block.
                # Try up to 3 retries asking for SEARCH/REPLACE output, then fall back
                # to deterministic anchor-based splice.
                _anchor_marker_splice = getattr(task, 'anchor_marker', None)
                _retry_count = 0
                _MAX_SPLICE_RETRIES = 3
                _current_file_content = _apply_target.read_text(encoding="utf-8", errors="replace") if _apply_target.is_file() else ""
                _original_output_for_retry = output

                while _retry_count < _MAX_SPLICE_RETRIES and _current_file_content and _anchor_marker_splice:
                    _retry_count += 1
                    print(f"  [Anchor Splice] 🔄 Retry {_retry_count}/{_MAX_SPLICE_RETRIES}: "
                          f"LLM output had no SEARCH/REPLACE blocks for {task.target_file} — "
                          f"re-prompting for SEARCH/REPLACE format...")

                    # Build a targeted re-prompt asking for SEARCH/REPLACE only
                    _splice_retry_prompt = (
                        f"## Your Task\n{getattr(task, 'spec', user_prompt)}\n\n"
                        f"## FORMAT ERROR\n"
                        f"Your previous output contained the full file content instead of "
                        f"a focused SEARCH/REPLACE block. This is NOT acceptable.\n\n"
                        f"Your assigned anchor marker is:\n\n"
                        f"  `{_anchor_marker_splice}`\n\n"
                        f"You must output ONLY a single SEARCH/REPLACE block where:\n"
                        f"- SEARCH is the EXACT anchor marker line above\n"
                        f"- REPLACE is your implementation code followed by the re-inserted anchor\n\n"
                        f"Example format:\n"
                        f"<<<<<<< SEARCH\n"
                        f"    {_anchor_marker_splice}\n"
                        f"=======\n"
                        f"    <your REAL implementation code for this task>\n"
                        f"    {_anchor_marker_splice}\n"
                        f">>>>>>> REPLACE\n\n"
                        f"Output ONLY that SEARCH/REPLACE block. No file content, no explanation."
                    )
                    try:
                        from ollama_client import resolve_ctx_size as _splice_ctx
                        # Inject the approved engine API list + lifecycle rules into the
                        # splice retry.  The bare retry prompt has zero domain context,
                        # so the 7B coder falls back to Garry's Mod / Roblox patterns
                        # (Vector(), GetPhysicsObject(), IsValid(), RegisterCallback(),
                        # DestroyDynamicBody(), getStats()) which the PhantomAPI gate then
                        # rejects.  Reuse the memoized bridge snippet for the agent.
                        _splice_api = ""
                        try:
                            from pipeline import _CTX as _splice_ctx_obj
                            if _splice_ctx_obj is not None:
                                _splice_cache = getattr(_splice_ctx_obj, '_shared_block_cache', None) or {}
                                _splice_api = _splice_cache.get(f"bridge_snippet:{agent_key}", "") or ""
                        except Exception:
                            _splice_api = ""
                        if not _splice_api:
                            try:
                                from _finalize_review import build_fix_bridge_snippet as _bfbs_splice
                                from pipeline import _CTX as _splice_ctx_obj2
                                if _splice_ctx_obj2 is not None:
                                    _splice_api = _bfbs_splice(_splice_ctx_obj2) or ""
                            except Exception:
                                _splice_api = ""
                        _splice_system = (
                            "You are a precise SEARCH/REPLACE block generator for a custom "
                            "Lua game engine. Given a task and an exact anchor marker, output "
                            "ONLY a valid SEARCH/REPLACE block targeting that anchor. Never "
                            "output full file content.\n"
                            "STRICT API RULES:\n"
                            "- Use ONLY the engine APIs listed below. Do NOT invent Garry's Mod "
                            "or Roblox APIs (no Vector(), Angle(), GetPhysicsObject(), IsValid(), "
                            "RegisterCallback(), ents, hook, SetPos, getStats()).\n"
                            "- Lifecycle hooks are bare globals OnLoadStatic()/OnLoad()/OnUnload(); "
                            "register the step callback via MidwayPhysics.OnStep(function(dt)...end).\n"
                            "- Do NOT redefine lifecycle functions inside your REPLACE block.\n\n"
                            + (_splice_api if _splice_api else "")
                        )
                        _splice_msgs = [
                            {"role": "system", "content": _splice_system},
                            {"role": "user", "content": _splice_retry_prompt},
                        ]
                        _splice_model = getattr(task, 'agent_model', preferred_model)
                        _retry_output = call_ollama_with_messages(
                            _splice_msgs, f"Anchor Splice Retry ({task.task_id})", _splice_model
                        )
                        if _retry_output and len(_retry_output.strip()) > 50:
                            # Check if retry produced SEARCH/REPLACE blocks
                            _retry_blocks = _extract_search_replace_blocks(_retry_output)
                            if _retry_blocks:
                                # Apply retry blocks
                                _file_content = _current_file_content
                                _patches_applied = 0
                                for _rb in _retry_blocks:
                                    _search_text = _rb.get("search", "")
                                    _replace_text = _rb.get("replace", "")
                                    _new_content = _fuzzy_apply_patch(_file_content, _search_text, _replace_text)
                                    if _new_content != _file_content:
                                        _file_content = _new_content
                                        _patches_applied += 1
                                if _patches_applied > 0:
                                    try:
                                        from _helpers_io import atomic_write_text as _staging_write
                                        _staging_write(_apply_target, _file_content)
                                    except:
                                        _apply_target.write_text(_file_content, encoding="utf-8")
                                    print(f"  [Anchor Splice] ✅ Retry {_retry_count}: Applied {_patches_applied} "
                                          f"SEARCH/REPLACE block(s) to {task.target_file}")
                                    # Update output so calling code sees the patched block
                                    output = _retry_output
                                    break
                                else:
                                    print(f"  [Anchor Splice] ⚠ Retry {_retry_count}: SEARCH/REPLACE blocks found "
                                          f"but none matched — retrying...")
                            else:
                                print(f"  [Anchor Splice] ⚠ Retry {_retry_count}: Output still has no "
                                      f"SEARCH/REPLACE blocks — retrying...")
                        else:
                            print(f"  [Anchor Splice] ⚠ Retry {_retry_count}: Empty retry output — retrying...")
                    except Exception as _splice_e:
                        print(f"  [Anchor Splice] ⚠ Retry {_retry_count} error: {_splice_e}")

                # -- If all retries exhausted, use deterministic anchor splice fallback --
                if _retry_count >= _MAX_SPLICE_RETRIES or not _anchor_marker_splice:
                    _file_content_for_fallback = _current_file_content
                    if _file_content_for_fallback and _anchor_marker_splice:
                        # Use original output (before retries polluted it)
                        _merged = _anchor_splice_fallback(
                            _file_content_for_fallback,
                            _original_output_for_retry,
                            _anchor_marker_splice,
                            task.target_file,
                        )
                        if _merged != _file_content_for_fallback:
                            try:
                                from _helpers_io import atomic_write_text as _staging_write
                                _staging_write(_apply_target, _merged)
                            except:
                                _apply_target.write_text(_merged, encoding="utf-8")
                            print(f"  [Anchor Splice] ✅ Anchor splice fallback applied to {task.target_file} "
                                  f"(after {_retry_count} retries)")
                        else:
                            print(f"  [Anchor Splice] ⚠ Anchor splice fallback produced no change for "
                                  f"{task.target_file} (anchor: '{_anchor_marker_splice[:60]}')")
                    elif not _anchor_marker_splice:
                        # No anchor marker — this is a non-anchor task that wrote a full file
                        # to an existing target.  This is unusual but not necessarily wrong;
                        # log and skip (the existing file content is preserved).
                        print(f"  [Anchor Splice] ℹ No anchor for {task.target_file} (no anchor_marker) — "
                              f"keeping existing file unchanged ({len(_file_content_for_fallback)} chars)")


    # -- Phase II: MoA Speculative Multi-Draft Synthesis ----------------

    # If the model output contains both [OPTION_A] and [OPTION_B] markers,
    # parse them into separate candidate buffers, pass through resolve_conflict()
    # for native consensus merging, and commit the deduplicated stream.
    import re as _re
    option_a_match = _re.search(r'\[OPTION_A\]\s*(.*?)\s*\[/OPTION_A\]', output, _re.DOTALL)
    option_b_match = _re.search(r'\[OPTION_B\]\s*(.*?)\s*\[/OPTION_B\]', output, _re.DOTALL)

    if option_a_match and option_b_match:
        option_a_code = option_a_match.group(1).strip()
        option_b_code = option_b_match.group(1).strip()
        print(f"  [MoA Multi-Draft] 📋 Detected dual candidate blocks ({len(option_a_code)} vs {len(option_b_code)} chars)")

        # Invoke Native Consensus API: pipe both candidates through CONF expert
        from _mesh_api import resolve_conflict
        from pipeline import call_ollama as _pipeline_call
        from domain_registry import ALL_DOMAINS as _all_domains

        consensus_result = resolve_conflict(
            option_a_code,
            option_b_code,
            veto_justification="Multi-draft synthesis: merge alternative structural implementations",
            feature_request=user_prompt,
            _call_ollama=_pipeline_call,
            _ALL_DOMAINS=_all_domains,
        )

        merged_code = getattr(consensus_result, 'merged_code', '') or ''
        verdict = getattr(consensus_result, 'verdict', 'COMPROMISE')

        # Commit Deduplicated Stream: apply ledger header and append to ledger
        if merged_code:
            print(f"  [MoA Multi-Draft] ✅ Consensus {verdict}: merged {len(option_a_code)} + {len(option_b_code)} -> {len(merged_code)} chars")
            output = ensure_ledger_header(merged_code, task.spec, task.agent)
            from ledger import _append_to_ledger
            _append_to_ledger(
                f"### [MoA Merge: {task.task_id}]\n**Verdict:** {verdict}\n**Merged Output:**\n{merged_code}\n",
                task.agent,
                task.spec,
            )
        else:
            print(f"  [MoA Multi-Draft] ⚠ Consensus returned empty merged_code - using original output")

    # Ledger Guard: auto-fix missing headers
    output = ensure_ledger_header(output, task.spec, task.agent)

    # API Ledger: signatures are written only after preflight validation clears
    # the output (arch-fix and fix-loop paths in _finalize_preflight.py call
    # update_internal_api_ledger on the validated code).  Writing here on raw
    # completion would persist phantom API names into the ledger before guards
    # have had a chance to reject them, poisoning downstream agent prompts.

    task.output = output
    task.signals = extract_signals(output)
    task.double_check = extract_double_check(output)
    task.completed = True

    # Thermal Pacing: Allow Steam Deck APU to dissipate heat between tasks.
    # 5s is the empirically-safe floor on the Deck; tune via MIDWAY_THERMAL_COOLDOWN.
    _thermal_cooldown = float(os.getenv("MIDWAY_THERMAL_COOLDOWN", "5.0") or "5.0")
    print(f"  [Thermal Pacing] Cooling down for {_thermal_cooldown:.1f}s...")
    time.sleep(_thermal_cooldown)

    return output


# -- Cross-Module Compiler Wrapper --------------------------------------

def compile_project(project_root: Path = None, timeout: int = 30) -> tuple[bool, str]:
    """Run the native project build and return (success, error_text).

    Calls cmake --build on Windows, make -j4 on POSIX.
    Error text is capped at 2,000 chars per Directive E.
    Returns (True, '') on successful compilation.
    """
    pr = project_root or PROJECT_ROOT
    try:
        if sys.platform == "win32":
            proc = subprocess.run(
                ["cmake", "--build", "."],
                capture_output=True, text=True, cwd=pr,
                shell=True, timeout=timeout,
            )
        else:
            proc = subprocess.run(
                ["make", "-j4"],
                capture_output=True, text=True, cwd=pr,
                timeout=timeout,
            )
        if proc.returncode == 0:
            return True, ""
        raw_stderr = (proc.stderr or "")[:2000]
        return False, raw_stderr
    except subprocess.TimeoutExpired:
        return False, f"C++ build timed out after {timeout}s"
    except Exception as e:
        return False, str(e)


# -- Director Prompt ------------------------------------------------------

def build_director_prompt(all_domains: dict = None, user_prompt: str = "", project_root: Path = None) -> str:
    domains = all_domains or _ALL_DOMAINS
    available = get_available_domains_text(domains)
    unavailable = get_unavailable_domains_text(domains)
    prompt = (
        "Decompose this feature request into 1-5 tasks. "
        "Each task must have a domain tag and a short title.\n\n"
        "IMPORTANT: You may assign as FEW as 1 task or as MANY as 5. "
        "Only create tasks that are absolutely necessary.\n\n"
        "AVAILABLE DOMAINS (use ONLY these):\n"
        f"{available}\n\n"
        "UNAVAILABLE DOMAINS (do NOT use these):\n"
        f"{unavailable}\n\n"
        "RULES:\n"
        "- Do NOT use [NET] - there is no networking code in the project.\n"
        "- Do NOT use [SHADER] - shader effects are not yet implemented.\n"
        "- Do NOT assign [Lua] tasks that write network code.\n"
        "- Only assign a domain if the project actually has code for it.\n\n"
        "Order by dependency: [C++] first, then [PHYS], then [Lua].\n\n"
        "OUTPUT FORMAT (exactly):\n"
        "## Task Breakdown: [Feature Name]\n"
        "### Task 1: [DOMAIN] - [Short Title] (DependsOn: None)\n"
        "### Task 2: [DOMAIN] - [Short Title] (DependsOn: Task 1)\n"
        "### Task 3: [DOMAIN] - [Short Title] (DependsOn: Task 1, Task 2)\n"
        "...\n\n"
        "CRITICAL: Do NOT write any code. Only list tasks.\n\n"
        "MATH SENSOR: If the user's request involves dense 3D math, quaternions, "
        "or complex physics algorithms, you MUST append the exact string [MATH_HEAVY] "
        "to the very end of your output."
    )

    # -- Live File State: inject current contents of referenced files -----
    if user_prompt and project_root:
        _file_state_blocks: list[str] = []
        _extracted_paths = re.findall(r'[\w/\\.-]+\.\w+', user_prompt)
        for _path_str in _extracted_paths:
            _candidate = (project_root / _path_str).resolve()
            if _candidate.is_file():
                try:
                    _content = _candidate.read_text(encoding="utf-8", errors="replace")
                    if len(_content) > 4000:
                        _content = _content[:4000] + "\n... [truncated to 4000 chars]"
                    _file_state_blocks.append(f"### {_path_str}\n```\n{_content}\n```")
                except Exception:
                    pass
        if _file_state_blocks:
            prompt += "\n\n### Current State of Target Files (Do NOT write tasks to regenerate existing structures):\n"
            prompt += "\n\n".join(_file_state_blocks)

    # -- Global Task Ledger: show previously completed tasks -------------
    if project_root:
        _ledger_path = project_root / "docs" / "memory" / "task_progress.md"
        if _ledger_path.exists():
            try:
                _ledger_content = _ledger_path.read_text(encoding="utf-8", errors="replace").strip()
                if _ledger_content:
                    prompt += f"\n\n### Previously Completed Tasks (DO NOT REPEAT THESE):\n{_ledger_content}"
            except Exception:
                pass

    return prompt
