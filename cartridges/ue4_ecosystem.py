"""
Unreal Engine 4 Agent Ecosystem Cartridge.

Provides domain registries, alias maps, environment metadata, and project-specific
rules for Unreal Engine 4 development tasks (C++ code generation, blueprint scripting,
asset management, build configuration, etc.).

This is a stub cartridge with placeholder API docs. Next phase: knowledge discovery
and API scraping to populate real documentation and domain-specific expertise.
"""

from typing import Dict, Set
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from models import DomainConfig, EnvironmentMetadata


class UE4AgentCartridge:
    """
    Agent Ecosystem for Unreal Engine 4 Projects.
    Governs orchestration across C++ engine code, blueprints, asset pipelines,
    build systems, and project configuration.
    """

    # Human-readable project name for prompts
    ECOSYSTEM_NAME = "Unreal Engine 4"

    # ─────────────────────────────────────────────────────────────────────────
    #  Domain Registry
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def get_domain_registry() -> Dict[str, DomainConfig]:
        """
        Return the complete domain registry for UE4 work.
        Each domain maps to a specific problem class and agent persona.
        """
        return {
            "CPP_ENGINE": {
                "name": "C++ Engine Code",
                "description": "Native C++ engine modifications, gameplay code, and plugin development",
                "model": "gpt-4",  # Will be overridden by OrchestrationConfig
                "aliases": ["cpp", "engine", "c++", "native"],
                "agent": "CPP_EXPERT",
                "rules": {
                    "max_file_size": 100000,
                    "syntax_gate_required": True,
                    "requires_compilation": True,
                    "dangerous_symbols": ["#include <windows.h>", "system()", "popen"],
                },
            },
            "BLUEPRINT": {
                "name": "Blueprint Visual Scripting",
                "description": "Blueprints, visual scripting, and Blueprintable classes",
                "model": "gpt-4",
                "aliases": ["bp", "blueprint", "visual"],
                "agent": "BLUEPRINT_EXPERT",
                "rules": {
                    "max_file_size": 50000,
                    "syntax_gate_required": True,
                    "requires_validation": True,
                    "dangerous_symbols": [],
                },
            },
            "ASSET": {
                "name": "Asset Management",
                "description": "Asset import, organization, material setup, and content browser operations",
                "model": "gpt-4",
                "aliases": ["asset", "content", "material", "texture"],
                "agent": "ASSET_EXPERT",
                "rules": {
                    "max_file_size": 50000,
                    "syntax_gate_required": False,
                    "dangerous_symbols": [],
                },
            },
            "BUILD": {
                "name": "Build & Project Configuration",
                "description": "CMake, .Build.cs files, project settings, and compilation configuration",
                "model": "gpt-4",
                "aliases": ["build", "cmake", "config", "project"],
                "agent": "BUILD_EXPERT",
                "rules": {
                    "max_file_size": 25000,
                    "syntax_gate_required": True,
                    "requires_validation": True,
                    "dangerous_symbols": ["system", "exec", "eval"],
                },
            },
            "GAMEPLAY": {
                "name": "Gameplay Framework",
                "description": "Game rules, character controllers, pawn behavior, and game mode logic",
                "model": "gpt-4",
                "aliases": ["gameplay", "game", "character", "controller"],
                "agent": "GAMEPLAY_EXPERT",
                "rules": {
                    "max_file_size": 75000,
                    "syntax_gate_required": True,
                    "requires_compilation": True,
                    "dangerous_symbols": ["DeleteMe", "BeginDestroy"],
                },
            },
            "UI": {
                "name": "UI & HUD Systems",
                "description": "UMG widgets, HUD classes, UI state management, and player interfaces",
                "model": "gpt-4",
                "aliases": ["ui", "hud", "umg", "widget"],
                "agent": "UI_EXPERT",
                "rules": {
                    "max_file_size": 50000,
                    "syntax_gate_required": True,
                    "requires_validation": True,
                    "dangerous_symbols": [],
                },
            },
            "ANIMATION": {
                "name": "Animation & Skeletal Systems",
                "description": "Skeletal meshes, animation blueprints, notifies, and state machines",
                "model": "gpt-4",
                "aliases": ["anim", "animation", "skeleton", "skeletal"],
                "agent": "ANIMATION_EXPERT",
                "rules": {
                    "max_file_size": 50000,
                    "syntax_gate_required": False,
                    "dangerous_symbols": [],
                },
            },
            "NETWORKING": {
                "name": "Networking & Replication",
                "description": "Network code, replication graphs, actor replication, and multiplayer setup",
                "model": "gpt-4",
                "aliases": ["net", "network", "multiplayer", "replication"],
                "agent": "NETWORKING_EXPERT",
                "rules": {
                    "max_file_size": 75000,
                    "syntax_gate_required": True,
                    "requires_compilation": True,
                    "dangerous_symbols": ["#define NDEBUG"],
                },
            },
            "PHYSICS": {
                "name": "Physics & Collision",
                "description": "Physics assets, collision setup, constraints, and vehicle physics",
                "model": "gpt-4",
                "aliases": ["physics", "collision", "constraint"],
                "agent": "PHYSICS_EXPERT",
                "rules": {
                    "max_file_size": 50000,
                    "syntax_gate_required": True,
                    "requires_compilation": True,
                    "dangerous_symbols": [],
                },
            },
            "AUDIO": {
                "name": "Audio & Sound",
                "description": "Audio assets, sound cues, dialogue systems, and acoustic properties",
                "model": "gpt-4",
                "aliases": ["audio", "sound", "voice", "dialogue"],
                "agent": "AUDIO_EXPERT",
                "rules": {
                    "max_file_size": 25000,
                    "syntax_gate_required": False,
                    "dangerous_symbols": [],
                },
            },
            "DOCUMENTATION": {
                "name": "Project Documentation",
                "description": "API docs, README files, design docs, and knowledge repositories",
                "model": "gpt-4",
                "aliases": ["doc", "documentation", "readme", "design"],
                "agent": "DOC_EXPERT",
                "rules": {
                    "max_file_size": 100000,
                    "syntax_gate_required": False,
                    "dangerous_symbols": [],
                },
            },
        }

    # ─────────────────────────────────────────────────────────────────────────
    #  Agent Alias Map
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def get_alias_map() -> Dict[str, str]:
        """
        Map user-friendly aliases to canonical agent names.
        Supports flexible command entry (e.g., "cpp" -> "CPP_EXPERT").
        """
        return {
            # C++ Engine
            "cpp": "CPP_EXPERT",
            "c++": "CPP_EXPERT",
            "engine": "CPP_EXPERT",
            "native": "CPP_EXPERT",
            "cpp_expert": "CPP_EXPERT",
            # Blueprint
            "bp": "BLUEPRINT_EXPERT",
            "blueprint": "BLUEPRINT_EXPERT",
            "visual": "BLUEPRINT_EXPERT",
            "blueprint_expert": "BLUEPRINT_EXPERT",
            # Asset
            "asset": "ASSET_EXPERT",
            "content": "ASSET_EXPERT",
            "material": "ASSET_EXPERT",
            "texture": "ASSET_EXPERT",
            "asset_expert": "ASSET_EXPERT",
            # Build
            "build": "BUILD_EXPERT",
            "cmake": "BUILD_EXPERT",
            "config": "BUILD_EXPERT",
            "project": "BUILD_EXPERT",
            "build_expert": "BUILD_EXPERT",
            # Gameplay
            "gameplay": "GAMEPLAY_EXPERT",
            "game": "GAMEPLAY_EXPERT",
            "character": "GAMEPLAY_EXPERT",
            "controller": "GAMEPLAY_EXPERT",
            "gameplay_expert": "GAMEPLAY_EXPERT",
            # UI
            "ui": "UI_EXPERT",
            "hud": "UI_EXPERT",
            "umg": "UI_EXPERT",
            "widget": "UI_EXPERT",
            "ui_expert": "UI_EXPERT",
            # Animation
            "anim": "ANIMATION_EXPERT",
            "animation": "ANIMATION_EXPERT",
            "skeleton": "ANIMATION_EXPERT",
            "skeletal": "ANIMATION_EXPERT",
            "animation_expert": "ANIMATION_EXPERT",
            # Networking
            "net": "NETWORKING_EXPERT",
            "network": "NETWORKING_EXPERT",
            "multiplayer": "NETWORKING_EXPERT",
            "replication": "NETWORKING_EXPERT",
            "networking_expert": "NETWORKING_EXPERT",
            # Physics
            "physics": "PHYSICS_EXPERT",
            "collision": "PHYSICS_EXPERT",
            "constraint": "PHYSICS_EXPERT",
            "physics_expert": "PHYSICS_EXPERT",
            # Audio
            "audio": "AUDIO_EXPERT",
            "sound": "AUDIO_EXPERT",
            "voice": "AUDIO_EXPERT",
            "dialogue": "AUDIO_EXPERT",
            "audio_expert": "AUDIO_EXPERT",
            # Documentation
            "doc": "DOC_EXPERT",
            "documentation": "DOC_EXPERT",
            "readme": "DOC_EXPERT",
            "design": "DOC_EXPERT",
            "doc_expert": "DOC_EXPERT",
        }

    # ─────────────────────────────────────────────────────────────────────────
    #  Environment Metadata
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def get_environment_metadata() -> Dict[str, EnvironmentMetadata]:
        """
        Return environment-specific metadata (e.g., development, staging, production).
        Maps environment names to build settings, optimization levels, and validation rules.
        """
        return {
            "development": EnvironmentMetadata(
                language="C++",
                test_framework="Catch2",
                code_tag="dev",
                extension=".cpp",
                assert_examples="check(Condition); ensure(Condition);",
                architectural_invariant="Debug symbols enabled, no optimization",
            ),
            "shipping": EnvironmentMetadata(
                language="C++",
                test_framework="Catch2",
                code_tag="shipping",
                extension=".cpp",
                assert_examples="ensure(Condition);",
                architectural_invariant="Full optimization, no debug symbols",
            ),
            "test": EnvironmentMetadata(
                language="C++",
                test_framework="Catch2",
                code_tag="test",
                extension=".cpp",
                assert_examples="check(Condition); ensure(Condition);",
                architectural_invariant="Validation enabled, moderate optimization",
            ),
        }

    # ─────────────────────────────────────────────────────────────────────────
    #  Project-Specific Prompt Content
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def get_reasoning_gate_domains() -> Set[str]:
        """
        Domains that require reasoning-gate review (extra scrutiny before execution).
        These are high-risk or critical path domains.
        """
        return {
            "CPP_ENGINE",      # Native code changes are risky
            "BUILD",           # Build system changes affect all developers
            "NETWORKING",      # Network code must be correct for multiplayer
            "GAMEPLAY",        # Game logic is core to functionality
        }

    @staticmethod
    def get_coding_mandates() -> str:
        """
        Critical UE4-specific coding standards and rules.
        These are appended to the kernel's ledger memory to guide agent behavior.
        """
        return """
CRITICAL UE4 CODING MANDATES:

1. MEMORY MANAGEMENT & POINTERS
   - Use NewObject<T>() and UObject::Destroy() for UObject lifecycle
   - Use TArray, TMap, TSet instead of std::vector, std::map, std::set
   - Mark classes as UCLASS(), structs as USTRUCT() if they need reflection
   - Use weak pointers (TWeakObjectPtr) to avoid circular references
   - Never use raw "new" or "delete" for UObjects — use the reflection system

2. NAMING CONVENTIONS & PREFIXES
   - Classes: prefix with U (UCharacter, UActor, UComponent)
   - Interfaces: prefix with I (IDamageableInterface)
   - Enums: prefix with E (EMovementMode, ECharacterState)
   - Booleans: prefix with b (bCanJump, bIsAlive)
   - Member variables: prefix with F_ for structs, U for objects (e.g., FVector Location, USkeletalMeshComponent* Mesh)

3. REPLICATION & NETWORKING
   - Use UPROPERTY(Replicated) and DOREPLIFETIME() macro for network properties
   - Validate ownership before executing Server RPC calls
   - Authority checks: if (!HasAuthority()) return;
   - Use NetMode checks: if (GetNetMode() == NM_Standalone) ...
   - RPCs must be declared with _Implementation suffix, then UFUNCTION(Server) or UFUNCTION(Client)

4. UNREAL REFLECTION SYSTEM
   - All public classes/structs must use GENERATED_BODY() in their declaration
   - Use UPROPERTY() for editor exposure and serialization
   - Use UFUNCTION() for RPC-eligible and Blueprint-callable functions
   - Always include the generated.h header at the end of your .h file

5. COMMON PITFALLS
   - DO NOT cast AActor* to derived class without IsA<DerivedClass>() check
   - DO NOT call virtual functions in constructors (vtable not set yet)
   - DO NOT store raw pointers to transient objects (they can be GC'd)
   - DO NOT use printf() — use UE_LOG(LogTemp, Warning, TEXT("..."))

6. ASSET REFERENCES & LOADING
   - Use TSoftObjectPtr<T> for lazy-loaded assets, then LoadSynchronous() when needed
   - Never hard-code asset paths in code — use asset specifiers or data tables
   - Always check IsValid(Asset) before use
   - Use FStreamableManager for async asset loading in production

7. BUILD CONFIGURATION
   - Use UPROPERTY(EditAnywhere, BlueprintReadWrite) for tuning values
   - Use UPROPERTY(VisibleDefaultsOnly) for read-only debug info
   - Separate game logic from platform-specific code using preprocessor guards
   - Test on all target platforms: Windows, Mac, Linux, Console

8. BLUEPRINT INTEROPERABILITY
   - Mark functions UFUNCTION(BlueprintCallable) to expose to Blueprints
   - Use UPROPERTY(BlueprintReadWrite) for Blueprint-settable members
   - Avoid exposing internal implementation details to Blueprint layer
   - Document Blueprintable contract clearly in class comments
"""

    @staticmethod
    def get_review_prompt_extra() -> str:
        """
        UE4-specific review criteria appended to the base review prompt.
        Ensures generated code adheres to engine conventions and best practices.
        """
        return """
6. Unreal Engine 4 Compliance
   - All UObject-derived classes properly use UCLASS(), UPROPERTY(), UFUNCTION() macros?
   - Memory management correct: NewObject<>, Destroy(), no raw new/delete for UObjects?
   - Naming conventions followed: U prefix for classes, b prefix for bools, F prefix for structs?
   - Replication logic correct: UPROPERTY(Replicated), DOREPLIFETIME(), Authority checks?
   - No raw pointers to transient objects; using TWeakObjectPtr for weak references?
   - RPC functions properly decorated with Server/Client specifiers and _Implementation suffix?
   - All dangerous casts protected with IsA<>() or Cast<>() null checks?
   - Asset references use TSoftObjectPtr for lazy loading, not hardcoded paths?
   - Cross-platform compatibility: any platform-specific code guarded with #if?
   - Logging uses UE_LOG, not printf()?

7. Blueprint Compatibility
   - If marked UFUNCTION(BlueprintCallable), is the function logic simple and pure?
   - All exposed UPROPERTY members have meaningful tooltips and category tags?
   - No exposing of internal state or implementation details?
   - Return types are Blueprint-compatible (no std containers, proper UE types)?

8. Performance & Scalability
   - Tick functions only used where necessary? Consider event-driven alternatives?
   - Heavy operations not in Tick or event handlers — use timers or async tasks?
   - Array allocations reasonable for typical project scope?
   - Any unbounded loops or recursive calls that could cause frame hitches?
"""

    @staticmethod
    def get_terminology_note() -> str:
        """
        UE4-specific terminology and naming conventions for agent understanding.
        Helps agents interpret project-specific terms correctly.
        """
        return """
UE4 TERMINOLOGY & CONCEPTS:

CORE CLASSES:
- AActor: Base class for all game objects. Has transforms, can be replicated, can be destroyed.
- APawn: Actor that can be possessed by a Controller. Represents gameplay entities.
- ACharacter: Pawn with skeletal mesh, movement, and animation support.
- UComponent: Object attached to an Actor (USkeletalMeshComponent, UBoxComponent, etc.).
- AController: Possesses a Pawn to control its behavior (PlayerController, AIController).
- APlayerController: Controller for human players, handles input.
- AAIController: Controller for AI-controlled pawns.
- UGameInstance: Persistent object for the lifetime of the game.
- UWorld: The current level/map being played.

REFLECTION SYSTEM:
- GENERATED_BODY(): Macro that generates reflection code for the class.
- UPROPERTY(): Exposes member variables to the editor, serialization, and replication.
- UFUNCTION(): Exposes member functions for RPC, Blueprint, and console commands.
- UCLASS(): Declares a class as part of the reflection system.
- USTRUCT(): Declares a struct as part of the reflection system.
- UENUM(): Declares an enum as part of the reflection system.

GAMEPLAY CONCEPTS:
- Pawn: Entity that can be possessed and controlled (player character or AI).
- Character: Specialized Pawn with walking, jumping, and animation.
- Controller: Logic that possesses a Pawn (player input or AI behavior).
- GameMode: Rules for the game (team size, score limits, etc.).
- PlayerState: State associated with a player (score, health, etc.).
- GameState: Replicated game state visible to all clients.

NETWORKING:
- Authority: Server controls the canonical state; clients are authority over their Pawns.
- Replication: Server sends state to clients. Clients send input to server.
- RPC (Remote Procedure Call): Function call that executes on server or client.
- Ownership: Actor owned by a specific PlayerController or Pawn (determines authority).

ASSET TYPES:
- USkeletalMesh: Mesh with bones for animation.
- UStaticMesh: Static, non-deforming mesh (buildings, props).
- UAnimSequence: Individual animation clip.
- UAnimBlueprint: Visual graph for blend spaces, state machines, animation logic.
- UBlueprint: Visual scripting container (inherits from UBlueprintGeneratedClass).
- UMaterial: Material definition (shaders, parameters).
- UTexture: Image asset (diffuse, normal, metallic, etc.).

BUILD SYSTEM:
- .Build.cs: Module build configuration file.
- Module: Collection of C++ files and their dependencies (like a library).
- Target: Executable definition (.Target.cs, e.g., MyGameEditor.Target.cs).
- UBT (Unreal Build Tool): Builds C++ projects; generates VS solution and compile commands.

PERFORMANCE CONSIDERATIONS:
- Tick: Called every frame, should be fast (< 0.1ms for non-critical systems).
- Timer: Delayed/repeated function call (better than Tick for infrequent logic).
- Async Task: Background task (doesn't stall gameplay frame).
- Streaming: Loading/unloading assets dynamically to manage memory.
"""

    # ─────────────────────────────────────────────────────────────────────────
    #  Cartridge Interface (required methods for dynamic loading)
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def get_domain_rules() -> Dict[str, dict]:
        """Return domain-specific invariants and validation rules (legacy compat)."""
        # Maps domain name to rule dict (used by domain_registry for agent setup)
        registry = UE4AgentCartridge.get_domain_registry()
        return {domain: cfg.get("rules", {}) for domain, cfg in registry.items()}


# ═════════════════════════════════════════════════════════════════════════════
#  CARTRIDGE INITIALIZATION STUB
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("UE4 Agent Cartridge Loaded")
    print(f"  Ecosystem: {UE4AgentCartridge.ECOSYSTEM_NAME}")
    print(f"  Domains: {len(UE4AgentCartridge.get_domain_registry())}")
    print(f"  Aliases: {len(UE4AgentCartridge.get_alias_map())}")
    print(f"  Reasoning gates: {UE4AgentCartridge.get_reasoning_gate_domains()}")
