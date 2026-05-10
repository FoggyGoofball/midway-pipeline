import sys
from pathlib import Path
from typing import Dict, Any

# Ensure parent directory is accessible to dynamically import canonical pipeline modules
_current_dir = Path(__file__).resolve().parent
_pipeline_dir = _current_dir.parent
if str(_pipeline_dir) not in sys.path:
    sys.path.insert(0, str(_pipeline_dir))

# Attempt dynamic import of canonical registries to prevent residual refactoring discrepancies
try:
    from domain_registry import ALL_DOMAINS, AGENT_ALIAS_MAP
except ImportError:
    ALL_DOMAINS = {}
    AGENT_ALIAS_MAP = {}

class MidwayAgentCartridge:
    """
    Preserved Agent Ecosystem for the 'Midway to Nowhere' Project.
    Provides dynamically assembled domain registries, full alias resolution maps,
    and complete environment metadata directly integrated with the core orchestrator.
    """
    
    @staticmethod
    def get_domain_registry() -> Dict[str, Dict[str, Any]]:
        """
        Dynamically retrieves the canonical domain registry directly from the host pipeline.
        Ensures complete encapsulation of system prompts, formatting mandates, allowed extensions,
        and persistent memory ledger rules without duplicate static hardcoding.
        """
        registry: Dict[str, Dict[str, Any]] = {}
        
        # Populate directly from the canonical ALL_DOMAINS registry if available
        if ALL_DOMAINS:
            for key, config in ALL_DOMAINS.items():
                registry[key] = dict(config)
        else:
            # Safe minimal fallback only if dynamic import fails
            registry = {
                "C++": {"name": "C++ Core", "model": "qwen2.5-coder:7b"},
                "LUA": {"name": "Lua Scripter", "model": "qwen2.5-coder:7b"},
                "PHYS": {"name": "Physics Architect", "model": "qwen2.5-coder:7b"},
                "SHADER": {"name": "Shader Artist", "model": "qwen2.5-coder:7b"},
                "DOC": {"name": "Code Documentarian", "model": "phi3:14b"},
                "OBSERVABILITY": {"name": "Observability Auditor", "model": "qwen2.5-coder:7b"},
                "CONF": {"name": "Conflict Resolver", "model": "phi3:14b"},
                "TRIBUNAL": {"name": "Tribunal Architect", "model": "phi3:14b"},
                "LIBRARIAN": {"name": "Librarian", "model": "qwen2.5-coder:7b"}
            }
            
        # Ensure legacy keys expected by downstream cartridge consumers are preserved
        if "LUA" not in registry and "Lua" in registry:
            registry["LUA"] = registry["Lua"]
        if "NET" not in registry:
            registry["NET"] = {
                "name": "Network Engineer", 
                "model": "qwen2.5-coder:7b",
                "allowed_extensions": [".cpp", ".h", ".hpp"],
                "description": "Network state replication and server-authoritative RPCs"
            }
        if "REVIEWER" not in registry:
            registry["REVIEWER"] = {
                "name": "Integration Reviewer", 
                "model": "phi3:14b",
                "allowed_extensions": [],
                "description": "Integration code review against engine rules"
            }
            
        return registry

    @staticmethod
    def get_alias_map() -> Dict[str, str]:
        """
        Retrieves the comprehensive mapping of conversational agent aliases.
        Directly delegates to the canonical AGENT_ALIAS_MAP to resolve all 60+ permutations.
        """
        if AGENT_ALIAS_MAP:
            combined_map = dict(AGENT_ALIAS_MAP)
            combined_map.update({
                "CPP": "C++",
                "C++": "C++",
                "LUA": "Lua",
                "PHYSICS": "PHYS",
                "PHYS": "PHYS",
                "SHADER": "SHADER",
                "GLSL": "SHADER",
                "NET": "NET",
                "NETWORK": "NET"
            })
            return combined_map
            
        return {
            "CPP": "C++",
            "C++": "C++",
            "LUA": "Lua",
            "PHYSICS": "PHYS",
            "PHYS": "PHYS",
            "SHADER": "SHADER",
            "GLSL": "SHADER",
            "NET": "NET",
            "NETWORK": "NET"
        }

    @staticmethod
    def get_environment_metadata() -> Dict[str, Dict[str, str]]:
        """
        Provides comprehensive environment metadata complete with structural invariants
        to reinforce absolute file sandboxing and engine isolation.
        """
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
                )
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
                )
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
                )
            },
            "PHYS": {
                "language": "C++",
                "test_framework": "C++ Google Test (gtest)",
                "code_tag": "cpp",
                "extension": ".cpp",
                "assert_examples": "Use EXPECT_EQ, EXPECT_NEAR, with rigorous numerical tolerance.",
                "architectural_invariant": "Core physics layer. Must preserve Vicious Cycle seam invariants."
            }
        }
