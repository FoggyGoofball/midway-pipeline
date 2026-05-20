"""
midway_data.py — Pure-data provider functions for MidwayAgentCartridge
=======================================================================
Extracted from midway_ecosystem.py to keep that file under 1 000 lines.

Every function in this module returns a plain Python dict or primitive that
MidwayAgentCartridge's static methods delegate to.

Import direction (no cycles):
    cartridge_loader.py
        → midway_ecosystem.py (MidwayAgentCartridge class)
                → midway_data.py   (pure data builders — no pipeline imports)

No pipeline or kernel imports are allowed here.
"""
from __future__ import annotations

from typing import Any, Dict


# ── Domain registry ─────────────────────────────────────────────────────────

def build_domain_registry(
    EXECUTION_MODEL: str,
    CODER_MODEL: str,
    REVIEWER_MODEL: str,
    REASONING_MODEL: str,
    LIBRARIAN_MODEL: str,
    KERNEL_ALIAS_MAP: dict,
) -> Dict[str, Dict[str, Any]]:
    """Return the full Midway domain registry dict."""
    _SEARCH_REPLACE_MANDATE = (
        "\n\nCRITICAL FORMATTING MANDATE:\n"
        "1. You are strictly FORBIDDEN from using `diff --git` or GNU Unified Diffs.\n"
        "2. You MUST use the exact SEARCH/REPLACE block format for ALL code modifications or creations:\n"
        "<<<<<<< SEARCH\n"
        "[exact original content to replace, or empty if this is a new file]\n"
        "=======\n"
        "[new content]\n"
        ">>>>>>> REPLACE\n"
        "3. Do NOT wrap the SEARCH/REPLACE block in ```diff markdown tags."
    )

    # ── Midway project-wide prohibitions ────────────────────────────────────
    # These rules apply to EVERY domain agent in this cartridge.
    # They address recurring hallucination patterns specific to this project.
    _MIDWAY_PROHIBITIONS = (
        "\n\n---\nMIDWAY PROJECT PROHIBITIONS (apply to every response):\n"
        "P1. SPATIAL SEAM / VICIOUS CYCLE: The Vicious Cycle spatial seam already exists "
        "and is FULLY OPERATIONAL. You are STRICTLY FORBIDDEN from implementing, referencing, "
        "or invoking any teleport thresholds, Z=0 resets, seam triggers, or "
        "OptimizeBroadPhase() calls in your output unless your Task Specification "
        "explicitly names the Vicious Cycle seam as the target. Individual attraction "
        "scripts (Lua) have ZERO awareness of seams or spatial boundaries.\n"
        "P2. LUA PHYSICS: Lua attraction scripts MUST NOT simulate physics manually. "
        "All physical behaviour MUST be achieved exclusively through "
        "MidwayPhysics.Spawn*, MoveKinematic, ApplyImpulse, and the other "
        "registered sol2 host APIs. Writing raw position arithmetic, euler "
        "integration, or collision maths in Lua is a CRITICAL VIOLATION.\n"
        "P3. PHANTOM APIs: You are FORBIDDEN from calling any MidwayPhysics, Engine, "
        "or sol binding function that is NOT explicitly listed in the active bridge contract "
        "injected into your context. If a required API is absent from the contract, create a "
        "C++ task to expose it via sol2. DO NOT emit [QUERY:DOC] signals as a substitute "
        "for writing code — that is a CRITICAL VIOLATION of P5.\n"
        "P3a. KNOWN PHANTOM NAMES — these do NOT exist, never use them:\n"
        "  SpawnDynamicBall → use SpawnDynamicSphere(lx, ly, lz, radius) instead.\n"
        "  SpawnDynamicBody, SpawnStaticBody (generic) → use the typed variants "
        "(SpawnStaticBox, SpawnDynamicSphere, etc.).\n"
        "  RemoveBody, DestroyEntity, ReleaseHandle → use DestroyBody(handle).\n"
        "  CheckCollision → use IsSensorTriggered(handle) for sensor overlap.\n"
        "  GetLinearVelocity → use GetVelocity(handle).\n"
        "  SetLinearVelocity(handle, vec) → use SetLinearVelocity(handle, vx, vy, vz).\n"
        "  MoveKinematic(handle, vec) → use MoveKinematic(handle, lx, ly, lz, dt).\n"
        "  sol.on_load / sol.on_step / sol.on_unload / sol.set_function -> these do NOT exist.\n"
        "  function OnStep(dt) ... end (bare global) -> this is NEVER called by the engine; "
        "register via MidwayPhysics.OnStep(function(dt) ... end) inside OnLoad() instead.\n"
        "P4. BOX2D: Box2D is permanently removed. Do NOT reference b2World, b2Vec2, "
        "b2Body, jpBody, or any Box2D header. All physics is Jolt-exclusive.\n"
        "P5. EMPTY OUTPUT: Your response MUST contain at least one concrete "
        "code block with real implementation code. Responding with only "
        "prose, explanations, or [DELEGATE:...] signals is a CRITICAL VIOLATION.\n"
        "P6. LUA STDLIB BAN: Lua attraction scripts MUST NOT use require(), io.*, "
        "os.*, package.*, or any standard Lua file/system library. "
        "File I/O and JSON parsing MUST be exposed by the C++ host via sol2. "
        "If a needed host function (e.g. Engine.LoadJSON) is absent from the active "
        "bridge contract, create a C++ task to add it. Using require() or io.open() "
        "directly in a Lua attraction script is a CRITICAL VIOLATION.\n"
    )

    _OBS_MANDATE = (
        "\n\nCRITICAL OUTPUT FORMAT:\n"
        "Return ONLY the fully-instrumented source file as a plain code block.\n"
        "Do NOT use SEARCH/REPLACE blocks, diff markers (<<<<<<< / ======= / >>>>>>>), "
        "or any diff format. Output the entire file verbatim with log statements added "
        "in-place. No commentary outside the code block."
    )

    registry: Dict[str, Dict[str, Any]] = {
        "C++": {
            "tag": "[C++]",
            "ready": True,
            "model": EXECUTION_MODEL,
            "allowed_extensions": [".cpp", ".h", ".hpp", ".c", ".hxx", ".cxx"],
            "ledger_rule": "MUST append signature to docs/internal_api_ledger.md",
            "description": (
                "Engine architecture, physics integration, rendering, memory, "
                "Vicious Cycle seam, modifier system, object pools, booth lifecycle"
            ),
            "ledger": "docs/memory/cpp_ledger.md",
            "name": "C++ Core",
            "system_prompt": (
                "You are the C++17 systems engineer for 'Midway to Nowhere'. "
                "Write ONLY C++17. Use SDL2, OpenGL 3.3+, nlohmann/json. "
                "Note: The 'Vicious Cycle' spatial seam (teleporting bodies to Z=0) "
                "is already established and functioning.\n"
                "CRITICAL TASK PRIMACY: Prioritize the concrete instructions in your "
                "## Task Specification over background engine notes. "
                "When exposing APIs or physics primitives, implement them fully. "
                "NEVER invent standalone classes for custom physics primitives — "
                "expose them exclusively by registering sol2 lambdas that wrap "
                "existing `MidwayPhysics::Spawn*` methods. "
                "All exposed primitives MUST return an integer handle."
            ) + _MIDWAY_PROHIBITIONS + _SEARCH_REPLACE_MANDATE,
        },
        "PHYS": {
            "tag": "[PHYS]",
            "ready": True,
            "model": EXECUTION_MODEL,
            "allowed_extensions": [".cpp", ".h", ".hpp", ".c", ".hxx", ".cxx"],
            "description": (
                "Unified Jolt Physics dynamics, custom colliders, "
                "and strict Box2D exclusion enforcement."
            ),
            "ledger": "docs/memory/phys_ledger.md",
            "name": "Physics Architect",
            "system_prompt": (
                "You are the Lead Physics Architect for 'Midway to Nowhere'. "
                "CRITICAL MANDATE: You operate exclusively on the Unified Jolt Physics standard. "
                "Box2D is FULLY DEPRECATED and removed from the runtime ecosystem; "
                "do NOT write Box2D initialisation vectors, variables, world instances, "
                "or API references. "
                "Model all planar attractions using strict Jolt DOF constraints.\n"
                "Analyse compiler diagnostics and call stacks for physical state discrepancies.\n"
                "CRITICAL TASK PRIMACY: Strictly execute the instructions in your "
                "## Task Specification. Do not ignore feature assignments in favour "
                "of background boilerplate notes.\n"
                "CRITICAL: You are FORBIDDEN from emitting only a [DELEGATE:...] signal "
                "as your entire response. [DELEGATE] may appear alongside real implementation "
                "output, but NEVER as a substitute for it. "
                "Always produce concrete SEARCH/REPLACE blocks addressing the task."
            ) + _MIDWAY_PROHIBITIONS + _SEARCH_REPLACE_MANDATE,
        },
        "SHADER": {
            "tag": "[SHADER]",
            "ready": False,
            "model": CODER_MODEL,
            "description": "GLSL shaders, Karmic-Temporal Matrix, PS1 vertex snapping, bloom, particles",
            "ledger": "docs/memory/shader_ledger.md",
            "name": "Shader Expert",
            "system_prompt": (
                "You are the Rendering Expert for 'Midway to Nowhere'. "
                "Specialise in OpenGL 3.3+ GLSL shader pipelines and the 'Midway Host' "
                "vertex buffer object (VBO) architecture.\n\n"
                "SCOPE:\n"
                "- Vertex and fragment shaders (.vert / .frag / .glsl).\n"
                "- Karmic-Temporal Matrix visual effects and PS1-style vertex snapping.\n"
                "- Bloom, particle, and post-processing passes.\n\n"
                "RULES:\n"
                "- Target GLSL version 330 core. No extensions unless explicitly approved.\n"
                "- Do NOT modify C++ engine code. Shader changes are file-scoped.\n"
                "- All uniforms MUST be documented with type, unit, and expected range."
            ) + _SEARCH_REPLACE_MANDATE,
        },
        "Lua": {
            "tag": "[Lua]",
            "ready": True,
            "model": EXECUTION_MODEL,
            "allowed_extensions": [".lua"],
            "ledger_rule": "MUST append signature to docs/internal_api_ledger.md",
            "description": "Attraction scripts, UI, economy, modifier consumption, OnLoad/OnStep/OnUnload",
            "ledger": "docs/memory/lua_ledger.md",
            "name": "Lua Scripter",
            "system_prompt": (
                "You are the gameplay scripter for 'Midway to Nowhere'. "
                "Write ONLY Lua 5.4. All host engine interaction goes through sol2 bindings exposed by the C++ host.\n\n"
                "ATTRACTION LIFECYCLE:\n"
                "Every attraction script MUST implement exactly three entry points:\n"
                "  OnLoadStatic() — called once for permanent geometry (cabinet, floor). Spawn static bodies here.\n"
                "  OnLoad()       — called once when the slot is mounted. Register the per-step callback HERE:\n"
                "                  MidwayPhysics.OnStep(function(dt) ... end)\n"
                "                  Spawn gameplay bodies here. Do NOT define a bare global OnStep(dt).\n"
                "  OnUnload()     — called when the slot is unmounted. Release all handles.\n\n"
                "CRITICAL: `OnStep` is NOT a bare global function. It is a callback registered by calling\n"
                "`MidwayPhysics.OnStep(function(dt) ... end)` inside OnLoad(). Defining `function OnStep(dt)` \n"
                "at global scope is a CRITICAL VIOLATION — the engine will NEVER invoke it.\n\n"
                "SOL2 BINDING RULES:\n"
                "- Only call host APIs that are registered in MidwayPhysics.cpp or Engine.cpp.\n"
                "- NEVER store or pass raw JPH::BodyID values. All physics objects are referenced "
                "by integer handles returned from the host spawn functions.\n"
                "- NEVER require() C modules or call os/io/package APIs.\n\n"
                "LOGGING:\n"
                "Use ONLY native Lua `print(...)` for all logging. "
                "Always coerce non-string values: `print('val=' .. tostring(val))`. "
                "FORBIDDEN: sol.log_message, sol.log, MidwayPhysics.log_message, MidwayPhysics.log, "
                "Engine.log — these do NOT exist in the engine bridge and will cause phantom API failures.\n\n"
                "MOVEKKINEMATIC SEMANTICS:\n"
                "MidwayPhysics.MoveKinematic(handle, lx, ly, lz, dt) sets the body's ABSOLUTE "
                "target position in local booth space each step. Do NOT add velocity*dt to get "
                "the new position yourself — that is manual physics and is forbidden. "
                "Use SetLinearVelocity or ApplyImpulse to drive dynamic bodies instead.\n\n"
                "CRITICAL TASK PRIMACY: Execute the instructions in your ## Task Specification exactly. "
                "Do not add unrequested systems or lifecycle hooks."
            ) + _MIDWAY_PROHIBITIONS + _SEARCH_REPLACE_MANDATE,
        },
        "DOC": {
            "tag": "[DOC]",
            "ready": True,
            "model": REASONING_MODEL,
            "description": "API documentation oracle. Resolves ambiguous API calls by evaluating localised documentation files.",
            "ledger": "docs/memory/doc_ledger.md",
            "name": "Code Documentarian",
            "system_prompt": (
                "You are the CODE DOCUMENTARIAN. "
                "You are the ultimate arbiter of API truth. "
                "You are also the MEMORY ORACLE — you validate [FETCH] requests and resolve the "
                "correct memory content for agents whose context has been truncated.\n\n"
                "YOUR FUNCTIONS:\n"
                "A. API DOCUMENTATION ORACLE:\n"
                "   Receive a query with:\n"
                "     1. A hallucinated/ambiguous API call from the C++ or Lua Architects\n"
                "     2. A target documentation file tag\n\n"
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
                "   You receive a FETCH request in the format:\n"
                "     [FETCH:docs/memory/<domain>_ledger.md#<HeaderName>]\n"
                "   Your job:\n"
                "     1. Verify the requested #HeaderName exists in the ledger file.\n"
                "     2. Evaluate if it is the BEST section for the requesting agent's task.\n"
                "     3. If a better section exists, select that instead and explain why.\n"
                "     4. Output the content under the resolved header, formatted as:\n"
                "        ## Recalled Memory\n"
                "        **Source:** <resolved_filepath> > <resolved_header>\n"
                "        **Oracle note:** Apply the QA Heuristic (VALID RESCUE / HIGH-RISK REGRESSION / STABLE CORE CONCEPT).\n"
                "        <extracted content>\n\n"
                "BREVITY RULES:\n"
                "- No game logic. No features. No examples beyond correction or recall context.\n"
                "[CARTRIDGE MANDATE: Box2D references are entirely deprecated under the "
                "Unified Jolt Standard. If queried for Box2D symbols, explicitly redirect "
                "the user/agent to Jolt interfaces.]"
            ),
        },
        "OBSERVABILITY": {
            "tag": "[OBSERVABILITY]",
            "ready": True,
            "model": EXECUTION_MODEL,
            "description": "Independent instrumentation pass. Injects mandatory logging without altering core logic.",
            "ledger": "docs/memory/qa_ledger.md",
            "name": "Observability Auditor",
            "system_prompt": (
                "You are the Lead Observability Auditor for 'Midway to Nowhere'. "
                "Your EXCLUSIVE directive is to instrument existing code blocks with mandatory "
                "log statements to satisfy the OBSERVABILITY MANDATE.\n\n"
                "CRITICAL LANGUAGE ENFORCEMENT:\n"
                "1. For [.lua] files: write pure Lua 5.4 using ONLY native `print(...)` for logging. "
                "Concatenate strings and explicitly coerce tables using `tostring()`. "
                "FORBIDDEN: sol.log_message, sol.log, MidwayPhysics.log_message — they do NOT exist "
                "in the engine bridge and will cause phantom API failures.\n"
                "2. For [.cpp/.h/.hpp] files: write pure C++17 using ONLY the custom logger "
                "(e.g. `log_info(...)` or `log_error(...)`).\n"
                "3. You are strictly FORBIDDEN from altering existing business logic, "
                "physics variables, or function return structures."
            ) + _MIDWAY_PROHIBITIONS + _OBS_MANDATE,
        },
        "CONF": {
            "tag": "[CONF]",
            "ready": True,
            "model": REASONING_MODEL,
            "description": "Conflict resolution mediator. Resolves VETO/OBJECT disputes between agents.",
            "ledger": "docs/memory/conf_ledger.md",
            "name": "Conflict Resolution",
            "system_prompt": (
                "You are the CONFLICT RESOLUTION AGENT for 'Midway to Nowhere'. "
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
                "CRITICAL RULE: Preserve feature intent over technical purity. "
                "If Agent A's 'fix' strips gameplay mechanics, narrative flavour, or modifier "
                "interactions, sustain the VETO."
            ),
        },
        "TRIBUNAL": {
            "tag": "[TRIBUNAL]",
            "ready": True,
            "model": REASONING_MODEL,
            "description": "Appellate Court — blind-reviews APPEAL signals between Coders and Reviewers",
            "ledger": "docs/memory/tribunal_ledger.md",
            "name": "Tribunal",
            "system_prompt": (
                "You are the TRIBUNAL AGENT for 'Midway to Nowhere'. "
                "You are a neutral appellate arbiter. You do NOT write code.\n\n"
                "When you receive an appeal:\n"
                "1. Read the Coder's code\n"
                "2. Read the Reviewer's critique\n"
                "3. Read the Coder's defence\n"
                "4. Evaluate both arguments against the original feature request\n"
                "5. Render a binding verdict\n\n"
                "Verdict options:\n"
                "- [MERGE:Agent:Justification]: The Coder's implementation is correct.\n"
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
            "description": "GDD knowledge retrieval, document querying, cross-reference, and ledger audit",
            "ledger": "docs/memory/doc_ledger.md",
            "ledger_rule": "MUST cite source file and line range for every decision",
            "name": "Librarian",
            "system_prompt": (
                "You are the GDD Librarian for 'Midway to Nowhere'. "
                "Your role is to search, summarise, and retrieve information from the "
                "Game Design Document (GDD) and other documentation. "
                "Cross-reference multiple sources when answering. "
                "Always cite which GDD section you are quoting.\n\n"
                "## Past Session Search\n"
                "Use the Session Timeline (docs/memory/session_timeline.md) to search "
                "for previous decisions when the question references recent pipeline activity.\n"
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

    # Legacy uppercase alias
    if "LUA" not in registry and "Lua" in registry:
        registry["LUA"] = registry["Lua"]

    # Extra Midway-specific domains
    if "NET" not in registry:
        registry["NET"] = {
            "tag": "[NET]",
            "ready": False,
            "name": "Network Engineer",
            "model": EXECUTION_MODEL,
            "allowed_extensions": [".cpp", ".h", ".hpp"],
            "description": "Network state replication and server-authoritative RPCs",
            "ledger": "docs/memory/net_ledger.md",
            "system_prompt": (
                "You are the Network Engineer for 'Midway to Nowhere'. "
                "Write ONLY C++17. Implement server-authoritative state replication, "
                "delta compression, and RPC security for the Vicious Cycle multiplayer loop.\n\n"
                "RULES:\n"
                "- All state mutations MUST originate server-side. Clients are display-only.\n"
                "- Use the existing Jolt Physics handles (integers) as network object IDs. "
                "Never transmit raw JPH::BodyID values over the wire.\n"
                "- Network layer must tolerate Vicious Cycle Z=0 teleport events without "
                "dropping or corrupting object state — but do NOT implement seam logic; "
                "the seam is handled by the engine, not the network layer.\n"
                "- CRITICAL TASK PRIMACY: Execute the ## Task Specification exactly."
            ) + _MIDWAY_PROHIBITIONS + _SEARCH_REPLACE_MANDATE,
        }

    if "REVIEWER" not in registry:
        registry["REVIEWER"] = {
            "tag": "[REVIEWER]",
            "ready": True,
            "name": "Integration Reviewer",
            "model": REVIEWER_MODEL,
            "allowed_extensions": [],
            "description": "Integration code review against Midway engine rules",
            "ledger": "docs/memory/reviewer_ledger.md",
            "system_prompt": (
                "You are the Integration Reviewer for 'Midway to Nowhere'. "
                "Your sole responsibility is to validate agent outputs against the project's "
                "technical standards. You do NOT implement features. You do NOT rewrite code unprompted.\n\n"
                "REVIEW CHECKLIST — apply every item to every submission:\n"
                "1. SEARCH/REPLACE FORMAT: Every code change MUST use the SEARCH/REPLACE block format. "
                "Reject any output using `diff --git`, unified diffs, or inline commentary changes.\n"
                "2. JOLT STANDARD: No Box2D API references anywhere. All physics MUST use Jolt. "
                "Flag any jpBody, b2World, b2Vec2, or Box2D header includes.\n"
                "3. SOL2 BINDINGS: C++ agents MUST expose physics primitives via sol2 lambdas wrapping "
                "`MidwayPhysics::Spawn*`. No raw JPH::BodyID in Lua-accessible code.\n"
                "4. LUA LIFECYCLE: Lua attraction scripts MUST implement OnLoad / OnStep(dt) / OnUnload. "
                "Flag any script missing these entry points.\n"
                "5. VICIOUS CYCLE SEAM CONTAMINATION (CRITICAL): Lua attraction scripts are "
                "COMPLETELY ISOLATED from the spatial seam. Flag any Lua output that contains "
                "teleport thresholds, Z-axis boundary checks, seam trigger logic, or "
                "OptimizeBroadPhase() calls. These are ALWAYS a [VETO].\n"
                "6. LUA MANUAL PHYSICS (CRITICAL): Lua scripts MUST NOT contain raw position "
                "arithmetic, euler/verlet integration, collision math, or any physics simulation "
                "written in pure Lua. All physics MUST go through MidwayPhysics host API calls. "
                "This is ALWAYS a [VETO].\n"
                "7. PHANTOM APIS (CRITICAL): Flag any call to a MidwayPhysics, Engine, or sol "
                "function that does NOT appear in the known bridge contract. This is ALWAYS a [VETO].\n"
                "8. DELEGATE GUARD: Reject any response that consists solely of a [DELEGATE] signal "
                "with no implementation output. This is ALWAYS a [VETO].\n"
                "9. EMPTY OUTPUT GUARD: Reject any response that contains only prose and no "
                "SEARCH/REPLACE block. This is ALWAYS a [VETO].\n"
                "10. SCOPE: Verify the implementation addresses the task specification — "
                "no missing steps, no unrequested additions.\n\n"
                "OUTPUT FORMAT:\n"
                "- Correct output: emit [APPROVE] + one-line summary of what was verified.\n"
                "- Rule violation: emit [VETO:Agent:Justification] citing the specific checklist item.\n"
                "- Minor issue: emit [OBJECT:Agent:Justification].\n"
                "CRITICAL: Never emit both [APPROVE] and [VETO] in the same response."
            ),
        }

    return registry


# ── Alias map ────────────────────────────────────────────────────────────────

def build_alias_map(kernel_alias_map: dict) -> Dict[str, str]:
    """Return the full Midway agent alias map (extends kernel map)."""
    midway_aliases = {
        # C++ domain
        "c++": "C++", "c++ core": "C++", "c++ engineer": "C++",
        "c++ systems engineer": "C++", "cpp": "C++", "cpp core": "C++",
        "CPP": "C++", "C++": "C++", "engine": "C++",
        "engine architect": "C++", "engine systems": "C++",
        "engine programmer": "C++", "systems engineer": "C++",
        "c++ programmer": "C++",
        # Physics domain
        "phys": "PHYS", "PHYS": "PHYS", "physics": "PHYS",
        "PHYSICS": "PHYS", "physics architect": "PHYS",
        "physics engineer": "PHYS", "jolt physics": "PHYS",
        "unified jolt": "PHYS", "physics systems": "PHYS",
        "lead physics": "PHYS", "physics programmer": "PHYS",
        "teleport physics": "PHYS",
        # Shader domain
        "shader": "SHADER", "SHADER": "SHADER", "shader expert": "SHADER",
        "glsl": "SHADER", "GLSL": "SHADER", "rendering": "SHADER",
        "rendering expert": "SHADER", "graphics": "SHADER",
        "graphics programmer": "SHADER", "opengl": "SHADER",
        "shader programmer": "SHADER", "render": "SHADER",
        # Lua domain
        "lua": "Lua", "LUA": "Lua", "lua scripter": "Lua",
        "lua gameplay": "Lua", "gameplay scripter": "Lua",
        "scripting": "Lua", "lua programmer": "Lua",
        "attractions script": "Lua", "attraction scripting": "Lua",
        "gameplay": "Lua", "lua engineer": "Lua", "lua developer": "Lua",
        # Network domain
        "net": "NET", "NET": "NET", "network": "NET",
        "NETWORK": "NET", "network engineer": "NET",
    }
    combined = dict(kernel_alias_map)
    combined.update(midway_aliases)
    return combined


# ── Environment metadata ──────────────────────────────────────────────────────

def build_environment_metadata() -> Dict[str, Dict[str, str]]:
    """Return per-domain environment metadata with architectural invariants."""
    return {
        "C++": {
            "language": "C++",
            "test_framework": "C++ Google Test (gtest)",
            "code_tag": "cpp",
            "extension": ".cpp",
            "assert_examples": "Use EXPECT_EQ, EXPECT_NEAR, ASSERT_TRUE, etc.",
            "architectural_invariant": (
                "Core Host Engine strictly. MUST remain 100% agnostic to modular business logic. "
                "Expose generic APIs, triggers, and object pools only. Hardcoding plugin-specific "
                "constants or gameplay state machines is strictly forbidden."
            ),
        },
        "LUA": {
            "language": "Lua",
            "test_framework": "Lua Busted (busted)",
            "code_tag": "lua",
            "extension": ".lua",
            "assert_examples": "Use assert.are.equal, assert.is_true, assert.is_near, etc.",
            "architectural_invariant": (
                "Modular Plugin Layer. Fully authoritative over game rules, local dimensions, "
                "spawns, and scoring. Must consume generic host APIs to interact with the world."
            ),
        },
        "Lua": {
            "language": "Lua",
            "test_framework": "Lua Busted (busted)",
            "code_tag": "lua",
            "extension": ".lua",
            "assert_examples": "Use assert.are.equal, assert.is_true, assert.is_near, etc.",
            "architectural_invariant": (
                "Modular Plugin Layer. Fully authoritative over game rules, local dimensions, "
                "spawns, and scoring. Must consume generic host APIs to interact with the world."
            ),
        },
        "PHYS": {
            "language": "C++",
            "test_framework": "C++ Google Test (gtest)",
            "code_tag": "cpp",
            "extension": ".cpp",
            "assert_examples": "Use EXPECT_EQ, EXPECT_NEAR, with rigorous numerical tolerance.",
            "architectural_invariant": (
                "Unified Jolt Physics standard exclusively. Box2D fully deprecated. "
                "2D planar attractions modeled via DOF locks (Z-translation locked, X/Y rotation locked, Z-only rotation). "
                "3D volumetric attractions use unconstrained Jolt rigid body configurations. "
                "Sol2 integer handle bindings strictly enforced — no raw JPH::BodyID in Lua. "
                "Vicious Cycle seam: |playerZ| >= 150.0f triggers Z=0 teleport, layer re-index, "
                "kinematic recalculation, and OptimizeBroadPhase()."
            ),
        },
    }


# ── Domain rules ──────────────────────────────────────────────────────────────

def build_domain_rules() -> Dict[str, Dict[str, Any]]:
    """Return consolidated rule checklists for every pipeline agent domain."""
    return {
        "C++": {
            "language_build": [
                "C++17 only. No C++20 features. No exceptions (-fno-exceptions). CMake build.",
                "Stack: SDL2 (window/input), OpenGL 3.3+ (rendering), GLEW, GLM, nlohmann/json.",
                "Physics: Jolt Physics for ALL attractions (Unified Jolt Standard). Box2D fully deprecated.",
                "Lua bridge: sol2 v3 bindings. Every Lua-accessible fn registered in MidwayPhysics.cpp or Engine.cpp.",
            ],
            "vicious_cycle_seam": [
                "Teleport trigger: |playerZ| >= CYCLE_LENGTH (150.0f) → Z=0 with zero visual interruption.",
                "Lap counter: increment m_viciousCycleLap on each seam crossing.",
                "All dynamic bodies re-positioned by teleport offset. Static bodies (booths) re-indexed, not moved.",
                "Post-teleport: OptimizeBroadPhase() after static body re-indexing.",
            ],
            "slot_booth_architecture": [
                "Booth lifecycle: BeginBoothCapture(staticIDs, slotID) → spawn → EndBoothCapture() → OptimizeBroadPhase().",
                "Dynamic lifecycle: BeginDynamicCapture(dynamicIDs, slotID) → spawn → EndDynamicCapture().",
                "Physics culling: suspend simulation for slots > 14.0m interaction radius. Destroy dynamics on walk-away.",
                "Coordinate transform: LocalToWorld(lx, lz, wx, wz) applies booth transform.",
            ],
            "body_handle_management": [
                "Handle map: return int handles to Lua (0 = invalid). Never expose raw JPH::BodyID.",
                "Object pools: CreatePool(name, hotN, coldN, params). Pool names unique per slot: {attraction}_{type}_{slotID}.",
                "Per-slot callbacks via SetSlotCallback(slotID, fn). Each slot gets OnStep(dt).",
            ],
            "modifier_system": [
                "Live sync: SyncModifierGlobalsToLua() runs every frame. 9 values synced: mass, volume, friction, karma, luck, persuasion, heat, sleightOfHand, nerve.",
                "Persistence: Load from modifier_state.json at startup. Save on shutdown. F1 panel changes live and auto-saved.",
            ],
            "rendering_shaders": [
                "Carnival Barker sprite uses billboard transform. 2D high-res sprite only.",
                "Karmic-Temporal Transmutation Matrix: dual-axis GLSL shader (X = PS1 snap ↔ smooth PBR, Y = Demonic ↔ Angelic).",
                "Demonic Skew: 3D noise-driven vertex displacement at extreme negative Karma.",
                "Barker's Exemption: 2D sprite ignores Transmutation Matrix entirely.",
            ],
            "memory_performance": [
                "No raw new/delete. Use unique_ptr, vector, map. RAII for all resources.",
                "Physics culling: simulate only active + adjacent slots. Max active bodies per slot: 200.",
                "Steam Deck target: 1280x720. Minimize draw calls. Use instancing for repeated geometry.",
            ],
        },
        "LUA": {
            "language_environment": [
                "Lua 5.4 via sol2. No LuaJIT features. No external Lua modules — only MidwayPhysics and Engine APIs.",
                "Self-contained constants. Every attraction defines LOCAL config tables. Never import shared tuning files.",
                "Game tuning values (pusher stroke, shelf height, spawn counts) belong in local constants, never in C++ bridge.",
            ],
            "script_lifecycle": [
                "OnLoadStatic(): call SpawnSharedBooth() first, then spawn permanent cabinet geometry.",
                "OnLoad(): spawn kinematic/dynamic bodies. Register OnStep callback via MidwayPhysics.OnStep(fn).",
                "OnUnload(): optional cleanup. Engine destroys dynamic bodies automatically.",
                "OnStep(dt): read AttractionConstants.modifiers every frame. Never cache modifier values at load time.",
            ],
            "coordinate_system": [
                "Local booth space: origin = floor center. +X = right, +Y = up, +Z = back. Opening faces -Z.",
                "1 engine unit = 1 meter. Engine auto-transforms local coords to world space.",
            ],
            "canonical_dimensions": [
                "Booth shell: width_x = 9.0, height_y = 9.0, depth_z = 15.0.",
                "Booth center offset: BOOTH_SIDE_X = ±10.5, BOOTH_SPACING = 15.0.",
                "Corridor: CORRIDOR_HALF_WIDTH = 6.0.",
            ],
            "engine_globals": [
                "Slot identity: BOOTH_SLOT_ID (int), BOOTH_WORLD_X (float), BOOTH_WORLD_Z (float), BOOTH_IS_STATIC (bool).",
                "Modifier globals: ENGINE_MOD_MASS, _VOLUME, _FRICTION, _KARMA, _LUCK, _PERSUASION, _HEAT, _SLEIGHT_OF_HAND, _NERVE.",
                "Preferred access: read from AttractionConstants.modifiers.* (snake_case).",
            ],
            "midwayphysics_api": [
                "Spawn: SpawnStaticBox/Sphere/Capsule/Cylinder/Mesh, SpawnKinematic*, SpawnDynamic*.",
                "Sensors: SpawnSensorBox/Sphere. Movement: MoveKinematic. Impulses: ApplyImpulse, AddLinearVelocity.",
                "Per-body overrides: SetFriction, SetRestitution, SetGravityFactor, SetMass, SetLinearDamping, SetAngularDamping.",
            ],
            "object_pools": [
                "CreatePool(unique_name_N, hotN, coldN, paramsTable). Pool name: {attraction}_{type}_{slotID}.",
                "Acquire/Return: PoolAcquire(name, lx, ly, lz) → handle. PoolReturn(name, handle).",
                "Culling: PoolCullBelow(name, yThreshold). Query: PoolFree, PoolTotal.",
            ],
            "economy_api": [
                "Engine.AwardTickets(amount, LABEL), Engine.AwardTokens(amount, LABEL).",
                "Read: Engine.GetTickets(), Engine.GetTokens(), Engine.GetStreak().",
                "Win banners: 3.5s display, fade last 1s. Multiple stack vertically.",
            ],
            "modifier_integration": [
                "Read MOD = AttractionConstants.modifiers inside OnStep each frame.",
                "Mass→kinetic transfer, Volume→hitbox size, Friction→bounce/restitution.",
                "Karma→RNG tilt, Luck→procedural generation, Heat→difficulty scaling.",
                "Sleight of Hand→TILT mechanic, Nerve→timing windows.",
            ],
        },
        "PHYS": {
            "engine_selection": [
                "UNIFIED JOLT STANDARD: ALL attractions use Jolt Physics exclusively — 3D volumetric and 2D planar alike.",
                "Box2D is FULLY DEPRECATED. Do NOT write Box2D initialization vectors, variables, or API references.",
                "2D planar attractions: modeled via Jolt DOF constraints (Z-translation locked, X/Y rotation locked, Z-only rotation).",
                "3D volumetric attractions: unconstrained Jolt rigid bodies.",
            ],
            "vicious_cycle_teleport": [
                "Check |playerZ| >= 150.0f every physics step. Teleport to Z=0, increment lap counter.",
                "Re-index static booth bodies relative to new Z=0 origin.",
                "Recalculate kinematic motion paths post-teleport — no spatial drift.",
                "Dynamic bodies crossing seam teleport with player. Call OptimizeBroadPhase() after re-index.",
            ],
            "booth_lifecycle_physics": [
                "BeginBoothCapture(slotID) → spawn statics → EndBoothCapture() → OptimizeBroadPhase().",
                "BeginDynamicCapture(slotID) → spawn dynamics → EndDynamicCapture().",
                "Walk-away: DestroyBodies() for slot's dynamic list. ActivateBodies/DeactivateBodies for statics.",
            ],
            "body_handle_management_phys": [
                "All spawn functions return int handles. 0 = invalid. Never expose JPH::BodyID.",
                "Handle map uses vector<JPH::BodyID> with m_nextHandle counter. Handle 0 reserved.",
                "DestroyBody(handle) removes from handle map AND physics system. No dangling handles.",
            ],
            "object_pools_two_tier": [
                "CreatePool(name, hotN, coldN, params): hot = active bodies, cold = parked at Y=-9999.",
                "PoolAcquire(name, lx, ly, lz), PoolReturn, PoolCullBelow(yThreshold).",
                "Pool name pattern: {attraction}_{type}_{slotID}.",
            ],
            "per_body_overrides": [
                "SetFriction(0.0-1.0), SetRestitution(0.0-1.0), SetGravityFactor, SetMass(>0.0).",
                "SetLinearDamping, SetAngularDamping, MoveKinematic(handle, lx, ly, lz, dt).",
            ],
            "collision_layers": [
                "NON_MOVING (0) = static. MOVING (1) = dynamic/kinematic. Sensors use MOVING.",
                "ObjLayerPairFilterImpl: NON_MOVING↔MOVING = collide. MOVING↔MOVING = collide.",
            ],
            "performance_phys": [
                "Physics culling: 14.0m interaction radius. DeactivateBodies() for off-screen booths.",
                "Max active bodies per slot: 200. 60 Hz fixed timestep accumulator.",
                "OptimizeBroadPhase() after batch static load.",
            ],
        },
        "SHADER": {
            "karmic_temporal_matrix": [
                "Dual-axis GLSL shader. X-axis: PS1 vertex snapping (left) ↔ smooth PBR (right).",
                "Y-axis: Demonic (bottom) ↔ Angelic (top). Origin (0,0) = max distortion.",
                "Driven by player's Karma modifier. Single shader with uniform interpolation.",
            ],
            "ps1_vertex_snapping": [
                "Vertex positions snapped to coarse grid in clip space (~1/256 range).",
                "Applies to all world geometry (not UI or Barker). Nearest-neighbor texture sampling when snap > 50%.",
            ],
            "demonic_visuals": [
                "Demonic Skew: 3D noise-driven vertex displacement (world space, ~0.5-2.0 Hz).",
                "Film grain, bruised skies (deep purple/crimson), flickering shadows, chromatic aberration (max 2px), vignette.",
            ],
            "angelic_visuals": [
                "Warm bloom (threshold luminance > 0.7), lens flare (3-5 ghost taps), soft lighting.",
                "Color grading (warm tint, saturation boost), god rays (16-32 ray march steps).",
            ],
            "barker_exemption": [
                "Barker's 2D sprite completely ignores Transmutation Matrix. Full resolution, no snapping, no grain.",
                "Rendered after matrix pass with override uniforms. Highest draw order. UI also ignores matrix.",
            ],
            "rendering_pipeline": [
                "OpenGL 3.3+ core profile. GLEW, SDL2. VBOs/IBOs/VAOs. UBOs for per-frame uniforms.",
                "Steam Deck target: 30 FPS min. 720p internal upscaled to 800p. Max 50 draw calls.",
                "Shadow mapping: single directional light, max 2048x2048.",
                "Max 100k vertices per frame. LOD system for distant geometry.",
            ],
        },
        "NET": {
            "authority_model": [
                "Server-authoritative. All state mutations validated by server.",
                "Client-side prediction for player movement. Server reconciliation corrects each tick.",
                "No client authority over economy. Engine.AwardTickets/Tokens server-only.",
            ],
            "transport_protocol": [
                "UDP transport. Sequenced unreliable for position/velocity. Reliable ordered for RPCs.",
                "Delta compression: changed state per tick. Full snapshots every 30th tick for late-joiners.",
                "Bandwidth budget: cap per-client. Prioritize active slot physics.",
            ],
            "vicious_cycle_sync": [
                "Server broadcasts lap count + teleport frame. Clients do NOT independently detect seam.",
                "Server broadcasts new booth-to-slot mapping on wrap. Force resync if client Z diverges > 1.0m.",
            ],
            "state_replication": [
                "Replicate position/velocity/type for active bodies in current + adjacent slots (14.0m radius).",
                "Do NOT replicate individual pool slot states — reconstruct from authoritative positions.",
                "Broadcast 9 modifier values as single compact packet.",
            ],
            "rpc_security": [
                "Authenticate all RPCs with session token. Rate-limit 10/sec per client.",
                "Validate parameters server-side. Never trust client-provided positions/scores.",
                "TILT mechanic impulse clamped server-side.",
            ],
            "death_streak_protocol": [
                "Death's Door server-authoritative. Streak Protocol server-tracked.",
                "Parasitic Prizes (Curses) server-assigned. Exorcism server-validated.",
            ],
        },
        "REVIEWER": {
            "api_contract_verification": [
                "Every Lua API function called must be registered in C++ via sol2 — signatures must match exactly.",
                "All handles returned to Lua are integers (0 = invalid). No raw JPH::BodyID leaks.",
                "Pool names in Lua match C++ routing key: {attraction}_{type}_{slotID}.",
                "SyncModifierGlobalsToLua() sets all 9 globals. Lua reads from AttractionConstants.modifiers each frame.",
            ],
            "feature_completeness": [
                "No orphaned tasks — every breakdown task corresponds to generated code.",
                "No phantom APIs — Lua does not call non-existent C++ bridge functions.",
                "NET wraps C++ — state replication covers bodies/events from C++ and Lua tasks.",
                "Economy events wrapped by server-authoritative NET layer.",
            ],
            "coordinate_alignment": [
                "Lua positions in local booth space (+X right, +Y up, +Z back). C++ LocalToWorld applies booth offset.",
                "All Lua geometry within 9.0 x 9.0 x 15.0m. Booth lifecycle uses Begin/EndBoothCapture.",
                "ALL attractions use Jolt (Unified Standard). Box2D references are invalid.",
            ],
            "vicious_cycle_review": [
                "NET broadcasts lap count + teleport frame. C++ applies teleport. Lua does NOT independently detect seam.",
                "C++ re-indexes static bodies. NET broadcasts new mapping. Lua receives updated BOOTH_WORLD_X/Z.",
            ],
            "modifier_system_review": [
                "All 9 values present in C++ sync, Lua read, and NET broadcast.",
                "F1 panel changes propagate to Lua every frame. NET broadcasts to remote clients.",
            ],
            "error_handling_security": [
                "No raw pointers in C++ — unique_ptr, vector, map. No new/delete.",
                "RPC authentication includes session token. Rate-limited 10/sec per client.",
                "Server-authoritative economy and Death's Door.",
            ],
        },
    }


# ── API references ────────────────────────────────────────────────────────────

def build_api_references(prefix: str = "docs") -> Dict[str, Dict[str, Any]]:
    """Return structured index of API documentation files.

    ``prefix`` is accepted for backwards-compatibility but all paths now resolve
    to the canonical sister-repo location (repos/midway/docs).  Callers that
    previously passed a relative prefix will still receive a valid dict; the
    ``path`` values are absolute so agents can open them without guessing.
    """
    from pathlib import Path as _Path
    _docs = _Path(__file__).resolve().parents[2] / "midway" / "docs"

    def _p(filename: str) -> str:
        return str(_docs / filename)

    return {
        # ── Scraped external API docs ────────────────────────────────────────
        "jolt": {
            "label": "Jolt Physics API",
            "path": _p("jolt_api.md"),
            "anchors": {
                "PhysicsSystem": "#physicssystem",
                "BodyInterface": "#bodyinterface",
                "BodyCreationSettings": "#bodycreationsettings",
                "ObjectLayerFilter": "#objectlayerfilter",
                "BroadPhaseLayer": "#broadphaselayerinterface",
                "ConstraintSettings": "#constraintsettings",
                "Body": "#body",
                "BodyID": "#bodyid",
                "EMotionType": "#emotiontype",
            },
            "search_terms": [
                "physics system", "body interface", "body creation",
                "shape settings", "constraints", "body id",
                "broadphase", "collision layers", "motion type",
            ],
        },
        "sol2": {
            "label": "sol2 Lua Binding API",
            "path": _p("sol2_api.md"),
            "anchors": {
                "sol::state": "#sol-state",
                "sol::state_view": "#sol-state-view",
                "sol::function": "#sol-function",
                "sol::table": "#sol-table",
                "sol::object": "#sol-object",
                "new_usertype": "#new-usertype",
                "set_function": "#set-function",
                "pointer_safety": "#pointer-safety",
            },
            "search_terms": [
                "lua state", "binding", "function registration",
                "usertype", "metatable", "ownership",
            ],
        },
        "cpp17": {
            "label": "C++17 Standard Library",
            "path": _p("cpp17_api.md"),
            "anchors": {
                "StructuredBindings": "#structured-bindings",
                "if_constexpr": "#if-constexpr",
                "FoldExpressions": "#fold-expressions",
                "InlineVariables": "#inline-variables",
                "filesystem": "#std-filesystem",
                "optional": "#std-optional",
                "variant": "#std-variant",
                "string_view": "#std-string-view",
                "shared_mutex": "#std-shared-mutex",
                "clamp": "#std-clamp",
            },
            "search_terms": [
                "filesystem", "optional", "variant", "string_view",
                "structured bindings", "constexpr", "fold expression",
            ],
        },
        "opengl_sdl": {
            "label": "OpenGL 3.3 + SDL2 API",
            "path": _p("opengl_sdl_api.md"),
            "anchors": {
                "SDL_CreateWindow": "#sdl-createwindow",
                "SDL_GL_CreateContext": "#sdl-gl-createcontext",
                "SDL_PollEvent": "#sdl-pollevent",
                "glCreateShader": "#gl-createshader",
                "glCreateProgram": "#gl-createprogram",
                "glGenVertexArrays": "#gl-genvertexarrays",
                "glBufferData": "#gl-bufferdata",
            },
            "search_terms": [
                "window creation", "opengl context", "shader compilation",
                "vertex array", "uniform location", "event loop",
            ],
        },
        "box2d": {
            "label": "Box2D API (deprecated — reference only)",
            "path": _p("box2d_api.md"),
            "search_terms": ["box2d", "b2World", "b2Body"],
        },
        # ── Project-authored reference docs ──────────────────────────────────
        "engine_bridge": {
            "label": "Engine↔Lua Bridge Contract",
            "path": _p("engine_lua_bridge_contract.md"),
            "search_terms": [
                "bridge contract", "midwayphysics api", "spawn",
                "modifier bridge", "economy api", "callbacks",
            ],
        },
        "api_index": {
            "label": "Master API Index",
            "path": _p("api_index.md"),
            "search_terms": ["api index", "symbol index", "function list"],
        },
        "internal_api_ledger": {
            "label": "Internal API Ledger",
            "path": _p("internal_api_ledger.md"),
            "search_terms": ["ledger", "internal api", "registered functions"],
        },
        "attraction_specs": {
            "label": "Attraction Specifications",
            "path": _p("attraction_specs.md"),
            "search_terms": [
                "attraction", "booth", "skeeball", "coin cascade",
                "mini golf", "carnival", "slot machine",
            ],
        },
        "pipeline_anchor_index": {
            "label": "Pipeline Anchor Index",
            "path": _p("pipeline_anchor_index.md"),
            "search_terms": ["anchor", "pipeline index", "agent anchor"],
        },
        # ── Rules docs ────────────────────────────────────────────────────────
        "rules_cpp": {
            "label": "C++ Coding Rules",
            "path": _p("rules_cpp.md"),
            "search_terms": ["cpp rules", "c++ standard", "coding mandates"],
        },
        "rules_lua": {
            "label": "Lua Scripting Rules",
            "path": _p("rules_lua.md"),
            "search_terms": ["lua rules", "lua scripting", "sol2 rules"],
        },
        "rules_phys": {
            "label": "Physics Rules",
            "path": _p("rules_phys.md"),
            "search_terms": ["physics rules", "jolt constraints", "collision rules"],
        },
        "rules_review": {
            "label": "Code Review Rules",
            "path": _p("rules_review.md"),
            "search_terms": ["review rules", "code review", "reviewer mandates"],
        },
        "rules_shader": {
            "label": "Shader Rules",
            "path": _p("rules_shader.md"),
            "search_terms": ["shader rules", "glsl", "vertex shader", "fragment shader"],
        },
        "rules_net": {
            "label": "Networking Rules",
            "path": _p("rules_net.md"),
            "search_terms": ["networking rules", "net code", "multiplayer"],
        },
    }


# ── Bridge contract ───────────────────────────────────────────────────────────

def build_bridge_contract() -> Dict[str, Any]:
    """Return the consolidated Engine↔Lua bridge contract."""
    return {
        "globals_injected": {
            "BOOTH_WORLD_X": "float — world-space X center of this slot",
            "BOOTH_WORLD_Z": "float — world-space Z center of this slot",
            "BOOTH_SLOT_ID": "int — unique slot identifier",
            "BOOTH_IS_STATIC": "bool — true during static load, false during dynamic load",
        },
        "modifier_globals": {
            "ENGINE_MOD_MASS":           {"gdd": "§4.1 Core Physical", "default": 1.0},
            "ENGINE_MOD_VOLUME":         {"gdd": "§4.1 Core Physical", "default": 1.0},
            "ENGINE_MOD_FRICTION":       {"gdd": "§4.1 Core Physical", "default": 1.0},
            "ENGINE_MOD_KARMA":          {"gdd": "§4.2 Meta-Navigational", "default": 0.0, "range": "-1..1"},
            "ENGINE_MOD_LUCK":           {"gdd": "§4.2 Meta-Navigational", "default": 0.0},
            "ENGINE_MOD_PERSUASION":     {"gdd": "§4.2 Meta-Navigational", "default": 0.0},
            "ENGINE_MOD_HEAT":           {"gdd": "§4.2 Meta-Navigational", "default": 0.0},
            "ENGINE_MOD_SLEIGHT_OF_HAND": {"gdd": "§4.3 Tactile", "default": 0.0},
            "ENGINE_MOD_NERVE":          {"gdd": "§4.3 Tactile", "default": 0.0},
        },
        "load_order": [
            "1. attractions/_shared/attraction_constants.lua — canonical dimensions, tuning, live modifiers",
            "2. attractions/booth_shared.lua — SpawnSharedBooth() helper and SharedBooth utilities",
            "3. Attraction script — OnLoadStatic() for permanent geometry, OnLoad() for gameplay bodies",
        ],
        "script_lifecycle": {
            "OnLoadStatic": "Call SpawnSharedBooth() first, then spawn cabinet-specific static geometry.",
            "OnLoad": "Spawn kinematic/dynamic bodies. Register OnStep callback via MidwayPhysics.OnStep(fn).",
            "OnUnload": "Optional cleanup. Engine destroys dynamic bodies automatically.",
            "OnStep_dt": "Read AttractionConstants.modifiers each frame. Never cache at load time.",
        },
        "midwayphysics_spawn_api": {
            "SpawnStaticBox/Sphere/Capsule/Cylinder/Mesh": "Permanent geometry (lx, ly, lz, dimensions...) → handle",
            "SpawnKinematicBox/Sphere/Capsule/Cylinder": "Moving platforms, pushers (lx, ly, lz, dimensions...) → handle",
            "SpawnDynamicBox/Sphere/Capsule/Cylinder/Mesh": "Physics-simulated objects (lx, ly, lz, dimensions, [mass]) → handle",
            "SpawnSensorBox/Sphere": "Trigger zones (lx, ly, lz, dimensions) → handle",
            "MoveKinematic(handle, lx, ly, lz, dt)": "Sets kinematic body target position each step.",
            "ApplyImpulse(handle, ix, iy, iz)": "Instantaneous force in local space.",
            "ApplyAngularImpulse(handle, ix, iy, iz)": "Instantaneous torque.",
            "SetLinearVelocity(handle, vx, vy, vz)": "Override current velocity.",
            "AddLinearVelocity(handle, vx, vy, vz)": "Additive velocity change.",
            "SetFriction/Restitution/GravityFactor/Mass/LinearDamping/AngularDamping": "Per-body property overrides.",
            "GetPosition(handle) → lx, ly, lz": "Local-space position relative to booth origin.",
            "GetVelocity(handle) → vx, vy, vz": "Local-space velocity.",
            "IsActive(handle) → bool": "False for destroyed or parked bodies.",
            "IsSensorTriggered(handle) → bool": "Overlap state from previous physics step.",
            "DestroyBody(handle)": "Remove from handle map AND physics system.",
        },
        "object_pools": {
            "CreatePool(name, hotN, coldN, paramsTable)": "Two-tier pool. params: shape, w, h, d, radius, halfH, mass, friction, restitution, damping.",
            "PoolAcquire(name, lx, ly, lz) → handle": "0 = pool exhausted.",
            "PoolReturn(name, handle)": "Park body at Y=-9999.",
            "PoolCullBelow(name, yThreshold)": "Return hot bodies below threshold to cold store.",
            "PoolFree(name) → int": "Available hot slots.",
            "PoolTotal(name) → int": "Hot + cold slots.",
            "Naming convention": "{attraction}_{type}_{slotID} (e.g. plinko_balls_3)",
        },
        "economy_api": {
            "Engine.AwardTickets(n, label)": "Adds n tickets, queues win banner.",
            "Engine.AwardTokens(n, label)": "Adds/subtracts n soul tokens, queues banner.",
            "Engine.GetTickets() → int": "Current ticket balance.",
            "Engine.GetTokens() → int": "Current soul token balance.",
            "Engine.GetStreak() → int": "Current streak counter.",
        },
        "win_banners": {
            "display_duration": "3.5 seconds",
            "fade_duration": "1 second (last second of display)",
            "stacking": "Multiple simultaneous banners stack vertically",
            "position": "Centered on screen at ~35% from top — gold header, white subtext (+N TICKETS)",
        },
    }


# ── Attraction specs ──────────────────────────────────────────────────────────

def build_attraction_specs() -> Dict[str, Any]:
    """Return consolidated attraction geometry and placement constants."""
    return {
        "unit_scale": "1 engine unit = 1 meter",
        "coordinate_system": {
            "origin": "center of booth at floor level (local space)",
            "axes": {"+X": "right", "+Y": "up", "+Z": "back", "opening": "-Z"},
        },
        "canonical_booth": {
            "width_x": 9.0,
            "height_y": 9.0,
            "depth_z": 15.0,
            "booth_side_x": 10.5,
            "booth_spacing": 15.0,
            "corridor_half_width": 6.0,
            "button_zone_local": {
                "x": 0.0,
                "y": 1.7,
                "z_expr": "-HD + 0.5 where HD = depth_z * 0.5",
            },
        },
        "cabinet_envelope": {
            "max_width": 7.0,
            "max_height": 6.5,
            "max_depth": 11.0,
            "clearance_side": 0.5,
            "clearance_rear": 0.5,
        },
        "placement": {
            "station_spacing_z": 15.0,
            "x_offset": "±10.5 from midway centerline",
            "booths_should_touch_visually_but_not_intersect": True,
        },
        "gdd_modifier_classes": {
            "core_physicals_4_1": ["Mass", "Volume", "Friction"],
            "meta_navigational_4_2": ["Karma", "Luck", "Persuasion", "Heat"],
            "tactile_4_3": ["Sleight of Hand", "Nerve"],
        },
    }
