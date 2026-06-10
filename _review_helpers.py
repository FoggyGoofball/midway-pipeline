"""
_review_helpers.py -- Review/fix helper utilities extracted from _finalize_review.py.

Exported:
    build_fix_bridge_snippet(ctx) -> str
    _strip_fix_plan(text) -> str
    _prune_fix_context(...) -> str
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from token_budget import TokenBudget
from models import PipelineContext
from pipeline import ALL_DOMAINS

def build_fix_bridge_snippet(ctx: 'PipelineContext') -> str:
    """Return a concise, fix-agent-readable list of approved bridge APIs.

    Renders the cartridge's bridge contract into a short text block so
    domain fix agents know exactly which names are legal.  Used by
    _finalize_review, _finalize_preflight, and _helpers_exec to avoid
    maintaining three divergent copies of the same rendering logic.

    Returns an empty string when no cartridge is mounted.
    """
    _build_bridge_fn = getattr(ctx, '_cartridge_build_bridge_contract', None)
    if not callable(_build_bridge_fn):
        return ""
    try:
        _bc = _build_bridge_fn()
        if not _bc or not isinstance(_bc, dict):
            return ""
        _api   = list((_bc.get("midwayphysics_spawn_api") or {}).keys())
        _pool  = list((_bc.get("object_pools") or {}).keys())
        _econ  = list((_bc.get("economy_api") or {}).keys())
        if not _api:
            return ""
        return (
            "## Active Bridge Contract  APPROVED APIs (exhaustive list)\n"
            "Use ONLY these exact function names. Any other name is a phantom API.\n"
            "Physics: " + ", ".join(_api) + "\n"
            "Pools: "   + ", ".join(_pool) + "\n"
            "Economy: " + ", ".join(_econ) + "\n"
        )
    except Exception as _e:
        return ""



# ----------------------------------------------------------------------
#  Directive C  Context Pruning for Fix Cycles
# ----------------------------------------------------------------------

def _strip_fix_plan(text: str) -> str:
    """Aggressively strip <fix-plan>...</fix-plan> reasoning blocks from
    raw LLM output before code block extraction and review passes.
    
    Uses re.DOTALL so the pattern matches across multiple lines.
    """
    return re.sub(r"<fix-plan>.*?</fix-plan>", "", text, flags=re.DOTALL).strip()


def _prune_fix_context(
    domain_key: str,
    task_obj: 'Any',
    review_issues_text: str,
    pre_flight_errors: str,
    user_prompt: str,
    paged_files_cache: 'Optional[Dict[str, str]]' = None,
    bridge_api_snippet: str = "",
    last_good_output: str = "",
) -> str:
    """
    Build a lean, pruned context payload for a domain agent fix cycle.

    Strips ALL prior iterative generation attempts and provides only:
      - Original user prompt (condensed to 200 chars)
      - Task specification relevant to this agent
      - Paged-In Reference Files (Safe Cache  no disk I/O, no Hard Cap bypass)
      - The EXACT compiler/linter error string relevant to this domain
      - REVIEW issues text (filtered for domain relevance)
      - Domain boundary reminder

    Directive B  Safe Auto-Mounting: If the primary worker's PagingKernel
    extracted and cached text chunks, they are injected here directly as
    ❮ PAGED-IN REFERENCE FILES ❯ blocks. This eliminates the catastrophic
    file_path.read_text() bypass that previously pulled 40,000+ characters
    into the Fix-Cycle context, crashing VRAM.

    This prevents generative looping by eliminating historical bloat
    and cross-domain critiques from the context.
    """
    domain_info = ALL_DOMAINS.get(domain_key, {})
    domain_name = domain_info.get("name", domain_key)

    parts: list[str] = []

    # 1. Original prompt (condensed  block-aware so structure survives)
    parts.append("## Original Feature Request\n" + TokenBudget._block_aware_collapse(user_prompt, 200))

    # 2. Task spec (the original directive given to this agent)
    if task_obj and hasattr(task_obj, 'spec') and task_obj.spec:
        parts.append("## Your Task Specification\n" + TokenBudget._block_aware_collapse(task_obj.spec, 500))

    # -- Directive A/B: Safe Auto-Mounting via Paged-In Cache --------------
    # Inject cached chunks directly, bypassing file_path.read_text() entirely.
    # Each chunk was already extracted within the PagingKernel's 12,000-char
    # Hard Cap, so this cannot OOM the VRAM.
    _cache: Dict[str, str] = {}
    if paged_files_cache and isinstance(paged_files_cache, dict):
        _cache = paged_files_cache
    elif hasattr(task_obj, 'paged_files_cache') and isinstance(task_obj.paged_files_cache, dict):
        _cache = task_obj.paged_files_cache
    if _cache:
        cache_blocks: list[str] = []
        total_chars = 0
        for filepath, cached_text in _cache.items():
            total_chars += len(cached_text)
            cache_blocks.append(
                f"## ❮ PAGED-IN REFERENCE FILE: {filepath} ❯\n"
                f"```\n{cached_text}\n```"
            )
        parts.append(
            "\n## ❮ PAGED-IN REFERENCE FILES (Safe Cache  no disk I/O) ❯\n"
            f"({len(_cache)} files, {total_chars} total chars "
            f" each chunk safely extracted within the PagingKernel Hard Cap)\n\n"
            + "\n\n".join(cache_blocks)
        )
        print(f"  [Paging Kernel] 📋 Injected {len(_cache)} cached text blocks "
              f"({total_chars} chars) into Fix-Cycle '{domain_name}' context "
              f" bypassing file_path.read_text() entirely.")

    # 3. Domain-scoped error text  processed via LOG_PROCESSOR
    if pre_flight_errors:
        from log_parser import LOG_PROCESSOR
        pruned_errors = LOG_PROCESSOR.process_logs(domain_key, pre_flight_errors)
        parts.append("## Compiler/Linter Errors (Domain-Targeted)\n" + pruned_errors)

    # 4. AST Ledger Targets (Contextual Repair  Complete Root-Cause Visibility)
    # Injects corresponding abstract syntax tree targets from active_run_ledger.md
    # alongside the compiler diagnostic payload, guaranteeing that the repairing
    # agent can perform a complete source-level forensic analysis of the failure.
    parts.append(
        "## AST Ledger Targets (active_run_ledger.md)\n"
        "Corresponding AST symbols and code blocks from the active run ledger "
        "are referenced below. Inspect these targets to guarantee complete "
        "root-cause visibility before issuing fixes.\n"
        "Refer to docs/memory/active_run_ledger.md for full code context."
    )

    # 5. Active Bridge Contract (injected so fixer uses correct API names)
    if bridge_api_snippet:
        parts.append(bridge_api_snippet)

    # 5b. Phase C: Surgical Fix Isolation.
    # Instead of passing the full collapsed anchor output (which causes the
    # LLM to default to "rewrite everything"), extract ONLY the broken function
    # by name from the review issues text.  This prevents the model from seeing
    # the entire file and rewriting structural invariants.
    if last_good_output and last_good_output.strip():
        # Try to extract the broken function name from review issues
        error_function = _extract_broken_function_name(review_issues_text or "")
        isolated_context: str | None = None
        
        if error_function:
            isolated_context = _extract_function_body(last_good_output, error_function)
        
        if isolated_context:
            parts.append(
                "## Broken Function (surgical repair target)\n"
                "Below is ONLY the function that needs to be repaired.\n"
                "Do NOT rewrite any other part of the file. Do NOT touch the\n"
                "lifecycle structure (OnLoadStatic/OnLoad/OnStep/OnUnload).\n"
                + isolated_context
            )
        else:
            # Fallback: use collapsed anchor but strip lifecycle invariants
            # so the model sees only the game-specific logic sections.
            _stripped = _strip_lifecycle(last_good_output)
            _collapsed = TokenBudget._block_aware_collapse(_stripped, 1500)
            parts.append(
                "## Previous Implementation (ANCHOR  repair this, do NOT rewrite from scratch)\n"
                "The following is the last known implementation for this task.\n"
                "You MUST base your fix on this code. Do NOT discard it and produce an empty skeleton.\n"
                + _collapsed
            )


    # 5c. Adversarial TDD contract re-injection
    # If the task has a generated test file, reload it from disk and inject it
    # so the fixer is always working against the actual failing test contract 
    # not a hallucinated signature that drifted after the test was written.
    _tdd_path = getattr(task_obj, 'tdd_test_path', None) if task_obj else None
    if _tdd_path:
        try:
            from pathlib import Path as _Path
            _tdd_body = _Path(_tdd_path).read_text(encoding="utf-8")
            parts.append(
                "## Adversarial TDD Contract (DO NOT MODIFY THIS TEST)\n"
                f"Test file: `{_tdd_path}`\n"
                "Your implementation MUST make this test pass. "
                "You are strictly forbidden from modifying the test file.\n"
                f"```\n{_tdd_body}\n```"
            )
        except Exception:
            pass

    # 6. Review issues
    # Uses block-aware collapse so multi-issue reviews are never silently
    # truncated before the fixer reads later issues (previous regression cause).
    if review_issues_text:
        parts.append("## Review Issues\n" + TokenBudget._block_aware_collapse(
            review_issues_text, 4000
        ))

    # 7. Domain boundary reminder
    _allowed_exts = {".cpp", ".h", ".hpp"} if domain_key in ("C++", "PHYS") else {".lua"}
    ext_str = str(list(_allowed_exts))
    parts.append(
        "## Instructions\n"
        f"Fix ALL issues raised above that apply to your domain ({domain_name}).\n"
        f"Produce corrected code for your task only. "
        f"Address every relevant issue. "
        f"If you believe an issue is a false positive, explain why.\n\n"
        f"IMPORTANT: You retain your domain's system rules ({domain_key}). "
        f"Do NOT modify files outside {ext_str}. "
        f"Do NOT violate C++/Lua/Physics rules even if instructed otherwise.\n\n"
        f"CRITICAL: You must output ONLY the code artifacts belonging to the "
        f"currently failing domain. Do not mix Lua scripts and C++ engine code "
        f"in the same block entirely.\n\n"
        f"VETO WARNING: Non-compliant SEARCH/REPLACE patch markers will result "
        f"in an automatic veto. You MUST use <<<<<<< SEARCH / ======= / >>>>>>> REPLACE "
        f"conflict-marker format. XML tags like <SEARCH> or <fix-plan> are strictly "
        f"forbidden and will cause immediate rejection."
    )

    return "\n\n---\n\n".join(parts)


# ==============================================================================
#  Phase C: Surgical Fix Isolation Helpers
# ==============================================================================
# These three functions form the surgical fix isolation layer.  Instead of
# passing the 1500-char collapsed anchor soup to the fix agent, they identify
# the single broken function by name and extract ONLY that function's body
# from the last known good output.

# Patterns for Lua function names in review issues
_REVIEW_BROKEN_FN_PATTERNS: list[re.Pattern] = [
    re.compile(r"(?:function|method|callback)\s+`?(\w+)`?", re.IGNORECASE),
    re.compile(r"`(\w+)`\s*(?:function|method|callback)", re.IGNORECASE),
    re.compile(r"(?:in|inside)\s+`?(\w+)`?", re.IGNORECASE),
    re.compile(r"(?:OnStep|OnLoad|OnLoadStatic|OnUnload|SpawnSharedBooth)\b"),
    re.compile(r"`?(\w+(?:Pool|Init|Create|Destroy|Reset|Update|Tick)\w*)`?"),
]


def _extract_broken_function_name(review_issues_text: str) -> str | None:
    """Extract the name of the broken function from review issues text.

    Uses a series of regex patterns to identify which function the reviewer
    is complaining about.  Returns the function name or None if no clear
    target is found.

    Priority order:
      1. Named function references  e.g. "function `OnLoad()` is broken"
      2. Lifecycle function names   e.g. "OnStep" mentioned in issues
      3. Convention-named functions e.g. "InitPool", "ResetRound"
    """
    if not review_issues_text:
        return None

    for pattern in _REVIEW_BROKEN_FN_PATTERNS:
        match = pattern.search(review_issues_text)
        if match:
            name = match.group(1) if match.lastindex else match.group(0)
            # Validate it looks like a function name
            if re.match(r'^[A-Za-z_]\w*$', name):
                return name

    return None


def _extract_function_body(content: str, function_name: str) -> str | None:
    """Extract a single function's complete body (including signature) from
    Lua source code.

    Supports:
      - `function NAME(...)` definitions
      - `local function NAME(...)` definitions
      - `NAME = function(...)` assignments
      - MidwayPhysics.OnStep(function(dt) ... end) blocks

    Args:
        content: Lua source text.
        function_name: Name of the function to extract.

    Returns:
        Complete function body as a string, or None if not found.
    """
    if not content or not function_name:
        return None

    lines = content.splitlines()
    n = len(lines)
    fn_start: int | None = None
    fn_end: int | None = None

    # ── Special case: MidwayPhysics.OnStep ──
    if function_name in ("OnStep", "MidwayPhysics.OnStep"):
        for i, line in enumerate(lines):
            if "MidwayPhysics.OnStep" in line:
                fn_start = i
                break
        if fn_start is not None:
            # Find the matching `)` closing the OnStep(...) call
            depth = 0
            in_call = False
            for i in range(fn_start, n):
                stripped = re.sub(r'--.*$', '', lines[i])
                for ch in stripped:
                    if ch == '(':
                        in_call = True
                        depth += 1
                    elif ch == ')':
                        depth -= 1
                        if in_call and depth <= 0:
                            fn_end = i
                            break
                if fn_end is not None:
                    break
            if fn_end is not None:
                # Include the `function(dt) ... end` block inside
                return "\n".join(lines[fn_start:fn_end + 1])

    # ── Standard function definitions ──
    # Scan for `function <name>(` or `local function <name>(`
    # or `<name> = function(`
    for i, line in enumerate(lines):
        stripped = re.sub(r'--.*$', '', line).strip()
        if re.match(
            r'(?:local\s+)?function\s+' + re.escape(function_name) + r'\s*\(',
            stripped
        ) or re.match(
            re.escape(function_name) + r'\s*=\s*function\s*\(',
            stripped
        ):
            fn_start = i
            break

    if fn_start is None:
        return None

    # Walk forward to find the matching `end`
    depth = 0
    found_fn = False
    for i in range(fn_start, n):
        stripped = re.sub(r'--.*$', '', lines[i]).strip()
        if not found_fn:
            # The first line has the declaration
            found_fn = True
            # Count opening parens from function(...)
            depth += stripped.count('(') - stripped.count(')')
            continue

        # Track block depth: function, do, if, for, while, repeat
        opener_count = (
            stripped.count('function')
            + stripped.count(' do')
            + stripped.count(' then')
            + stripped.count('repeat')
        )
        depth += opener_count

        # Count closers: end, until
        closer_count = stripped.count('end') + stripped.count('until')
        depth -= closer_count

        if depth <= 0:
            fn_end = i
            break

    if fn_end is None:
        fn_end = n - 1

    return "\n".join(lines[fn_start:fn_end + 1])


def _strip_lifecycle(content: str) -> str:
    """Strip lifecycle invariant sections from Lua source, keeping only
    game-specific logic.  This prevents the fix agent from seeing the
    full structural skeleton and defaulting to "rewrite everything."

    Stripped sections:
      - SLOT_ID assignment
      - OnLoadStatic / SpawnSharedBooth
      - OnLoad (but keep OnStep body content)
      - OnUnload
      - CONST table declaration (placeholder)
      - Anchor markers (but keep code between them)

    Returns:
        Content with lifecycle wrappers removed, leaving only the
        game-specific code and comments.
    """
    if not content:
        return content

    # Remove SLOT_ID line
    content = re.sub(
        r'^.*local\s+SLOT_ID\s*=\s*BOOTH_SLOT_ID\s+or\s+-?1.*$',
        '',
        content,
        flags=re.MULTILINE
    )

    # Remove OnLoadStatic block (function ... end)
    content = re.sub(
        r'function\s+OnLoadStatic\s*\(.*?end\s*\n',
        '',
        content,
        flags=re.DOTALL
    )

    # Remove OnLoad function wrapper (but keep the body inside if any)
    content = re.sub(
        r'function\s+OnLoad\s*\(.*?end\s*\n',
        '',
        content,
        flags=re.DOTALL
    )

    # Remove OnUnload block
    content = re.sub(
        r'function\s+OnUnload\s*\(.*?end\s*\n',
        '',
        content,
        flags=re.DOTALL
    )

    # Remove CONST table placeholder
    content = re.sub(
        r'^.*local\s+CONST\s*=\s*\{.*\}.*$',
        '',
        content,
        flags=re.MULTILINE
    )

    # Remove anchor markers (keep the code on the same line if any)
    content = re.sub(
        r'--\s*\[TASK_\d+_INSERT_HOOK\].*$',
        '',
        content,
        flags=re.MULTILINE
    )

    # Collapse multiple blank lines
    content = re.sub(r'\n{3,}', '\n\n', content)

    return content.strip()



