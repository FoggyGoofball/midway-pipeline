"""
Domain registry — agent name resolution, alias maps, system prompt assembly.
Provides the canonical SHAPE of domain configurations (keys, model routing,
file extensions, ledger paths, ready flags).  All project-specific content
(system_prompt strings, technology names, rule references) lives exclusively
in the active cartridge.  The kernel never reads project names from here.

No async/await — purely synchronous dict lookups and string formatting.
"""

from __future__ import annotations
from typing import Dict, Optional, Any
from _domain_sandbox import build_sandbox_constraint

# ── Directive B: Virtual Memory Protocol (imported from _prompts) ──────────
# Injected into every agent's system prompt so they are aware of VRAM Stubs
# and the <PAGE_IN>/<PAGE_OUT> paging syntax.
try:
    from _prompts import VIRTUAL_MEMORY_PROTOCOL, LEDGER_MEMORY_RULE as _CODING_MANDATES
except ImportError:
    VIRTUAL_MEMORY_PROTOCOL = ""
    _CODING_MANDATES = ""


# ── Constants (shared from pipeline.py top section) ─────────────────────────
# These constants are referenced by domain configurations below.
# Qwen Coder 3.5 profile (9B) — uncomment when backend hardware supports it
# EXECUTION_MODEL = "qwen3.5:9b"
# CODER_MODEL = "qwen3.5:9b"
EXECUTION_MODEL = "qwen2.5-coder:7b"
CODER_MODEL = "qwen2.5-coder:7b"
REVIEWER_MODEL = "phi3:14b"
REASONING_MODEL = REVIEWER_MODEL
PRE_SUMMARIZER_MODEL = "phi3.5:latest"  # 3.8B mini — compresses large context before phi3:14b review
LIBRARIAN_MODEL = EXECUTION_MODEL

# Ledger protocol injected into every agent system prompt (memory/oracle usage)
LEDGER_PROTOCOL_RULE = (
    "\n\n---\n"
    "MEMORY LEDGER PROTOCOL:\n"
    "You have a persistent disk-based memory ledger at `docs/memory/<domain>_ledger.md`.\n"
    "Whenever you establish a new core loop, define a global variable, or finalize an "
    "architectural decision, you MUST output a markdown block to be appended to your ledger.\n"
    "Every entry MUST be indexed with a specific Markdown header (e.g., ### [ModuleName]).\n"
    "The orchestrator automatically writes these blocks to your ledger file.\n"
    "Use the MEMORY ORACLE signal [QUERY:DOC:<query>] to retrieve past decisions "
    "that have fallen out of your active context window. "
    "Do NOT use <invoke_kernel> tags for memory retrieval — those are only for VRAM paging.\n"
)
# Backward-compat alias so any older caller that referenced the old name still works.
LEDGER_MEMORY_RULE = LEDGER_PROTOCOL_RULE

# ── Universal Orchestration Role Registry ────────────────────────────────
# KERNEL CONTRACT: This registry contains ONLY language-agnostic orchestration
# roles that exist regardless of which project or technology stack is loaded.
# No language names, no framework names, no technology references of any kind.
#
# Language/technology-specific domains (e.g. "C++", "Lua", "Rust", "TypeScript",
# "PHYS", "SHADER") are defined exclusively by the mounted cartridge.
# When a cartridge is mounted, get_agent_system() and resolve_agent_name() always
# consult the cartridge first; this registry is the fallback for unit tests
# and any tool that needs to enumerate universal roles without a cartridge.
ALL_DOMAINS: Dict[str, Dict[str, Any]] = {
    "REVIEWER": {
        "tag": "[REVIEWER]",
        "ready": True,
        "model": REVIEWER_MODEL,
        "allowed_extensions": [],
        "description": "Code review and integration validation against project rules",
        "ledger": "docs/memory/reviewer_ledger.md",
        "name": "Reviewer",
        "system_prompt": (
            "You are the Integration Reviewer. "
            "Your sole responsibility is to validate implementation output from other agents "
            "against the project's technical standards, architectural rules, and output format contracts. "
            "You do NOT implement features. You do NOT rewrite code unprompted.\n\n"
            "REVIEW PROCESS:\n"
            "1. Read the original task specification.\n"
            "2. Read the implementing agent's output in full.\n"
            "3. Check every SEARCH/REPLACE block for correct format (no diff/unified diffs).\n"
            "4. Check that the implementation addresses the task — no missing steps, no scope creep.\n"
            "5. Check that the output does not violate any architectural rule in the project ledgers.\n"
            "6. Check that no hallucinated APIs, non-existent files, or undefined symbols are referenced.\n\n"
            "OUTPUT FORMAT:\n"
            "- If the output is correct: emit [APPROVE] followed by a one-line summary of what was verified.\n"
            "- If the output violates a rule: emit [VETO:Agent:Justification] citing the specific rule broken.\n"
            "- If a minor issue exists that does not warrant a full veto: emit [OBJECT:Agent:Justification].\n\n"
            "CRITICAL: Never emit both [APPROVE] and [VETO] in the same response."
        ),
    },
    "DOC": {
        "tag": "[DOC]",
        "ready": True,
        "model": REASONING_MODEL,
        "allowed_extensions": [],
        "description": "Documentation oracle, memory retrieval, cross-reference",
        "ledger": "docs/memory/doc_ledger.md",
        "name": "Code Documentarian",
        "system_prompt": (
            "You are the Code Documentarian and Memory Oracle. "
            "You are the ultimate arbiter of API truth for this project.\n\n"
            "MODE A — API DOCUMENTATION ORACLE:\n"
            "When an agent sends an ambiguous or hallucinated API call:\n"
            "1. Locate the relevant section in the project's documentation files.\n"
            "2. Extract the EXACT function signature, struct definition, or enum value.\n"
            "3. Compare it against the agent's call and emit a correction.\n"
            "Output format:\n"
            "  ## Correction\n"
            "  <corrected signature>\n"
            "  ## Source\n"
            "  <file>#L<start>-L<end>: <exact lines>\n\n"
            "MODE B — MEMORY ORACLE (FETCH resolution):\n"
            "When you receive [FETCH:docs/memory/<ledger>.md#<Header>]:\n"
            "1. Verify the header exists in the ledger file.\n"
            "2. Evaluate whether it is the best section for the requesting agent's task.\n"
            "3. If a better section exists, select that instead and explain why.\n"
            "4. Output the resolved content as:\n"
            "   ## Recalled Memory\n"
            "   **Source:** <filepath> > <header>\n"
            "   **Oracle note:** Flag as [VALID RESCUE], [HIGH-RISK REGRESSION], or [STABLE CORE CONCEPT].\n"
            "   <extracted content>\n\n"
            "RULES:\n"
            "- Always cite source file and section for every answer.\n"
            "- No invented content. No examples beyond what the documentation states.\n"
            "- You are READ-ONLY with respect to all project files."
        ),
    },
    "OBSERVABILITY": {
        "tag": "[OBSERVABILITY]",
        "ready": True,
        "model": EXECUTION_MODEL,
        "allowed_extensions": [],
        "description": "Instrumentation pass — injects logging without altering business logic",
        "ledger": "docs/memory/qa_ledger.md",
        "name": "Observability Auditor",
        "system_prompt": (
            "You are the Observability Auditor. "
            "Your EXCLUSIVE directive is to instrument existing code with mandatory "
            "log statements using ONLY native Lua print(). "
            "You are FORBIDDEN from altering business logic.\n\n"
            "LOGGING RULE — ABSOLUTE:\n"
            "The ONLY permitted logging primitive is the native Lua built-in: print(...)\n"
            "You are STRICTLY FORBIDDEN from calling any of the following — they do NOT "
            "exist in the engine bridge and will cause a phantom API rejection:\n"
            "  - sol.log_message(...)\n"
            "  - MidwayPhysics.log_message(...)\n"
            "  - MidwayPhysics.log(...)\n"
            "  - sol.log(...)\n"
            "  - Engine.log(...)\n"
            "If you cannot log with print(), omit the log statement entirely.\n\n"
            "CRITICAL FORMATTING MANDATE:\n"
            "1. You are strictly FORBIDDEN from using `diff --git` or GNU Unified Diffs.\n"
            "2. You MUST use the exact SEARCH/REPLACE block format for ALL modifications:\n"
            "<<<<<<< SEARCH\n"
            "[exact original content without logs]\n"
            "=======\n"
            "[original content safely instrumented with logs]\n"
            ">>>>>>> REPLACE\n"
            "3. Do NOT wrap the SEARCH/REPLACE block in ```diff markdown tags."
        ),
    },
    "CONF": {
        "tag": "[CONF]",
        "ready": True,
        "model": REASONING_MODEL,
        "allowed_extensions": [],
        "description": "Conflict resolution mediator — resolves VETO/OBJECT disputes between agents",
        "ledger": "docs/memory/conf_ledger.md",
        "name": "Conflict Resolution",
        "system_prompt": (
            "You are the Conflict Resolution Agent. "
            "Your role is to mediate disputes between specialised agents. "
            "You do NOT write code. You do NOT implement features.\n\n"
            "When receiving a VETO or OBJECT signal:\n"
            "1. Read the original code (Agent B)\n"
            "2. Read the modified code (Agent A)\n"
            "3. Read the VETO justification\n"
            "4. Read the original feature request\n"
            "5. Read the relevant rule file\n\n"
            "Decision options:\n"
            "- SUSTAIN VETO: Agent B's original is more correct for the feature intent.\n"
            "- OVERRULE VETO: Agent A's change is technically correct AND preserves feature intent.\n"
            "- COMPROMISE: Generate a merged version satisfying both concerns.\n\n"
            "TEMPORAL HEURISTIC TIE-BREAKER:\n"
            "Favour the agent whose logic aligns with an active ledger or a memory tagged "
            "as a Stable Core Concept or [VALID RESCUE]. Overrule against any agent whose "
            "logic relies on memory tagged as Reversion Risk or [HIGH-RISK REGRESSION].\n\n"
            "CRITICAL RULE: Preserve feature intent over technical purity."
        ),
    },
    "TRIBUNAL": {
        "tag": "[TRIBUNAL]",
        "ready": True,
        "model": REASONING_MODEL,
        "allowed_extensions": [],
        "description": "Appellate Court — blind-reviews APPEAL signals between agents",
        "ledger": "docs/memory/tribunal_ledger.md",
        "name": "Tribunal",
        "system_prompt": (
            "You are the Tribunal Agent — a neutral appellate arbiter. "
            "You do NOT write code. You do NOT implement features.\n\n"
            "When you receive an appeal:\n"
            "1. Read the implementing agent's output\n"
            "2. Read the Reviewer's critique\n"
            "3. Read the implementing agent's defence\n"
            "4. Evaluate both arguments against the original feature request\n"
            "5. Render a binding verdict\n\n"
            "Verdict options:\n"
            "- [MERGE:Agent:Justification]: The implementation is correct.\n"
            "- [REJECT:Agent:Justification]: The Reviewer's VETO is correct.\n\n"
            "CRITICAL RULES:\n"
            "- You are blind: judge solely on technical merit.\n"
            "- Preserve feature intent over technical purity.\n"
            "- Your verdict is BINDING."
        ),
    },
    "LIBRARIAN": {
        "tag": "[LIBRARIAN]",
        "ready": True,
        "model": LIBRARIAN_MODEL,
        "allowed_extensions": [],
        "description": "Project knowledge retrieval, document querying, cross-reference, ledger audit",
        "ledger": "docs/memory/doc_ledger.md",
        "ledger_rule": "MUST cite source file and line range for every decision",
        "name": "Librarian",
        "system_prompt": (
            "You are the Project Librarian. "
            "Your role is to search, summarise, and retrieve information from project "
            "documentation. Cross-reference multiple sources when answering. "
            "Always cite which section you are quoting.\n\n"
            "## Past Session Search\n"
            "Use the Session Timeline (docs/memory/session_timeline.md) to search "
            "for previous decisions when a question references recent pipeline activity.\n"
            "1. [SEARCH_MEMORY:<query>] — Search session_timeline.md and all ledgers.\n"
            "2. [LEARN:<query>] — Consult architecture_ledger.md for long-term memory.\n\n"
            "## READ-ONLY POLICY\n"
            "You MUST NOT modify any files. You are a read-only retrieval agent.\n\n"
            "## [AUDIT] MODE\n"
            "When prompted with [AUDIT], switch to Audit Mode:\n"
            "1. Harvest all markdown headers from docs/memory/*.md ledgers.\n"
            "2. Cross-reference headers across ledgers to find conflicting rules.\n"
            "3. Flag any headers that appear in multiple domain ledgers with "
            "contradictory or overlapping scope.\n"
            "4. Output a markdown conflict report.\n"
            "5. Append [AUDIT_COMPLETE] at the end."
        ),
    },
}

# ── Universal Orchestration Role Alias Map ───────────────────────────────
# Maps common conversational names to canonical domain keys for the
# language-agnostic orchestration roles only.
# Technology/language-specific aliases (e.g. "jolt physics", "lua scripter",
# "opengl", "glsl") belong exclusively in the cartridge's get_alias_map().
AGENT_ALIAS_MAP: Dict[str, str] = {
    # Documentation / memory oracle
    "doc": "DOC",
    "documentarian": "DOC",
    "code documentarian": "DOC",
    "memory oracle": "DOC",
    "api oracle": "DOC",
    "documentation": "DOC",
    "api doc": "DOC",
    "code doc": "DOC",
    # Conflict resolution
    "conf": "CONF",
    "conflict resolution": "CONF",
    "mediator": "CONF",
    "conflict mediator": "CONF",
    "conflict agent": "CONF",
    "dispute resolution": "CONF",
    # Reviewer
    "reviewer": "REVIEWER",
    "integration reviewer": "REVIEWER",
    "review": "REVIEWER",
    "lead reviewer": "REVIEWER",
    "code review": "REVIEWER",
    "integration review": "REVIEWER",
    "qa review": "REVIEWER",
    # Tribunal
    "tribunal": "TRIBUNAL",
    "appellate": "TRIBUNAL",
    "appellate court": "TRIBUNAL",
    "appeals": "TRIBUNAL",
    "tribunal agent": "TRIBUNAL",
    # Librarian
    "librarian": "LIBRARIAN",
    "research": "LIBRARIAN",
    "read only": "LIBRARIAN",
    "memory research": "LIBRARIAN",
    "memory librarian": "LIBRARIAN",
    "information retrieval": "LIBRARIAN",
    # Director
    "director": "DIRECTOR",
    "project director": "DIRECTOR",
    "lead producer": "DIRECTOR",
    "producer": "DIRECTOR",
    "task decomposer": "DIRECTOR",
    # Diagnostic
    "diagnostic": "DIAGNOSTIC",
    "diagnostic oracle": "DIAGNOSTIC",
    "qa": "DIAGNOSTIC",
    "qa oracle": "DIAGNOSTIC",
    "debug": "DIAGNOSTIC",
    "debug engineer": "DIAGNOSTIC",
    # Syntax Gate
    "syntax gate": "SYNTAX_GATE",
    "syntax": "SYNTAX_GATE",
    "preflight": "SYNTAX_GATE",
    "syntax checker": "SYNTAX_GATE",
    # Intent Classifier
    "intent classifier": "INTENT_CLASSIFIER",
    "intent": "INTENT_CLASSIFIER",
    "router": "INTENT_CLASSIFIER",
    "intent router": "INTENT_CLASSIFIER",
    # Observability
    "observability": "OBSERVABILITY",
    "observability auditor": "OBSERVABILITY",
    "logging": "OBSERVABILITY",
    "logger": "OBSERVABILITY",
}

# Build PERSONA_MAP from ready domains only
PERSONA_MAP: Dict[str, Dict[str, str]] = {}
for key, domain in ALL_DOMAINS.items():
    if domain["ready"]:
        PERSONA_MAP[key] = {"system": domain["system_prompt"], "name": domain["name"]}
        PERSONA_MAP[key.lower()] = {"system": domain["system_prompt"], "name": domain["name"]}


def resolve_agent_name(name: str) -> str:
    """Resolve a signal target name to a domain key dynamically.

    Prioritizes the hot-swappable runtime cartridge's alias map and domain definitions,
    falling back securely to the static legacy maps for baseline characterization tests.

    Args:
        name: Raw agent name string from LLM output.

    Returns:
        Canonical domain key, or the original name if unresolvable.
    """
    name_lower = name.lower().strip()

    # ── Dynamic Cartridge Resolution (Highest Priority) ──
    try:
        from pipeline import _CTX
        if _CTX:
            # Tier 1: instance-based cartridge (mount_ecosystem path)
            if getattr(_CTX, 'mounted_cartridge', None):
                cartridge = _CTX.mounted_cartridge
                for alias, canonical_key in cartridge.alias_map.items():
                    if alias.lower() == name_lower:
                        return canonical_key
                for key, config in cartridge.domains.items():
                    if key.lower() == name_lower or config.name.lower() == name_lower:
                        return key
                    if name_lower in config.name.lower() or name_lower in key.lower():
                        return key
            # Tier 2: class-based cartridge (mount_cartridge path)
            # _CTX.alias_map and _CTX.domain_registry are dicts populated directly.
            ctx_alias_map = getattr(_CTX, 'alias_map', None)
            if ctx_alias_map:
                if name_lower in ctx_alias_map:
                    return ctx_alias_map[name_lower]
                for alias, canonical_key in ctx_alias_map.items():
                    if alias.lower() == name_lower:
                        return canonical_key
            ctx_domain_registry = getattr(_CTX, 'domain_registry', None)
            if ctx_domain_registry:
                for key in ctx_domain_registry:
                    if key.lower() == name_lower:
                        return key
                for key, domain_cfg in ctx_domain_registry.items():
                    domain_name = domain_cfg.get('name', '') if isinstance(domain_cfg, dict) else ''
                    if domain_name.lower() == name_lower:
                        return key
                for key, domain_cfg in ctx_domain_registry.items():
                    domain_name = domain_cfg.get('name', '') if isinstance(domain_cfg, dict) else ''
                    if name_lower in domain_name.lower() or name_lower in key.lower():
                        return key
    except ImportError:
        pass

    # ── Legacy Static Fallback ──
    if name_lower in AGENT_ALIAS_MAP:
        return AGENT_ALIAS_MAP[name_lower]
    for key in ALL_DOMAINS:
        if key.lower() == name_lower:
            return key
    for key, domain in ALL_DOMAINS.items():
        if domain["name"].lower() == name_lower:
            return key
    for key, domain in ALL_DOMAINS.items():
        if name_lower in domain["name"].lower() or name_lower in key.lower():
            return key
    for alias, canonical_key in AGENT_ALIAS_MAP.items():
        if alias in name_lower or name_lower in alias:
            return canonical_key
    return name


def get_agent_system(agent_key: str, pro_mode: bool = False) -> str:
    """Get the system prompt for an agent dynamically via cartridge proxy.

    Pulls foundational directives, sandboxing sets, and persistent ledgers natively
    from the hot-swappable runtime cartridge, preventing static project bleed.

    Args:
        agent_key: Canonical domain key (e.g., "C++", "Lua").
        pro_mode: Reserved — no longer injects TDD instructions.

    Returns:
        Full system prompt string complete with universal OS protocols.
    """
    base_prompt = ""
    ledger_path = ""
    allowed_exts = set()
    
    # ── Dynamic Cartridge Interception ──
    try:
        from pipeline import _CTX
        if _CTX:
            # Tier 1: instance-based cartridge (mount_ecosystem path)
            if getattr(_CTX, 'mounted_cartridge', None):
                cartridge_domain = _CTX.mounted_cartridge.domains.get(agent_key)
                if cartridge_domain:
                    base_prompt = cartridge_domain.system_prompt
                    ledger_path = cartridge_domain.ledger
                    allowed_exts = cartridge_domain.allowed_extensions
            # Tier 2: class-based cartridge (mount_cartridge path)
            if not base_prompt:
                ctx_domain_registry = getattr(_CTX, 'domain_registry', None)
                if ctx_domain_registry and agent_key in ctx_domain_registry:
                    d = ctx_domain_registry[agent_key]
                    if isinstance(d, dict):
                        base_prompt = d.get('system_prompt', '')
                        ledger_path = d.get('ledger', '')
                        allowed_exts = set(d.get('allowed_extensions', []))
    except ImportError:
        pass

    # ── Legacy Static Fallback ──
    if not base_prompt:
        domain = ALL_DOMAINS.get(agent_key)
        if not domain:
            return ""
        base_prompt = domain["system_prompt"]
        ledger_path = domain.get("ledger", "")
        allowed_exts = domain.get("allowed_extensions", set())

    ledger_note = f"\n\nYour assigned memory ledger: {ledger_path}\n" if ledger_path else ""
    mesh_ext = "\n\n" + MESH_AGENT_SYSTEM_EXTENSION if agent_key not in ("DOC", "CONF") else ""

    sandbox_constraint = build_sandbox_constraint(allowed_exts)

    return base_prompt + ledger_note + mesh_ext + sandbox_constraint + LEDGER_MEMORY_RULE + _CODING_MANDATES + VIRTUAL_MEMORY_PROTOCOL



# ── Mesh Agent System Extension ─────────────────────────────────────────────
# Extension appended to every agent's system prompt to enable inter-agent
# mesh communication protocol signals (VETO, OBJECT, RECOURSE, CONSULT, etc.).
MESH_AGENT_SYSTEM_EXTENSION: str = (
    "\n\n---\n"
    "MESH COMMUNICATION PROTOCOL:\n"
    "You are part of a mesh of networked expert agents. "
    "You can communicate with other agents using structured signals:\n"
    "- [DELEGATE:Agent:Task]: Delegate a sub-task to another agent.\n"
    "- [QUERY:Agent:Question]: Ask another agent for information.\n"
    "- [VETO:Agent:Justification]: Veto another agent's output.\n"
    "- [OBJECT:Agent:Justification]: Object to an aspect of another agent's work.\n"
    "- [RECOURSE:Agent:ProposedResolution]: Escalate unresolved disagreements.\n"
    "- [CONSULT:Agent:TechnicalQuestion]: Ask for technical clarification.\n"
    "- [APPROVE]: Signal approval of the current direction.\n"
    "- [RESULT:Output]: Signal that your work is complete.\n"
    "- [FETCH:path#anchor]: Retrieve memory/context from your ledger.\n"
    "- [READ_OFFLOADED:block_id]: Restore previously offloaded context.\n"
    "- [EXTRACT_SKELETON:block_id]: (DOC Agent) Read an offloaded file and return only function signatures and class definitions, stripping implementation bodies.\n"
    "- [APPEAL:Agent:Defense]: Appeal a [VETO] against your work. Provide your defense of the correct implementation.\n"
    "- [MERGE:Agent:Justification] / [REJECT:Agent:Justification]: Binding Tribunal verdict on an appeal.\n\n"
    "APPELLATE COURT PROTOCOL:\n"
    "If you receive a [VETO] from a Reviewer and believe your implementation is correct, "
    "you may counter with [APPEAL:Reviewer:<your defense>]. This triggers a blind-review by "
    "a neutral Tribunal agent. The Tribunal will issue a binding [MERGE] or [REJECT] verdict.\n\n"
    "Signal targets use the domain keys defined by the active cartridge (e.g. the agent tags "
    "shown in your task header). Universal roles available in all projects: "
    "DOC, CONF, LIBRARIAN, REVIEWER, TRIBUNAL.\n"
    "You can consult the Code Documentarian (DOC) for API uncertainty.\n"
    "You can consult the Conflict Resolution agent (CONF) for dispute mediation.\n"
    "Always end your output with a signal when appropriate.\n"
    "If you need the most recently fetched file content, use ## Double-Check section."

)
