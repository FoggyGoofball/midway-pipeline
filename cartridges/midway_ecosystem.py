"""
midway_ecosystem.py â€” MidwayAgentCartridge
==========================================
Thin orchestrating wrapper.  All large pure-data builders live in
cartridges/midway_data.py to keep each file under 1 000 lines.

Import direction (no cycles):
    cartridge_loader.py
        â†’ midway_ecosystem.py   (class definition + small logic)
                â†’ midway_data.py  (pure-data builders, no pipeline imports)
"""
import sys
from pathlib import Path
from typing import Dict, Any

# Ensure parent directory is on sys.path so pipeline-level modules resolve
_current_dir = Path(__file__).resolve().parent
_pipeline_dir = _current_dir.parent
if str(_pipeline_dir) not in sys.path:
    sys.path.insert(0, str(_pipeline_dir))

# Import only kernel shape constants â€” never project-specific prompt content
try:
    from domain_registry import (
        EXECUTION_MODEL,
        CODER_MODEL,
        REVIEWER_MODEL,
        REASONING_MODEL,
        LIBRARIAN_MODEL,
    )
except ImportError:
    EXECUTION_MODEL = "qwen2.5-coder:7b"
    CODER_MODEL = "qwen2.5-coder:7b"
    REVIEWER_MODEL = "phi3:14b"
    REASONING_MODEL = "phi3:14b"
    PRE_SUMMARIZER_MODEL = "phi3.5:latest"
    LIBRARIAN_MODEL = "qwen2.5-coder:7b"

# Import kernel alias map â€” cartridge merges Midway-specific entries on top
try:
    from domain_registry import AGENT_ALIAS_MAP as _KERNEL_ALIAS_MAP
except ImportError:
    _KERNEL_ALIAS_MAP = {}

# Pure-data builders extracted to keep this file under 1 000 lines
from cartridges.midway_data import (
    build_domain_registry,
    build_alias_map,
    build_environment_metadata,
    build_domain_rules,
    build_api_references,
    build_bridge_contract,
    build_attraction_specs,
)


class MidwayAgentCartridge:
    """
    Preserved Agent Ecosystem for the 'Midway to Nowhere' Project.
    Provides dynamically assembled domain registries, full alias resolution maps,
    and complete environment metadata directly integrated with the core orchestrator.

    Large static data is delegated to cartridges.midway_data to keep this file
    under 1 000 lines.
    """

    # Human-readable project name consumed by _prompts._project_name()
    # when this cartridge is mounted via PipelineContext.mount_cartridge().
    ECOSYSTEM_NAME = "Midway to Nowhere"

    @staticmethod
    def get_domain_registry() -> Dict[str, Dict[str, Any]]:
        """
        Fully self-contained Midway domain registry.
        All system_prompt strings for Midway agents are defined here â€” the kernel
        ALL_DOMAINS fallback stubs are never used when this cartridge is mounted.
        """
        return build_domain_registry(
            EXECUTION_MODEL=EXECUTION_MODEL,
            CODER_MODEL=CODER_MODEL,
            REVIEWER_MODEL=REVIEWER_MODEL,
            REASONING_MODEL=REASONING_MODEL,
            LIBRARIAN_MODEL=LIBRARIAN_MODEL,
            KERNEL_ALIAS_MAP=_KERNEL_ALIAS_MAP,
        )

    @staticmethod
    def get_alias_map() -> Dict[str, str]:
        """
        Comprehensive mapping of conversational agent aliases for Midway.
        Extends the kernel alias map with Midway-specific overrides.
        """
        return build_alias_map(_KERNEL_ALIAS_MAP)

    @staticmethod
    def get_environment_metadata() -> Dict[str, Dict[str, str]]:
        """
        Provides comprehensive environment metadata complete with structural invariants
        to reinforce absolute file sandboxing and engine isolation.
        """
        return build_environment_metadata()

    @staticmethod
    def get_domain_rules() -> Dict[str, Dict[str, Any]]:
        """
        Consolidated rule checklists for every pipeline agent domain.
        Derived from midway/docs/rules_*.md; PHYS rewritten for Unified Jolt.
        """
        return build_domain_rules()

    @staticmethod
    def get_api_references() -> Dict[str, Dict[str, Any]]:
        """
        Provides a structured index of API documentation files in the pipeline's
        own docs/ directory, with search terms mapped to section anchors.
        These files are adopted copies from the sibling midway/ repository.
        """
        return build_api_references(prefix="docs")

    @staticmethod
    def get_bridge_contract() -> Dict[str, Any]:
        """
        Consolidated Engineâ†”Lua bridge contract: globals, modifiers, lifecycle,
        load order, spawn API, object pools, economy, and win banners.
        """
        return build_bridge_contract()

    @staticmethod
    def get_attraction_specs() -> Dict[str, Any]:
        """
        Consolidated attraction geometry and placement constants.
        Source: midway/docs/attraction_specs.md
        """
        return build_attraction_specs()

    # â”€â”€ Kernel agnosticism fields â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    @staticmethod
    def get_reasoning_gate_domains() -> set:
        """Domains whose outputs pass through the Reasoning Gate audit step."""
        return {"C++", "Lua", "PHYS"}

    @staticmethod
    def get_terminology_note() -> str:
        """Domain-specific terminology injected into the Analyst system prompt."""
        return (
            "In this project, playable experiences are exclusively referred to as "
            "'Attractions', 'Booths', 'Encounters', or 'Minigames'. "
            "The walking area between booths is the 'Midway' or 'Corridor'."
        )

    @staticmethod
    def get_review_system_extra() -> str:
        """Project-specific veto rules appended to the kernel's REVIEW_SYSTEM persona.
        These are hard FAIL criteria the reviewer MUST enforce on every cycle."""
        return (
            "PROJECT-SPECIFIC VETO RULES (automatic FAIL on any violation):\n"
            "V1. SEAM CONTAMINATION: Any Lua attraction script containing teleport "
            "thresholds, Z-axis boundary checks, seam trigger logic, or "
            "OptimizeBroadPhase() calls is an automatic FAIL. The Vicious Cycle seam "
            "is engine-internal — Lua scripts have zero awareness of it.\n"
            "V2. LUA MANUAL PHYSICS: Any Lua script performing raw position arithmetic, "
            "euler/verlet integration, or collision detection in pure Lua is an automatic "
            "FAIL. All physics must go through MidwayPhysics host API calls. "
            "IMPORTANT: Calling MidwayPhysics.MoveKinematic, MidwayPhysics.SetLinearVelocity, "
            "MidwayPhysics.ApplyImpulse, or any other MidwayPhysics.* function IS the correct "
            "way to drive physics and is NOT a V2 violation. Only flag V2 when Lua code "
            "directly computes positions/velocities itself (e.g. pos.x = pos.x + vel*dt) "
            "without going through a MidwayPhysics API call.\n"
            "V3. PHANTOM APIS: Any call to a MidwayPhysics, Engine, or sol function "
            "not present in the active bridge contract is an automatic FAIL. "
            "NAMESPACE RULE: All MidwayPhysics functions MUST be prefixed with `MidwayPhysics.` — "
            "bare calls such as `DestroyBody(handle)` or `MoveKinematic(...)` are PHANTOM APIs. "
            "FILE-PATH OVERLOAD RULE: `SpawnStaticMesh('path/to/file.obj')` and any spawn call "
            "that passes a file-path string is an UNSUPPORTED overload — treat as a PHANTOM API. "
            "Known confirmed-phantom patterns (NEVER appear in valid code): "
            "GetBody, SpawnBox, SpawnTrigger, SpawnCircle, "
            "RemoveBody, MoveBoundary, SetBoundary, SpawnRectangle, SpawnIsland, "
            "GetLinearVelocity, SetLinearVelocity(handle, vec), CheckCollision, GetEntityHandle, "
            "DestroyEntity, ReleaseHandle, SpawnDynamicBody, SpawnDynamicBall, SpawnStaticBody, "
            "GetJoltBodyFromHandle, "
            "sol.get_variable, sol.set_variable, sol.off_tick, sol.on_tick, "
            "sol.on_load, sol.on_step, sol.on_unload, sol.set_function, sol.get_time, "
            "sol.get_elapsed_time, sol.bindings, sol.log_message, sol.log.\n"
            "V4. BOX2D: Any b2World, b2Vec2, b2Body, or Box2D header reference is an "
            "automatic FAIL.\n"
            "V5. EMPTY OUTPUT: Any task response with only prose and no code block "
            "is an automatic FAIL.\n"
            "V6. DOMAIN MISMATCH: C++ code in a Lua task or Lua code in a C++ task "
            "is an automatic FAIL.\n"
            "When issuing FAIL, cite the specific Vx rule number and quote the EXACT "
            "offending line from the code shown. Do NOT fail based on code that is not "
            "present in the provided snippets. Do NOT speculate about missing files or "
            "unshown bridge registrations."
        )

    @staticmethod
    def get_review_prompt_extra() -> str:
        """Project-specific checklist items appended to the kernel's REVIEW_PROMPT."""
        return (
            "6. Cross-domain bridge correctness: C++ sol2 registrations must match every Lua call-site.\n"
            "   EXEMPTION: OnLoad(), OnStep(dt), and OnUnload() are Lua CALLBACKS invoked BY the "
            "C++ engine. They require NO sol2 registration and must NEVER be flagged as missing "
            "bridge registrations. Only flag Lua calls that invoke C++ functions (e.g. MidwayPhysics.*, "
            "Engine.*) without a corresponding sol2 binding.\n"
            "7. SEAM CONTAMINATION: Lua scripts MUST NOT contain teleport thresholds, Z-axis boundary "
            "checks, seam triggers, or OptimizeBroadPhase() calls. Engine-internal only. FAIL.\n"
            "8. LUA MANUAL PHYSICS: Lua MUST NOT perform raw position arithmetic, euler/verlet "
            "integration, or pure-Lua collision detection. Use MidwayPhysics host calls. FAIL.\n"
            "9. PHANTOM APIS — only flag these KNOWN-BAD names: GetBody, SpawnBox, SpawnTrigger, "
            "SpawnCircle, RemoveBody, MoveBoundary, SetBoundary, SpawnRectangle, SpawnIsland, "
            "GetLinearVelocity, SetLinearVelocity(handle, vec_obj), CheckCollision, GetEntityHandle, "
            "DestroyEntity, ReleaseHandle, SpawnDynamicBody, SpawnStaticBody (generic), "
            "SpawnDynamicBall, GetJoltBodyFromHandle, sol.get_variable, sol.set_variable, "
            "sol.off_tick, sol.on_tick, sol.on_load, sol.on_step, sol.on_unload, sol.set_function, "
            "sol.get_time, sol.get_elapsed_time, sol.bindings.*. "
            "NAMESPACE RULE: All MidwayPhysics functions MUST be called with the full namespace prefix. "
            "A bare call such as `DestroyBody(handle)` or `MoveKinematic(handle, ...)` without "
            "`MidwayPhysics.` is a PHANTOM API — flag as FAIL. "
            "FILE-PATH OVERLOAD RULE: `SpawnStaticMesh('path/to/file.obj')` and any other spawn call "
            "that passes a file path string (e.g. mesh paths, texture paths) is an UNSUPPORTED overload "
            "and is a PHANTOM API — flag as FAIL. Only integer handle-returning spawn functions exist. "
            "APPROVED APIs (do NOT flag): MidwayPhysics.SpawnStaticBox, "
            "MidwayPhysics.SpawnKinematicBox, MidwayPhysics.SpawnDynamicBox, "
            "MidwayPhysics.SpawnStaticSphere, MidwayPhysics.SpawnDynamicSphere, "
            "MidwayPhysics.SpawnStaticCapsule, MidwayPhysics.SpawnKinematicCapsule, "
            "MidwayPhysics.SpawnDynamicCapsule, MidwayPhysics.SpawnSensorBox, "
            "MidwayPhysics.SpawnSensorSphere, MidwayPhysics.SpawnStaticMesh (handle overload only), "
            "MidwayPhysics.SpawnDynamicMesh, MidwayPhysics.MoveKinematic, MidwayPhysics.ApplyImpulse, "
            "MidwayPhysics.ApplyAngularImpulse, MidwayPhysics.SetLinearVelocity (vx,vy,vz form), "
            "MidwayPhysics.AddLinearVelocity, MidwayPhysics.SetFriction, MidwayPhysics.SetRestitution, "
            "MidwayPhysics.SetGravityFactor, MidwayPhysics.SetMass, MidwayPhysics.SetLinearDamping, "
            "MidwayPhysics.SetAngularDamping, MidwayPhysics.GetPosition, MidwayPhysics.GetVelocity, "
            "MidwayPhysics.IsActive, MidwayPhysics.IsSensorTriggered, MidwayPhysics.DestroyBody, "
            "MidwayPhysics.CreatePool, MidwayPhysics.PoolAcquire, MidwayPhysics.PoolReturn, "
            "MidwayPhysics.PoolCullBelow, MidwayPhysics.PoolFree, MidwayPhysics.PoolTotal, "
            "Engine.AwardTickets, Engine.AwardTokens, "
            "Engine.GetTickets, Engine.GetTokens, Engine.GetStreak.\n"
            "10. EMPTY OUTPUT: A task with ONLY prose and NO code block is a FAIL. "
            "Explanatory prose alongside a code block is fine.\n"
            "11. Modifier system: AttractionConstants.modifiers must be read live inside the "
            "MidwayPhysics.OnStep callback each frame, never cached at load time. "
            "The modifier table has keys: mass, volume, friction, karma, luck, "
            "persuasion, heat, sleightOfHand, nerve.\n"
            "12. Box2D deprecated: any b2World, b2Vec2, b2Body reference is an automatic FAIL.\n"
            "13. LUA STDLIB BAN (CRITICAL): Any Lua code using require(), io.open(), io.read(), "
            "os.*, or package.* is an automatic FAIL. File I/O and JSON must go through "
            "host-exposed sol2 functions (e.g. Engine.LoadJSON). Flag as FAIL immediately.\n"
            "IMPORTANT: Only flag issues that are VISIBLY PRESENT in the code shown. "
            "Do NOT speculate about missing files, unshown C++ bridge code, or rules that "
            "cannot be evaluated from the provided snippets."

        )

    @staticmethod
    def get_director_extra() -> str:
        """Project-specific directives prepended to the Director task-decomposition prompt."""
        return (
            "ATTRACTION ARCHITECTURE MANDATE — read before decomposing tasks:\n"
            "1. Scoring, win conditions, score display, game reset, and all player-state "
            "tracking are PURE LUA responsibilities. Do NOT create C++ tasks for these.\n"
            "2. OnLoad/OnStep/OnUnload are Lua callbacks invoked BY the engine — they need "
            "no C++ work unless a NEW physics primitive is required.\n"
            "3. Create a C++ task ONLY if a required primitive is provably absent from the "
            "'Active Bridge Contract' API list shown below.\n"
            "4. DO NOT create tasks for: 'Load Lua script', 'Integrate scoring into C++', "
            "'Register OnStep with engine', or any variant — these are automatic.\n"
            "5. SpawnStaticBox, SpawnDynamicSphere, SpawnKinematicBox, MoveKinematic, "
            "ApplyImpulse, GetPosition, GetVelocity are ALREADY bridged — no C++ task needed."
        )

    @staticmethod
    def get_coding_mandates() -> str:
        """Project-specific API binding mandates appended to LEDGER_MEMORY_RULE."""
        return (
            "CRITICAL API BINDING MANDATE (C++ / Lua):\n"
            "When exposing C++ primitives to Lua, you are strictly FORBIDDEN from using raw Lua C API "
            "stack operations (e.g. lua_pushcfunction, lua_touserdata, luaL_newmetatable). "
            "You MUST use modern sol2 bindings exclusively (e.g. sol::state_view, new_usertype). "
            "Physics primitives MUST NEVER include SDL rendering or drawing logic.\n\n"
            "UNIFIED JOLT STANDARD:\n"
            "Box2D is FULLY DEPRECATED and removed from the runtime. Do NOT write Box2D initialisation "
            "vectors, variables, world instances, or API references. "
            "Model all planar attractions using strict Jolt DOF constraints."
        )

    @staticmethod
    def get_project_context(prompt: str) -> str:
        """
        Extract relevant project-specific documentation context based on the
        user's prompt. Delegates internally to GDD extractors, API doc scanners,
        and keyword routers â€” all of which are project-private implementation
        details that the kernel must never know about.

        Args:
            prompt: User's natural language query or task specification.

        Returns:
            Formatted markdown string of matched documentation excerpts,
            or empty string if no relevant context found.
        """
        from context_extractor import extract_project_context as _extract
        return _extract(prompt)

