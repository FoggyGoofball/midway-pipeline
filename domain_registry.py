"""
Domain registry — agent name resolution, alias maps, system prompt assembly.
Provides the canonical mapping from domain keys to agent configurations.

No async/await — purely synchronous dict lookups and string formatting.
"""

from __future__ import annotations
from typing import Dict, Optional, Any


# ── Constants (shared from pipeline.py top section) ─────────────────────────
# These constants are referenced by domain configurations below.
EXECUTION_MODEL = "qwen2.5-coder:7b"
CODER_MODEL = "qwen2.5-coder:7b"
REVIEWER_MODEL = "phi3:14b"
REASONING_MODEL = REVIEWER_MODEL

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

# ── Domain Availability ────────────────────────────────────────────────────
ALL_DOMAINS: Dict[str, Dict[str, Any]] = {
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
            "Be aware of the 'Vicious Cycle' spatial seam (teleporting bodies to Z=0).\n\n"
            "DIFF OUTPUT FORMAT: Never output the entire file. Only output the exact lines "
            "to change using a Unified Diff or Search/Replace block format. Use SEARCH/REPLACE "
            "blocks with `------- SEARCH` and `+++++++ REPLACE` markers showing exactly which "
            "lines to remove and which to insert. This preserves whitespace, comments, and "
            "existing code structure."
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
        "model": CODER_MODEL,
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
            "Focus on Lua 5.4 and sol2 bindings to the C++ host.\n\n"
            "DIFF OUTPUT FORMAT: Never output the entire file. Only output the exact lines "
            "to change using a Unified Diff or Search/Replace block format. Use SEARCH/REPLACE "
            "blocks with `------- SEARCH` and `+++++++ REPLACE` markers showing exactly which "
            "lines to remove and which to insert. This preserves whitespace, comments, and "
            "existing code structure."
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
            "        **Chronological Read Depth:** <temporal_depth>\n"
            "        **Oracle note:** Apply the following QA Heuristic:\n"
            "           - Cross-reference the memory with `docs/memory/qa_ledger.md`.\n"
            "           - If the QA Ledger states a system is currently BROKEN, and this memory is from an older, higher-depth archive, flag as **[VALID RESCUE]**: 'This deep-history memory is a valid rescue for a currently broken system.'\n"
            "           - If the QA Ledger states a system is WORKING, and this memory alters it, flag as **[HIGH-RISK REGRESSION]**: 'Warning: Alters a system the user explicitly marked as working.'\n"
            "           - If no QA anchor exists, use standard flags: **[REVERSION RISK]** (if it contradicts active ledgers) or **[STABLE CORE CONCEPT]** (if foundational).\n\n"
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
            "TEMPORAL HEURISTIC TIE-BREAKER:\n"
            "If the dispute involves memory fetched from the archives:\n"
            "- Favor the agent whose logic aligns with an active ledger or a memory tagged by the Oracle as a **Stable Core Concept** or **[VALID RESCUE]**.\n"
            "- Overrule or sustain against any agent whose logic relies on a memory tagged as a **Reversion Risk** or **[HIGH-RISK REGRESSION]**.\n\n"
            "CRITICAL RULE: Preserve feature intent over technical purity. "
            "If Agent A's 'fix' strips gameplay mechanics, narrative flavor, or modifier interactions, sustain the VETO."
        ),
        "name": "Conflict Resolution",
    },
    "TRIBUNAL": {
        "tag": "[TRIBUNAL]",
        "ready": True,
        "model": REASONING_MODEL,
        "description": "Appellate Court — blind-reviews APPEAL signals between Coders and Reviewers",
        "ledger": "docs/memory/tribunal_ledger.md",
        "system_prompt": (
            "You are the TRIBUNAL AGENT for 'Midway to Nowhere'. "
            "You are a neutral appellate arbiter. You do NOT write code. "
            "You do NOT implement features.\n\n"
            "When you receive an appeal:\n"
            "1. Read the Coder's code (the implementation being appealed)\n"
            "2. Read the Reviewer's critique (the [VETO] justification)\n"
            "3. Read the Coder's defense (the [APPEAL] justification)\n"
            "4. Evaluate both arguments against the original feature request\n"
            "5. Render a binding verdict\n\n"
            "Verdict options:\n"
            "- [MERGE:Agent:Justification]: The Coder's implementation is correct. "
            "Explain why the Reviewer's VETO is overruled.\n"
            "- [REJECT:Agent:Justification]: The Reviewer's VETO is correct. "
            "Explain what the Coder needs to fix.\n\n"
            "CRITICAL RULES:\n"
            "- You are blind: you do NOT know which agent produced which content. "
            "Judge solely on technical merit.\n"
            "- Preserve feature intent over technical purity.\n"
            "- Your verdict is BINDING. The pipeline will respect it automatically."
        ),
        "name": "Tribunal",
    },
    "LIBRARIAN": {

        "tag": "[LIBRARIAN]",
        "ready": True,
        "model": REASONING_MODEL,
        "description": "Read-only research agent — answers queries about past decisions, architecture, and memory ledgers without modifying any code",
        "ledger": "docs/memory/librarian_ledger.md",
        "system_prompt": (
            "You are the LIBRARIAN for 'Midway to Nowhere'. "
            "You are a read-only research agent. You answer questions strictly "
            "by navigating the project's structured Markdown memory ledgers.\n\n"
            "IMPORTANT CONSTRAINTS:\n"
            "1. You MUST NOT attempt to modify any code or files.\n"
            "2. You MUST answer ONLY based on information found in the provided memory documents.\n"
            "3. If you cannot find the answer in the memory documents, say so honestly.\n"
            "4. You have access to a search_memory tool that gives you the Memory Ledger "
            "Table of Contents. Use it to identify which ledger sections are relevant, "
            "then use [FETCH:docs/memory/<file>.md#<HeaderName>] to retrieve specific entries.\n\n"
            "RESEARCH METHODOLOGY:\n"
            "- First, scan the Memory Ledger Table of Contents to identify relevant sections.\n"
            "- Then retrieve specific sections using [FETCH:path#anchor] tags.\n"
            "- Synthesize the findings into a clear answer.\n"
            "- Cite your sources (which ledger file and section header)."
        ),
        "name": "Librarian",
    },
}

# ── Agent Role String Normalization ──────────────────────────────────────────
# Maps conversational/variant role strings from the Director LLM to canonical
# ALL_DOMAINS keys.
AGENT_ALIAS_MAP: Dict[str, str] = {
    # C++ domain
    "c++": "C++",
    "c++ core": "C++",
    "c++ engineer": "C++",
    "c++ systems engineer": "C++",
    "cpp": "C++",
    "cpp core": "C++",
    "engine": "C++",
    "engine architect": "C++",
    "engine systems": "C++",
    "engine programmer": "C++",
    "systems engineer": "C++",
    "c++ programmer": "C++",
    # Physics domain
    "phys": "PHYS",
    "physics": "PHYS",
    "physics architect": "PHYS",
    "physics engineer": "PHYS",
    "jolt physics": "PHYS",
    "box2d": "PHYS",
    "physics systems": "PHYS",
    "lead physics": "PHYS",
    "physics programmer": "PHYS",
    "teleport physics": "PHYS",
    # Shader domain
    "shader": "SHADER",
    "shader expert": "SHADER",
    "glsl": "SHADER",
    "rendering": "SHADER",
    "rendering expert": "SHADER",
    "graphics": "SHADER",
    "graphics programmer": "SHADER",
    "opengl": "SHADER",
    "shader programmer": "SHADER",
    "render": "SHADER",
    # Lua domain
    "lua": "Lua",
    "lua scripter": "Lua",
    "lua gameplay": "Lua",
    "gameplay scripter": "Lua",
    "scripting": "Lua",
    "lua programmer": "Lua",
    "attractions script": "Lua",
    "attraction scripting": "Lua",
    "gameplay": "Lua",
    "lua engineer": "Lua",
    "lua developer": "Lua",
    # Doc domain
    "doc": "DOC",
    "documentarian": "DOC",
    "code documentarian": "DOC",
    "memory oracle": "DOC",
    "api oracle": "DOC",
    "documentation": "DOC",
    "api doc": "DOC",
    "code doc": "DOC",
    # Conflict domain
    "conf": "CONF",
    "conflict resolution": "CONF",
    "mediator": "CONF",
    "conflict mediator": "CONF",
    "conflict agent": "CONF",
    "dispute resolution": "CONF",
    # Reviewer domain
    "reviewer": "REVIEWER",
    "integration reviewer": "REVIEWER",
    "review": "REVIEWER",
    "lead reviewer": "REVIEWER",
    "code review": "REVIEWER",
    "integration review": "REVIEWER",
    "qa review": "REVIEWER",
    # Tribunal domain (Appellate Court)
    "tribunal": "TRIBUNAL",
    "appellate": "TRIBUNAL",
    "appellate court": "TRIBUNAL",
    "appeals": "TRIBUNAL",
    "tribunal agent": "TRIBUNAL",
    # Librarian domain
    "librarian": "LIBRARIAN",

    "research": "LIBRARIAN",
    "read only": "LIBRARIAN",
    "memory research": "LIBRARIAN",
    "memory librarian": "LIBRARIAN",
    "information retrieval": "LIBRARIAN",
    # Director domain
    "director": "DIRECTOR",
    "project director": "DIRECTOR",
    "lead producer": "DIRECTOR",
    "producer": "DIRECTOR",
    "task decomposer": "DIRECTOR",
    # Diagnostic domain
    "diagnostic": "DIAGNOSTIC",
    "diagnostic oracle": "DIAGNOSTIC",
    "qa": "DIAGNOSTIC",
    "qa oracle": "DIAGNOSTIC",
    "debug": "DIAGNOSTIC",
    "debug engineer": "DIAGNOSTIC",
    # Syntax Gate domain
    "syntax gate": "SYNTAX_GATE",
    "syntax": "SYNTAX_GATE",
    "preflight": "SYNTAX_GATE",
    "syntax checker": "SYNTAX_GATE",
    # Intent Classifier domain
    "intent classifier": "INTENT_CLASSIFIER",
    "intent": "INTENT_CLASSIFIER",
    "router": "INTENT_CLASSIFIER",
    "intent router": "INTENT_CLASSIFIER",
}

# Build PERSONA_MAP from ready domains only
PERSONA_MAP: Dict[str, Dict[str, str]] = {}
for key, domain in ALL_DOMAINS.items():
    if domain["ready"]:
        PERSONA_MAP[key] = {"system": domain["system_prompt"], "name": domain["name"]}
        PERSONA_MAP[key.lower()] = {"system": domain["system_prompt"], "name": domain["name"]}


def resolve_agent_name(name: str) -> str:
    """Resolve a signal target name to a domain key.

    Checks the AGENT_ALIAS_MAP first, then tries direct match, name match,
    partial match, and finally substring matching against alias map keys.

    Args:
        name: Raw agent name string from LLM output.

    Returns:
        Canonical domain key, or the original name if unresolvable.
    """
    name_lower = name.lower().strip()
    # Alias Map Lookup (first priority)
    if name_lower in AGENT_ALIAS_MAP:
        return AGENT_ALIAS_MAP[name_lower]
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
    # Final fallback: AGENT_ALIAS_MAP reverse scan
    for alias, canonical_key in AGENT_ALIAS_MAP.items():
        if alias in name_lower or name_lower in alias:
            return canonical_key
    return name


def get_agent_system(agent_key: str, pro_mode: bool = False) -> str:
    """Get the system prompt for an agent, with mesh extension.

    Args:
        agent_key: Canonical domain key (e.g., "C++", "Lua").
        pro_mode: Reserved — no longer injects TDD instructions (handled upstream).

    Returns:
        Full system prompt string with ledger and mesh extensions.
    """
    domain = ALL_DOMAINS.get(agent_key)
    if not domain:
        return ""
    base = domain["system_prompt"]
    ledger_path = domain.get("ledger", "")
    ledger_note = ""
    if ledger_path:
        ledger_note = f"\n\nYour assigned memory ledger: {ledger_path}\n"
    # MESH_AGENT_SYSTEM_EXTENSION for non-DOC/CONF agents
    mesh_ext = "\n\n" + MESH_AGENT_SYSTEM_EXTENSION if agent_key not in ("DOC", "CONF") else ""

    return base + ledger_note + mesh_ext + LEDGER_MEMORY_RULE



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
    "Signal targets use domain names: C++, PHYS, SHADER, Lua, DOC, CONF, LIBRARIAN, REVIEWER, TRIBUNAL.\n"
    "You can consult the Code Documentarian (DOC) for API uncertainty.\n"
    "You can consult the Conflict Resolution agent (CONF) for dispute mediation.\n"
    "Always end your output with a signal when appropriate.\n"
    "If you need the most recently fetched file content, use ## Double-Check section."

)
