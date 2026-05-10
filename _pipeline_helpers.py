"""
_pipeline_helpers.py — Facade module for the mesh consensus pipeline helpers.

This module re-exports all public symbols from the three sub-modules:
    _helpers_exec  — Base layer & LLM logic
    _helpers_text  — Text parsing & formatting
    _helpers_io    — File system & hashing

All imports use relative syntax to prevent circular imports.
"""

from __future__ import annotations

# ── Re-export from _helpers_exec (Base Layer & LLM Logic) ─────────────────
from _helpers_exec import (
    PROJECT_ROOT,
    MAX_ITERATIONS,
    MAX_CONSENSUS_ITERATIONS,
    MAX_SUBTASKS_PER_AGENT,
    REVIEW_MAX_ITERATIONS,
    SCOPE_FILE_LIMIT,
    SCOPE_LINE_LIMIT,
    _ALL_DOMAINS,
    _init_config,
    INTENT_CLASSIFIER_SYSTEM,
    classify_intent,
    recursive_librarian,
    get_project_state,
    get_available_domains_text,
    get_unavailable_domains_text,
    execute_task,
    build_director_prompt,
)

# ── Re-export from _helpers_text (Text Parsing & Formatting) ──────────────
from _helpers_text import (
    CHAT_PATTERNS,
    is_likely_chat,
    format_file_context,
    generate_failure_report,
    get_normalized_syntax,
)

# ── Re-export from _helpers_io (File System & Hashing) ───────────────────
from _helpers_io import (
    trigger_chime,
    _DOC_CACHE,
    _DOC_CACHE_TTL,
    _DOC_CACHE_MAX,
    _get_doc_cached,
    curate_project_structure,
    find_relevant_files,
    search_memory,
    AGENT_FILE_TOOLS_PROMPT,
    _read_single_file,
    handle_file_read,
    _list_single_dir,
    handle_file_list,
    atomic_write_text,
    _FILE_HASHES,
    compute_file_hash,
    save_initial_file_hashes_from_context,
    verify_file_hashes,
    get_tracked_file_hashes,
)

# ── Public API — explicit __all__ to keep external imports unbroken ───────
__all__ = [
    # Config & globals
    "PROJECT_ROOT",
    "MAX_ITERATIONS",
    "MAX_CONSENSUS_ITERATIONS",
    "MAX_SUBTASKS_PER_AGENT",
    "REVIEW_MAX_ITERATIONS",
    "SCOPE_FILE_LIMIT",
    "SCOPE_LINE_LIMIT",
    "_ALL_DOMAINS",
    "_init_config",

    # Intent & chat detection
    "INTENT_CLASSIFIER_SYSTEM",
    "classify_intent",
    "recursive_librarian",
    "CHAT_PATTERNS",
    "is_likely_chat",

    # Project state
    "get_project_state",
    "get_available_domains_text",
    "get_unavailable_domains_text",

    # Execution
    "execute_task",
    "build_director_prompt",

    # File context & formatting
    "format_file_context",
    "generate_failure_report",
    "get_normalized_syntax",

    # Audio chime
    "trigger_chime",

    # Doc cache
    "_DOC_CACHE",
    "_DOC_CACHE_TTL",
    "_DOC_CACHE_MAX",
    "_get_doc_cached",

    # Project scanner
    "curate_project_structure",
    "find_relevant_files",
    "search_memory",

    # File tools
    "AGENT_FILE_TOOLS_PROMPT",
    "_read_single_file",
    "handle_file_read",
    "_list_single_dir",
    "handle_file_list",

    # Atomic I/O
    "atomic_write_text",

    # File hash locking
    "_FILE_HASHES",
    "compute_file_hash",
    "save_initial_file_hashes_from_context",
    "verify_file_hashes",
    "get_tracked_file_hashes",
]
