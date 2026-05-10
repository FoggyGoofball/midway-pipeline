#!/usr/bin/env python3
"""
Midway to Nowhere — Mesh Consensus Pipeline Orchestrator
========================================================
Multi-agent mesh with full inter-agent communication, recursive sub-task
decomposition, dissent protocol (VETO/OBJECT/RECOURSE), double-check loops,
consensus gate, review-fix cycles, and failure report generation.

Usage:
    python pipeline.py "add a jackpot feature to the plinko attraction"
    python pipeline.py --checkpoint <id> "continue from checkpoint"

Output: streams to terminal + saves to pipeline_output_<timestamp>.md
"""

import json
import os
import re
import sys
import urllib.request
import urllib.error
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from collections import deque

# Import snapshot manager (optional — pipeline works without it)
try:
    from pipeline_snapshot import SnapshotManager
    HAS_SNAPSHOT = True
except ImportError:
    SnapshotManager = None
    HAS_SNAPSHOT = False

# ── Configuration ──────────────────────────────────────────────────────────
OLLAMA_HOST = "http://192.168.0.16:11434"
EXECUTION_MODEL = "qwen2.5-coder:7b"
REASONING_MODEL = "llama3.1:8b-instruct-q4_K_M"
MODEL = EXECUTION_MODEL          # default fallback for call_ollama when no model kwarg
DIRECTOR_MODEL = REASONING_MODEL # Director & Review use reasoning model
PROJECT_ROOT = Path(__file__).parent.resolve()
MAX_ITERATIONS = 3               # Steam Deck: 3 is industry standard for self-correction
MAX_CONSENSUS_ITERATIONS = 3
MAX_SUBTASKS_PER_AGENT = 5
REVIEW_MAX_ITERATIONS = 3        # Max review→fix→re-review cycles
SCOPE_FILE_LIMIT = 3             # Max files before Lead Producer deems "TOO_BROAD"
SCOPE_LINE_LIMIT = 200           # Max lines before Lead Producer deems "TOO_BROAD"
OLLAMA_TIMEOUT = 420
OLLAMA_NUM_CTX = 12288           # 12K input context leaves 20K headroom for output tokens in 32K total
MAX_TOKENS = 6000                 # Client-side token packing ceiling. Leaves ~6K of 12K context window for model output.
CHECKPOINT_DIR = PROJECT_ROOT / ".pipeline_checkpoints"
MEMORY_DIR = PROJECT_ROOT / "docs" / "memory"

# Standard ledger rule appended to every agent system prompt
LEDGER_MEMORY_RULE = (
    "\n\n---\n"
    "MEMORY LEDGER PROTOCOL:\n"
    "You have a persistent disk-based memory ledger at `docs/memory/<domain>_ledger.md`.\n"
    "Whenever you establish a new core loop, define a global variable, or finalize an "
    "architectural decision, you MUST output a markdown block to be appended to your ledger.\n"
    "Every entry MUST be indexed with a specific Markdown header (e.g., ### [ModuleName]).\n"
    "The orchestrator automatically writes these blocks to your ledger file.\n"
    "Use [FETCH:docs/memory/<domain>_ledger.md#<HeaderName>] to retrieve past decisions "
    "that have fallen out of your active context window.\n"
)

# ── Token Budget Tracker ────────────────────────────────────────────────────
# Steam Deck / 32K total context window enforcement

class TokenBudget:
    """Track and enforce token budget across pipeline execution.

    Industry standard pattern: measure before send, truncate before overflow.
    For qwen2.5-coder:7b, estimate 1 token ≈ 3 chars on average (code-heavy).
    """

    def __init__(self, hard_limit: int = MAX_TOKENS):
        self.hard_limit = hard_limit
        self.used = 0
        self.warnings = []

    def estimate_tokens(self, text: str) -> int:
        """Rough token estimate. 1 token per 3 chars for code."""
        return len(text) // 3

    def check(self, text: str) -> bool:
        """Check if adding text would exceed budget. Returns True if safe."""
        estimated = self.estimate_tokens(text)
        return (self.used + estimated) < self.hard_limit

    def add(self, text: str, label: str = "") -> str:
        """Add text to budget. Auto-truncates if would exceed limit.

        Returns (possibly truncated) text. Industry standard: truncate oldest
        context first, preserve latest output."""
        estimated = self.estimate_tokens(text)
        if self.used + estimated < self.hard_limit:
            self.used += estimated
            return text

        # Need to truncate: keep first 10% as framing, then skip, then last 30%
        available = self.hard_limit - self.used
        if available <= 100:
            self.warnings.append(f"[Budget] {label}: OVERFLOW — no room available")
            return f"\n[TOKEN BUDGET EXCEEDED: {label} truncated]\n"

        # Keep head (framing context) + tail (latest content)
        head_tokens = max(50, available // 4)
        tail_tokens = available - head_tokens
        head = text[:head_tokens * 3]
        tail = text[-tail_tokens * 3:]
        truncated = f"{head}\n\n[... TOKEN BUDGET TRUNCATION: ~{estimated} tokens compressed to {available} ...]\n\n{tail}"
        self.used += available
        self.warnings.append(f"[Budget] {label}: truncated {estimated} → {available} tokens")
        return truncated

    def status(self) -> str:
        pct = (self.used / self.hard_limit) * 100
        warnings = ""
        if self.warnings:
            warnings = "\n" + "\n".join(self.warnings[-3:])
        return f"Token budget: {self.used}/{self.hard_limit} ({pct:.0f}%){warnings}"


# ── LRU Doc Cache ───────────────────────────────────────────────────────────
# Cache API doc reads in memory so redundant lookups don't hammer disk or
# blow context with repeated reads.

_DOC_CACHE = {}       # path -> (content, timestamp)
_DOC_CACHE_TTL = 300  # 5 minutes
_DOC_CACHE_MAX = 8    # max entries before eviction

def _get_doc_cached(rel_path: str) -> str:
    """Read a doc file with LRU caching. Returns content or empty string."""
    from time import time
    now = time()

    # Check cache
    if rel_path in _DOC_CACHE:
        content, ts = _DOC_CACHE[rel_path]
        if now - ts < _DOC_CACHE_TTL:
            return content

    # Load fresh
    full_path = PROJECT_ROOT / rel_path
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


# ── Doc Format: Anchor-TOC Builder ──────────────────────────────────────────
# Build cross-reference anchors so the model can cite exact source lines.
# Used by Code Documentarian persona.

def build_anchor_toc(doc_path: str) -> str:
    """Build a Table of Contents with line anchors for a doc file."""
    content = _get_doc_cached(doc_path)
    if not content:
        return f"(doc {doc_path} not found)"
    lines = content.splitlines()
    toc_lines = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#"):
            level = len(stripped.split("#")[0]) + 1
            title = stripped.lstrip("#").strip()
            anchor = f"{doc_path}#L{i+1}"
            toc_lines.append(f"{'  ' * (level-1)}- [{title}]({anchor})")
    return "\n".join(toc_lines[:20])  # max 20 entries to save tokens


# ── Memory Ledger: Table of Contents Builder ───────────────────────────────
# Scans docs/memory/ and builds a TOC for each ledger file.
# Agents see what memory exists and use [FETCH] to pull specific sections.
# Smart truncation: never cuts mid-line, drops subsections first, then whole files.

BOILERPLATE_TITLES = {"Table of Contents", "Memory Bank", "Persistent memory bank"}

def _collect_ledger_entries(mem_file: Path) -> list:
    """Parse a ledger file and return (is_subsection, entry_text) pairs."""
    entries = []
    try:
        content = mem_file.read_text(encoding="utf-8", errors="replace")
        for ln in content.splitlines():
            stripped = ln.strip()
            if not (stripped.startswith("##") or stripped.startswith("###")):
                continue
            title = stripped.lstrip("#").strip()
            if title in BOILERPLATE_TITLES:
                continue
            bm = re.search(r"\[(.*?)\]", stripped)
            anchor = bm.group(1).strip() if bm else title.lower().replace(" ", "-")
            is_sub = stripped.startswith("###")
            rel = mem_file.relative_to(PROJECT_ROOT).as_posix()
            text = f"  - [{title}]({rel}#{anchor}) -- use [FETCH:{rel}#{anchor}]\n"
            entries.append((is_sub, text))
    except Exception:
        pass
    return entries

def ledger_toc(domain_key: str = None) -> str:
    """Build a Table of Contents for all memory ledger files in docs/memory/.
    
    Args:
        domain_key: If provided, the agent's own ledger gets full listing
                    (including ### subsections). Other ledgers show only ## headers.
    """
    mem_dir = MEMORY_DIR
    if not mem_dir.is_dir():
        return ""
    
    # Determine the agent's own ledger file
    own_ledger = ""
    if domain_key and domain_key in ALL_DOMAINS:
        own_ledger = ALL_DOMAINS[domain_key].get("ledger", "")
    
    HARD_LIMIT = 3000
    parts = ["## Memory Ledger Table of Contents\n"]
    
    # Sort: own ledger first, then alphabetical
    ledger_files = sorted(mem_dir.glob("*_ledger.md"),
                          key=lambda f: (f.name != Path(own_ledger).name if own_ledger else False, f.name))
    
    for f in ledger_files:
        rel = f.relative_to(PROJECT_ROOT).as_posix()
        is_own = (rel == own_ledger)
        label = f"### {rel}"
        if is_own:
            label += " (YOUR LEDGER)"
        label += "\n"
        
        # Check if adding file header would exceed limit
        candidate = label
        if len("".join(parts)) + len(candidate) > HARD_LIMIT:
            parts.append(f"  - [... remaining ledgers omitted — use [FETCH] to retrieve ...]\n")
            break
        
        parts.append(candidate)
        
        entries = _collect_ledger_entries(f)
        for is_sub, entry_text in entries:
            # Skip subsections for non-own ledgers (save space)
            if is_sub and not is_own:
                continue
            if len("".join(parts)) + len(entry_text) > HARD_LIMIT:
                parts.append(f"  - [... deeper subsections omitted — use [FETCH] to retrieve ...]\n")
                break
            parts.append(entry_text)
    
    result = "".join(parts)
    return result


# ── Domain Availability ────────────────────────────────────────────────────
ALL_DOMAINS = {
    "C++": {
        "tag": "[C++]",
        "ready": True,
        "model": EXECUTION_MODEL,
        "ledger_rule": "MUST append signature to docs/internal_api_ledger.md",
        "description": "Engine architecture, physics integration, rendering, memory, Vicious Cycle seam, modifier system, object pools, booth lifecycle",
        "ledger": "docs/memory/cpp_ledger.md",
        "system_prompt": (
            "You are the C++17 systems engineer for 'Midway to Nowhere'. "
            "Write ONLY C++17. Use SDL2, OpenGL 3.3+, nlohmann/json. "
            "Be aware of the 'Vicious Cycle' spatial seam (teleporting bodies to Z=0)."
        ),
        "name": "C++ Core",
    },
    "PHYS": {
        "tag": "[PHYS]",
        "ready": True,
        "model": EXECUTION_MODEL,
        "description": "Jolt/Box2D physics, teleport stability, kinematic control, collision layers, sensors",
        "ledger": "docs/memory/phys_ledger.md",
        "system_prompt": (
            "You are the Lead Physics Architect for 'Midway to Nowhere'. "
            "Focus on Jolt/Box2D stability and state-corruption during 'Vicious Cycle' teleports. "
            "Analyze MSVC diagnostics and call stacks for memory leaks."
        ),
        "name": "Physics Architect",
    },
    "SHADER": {
        "tag": "[SHADER]",
        "ready": False,
        "model": EXECUTION_MODEL,
        "description": "GLSL shaders, Karmic-Temporal Matrix, PS1 vertex snapping, bloom, particles",
        "ledger": "docs/memory/shader_ledger.md",
        "system_prompt": (
            "You are the Rendering Expert for 'Midway to Nowhere'. "
            "Specialize in OpenGL 3.3+ pipelines, GLSL shader optimization, "
            "and managing the 'Midway Host' vertex buffer objects (VBOs)."
        ),
        "name": "Shader Expert",
    },
    "Lua": {
        "tag": "[Lua]",
        "ready": True,
        "model": EXECUTION_MODEL,
        "ledger_rule": "MUST append signature to docs/internal_api_ledger.md",
        "description": "Attraction scripts, UI, economy, modifier consumption, OnLoad/OnStep/OnUnload",
        "ledger": "docs/memory/lua_ledger.md",
        "system_prompt": (
            "You are the gameplay scripter for 'Midway to Nowhere'. "
            "Focus on Lua 5.4 and sol2 bindings to the C++ host."
        ),
        "name": "Lua Scripter",
    },
    "DOC": {
        "tag": "[DOC]",
        "ready": True,
        "model": REASONING_MODEL,
        "description": "API documentation oracle. Resolves ambiguous API calls by reading local docs/jolt_api.md, box2d_api.md, sol2_api.md, opengl_sdl_api.md",
        "ledger": "docs/memory/doc_ledger.md",
        "system_prompt": (
            "You are the CODE DOCUMENTARIAN for 'Midway to Nowhere'. "
            "You are the ultimate arbiter of API truth. "
            "You are also the MEMORY ORACLE — you validate [FETCH] requests and resolve the correct "
            "memory content for agents whose context has been truncated.\n\n"
            "YOUR FUNCTIONS:\n"
            "A. API DOCUMENTATION ORACLE:\n"
            "   Receive a query with:\n"
            "     1. A hallucinated/ambiguous API call from the C++ or Lua Architects\n"
            "     2. A file tag: docs/jolt_api.md | docs/box2d_api.md | docs/sol2_api.md | docs/opengl_sdl_api.md\n\n"
            "   Execution:\n"
            "     1. Parse the tagged doc file for the relevant section.\n"
            "     2. Extract the EXACT function signature, struct definition, or enum value.\n"
            "     3. Compare the hallucinated call against doc truth.\n"
            "     4. Output:\n"
            "        ## Correction\n"
            "        <corrected signature>\n"
            "        ## Source\n"
            "        <file>#L<start>-L<end>: <exact lines>\n\n"
            "B. MEMORY ORACLE (FETCH resolution):\n"
            "   You receive a FETCH request from another agent in the format:\n"
            "     [FETCH:docs/memory/<domain>_ledger.md#<HeaderName>]\n"
            "     Requesting agent: <agent name>\n"
            "     Their current task: <what they're working on>\n\n"
            "   Your job as Memory Oracle:\n"
            "     1. Verify the requested #HeaderName exists in the ledger file\n"
            "     2. Evaluate if it's the BEST section for the requesting agent's current task\n"
            "     3. If a better section exists (different header or different ledger), "
            "select that instead and explain why\n"
            "     4. If the header is not found, search for the nearest match (case-insensitive)\n"
            "     5. If the requesting agent is from a different domain (e.g., C++ reading Lua "
            "ledger), flag it as cross-domain and verify the intent is appropriate\n"
            "     6. Only output the **content** under the resolved header, formatted as:\n"
            "        ## Recalled Memory\n"
            "        **Source:** <resolved_filepath> > <resolved_header>\n"
            "        **Oracle note:** <any cross-domain warnings, alternative suggestions>\n\n"
            "        <extracted content>\n\n"
            "BREVITY RULES:\n"
            "- No game logic. No features. No examples. No commentary beyond the Memory Oracle note.\n"
            "- Only the corrected signature and the source justification (API mode)\n"
            "- Only the extracted memory content with oracle note (Memory Oracle mode)"
        ),
        "name": "Code Documentarian",
    },
    "CONF": {
        "tag": "[CONF]",
        "ready": True,
        "model": REASONING_MODEL,
        "description": "Conflict resolution mediator. Resolves VETO/OBJECT disputes between agents.",
        "ledger": "docs/memory/conf_ledger.md",
        "system_prompt": (
            "You are the CONFLICT RESOLUTION AGENT for 'Midway to Nowhere'. "
            "Your role is to mediate disputes between specialized agents. "
            "You do NOT write code. You do NOT implement features.\n\n"
            "When receiving a VETO or OBJECT signal:\n"
            "1. Read the original code (Agent B)\n"
            "2. Read the modified code (Agent A)\n"
            "3. Read the VETO justification\n"
            "4. Read the original feature request\n"
            "5. Read the relevant rule file\n\n"
            "Decision options:\n"
            "- SUSTAIN VETO: Agent B's original is more correct for the feature intent. "
            "Agent A's suggestions are appended as notes.\n"
            "- OVERRULE VETO: Agent A's change is technically correct AND preserves feature intent. "
            "Explain why the VETO is overruled.\n"
            "- COMPROMISE: Neither is fully correct. Generate a merged version that satisfies both concerns.\n\n"
            "CRITICAL RULE: Preserve feature intent over technical purity. "
            "If Agent A's 'fix' strips gameplay mechanics, narrative flavor, or modifier interactions, sustain the VETO."
        ),
        "name": "Conflict Resolution",
    },
}

# Build PERSONA_MAP from ready domains only
PERSONA_MAP = {}
for key, domain in ALL_DOMAINS.items():
    if domain["ready"]:
        PERSONA_MAP[key] = {"system": domain["system_prompt"], "name": domain["name"]}
        PERSONA_MAP[key.lower()] = {"system": domain["system_prompt"], "name": domain["name"]}


# ── System Prompts ─────────────────────────────────────────────────────────

DIRECTOR_SYSTEM = (
    "You are the PROJECT DIRECTOR for 'Midway to Nowhere' game project. "
    "Your ONLY job: decompose feature requests into 1-5 tasks, each tagged with an available domain. "
    "Output ONLY the task list. NO code. NO explanations. NO commentary."
) + (
    "\n\n---\n"
    "MEMORY LEDGER PROTOCOL:\n"
    "Your assigned memory ledger: docs/memory/architecture_ledger.md\n"
    "Whenever you finalize a task decomposition or architectural decision, "
    "you MUST output a markdown block to be appended to your ledger.\n"
    "Every entry MUST be indexed with a specific Markdown header (e.g., ### [ModuleName]).\n"
    "Use [FETCH:docs/memory/architecture_ledger.md#<HeaderName>] to retrieve past decisions.\n"
)

REVIEW_SYSTEM = (
    "You are the INTEGRATION REVIEWER for 'Midway to Nowhere'. "
    "Your ONLY job: review generated code against engine rules and identify issues. "
    "Do NOT write code. Do NOT fix problems. "
    "End your review with **PASS** or **FAIL** on its own line."
) + LEDGER_MEMORY_RULE

REVIEW_PROMPT = (
    "Review the generated code below. Check for:\n"
    "1. Cross-domain issues: C++ bridge <-> Lua calls\n"
    "2. Rule compliance: Check against docs/rules_cpp.md, rules_lua.md, rules_phys.md, rules_shader.md\n"
    "3. Vicious Cycle consistency: C++ applies teleport, Lua does not decide\n"
    "4. Modifier system: All 9 values synced across all layers\n"
    "5. Error handling: No raw pointers, server-authoritative economy\n\n"
    "OUTPUT FORMAT:\n"
    "## Integration Review\n"
    "### Issues\n"
    "- Issue 1: ...\n"
    "### Verdict\n"
    "**PASS** or **FAIL**\n\n"
    "End with **PASS** or **FAIL** on its own line."
)

FINAL_APPROVAL_SYSTEM = (
    "You are the PROJECT DIRECTOR for 'Midway to Nowhere'. "
    "Review the completed work and either APPROVE or request REVISIONS. "
    "Start your response with **APPROVED** or **REVISION REQUIRED**."
) + LEDGER_MEMORY_RULE

SELF_CORRECT_SYSTEM = (
    "You are a code reviewer examining your own previous output. "
    "Identify errors, bugs, or missing pieces, then produce an improved version. "
    "If no issues found, state 'NO ISSUES FOUND' and repeat your previous output unchanged."
)

ARCHITECT_FIX_SYSTEM = (
    "You are the domain architect for 'Midway to Nowhere'. "
    "The Integration Reviewer has identified issues in your code. "
    "Fix ALL reported issues and produce corrected code. "
    "Address every issue the Reviewer raised. "
    "If you believe an issue is a false positive, explain why."
) + LEDGER_MEMORY_RULE

LIBRARIAN_SYSTEM = (
    "You are the GDD LIBRARIAN for 'Midway to Nowhere'. "
    "Your ONLY job: given a feature request, identify which sections of the "
    "Game Design Document (GDD) are relevant. Output ONLY the section names "
    "and a 1-sentence summary of why each is relevant. NO code. NO commentary."
)

MESH_AGENT_SYSTEM_EXTENSION = (
    "\n\n---\n"
    "MESH COMMUNICATION PROTOCOL:\n"
    "You may communicate with other agents by embedding signals in your output:\n"
    "- [QUERY:<target_agent>:<question>] — Ask another agent for information. "
    "The orchestrator will pause you, route the query, and inject the answer back.\n"
    "- [DELEGATE:<target_agent>:<sub_task_spec>] — Break off a sub-task (max 5 total). "
    "The orchestrator will execute it and return the result.\n"
    "- [RESULT:<summary>] — Summarize your output for other agents.\n"
    "- [APPROVE] — Signal you are satisfied with the current state.\n"
    "- [REVISE:<target_agent>:<reason>] — Request changes from another agent.\n"
    "- [VETO:<target_agent>:<reason>] — HARD BLOCK. Another agent modified your code "
    "in a way that breaks feature intent. This triggers conflict resolution.\n"
    "- [OBJECT:<target_agent>:<concern>] — Soft flag. Another agent's change has issues "
    "but is not blocking.\n"
    "- [RECOURSE:Director:<appeal>] — Appeal a VETO override to the Director.\n"
    "- [CONSULT:<target_agent>:<query>] — Request peer review.\n\n"
    "- [FETCH:<filepath>#<HeaderName>] — Recall context from your disk-based memory ledger. Does NOT count against iteration limit.\n"
    "DOUBLE-CHECK REQUIREMENT:\n"
    "At the end of your output, include:\n"
    "## Double-Check\n"
    "**Original prompt:** <truncated original request>\n"
    "**My output addresses:** <bullet points linking output back to prompt>\n"
    "**Unresolved items:** <anything from original prompt not yet addressed>\n"
    "If there are unresolved items, the orchestrator will give you another iteration."
)

# ── Signal Types ───────────────────────────────────────────────────────────

SIGNAL_PATTERNS = {
    "QUERY": r"\[QUERY:([^\]]+):([^\]]+)\]",
    "DELEGATE": r"\[DELEGATE:([^\]]+):([^\]]+)\]",
    "RESULT": r"\[RESULT:([^\]]+)\]",
    "APPROVE": r"\[APPROVE\]",
    "REVISE": r"\[REVISE:([^\]]+):([^\]]+)\]",
    "VETO": r"\[VETO:([^\]]+):([^\]]+)\]",
    "OBJECT": r"\[OBJECT:([^\]]+):([^\]]+)\]",
    "RECOURSE": r"\[RECOURSE:([^\]]+):([^\]]+)\]",
    "CONSULT": r"\[CONSULT:([^\]]+):([^\]]+)\]",
    "FETCH": r"\[FETCH:([^\]]+)#([^\]]+)\]",
}

# Multi-line double-check pattern: captures the remainder of the block after
# the three marked sections, allowing bullet items across lines.
DOUBLE_CHECK_PATTERN = (
    r"## Double-Check\s*\n"
    r"\*\*Original prompt:\*\*(.*?)(?:\n|$)"
    r"\s*\*\*My output addresses:\*\*(.*?)(?:\n|$)"
    r"\s*\*\*Unresolved items:\*\*(.*?)(?:\n##|\n---|\Z)"
)


# ── GDD Librarian ──────────────────────────────────────────────────────────

GDD_PATH = PROJECT_ROOT / "GDD" / "Midway_to_Nowhere_Master_GDD_v19.md"

GDD_SECTION_MAP = {
    "executive":       {"start": 24, "end": 33, "label": "1. Executive Summary & Core Architecture"},
    "technical":       {"start": 34, "end": 52, "label": "2. Technical Backbone: Custom C++ Engine"},
    "economy":         {"start": 53, "end": 57, "label": "3. Dual-Currency Economy & Streak Protocol"},
    "modifier":        {"start": 58, "end": 73, "label": "4. Global Identity Stats & Modifiers"},
    "loot":            {"start": 74, "end": 78, "label": "5. Augmentation & Bribery Loop"},
    "pacing":          {"start": 79, "end": 103, "label": "6. Pacing, Onboarding, Meta-Progression"},
    "concessions":     {"start": 104, "end": 108, "label": "7. Concessions & Utility Stalls"},
    "coin cascade":    {"start": 113, "end": 118, "label": "8. The Coin Cascade"},
    "plinko":          {"start": 119, "end": 124, "label": "8. Purgatorial Plinko"},
    "claw":            {"start": 125, "end": 130, "label": "8. The Claw of Condemnation"},
    "slingshot":       {"start": 131, "end": 136, "label": "8. The Slingshot Array"},
    "ring toss":       {"start": 137, "end": 142, "label": "8. The Ring Toss"},
    "crumbling":       {"start": 143, "end": 150, "label": "8. The Crumbling Façade"},
    "future":          {"start": 151, "end": 160, "label": "9. Midway Expansion Catalog"},
    "boss":            {"start": 161, "end": 244, "label": "10. Identity Loadouts & Boss Encounters"},
    "endgame":         {"start": 245, "end": 248, "label": "11. Terminal Wagers & True Endgame"},
    "narrative":       {"start": 249, "end": 331, "label": "12-13. Narrative & Origins"},
    "roadmap":         {"start": 332, "end": 336, "label": "14. Solo Developer Roadmap"},
}

KEYWORD_TO_SECTION = {
    "plinko": "plinko", "coin cascade": "coin cascade", "coin": "coin cascade",
    "cascade": "coin cascade", "crumbling": "crumbling", "facade": "crumbling",
    "claw": "claw", "slingshot": "slingshot", "ring toss": "ring toss",
    "ring": "ring toss", "economy": "economy", "token": "economy",
    "ticket": "economy", "currency": "economy", "modifier": "modifier",
    "stats": "modifier", "karma": "modifier", "boss": "boss", "bosses": "boss",
    "encounter": "boss", "narrative": "narrative", "story": "narrative",
    "lore": "narrative", "engine": "technical", "c++": "technical",
    "physics": "technical", "rendering": "technical", "shader": "technical",
    "lua": "technical", "pacing": "pacing", "onboarding": "pacing",
    "meta": "pacing", "concession": "concessions", "food": "concessions",
    "loot": "loot", "prize": "loot", "augment": "loot", "future": "future",
    "expansion": "future", "endgame": "endgame", "terminal": "endgame",
    "roadmap": "roadmap", "executive": "executive", "summary": "executive",
}


def get_project_state() -> str:
    """Reads docs/completed_features.md and docs/todo.md for project summary."""
    lines = ["## Current Project State\n"]
    completed_path = PROJECT_ROOT / "docs" / "completed_features.md"
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
    todo_path = PROJECT_ROOT / "docs" / "todo.md"
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
    for key, domain in ALL_DOMAINS.items():
        if domain["ready"]:
            lines.append(f"- {domain['tag']} {domain['description']}")
    lines.append("")
    lines.append("### 🔴 Unavailable Domains")
    for key, domain in ALL_DOMAINS.items():
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


def get_available_domains_text() -> str:
    parts = []
    for key, domain in ALL_DOMAINS.items():
        if domain["ready"]:
            parts.append(f"- {domain['tag']} {domain['description']}")
    return "\n".join(parts)


def get_unavailable_domains_text() -> str:
    parts = []
    for key, domain in ALL_DOMAINS.items():
        if not domain["ready"]:
            parts.append(f"- {domain['tag']} {domain['description']}")
    return "\n".join(parts)


def build_director_prompt() -> str:
    available = get_available_domains_text()
    unavailable = get_unavailable_domains_text()
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


def curate_project_structure(prompt: str) -> str:
    """Scan the project directory tree for files relevant to the prompt."""
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
        full_path = PROJECT_ROOT / d
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


def extract_gdd_sections(prompt: str) -> str:
    """GDD Librarian: extract only relevant GDD sections based on the prompt."""
    if not GDD_PATH.is_file():
        return ""
    prompt_lower = prompt.lower()
    selected_keys = set()
    for keyword, section_key in KEYWORD_TO_SECTION.items():
        if keyword in prompt_lower:
            selected_keys.add(section_key)
    selected_keys.add("executive")
    selected_keys.add("technical")
    try:
        lines = GDD_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return ""
    excerpts = []
    for key in sorted(selected_keys, key=lambda k: GDD_SECTION_MAP[k]["start"]):
        section = GDD_SECTION_MAP.get(key)
        if not section:
            continue
        start = section["start"] - 1
        end = min(section["end"], len(lines))
        if start >= end:
            continue
        section_lines = lines[start:end]
        excerpts.append(f"### {section['label']}\n" + "\n".join(section_lines))
    if not excerpts:
        return ""
    result = "## Relevant GDD Excerpts\n" + "\n\n".join(excerpts)
    print(f"  [GDD Librarian] Extracted {len(selected_keys)} section(s) from GDD")
    return result


def recursive_librarian(prompt: str, depth: int = 0, max_depth: int = 3) -> str:
    if depth >= max_depth:
        return ""
    result = extract_gdd_sections(prompt)
    if result and len(result) > 100:
        return result
    librarian_prompt = (
        f"Given this feature request: '{prompt}'\n\n"
        f"Which GDD sections would be most relevant? "
        f"Available sections: {', '.join(GDD_SECTION_MAP.keys())}\n"
        f"Output only the section names, comma-separated."
    )
    librarian_response = call_ollama(
        LIBRARIAN_SYSTEM,
        librarian_prompt,
        f"GDD Librarian (recursive depth {depth + 1})",
    )
    found_sections = []
    for key in GDD_SECTION_MAP.keys():
        if key.lower() in librarian_response.lower():
            found_sections.append(key)
    if found_sections:
        refined_prompt = prompt + " " + " ".join(found_sections)
        return recursive_librarian(refined_prompt, depth + 1, max_depth)
    return result


# ── Autonomous File Reading ────────────────────────────────────────────────

def find_relevant_files(prompt: str, persona: str) -> list:
    """Scan the project for files relevant to the given prompt and persona."""
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
    candidate_paths = set()
    for keyword, paths in keyword_map.items():
        if keyword in prompt_lower:
            for p in paths:
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
        full_path = PROJECT_ROOT / rel_path
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
                        rel = f.relative_to(PROJECT_ROOT).as_posix()
                        relevant.append((rel, content))
                        count += 1
                        if count >= 3:
                            break
                    except Exception:
                        pass
            print(f"  Read {count} files from {rel_path}")
    return relevant


def format_file_context(files: list, domain_key: str = None) -> str:
    """Format discovered files into a context block for the model.
    
    Args:
        files: List of (path, content) tuples from find_relevant_files()
        domain_key: Agent domain key for ledger-aware TOC scoping.
    """
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
    toc = ledger_toc(domain_key)
    if toc:
        parts.insert(0, toc + "\n")
    return "\n".join(parts)


# ── Ollama API Call ────────────────────────────────────────────────────────

def call_ollama(system: str, user: str, label: str, model: str = None) -> str:
    """Call Ollama's /api/chat endpoint. Returns the full response text."""
    use_model = model or MODEL
    print(f"\n{'='*60}")
    print(f"  [{label}] Calling Ollama ({use_model})...")
    print(f"{'='*60}")
    sys.stdout.flush()
    payload = {
        "model": use_model,
        "stream": False,
        "options": {"num_ctx": OLLAMA_NUM_CTX},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA_HOST}/api/chat",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            content = result.get("message", {}).get("content", "")
            print(content)
            sys.stdout.flush()
            return content
    except urllib.error.URLError as e:
        msg = f"[SYSTEM ERROR: OLLAMA TIMEOUT] Could not reach Ollama at {OLLAMA_HOST}: {e.reason}"
        print(msg)
        return msg
    except json.JSONDecodeError as e:
        msg = f"[ERROR] Invalid JSON response from Ollama: {e}"
        print(msg)
        return msg
    except Exception as e:
        msg = f"[ERROR] {e}"
        print(msg)
        return msg


# ── Signal Parsing ─────────────────────────────────────────────────────────

def extract_signals(text: str) -> list:
    """Extract all mesh communication signals from agent output."""
    signals = []
    for signal_type, pattern in SIGNAL_PATTERNS.items():
        for match in re.finditer(pattern, text, re.DOTALL):
            groups = match.groups()
            signal = {"type": signal_type, "match": match.group(0)}
            if signal_type == "APPROVE":
                signal["target"] = None
                signal["content"] = None
            elif signal_type == "RESULT":
                signal["target"] = None
                signal["content"] = groups[0].strip()
            else:
                signal["target"] = groups[0].strip()
                signal["content"] = groups[1].strip()
            signals.append(signal)
    return signals


def extract_double_check(text: str) -> dict:
    """Extract the double-check section from agent output.

    Uses a multi-line pattern that captures bullet-point content across lines
    rather than stopping at the first newline.
    """
    match = re.search(DOUBLE_CHECK_PATTERN, text, re.DOTALL)
    if match:
        return {
            "original_prompt": match.group(1).strip(),
            "addresses": match.group(2).strip(),
            "unresolved": match.group(3).strip(),
        }
    return None


def get_verdict(review_text: str) -> str:
    """Extract the verdict from a review output.

    Returns 'PASS', 'FAIL', or 'UNKNOWN'.
    FAIL is checked first (higher priority) to avoid false PASS on negative commentary.
    """
    # Check FAIL first — bold or bare
    if re.search(r"\*\*FAIL\*\*", review_text):
        return "FAIL"
    if re.search(r"(?m)^FAIL$", review_text):
        return "FAIL"
    # Then check PASS
    if re.search(r"\*\*PASS\*\*", review_text):
        return "PASS"
    if re.search(r"(?m)^PASS$", review_text):
        return "PASS"
    return "UNKNOWN"



# ── Memory Fetch: [FETCH] Recall Protocol ─────────────────────────────────
def handle_fetch_signal(fetch_tag: str) -> str:
    """Parse [FETCH:filepath#anchor] tag, return content under that header."""
    match = re.match(r"\[FETCH:([^#]+)#([^\]]+)\]", fetch_tag.strip())
    if not match:
        print(f"  [FETCH] Invalid format: {fetch_tag[:80]}")
        return ""
    filepath = match.group(1).strip()
    anchor = match.group(2).strip()
    full_path = PROJECT_ROOT / filepath
    if not full_path.is_file():
        print(f"  [FETCH] File not found: {filepath}")
        return ""
    try:
        content = full_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        print(f"  [FETCH] Error reading {filepath}: {e}")
        return ""
    lines_ = content.splitlines()
    header_line = None
    header_start = -1
    for idx, ln in enumerate(lines_):
        stripped = ln.strip()
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip().lower()
            if anchor.lower() == title or anchor.lower() in title:
                header_start = idx; header_line = stripped; break
            bm = re.search(r"\[(.*?)\]", stripped)
            if bm and anchor.lower() == bm.group(1).strip().lower():
                header_start = idx; header_line = stripped; break
    if header_start == -1:
        print(f"  [FETCH] Header {anchor} not found in {filepath}")
        return ""
    hdr_lvl = header_line.split("#")[0].count("#") + 1
    content_lines = []
    for j in range(header_start + 1, len(lines_)):
        ln = lines_[j]
        stripped = ln.strip()
        if stripped.startswith("#"):
            level = stripped.split("#")[0].count("#") + 1
            if level <= hdr_lvl:
                break
        content_lines.append(ln)
    result = ("\n## Recalled Memory\n" + f"**Source:** {filepath} > {header_line.strip()}\n\n" + "\n".join(content_lines))
    print(f"  [FETCH] Recalled {len(content_lines)} lines from {filepath}#{anchor}")
    return result

# ── Checkpoint System ──────────────────────────────────────────────────────

def save_checkpoint(checkpoint_id: str, phase: str, data: dict) -> None:
    """Save pipeline state to a checkpoint file."""
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    ckpt = {
        "checkpoint_id": checkpoint_id,
        "phase": phase,
        "timestamp": datetime.now().isoformat(),
        "data": data,
    }
    path = CHECKPOINT_DIR / f"{checkpoint_id}.json"
    path.write_text(json.dumps(ckpt, indent=2), encoding="utf-8")
    print(f"  [Checkpoint] Saved: {checkpoint_id} (phase: {phase})")


def load_checkpoint(checkpoint_id: str) -> dict:
    """Load a checkpoint by ID."""
    path = CHECKPOINT_DIR / f"{checkpoint_id}.json"
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def list_checkpoints() -> list:
    """List all saved checkpoints."""
    if not CHECKPOINT_DIR.is_dir():
        return []
    checkpoints = []
    for f in sorted(CHECKPOINT_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            checkpoints.append(data)
        except Exception:
            pass
    return checkpoints


# ── Mesh Consensus Engine ──────────────────────────────────────────────────

class Task:
    """A work item in the mesh queue."""
    def __init__(self, agent: str, spec: str, parent: str = None,
                 task_id: str = None, is_query: bool = False,
                 iteration: int = 0, context: str = ""):
        self.agent = agent
        self.spec = spec
        self.parent = parent
        self.task_id = task_id or f"{agent}_{datetime.now().strftime('%H%M%S%f')}"
        self.is_query = is_query
        self.iteration = iteration
        self.context = context
        self.output = ""
        self.signals = []
        self.double_check = None
        self.completed = False

    def __repr__(self):
        return f"Task({self.agent}, parent={self.parent}, query={self.is_query}, iter={self.iteration})"


def resolve_agent_name(name: str) -> str:
    """Resolve a signal target name to a domain key."""
    name_lower = name.lower().strip()
    # Direct match
    for key in ALL_DOMAINS:
        if key.lower() == name_lower:
            return key
    # Name match
    for key, domain in ALL_DOMAINS.items():
        if domain["name"].lower() == name_lower:
            return key
    # Partial match
    for key, domain in ALL_DOMAINS.items():
        if name_lower in domain["name"].lower() or name_lower in key.lower():
            return key
    return name


def get_agent_system(agent_key: str) -> str:
    """Get the system prompt for an agent, with mesh extension."""
    domain = ALL_DOMAINS.get(agent_key)
    if not domain:
        return ""
    base = domain["system_prompt"]
    ledger_path = domain.get("ledger", "")
    ledger_note = ""
    if ledger_path:
        ledger_note = f"\n\nYour assigned memory ledger: {ledger_path}\n"
    if agent_key in ("DOC", "CONF"):
        return base + ledger_note + LEDGER_MEMORY_RULE  # Skip mesh, keep ledgers
    return base + ledger_note + MESH_AGENT_SYSTEM_EXTENSION + LEDGER_MEMORY_RULE


def execute_task(task: Task, user_prompt: str, director_output: str,
                 all_results: dict, file_context: str, gdd_context: str) -> str:
    """Execute a single task by calling the appropriate agent."""
    agent_key = resolve_agent_name(task.agent)
    domain = ALL_DOMAINS.get(agent_key)

    if not domain:
        return f"[ERROR] Unknown agent: {task.agent}"

    preferred_model = domain.get("model", EXECUTION_MODEL)
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

    # Include parent context if this is a sub-task
    if task.parent and task.parent in all_results:
        context_parts.append(f"## Parent Task Context\n{all_results[task.parent]}")

    # Include previous iteration output for self-correction
    if task.iteration > 0 and task.output:
        context_parts.append(f"## Your Previous Output (iteration {task.iteration})\n{task.output}")
        if task.iteration >= MAX_ITERATIONS - 1:
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

    output = call_ollama(system, user_message, label, preferred_model)

    task.output = output
    task.signals = extract_signals(output)
    task.double_check = extract_double_check(output)
    task.completed = True

    return output


def run_mesh_pipeline(user_prompt: str, checkpoint_id: str = None) -> str:
    """Main mesh consensus pipeline orchestrator."""
    output_parts = []
    all_results = {}  # task_id -> output text
    all_approvals = {}  # agent_key -> bool
    all_vetos = []  # list of veto signals
    all_objects = []  # list of object signals
    consensus_iteration = 0
    snapshot = None

    # ── Phase 0: Setup ────────────────────────────────────────────────────
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    feature_slug = re.sub(r'[^a-z0-9]+', '_', user_prompt.lower())[:40].strip('_')
    run_id = f"{feature_slug}_{timestamp}"

    print(f"\n{'='*70}")
    print(f"  Midway Mesh Pipeline — Run: {run_id}")
    print(f"{'='*70}")

    output_parts.append(f"# Midway Mesh Pipeline — {run_id}\n")
    output_parts.append(f"**Request:** {user_prompt}\n")
    output_parts.append(f"**Started:** {datetime.now().isoformat()}\n---\n")

    # Initialize snapshot if available
    if HAS_SNAPSHOT:
        try:
            snapshot = SnapshotManager(run_id, user_prompt)
            output_parts.append(f"📸 Snapshot tracking enabled: {run_id}\n")
        except Exception as e:
            print(f"  [Snapshot] Init failed: {e}")
            snapshot = None

    # ── Phase 0.5: Lead Producer (Scope Gate & Auto-Feeder) ───────────────
    blueprint_path = PROJECT_ROOT / "docs" / "project_blueprint.md"
    
    if not user_prompt:
        if blueprint_path.is_file():
            content = blueprint_path.read_text(encoding="utf-8")
            match = re.search(r"- \\[ \\] (Task \\d+: .+)", content)
            if match:
                user_prompt = match.group(1)
                print(f"  [Lead Producer] Auto-feeding next task: {user_prompt}")
                new_content = content.replace(f"- [ ] {user_prompt}", f"- [x] {user_prompt}", 1)
                blueprint_path.write_text(new_content, encoding="utf-8")
            else:
                print("  [Lead Producer] Blueprint complete. Nothing to do.")
                return "Blueprint complete."
        else:
            print("  [ERROR] No prompt provided and no blueprint found.")
            return "Failed to start."
    else:
        scope_prompt = f"Analyze this prompt: '{user_prompt}'. If it requires modifying >{SCOPE_FILE_LIMIT} files or writing >{SCOPE_LINE_LIMIT} lines, respond strictly with 'TOO_BROAD'. Otherwise respond 'NARROW'."
        scope_eval = call_ollama("You are a Lead Producer.", scope_prompt, "Scope Gate", REASONING_MODEL)
        
        if "TOO_BROAD" in scope_eval.upper():
            print(f"\n  [Lead Producer] Scope is TOO BROAD. Generating blueprint...")
            blueprint_prompt = f"Create a step-by-step markdown blueprint to accomplish: {user_prompt}. Format as a checklist: '- [ ] Task 1: ...'"
            blueprint = call_ollama("You are a Lead Producer.", blueprint_prompt, "Blueprint Generation", REASONING_MODEL)
            blueprint_path.parent.mkdir(exist_ok=True)
            blueprint_path.write_text(blueprint, encoding="utf-8")
            print(f"  [Lead Producer] Saved to docs/project_blueprint.md. Exiting. Run pipeline with no prompt to execute Task 1.")
            import sys
            sys.exit(0)

    # ── Phase 1: GDD Librarian ────────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  Phase 1: GDD Librarian")
    print(f"{'='*70}")
    output_parts.append("\n## Phase 1: GDD Librarian\n")

    gdd_context = recursive_librarian(user_prompt)
    if gdd_context:
        output_parts.append(gdd_context + "\n")
    else:
        output_parts.append("No relevant GDD sections found.\n")

    # ── Phase 2: Project State & File Context ─────────────────────────────
    print(f"\n{'='*70}")
    print(f"  Phase 2: Project Context")
    print(f"{'='*70}")
    output_parts.append("\n## Phase 2: Project Context\n")

    project_state = get_project_state()
    output_parts.append(project_state + "\n")

    structure = curate_project_structure(user_prompt)
    output_parts.append(structure + "\n")

    # ── Phase 3: Director ─────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  Phase 3: Director — Task Decomposition")
    print(f"{'='*70}")
    output_parts.append("\n## Phase 3: Director — Task Decomposition\n")

    director_prompt = build_director_prompt()
    director_input = f"{director_prompt}\n\n---\nUSER REQUEST:\n{user_prompt}"
    director_output = call_ollama(DIRECTOR_SYSTEM, director_input, "Director", REASONING_MODEL)
    output_parts.append(director_output + "\n")

    # Parse tasks from Director output
    task_regex = r"### Task (\d+): \[([^\]]+)\] — (.+)"
    tasks = []
    for match in re.finditer(task_regex, director_output):
        tasks.append({
            "id": match.group(1),
            "domain": match.group(2).strip(),
            "title": match.group(3).strip(),
        })

    if not tasks:
        # Fallback: create a single default task
        tasks.append({"id": "1", "domain": "C++", "title": "Full Implementation"})
        print(f"  [Director] No tasks parsed, created default task")

    print(f"  [Director] Created {len(tasks)} task(s)")

    # ── Phase 4: Mesh Execution ───────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  Phase 4: Mesh Execution — {len(tasks)} Task(s)")
    print(f"{'='*70}")
    output_parts.append(f"\n## Phase 4: Mesh Execution ({len(tasks)} tasks)\n")

    # Build the work queue
    work_queue = deque()
    task_map = {}  # task_id -> Task object

    for t in tasks:
        task_obj = Task(
            agent=t["domain"],
            spec=t["title"],
            task_id=f"task_{t['id']}",
            parent=None,
        )
        work_queue.append(task_obj)
        task_map[task_obj.task_id] = task_obj

    # Process work queue (depth-first)
    processed_ids = set()
    query_results = {}  # query_id -> answer
    pending_fetches = {}  # query_task_id -> original Task awaiting DOC oracle answer

    while work_queue:
        task = work_queue.popleft()

        if task.task_id in processed_ids and not task.is_query:
            continue

        # Check for query results to inject
        context_extra = ""
        if task.is_query:
            # This is a query being answered — just execute it
            pass
        elif task.parent and task.parent in query_results:
            context_extra = f"## Answer from Query\n{query_results[task.parent]}"

        task.context = context_extra

        # Find relevant files for this task (ledger-aware: agent sees own TOC first)
        file_context = ""
        try:
            files = find_relevant_files(task.spec, task.agent)
            file_context = format_file_context(files, domain_key=task.agent)
        except Exception as e:
            print(f"  [FileReader] Error: {e}")

        # Execute the task
        output = execute_task(
            task, user_prompt, director_output,
            all_results, file_context, gdd_context,
        )

        all_results[task.task_id] = output
        processed_ids.add(task.task_id)

        if task.is_query:
            query_results[task.parent] = output
            print(f"  [Query Tracker] Saved answer for parent task {task.task_id}")
            
            # If this was a DOC FETCH resolution, re-queue the original agent
            if task.task_id in pending_fetches:
                original_task = pending_fetches.pop(task.task_id)
                # Inject the DOC oracle's response as recalled memory
                original_task.context = (original_task.context or "") + "\n" + output
                original_task._fetch_depth = getattr(task, '_fetch_depth', 0)
                original_task.completed = False
                # Re-queue the original task WITHOUT incrementing iteration count
                work_queue.appendleft(original_task)
                print(f"  [FETCH] Injected DOC oracle response into {original_task.agent}, re-queued")

        # Process signals from this task
        for signal in task.signals:
            stype = signal["type"]

            if stype == "QUERY":
                # Route query to target agent
                target = resolve_agent_name(signal["target"])
                query_task = Task(
                    agent=target,
                    spec=signal["content"],
                    task_id=f"query_{task.task_id}_{len(query_results)}",
                    parent=task.task_id,
                    is_query=True,
                )
                work_queue.appendleft(query_task)  # Priority: process before next task
                print(f"  [Signal] QUERY: {task.agent} -> {target}: {signal['content'][:60]}...")

            elif stype == "DELEGATE":
                # Create sub-task
                target = resolve_agent_name(signal["target"])
                sub_count = sum(1 for t in task_map.values()
                                if t.parent == task.task_id)
                if sub_count < MAX_SUBTASKS_PER_AGENT:
                    sub_task = Task(
                        agent=target,
                        spec=signal["content"],
                        task_id=f"sub_{task.task_id}_{sub_count + 1}",
                        parent=task.task_id,
                    )
                    work_queue.append(sub_task)
                    task_map[sub_task.task_id] = sub_task
                    print(f"  [Signal] DELEGATE: {task.agent} -> {target}: {signal['content'][:60]}...")
                else:
                    print(f"  [Signal] DELEGATE SKIPPED: {task.agent} already has {sub_count} sub-tasks (max {MAX_SUBTASKS_PER_AGENT})")

            elif stype == "VETO":
                all_vetos.append({
                    "from": task.agent,
                    "target": signal["target"],
                    "reason": signal["content"],
                    "task_id": task.task_id,
                })
                print(f"  [Signal] VETO: {task.agent} -> {signal['target']}: {signal['content'][:80]}...")

            elif stype == "OBJECT":
                all_objects.append({
                    "from": task.agent,
                    "target": signal["target"],
                    "concern": signal["content"],
                    "task_id": task.task_id,
                })
                print(f"  [Signal] OBJECT: {task.agent} -> {signal['target']}: {signal['content'][:80]}...")

            elif stype == "APPROVE":
                all_approvals[task.agent] = True
                print(f"  [Signal] APPROVE: {task.agent}")

            elif stype == "REVISE":
                target = resolve_agent_name(signal["target"])
                # Re-queue the target task for revision
                revise_task = Task(
                    agent=target,
                    spec=f"Revision requested by {task.agent}: {signal['content']}",
                    task_id=f"revise_{target}_{consensus_iteration}",
                    parent=task.task_id,
                    iteration=0,
                )
                work_queue.append(revise_task)
                print(f"  [Signal] REVISE: {task.agent} -> {target}: {signal['content'][:60]}...")

            elif stype == "RECOURSE":
                print(f"  [Signal] RECOURSE: {task.agent} -> Director: {signal['content'][:80]}...")
                # Director will handle this in the consensus phase

            elif stype == "CONSULT":
                target = resolve_agent_name(signal["target"])
                consult_task = Task(
                    agent=target,
                    spec=f"Consultation requested by {task.agent}: {signal['content']}",
                    task_id=f"consult_{task.task_id}_{len(query_results)}",
                    parent=task.task_id,
                    is_query=True,
                )
                work_queue.append(consult_task)
                print(f"  [Signal] CONSULT: {task.agent} -> {target}: {signal['content'][:60]}...")

            elif stype == "FETCH":
                # Intercept [FETCH] signal: route through DOC oracle for reasoning.
                # DOC evaluates whether the requested header is optimal for the
                # requesting agent's current task, cross-references ledgers, and
                # resolves fuzzy matches. Then inject back WITHOUT iteration cost.
                
                # Track fetch depth to prevent infinite recursive loops
                fetch_depth = getattr(task, '_fetch_depth', 0)
                if fetch_depth >= 3:
                    max_depth_msg = (
                        "\n## Recalled Memory\n"
                        "**Source:** orchestrator\n"
                        "**Oracle note:** [FETCH ERROR: max recursion depth (3) reached. "
                        "Agent must synthesize from available context.]\n\n"
                    )
                    task.context = (task.context or "") + "\n" + max_depth_msg
                    task.completed = False
                    work_queue.appendleft(task)
                    print(f"  [FETCH] Max depth (3) reached for {task.agent}, returning error")
                    continue
                
                # Build the DOC oracle query: what memory does the agent need?
                fetch_target = signal.get("content", "")
                doc_query_spec = (
                    f"Memory Oracle: Resolve FETCH request\n"
                    f"Requesting agent: {task.agent}\n"
                    f"Their current task: {task.spec[:300]}\n"
                    f"Their fetch target: {fetch_target}\n"
                    f"---\n"
                    f"As Memory Oracle, evaluate if this fetch target is correct, "
                    f"cross-reference available ledgers, resolve the best section, "
                    f"and return the content formatted as ## Recalled Memory."
                )
                
                # Queue DOC to resolve the FETCH (NOT the original task yet)
                doc_fetch_task = Task(
                    agent="DOC",
                    spec=doc_query_spec,
                    task_id=f"doc_fetch_{task.task_id}_{fetch_depth}",
                    parent=task.task_id,
                    is_query=True,
                )
                doc_fetch_task._fetch_depth = fetch_depth + 1
                
                # Store original task — will be re-queued AFTER DOC answers
                pending_fetches[doc_fetch_task.task_id] = task
                
                work_queue.appendleft(doc_fetch_task)
                print(f"  [FETCH] Routed to DOC oracle (depth {fetch_depth+1}/3): {fetch_target[:80]}...")

        # Check double-check for unresolved items
        if task.double_check and task.double_check["unresolved"]:
            unresolved = task.double_check["unresolved"].strip()
            if unresolved and unresolved.lower() not in ("none", "n/a", "nothing", ""):
                if task.iteration < MAX_ITERATIONS:
                    task.iteration += 1
                    task.completed = False
                    work_queue.appendleft(task)
                    print(f"  [Double-Check] {task.agent} has unresolved items, re-queuing (iter {task.iteration})")

        # Save snapshot after each task
        if snapshot:
            try:
                snapshot.save_proposal(
                    ALL_DOMAINS.get(resolve_agent_name(task.agent), {}).get("name", task.agent),
                    len(processed_ids),
                    f"output_{task.task_id}.md",
                    output,
                )
            except Exception as e:
                print(f"  [Snapshot] Save error: {e}")

    # ── Phase 5: Conflict Resolution ──────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  Phase 5: Conflict Resolution")
    print(f"{'='*70}")
    output_parts.append("\n## Phase 5: Conflict Resolution\n")

    conflict_resolutions = []
    for veto in all_vetos:
        target = resolve_agent_name(veto["target"])
        from_agent = resolve_agent_name(veto["from"])

        conflict_prompt = (
            f"## VETO Signal\n"
            f"**From:** {ALL_DOMAINS.get(from_agent, {}).get('name', from_agent)}\n"
            f"**Target:** {ALL_DOMAINS.get(target, {}).get('name', target)}\n"
            f"**Reason:** {veto['reason']}\n\n"
            f"## Original Feature Request\n{user_prompt}\n\n"
            f"## Director's Task Breakdown\n{director_output}\n\n"
        )

        # Include relevant outputs
        if veto["task_id"] in all_results:
            conflict_prompt += f"## Output that triggered VETO\n{all_results[veto['task_id']]}\n\n"

        conflict_output = call_ollama(
            ALL_DOMAINS["CONF"]["system_prompt"],
            conflict_prompt,
            f"Conflict Resolution: {veto['from']} vs {veto['target']}",
        )
        conflict_resolutions.append(conflict_output)
        output_parts.append(f"### VETO: {veto['from']} -> {veto['target']}\n{conflict_output}\n")

    for obj in all_objects:
        target = resolve_agent_name(obj["target"])
        from_agent = resolve_agent_name(obj["from"])

        object_prompt = (
            f"## OBJECT Signal\n"
            f"**From:** {ALL_DOMAINS.get(from_agent, {}).get('name', from_agent)}\n"
            f"**Target:** {ALL_DOMAINS.get(target, {}).get('name', target)}\n"
            f"**Concern:** {obj['concern']}\n\n"
            f"## Original Feature Request\n{user_prompt}\n\n"
        )

        if obj["task_id"] in all_results:
            object_prompt += f"## Output that triggered OBJECT\n{all_results[obj['task_id']]}\n\n"

        object_output = call_ollama(
            ALL_DOMAINS["CONF"]["system_prompt"],
            object_prompt,
            f"Conflict Resolution: {obj['from']} OBJECTS {obj['target']}",
        )
        conflict_resolutions.append(object_output)
        output_parts.append(f"### OBJECT: {obj['from']} -> {obj['target']}\n{object_output}\n")

    # ── Phase 6: Integration Review & Fix Loop ────────────────────────────
    print(f"\n{'='*70}")
    print(f"  Phase 6: Integration Review & Fix Loop")
    print(f"{'='*70}")
    output_parts.append("\n## Phase 6: Integration Review & Fix Loop\n")

    all_code_str = "\n\n".join([f"### {tid}\n{output}" for tid, output in all_results.items()])
    
    print("  [Pre-Flight] Running background compilers...")
    pre_flight_errors = ""
    try:
        make_process = subprocess.run(["make", "-j4"], capture_output=True, text=True, cwd=PROJECT_ROOT)
        if make_process.returncode != 0:
            err_tail = "\n".join(make_process.stderr.splitlines()[-50:])
            pre_flight_errors += f"\n## C++ Compiler Errors:\n```\n{err_tail}\n```"
    except Exception as e:
        pass

    for lf in PROJECT_ROOT.rglob("*.lua"):
        try:
            lua_proc = subprocess.run(["luac", "-p", str(lf)], capture_output=True, text=True)
            if lua_proc.returncode != 0:
                pre_flight_errors += f"\n## Lua Syntax Error in {lf.name}:\n```\n{lua_proc.stderr}\n```"
        except Exception:
            pass

    if pre_flight_errors:
        print("  [Pre-Flight] Syntax errors detected. Forcing Architect Fix Cycle.")
        fix_input = f"Your generated code failed local compilation/syntax checks. Fix the following errors:\n{pre_flight_errors}\n\nPreviously generated code:\n{all_code_str}"
        all_code_str = call_ollama(ARCHITECT_FIX_SYSTEM, fix_input, "Architect Syntax Fix", EXECUTION_MODEL)
        
        # Override all_results with the fixed code so Reviewer sees it
        for tid in all_results:
            all_results[tid] = all_code_str

    all_code = "\n\n".join([
        f"### {tid}\n{output}"
        for tid, output in all_results.items()
    ])

    review_output = ""
    review_verdict = "UNKNOWN"
    review_cycle = 0

    while review_cycle < REVIEW_MAX_ITERATIONS:
        review_cycle += 1
        print(f"\n  [Review-Fix] Cycle {review_cycle}/{REVIEW_MAX_ITERATIONS}")

        cycle_label = f"Integration Review (cycle {review_cycle})"

        review_input = (
            f"## Original Feature Request\n{user_prompt}\n\n"
            f"## Director's Task Breakdown\n{director_output}\n\n"
            f"## All Generated Code\n{all_code}\n\n"
            f"{REVIEW_PROMPT}"
        )

        review_output = call_ollama(REVIEW_SYSTEM, review_input, cycle_label)
        review_verdict = get_verdict(review_output)
        output_parts.append(f"### Review Cycle {review_cycle}\n{review_output}\n")

        print(f"  [Review-Fix] Verdict: {review_verdict}")

        if review_verdict == "PASS":
            print(f"  [Review-Fix] Passed on cycle {review_cycle}")
            break

        if review_verdict == "FAIL" and review_cycle < REVIEW_MAX_ITERATIONS:
            # Extract issues from review to give the architect context
            issues_match = re.search(
                r"### Issues\s*\n(.*?)(?=###|\Z)",
                review_output, re.DOTALL,
            )
            issues_text = issues_match.group(1).strip() if issues_match else review_output[:1000]

            # Call each domain architect to fix their code
            print(f"  [Review-Fix] Review failed — architect fixing...")
            fix_input = (
                f"## Original Feature Request\n{user_prompt}\n\n"
                f"## Review Issues (Cycle {review_cycle})\n{issues_text}\n\n"
                f"## Previously Generated Code\n{all_code}\n\n"
                f"Fix ALL issues listed above. Produce corrected code for all domains. "
                f"Address every issue the Reviewer raised. "
                f"If you believe an issue is a false positive, explain why."
            )
            fix_output = call_ollama(ARCHITECT_FIX_SYSTEM, fix_input,
                                     f"Architect Fix (cycle {review_cycle})")
            output_parts.append(f"### Architect Fix Cycle {review_cycle}\n{fix_output}\n")

            # Update all_code with the fixed version
            all_code = fix_output
            continue

        # If FAIL but we're out of cycles, break and report
        break

    # ── Phase 7: Consensus Gate ───────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  Phase 7: Consensus Gate")
    print(f"{'='*70}")
    output_parts.append("\n## Phase 7: Consensus Gate\n")

    review_passed = (review_verdict == "PASS")
    has_active_vetos = len(all_vetos) > 0
    has_recourses = any(s["type"] == "RECOURSE" for task_list in
                        [t.signals for t in task_map.values()] for s in task_list)

    consensus_checks = {
        "All tasks executed": len(processed_ids) >= len(tasks),
        "All sub-trees resolved": True,  # Work queue is empty
        "Double-check passed": all(
            not (t.double_check and t.double_check["unresolved"].strip().lower() not in ("none", "n/a", "", "nothing"))
            for t in task_map.values() if t.completed and t.double_check
        ),
        "No active VETOs": not has_active_vetos,
        "Review passed": review_passed,
        "No RECOURSE pending": not has_recourses,
    }

    all_checks_pass = all(consensus_checks.values())

    output_parts.append("### Consensus Checks\n")
    for check, passed in consensus_checks.items():
        status = "✅" if passed else "❌"
        output_parts.append(f"- {status} {check}\n")

    # ── Phase 8: Final Approval or Failure Report ─────────────────────────
    if all_checks_pass:
        print(f"\n{'='*70}")
        print(f"  Phase 8: Final Approval")
        print(f"{'='*70}")
        output_parts.append("\n## Phase 8: Final Approval\n")

        final_input = (
            f"## Original Feature Request\n{user_prompt}\n\n"
            f"## Your Task Breakdown\n{director_output}\n\n"
            f"## Generated Code\n{all_code}\n\n"
            f"## Integration Review\n{review_output}\n\n"
            f"Review the complete output. "
            f"State **APPROVED** if everything is satisfactory, "
            f"or **REVISION REQUIRED** with specific changes needed."
        )

        final_output = call_ollama(FINAL_APPROVAL_SYSTEM, final_input, "Director (Final Approval)")
        output_parts.append(final_output + "\n")

        output_parts.append("\n---\n## ✅ Pipeline Complete\n")
        output_parts.append(f"**Tasks executed:** {len(processed_ids)}\n")
        output_parts.append(f"**Review reviews:** {review_cycle} cycle(s)\n")
        output_parts.append(f"**Review verdict:** {'PASS' if review_passed else 'FAIL'}\n")
        output_parts.append(f"**Status:** APPROVED\n")

    else:
        print(f"\n{'='*70}")
        print(f"  Phase 8: Failure Report")
        print(f"{'='*70}")
        output_parts.append("\n## Phase 8: Failure Report\n")

        failure_report = generate_failure_report(
            user_prompt, consensus_checks, all_vetos, all_objects,
            all_results, task_map, director_output,
        )
        output_parts.append(failure_report + "\n")

        # ── Phase 8b: Lead Producer Scope Post-Mortem ──────────────────────
        print(f"\n{'='*70}")
        print(f"  Phase 8b: Lead Producer — Scope Post-Mortem")
        print(f"{'='*70}")
        output_parts.append("\n## Phase 8b: Lead Producer Scope Post-Mortem\n")

        post_mortem_prompt = (
            f"## Original User Prompt\n{user_prompt}\n\n"
            f"## Director's Task Breakdown\n{director_output}\n\n"
            f"## Failure Report\n{failure_report}\n\n"
            f"Analyze the failure above. Determine:\n"
            f"1. **TOO_BROAD** — was the original prompt too wide for sub-agents "
            f"(requiring >{SCOPE_FILE_LIMIT} files or >{SCOPE_LINE_LIMIT} lines across multiple domains)?\n"
            f"2. **NARROW** — scope was fine, failure was technical "
            f"(model misinterpretation, real code bug, Ollama issue)?\n\n"
            f"If TOO_BROAD, suggest a narrower version of the prompt the user "
            f"could run instead.\n"
            f"If NARROW, recommend the user re-run with more specific constraints "
            f"or check Ollama status.\n\n"
            f"Start with **TOO_BROAD** or **NARROW** on its own line, "
            f"then explain your reasoning."
        )
        post_mortem_output = call_ollama(
            DIRECTOR_SYSTEM,
            post_mortem_prompt,
            "Lead Producer (Scope Post-Mortem)",
            REASONING_MODEL,
        )
        output_parts.append(post_mortem_output + "\n")

    # ── Blueprint Step Chaining ─────────────────────────────────────────────
    if all_checks_pass:
        blueprint_path = PROJECT_ROOT / "docs" / "project_blueprint.md"
        if blueprint_path.is_file():
            try:
                bp_content = blueprint_path.read_text(encoding="utf-8")
                next_match = re.search(r"- \\[ \\] (Task \\d+: .+)", bp_content)
                if next_match:
                    next_step = next_match.group(1)
                    output_parts.append(
                        f"\n### Next Blueprint Step\n"
                        f"**{next_step}** — run with:\n"
                        f"`python pipeline.py \"{next_step}\"`\n"
                    )
                else:
                    output_parts.append("\n### Blueprint Complete ✅\n"
                                        "All tasks in the blueprint have been executed.\n")
            except Exception:
                pass

    # ── Save output ───────────────────────────────────────────────────────
    final_output = "\n".join(output_parts)
    output_path = PROJECT_ROOT / f"pipeline_output_{run_id}.md"
    try:
        output_path.write_text(final_output, encoding="utf-8")
        print(f"\n  Output saved to: {output_path}")
    except Exception as e:
        print(f"\n  Could not save output: {e}")

    # Save snapshot final
    if snapshot:
        try:
            snapshot.apply_proposals()
            print(f"  [Snapshot] Applied proposals")
        except Exception as e:
            print(f"  [Snapshot] Apply error: {e}")

    print(f"\n{'='*70}")
    print(f"  Pipeline Complete — {'APPROVED' if all_checks_pass else 'FAILED'}")
    print(f"{'='*70}")

    return final_output


def generate_failure_report(user_prompt: str, consensus_checks: dict,
                            vetos: list, objects: list,
                            all_results: dict, task_map: dict,
                            director_output: str) -> str:
    """Generate a curated failure report with suggested manual breakdown."""
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
            from_name = ALL_DOMAINS.get(resolve_agent_name(v["from"]), {}).get("name", v["from"])
            target_name = ALL_DOMAINS.get(resolve_agent_name(v["target"]), {}).get("name", v["target"])
            parts.append(f"1. **{from_name}** VETO'd **{target_name}**\n")
            parts.append(f"   - Reason: {v['reason']}\n")
            if v["task_id"] in all_results:
                # Extract first 200 chars of the offending output
                offending = all_results[v["task_id"]][:200]
                parts.append(f"   - Offending output: {offending}...\n")
            parts.append("")

    # Blocking OBJECTs
    if objects:
        parts.append("### Unresolved OBJECTions\n")
        for o in objects:
            from_name = ALL_DOMAINS.get(resolve_agent_name(o["from"]), {}).get("name", o["from"])
            target_name = ALL_DOMAINS.get(resolve_agent_name(o["target"]), {}).get("name", o["target"])
            parts.append(f"1. **{from_name}** OBJECTed to **{target_name}**\n")
            parts.append(f"   - Concern: {o['concern']}\n")
            parts.append("")

    # Suggested manual decomposition
    parts.append("### Suggested Manual Decomposition\n")
    parts.append("To resolve this manually, break into these sub-tasks:\n")

    # Generate suggested /arch_* commands based on failed domains
    suggested_commands = []
    for v in vetos:
        target = resolve_agent_name(v["target"])
        domain = ALL_DOMAINS.get(target, {})
        tag = domain.get("tag", f"[{target}]")
        name = domain.get("name", target)
        suggested_commands.append(
            f"1. `/arch_{target.lower()}` \"{name}: {v['reason']}\""
        )
    for o in objects:
        target = resolve_agent_name(o["target"])
        domain = ALL_DOMAINS.get(target, {})
        tag = domain.get("tag", f"[{target}]")
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


# ── Mesh Work Queue API ─────────────────────────────────────────────────────
# These are the exports expected by pipeline_server.py for the REST API.

_MESH_TASK_REGISTRY = {}        # task_id -> task metadata
_MESH_RESULTS = {}              # task_id -> final output
_MESH_WORK_QUEUE = deque()      # pending tasks
_MESH_REGISTRY_LOCK = False     # simple flag-based coarsening to avoid race

def submit_mesh_task(task_type: str, payload: dict, priority: int = 0) -> str:
    """Submit a new mesh task to the global queue. Returns a task_id."""
    global _MESH_WORK_QUEUE
    task_id = f"mesh_{datetime.now().strftime('%H%M%S%f')}_{hash(str(payload)) % 10000}"
    _MESH_TASK_REGISTRY[task_id] = {
        "task_id": task_id,
        "task_type": task_type,
        "payload": payload,
        "priority": priority,
        "status": "queued",
        "created": datetime.now().isoformat(),
        "completed": None,
        "error": None,
    }
    _MESH_WORK_QUEUE.append(task_id)
    # Re-sort by priority: higher priority first
    sorted_list = sorted(
        list(_MESH_WORK_QUEUE),
        key=lambda tid: _MESH_TASK_REGISTRY.get(tid, {}).get("priority", 0),
        reverse=True,
    )
    _MESH_WORK_QUEUE.clear()
    _MESH_WORK_QUEUE.extend(sorted_list)
    print(f"  [MeshQueue] Submitted {task_id} (type={task_type}, priority={priority})")
    return task_id


def get_mesh_task_status(task_id: str) -> dict:
    """Get the status of a submitted mesh task."""
    return _MESH_TASK_REGISTRY.get(task_id)


def list_mesh_tasks() -> list:
    """List all mesh tasks in the registry."""
    return list(_MESH_TASK_REGISTRY.values())


def cancel_mesh_task(task_id: str) -> bool:
    """Cancel a queued mesh task. Returns True if cancelled."""
    global _MESH_WORK_QUEUE
    if task_id in _MESH_TASK_REGISTRY:
        entry = _MESH_TASK_REGISTRY[task_id]
        if entry["status"] in ("queued",):
            entry["status"] = "cancelled"
            entry["completed"] = datetime.now().isoformat()
            # Remove from work queue if present
            if task_id in _MESH_WORK_QUEUE:
                temp = [t for t in _MESH_WORK_QUEUE if t != task_id]
                _MESH_WORK_QUEUE.clear()
                _MESH_WORK_QUEUE.extend(temp)
            print(f"  [MeshQueue] Cancelled {task_id}")
            return True
    return False


def get_mesh_work_queue() -> list:
    """Get the current work queue as a list of task IDs with metadata."""
    return [
        {"task_id": tid, **{k: v for k, v in _MESH_TASK_REGISTRY.get(tid, {}).items() if k != "payload"}}
        for tid in _MESH_WORK_QUEUE
    ]


def get_mesh_results() -> list:
    """Get all completed mesh results."""
    return [
        {"task_id": tid, "output": output, "completed": _MESH_TASK_REGISTRY.get(tid, {}).get("completed")}
        for tid, output in _MESH_RESULTS.items()
    ]


# ── Signal Type Definitions ─────────────────────────────────────────────────

from enum import Enum

class SignalType(Enum):
    """Enum of all mesh communication signal types."""
    QUERY = "QUERY"
    DELEGATE = "DELEGATE"
    RESULT = "RESULT"
    APPROVE = "APPROVE"
    REVISE = "REVISE"
    VETO = "VETO"
    OBJECT = "OBJECT"
    RECOURSE = "RECOURSE"
    CONSULT = "CONSULT"


class MeshSignal:
    """A parsed mesh signal with typed fields."""
    def __init__(self, signal_type: SignalType, target: str = None,
                 content: str = None, source: str = None):
        self.type = signal_type
        self.target = target
        self.content = content
        self.source = source

    def to_dict(self) -> dict:
        return {
            "type": self.type.value,
            "target": self.target,
            "content": self.content,
            "source": self.source,
        }

    def __repr__(self):
        return f"MeshSignal({self.type.value}, target={self.target})"


def parse_signal(text: str, source: str = None) -> list:
    """Parse a text for mesh signals, returning MeshSignal objects.

    This is the structured export version of extract_signals().
    Used by pipeline_server.py's /mesh/detect-edits and /mesh/resolve-conflict endpoints.
    """
    signals = extract_signals(text)
    return [
        MeshSignal(
            signal_type=SignalType(s["type"]),
            target=s.get("target"),
            content=s.get("content"),
            source=source,
        )
        for s in signals
    ]


# ── Cross-Agent Edit Detection ──────────────────────────────────────────────

class ConsensusResult:
    """Result of a conflict resolution or consensus check."""
    def __init__(self, verdict: str, merged_code: str = "",
                 explanation: str = "", warnings: list = None):
        self.verdict = verdict          # "SUSTAIN" | "OVERRULE" | "COMPROMISE"
        self.merged_code = merged_code
        self.explanation = explanation
        self.warnings = warnings or []

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "merged_code": self.merged_code,
            "explanation": self.explanation,
            "warnings": self.warnings,
        }

    def __repr__(self):
        return f"ConsensusResult({self.verdict})"


def detect_cross_agent_edits(agent_a_code: str, agent_b_code: str) -> list:
    """Detect conflicting edits between two agents' code outputs.

    Returns a list of conflict dicts with file, line ranges, and descriptions.
    Uses simple line-by-line diff. In production, would use `difflib`.
    """
    import difflib
    conflicts = []

    a_lines = agent_a_code.splitlines()
    b_lines = agent_b_code.splitlines()

    # Use difflib to find differing regions
    differ = difflib.Differ()
    diff = list(differ.compare(a_lines, b_lines))

    conflict_start = None
    conflict_lines_a = []
    conflict_lines_b = []

    for i, line in enumerate(diff):
        if line.startswith("- ") or line.startswith("+ "):
            if conflict_start is None:
                conflict_start = i
            if line.startswith("- "):
                conflict_lines_a.append((i, line[2:]))
            else:
                conflict_lines_b.append((i, line[2:]))
        else:
            if conflict_start is not None:
                # We ended a diff region — check if both sides modified
                if conflict_lines_a and conflict_lines_b:
                    conflicts.append({
                        "region_start": conflict_start,
                        "region_end": i,
                        "agent_a_lines": [
                            {"line": ln, "content": ct}
                            for ln, ct in conflict_lines_a
                        ],
                        "agent_b_lines": [
                            {"line": ln, "content": ct}
                            for ln, ct in conflict_lines_b
                        ],
                        "description": f"Conflict at lines {conflict_lines_a[0][0]}-{conflict_lines_a[-1][0]} (A) vs {conflict_lines_b[0][0]}-{conflict_lines_b[-1][0]} (B)",
                    })
                conflict_start = None
                conflict_lines_a = []
                conflict_lines_b = []

    # Handle final region
    if conflict_lines_a and conflict_lines_b:
        conflicts.append({
            "region_start": conflict_start,
            "region_end": len(diff),
            "agent_a_lines": [
                {"line": ln, "content": ct}
                for ln, ct in conflict_lines_a
            ],
            "agent_b_lines": [
                {"line": ln, "content": ct}
                for ln, ct in conflict_lines_b
            ],
            "description": f"Final conflict at lines {conflict_lines_a[0][0]}-{conflict_lines_a[-1][0]} (A) vs {conflict_lines_b[0][0]}-{conflict_lines_b[-1][0]} (B)",
        })

    return conflicts


def resolve_conflict(agent_a_code: str, agent_b_code: str,
                     veto_justification: str,
                     feature_request: str) -> ConsensusResult:
    """Resolve a conflict between two agents' code using the CONF agent.

    Returns a ConsensusResult with verdict and merged code.
    """
    prompt = (
        f"## Original Feature Request\n{feature_request}\n\n"
        f"## VETO Justification\n{veto_justification}\n\n"
        f"## Agent A Code\n{agent_a_code}\n\n"
        f"## Agent B Code\n{agent_b_code}\n\n"
        f"Resolve this conflict. Output one of:\n"
        f"- **SUSTAIN** VETO: Agent B's original is more correct\n"
        f"- **OVERRULE** VETO: Agent A's change is technically correct\n"
        f"- **COMPROMISE** with a merged version\n\n"
        f"Then output the final merged code under ## Merged Code."
    )

    result_text = call_ollama(
        ALL_DOMAINS["CONF"]["system_prompt"],
        prompt,
        "Conflict Resolution (API)",
    )

    # Parse verdict
    verdict = "COMPROMISE"  # default
    if "SUSTAIN" in result_text.upper():
        verdict = "SUSTAIN"
    elif "OVERRULE" in result_text.upper():
        verdict = "OVERRULE"

    # Extract merged code block if present
    merged_code = ""
    code_match = re.search(
        r"## Merged Code\s*\n```(?:\w+)?\s*\n(.*?)```",
        result_text, re.DOTALL,
    )
    if code_match:
        merged_code = code_match.group(1).strip()

    return ConsensusResult(
        verdict=verdict,
        merged_code=merged_code or result_text,
        explanation=result_text[:500],  # first 500 chars as summary
    )


# ── Overload: generate_failure_report for REST API ──────────────────────────
# pipeline_server.py calls generate_failure_report(task_id, error_details)
# while the internal pipeline uses generate_failure_report(...) with 7 args.
# We add an overload wrapper that accepts the 2-arg REST signature.

def _generate_failure_report_rest(task_id: str, error_details: str) -> str:
    """Generate a failure report from the REST API (2-arg signature)."""
    task_data = _MESH_TASK_REGISTRY.get(task_id, {})
    return (
        f"## Pipeline Failure Report (Mesh API)\n\n"
        f"**Task ID:** {task_id}\n"
        f"**Task Type:** {task_data.get('task_type', 'unknown')}\n"
        f"**Error Details:** {error_details}\n\n"
        f"### Suggested Action\n"
        f"1. Check Ollama is running at {OLLAMA_HOST}\n"
        f"2. Verify the model '{EXECUTION_MODEL}' is pulled\n"
        f"3. Re-run with more specific constraints\n"
        f"4. Check docs/ for relevant API references\n\n"
        f"### Cross-Reference\n"
        f"- docs/rules_cpp.md\n"
        f"- docs/rules_lua.md\n"
        f"- docs/rules_phys.md\n"
        f"- docs/rules_shader.md\n"
        f"- docs/engine_lua_bridge_contract.md\n"
    )


# ── Progressive Output Support ──────────────────────────────────────────────
# For streaming / progressive updates during long pipeline runs.

PROGRESS_LISTENERS = []  # list of callbacks receiving (phase, status, detail)

def register_progress_listener(callback):
    """Register a callback for progressive output updates.
    callback(phase: str, status: str, detail: str) -> None
    """
    PROGRESS_LISTENERS.append(callback)

def _emit_progress(phase: str, status: str, detail: str = ""):
    """Emit a progress update to all registered listeners."""
    for cb in PROGRESS_LISTENERS:
        try:
            cb(phase, status, detail)
        except Exception:
            pass


# ── Entry Point ────────────────────────────────────────────────────────────

def run_pipeline(user_prompt: str, checkpoint_id: str = None) -> str:
    """Main entry point. Returns the full pipeline output as a string."""
    _emit_progress("init", "started", f"Processing: {user_prompt[:60]}...")
    result = run_mesh_pipeline(user_prompt, checkpoint_id)
    _emit_progress("complete", "done")
    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Midway Mesh Pipeline Orchestrator")
    parser.add_argument("prompt", nargs="?", default=None,
                        help="Feature request prompt")
    parser.add_argument("--checkpoint", type=str, default=None,
                        help="Resume from a checkpoint ID")
    parser.add_argument("--list-checkpoints", action="store_true",
                        help="List all saved checkpoints")

    args = parser.parse_args()

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

    result = run_pipeline(args.prompt, args.checkpoint)
    print("\n" + result)
