"""
_pipeline_helpers.py — Helper functions for the mesh consensus pipeline.
Extracted from pipeline.py to reduce its size to ~800 lines.

Includes: doc cache, session timeline, intent classification, GDD librarian,
project state, director, file tools, task execution, failure report.

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

# These are imported at call time to avoid circular imports.
# Functions that need pipeline or ollama_client will import locally.


# ── Configuration re-exports (set by pipeline.py at import time) ───────────

PROJECT_ROOT: Path = Path(__file__).parent.resolve()
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


# ── LRU Doc Cache ───────────────────────────────────────────────────────────

_DOC_CACHE: Dict[str, Tuple[str, float]] = {}
_DOC_CACHE_TTL = 30.0
_DOC_CACHE_MAX = 20


def _get_doc_cached(rel_path: str, project_root: Path = None) -> str:
    """Read a doc file with LRU caching. Returns content or empty string."""
    from time import time
    now = time()
    pr = project_root or PROJECT_ROOT

    # Check cache
    if rel_path in _DOC_CACHE:
        content, ts = _DOC_CACHE[rel_path]
        if now - ts < _DOC_CACHE_TTL:
            return content

    # Load fresh
    full_path = pr / rel_path
    if not full_path.is_file():
        return ""
    try:
        content = full_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""

    # Evict oldest if at capacity
    if len(_DOC_CACHE) >= _DOC_CACHE_MAX:
        oldest_key = min(_DOC_CACHE.keys(), key=lambda k: _DOC_CACHE[k][1])
        del _DOC_CACHE[oldest_key]

    _DOC_CACHE[rel_path] = (content, now)
    return content


# ── Chat Intent Detection — Fast-Path Regexes ─────────────────────────────────

def is_likely_chat(prompt: str) -> bool:
    """Fast-path regex check for conversational prompts before LLM classifier runs."""
    prompt_lower = prompt.lower().strip()
    for pattern in CHAT_PATTERNS:
        if re.search(pattern, prompt_lower):
            return True
    return False


CHAT_PATTERNS = [
    r"^(hello|hi|hey|greetings|yo|sup)\b",
    r"^(what can you do|what do you do|how can you help)\b",
    r"(help|guide|explain|understand|walk me|tell me about|show me how)\s+(me|with|the|how|to|what)",
    r"can you (help|explain|tell|show|walk|guide)\s+me",
    r"how (do|does|can|would|should) (i|we|you|the)",
    r"what is (the|a|an|your|this)\b",
    r"(thanks|thank you|good|great|awesome|nice)\b",
    r"(just checking|just asking|just curious|by the way|btw)\b",
    r"(what'?s up|how'?s it going|how are you)",
]


# ── Intent Classification ──────────────────────────────────────────────────

INTENT_CLASSIFIER_SYSTEM = (
    "You are the INTENT CLASSIFIER for 'Midway to Nowhere'. "
    "Analyze the user's prompt and classify it as exactly one of: "
    "MODIFICATION, QUERY, or CHAT.\n\n"
    "MODIFICATION: User wants to build, add, fix, or modify game features/code.\n"
    "QUERY: User is asking about past work, memory ledgers, or wants information.\n"
    "CHAT: User is greeting, asking how things work generally, or having a conversation.\n\n"
    "Output ONLY the classification word."
)


def classify_intent(user_prompt: str, call_ollama_func, director_model: str) -> str:
    """Zero-shot intent classification using the Director model."""
    intent = call_ollama_func(
        INTENT_CLASSIFIER_SYSTEM,
        f"User prompt: '{user_prompt}'\n\nClassify as MODIFICATION, QUERY, or CHAT.",
        "Intent Classifier",
        director_model,
    )
    intent_clean = intent.strip().upper()
    if "CHAT" in intent_clean:
        return "CHAT"
    if "QUERY" in intent_clean:
        return "QUERY"
    return "MODIFICATION"


# ── GDD Librarian ──────────────────────────────────────────────────────────

def recursive_librarian(user_prompt: str, extract_gdd_sections_func=None,
                         gdd_section_map=None, keyword_to_section=None) -> str:
    """Query the Librarian for all relevant GDD sections to build full context."""
    if extract_gdd_sections_func is None:
        from gdd_extractor import extract_gdd_sections as _e
        extract_gdd_sections_func = _e
    sections = extract_gdd_sections_func(user_prompt)
    if not sections:
        return ""
    parts = []
    for section_name, content_text in sections.items():
        parts.append(f"### {section_name}\n{content_text}")
    return "## Relevant GDD Sections\n" + "\n---\n".join(parts)


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
            not_started = re.findall(r"\|\s*\d+\s*\|.*?\| ⬜ (?:Open|Next)\s*\|(.+?)\|", text)
            if not_started:
                lines.append("### ⬜ Not Yet Implemented")
                for s in not_started[:10]:
                    lines.append(f"- {s.strip()}")
                if len(not_started) > 10:
                    lines.append(f"- ... ({len(not_started) - 10} more)")
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


# ── Task Execution ──────────────────────────────────────────────────────────

def execute_task(task, user_prompt: str, director_output: str,
                 all_results: dict, file_context: str, gdd_context: str) -> str:
    """Execute a single task by calling the appropriate agent."""
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

    # Include parent context if this is a sub-task
    if task.parent and task.parent in all_results:
        context_parts.append(f"## Parent Task Context\n{all_results[task.parent]}")

    # Include previous iteration output for self-correction
    # MAX_ITERATIONS is imported at module level above
    if task.iteration > 0 and task.output:
        context_parts.append(f"## Your Previous Output (iteration {task.iteration})\n{task.output}")
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

    # Call LLM
    from ollama_client import call_ollama
    output = call_ollama(system, user_message, label, preferred_model)

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


def get_unavailable_domains_text(all_domains: dict = None) -> str:
    domains = all_domains or _ALL_DOMAINS
    parts = []
    for key, domain in domains.items():
        if not domain["ready"]:
            parts.append(f"- {domain['tag']} {domain['description']}")
    return "\n".join(parts)


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
        "### Task 1: [DOMAIN] - [Short Title]\n"
        "### Task 2: [DOMAIN] - [Short Title]\n"
        "...\n\n"
        "CRITICAL: Do NOT write any code. Only list tasks."
    )


# ── Project Structure Scanner ─────────────────────────────────────────────

def curate_project_structure(prompt: str, project_root: Path = None) -> str:
    """Scan the project directory tree for files relevant to the prompt."""
    pr = project_root or PROJECT_ROOT
    prompt_lower = prompt.lower()
    relevant_dirs = set()
    dir_keywords = {
        "src": ["engine", "c++", "physics", "render", "shader", "attraction",
                "economy", "modifier", "model", "console", "debug"],
        "attractions": ["plinko", "crumbling", "coin cascade", "coin", "cascade",
                        "claw", "slingshot", "ring toss", "ring", "attraction",
                        "booth", "lua", "script"],
        "assets/shaders": ["shader", "glsl", "render", "bloom", "particle",
                           "vertex", "fragment", "karmic"],
        "assets/audio": ["audio", "sound", "music", "sfx"],
        "assets/textures": ["texture", "sprite", "ui"],
        "assets/meshes": ["mesh", "model", "3d"],
        "assets/fonts": ["font", "text"],
        "docs": ["rules", "docs", "documentation", "bridge", "contract",
                 "spec", "todo", "index"],
        "GDD": ["gdd", "design", "spec", "game design"],
        "resources": ["dialog", "json", "data"],
    }
    for keyword, dirs in dir_keywords.items():
        for d in dirs:
            if d in prompt_lower:
                relevant_dirs.add(keyword)
    relevant_dirs.add("src")
    relevant_dirs.add("docs")
    lines = ["## Project Structure (relevant directories)\n"]
    for d in sorted(relevant_dirs):
        full_path = pr / d
        if not full_path.is_dir():
            continue
        lines.append(f"### {d}/")
        count = 0
        for f in sorted(full_path.iterdir()):
            if f.is_file() and f.suffix in (".cpp", ".h", ".hpp", ".lua", ".py",
                                             ".json", ".md", ".glsl", ".vert",
                                             ".frag", ".txt", ".cfg", ".xml"):
                lines.append(f"  - {f.name}")
                count += 1
                if count >= 10:
                    if len(list(full_path.iterdir())) > 10:
                        lines.append(f"  - ... ({len(list(full_path.iterdir())) - 10} more)")
                    break
        for f in sorted(full_path.iterdir()):
            if f.is_dir():
                lines.append(f"  - {f.name}/")
        lines.append("")
    return "\n".join(lines)


# ── File Tools ─────────────────────────────────────────────────────────────

AGENT_FILE_TOOLS_PROMPT = (
    "\n\n---\n"
    "PROGRESSIVE FILE DISCLOSURE TOOLS:\n"
    "You have access to synchronous file-system tools to read the codebase "
    "during your reasoning. To use them, embed one of these signals:\n"
    "- [FILE_READ:<relative_path>] — Read the contents of a file. "
    "Returns the full text of the file at the given path.\n"
    "- [FILE_LIST:<relative_dir>] — List the files in a directory. "
    "Returns a directory listing with file names.\n"
    "The orchestrator will execute the tool and inject the result back into "
    "your context before your next iteration. Use these to explore the "
    "codebase progressively without exceeding your context window.\n"
)


def handle_file_read(signal_content: str, project_root: Path = None) -> str:
    """Synchronous file read tool for agent progressive disclosure.
    
    Includes strict path traversal boundary check — any path that resolves
    outside PROJECT_ROOT is rejected with an error.
    """
    pr = (project_root or PROJECT_ROOT).resolve()
    path = signal_content.strip()
    full_path = (pr / path).resolve()
    # Path traversal boundary check
    try:
        full_path.relative_to(pr)
    except ValueError:
        return f"\n## File Tool Result: ERROR\n**Path:** {path}\n**Error:** Path traversal forbidden.\n"
    if not full_path.is_file():
        return f"\n## File Tool Result: ERROR\n**Path:** {path}\n**Error:** File not found or is a directory.\n"
    try:
        content = full_path.read_text(encoding="utf-8", errors="replace")
        ext = full_path.suffix.lower()
        lang = {".cpp": "cpp", ".h": "cpp", ".hpp": "cpp",
                ".lua": "lua", ".py": "python",
                ".json": "json", ".md": "markdown",
                ".glsl": "glsl", ".vert": "glsl", ".frag": "glsl"}.get(ext, "")
        print(f"  [FileTool] FILE_READ: {path} ({len(content)} chars)")
        if len(content) > 6000:
            content = content[:6000] + "\n\n[... truncated at 6000 chars ...]"
        return f"\n## File Tool Result: {path}\n```{lang}\n{content}\n```\n"
    except Exception as e:
        return f"\n## File Tool Result: ERROR\n**Path:** {path}\n**Error:** {e}\n"


def handle_file_list(signal_content: str, project_root: Path = None) -> str:
    """Synchronous directory listing tool for agent progressive disclosure.
    
    Includes strict path traversal boundary check — any path that resolves
    outside PROJECT_ROOT is rejected with an error.
    """
    pr = (project_root or PROJECT_ROOT).resolve()
    path = signal_content.strip()
    full_path = (pr / path).resolve()
    # Path traversal boundary check
    try:
        full_path.relative_to(pr)
    except ValueError:
        return f"\n## File Tool Result: ERROR\n**Path:** {path}\n**Error:** Path traversal forbidden.\n"
    if not full_path.is_dir():
        return f"\n## File Tool Result: ERROR\n**Path:** {path}\n**Error:** Directory not found.\n"
    try:
        files = sorted(full_path.iterdir())
        items = []
        for f in files:
            label = f.name
            if f.is_dir():
                label += "/"
            items.append(label)
        truncated = items[:50]
        if len(items) > 50:
            truncated.append(f"... ({len(items) - 50} more)")
        result = f"\n## File Tool Result: {path}/\n```\n" + "\n".join(truncated) + "\n```\n"
        print(f"  [FileTool] FILE_LIST: {path} ({len(items)} entries)")
        return result
    except Exception as e:
        return f"\n## File Tool Result: ERROR\n**Path:** {path}\n**Error:** {e}\n"


# ── Autonomous File Reading ────────────────────────────────────────────────

def find_relevant_files(prompt: str, persona: str, project_root: Path = None) -> list:
    """Scan the project for files relevant to the given prompt and persona."""
    pr = project_root or PROJECT_ROOT
    relevant = []
    prompt_lower = prompt.lower()
    persona_lower = persona.lower()
    keyword_map = {
        "plinko":       ["attractions/plinko/"],
        "crumbling":    ["attractions/crumblingfacade/"],
        "coin cascade": ["attractions/coin_cascade/"],
        "physics":      ["src/PhysicsManager.cpp", "src/PhysicsManager.h",
                         "src/MidwayPhysics.cpp", "src/MidwayPhysics.h"],
        "engine":       ["src/Engine.cpp", "src/Engine.h"],
        "attraction":   ["src/AttractionManager.cpp", "src/AttractionManager.h",
                         "attractions/_shared/"],
        "economy":      ["src/EconomyManager.cpp", "src/EconomyManager.h",
                         "economy_state.json"],
        "modifier":     ["src/ModifierState.h", "modifier_state.json"],
        "lua":          ["attractions/", "init.lua"],
        "shader":       ["assets/shaders/"],
        "render":       ["src/DebugRenderer.cpp", "src/DebugRenderer.h"],
        "model":        ["src/ModelManager.cpp", "src/ModelManager.h"],
        "dev console":  ["src/DevConsole.cpp", "src/DevConsole.h"],
        "dialog":       ["resources/dialog.json"],
        "gdd":          ["GDD/"],
        "jolt":         ["docs/jolt_api.md"],
        "box2d":        ["docs/box2d_api.md"],
        "sol2":         ["docs/sol2_api.md"],
        "opengl":       ["docs/opengl_sdl_api.md"],
        "sdl":          ["docs/opengl_sdl_api.md"],
    }
    rule_map = {
        "c++":    "docs/rules_cpp.md",
        "phys":   "docs/rules_phys.md",
        "shader": "docs/rules_shader.md",
        "lua":    "docs/rules_lua.md",
    }
    # Skip pipeline system directories — not game engine files
    _PIPELINE_EXCLUDED_PREFIXES = {
        "midway-pipeline/",
        "docs/memory/",
        "docs/.pipeline_journal/",
        "docs/rules_",
        ".pipeline_checkpoints/",
        ".pipeline_snapshots/",
        "offload_store/",
        "pipeline_output_",
    }
    candidate_paths = set()
    for keyword, paths in keyword_map.items():
        if keyword in prompt_lower:
            for p in paths:
                # Don't include pipeline-specific paths
                if not any(p.startswith(prefix) for prefix in _PIPELINE_EXCLUDED_PREFIXES):
                    candidate_paths.add(p)
    for key, rule_path in rule_map.items():
        if key in persona_lower:
            candidate_paths.add(rule_path)
    candidate_paths.add("docs/engine_lua_bridge_contract.md")
    candidate_paths.add("docs/rules_review.md")
    if not candidate_paths:
        candidate_paths = {
            "src/Engine.h", "src/AttractionManager.h",
            "docs/rules_cpp.md", "docs/rules_lua.md",
            "docs/engine_lua_bridge_contract.md",
        }
    for rel_path in sorted(candidate_paths):
        full_path = pr / rel_path
        if full_path.is_file():
            try:
                content = full_path.read_text(encoding="utf-8", errors="replace")
                if len(content) > 6000:
                    content = content[:6000] + "\n\n[... truncated ...]"
                relevant.append((rel_path, content))
                print(f"  Read: {rel_path} ({len(content)} chars)")
            except Exception as e:
                print(f"  Could not read {rel_path}: {e}")
        elif full_path.is_dir():
            count = 0
            for f in sorted(full_path.rglob("*")):
                if f.is_file() and f.suffix in (".lua", ".cpp", ".h", ".json", ".md", ".glsl", ".vert", ".frag"):
                    try:
                        content = f.read_text(encoding="utf-8", errors="replace")
                        if len(content) > 4000:
                            content = content[:4000] + "\n\n[... truncated ...]"
                        rel = f.relative_to(pr).as_posix()
                        relevant.append((rel, content))
                        count += 1
                        if count >= 3:
                            break
                    except Exception:
                        pass
            print(f"  Read {count} files from {rel_path}")
    return relevant


def format_file_context(files: list, domain_key: str = None,
                        ledger_toc_func=None) -> str:
    """Format discovered files into a context block for the model."""
    if not files:
        return ""
    parts = ["## Relevant Project Files\n"]
    for path, content in files:
        ext = Path(path).suffix.lower()
        lang = {".cpp": "cpp", ".h": "cpp", ".hpp": "cpp",
                ".lua": "lua", ".py": "python",
                ".json": "json", ".md": "markdown",
                ".glsl": "glsl", ".vert": "glsl", ".frag": "glsl"}.get(ext, "")
        parts.append(f"### File: {path}")
        parts.append("```" + lang)
        parts.append(content)
        parts.append("```\n")
    if ledger_toc_func:
        toc = ledger_toc_func(domain_key)
        if toc:
            parts.insert(0, toc + "\n")
    return "\n".join(parts)


# ── Failure Report ─────────────────────────────────────────────────────────

def generate_failure_report(user_prompt: str, consensus_checks: dict,
                            vetos: list, objects: list,
                            all_results: dict, task_map: dict,
                            director_output: str,
                            all_domains: dict = None,
                            resolve_agent_name_func=None) -> str:
    """Generate a curated failure report with suggested manual breakdown."""
    domains = all_domains or _ALL_DOMAINS
    parts = []

    parts.append("## Pipeline Failure Report\n")
    parts.append(f"**Feature request:** {user_prompt}\n")
    parts.append(f"**Consensus iterations exhausted:** {MAX_CONSENSUS_ITERATIONS}\n\n")

    # Failed checks
    parts.append("### Failed Checks\n")
    for check, passed in consensus_checks.items():
        if not passed:
            parts.append(f"- ❌ {check}\n")
    parts.append("")

    # Blocking VETOs
    if vetos:
        parts.append("### Blocking VETOs\n")
        for v in vetos:
            from_name = domains.get(resolve_agent_name_func(v["from"]) if resolve_agent_name_func else v["from"], {}).get("name", v["from"])
            target_name = domains.get(resolve_agent_name_func(v["target"]) if resolve_agent_name_func else v["target"], {}).get("name", v["target"])
            parts.append(f"1. **{from_name}** VETO'd **{target_name}**\n")
            parts.append(f"   - Reason: {v['reason']}\n")
            if v["task_id"] in all_results:
                offending = all_results[v["task_id"]][:200]
                parts.append(f"   - Offending output: {offending}...\n")
            parts.append("")

    # Blocking OBJECTs
    if objects:
        parts.append("### Unresolved OBJECTions\n")
        for o in objects:
            from_name = domains.get(resolve_agent_name_func(o["from"]) if resolve_agent_name_func else o["from"], {}).get("name", o["from"])
            target_name = domains.get(resolve_agent_name_func(o["target"]) if resolve_agent_name_func else o["target"], {}).get("name", o["target"])
            parts.append(f"1. **{from_name}** OBJECTed to **{target_name}**\n")
            parts.append(f"   - Concern: {o['concern']}\n")
            parts.append("")

    # Suggested manual decomposition
    parts.append("### Suggested Manual Decomposition\n")
    parts.append("To resolve this manually, break into these sub-tasks:\n")
    suggested_commands = []
    for v in vetos:
        target = resolve_agent_name_func(v["target"]) if resolve_agent_name_func else v["target"]
        domain = domains.get(target, {})
        name = domain.get("name", target)
        suggested_commands.append(
            f"1. `/arch_{target.lower()}` \"{name}: {v['reason']}\""
        )
    for o in objects:
        target = resolve_agent_name_func(o["target"]) if resolve_agent_name_func else o["target"]
        domain = domains.get(target, {})
        name = domain.get("name", target)
        suggested_commands.append(
            f"1. `/arch_{target.lower()}` \"{name}: {o['concern']}\""
        )
    if not suggested_commands:
        suggested_commands.append(
            "1. `/pipeline` \"Re-run the original prompt with more specific constraints\""
        )
    for cmd in suggested_commands[:5]:
        parts.append(f"{cmd}\n")

    parts.append("\n### Cross-Reference\n")
    parts.append("- docs/rules_cpp.md — C++ engine rules\n")
    parts.append("- docs/rules_lua.md — Lua scripting rules\n")
    parts.append("- docs/rules_phys.md — Physics integration rules\n")
    parts.append("- docs/rules_shader.md — Shader development rules\n")
    parts.append("- docs/engine_lua_bridge_contract.md — C++/Lua API contract\n")

    return "\n".join(parts)


def get_normalized_syntax(code: str) -> str:
    """Strip comments and normalize whitespace for functional code comparison."""
    # Remove C++ and Lua comments
    code = re.sub(r'//.*?\n|/\*.*?\*/|--.*?\n', '', code, flags=re.DOTALL)
    # Normalize whitespace
    code = re.sub(r'\s+', ' ', code).strip()
    # Normalize common structural tokens
    code = code.replace('{ ', '{').replace(' }', '}').replace('( ', '(').replace(' )', ')')
    return code
