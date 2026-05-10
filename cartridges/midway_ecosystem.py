from typing import Dict, Any

class MidwayAgentCartridge:
    """
    Preserved Agent Ecosystem for the 'Midway to Nowhere' Project.
    Provides domain registries, routing aliases, and environment metadata 
    to the Universal Orchestrator dynamically.
    """
    
    @staticmethod
    def get_domain_registry() -> Dict[str, Dict[str, Any]]:
        return {
            "C++": {"name": "C++ Core", "model": "qwen2.5-coder:7b"},
            "LUA": {"name": "Lua Scripter", "model": "qwen2.5-coder:7b"},
            "PHYS": {"name": "Physics Architect", "model": "qwen2.5-coder:7b"},
            "SHADER": {"name": "Shader Artist", "model": "qwen2.5-coder:7b"},
            "NET": {"name": "Network Engineer", "model": "qwen2.5-coder:7b"},
            "REVIEWER": {"name": "Integration Reviewer", "model": "qwen2.5-coder:7b"},
            "TRIBUNAL": {"name": "Tribunal Architect", "model": "llama3.1:8b-instruct-q4_K_M"},
            "CONF": {"name": "Conflict Resolver", "model": "qwen2.5-coder:7b"}
        }

    @staticmethod
    def get_alias_map() -> Dict[str, str]:
        return {
            "CPP": "C++",
            "C++": "C++",
            "LUA": "LUA",
            "PHYSICS": "PHYS",
            "PHYS": "PHYS",
            "SHADER": "SHADER",
            "GLSL": "SHADER",
            "NET": "NET",
            "NETWORK": "NET"
        }

    @staticmethod
    def get_environment_metadata() -> Dict[str, Dict[str, str]]:
        return {
            "C++": {
                "language": "C++",
                "test_framework": "C++ Google Test (gtest)",
                "code_tag": "cpp",
                "extension": ".cpp",
                "assert_examples": "Use EXPECT_EQ, EXPECT_NEAR, ASSERT_TRUE, etc."
            },
            "LUA": {
                "language": "Lua",
                "test_framework": "Lua Busted (busted)",
                "code_tag": "lua",
                "extension": ".lua",
                "assert_examples": "Use assert.are.equal, assert.is_true, assert.is_near, etc."
            },
            "PHYS": {
                "language": "C++",
                "test_framework": "C++ Google Test (gtest)",
                "code_tag": "cpp",
                "extension": ".cpp",
                "assert_examples": "Use EXPECT_EQ, EXPECT_NEAR, with rigorous numerical tolerance."
            }
        }
