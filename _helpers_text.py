"""
_helpers_text.py — Text parsing & formatting for the mesh consensus pipeline.
Contains: chat pattern detection, file context formatting, failure report
generation, syntax normalization.

No async/await — purely synchronous.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from _helpers_exec import MAX_CONSENSUS_ITERATIONS, _ALL_DOMAINS


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


# ── File Context Formatting ──────────────────────────────────────────────────

def format_file_context(files: list, domain_key: str = None,
                        ledger_toc_func=None) -> str:
    """Format discovered files into a context block for the model."""
    if not files:
        return ""
    parts = ["## Relevant Project Files\n"]
    for path, content in files:
        # VRAM_STUB detection: inject stub directly so the agent's
        # regex parser clearly recognizes it as a virtual memory pointer.
        if content.startswith("<VRAM_STUB"):
            parts.append(f"### File: {path}")
            parts.append(content + "\n")
            continue
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
    parts.append("- docs/rules_logging.md — C++/Lua logging rules\n")
    parts.append("- docs/engine_lua_bridge_contract.md — C++/Lua API contract\n")

    parts.append("\n### External Agent SOS Prompt\n")
    parts.append("Copy-paste the block below into a fresh agent session to recover from this deadlock:\n\n")
    parts.append("```\n")
    parts.append("## SOS — Pipeline Deadlock Recovery\n")
    parts.append(f"**Original User Prompt:** {user_prompt}\n")
    parts.append(f"**Deadlock Context:** Consensus iterations exhausted ({MAX_CONSENSUS_ITERATIONS}); ")
    parts.append("VETOs/OBJECTs blocked final approval.\n")

    # ── SOS FORMATTING MANDATE ──────────────────────────────────
    parts.append("\n## 🚨 FORMATTING REQUIREMENTS (MANDATORY)\n")
    parts.append("As the external AI recovering this deadlock, you MUST adhere to the following:\n\n")
    parts.append("### 1. Output Format — Memory Ledger Headers\n")
    parts.append("You MUST format your response using our standard `### [Feature_Name]` headers.\n")
    parts.append("Each new feature, system, or fix you propose MUST start with:\n")
    parts.append("  `### [YourFeatureName]`\n")
    parts.append("This ensures the output is parsable by our memory ledger pipeline.\n")
    parts.append("Do NOT use free-form prose without section headers.\n\n")
    parts.append("### 2. Engine Constraint Compliance\n")
    parts.append("This is a custom C++17 engine. You MUST adhere to:\n")
    parts.append("- **Rendering:** SDL2 + OpenGL 3.3+ only. No Unreal, Unity, Godot.\n")
    parts.append("- **Physics:** Jolt Physics SDK for rigid bodies; Box2D for 2D colliders.\n")
    parts.append("- **Scripting:** Lua 5.4 via sol2. Do NOT invent custom scripting languages.\n")
    parts.append("- **Networking:** NONE. There is no multiplayer/networking code.\n")
    parts.append("- **Shader:** GLSL 3.3 only. No HLSL, no Metal.\n")
    parts.append("- **Audio:** SoLoud planned but NOT yet integrated.\n")
    parts.append("Any code that references engines, APIs, or libraries outside these constraints will be automatically VETO'd.\n\n")
    parts.append("### 3. Runtime Log Verbosity\n")
    parts.append("You MUST include verbose runtime log lines for every significant operation.\n")
    parts.append("Format:\n")
    parts.append("  `[SystemName] Description of what happened`\n")
    parts.append("For example:\n")
    parts.append("  `[PhysicsManager] Teleported body 'plinko_ball_3' to Z=0 (Vicious Cycle seam)`\n")
    parts.append("  `[AttractionManager] Plinko booth OnLoad: registered 12 collision sensors`\n")
    parts.append("Logs MUST be specific enough to identify the exact subsystem and action.\n")
    parts.append("Do NOT use generic logs like 'operation completed successfully'.\n\n")
    parts.append("### 4. Task Recovery Approach\n")
    parts.append("Resolve each VETO/OBJECT as follows:\n")
    parts.append("  - Identify which domain agent's output caused the issue.\n")
    parts.append("  - Apply the original domain agent's rules (C++17, Lua 5.4, etc.) when fixing.\n")
    parts.append("  - Cross-reference with the feature intent from the user prompt.\n")
    parts.append("  - Output your fix under a `### [FeatureName]` header with runtime logs.\n")

    if vetos:
        parts.append("**Blocking VETOs:**\n")
        for v in vetos:
            from_name = domains.get(resolve_agent_name_func(v["from"]) if resolve_agent_name_func else v["from"], {}).get("name", v["from"])
            target_name = domains.get(resolve_agent_name_func(v["target"]) if resolve_agent_name_func else v["target"], {}).get("name", v["target"])
            parts.append(f"- {from_name} VETO'd {target_name}: {v['reason']}\n")
            if v["task_id"] in all_results:
                parts.append(f"  Offending draft:\n{all_results[v['task_id']][:300]}\n")
    if objects:
        parts.append("**Unresolved OBJECTions:**\n")
        for o in objects:
            from_name = domains.get(resolve_agent_name_func(o["from"]) if resolve_agent_name_func else o["from"], {}).get("name", o["from"])
            target_name = domains.get(resolve_agent_name_func(o["target"]) if resolve_agent_name_func else o["target"], {}).get("name", o["target"])
            parts.append(f"- {from_name} OBJECTed to {target_name}: {o['concern']}\n")
    parts.append("**Suggested next action:** Manually resolve each VETO/OBJECT, re-run with narrower scope.\n")
    parts.append("```\n")

    return "\n".join(parts)


# ── Code Block Extraction (Directives A & B) ──────────────────────────────

def extract_code_blocks(text: str, lang: Optional[str] = None) -> List[str]:
    """Extract all fenced code blocks from LLM output, stripping conversational prose.

    Directive A/B: Agents must communicate via code artifacts committed to the
    codebase, not via conversational prose. This function parses markdown code
    fences and returns only the raw code blocks.

    Args:
        text: Raw LLM output containing markdown prose and code fences.
        lang: Optional language filter (e.g., 'cpp', 'python', 'lua').
              If provided, only blocks matching that language are returned.

    Returns:
        List of raw code block strings (without fence markers).
    """
    if lang:
        pattern = rf"```(?:{lang})\s*\n(.*?)```"
    else:
        pattern = r"```(?:\w+)?\s*\n(.*?)```"
    blocks = re.findall(pattern, text, re.DOTALL)
    return [b.strip() for b in blocks if b.strip()]


def strip_to_code_artifacts(text: str, fallback_truncation: int = 800) -> str:
    """Strip conversational prose, keeping only code artifacts.

    Directive A & B: Use this to sanitize agent outputs before passing them
    as context to subsequent agents. If code blocks are found, only those
    are returned (concatenated). If no code blocks are found, the text is
    truncated to fallback_truncation chars as a safe fallback.

    Args:
        text: Raw agent output that may contain prose + code blocks.
        fallback_truncation: Max chars to keep if no code blocks found.

    Returns:
        Sanitized string containing only code artifacts or truncated prose.
    """
    blocks = extract_code_blocks(text, lang=None)
    if blocks:
        summary_parts: List[str] = []
        for block in blocks:
            summary_parts.append(block)
        return "\n\n".join(summary_parts)
    # No code blocks found — fall back to aggressive truncation
    if len(text) > fallback_truncation:
        return text[:fallback_truncation] + "\n[... truncated (no code artifacts found) ...]"
    return text


# ── Syntax Normalization ──────────────────────────────────────────────────────

def get_normalized_syntax(code: str) -> str:
    """Strip comments and normalize whitespace for functional code comparison."""
    # Remove C++ and Lua comments
    code = re.sub(r'//.*?\n|/\*.*?\*/|--.*?\n', '', code, flags=re.DOTALL)
    # Normalize whitespace
    code = re.sub(r'\s+', ' ', code).strip()
    # Normalize common structural tokens
    code = code.replace('{ ', '{').replace(' }', '}').replace('( ', '(').replace(' )', ')')
    return code
