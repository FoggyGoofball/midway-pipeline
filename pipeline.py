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
import context_extractor
import tagsuggester
import fetch_handler
import gdd_extractor

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
check_insanity_similarity = ledger_module.check_insanity_similarity

# Context Extractor
from context_extractor import extract_project_context

# Tag Suggester
from tagsuggester import TagSuggester

# Fetch Handler
from fetch_handler import handle_fetch_signal, read_offloaded_file, handle_read_offloaded_signal, _page_out_context

# GDD Extractor
GDD_SECTION_MAP = gdd_extractor.GDD_SECTION_MAP
KEYWORD_TO_SECTION = gdd_extractor.KEYWORD_TO_SECTION
extract_gdd_sections = gdd_extractor.extract_gdd_sections

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
    project_root=Path(os.getenv("MIDWAY_PROJECT_ROOT", Path(__file__).resolve().parent.with_name("midway"))),
    memory_dir=Path(os.getenv("MIDWAY_PROJECT_ROOT", Path(__file__).resolve().parent.with_name("midway"))) / "docs" / "memory",
    session_id="",
    tasks=[],
    global_signals=[],
)

# ── Configuration ──────────────────────────────────────────────────────────
OLLAMA_HOST = "http://192.168.0.16:11434"

# Qwen Coder 3.5 profile (9B) — uncomment when backend hardware supports it
# CODER_MODEL = "qwen3.5:9b"
CODER_MODEL = "qwen2.5-coder:7b"
REVIEWER_MODEL = "phi3:14b"
ANALYST_MODEL = REVIEWER_MODEL
FALLBACK_REVIEWER_MODEL = "llama3.1:8b-instruct-q4_K_M"
PRE_SUMMARIZER_MODEL = "phi3.5:latest"  # 3.8B mini — compresses large context before phi3:14b review
LIBRARIAN_MODEL = "llama3.1:8b-instruct-q4_K_M"
SYNTAX_GATE_MODEL = "qwen2.5-coder:1.5b"
INTENT_CLASSIFIER_MODEL = "llama3.2:1b"
CHAT_MODEL = CODER_MODEL
EXECUTION_MODEL = CODER_MODEL
REASONING_MODEL = REVIEWER_MODEL
MODEL = EXECUTION_MODEL
DIRECTOR_MODEL = "llama3.1:8b-instruct-q4_K_M"

# Point to the game engine project root (midway/), not midway-pipeline/ itself
PROJECT_ROOT = Path(os.getenv("MIDWAY_PROJECT_ROOT", Path(__file__).resolve().parent.with_name("midway")))
MAX_ITERATIONS = 3
MAX_CONSENSUS_ITERATIONS = 3
MAX_SUBTASKS_PER_AGENT = 5
REVIEW_MAX_ITERATIONS = 4
# Max review-fix failures per task before the circuit breaker trips the loop.
CIRCUIT_BREAKER_MAX_FAILURES = 3
# Max times the final-approval stage re-enters the review-fix loop when the
# Director emits REVISION REQUIRED.
FA_MAX_RETRY = 2
SCOPE_FILE_LIMIT = 5
SCOPE_LINE_LIMIT = 400
OLLAMA_TIMEOUT = 420
# Set True to skip all intermediate human-in-the-loop gates (blueprint, architect,
# wireframe, reconciliation, memory archive).  The final merge/integrate gate in
# mesh_finalize.py is intentionally excluded and always requires explicit authorisation.
AUTO_APPROVE_GATES = False
# Hardened global context ceiling aligned to 16GB host RAM / 12GB dedicated safety margin
OLLAMA_NUM_CTX = 16384
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
    ANALYST_SYSTEM, AUDITOR_SYSTEM,
)

# ── Helpers ─────────────────────────────────────────────────────────────────
from _pipeline_helpers import (
    atomic_write_text,
    is_likely_chat, classify_intent,
    recursive_librarian, get_project_state,
    get_available_domains_text, get_unavailable_domains_text,
    build_director_prompt, curate_project_structure,
    AGENT_FILE_TOOLS_PROMPT, handle_file_read, handle_file_list,
    find_relevant_files, format_file_context,
    generate_failure_report,
    _get_doc_cached,
    execute_task,
    search_memory,
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

    # Clear the internal API ledger so this run starts from a blank slate.
    # Without this, signatures from previous runs accumulate and mislead agents.
    from ledger import reset_internal_api_ledger
    reset_internal_api_ledger()
    
    # ── Mount the selected Cartridge (kernel/cartridge separation) ────────────
    # Use cartridge_loader to discover and load the configured cartridge.
    # On first run, defaults to Midway. After that, respects .pipeline_config.json.
    try:
        from cartridge_loader import load_cartridge
        loaded_cartridge = load_cartridge()
        if loaded_cartridge:
            ctx.mount_cartridge(loaded_cartridge)
            cart_name = getattr(ctx, "_cartridge_ecosystem_name", "<unknown>")
            print(f"  [Kernel] Mounted cartridge for project: {cart_name}")
        else:
            print("  [Kernel] ERROR: Could not load any cartridge. Pipeline cannot proceed.")
            ctx.final_output = "Error: No cartridge loaded."
            return ctx.final_output
    except Exception as e:
        print(f"  [Kernel] ERROR loading cartridge: {e}")
        ctx.final_output = f"Error: Cartridge load failed — {e}"
        return ctx.final_output

    # ── Cartridge-Driven Constant Override ────────────────────────────────────
    # Dynamically pull and overwrite primary top-level constants from the mounted
    # cartridge context. Reads OrchestrationConfig fields (and cartridge metadata)
    # and applies them to the module-level globals that downstream modules import.
    # Uses globals() to rewrite module-scoped variables so downstream imports and
    # callers (mesh_loops, mesh_finalize) see the updated values.
    def _overwrite_constants_from_config(cfg):
        """Overwrite module-level pipeline globals from OrchestrationConfig.
        
        Also propagates model overrides into domain_registry.ALL_DOMAINS
        so expert agents resolve dynamically-switched models at runtime.
        """
        _g = globals()
        _g['OLLAMA_HOST'] = cfg.ollama_host
        _g['CODER_MODEL'] = cfg.coder_model
        _g['REVIEWER_MODEL'] = cfg.reviewer_model
        _g['ANALYST_MODEL'] = cfg.analyst_model
        _g['FALLBACK_REVIEWER_MODEL'] = cfg.fallback_reviewer_model
        _g['PRE_SUMMARIZER_MODEL'] = cfg.pre_summarizer_model
        _g['LIBRARIAN_MODEL'] = cfg.librarian_model
        _g['SYNTAX_GATE_MODEL'] = cfg.syntax_gate_model
        _g['INTENT_CLASSIFIER_MODEL'] = cfg.intent_classifier_model
        _g['DIRECTOR_MODEL'] = cfg.director_model
        _g['CHAT_MODEL'] = _g['CODER_MODEL']
        _g['EXECUTION_MODEL'] = _g['CODER_MODEL']
        _g['REASONING_MODEL'] = _g['REVIEWER_MODEL']
        _g['MODEL'] = _g['EXECUTION_MODEL']
        _g['MAX_ITERATIONS'] = cfg.max_iterations
        _g['MAX_CONSENSUS_ITERATIONS'] = cfg.max_consensus_iterations
        _g['MAX_SUBTASKS_PER_AGENT'] = cfg.max_subtasks_per_agent
        _g['REVIEW_MAX_ITERATIONS'] = cfg.review_max_iterations
        _g['SCOPE_FILE_LIMIT'] = cfg.scope_file_limit
        _g['SCOPE_LINE_LIMIT'] = cfg.scope_line_limit
        _g['OLLAMA_TIMEOUT'] = cfg.ollama_timeout
        _g['OLLAMA_NUM_CTX'] = cfg.ollama_num_ctx
        _g['MAX_TOKENS'] = cfg.max_tokens
        
        # ── Propagate model overrides into domain_registry module ──────
        # ALL_DOMAINS in domain_registry.py is evaluated at import time
        # with static constants. After cartridge override, we must update
        # both the module-level constants and the ALL_DOMAINS dict entries
        # so execute_task() resolves dynamic models.
        import domain_registry
        domain_registry.EXECUTION_MODEL = _g['EXECUTION_MODEL']
        domain_registry.CODER_MODEL = _g['CODER_MODEL']
        domain_registry.REVIEWER_MODEL = _g['REVIEWER_MODEL']
        domain_registry.REASONING_MODEL = _g['REASONING_MODEL']
        domain_registry.PRE_SUMMARIZER_MODEL = _g['PRE_SUMMARIZER_MODEL']
        domain_registry.LIBRARIAN_MODEL = _g['LIBRARIAN_MODEL']

        # ── Propagate into ollama_client and _pipeline_helpers ──────────
        # mesh_tasks.py re-exports model constants from _pipeline_helpers
        # which in turn re-exports from ollama_client.  Those module-level
        # names are bound at import time, so we must patch them in-place
        # after the cartridge config override runs.
        import ollama_client as _oc
        import _pipeline_helpers as _ph
        for _mod in (_oc, _ph):
            for _attr, _val in (
                ('CODER_MODEL',          _g['CODER_MODEL']),
                ('REVIEWER_MODEL',       _g['REVIEWER_MODEL']),
                ('DIRECTOR_MODEL',       _g['DIRECTOR_MODEL']),
                ('EXECUTION_MODEL',      _g['EXECUTION_MODEL']),
                ('REASONING_MODEL',      _g['REASONING_MODEL']),
                ('PRE_SUMMARIZER_MODEL', _g['PRE_SUMMARIZER_MODEL']),
            ):
                if hasattr(_mod, _attr):
                    setattr(_mod, _attr, _val)
        
        # Update each domain's model field in ALL_DOMAINS
        from domain_registry import ALL_DOMAINS
        _model_map = {
            "C++": _g['EXECUTION_MODEL'],
            "PHYS": _g['EXECUTION_MODEL'],
            "SHADER": _g['CODER_MODEL'],
            "Lua": _g['EXECUTION_MODEL'],
            "DOC": _g['REASONING_MODEL'],
            "OBSERVABILITY": _g['EXECUTION_MODEL'],
            "CONF": _g['REASONING_MODEL'],
            "TRIBUNAL": _g['REASONING_MODEL'],
            "LIBRARIAN": _g['LIBRARIAN_MODEL'],
        }
        for domain_key, model_name in _model_map.items():
            if domain_key in ALL_DOMAINS:
                ALL_DOMAINS[domain_key]["model"] = model_name
                
        # Also sync _helpers_exec._ALL_DOMAINS which execute_task() reads
        from _helpers_exec import _ALL_DOMAINS as _exec_domains
        for domain_key, model_name in _model_map.items():
            if domain_key in _exec_domains:
                _exec_domains[domain_key]["model"] = model_name


    # Merge OrchestrationConfig from the cartridge into module-level globals
    if getattr(ctx, 'config', None) is not None:
        _overwrite_constants_from_config(ctx.config)
        print("  [Kernel] Module-level constants overwritten from cartridge context.")
        # ── Patch ctx.domain_registry model fields ──────────────────────
        # get_domain_registry() was called during mount_cartridge (before this
        # override ran), so ctx.domain_registry may have pre-override model
        # strings.  Re-apply the same _model_map so fix routing reads live values.
        _live_dr = getattr(ctx, 'domain_registry', None)
        if _live_dr:
            _dr_model_map = {
                "C++":          globals()['EXECUTION_MODEL'],
                "PHYS":         globals()['EXECUTION_MODEL'],
                "SHADER":       globals()['CODER_MODEL'],
                "Lua":          globals()['EXECUTION_MODEL'],
                "DOC":          globals()['REASONING_MODEL'],
                "OBSERVABILITY":globals()['EXECUTION_MODEL'],
                "CONF":         globals()['REASONING_MODEL'],
                "TRIBUNAL":     globals()['REASONING_MODEL'],
                "LIBRARIAN":    globals()['LIBRARIAN_MODEL'],
            }
            for _dk, _mn in _dr_model_map.items():
                if _dk in _live_dr and isinstance(_live_dr[_dk], dict):
                    _live_dr[_dk]["model"] = _mn
    else:
        print("  [Kernel] No cartridge config present — using built-in defaults.")

    # ── Bootstrap prompt factories from the mounted cartridge ─────────────
    # Must run AFTER mount_cartridge and _overwrite_constants_from_config so
    # that _project_name() resolves to the real ecosystem name and all
    # cartridge-supplied fields (reasoning_gate_domains, coding_mandates,
    # review_prompt_extra, terminology_note) are present.
    try:
        from _prompts import pipeline_bootstrap_prompts
        pipeline_bootstrap_prompts()
        print("  [Kernel] Prompt factories refreshed from cartridge.")
    except Exception as _pbe:
        print(f"  [Kernel] WARNING: prompt bootstrap failed — {_pbe}")

    ctx.user_prompt = user_prompt
    ctx.project_root = PROJECT_ROOT
    ctx.session_mgr = session_mgr

    # ── Pre-Flight: Ollama Health Check ────────────────────────────────────
    try:
        urllib.request.urlopen(f"{OLLAMA_HOST}/api/tags", timeout=1.0)
    except Exception as e:
        print(f"\n[FATAL ERROR] Cannot connect to Ollama at {OLLAMA_HOST}. Is it running?")
        ctx.final_output = "Error: Ollama is offline."
        return ctx.final_output

    # Session Timeline Init
    ctx.session_timeline_path = _get_session_timeline_path()
    os.makedirs(ctx.session_timeline_path.parent, exist_ok=True)
    if not ctx.session_timeline_path.is_file():
        atomic_write_text(
            ctx.session_timeline_path, "# Session Timeline\n\n"
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

    # ── Resurrection Check ─────────────────────────────────────────────────
    # If resuming from a BLOCKED checkpoint, skip Phases 1-3 and reconstruct
    # state from the checkpoint data. Treat user_prompt as the manual fix.
    # DIAGNOSTIC is handled in Phase 0.05 above.
    if checkpoint_id and cp and cp.get("phase") == "BLOCKED":
        _resumed_blocked = False
        ckpt_data = cp["data"]

        # Guardrail: Prevent resuming a blocked pipeline without an actual fix
        auto_feed_triggers = {"continue", "next", "proceed", "c", "go", "next task"}
        is_empty_or_trigger = (
            not user_prompt
            or user_prompt.strip().lower() in auto_feed_triggers
        )

        if is_empty_or_trigger:
            print(f"  [Resurrection] Empty/Trigger prompt received. Remaining in BLOCKED state.")
            ctx.output_parts.append("\n**PIPELINE PAUSED.**\n")
            ctx.output_parts.append("You submitted an empty response or a 'continue' trigger. To resume the pipeline from this blocked state, you must provide a manual code fix or reply 'abort' to terminate the run.\n")
            ctx.final_output = "\n".join(ctx.output_parts)
            return ctx.final_output

        _resumed_blocked = True
        ctx.resumed_blocked = True

        # Re-hydrate PipelineContext from BLOCKED checkpoint data
        ctx.all_results_dict = ckpt_data.get("all_results", ctx.all_results_dict)
        ctx.active_code_index = ckpt_data.get("active_code_index", ctx.active_code_index)
        ctx.director_output = ckpt_data.get("director_output", ctx.director_output)
        ctx.user_prompt = user_prompt

        # Reconstruct task_map from saved work_queue
        saved_work_queue = ckpt_data.get("work_queue", [])
        for task_dict in saved_work_queue:
            task_obj = Task(
                agent=task_dict.get("agent", ""),
                spec=task_dict.get("spec", ""),
                parent=task_dict.get("parent"),
                task_id=task_dict.get("task_id", ""),
                iteration=task_dict.get("iteration", 0),
            )
            task_obj.completed = task_dict.get("completed", False)
            task_obj.output = task_dict.get("output", "")
            ctx.task_map[task_obj.task_id] = task_obj

        print(f"  [Resurrection] BLOCKED checkpoint detected. Re-hydrated {len(saved_work_queue)} tasks from checkpoint data.")
        print(f"  [Resurrection] User prompt: {user_prompt[:80]}...")
        print(f"  [Resurrection] Skipping Phases 0.5-3. Resuming from Phases 4-8.")

    # Intent Classification
    if getattr(ctx, 'resumed_blocked', False):
        print("  [Routing] Resurrection active. Bypassing intent classification for manual fix.")
        ctx.is_chat = False
        intent = "EXECUTE"
    elif session_mgr and user_prompt.startswith("[CHAT_FORCED]"):
        ctx.is_chat = True
        user_prompt = user_prompt.replace("[CHAT_FORCED] ", "", 1)
    elif is_likely_chat(user_prompt):
        ctx.is_chat = True
    else:
        intent = classify_intent(user_prompt, call_ollama, DIRECTOR_MODEL)
        ctx.is_chat = (intent == "CHAT")

    if ctx.is_chat:
        _ts = datetime.now().strftime('%H:%M:%S')
        print(f"\n{'='*70}")
        print(f"  [{_ts}] Chat Mode Detected — Direct Response (bypassing pipeline)")
        print(f"{'='*70}")

        # ── Inject project context so CHAT mode has awareness ────
        # The CHAT_SYSTEM prompt says "use provided project context"
        # but previously we sent zero context — the model had no
        # access to GDD, docs, or project state.
        chat_context_parts = [user_prompt]
        try:
            state = get_project_state(PROJECT_ROOT, domain_registry.ALL_DOMAINS)
            if state.strip():
                chat_context_parts.append(
                    "The following is the current project state. "
                    "Use it to inform your answer.\n\n" + state
                )
        except Exception:
            pass
        try:
            structure = curate_project_structure(user_prompt, PROJECT_ROOT)
            if structure.strip():
                chat_context_parts.append(
                    "The following is the project directory structure "
                    "relevant to this query.\n\n" + structure
                )
        except Exception:
            pass
        enriched_input = "\n\n---\n\n".join(chat_context_parts)

        response = call_ollama(CHAT_SYSTEM, enriched_input, "Chat", CHAT_MODEL)
        ctx.final_output = response
        return response

    # ── INFORMATIONAL Route: Analyst (Librarian-First, Read-Only) ──────────
    if intent == "INFORMATIONAL":
        _ts = datetime.now().strftime('%H:%M:%S')
        print(f"\n{'='*70}")
        print(f"  [{_ts}] Informational Query Detected — Analyst Route")
        print(f"{'='*70}")

        # Phase A: Gather ALL source documents first (Librarian-first)
        analyst_context_parts = [f"## User Question\n{user_prompt}"]

        # 1. GDD — full text sections via the Librarian
        try:
            gdd_sections = recursive_librarian(user_prompt)
            if gdd_sections.strip():
                analyst_context_parts.append(
                    "## Relevant GDD Sections\n" + gdd_sections
                )
        except Exception:
            pass

        # 2. Project state (completed features, todo, available domains)
        try:
            state = get_project_state(PROJECT_ROOT, domain_registry.ALL_DOMAINS)
            if state.strip():
                analyst_context_parts.append(
                    "## Current Project State\n" + state
                )
        except Exception:
            pass

        # 3. Project directory structure
        try:
            structure = curate_project_structure(user_prompt, PROJECT_ROOT)
            if structure.strip():
                analyst_context_parts.append(
                    "## Project Structure\n" + structure
                )
        except Exception:
            pass

        analyst_input = "\n\n---\n\n".join(analyst_context_parts)
        print(f"  [Analyst] Gathered {len(analyst_context_parts)} context sources. Querying Analyst...")

        response = call_ollama(ANALYST_SYSTEM, analyst_input, "Analyst", ANALYST_MODEL)
        ctx.final_output = response
        return response

    # ── Blueprint Execution Loop ──────────────────────────────────────────
    # Runs fetches → tasks → merge in a loop, auto-feeding the next blueprint
    # task after each approved cycle, until the blueprint is complete.
    # The integration gate is only shown once all blueprint tasks are done.
    _blueprint_iteration = 0
    while True:
        _blueprint_iteration += 1
        if _blueprint_iteration > 1:
            # Subsequent iterations: reset per-run accumulators but keep
            # the cartridge, project_root, session_mgr, and blueprint-session
            # scope fields mounted.
            _saved_root = ctx.project_root
            _saved_session = ctx.session_mgr
            _saved_cartridge = getattr(ctx, '_mounted_cartridge', None)
            # Scope fields set by run_fetches() on iteration 1 — must survive reset
            # so the auto-feeder's <macro_invariants> wrapper and scope-inheritance
            # logic see the correct values on all subsequent iterations.
            _saved_scope_mode   = getattr(ctx, '_scope_mode',   'GENERAL')
            _saved_scope_target = getattr(ctx, '_scope_target',  '')
            _saved_scope_refs   = getattr(ctx, '_scope_refs',    [])
            _saved_orig_prompt  = getattr(ctx, '_original_user_prompt', '')

            # ── Blueprint cross-iteration memory: snapshot approved files ────
            # Collect every file path that was written during this iteration so
            # the Director on the NEXT iteration sees the current on-disk state
            # and does not re-implement work that already exists.
            _saved_snapshots: dict = dict(getattr(ctx, 'completed_file_snapshots', {}))
            _candidate_paths: set = set()
            # Primary scope target (e.g. attractions/skeeball/skeeball.lua)
            if _saved_scope_target:
                _candidate_paths.add(_saved_scope_target)
            # Per-task target_file fields captured by the Director
            for _t in getattr(ctx, 'tasks_list', []):
                _tf = _t.get('target_file') or ''
                if _tf:
                    _candidate_paths.add(_tf)
            # all_results entries also carry target_file
            for _r in getattr(ctx, 'all_results', []):
                _tf = _r.get('target_file') or ''
                if _tf:
                    _candidate_paths.add(_tf)
            # Read each path from disk (they were just written by the approval step)
            # For NEW_ATTRACTION scope, only accept the canonical path so stale
            # root-level or mis-spelled artifacts never pollute the snapshot store.
            _canonical_constraint: str = ""
            if _saved_scope_mode == "NEW_ATTRACTION" and _saved_scope_target:
                import re as _re_pipe
                _slug = _re_pipe.sub(r'[^\w]+', '_', _saved_scope_target.strip().lower()).strip('_')
                if _slug:
                    _canonical_constraint = f"attractions/{_slug}/{_slug}.lua"

            for _rel_path in _candidate_paths:
                if _canonical_constraint and _rel_path != _canonical_constraint:
                    print(
                        f"  [Blueprint Loop] 🚫 Skipping snapshot for non-canonical path: "
                        f"'{_rel_path}' (expected '{_canonical_constraint}')"
                    )
                    continue
                _abs = ctx.project_root / _rel_path
                if _abs.is_file():
                    try:
                        _saved_snapshots[_rel_path] = _abs.read_text(encoding='utf-8', errors='replace')
                        print(f"  [Blueprint Loop] 📸 Snapshot: {_rel_path} ({len(_saved_snapshots[_rel_path])} chars)")
                    except OSError:
                        pass

            ctx.reset_state()
            ctx.project_root = _saved_root
            ctx.session_mgr = _saved_session
            if _saved_cartridge is not None:
                ctx._mounted_cartridge = _saved_cartridge
            # Restore scope + original prompt so all continuation iterations
            # inherit the correct blueprint session context.
            ctx._scope_mode            = _saved_scope_mode
            ctx._scope_target          = _saved_scope_target
            ctx._scope_refs            = _saved_scope_refs
            ctx._original_user_prompt  = _saved_orig_prompt
            # Restore accumulated cross-iteration file snapshots
            ctx.completed_file_snapshots = _saved_snapshots
            # Empty prompt triggers the auto-feeder to pick the next blueprint task
            ctx.user_prompt = ""
            _ts_iter = datetime.now().strftime('%H:%M:%S')
            print(f"\n{'='*70}")
            print(f"  [{_ts_iter}] [Blueprint Loop] Starting iteration {_blueprint_iteration}...")
            print(f"{'='*70}")

        # ── Phase 0.5–3: Fetches ──
        # On the very first iteration, remove any stale root-level attraction
        # files that a prior bad run may have written.  Attractions must live at
        # attractions/<slug>/<slug>.lua; a root-level <slug>.lua is always wrong.
        if _blueprint_iteration == 1 and ctx.project_root:
            import re as _re_cleanup
            _cl_prompt = getattr(ctx, 'user_prompt', '') or ''
            _cl_slug_raw = ""
            # Try to extract a slug from the prompt (same heuristic as scope classifier)
            _cl_m = _re_cleanup.search(r'\b([a-z][a-z0-9_]{2,}(?:\s+[a-z][a-z0-9_]{2,})?)\b', _cl_prompt.lower())
            if _cl_m:
                _cl_slug_raw = _cl_m.group(1).strip()
            if _cl_slug_raw:
                _cl_slug = _re_cleanup.sub(r'[^\w]+', '_', _cl_slug_raw).strip('_')
                if _cl_slug:
                    for _bad_name in (f"{_cl_slug}.lua", f"{_cl_slug.replace('_', '')}.lua"):
                        _bad_path = ctx.project_root / _bad_name
                        if _bad_path.is_file():
                            try:
                                _bad_path.unlink()
                                print(
                                    f"  [Blueprint Loop] 🗑️  Removed stale root-level artifact: "
                                    f"{_bad_name} (attractions belong in attractions/{_cl_slug}/{_cl_slug}.lua)"
                                )
                            except OSError as _e:
                                print(f"  [Blueprint Loop] ⚠️  Could not remove {_bad_name}: {_e}")

        ctx = run_fetches(ctx)
        if ctx.final_output:
            return ctx.final_output

        # ── Phase 0.9: Pre-Decomposition Architect Pass ───────────────────────
        # Runs AFTER run_fetches so scope/GDD context is available, but BEFORE
        # run_tasks so every agent benefits from the shared design document.
        # Skipped on blueprint continuation iterations (design already present).
        _design_was_fresh = False
        try:
            from mesh_architect import run_architect_pass
            _design_before = getattr(ctx, 'attraction_design', None)
            ctx = run_architect_pass(ctx)
            _design_after = getattr(ctx, 'attraction_design', None)
            _design_was_fresh = (_design_before is None and _design_after is not None)
        except Exception as _arch_err:
            print(f"  [Kernel] ⚠ Architect pass skipped (import/runtime error): {_arch_err}")

        # ── Phase 0.95: Blueprint Approval Gate ──────────────────────────────
        # When the architect pass produced a brand-new design document this
        # iteration, pause and show it to the human before task decomposition
        # begins.  The human can approve (continue), reject (abort), or skip
        # (proceed without a design doc so the Director has full freedom).
        if _design_was_fresh and getattr(ctx, 'attraction_design', None) is not None:
            _design = ctx.attraction_design
            print(f"\n{'='*70}")
            print(f"  🎨 BLUEPRINT APPROVAL — Review the Architect's design document")
            print(f"{'='*70}")
            try:
                print(_design.to_context_block())
            except Exception:
                print(str(_design))
            print(f"\n{'─'*70}")
            print("  This design will guide every task agent in this run.")
            print("  a — Approve and continue (recommended)")
            print("  r — Reject and abort pipeline")
            print("  s — Skip design (run without a design document)")
            print(f"{'─'*70}")
            from pipeline import AUTO_APPROVE_GATES as _auto_gates
            if _auto_gates:
                print("  [Blueprint Gate] ✓ Design auto-approved (AUTO_APPROVE_GATES=True).")
            else:
                while True:
                    try:
                        _bp_choice = input("\nBlueprint approval [a/r/s]: ").strip().lower()
                    except (KeyboardInterrupt, EOFError):
                        _bp_choice = 'r'
                    if _bp_choice in ('a', 'approve', 'yes', 'y'):
                        print("  [Blueprint Gate] ✓ Design approved — proceeding to task decomposition.")
                        break
                    elif _bp_choice in ('r', 'reject', 'no', 'n'):
                        print("  [Blueprint Gate] ⛔ Design rejected — aborting pipeline.")
                        ctx.final_output = "Pipeline aborted: blueprint rejected by user."
                        return ctx.final_output
                    elif _bp_choice in ('s', 'skip'):
                        print("  [Blueprint Gate] ⏭ Design skipped — pipeline will run without a design document.")
                        ctx.attraction_design = None
                        break
                    else:
                        print("  Invalid input. Enter 'a' to approve, 'r' to reject, or 's' to skip.")

        # ── Phase 4: Task Execution ──
        ctx = run_tasks(ctx)

        # ── VRAM Abort Guard: Skip Phases 5–8 if TPS watchdog fired ──
        # If the VRAM Circuit Breaker in run_tasks detected token speed below
        # 2.0 tok/s, ctx.final_verdict is set to "VRAM_OVERRUN" and all
        # remaining waves are aborted. We must NOT proceed to Phases 5–8
        # because that will immediately load models and trigger another
        # cascade of VRAM overruns (as seen in the Architect Syntax Fix loop).
        if getattr(ctx, 'final_verdict', None) == "VRAM_OVERRUN":
            print(f"\n  [VRAM Abort Guard] ⛔ Pipeline aborted during task execution "
                  f"(VRAM overrun). Skipping Phases 5–8.\n")
            output_path = PROJECT_ROOT / f"pipeline_abort_{datetime.now():%Y%m%d_%H%M%S}.md"
            atomic_write_text(output_path, ctx.final_output)
            print(f"  Abort report saved to {output_path.name}")
            return ctx.final_output

        # ── Phases 5–8: Code Merge, Review, Consensus, Final Approval ──
        # run_code_merge handles revision-required retry internally (inside
        # _handle_approved in mesh_finalize.py) before returning to this loop.
        ctx = run_code_merge(ctx)

        # Write per-iteration output file atomically
        output_path = PROJECT_ROOT / f"pipeline_output_{datetime.now():%Y%m%d_%H%M%S}.md"
        atomic_write_text(output_path, ctx.final_output)
        print(f"\n{'='*60}")
        print(f"  Output saved to {output_path.name}")
        print(f"{'='*60}")

        # If the finalize phase signalled that the blueprint has more tasks,
        # loop back and process the next one automatically.
        if getattr(ctx, '_blueprint_continue', False):
            ctx._blueprint_continue = False
            continue

        # Blueprint complete (or no blueprint) — exit the loop.
        break

    return ctx.final_output


# ── Main Entry Point ───────────────────────────────────────────────────────

def run_pipeline(user_prompt: str, checkpoint_id: str = None,
                 session_id: str = None) -> str:
    """Main entry point. Returns the full pipeline output as a string."""
    session_mgr = None
    try:
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
    except ImportError:
        pass

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
