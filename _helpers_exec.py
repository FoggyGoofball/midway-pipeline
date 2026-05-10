"""
_helpers_exec.py — Base Layer & LLM Logic for the mesh consensus pipeline.
Contains: globals/config, intent classification, GDD librarian,
project state, director prompt, task execution.

No async/await — purely synchronous.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import subprocess
import textwrap
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


# ── Configuration re-exports (set by pipeline.py at import time) ───────────

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


# ── Intent Classification ──────────────────────────────────────────────────

INTENT_CLASSIFIER_SYSTEM = (
    "You are the INTENT CLASSIFIER for 'Midway to Nowhere'. "
    "Analyze the user's prompt and classify it as exactly one of: "
    "MODIFICATION, INFORMATIONAL, QUERY, or CHAT.\n\n"
    "MODIFICATION: User wants to build, add, fix, or modify game features/code. "
    "NEVER classify as MODIFICATION if the user just wants information.\n"
    "INFORMATIONAL: User is asking about the project's progress, architecture, "
    "how something works, GDD contents, or wants a summary/status update. "
    "The user wants a read-only answer.\n"
    "QUERY: User is asking about past work, memory ledgers, or wants information.\n"
    "CHAT: User is greeting, asking how things work generally, or having a conversation.\n\n"
    "Output ONLY the classification word."
)


def classify_intent(user_prompt: str, call_ollama_func, director_model: str) -> str:
    """Zero-shot intent classification using the Director model."""
    intent = call_ollama_func(
        INTENT_CLASSIFIER_SYSTEM,
        f"User prompt: '{user_prompt}'\n\n"
        f"Classify as MODIFICATION, INFORMATIONAL, QUERY, or CHAT.",
        "Intent Classifier",
        director_model,
    )
    intent_clean = intent.strip().upper()
    if "CHAT" in intent_clean:
        return "CHAT"
    if "INFORMATIONAL" in intent_clean:
        return "INFORMATIONAL"
    if "QUERY" in intent_clean:
        return "QUERY"
    return "MODIFICATION"


# ── GDD Librarian ──────────────────────────────────────────────────────────

def recursive_librarian(user_prompt: str, extract_gdd_sections_func=None,
                         gdd_section_map=None, keyword_to_section=None) -> str:
    """Query the Librarian for all relevant GDD sections to build full context."""
    if extract_gdd_sections_func is None:
        from context_extractor import extract_project_context as _e
        extract_gdd_sections_func = _e
    # extract_project_context already returns a formatted markdown string
    sections_text = extract_gdd_sections_func(user_prompt)
    if not sections_text:
        return ""

    return sections_text


# ── Project State ──────────────────────────────────────────────────────────

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


# ── Task Execution ──────────────────────────────────────────────────────────

def execute_task(task, user_prompt: str, director_output: str,
                 all_results: dict, file_context: str, gdd_context: str,
                 sibling_context: str = "",
                 ollama_params: Optional[dict] = None) -> str:
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

    if not domain:
        return f"[ERROR] Unknown agent: {task.agent}"

    preferred_model = domain.get("model", "qwen2.5-coder:7b")
    system = get_agent_system(agent_key)

    # Build context
    context_parts = [
        f"## Original Feature Request\n{user_prompt}",
    ]

    if director_output:
        context_parts.append(f"## Director's Task Breakdown\n{director_output}")

    if file_context:
        context_parts.append(file_context)

    if gdd_context:
        context_parts.append(gdd_context)

    # Auto-Inject Referenced Files
    refs_block = get_referenced_files_cache()
    if refs_block:
        context_parts.append(refs_block)

    # ── Directive A: Stateless Parent Context ──────────────────────────────
    # Parent context is stripped to code artifacts only to prevent linear
    # conversational bloat from bleeding across tasks.
    if task.parent and task.parent in all_results:
        from _helpers_text import strip_to_code_artifacts
        parent_output = all_results[task.parent]
        parent_clean = strip_to_code_artifacts(parent_output, fallback_truncation=800)
        context_parts.append(f"## Parent Task Context (code artifacts only)\n{parent_clean}")

    # ── Directive A: Stateless Sibling Context ─────────────────────────────
    # Already stripped by run_tasks() before being passed as sibling_context.
    # Accept as-is — it has already been code-artifact-sanitized upstream.
    if sibling_context:
        context_parts.append(sibling_context)

    # ── Directive A: Stateless Iteration Output ────────────────────────────
    # Previous iteration output is stripped to code artifacts to prevent the
    # agent's own conversational prose from compounding the context window.
    if task.iteration > 0 and task.output:
        from _helpers_text import strip_to_code_artifacts
        iter_clean = strip_to_code_artifacts(task.output, fallback_truncation=600)
        context_parts.append(f"## Your Previous Output (iteration {task.iteration})\n{iter_clean}")
        if task.iteration >= MAX_ITERATIONS - 1:
            from _prompts import SELF_CORRECT_SYSTEM
            system = SELF_CORRECT_SYSTEM

    # Include any query results that were injected
    if task.context:
        context_parts.append(task.context)

    # The task spec
    context_parts.append(f"## Task Specification\n{task.spec}")

    user_message = "\n\n".join(context_parts)

    label = f"{domain['name']} (Task {task.task_id})"
    if task.is_query:
        label = f"[QUERY] {domain['name']} -> {task.parent}"

    # ── Directive C: Kernel Interrupt — VRAM critical check ──────────────
    # Before sending the prompt to the LLM, check if the combined token
    # payload exceeds 80% of the model's safe context window.
    # If it does, inject a [SYSTEM KERNEL: VRAM critical] warning into the
    # user message, instructing the agent to <PAGE_OUT> before generating.
    from token_budget import TokenBudget
    vram_warning = TokenBudget.check_vram_critical(system, user_message, preferred_model)
    if vram_warning:
        print(f"  [Kernel Interrupt] ⚠ Prepending VRAM critical warning to '{label}'")
        user_message = vram_warning + "\n\n" + user_message

    # ── Directive A: Hard Context Firewall (Absolute Statelessness) ─────────
    # NO history survives between tasks. A brand new messages array is built
    # fresh for every single task invocation: [System Prompt, User Prompt].
    # No .pop(), no pruning, no accumulation — explicit zero-state per call.
    from ollama_client import call_ollama_with_messages, get_last_paged_cache
    messages: list = []
    messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user_message})
    output = call_ollama_with_messages(messages, label, preferred_model, params=ollama_params)

    # ── Directive A: Capture paged_in_cache for Pro-Mode Inheritance ──
    task.paged_files_cache = get_last_paged_cache()
    if task.paged_files_cache:
        print(f"  [Paging Kernel] 📋 Task '{task.task_id}' paged_files_cache: "
              f"{len(task.paged_files_cache)} files, "
              f"{sum(len(v) for v in task.paged_files_cache.values())} total chars")

    # Ledger Guard: auto-fix missing headers
    output = ensure_ledger_header(output, task.spec, task.agent)
    task.output = output
    task.signals = extract_signals(output)
    task.double_check = extract_double_check(output)
    task.completed = True

    # Thermal Pacing: Allow Steam Deck APU to dissipate heat
    print(f"  [Thermal Pacing] Cooling down for 2.0s...")
    time.sleep(2.0)

    return output


# ── Director Prompt ──────────────────────────────────────────────────────

def build_director_prompt(all_domains: dict = None) -> str:
    domains = all_domains or _ALL_DOMAINS
    available = get_available_domains_text(domains)
    unavailable = get_unavailable_domains_text(domains)
    return (
        "Decompose this feature request into 1-5 tasks. "
        "Each task must have a domain tag and a short title.\n\n"
        "IMPORTANT: You may assign as FEW as 1 task or as MANY as 5. "
        "Only create tasks that are absolutely necessary.\n\n"
        "AVAILABLE DOMAINS (use ONLY these):\n"
        f"{available}\n\n"
        "UNAVAILABLE DOMAINS (do NOT use these):\n"
        f"{unavailable}\n\n"
        "RULES:\n"
        "- Do NOT use [NET] — there is no networking code in the project.\n"
        "- Do NOT use [SHADER] — shader effects are not yet implemented.\n"
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
