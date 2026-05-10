#!/usr/bin/env python3
"""
hardening_patches.py — Four-Directive Architectural Hardening Patch
====================================================================
Applies all four critical architectural fixes to eliminate:
- Multi-agent context bleed (Directive A)
- Systemic logging overreach by Reviewer (Directive B)
- Context saturation & repetition traps in fix cycles (Directive C)
- Socket stream instability & crash from WinError 10054 (Directive D)

Usage:
    python hardening_patches.py
"""

import os
import sys
import re
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()

PATCH_LOG: list[str] = []


def log(status: str, label: str, detail: str = ""):
    msg = f"[{status}] {label}"
    if detail:
        msg += f": {detail}"
    PATCH_LOG.append(msg)
    print(msg)


# ═══════════════════════════════════════════════════════════════════════════
#  DIRECTIVE A — Absolute File-Level Sandboxing (Domain Enforcement)
# ═══════════════════════════════════════════════════════════════════════════

def patch_directive_a():
    """Add allowed_extensions, sandboxing restrictions, and validate() function."""

    # ── 1. domain_registry.py: Inject allowed_extensions + system prompt restrictions ──
    fp = BASE_DIR / "domain_registry.py"
    text = fp.read_text(encoding="utf-8")

    # C++ domain — add allowed_extensions after "model": EXECUTION_MODEL,
    cpp_pattern = '''        "model": EXECUTION_MODEL,
        "ledger_rule": "MUST append signature to docs/internal_api_ledger.md",'''
    cpp_replacement = '''        "model": EXECUTION_MODEL,
        "allowed_extensions": [".cpp", ".h", ".hpp", ".c", ".hxx", ".cxx"],
        "ledger_rule": "MUST append signature to docs/internal_api_ledger.md",'''
    if cpp_pattern in text:
        text = text.replace(cpp_pattern, cpp_replacement, 1)
        log(" OK ", "domain_registry.py: added .cpp/.h allowed_extensions to C++")
    else:
        log("SKIP", "domain_registry.py: C++ allowed_extensions pattern not found")

    # PHYS domain — add allowed_extensions after "model": EXECUTION_MODEL,
    phys_pattern = '''        "model": EXECUTION_MODEL,
        "description": "Jolt/Box2D physics, teleport stability, kinematic control, collision layers, sensors",'''
    phys_replacement = '''        "model": EXECUTION_MODEL,
        "allowed_extensions": [".cpp", ".h", ".hpp", ".c", ".hxx", ".cxx"],
        "description": "Jolt/Box2D physics, teleport stability, kinematic control, collision layers, sensors",'''
    if phys_pattern in text:
        text = text.replace(phys_pattern, phys_replacement, 1)
        log(" OK ", "domain_registry.py: added .cpp/.h allowed_extensions to PHYS")
    else:
        log("SKIP", "domain_registry.py: PHYS allowed_extensions pattern not found")

    # Lua domain — add allowed_extensions after "model": EXECUTION_MODEL,
    # Must find Lua's block specifically (it has same pattern as C++ initially)
    idx_lua = text.find('"Lua": {')
    if idx_lua >= 0:
        lua_window = text[idx_lua:idx_lua + 400]
        if '"allowed_extensions"' not in lua_window:
            lua_old = '''        "model": EXECUTION_MODEL,
        "ledger_rule": "MUST append signature to docs/internal_api_ledger.md",'''
            lua_new = '''        "model": EXECUTION_MODEL,
        "allowed_extensions": [".lua"],
        "ledger_rule": "MUST append signature to docs/internal_api_ledger.md",'''
            if lua_old in text[idx_lua:]:
                text = text[:idx_lua] + text[idx_lua:].replace(lua_old, lua_new, 1)
                log(" OK ", "domain_registry.py: added .lua allowed_extensions to Lua")
            else:
                log("SKIP", "domain_registry.py: Lua old pattern variant")
        else:
            log("SKIP", "domain_registry.py: Lua already has allowed_extensions")
    else:
        log("SKIP", "domain_registry.py: Lua domain block not found")

    # Append file-restriction constraint to C++ system_prompt
    cpp_restriction = (
        '\n\n'
        'CRITICAL: You are physically restricted to modifying [.cpp, .h, .hpp, .c] files. '
        'Any SEARCH/REPLACE blocks targeting other extensions (.lua, .py, .md, .json) '
        'will trigger a fatal system error.'
    )
    idx_cpp_sys = text.find('"system_prompt": (', text.find('"C++": {'))
    if idx_cpp_sys >= 0:
        cpp_body_start = text.find('"You are the C++17 systems engineer', idx_cpp_sys)
        if cpp_body_start >= 0:
            cpp_paren_close = text.find('\n        ),', cpp_body_start)
            if cpp_paren_close >= 0:
                text = text[:cpp_paren_close] + cpp_restriction + text[cpp_paren_close:]
                log(" OK ", "domain_registry.py: added C++ file sandboxing restriction to system_prompt")
            else:
                log("SKIP", "domain_registry.py: C++ system_prompt close paren not found")
        else:
            log("SKIP", "domain_registry.py: C++ system_prompt body not found")
    else:
        log("SKIP", "domain_registry.py: C++ system_prompt key not found")

    # Append file-restriction constraint to PHYS system_prompt
    phys_restriction = (
        '\n\n'
        'CRITICAL: You are physically restricted to modifying [.cpp, .h, .hpp, .c] files. '
        'Any SEARCH/REPLACE blocks targeting other extensions (.lua, .py, .md, .json) '
        'will trigger a fatal system error.'
    )
    idx_phys_sys = text.find('"system_prompt": (', text.find('"PHYS": {'))
    if idx_phys_sys >= 0:
        phys_body_start = text.find('"You are the Lead Physics Architect', idx_phys_sys)
        if phys_body_start >= 0:
            phys_paren_close = text.find('\n        ),', phys_body_start)
            if phys_paren_close >= 0:
                text = text[:phys_paren_close] + phys_restriction + text[phys_paren_close:]
                log(" OK ", "domain_registry.py: added PHYS file sandboxing restriction to system_prompt")
            else:
                log("SKIP", "domain_registry.py: PHYS system_prompt close paren not found")
        else:
            log("SKIP", "domain_registry.py: PHYS system_prompt body not found")
    else:
        log("SKIP", "domain_registry.py: PHYS system_prompt key not found")

    # Append file-restriction constraint to Lua system_prompt
    lua_restriction = (
        '\n\n'
        'CRITICAL: You are physically restricted to modifying [.lua] files only. '
        'Any SEARCH/REPLACE blocks targeting other extensions (.cpp, .h, .py, .md, .json) '
        'will trigger a fatal system error.'
    )
    idx_lua_sys = text.find('"system_prompt": (', text.find('"Lua": {'))
    if idx_lua_sys >= 0:
        lua_body_start = text.find('"You are the gameplay scripter', idx_lua_sys)
        if lua_body_start >= 0:
            lua_paren_close = text.find('\n        ),', lua_body_start)
            if lua_paren_close >= 0:
                text = text[:lua_paren_close] + lua_restriction + text[lua_paren_close:]
                log(" OK ", "domain_registry.py: added Lua file sandboxing restriction to system_prompt")
            else:
                log("SKIP", "domain_registry.py: Lua system_prompt close paren not found")
        else:
            log("SKIP", "domain_registry.py: Lua system_prompt body not found")
    else:
        log("SKIP", "domain_registry.py: Lua system_prompt key not found")

    fp.write_text(text, encoding="utf-8")
    log(" OK ", "domain_registry.py: saved all Directive-A changes")

    # ── 2. _pipeline_helpers.py: Add sandboxing validate/reject functions ──
    helpers_fp = BASE_DIR / "_pipeline_helpers.py"
    helpers_text = helpers_fp.read_text(encoding="utf-8")

    # Build the sandboxing function block (avoid nested f-string / brace issues
    # by constructing the raw python code as a list of lines and joining)
    sandboxing_code = r'''

# ── Directive A: File-Level Sandboxing Validation ───────────────────────────

# Canonical file extension mapping per agent domain key
DOMAIN_ALLOWED_EXTENSIONS: dict = {
    "C++": {".cpp", ".h", ".hpp", ".c", ".hxx", ".cxx"},
    "PHYS": {".cpp", ".h", ".hpp", ".c", ".hxx", ".cxx"},
    "Lua": {".lua"},
    "SHADER": {".glsl", ".vert", ".frag", ".geom"},
    "DOC": set(),      # read-only — no file writes
    "CONF": set(),     # read-only — no file writes
    "TRIBUNAL": set(), # read-only — no file writes
    "LIBRARIAN": set(),# read-only — no file writes
    "REVIEWER": set(), # read-only — no file writes
}


def get_allowed_extensions(domain_key: str) -> set:
    """Return the set of allowed file extensions for a domain key."""
    return DOMAIN_ALLOWED_EXTENSIONS.get(domain_key, set())


# Regex patterns to detect file path references in agent output
_FILE_PATH_IN_HEADING = re.compile(
    r'###\s*File:\s*([^\s\n]+)', re.IGNORECASE
)
_FILE_PATH_IN_CODEBLOCK = re.compile(
    r'```[\w]*\n(?:.*?\n)*?.*?(\/[\w\/.-]+\.[\w]+)',
    re.DOTALL,
)


def extract_file_paths_from_output(output_text: str) -> list[str]:
    """
    Extract all file paths referenced in an agent's output.
    Checks markdown headings (### File: path) and SEARCH/REPLACE block context.
    Returns list of unique paths found.
    """
    paths: set[str] = set()

    # 1. Check ### File: headings
    for match in _FILE_PATH_IN_HEADING.finditer(output_text):
        path = match.group(1).strip()
        if path:
            paths.add(path)

    # 2. Check ### `filename` code block headings
    for match in re.finditer(r'###\s*`([^`]+\.\w+)`', output_text):
        path = match.group(1).strip()
        if path:
            paths.add(path)

    # 3. Check for file paths near SEARCH/REPLACE blocks
    for match in re.finditer(
        r'(?:^|\n)\s*([\w\/.-]+\.(?:cpp|h|hpp|lua|py|c|hxx|cxx))\s*\n<<<<<<< SEARCH',
        output_text, re.MULTILINE,
    ):
        path = match.group(1).strip()
        if path:
            paths.add(path)

    return list(paths)


def validate_domain_file_write(
    domain_key: str,
    output_text: str,
    known_whitelist: set[str] | None = None,
) -> tuple[bool, list[str]]:
    """
    Validate that an agent's output does NOT attempt to modify files outside
    its domain's allowed extensions.

    Args:
        domain_key: Canonical domain key (e.g. "C++", "Lua", "PHYS").
        output_text: The raw text output from the agent.
        known_whitelist: Optional pre-computed set of allowed paths.

    Returns:
        Tuple of (is_valid: bool, violations: list[str])
    """
    allowed_exts = get_allowed_extensions(domain_key)
    if not allowed_exts:
        # Read-only domain — any file write is a violation
        referenced = extract_file_paths_from_output(output_text)
        if referenced:
            return False, [
                f"[SANDBOX] {domain_key} is READ-ONLY but referenced files: {referenced}"
            ]
        return True, []

    # Heuristic: if agent output contains SEARCH/REPLACE blocks, check file paths
    search_replace_count = output_text.count("<<<<<<< SEARCH")
    if search_replace_count == 0:
        return True, []  # No code modifications detected

    file_paths = extract_file_paths_from_output(output_text)
    violations: list[str] = []

    for fpath in file_paths:
        ext = Path(fpath).suffix.lower()
        if ext not in allowed_exts:
            violations.append(
                f"[SANDBOX] {domain_key} attempted to modify '{fpath}' "
                f"(extension '{ext}' not in allowed set: {allowed_exts})"
            )

    return len(violations) == 0, violations


def reject_cross_domain_output(
    domain_key: str,
    output_text: str,
    persona_name: str = "",
) -> tuple[bool, str]:
    """
    Hard reject: if an agent output contains cross-domain file writes,
    truncate the output to a safe error message and log the violation.

    Returns:
        Tuple of (is_clean: bool, safe_output: str)
            is_clean — True if no cross-domain violations found.
            safe_output — Original output if clean, or truncated safe message.
    """
    is_valid, violations = validate_domain_file_write(domain_key, output_text)
    if is_valid:
        return True, output_text

    # Log violations for diagnostics
    name = persona_name or domain_key
    for v in violations:
        print(f"  [SANDBOX] \u26d4 {v}")

    # Truncate — return a safe stub that won't poison the next cycle
    ext_list = list(get_allowed_extensions(domain_key))
    violations_str = "\n".join(f"- {v}" for v in violations)
    safe_stub = (
        f"## \u26d4 DOMAIN SANDBOX VIOLATION \u2014 Output Rejected\n\n"
        f"The {name} agent attempted to modify files outside its domain scope.\n"
        f"**Violations detected:**\n"
        f"{violations_str}\n\n"
        f"This output has been discarded. Please restrict modifications to "
        f"files with extensions: {ext_list}.\n"
    )
    return False, safe_stub
'''

    # Insert before get_normalized_syntax
    insert_marker = "\ndef get_normalized_syntax"
    pos = helpers_text.find(insert_marker)
    if pos >= 0:
        helpers_text = helpers_text[:pos] + sandboxing_code + helpers_text[pos:]
        log(" OK ", "_pipeline_helpers.py: added Directive-A sandboxing functions")
    else:
        helpers_text += sandboxing_code
        log("WARN", "_pipeline_helpers.py: inserted sandboxing at end (get_normalized_syntax not found)")

    helpers_fp.write_text(helpers_text, encoding="utf-8")
    log(" OK ", "_pipeline_helpers.py: saved Directive-A changes")


# ═══════════════════════════════════════════════════════════════════════════
#  DIRECTIVE B — Differential Enforcement of Mandates (Reviewer Constraint)
# ═══════════════════════════════════════════════════════════════════════════

def patch_directive_b():
    """Update REVIEW_SYSTEM in _prompts.py to add differential enforcement constraint."""
    fp = BASE_DIR / "_prompts.py"
    text = fp.read_text(encoding="utf-8")

    # The OBSERVABILITY MANDATE block to find and replace
    old_obs = (
        '    "OBSERVABILITY MANDATE:\\n"\n'
        '    "You MUST VETO any code that introduces silent code paths \u2014 every conditional\\n"\n'
        '    "branch must have a corresponding log statement. Any new feature without\\n"\n'
        '    "instrumentation (printf, fmt::print, spdlog, or Lua print) is a defect.\\n"\n'
        '    "See docs/rules_logging.md for the full logging rules.\\n"\n'
        '    "If logs are missing, your verdict MUST be **FAIL**."'
    )

    new_obs = (
        '    "OBSERVABILITY MANDATE:\\n"\n'
        '    "You MUST VETO any code that introduces silent code paths \u2014 every conditional\\n"\n'
        '    "branch must have a corresponding log statement. Any new feature without\\n"\n'
        '    "instrumentation (printf, fmt::print, spdlog, or Lua print) is a defect.\\n"\n'
        '    "See docs/rules_logging.md for the full logging rules.\\n"\n'
        '    "If logs are missing, your verdict MUST be **FAIL**.\\n"\n'
        '    "\\n"\n'
        '    "CRITICAL DIRECTIVE: Observability, logging, and commenting mandates apply\\n"\n'
        '    "ONLY to new or modified code. Do NOT instruct agents to retrofit existing\\n"\n'
        '    "legacy code with logs or comments. Evaluate only the delta (diff) introduced\\n"\n'
        '    "by the current task. Systemic retrofitting of legacy files is prohibited."'
    )

    if old_obs in text:
        text = text.replace(old_obs, new_obs, 1)
        log(" OK ", "_prompts.py: added differential enforcement constraint to REVIEW_SYSTEM")
        fp.write_text(text, encoding="utf-8")
        log(" OK ", "_prompts.py: saved Directive-B changes")
        return

    # Alternate format without leading indent
    alt_obs = (
        '"OBSERVABILITY MANDATE:\\n"\n'
        '"You MUST VETO any code that introduces silent code paths \u2014 every conditional\\n"\n'
        '"branch must have a corresponding log statement. Any new feature without\\n"\n'
        '"instrumentation (printf, fmt::print, spdlog, or Lua print) is a defect.\\n"\n'
        '"See docs/rules_logging.md for the full logging rules.\\n"\n'
        '"If logs are missing, your verdict MUST be **FAIL**."'
    )
    if alt_obs in text:
        text = text.replace(alt_obs, new_obs, 1)
        log(" OK ", "_prompts.py: added differential enforcement (alternate format)")
        fp.write_text(text, encoding="utf-8")
        log(" OK ", "_prompts.py: saved Directive-B changes")
        return

    # Fallback: find REVIEW_SYSTEM and append the constraint
    log("WARN", "_prompts.py: OBSERVABILITY MANDATE not found — appending to REVIEW_SYSTEM")
    rs_start = text.find('REVIEW_SYSTEM = (')
    if rs_start >= 0:
        close_paren = text.find('\n)', rs_start)
        if close_paren >= 0:
            insert_block = (
                '\n    "\\n"\n'
                '    "CRITICAL DIRECTIVE: Observability, logging, and commenting mandates apply\\n"\n'
                '    "ONLY to new or modified code. Do NOT instruct agents to retrofit existing\\n"\n'
                '    "legacy code with logs or comments. Evaluate only the delta (diff) introduced\\n"\n'
                '    "by the current task. Systemic retrofitting of legacy files is prohibited."'
            )
            text = text[:close_paren] + insert_block + text[close_paren:]
            log(" OK ", "_prompts.py: appended differential enforcement constraint to REVIEW_SYSTEM")
        else:
            log("FAIL", "_prompts.py: REVIEW_SYSTEM close paren not found")
    else:
        log("FAIL", "_prompts.py: REVIEW_SYSTEM block not found")

    fp.write_text(text, encoding="utf-8")
    log(" OK ", "_prompts.py: saved Directive-B changes")


# ═══════════════════════════════════════════════════════════════════════════
#  DIRECTIVE C — Context Pruning in Fix Cycles
# ═══════════════════════════════════════════════════════════════════════════

def patch_directive_c():
    """Add context-pruning function and integrate into fix cycle in mesh_finalize.py."""
    fp = BASE_DIR / "mesh_finalize.py"
    text = fp.read_text(encoding="utf-8")

    # ── Insert context-pruning function (as raw string to avoid brace issues) ──
    prune_func = r'''


# ═══════════════════════════════════════════════════════════════════════════
#  Directive C — Context Pruning for Fix Cycles
# ═══════════════════════════════════════════════════════════════════════════

def _prune_fix_context(
    domain_key: str,
    task_obj: 'Any',
    review_issues_text: str,
    pre_flight_errors: str,
    user_prompt: str,
) -> str:
    """
    Build a lean, pruned context payload for a domain agent fix cycle.

    Strips ALL prior iterative generation attempts and provides only:
      - Original user prompt (condensed to 200 chars)
      - Task specification relevant to this agent
      - The EXACT compiler/linter error string relevant to this domain
      - REVIEW issues text (filtered for domain relevance)
      - Domain boundary reminder

    This prevents generative looping by eliminating historical bloat
    and cross-domain critiques from the context.
    """
    from domain_registry import ALL_DOMAINS as _domains

    domain_info = _domains.get(domain_key, {})
    domain_name = domain_info.get("name", domain_key)

    parts: list[str] = []

    # 1. Original prompt (condensed to first 200 chars)
    parts.append("## Original Feature Request\n" + user_prompt[:200])

    # 2. Task spec (the original directive given to this agent)
    if task_obj and hasattr(task_obj, 'spec') and task_obj.spec:
        parts.append("## Your Task Specification\n" + task_obj.spec[:500])

    # 3. Domain-scoped error text — filter compiler/linter errors
    allowed_exts = {".cpp", ".h", ".hpp"} if domain_key in ("C++", "PHYS") else {".lua"}
    if pre_flight_errors:
        filtered_errors = []
        for line in pre_flight_errors.split("\n"):
            if any(ext in line.lower() for ext in allowed_exts):
                filtered_errors.append(line)
        if filtered_errors:
            parts.append(
                "## Compiler/Linter Errors (Domain-Filtered)\n"
                + "\n".join(filtered_errors[-15:])
            )
        else:
            parts.append("## Compiler/Linter Errors\n" + pre_flight_errors[:1000])

    # 4. Review issues (heuristic domain filter)
    if review_issues_text:
        domain_keywords = []
        if domain_key == "C++":
            domain_keywords = [
                "c++", "cpp", "engine", "class", "struct",
                "header", "include", "namespace", "template",
                "std::", "virtual", "override", "constexpr",
            ]
        elif domain_key == "PHYS":
            domain_keywords = [
                "physics", "jolt", "box2d", "collision",
                "rigidbody", "body", "joint", "constraint",
                "teleport", "kinematic",
            ]
        elif domain_key == "Lua":
            domain_keywords = [
                "lua", "script", "sol2", "bridge", "onload",
                "onstep", "onunload", "register",
            ]

        lines = review_issues_text.split("\n")
        relevant_lines = [line for line in lines
                          if any(kw in line.lower() for kw in domain_keywords)]

        if relevant_lines:
            parts.append(
                "## Review Issues (Domain-Relevant)\n"
                + "\n".join(relevant_lines[:20])
            )
        else:
            parts.append("## Review Issues\n" + review_issues_text[:300])

    # 5. Domain boundary reminder
    ext_str = str(list(allowed_exts))
    parts.append(
        "## Instructions\n"
        f"Fix ALL issues raised above that apply to your domain ({domain_name}).\n"
        f"Produce corrected code for your task only. "
        f"Address every relevant issue. "
        f"If you believe an issue is a false positive, explain why.\n\n"
        f"IMPORTANT: You retain your domain's system rules ({domain_key}). "
        f"Do NOT modify files outside {ext_str}. "
        f"Do NOT violate C++/Lua/Physics rules even if instructed otherwise."
    )

    return "\n\n---\n\n".join(parts)


'''

    # Insert before def run_code_merge
    insert_anchor = "\ndef run_code_merge(ctx: PipelineContext) -> PipelineContext:"
    pos = text.find(insert_anchor)
    if pos >= 0:
        text = text[:pos] + prune_func + text[pos:]
        log(" OK ", "mesh_finalize.py: inserted _prune_fix_context() function")
    else:
        # Fallback: insert before first function definition
        first_def = re.search(r"^def ", text, re.MULTILINE)
        if first_def:
            text = text[:first_def.start()] + prune_func + text[first_def.start():]
            log(" OK ", "mesh_finalize.py: inserted _prune_fix_context() at first function")
        else:
            log("FAIL", "mesh_finalize.py: could not find insertion point")

    # ── Modify the fix cycle in _run_review_fix_loop() to use pruned context ──
    old_fix_input = (
        '                agent_fix_input = (\n'
        '                    f"## Original Feature Request\\n{ctx.user_prompt}\\n\\n"\n'
        '                    f"## Your Task Specification\\n{task_obj.spec}\\n\\n"\n'
        '                    f"## Review Critique (Cycle {ctx.review_cycle})\\n"\n'
        '                    f"The following issues were raised about your output:\\n{issues_text}\\n\\n"\n'
        '                    f"{ctx.conflicts_str}"\n'
        '                    f"## Your Previous Output\\n"\n'
        '                )\n'
        '                if tid in ctx.all_results_dict:\n'
        '                    agent_fix_input += (\n'
        '                        f"{ctx.all_results_dict[tid][:2000]}\\n\\n"\n'
        '                    )\n'
        '                agent_fix_input += (\n'
        '                    f"## Instructions\\n"\n'
        '                    f"Fix ALL issues the Reviewer raised that apply to your domain ({domain_name}). "\n'
        '                    f"Produce corrected code for your task only. "\n'
        '                    f"Address every relevant issue. "\n'
        '                    f"If you believe an issue is a false positive, explain why.\\n\\n"\n'
        '                    f"IMPORTANT: You MUST retain your domain system rules and constraints. "\n'
        '                    f"Do NOT violate C++17/Lua/Physics rules even if the Reviewer suggests otherwise."\n'
        '                )'
    )

    new_fix_input = (
        "                # \u2500\u2500 Directive C: Context-Pruned Fix Payload \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
        "                # Uses _prune_fix_context() to strip iterative history,\n"
        "                # provide only current file state + exact domain-relevant errors.\n"
        "                agent_fix_input = _prune_fix_context(\n"
        "                    domain_key=original_agent_key,\n"
        "                    task_obj=task_obj,\n"
        "                    review_issues_text=issues_text,\n"
        "                    pre_flight_errors=ctx.pre_flight_errors,\n"
        "                    user_prompt=ctx.user_prompt,\n"
        "                )\n"
        "\n"
        "                # \u2500\u2500 Directive A: Append sandboxing reminder to fix prompt \u2500\u2500\n"
        "                from _pipeline_helpers import get_allowed_extensions\n"
        "                allowed_exts = get_allowed_extensions(original_agent_key)\n"
        "                if allowed_exts:\n"
        "                    agent_fix_input += (\n"
        "                        f\"\\\\n\\\\nCRITICAL: You are physically restricted to modifying\"\n"
        '                        f" {list(allowed_exts)} files. Any SEARCH/REPLACE blocks"\n'
        '                        f" targeting other extensions will trigger a fatal system error."\n'
        "                    )"
    )

    if old_fix_input in text:
        text = text.replace(old_fix_input, new_fix_input, 1)
        log(" OK ", "mesh_finalize.py: replaced fix cycle with context-pruned payload")
    else:
        log("WARN", "mesh_finalize.py: exact fix_input pattern not found — trying regex match")
        pattern = re.compile(
            r'agent_fix_input\s*=\s*\(.*?'
            r'## Original Feature Request.*?'
            r'## Your Task Specification.*?'
            r'## Review Critique.*?'
            r'Do NOT violate C\+\+17.*?\)\s*\n',
            re.DOTALL,
        )
        match = pattern.search(text)
        if match:
            start, end = match.start(), match.end()
            text = text[:start] + new_fix_input + text[end:]
            log(" OK ", "mesh_finalize.py: replaced fix cycle (regex match)")
        else:
            log("WARN", "mesh_finalize.py: could not find fix_input block — attempting inline append")

            # Last resort: find and replace the first agent_fix_input assignment
            fi_pos = text.find('agent_fix_input = (')
            if fi_pos >= 0:
                # Find the closing of the whole block
                imp_pos = text.find("Do NOT violate C++17/Lua/Physics", fi_pos)
                if imp_pos >= 0:
                    end_pos = text.find('\n                )\n', imp_pos)
                    if end_pos >= 0:
                        end_pos = end_pos + len('\n                )\n')
                        text = text[:fi_pos] + new_fix_input + text[end_pos:]
                        log(" OK ", "mesh_finalize.py: inline replacement of agent_fix_input block")
                    else:
                        log("FAIL", "mesh_finalize.py: could not find end of fix_input block")
                else:
                    log("FAIL", "mesh_finalize.py: could not find IMPORTANT marker")
            else:
                log("FAIL", "mesh_finalize.py: agent_fix_input assignment not found")

    fp.write_text(text, encoding="utf-8")
    log(" OK ", "mesh_finalize.py: saved Directive-C changes")


# ═══════════════════════════════════════════════════════════════════════════
#  DIRECTIVE D — Stream Resilience and State Rollback
# ═══════════════════════════════════════════════════════════════════════════

def patch_directive_d():
    """Add ConnectionResetError handling, rollback, and retry to ollama_client.py
    and pipeline_stream_server.py."""

    # ── 1. ollama_client.py: Add retry helpers + wrap streaming HTTP in try/except ──
    fp = BASE_DIR / "ollama_client.py"
    text = fp.read_text(encoding="utf-8")

    # Insert retry helpers after imports, before _active_model
    retry_helpers = r'''


# ═══════════════════════════════════════════════════════════════════════════
#  Directive D — Stream Resilience Helpers
# ═══════════════════════════════════════════════════════════════════════════

import time as _time
import threading as _threading

_tls = _threading.local()
_tls.retry_attempt = 0
_tls.last_temperature = 0.5


def _cooldown_and_retry(
    exception: Exception,
    system: str, user: str, label: str, model: str, params: dict | None
) -> 'Generator[str, None, None]':
    """Log critical warning, cooldown VRAM, decrement temperature, retry once.

    Called when a ConnectionResetError (WinError 10054) is caught during
    HTTP streaming from Ollama. Does NOT pass the truncated output to any
    downstream reviewer — instead:
      1. Logs a critical warning with thermal/timeout context
      2. Triggers 5-second VRAM cooldown (time.sleep(5))
      3. Decrements LLM temperature by 0.1 (floor 0.1)
      4. Attempts a single automatic retry
    """
    _tls.retry_attempt = getattr(_tls, 'retry_attempt', 0) + 1

    # 1. Log critical warning
    print()
    print("=" * 60)
    print(f"  [CONNECTION_RESET] CRITICAL: Socket dropped during stream for '{label}'")
    print(f"  Exception: {exception}")
    print(f"  Probable cause: Payload too large / VRAM timeout / thermal throttle")
    print(f"  Retry attempt: {_tls.retry_attempt}")
    print("=" * 60)

    # 2. VRAM cooldown
    cooldown = 5.0
    print(f"  [VRAM Cooldown] Sleeping for {cooldown}s to allow thermal dissipation...")
    _time.sleep(cooldown)

    # 3. Decrement temperature (floor 0.1)
    current_temp = getattr(_tls, 'last_temperature', 0.5)
    new_temp = max(0.1, current_temp - 0.1)
    _tls.last_temperature = new_temp
    print(f"  [Retry] Decreased temperature from {current_temp} to {new_temp}")

    retry_params = dict(params or {})
    retry_params["temperature"] = new_temp

    # 4. Single automatic retry
    if _tls.retry_attempt >= 2:
        msg = f"[FATAL] Socket dropped twice for '{label}' — giving up."
        print(f"  {msg}")
        yield msg
        return

    print(f"  [Retry] Attempting automatic retry for '{label}' (attempt {_tls.retry_attempt})...")
    try:
        yield from call_ollama_streamed(system, user, label, model, params=retry_params)
    except (ConnectionResetError, ConnectionAbortedError) as e2:
        msg = f"[FATAL] Socket dropped again on retry for '{label}': {e2}. Giving up."
        print(f"  {msg}")
        yield msg
    except Exception as e2:
        msg = f"[RETRY ERROR] Non-socket exception on retry for '{label}': {e2}"
        print(f"  {msg}")
        yield msg


'''

    insert_pos = text.find("_active_model = None")
    if insert_pos >= 0:
        text = text[:insert_pos] + retry_helpers + "\n" + text[insert_pos:]
        log(" OK ", "ollama_client.py: inserted retry helpers at module top")
    else:
        log("WARN", "ollama_client.py: could not find insertion point — appending at end")
        text += retry_helpers

    # Wrap the streaming HTTP `chunk = resp.read(4096)` in ConnectionResetError try/except
    # Find the exact block in call_ollama_streamed
    old_stream_block = (
        '        with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT) as resp:\n'
        '            buffer = b""\n'
        '            while True:\n'
        '                chunk = resp.read(4096)\n'
        '                if not chunk:\n'
        '                    break\n'
        '                buffer += chunk\n'
        '                while b"\\n" in buffer:\n'
        '                    line, buffer = buffer.split(b"\\n", 1)\n'
        '                    if line.strip():\n'
        '                        try:\n'
        '                            obj = json.loads(line.decode("utf-8"))\n'
        '                            token = obj.get("message", {}).get("content", "")\n'
        '                            if token:\n'
        '                                print(token, end="")\n'
        '                                sys.stdout.flush()\n'
        '                                cb = _stream_callback\n'
        '                                if cb is not None:\n'
        '                                    try:\n'
        '                                        cb(token)\n'
        '                                    except Exception:\n'
        '                                        pass\n'
        '                                yield token\n'
        '                        except json.JSONDecodeError:\n'
        '                            pass  # skip malformed lines\n'
    )

    new_stream_block = (
        '        with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT) as resp:\n'
        '            buffer = b""\n'
        '            while True:\n'
        '                try:\n'
        '                    chunk = resp.read(4096)\n'
        '                except (ConnectionResetError, ConnectionAbortedError) as sock_err:\n'
        '                    # ── Directive D: Socket drop handling ──────────────\n'
        '                    print(f"\\n  [Stream] \\u26a0 Socket dropped (WinError 10054) during read. Triggering retry...",\n'
        '                          file=sys.stderr)\n'
        '                    sys.stderr.flush()\n'
        '                    yield from _cooldown_and_retry(\n'
        '                        exception=sock_err,\n'
        '                        system=system,\n'
        '                        user=user,\n'
        '                        label=label,\n'
        '                        model=use_model,\n'
        '                        params=params,\n'
        '                    )\n'
        '                    return\n'
        '\n'
        '                if not chunk:\n'
        '                    break\n'
        '                buffer += chunk\n'
        '                while b"\\n" in buffer:\n'
        '                    line, buffer = buffer.split(b"\\n", 1)\n'
        '                    if line.strip():\n'
        '                        try:\n'
        '                            obj = json.loads(line.decode("utf-8"))\n'
        '                            token = obj.get("message", {}).get("content", "")\n'
        '                            if token:\n'
        '                                print(token, end="")\n'
        '                                sys.stdout.flush()\n'
        '                                cb = _stream_callback\n'
        '                                if cb is not None:\n'
        '                                    try:\n'
        '                                        cb(token)\n'
        '                                    except Exception:\n'
        '                                        pass\n'
        '                                yield token\n'
        '                        except json.JSONDecodeError:\n'
        '                            pass  # skip malformed lines\n'
    )

    if old_stream_block in text:
        text = text.replace(old_stream_block, new_stream_block, 1)
        log(" OK ", "ollama_client.py: wrapped HTTP read loop in ConnectionResetError handling")
    else:
        log("WARN", "ollama_client.py: streaming block not found exactly — trying read-only line patch")
        # Surgical: replace just `chunk = resp.read(4096)` inside the function
        old_read = '                chunk = resp.read(4096)'
        new_read = (
            '                try:\n'
            '                    chunk = resp.read(4096)\n'
            '                except (ConnectionResetError, ConnectionAbortedError) as sock_err:\n'
            '                    print(f"\\n  [Stream] \\u26a0 Socket dropped during read. Triggering retry...",\n'
            '                          file=sys.stderr)\n'
            '                    sys.stderr.flush()\n'
            '                    yield from _cooldown_and_retry(\n'
            '                        exception=sock_err,\n'
            '                        system=system,\n'
            '                        user=user,\n'
            '                        label=label,\n'
            '                        model=use_model,\n'
            '                        params=params,\n'
            '                    )\n'
            '                    return'
        )
        if old_read in text:
            text = text.replace(old_read, new_read, 1)
            log(" OK ", "ollama_client.py: surgical patch applied to resp.read(4096)")
        else:
            log("FAIL", "ollama_client.py: could not find resp.read(4096) line")

    fp.write_text(text, encoding="utf-8")
    log(" OK ", "ollama_client.py: saved Directive-D changes")

    # ── 2. pipeline_stream_server.py: Add SnapshotManager rollback on disconnect ──
    srv_fp = BASE_DIR / "pipeline_stream_server.py"
    if not srv_fp.is_file():
        log("SKIP", "pipeline_stream_server.py: file not found")
        return

    srv_text = srv_fp.read_text(encoding="utf-8")

    # Update imports
    old_imports = "from pipeline_stream import stream_pipeline_generator"
    new_imports = (
        "from pipeline_stream import stream_pipeline_generator\n"
        "from pipeline_snapshot import SnapshotManager\n"
        "import time"
    )
    if old_imports in srv_text:
        srv_text = srv_text.replace(old_imports, new_imports, 1)
        log(" OK ", "pipeline_stream_server.py: added SnapshotManager import")
    else:
        log("SKIP", "pipeline_stream_server.py: import line not found")

    # Update POST handler disconnect
    old_post_disc = (
        '                        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):\n'
        '                            print("  [OpenAI POST] Client disconnected")\n'
        '                            return'
    )
    new_post_disc = (
        "                        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError) as conn_err:\n"
        '                            print(f"  [OpenAI POST] Client disconnected: {conn_err}")\n'
        "                            print(\"  [StreamServer] CRITICAL: Stream socket dropped -- possible \"\n"
        '                                  "payload overflow or thermal timeout. Truncated data will NOT "\n'
        '                                  "be forwarded to Integration Review.")\n'
        "                            try:\n"
        "                                snap = SnapshotManager()\n"
        "                                snap.revert_all()\n"
        '                                print("  [StreamServer] Rolled back to pre-task snapshot")\n'
        "                            except Exception as snap_err:\n"
        '                                print(f"  [StreamServer] Snapshot rollback failed: {snap_err}")\n'
        "                            return"
    )
    if old_post_disc in srv_text:
        srv_text = srv_text.replace(old_post_disc, new_post_disc, 1)
        log(" OK ", "pipeline_stream_server.py: added rollback + critical warning to POST disconnect handler")
    else:
        log("SKIP", "pipeline_stream_server.py: POST disconnect pattern not found")

    # Update SSE GET handler disconnect
    old_sse_disc = (
        '            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):\n'
        '                print("  [StreamServer] Client disconnected")\n'
        '                break'
    )
    new_sse_disc = (
        "            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError) as conn_err:\n"
        '                print(f"  [StreamServer] Client disconnected: {conn_err}")\n'
        "                print(\"  [StreamServer] CRITICAL: Stream socket dropped during SSE -- \"\n"
        '                      "truncated data will NOT be forwarded to downstream reviewers.")\n'
        "                try:\n"
        "                    snap = SnapshotManager()\n"
        "                    snap.revert_all()\n"
        '                    print("  [StreamServer] Rolled back to pre-task snapshot")\n'
        "                except Exception as snap_err:\n"
        '                    print(f"  [StreamServer] Snapshot rollback failed: {snap_err}")\n'
        "                break"
    )
    if old_sse_disc in srv_text:
        srv_text = srv_text.replace(old_sse_disc, new_sse_disc, 1)
        log(" OK ", "pipeline_stream_server.py: added rollback + critical warning to SSE disconnect handler")
    else:
        log("SKIP", "pipeline_stream_server.py: SSE disconnect pattern not found")

    srv_fp.write_text(srv_text, encoding="utf-8")
    log(" OK ", "pipeline_stream_server.py: saved Directive-D changes")


# ═══════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════

def main():
    print()
    print("=" * 70)
    print("  Midway Pipeline \u2014 Four-Directive Hardening Patch")
    print("=" * 70)

    print()
    print("\u2500" * 70)
    print("  DIRECTIVE A \u2014 File-Level Sandboxing (Domain Enforcement)")
    print("\u2500" * 70)
    patch_directive_a()

    print()
    print("\u2500" * 70)
    print("  DIRECTIVE B \u2014 Differential Enforcement of Mandates (Reviewer)")
    print("\u2500" * 70)
    patch_directive_b()

    print()
    print("\u2500" * 70)
    print("  DIRECTIVE C \u2014 Context Pruning in Fix Cycles")
    print("\u2500" * 70)
    patch_directive_c()

    print()
    print("\u2500" * 70)
    print("  DIRECTIVE D \u2014 Stream Resilience & State Rollback")
    print("\u2500" * 70)
    patch_directive_d()

    # Summary
    print()
    print("=" * 70)
    ok_count = sum(1 for m in PATCH_LOG if m.startswith("[ OK "))
    skip_count = sum(1 for m in PATCH_LOG if m.startswith("[SKIP"))
    warn_count = sum(1 for m in PATCH_LOG if m.startswith("[WARN"))
    fail_count = sum(1 for m in PATCH_LOG if m.startswith("[FAIL"))
    total = len(PATCH_LOG)

    print(f"  Patch Summary:")
    print(f"    OK:   {ok_count:3d} / {total}")
    print(f"    SKIP: {skip_count:3d} / {total}")
    print(f"    WARN: {warn_count:3d} / {total}")
    print(f"    FAIL: {fail_count:3d} / {total}")
    print("=" * 70)

    for entry in PATCH_LOG:
        print(f"  {entry}")

    print()
    print("=" * 70)
    print("  Files modified:")
    for fn in [
        "domain_registry.py",
        "_pipeline_helpers.py",
        "_prompts.py",
        "mesh_finalize.py",
        "ollama_client.py",
        "pipeline_stream_server.py",
    ]:
        fpath = BASE_DIR / fn
        if fpath.is_file():
            print(f"    - {fn} ({len(fpath.read_bytes())} bytes)")
    print("=" * 70)
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
