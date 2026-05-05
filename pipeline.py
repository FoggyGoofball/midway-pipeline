#!/usr/bin/env python3
"""
Midway to Nowhere — Mesh Consensus Pipeline Orchestrator
========================================================
Thin orchestrator (~800 lines). System prompts, helpers, and mesh API
have been extracted to _prompts.py, _pipeline_helpers.py, and _mesh_api.py.
The mesh iteration loops (Phases 0.5-8) live in mesh_loops.py / mesh_finalize.py.

Usage:
    python pipeline.py "add a jackpot feature to the plinko attraction"
    python pipeline.py --checkpoint <id> "continue from checkpoint"

Output: streams to terminal + saves to pipeline_output_<timestamp>.md
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
import urllib.error
import shutil
import subprocess
import textwrap
from datetime import datetime
from pathlib import Path
from collections import deque
import hashlib
import contextlib

# ── Snapshot Manager (optional) ────────────────────────────────────────────
try:
    from pipeline_snapshot import SnapshotManager
    HAS_SNAPSHOT = True
except ImportError:
    SnapshotManager = None
    HAS_SNAPSHOT = False

# ── Extracted Module Imports ───────────────────────────────────────────────
import models
import signals
import domain_registry
import ollama_client
import token_budget as token_budget_module
import offload_store as offload_store_module
import checkpoint as checkpoint_module
import file_references as file_references_module
import ledger as ledger_module
import gdd_extractor
import tagsuggester
import fetch_handler

# Models
from models import SignalType, MeshSignal, ConsensusResult, TaskDescriptor, PipelineContext, Task

# Signals
from signals import extract_signals, parse_signal, extract_double_check, get_verdict
SIGNAL_PATTERNS = signals.SIGNAL_PATTERNS
DOUBLE_CHECK_PATTERN = signals.DOUBLE_CHECK_PATTERN

# Domain Registry
from domain_registry import (
    resolve_agent_name, get_agent_system,
    ALL_DOMAINS, AGENT_ALIAS_MAP,
    LEDGER_MEMORY_RULE, MESH_AGENT_SYSTEM_EXTENSION,
)
PERSONA_MAP = domain_registry.PERSONA_MAP

# Ollama Client
from ollama_client import (
    call_ollama, call_ollama_streamed,
    OLLAMA_HOST, MODEL, CODER_MODEL,
)
REVIEWER_MODEL = ollama_client.REVIEWER_MODEL
OLLAMA_TIMEOUT = ollama_client.OLLAMA_TIMEOUT
OLLAMA_NUM_CTX = ollama_client.OLLAMA_NUM_CTX
MAX_TOKENS = ollama_client.MAX_TOKENS

# Token Budget / Offload Store / Checkpoint / File References
TokenBudget = token_budget_module.TokenBudget
OffloadStore = offload_store_module.OffloadStore
get_offload_store = offload_store_module.get_offload_store
save_checkpoint = checkpoint_module.save_checkpoint
load_checkpoint = checkpoint_module.load_checkpoint
list_checkpoints = checkpoint_module.list_checkpoints
CHECKPOINT_DIR = checkpoint_module.CHECKPOINT_DIR
parse_file_references = file_references_module.parse_file_references
fetch_referenced_files = file_references_module.fetch_referenced_files
set_referenced_files_cache = file_references_module.set_referenced_files_cache
get_referenced_files_cache = file_references_module.get_referenced_files_cache

# Ledger
_normalize_fix_fingerprint = ledger_module._normalize_fix_fingerprint
log_to_session_timeline = ledger_module.log_to_session_timeline
build_anchor_toc = ledger_module.build_anchor_toc
ledger_toc = ledger_module.ledger_toc
ensure_ledger_header = ledger_module.ensure_ledger_header
_append_to_ledger = ledger_module._append_to_ledger
_collect_ledger_entries = ledger_module._collect_ledger_entries
_generate_module_name = ledger_module._generate_module_name

# GDD Extractor
from gdd_extractor import extract_gdd_sections, GDD_SECTION_MAP, KEYWORD_TO_SECTION, search_memory

# Tag Suggester
from tagsuggester import TagSuggester

# Fetch Handler
from fetch_handler import handle_fetch_signal, read_offloaded_file, handle_read_offloaded_signal, _page_out_context

# ── Mesh Loops — lazy-imported to break circular imports ───────────────────
# mesh_loops.py imports from pipeline, so we use __getattr__.
_mesh_loops_lazy = None

def __getattr__(name):
    global _mesh_loops_lazy
    if name in ('run_fetches', 'run_tasks', 'run_code_merge'):
        if _mesh_loops_lazy is None:
            import mesh_loops as _m
            _mesh_loops_lazy = _m
        return getattr(_mesh_loops_lazy, name)
    raise AttributeError(f"module 'pipeline' has no attribute {name!r}")

# ── PipelineContext Singleton ──────────────────────────────────────────────
_CTX = PipelineContext(
    project_root=Path(__file__).resolve().parent.parent,
    memory_dir=Path(__file__).resolve().parent.parent / "docs" / "memory",
    session_id="",
    tasks=[],
    global_signals=[],
)

# ── Configuration ──────────────────────────────────────────────────────────
OLLAMA_HOST = "http://192.168.0.16:11434"

CODER_MODEL = "qwen2.5-coder:7b"
REVIEWER_MODEL = "phi3:14b"
FALLBACK_REVIEWER_MODEL = "llama3.1:8b-instruct-q4_K_M"
LIBRARIAN_MODEL = "llama3.1:8b-instruct-q4_K_M"
SYNTAX_GATE_MODEL = "qwen2.5-coder:1.5b"
INTENT_CLASSIFIER_MODEL = "llama3.2:1b"
CHAT_MODEL = CODER_MODEL
EXECUTION_MODEL = CODER_MODEL
REASONING_MODEL = REVIEWER_MODEL
MODEL = EXECUTION_MODEL
DIRECTOR_MODEL = "llama3.1:8b-instruct-q4_K_M"

# Point to the game engine project root (midway/), not midway-pipeline/ itself
PROJECT_ROOT = Path(__file__).resolve().parent.parent
MAX_ITERATIONS = 3
MAX_CONSENSUS_ITERATIONS = 3
MAX_SUBTASKS_PER_AGENT = 5
REVIEW_MAX_ITERATIONS = 3
SCOPE_FILE_LIMIT = 5
SCOPE_LINE_LIMIT = 400
OLLAMA_TIMEOUT = 420
OLLAMA_NUM_CTX = 32768
MAX_TOKENS = 12000
CHECKPOINT_DIR = PROJECT_ROOT / ".pipeline_checkpoints"
MEMORY_DIR = PROJECT_ROOT / "docs" / "memory"

# ── System Prompts ─────────────────────────────────────────────────────────
# Imported from _prompts.py. These are identical to the originals.
from _prompts import (
    DIRECTOR_SYSTEM, REVIEW_SYSTEM, REVIEW_PROMPT,
    FINAL_APPROVAL_SYSTEM, SELF_CORRECT_SYSTEM, ARCHITECT_FIX_SYSTEM,
    LIBRARIAN_SYSTEM, DIAGNOSTIC_ORACLE_SYSTEM, INTENT_ROUTER_SYSTEM,
    INTENT_CLASSIFIER_SYSTEM, CHAT_SYSTEM,
    CHAT_PATTERNS, SEARCH_MEMORY_SYSTEM,
    REASONING_GATE_DOMAINS, REASONING_GATE_SYSTEM,
)

# ── Helpers ─────────────────────────────────────────────────────────────────
from _pipeline_helpers import (
    is_likely_chat, classify_intent,
    recursive_librarian, get_project_state,
    get_available_domains_text, get_unavailable_domains_text,
    build_director_prompt, curate_project_structure,
    AGENT_FILE_TOOLS_PROMPT, handle_file_read, handle_file_list,
    find_relevant_files, format_file_context,
    generate_failure_report,
    _get_doc_cached,
    execute_task,
)

# ── Mesh API ───────────────────────────────────────────────────────────────
from _mesh_api import (
    submit_mesh_task, get_mesh_task_status, list_mesh_tasks,
    cancel_mesh_task, get_mesh_work_queue, get_mesh_results,
    resolve_conflict, _generate_failure_report_rest,
    register_progress_listener, _emit_progress,
)

# ── Session Timeline ───────────────────────────────────────────────────────
def _get_session_timeline_path() -> Path:
    if _CTX.session_timeline_path is None:
        _CTX.session_timeline_path = PROJECT_ROOT / "docs" / "memory" / "session_timeline.md"
    return _CTX.session_timeline_path

SESSION_TIMELINE_PATH = _get_session_timeline_path()
_MAX_OUTPUT_CHARS = 4000


# ══════════════════════════════════════════════════════════════════════════
#  run_mesh_pipeline — Main orchestration
#  Delegates to mesh_loops.run_fetches / run_tasks and
#  mesh_finalize.run_code_merge.
# ══════════════════════════════════════════════════════════════════════════

def run_mesh_pipeline(user_prompt: str, checkpoint_id: str = None,
                      session_mgr=None) -> str:
    """Run the full mesh consensus pipeline. Synchronous — no async/await."""
    from mesh_loops import run_fetches, run_tasks
    from mesh_finalize import run_code_merge

    ctx = _CTX
    ctx.reset_state()
    ctx.user_prompt = user_prompt
    ctx.project_root = PROJECT_ROOT
    ctx.session_mgr = session_mgr

    # Session Timeline Init
    ctx.session_timeline_path = _get_session_timeline_path()
    os.makedirs(ctx.session_timeline_path.parent, exist_ok=True)
    if not ctx.session_timeline_path.is_file():
        ctx.session_timeline_path.write_text(
            "# Session Timeline\n\n", encoding="utf-8"
        )

    # Checkpoint Resume
    if checkpoint_id:
        cp = load_checkpoint(checkpoint_id)
        if cp and "ctx_state" in cp:
            print(f"  Resuming from checkpoint {checkpoint_id}")
            ctx.load_state(cp["ctx_state"])
            ctx.all_results_dict = cp.get("all_results", {})
            ctx.processed_ids = cp.get("processed_ids", set())
            ctx.tasks_list = cp.get("tasks_list", [])
            user_prompt = ctx.user_prompt
        else:
            print(f"  Checkpoint {checkpoint_id} not found, starting fresh")

    # Intent Classification
    if session_mgr and user_prompt.startswith("[CHAT_FORCED]"):
        ctx.is_chat = True
        user_prompt = user_prompt.replace("[CHAT_FORCED] ", "", 1)
    elif is_likely_chat(user_prompt):
        ctx.is_chat = True
    else:
        intent = classify_intent(user_prompt, call_ollama, DIRECTOR_MODEL)
        ctx.is_chat = (intent == "CHAT")

    if ctx.is_chat:
        print(f"\n{'='*70}")
        print(f"  Chat Mode Detected — Direct Response (bypassing pipeline)")
        print(f"{'='*70}")
        response = call_ollama(CHAT_SYSTEM, user_prompt, "Chat", CHAT_MODEL)
        ctx.final_output = response
        return response

    # ── Phase 0.5–3: Fetches ──
    ctx = run_fetches(ctx)
    if ctx.final_output:
        return ctx.final_output

    # ── Phase 4: Task Execution ──
    ctx = run_tasks(ctx)

    # ── Phases 5–8: Code Merge, Review, Consensus, Final Approval ──
    ctx = run_code_merge(ctx)

    # Write output file
    output_path = PROJECT_ROOT / f"pipeline_output_{datetime.now():%Y%m%d_%H%M%S}.md"
    output_path.write_text(ctx.final_output, encoding="utf-8")
    print(f"\n{'='*60}")
    print(f"  Output saved to {output_path.name}")
    print(f"{'='*60}")

    return ctx.final_output


# ── Main Entry Point ───────────────────────────────────────────────────────

def run_pipeline(user_prompt: str, checkpoint_id: str = None,
                 session_id: str = None) -> str:
    """Main entry point. Returns the full pipeline output as a string."""
    session_mgr = None
    if HAS_SNAPSHOT:
        from pipeline_session import get_or_create_session
        session_mgr = get_or_create_session(
            user_prompt=user_prompt,
            session_id=session_id,
        )
        session_mgr.set_model(EXECUTION_MODEL)
        if session_id:
            print(f"  [Session] Resumed session: {session_id}")
        else:
            print(f"  [Session] Started new session: {session_mgr.session_id}")

    _emit_progress("init", "started", f"Processing: {user_prompt[:60]}...")
    result = run_mesh_pipeline(user_prompt, checkpoint_id, session_mgr)
    _emit_progress("complete", "done")

    if session_mgr:
        session_mgr.mark_completed()

    return result


# ── TagSuggester: Post-Pipeline Tag Auto-Detection ─────────────────────────
try:
    from pipeline_session import SessionManager, get_or_create_session
    HAS_SESSION_MANAGER = True
except ImportError:
    HAS_SESSION_MANAGER = False


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Midway Mesh Pipeline Orchestrator")
    parser.add_argument("prompt", nargs="?", default=None,
                        help="Feature request prompt")
    parser.add_argument("--checkpoint", type=str, default=None,
                        help="Resume from a checkpoint ID")
    parser.add_argument("--list-checkpoints", action="store_true",
                        help="List all saved checkpoints")
    parser.add_argument("--session-id", type=str, default=None,
                        help="Session ID for continuity tracking (auto-generated if absent)")
    parser.add_argument("--chat", action="store_true",
                        help="Force conversational CHAT mode (bypasses intent classification)")

    args = parser.parse_args()

    if args.chat:
        chat_prompt = f"[CHAT_FORCED] {args.prompt}" if args.prompt else "[CHAT_FORCED] Hello"
        result = run_pipeline(chat_prompt, args.checkpoint, args.session_id)
        print("\n" + result)
        sys.exit(0)

    if args.list_checkpoints:
        checkpoints = list_checkpoints()
        if checkpoints:
            print("Saved checkpoints:")
            for c in checkpoints:
                print(f"  {c.get('checkpoint_id')}: {c.get('phase')} ({c.get('timestamp')})")
        else:
            print("No checkpoints found.")
        sys.exit(0)

    if not args.prompt:
        parser.print_help()
        sys.exit(1)

    result = run_pipeline(args.prompt, args.checkpoint, args.session_id)
    print("\n" + result)
