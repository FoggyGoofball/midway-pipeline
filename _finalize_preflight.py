"""
_finalize_preflight.py — Pre-Flight Checks & Architect Fix
===========================================================
Extracted from mesh_finalize.py — handles background compilation,
syntax validation, and the Architect Syntax Fix invocation loop.

Exported:
    _run_preflight_checks(ctx) -> PipelineContext
"""

from __future__ import annotations

import re
from patch_regexes import SEARCH_REPLACE_PATTERN
import subprocess
import sys
from typing import Any, Dict, List, Optional

from models import PipelineContext
from _pipeline_helpers import atomic_write_text
from token_budget import TokenBudget
import _prompts as _prompts_mod  # live module ref — reads post-bootstrap values
from pipeline import (
    PROJECT_ROOT,
    call_ollama,
)


# ──────────────────────────────────────────────────────────────────────
#  Pre-Flight Checks: Compilation & Syntax Validation
# ──────────────────────────────────────────────────────────────────────

def _flush_results_to_workspace(ctx: PipelineContext) -> None:
    """Pre-compilation file sync: flush all result content to disk before build."""
    for tid, content in ctx.all_results_dict.items():
        task = ctx.task_map.get(tid)
        if task and task.target_file:
            target_path = ctx.project_root / task.target_file
            clean_content = _strip_search_replace_metadata(content)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(target_path, clean_content)


def _strip_search_replace_metadata(content: str) -> str:
    """Sanitize SEARCH/REPLACE blocks: apply diff instructions natively in memory.

    Handles multiple model-generated patch formats robustly:

    Format 1 — canonical conflict-marker style (correct format):
        <<<<<<< SEARCH
        <old content>
        =======
        <new content>
        >>>>>>> REPLACE

    Format 2 — markdown-header style (model hallucination, tolerated):
        ### SEARCH
        <old content>
        ### REPLACE
        <new content>

    Both formats are matched case-insensitively and tolerate extra angle-bracket
    repetitions, extra hash characters, surrounding whitespace, colons, dashes,
    and underscores around the SEARCH / REPLACE keywords.

    Strategy for Format 2: strip the entire
        ### SEARCH\n<old>\n### REPLACE\n
    span (leaving only the REPLACE content in place), which safely handles
    sequences of multiple consecutive blocks in a single pass.
    """
    import re as _re
    result = content

    # ── Format 1: canonical conflict-marker style ─────────────────────────
    # Tolerates: extra < / > / = chars, surrounding spaces, lowercase.
    # e.g.  <<<< search ... ==== ... >>>> replace
    canonical = _re.compile(
        r'<{3,9}[ \t]*search[ \t]*\n(.*?)\n={3,9}[ \t]*\n(.*?)\n>{3,9}[ \t]*replace[ \t]*',
        _re.DOTALL | _re.IGNORECASE,
    )
    for match in canonical.finditer(content):
        result = result.replace(match.group(0), match.group(2), 1)

    # ── Format 2: markdown-header style ──────────────────────────────────
    # Strip everything from ### SEARCH up to and including the ### REPLACE
    # header line, leaving the replacement content intact in place.
    # Tolerates: 1-6 # chars, spaces/dashes/underscores/colons around keyword,
    # lowercase, e.g.  ## search:  /  #### SEARCH --  /  # replace
    md_strip = _re.compile(
        r'#{1,6}[ \t_\-]*search[ \t_\-:]*\n'   # ### SEARCH header
        r'.*?'                                    # old content (non-greedy)
        r'#{1,6}[ \t_\-]*replace[ \t_\-:]*\n',  # ### REPLACE header (consumed)
        _re.DOTALL | _re.IGNORECASE,
    )
    result = md_strip.sub('', result)

    return result


_COMMENT_ONLY_RE = re.compile(
    r'^\s*(//[^\n]*|/\*.*?\*/|#[^\n]*)\s*$',
    re.DOTALL,
)


def _is_comment_only(content: str) -> bool:
    """Return True if content is nothing but comments and whitespace."""
    # Strip fenced code block markers, then check.
    stripped = re.sub(r'```[^\n]*\n?|```', '', content).strip()
    return bool(_COMMENT_ONLY_RE.match(stripped)) or not stripped


# Detects a meaningful code block: at least one SEARCH/REPLACE pair, or a real
# language keyword / declaration line. Bare triple-backtick fence markers are
# intentionally excluded — a fenced block that contains only delegation signals
# must NOT be treated as a real code implementation.
_HAS_CODE_RE = re.compile(
    r'(<{7}\s*SEARCH|\bfunction\b|\bclass\b|\bdef\b|\bvoid\b|\bint\b|\breturn\b|'
    r'\blocal\s+\w+\s*=\s*(?:function\b|"[^"]*"|\d+|MidwayPhysics\.|\{))',
    re.IGNORECASE,
)
# Fix K: 'local' keyword only counts as code if it's followed by an assignment
# to a function, string, number, MidwayPhysics call, or table literal.
# A bare 'local handle' or 'local handle = nil' is NOT real code — it's a
# variable stub that the model puts in as a placeholder.



# Patterns that indicate the output is a pure signal/delegation with no real code.
# Matches DELEGATE, QUERY, CONF, REVISE, APPROVE, APPEAL, VETO, OBJECT, RECOURSE
# and similar inter-agent signal lines so they are excluded from the "has real code"
# check and from the non-signal line count.
_DELEGATE_ONLY_RE = re.compile(
    r'^\s*(\[DELEGATE:|\[QUERY:|\[CONF:|\[REVISE:|\[APPROVE\]|\[APPEAL:|\[VETO:|\[OBJECT:|\[RECOURSE:|\[CONSULT:|\[REJECT:|\[MERGE:|\[FLUSH\]|\[RESULT:)',
    re.IGNORECASE | re.MULTILINE,
)

def _task_has_code(content: str) -> bool:
    """Return True if *content* contains at least one concrete code construct
    AND is not a pure delegation/signal response.

    Rejects outputs that are primarily markdown table rows (hallucinated API
    ledgers) even when they contain function keywords, since those keywords
    appear inside table cell text, not as real code constructs.
    """
    if not content or not content.strip():
        return False
    # If the entire (stripped) content is only signal lines, treat as no-code.
    non_signal_lines = [
        ln for ln in content.splitlines()
        if ln.strip() and not _DELEGATE_ONLY_RE.match(ln)
    ]
    if not non_signal_lines:
        return False
    # Ledger-hallucination guard: if the majority of non-empty lines look like
    # markdown table rows (start with '|'), the LLM dumped a ledger table, not code.
    _table_rows = sum(1 for ln in non_signal_lines if ln.strip().startswith('|'))
    if _table_rows > 0 and _table_rows / len(non_signal_lines) > 0.40:
        return False
    # Must contain a real code construct in the non-signal portion of the output.
    # We search the joined non-signal lines rather than the full raw content so
    # that keywords buried inside APPEAL/APPROVE prose cannot satisfy the check.
    non_signal_text = "\n".join(non_signal_lines)
    return bool(_HAS_CODE_RE.search(non_signal_text))


def _inject_empty_output_errors(ctx: PipelineContext) -> None:
    """
    Universal guard: if a task result contains only prose with no code, inject a
    pre-flight error so the fix loop forces a proper implementation before review.
    This check is project-agnostic — empty outputs are always a failure regardless
    of domain or technology.
    """
    for tid, content in list(ctx.all_results_dict.items()):
        if not content or not content.strip() or _is_comment_only(content):
            ctx.pre_flight_errors += (
                f"\n## Empty Output — Task {tid}\n"
                f"Task {tid} produced no output at all (or only comments). "
                f"The agent must provide a concrete implementation.\n"
            )
            print(f"  [Pre-Flight] EMPTY OUTPUT detected for task {tid} — injecting fix demand.")
        elif _DELEGATE_ONLY_RE.search(content) and not _task_has_code(content):
            ctx.pre_flight_errors += (
                f"\n## Delegation Signal Only — Task {tid}\n"
                f"Task {tid} responded with only a [DELEGATE/QUERY/CONF] signal and no "
                f"concrete code. Delegation signals are forbidden as a substitute for "
                f"implementation. The agent MUST produce a complete code block.\n"
            )
            print(f"  [Pre-Flight] DELEGATE-ONLY output detected for task {tid} — injecting fix demand.")
        elif _DELEGATE_ONLY_RE.search(content[:500]) and not _task_has_code(content):
            # Delegate signal buried at the start of a long hallucinated dump.
            ctx.pre_flight_errors += (
                f"\n## Delegation Signal Buried in Output — Task {tid}\n"
                f"Task {tid} began with a [DELEGATE/QUERY/CONF] signal followed by "
                f"non-code content (e.g., a ledger table dump). This is not a valid "
                f"implementation. The agent MUST produce a complete working code block.\n"
            )
            print(f"  [Pre-Flight] DELEGATE+HALLUCINATION detected for task {tid} — injecting fix demand.")
        # ── Orphaned SEARCH/REPLACE check ──
        # If the LLM output contains SEARCH/REPLACE blocks (canonical
        # <<<<<<< SEARCH format or markdown ### SEARCH format) but no
        # ### task_X header, do NOT throw a NO CODE BLOCK error. Instead,
        # apply the blocks to the target file and propagate the patched
        # result to ALL tasks targeting that file so they are all resolved.
        _has_sr = bool(re.search(
            r'(?:<{3,9}\s*SEARCH|#{1,6}\s*SEARCH)', content, re.IGNORECASE
        ))
        if _has_sr:
            # SEARCH/REPLACE blocks detected — apply them to the target file
            # and propagate to all tasks sharing that target_file so they
            # are all considered resolved.
            _task_obj = ctx.task_map.get(tid)
            if _task_obj and _task_obj.target_file:
                _target_path = ctx.project_root / _task_obj.target_file
                if _target_path.is_file():
                    _file_content = _target_path.read_text(encoding="utf-8", errors="replace")
                    _patched_content = _strip_search_replace_metadata(content)
                    if _patched_content != content:
                        # Before broadcasting, verify the patched content doesn't
                        # contain static-guard violations.  A SEARCH/REPLACE block
                        # that introduces phantom APIs (e.g. SpawnStaticMesh with a
                        # string path, BUTTON.x, SLOT_X globals) must never be
                        # propagated to sibling tasks — that would poison every task
                        # sharing the file with the same bad pattern.
                        _SR_GUARD_PATTERNS = [
                            re.compile(r'MidwayPhysics\.SpawnStaticMesh\s*\(\s*[\'"]', re.IGNORECASE),
                            re.compile(r'MidwayPhysics\.SpawnStaticPlane', re.IGNORECASE),
                            re.compile(r'MidwayPhysics\.ApplyForce\b', re.IGNORECASE),
                            re.compile(r'\bBUTTON\s*\.', re.MULTILINE),
                            re.compile(r'\bSLOT_[XYZ]\b', re.MULTILINE),
                            re.compile(r'\bSharedBooth\s*\.', re.MULTILINE),
                            re.compile(r'\b(?:Mouse|Input)\s*\.', re.MULTILINE),
                            re.compile(r'\bAttractionConstants\.(?:initialize|get)\w+\s*\(', re.IGNORECASE),
                            re.compile(r'\bMidwayPhysics\.OnLoadStatic\s*\(', re.IGNORECASE),
                            re.compile(r'\bsol\s*\.\s*(?:set_function|new_usertype|state)\s*\(', re.IGNORECASE),
                        ]
                        _sr_guard_hit = next(
                            (p for p in _SR_GUARD_PATTERNS if p.search(_patched_content)),
                            None,
                        )
                        if _sr_guard_hit:
                            ctx.pre_flight_errors += (
                                f"\n## Static Pattern Violation — Task {tid} [SEARCH/REPLACE blocked]\n"
                                f"**Rule:** SEARCH/REPLACE output contains a static-guard violation "
                                f"(matched: `{_sr_guard_hit.pattern}`) and was NOT broadcast to sibling tasks.\n"
                                f"Rewrite task {tid} without phantom APIs, undefined globals (BUTTON, SLOT_X, "
                                f"SharedBooth), or unsupported SpawnStaticMesh overloads.\n"
                            )
                            print(f"  [Pre-Flight] ⛔ SEARCH/REPLACE for task {tid} blocked — static guard hit: {_sr_guard_hit.pattern}")
                            continue
                        # SEARCH/REPLACE was applied — store globally for all
                        # tasks targeting this file.
                        ctx.all_results_dict[tid] = _patched_content
                        # Sync all_results for the primary task.
                        _fp_f0 = False
                        for _fp_i0, _fp_e0 in enumerate(ctx.all_results):
                            if _fp_e0.get("task_id") == tid:
                                ctx.all_results[_fp_i0] = {"task_id": tid, "output": _patched_content}
                                _fp_f0 = True
                                break
                        if not _fp_f0:
                            ctx.all_results.append({"task_id": tid, "output": _patched_content})
                        for _otid, _otask in ctx.task_map.items():
                            if _otid != tid and _otask.target_file == _task_obj.target_file:
                                ctx.all_results_dict[_otid] = _patched_content
                                _fp_f1 = False
                                for _fp_i1, _fp_e1 in enumerate(ctx.all_results):
                                    if _fp_e1.get("task_id") == _otid:
                                        ctx.all_results[_fp_i1] = {"task_id": _otid, "output": _patched_content}
                                        _fp_f1 = True
                                        break
                                if not _fp_f1:
                                    ctx.all_results.append({"task_id": _otid, "output": _patched_content})
                        # Write patched content to disk
                        _target_path.parent.mkdir(parents=True, exist_ok=True)
                        atomic_write_text(_target_path, _patched_content)
                        print(f"  [Pre-Flight] ✅ SEARCH/REPLACE applied globally to {_task_obj.target_file} via {tid}")
                        continue
            # Fall through if SR blocks couldn't be applied — do NOT throw
            # NO CODE BLOCK error (SR content is real code even without headers).
        elif not _task_has_code(content):
            ctx.pre_flight_errors += (
                f"\n## No Code Block — Task {tid}\n"
                f"Task {tid} response contains only prose with no code construct. "
                f"The agent must produce at least one concrete code block "
                f"that implements the task specification.\n"
            )
            print(f"  [Pre-Flight] NO CODE BLOCK detected for task {tid} — injecting fix demand.")


def _inject_static_pattern_errors(ctx: PipelineContext) -> None:
    """Deterministic, compiler-free checks for patterns that are always wrong.

    These fire before any LLM reviewer sees the code, so they cannot be
    talked past by a permissive reviewer or skipped when no CMakeCache exists.
    Each guard targets a failure pattern witnessed in real pipeline runs.
    """
    # ── Guard patterns ────────────────────────────────────────────────────
    # Each entry: (domain_filter, regex, short_label, explanation)
    # domain_filter: None = all domains, otherwise only tasks for that agent.
    _GUARDS = [
        # Lua: require('nlohmann.json') — nlohmann is a C++ library.
        (
            "Lua",
            re.compile(r"""require\s*\(\s*['"]nlohmann""", re.IGNORECASE),
            "phantom require('nlohmann.json')",
            "nlohmann/json is a C++ library and cannot be require()'d from Lua. "
            "Use Engine.LoadJSON(path) or parse via the bridge contract instead.",
        ),
        # NOTE: SpawnDynamicBall, SpawnStaticBall, require('midway_physics'), and other
        # phantom API names are now caught universally by the contract validator (C9).
        # Individual entries here are no longer needed.
        # C++: lua.set_function("X.Y", ...) — sol2 dot-notation table paths.
        (
            "C++",
            re.compile(r'\.set_function\s*\(\s*"[A-Za-z_]\w*\.[A-Za-z_]\w*"', re.IGNORECASE),
            "wrong sol2 table registration (dot-notation in set_function)",
            'sol2 set_function() does not accept "Table.Method" dot-path strings. '
            'Use lua["Table"]["Method"] = ... to register table-scoped functions.',
        ),
        # C7: Lua calling any sol.* method — sol is a C++ binding layer with no Lua-side object.
        # Covers sol.set_function, sol.new_usertype, sol.state, sol.script, sol.log_message,
        # and chained calls like sol.input.is_action_pressed(...), sol.state.open_libraries(...).
        (
            "Lua",
            re.compile(r'\bsol\s*(?:\.[A-Za-z_]\w*)+\s*\(', re.IGNORECASE),
            "sol.* called from Lua — sol is a C++ binding layer with no Lua-side object",
            "'sol' is a C++ namespace/object and does not exist at Lua runtime. "
            "Remove all sol.* calls (including sol.input.*, sol.state.*, etc.) from Lua code. "
            "Use print() for logging; player input is handled by engine callbacks, not sol.",
        ),
        # C8 / C8b / C8c / C8d: Bare engine calls (DestroyBody, IsSensorTriggered,
        # SpawnXxx, ApplyImpulse, etc.) without the required namespace prefix are now
        # caught universally by the contract validator (C9 / bare-call pass).
        # F19: SpawnStaticPlane and other phantom MidwayPhysics.* names are also
        # caught by the contract validator.
        # C15: MidwayPhysics.log / .log_message — subsumed by contract validator.
        (
            "C++",
            re.compile(
                r'sol::state[^;]{0,200}(?:SpawnDynamic|SpawnStatic|SpawnKinematic|SpawnSensor)',
                re.DOTALL | re.IGNORECASE,
            ),
            "re-registration of existing bridge spawn function",
            "SpawnDynamic/Static/Kinematic/SensorXxx are already registered in the sol2 "
            "bridge contract. Re-adding them causes duplicate bindings. "
            "Remove the C++ registration task and use the existing API from Lua instead.",
        ),
        # F17: Singleton method bound as free function pointer.
        (
            "C++",
            re.compile(
                r'set_function\s*\(\s*"[^"]+"\s*,\s*&[A-Z][A-Za-z_0-9]*::[A-Z][A-Za-z_0-9]*\s*\)',
                re.IGNORECASE,
            ),
            "singleton method bound as free function pointer via set_function",
            "Instance methods on singletons (e.g. &Engine::GetStreak) cannot be bound "
            "directly as free function pointers. Wrap in a lambda: "
            '[]{ return Engine::GetStreak(); }',
        ),
        # F18: Engine.Method() dot-notation in C++ (should be Engine::Method()).
        (
            "C++",
            re.compile(r'\bEngine\.[A-Z][A-Za-z_0-9]*\s*\(', re.IGNORECASE),
            "Engine.Method() dot-notation in C++ — should be Engine::Method()",
            "C++ uses the :: scope operator, not the dot operator. "
            "Replace Engine.GetStreak() with Engine::GetStreak() (or via the singleton accessor).",
        ),
        # F18b: MidwayPhysics.Method() dot-notation in C++ (not Lua).
        (
            "C++",
            re.compile(r'\bMidwayPhysics\.[A-Z][A-Za-z_0-9]*\s*\(', re.IGNORECASE),
            "MidwayPhysics.Method() dot-notation in C++ — should be MidwayPhysics::Method()",
            "C++ uses the :: scope operator. "
            "Replace MidwayPhysics.ApplyImpulse(...) with MidwayPhysics::ApplyImpulse(...) etc.",
        ),
        # C10: Duplicate top-level function definition in Lua.
        # Detected via post-guard check below — regex finds all names first.
        # C12: Bare OnStep defined — reliable two-pass check (see below).
        # C++: re-registering existing bridge spawn functions.
        # C17: MidwayInput called with an unknown action name.
        # The only valid action strings are the five defined in MidwayInput.cpp.
        (
            "Lua",
            re.compile(
                r'\bMidwayInput\.IsActionDown\s*\(\s*["\'](?!fire|aim_left|aim_right|power_up|power_down)[^"\']+["\']',
                re.IGNORECASE,
            ),
            "unknown MidwayInput action name",
            "MidwayInput.IsActionDown only accepts: "
            '"fire", "aim_left", "aim_right", "power_up", "power_down". '
            "Use MidwayInput.IsKeyDown(name) for raw SDL key names instead.",
        ),
        # C18: MidwayInput.* dot-notation called from C++ (should use the bridge).
        (
            "C++",
            re.compile(r'\bMidwayInput\.[A-Z][A-Za-z_0-9]*\s*\(', re.IGNORECASE),
            "MidwayInput.Method() dot-notation in C++ — should be MidwayInput::Method()",
            "C++ uses the :: scope operator. "
            "Replace MidwayInput.Register(...) with MidwayInput::Register(...) etc.",
        ),
        # C16: require() referencing a wrapper or non-existent attraction file.
        # Attractions are loaded by AttractionManager, not via Lua require().
        (
            "Lua",
            re.compile(r'\brequire\s*\(\s*["\'](?:wrapped_|skeebalooks|skeeball_wrap)', re.IGNORECASE),
            "require() targeting a non-existent wrapper file",
            "Attraction scripts are loaded by AttractionManager directly. "
            "Do not use require() to load other attraction files. "
            "Define OnLoadAttraction() and OnUnload() directly in the target file.",
        ),
        # S1: Lua scaffold function — body is only a TODO comment or return nil stub.
        # Matches functions whose entire body (ignoring whitespace/comments) is one of:
        #   -- TODO / -- todo / -- placeholder / -- implement / -- stub / -- FIXME
        #   return nil / return false / return 0 / return {} / ... (Lua vararg pass-through)
        # These are never valid shipped implementations.
        (
            "Lua",
            re.compile(
                r'\bfunction\b[^\n]*\n'           # function header
                r'(?:\s*(?:--[^\n]*)?\n)*'        # optional leading comment lines
                r'\s*(?:'
                    r'--\s*(?:TODO|FIXME|stub|placeholder|implement\s+me|to[-\s]?do)\b'
                    r'|return\s+(?:nil|false|0|\{\s*\})'
                    r'|\.\.\.'
                r')\s*\n'
                r'(?:\s*(?:--[^\n]*)?\n)*'        # optional trailing comment lines
                r'\s*end\b',
                re.IGNORECASE | re.DOTALL,
            ),
            "scaffold/stub Lua function — body is a TODO, return nil, or ... pass-through",
            "The function contains only a placeholder body and is not a real implementation. "
            "Replace the stub body with a complete, working implementation.",
        ),
        # S2: C++ scaffold function — body contains only a TODO comment or a bare return.
        (
            "C++",
            re.compile(
                r'\)\s*(?:const\s*)?\{[^}]{0,200}'    # short function body
                r'(?://\s*(?:TODO|FIXME|stub|placeholder|implement\s+me|to[-\s]?do)\b'
                r'|/\*\s*(?:TODO|FIXME|stub|placeholder)[^*]*\*/'
                r'|\breturn\s*;\s*'
                r')',
                re.IGNORECASE | re.DOTALL,
            ),
            "scaffold/stub C++ function — body is a TODO comment or empty return",
            "The C++ function body is a placeholder and not a real implementation. "
            "Provide a complete function body with actual logic.",
        ),
        # G1: Undefined booth globals — BUTTON, SLOT_X/Y/Z, SharedBooth.
        # These are never defined anywhere in the Midway runtime and will crash at load.
        # Models hallucinate them from booth_shared.lua comments.
        (
            "Lua",
            re.compile(r'\b(?:BUTTON|SLOT_[XYZ]|SharedBooth)\b', re.MULTILINE),
            "undefined booth global (BUTTON / SLOT_X/Y/Z / SharedBooth)",
            "BUTTON, SLOT_X, SLOT_Y, SLOT_Z, and SharedBooth are NOT defined in the "
            "Midway runtime and will crash at load time. "
            "Remove every reference to these names. "
            "Replace them with plain numeric literals (e.g. 0, 1.0) declared as "
            "module-level local constants at the top of the file. "
            "Do NOT call SharedBooth.ButtonZ() or access BOOTH.width_x/height_y/depth_z — "
            "those table fields do not exist in the runtime either.",
        ),
        # G2: Undefined input globals — Mouse.Position() / Input.Pressed().
        # These namespaces are not in the engine bridge contract and will error at runtime.
        (
            "Lua",
            re.compile(r'\b(?:Mouse|Input)\s*\.', re.MULTILINE),
            "undefined input global (Mouse.* / Input.*)",
            "Mouse and Input are NOT in the Midway engine bridge contract. "
            "Remove all calls to Mouse.Position(), Input.Pressed(), and similar. "
            "Player input is delivered through the OnStep dt callback and "
            "AttractionConstants.modifiers — do not poll a Mouse or Input namespace.",
        ),
    ]

    # ── Pre-pass: deterministically strip known phantom AttractionConstants / MidwayPhysics
    # calls that the model repeatedly hallucinates.  These are stripped before the guard
    # loop so the guards see clean content and do not fire on already-removed phantoms.
    # Each entry: (regex_to_match_full_statement, replacement_string, description)
    _PHANTOM_STRIP_PATTERNS = [
        # AttractionConstants.initializeSkeeballMachine() — no such method in contract
        (
            re.compile(
                r'\bAttractionConstants\.initialize\w+\(\s*\)[^\n]*\n?',
                re.MULTILINE,
            ),
            "",
            "phantom AttractionConstants.initialize*()",
        ),
        # AttractionConstants.getSkeeballMachinePos() — no such method in contract
        (
            re.compile(
                r'\bAttractionConstants\.get\w+\([^)]*\)[^\n]*\n?',
                re.MULTILINE,
            ),
            "",
            "phantom AttractionConstants.get*()",
        ),
        # MidwayPhysics.OnLoadStatic() — not a real bridge function
        (
            re.compile(
                r'\bMidwayPhysics\.OnLoadStatic\(\s*\)[^\n]*\n?',
                re.MULTILINE,
            ),
            "",
            "phantom MidwayPhysics.OnLoadStatic()",
        ),
        # AttractionConstants.booth / BOOTH table — these fields don't exist
        (
            re.compile(
                r'^[\t ]*local\s+BOOTH\s*=\s*AttractionConstants\.booth[^\n]*\n',
                re.MULTILINE,
            ),
            "",
            "phantom AttractionConstants.booth table",
        ),
        # local SLOT_X/Y/Z = BOOTH.* — depends on the stripped BOOTH line
        (
            re.compile(
                r'^[\t ]*local\s+SLOT_[XYZ]\s*=\s*BOOTH\.\w+[^\n]*\n',
                re.MULTILINE,
            ),
            "",
            "phantom BOOTH.* slot dimension",
        ),
        # SharedBooth.ButtonZ() call sites
        (
            re.compile(
                r'SharedBooth\.ButtonZ\(\s*\)',
                re.MULTILINE,
            ),
            "0",
            "phantom SharedBooth.ButtonZ()",
        ),
    ]
    for tid, content in list(ctx.all_results_dict.items()):
        if not content:
            continue
        task_obj = ctx.task_map.get(tid) if ctx.task_map else None
        domain = getattr(task_obj, "agent", None) if task_obj else None
        if domain == "Lua":
            _stripped = False
            for (_pat, _repl, _desc) in _PHANTOM_STRIP_PATTERNS:
                _new_content = _pat.sub(_repl, content)
                if _new_content != content:
                    content = _new_content
                    _stripped = True
                    print(f"  [Phantom Strip] ✂ Task {tid}: removed {_desc}")
            if _stripped:
                ctx.all_results_dict[tid] = content

    for tid, content in list(ctx.all_results_dict.items()):
        if not content:
            continue
        task_obj = ctx.task_map.get(tid) if ctx.task_map else None
        domain = getattr(task_obj, "agent", None) if task_obj else None

        for (guard_domain, pattern, label, explanation) in _GUARDS:
            if guard_domain and domain and domain != guard_domain:
                continue
            if pattern.search(content):
                ctx.pre_flight_errors += (
                    f"\n## Static Pattern Violation — Task {tid} [{domain or '?'}]\n"
                    f"**Rule:** {label}\n"
                    f"**Why this is always wrong:** {explanation}\n"
                    f"Fix this before the reviewer sees the code.\n"
                )
                print(f"  [Static Guard] ❌ Task {tid} [{domain or '?'}]: {label}")

        # ── C6: SpawnDynamicXxx / SpawnStaticXxx argument count hard check ────
        # Maps function name → (min_args, max_args).
        # Optional trailing args (e.g. mass, yawDeg) widen the max bound.
        # The bridge registers mass via sol::object so it is always optional in Lua.
        _SPAWN_SIGS = {
            # name:                (min, max)
            "SpawnDynamicSphere":   (4, 5),  # lx ly lz r [mass]
            "SpawnDynamicBox":      (6, 7),  # lx ly lz w h d [mass]
            "SpawnDynamicCapsule":  (5, 6),  # lx ly lz halfH r [mass]
            "SpawnDynamicCylinder": (5, 6),  # lx ly lz halfH r [mass]
            "SpawnDynamicMesh":     (6, 6),  # lx ly lz yaw mass path
            "SpawnDynamicBoxR":     (7, 8),  # lx ly lz w h d mass [yawDeg]
            "SpawnDynamicSphereR":  (5, 6),  # lx ly lz r mass [yawDeg]
            "SpawnDynamicCapsuleR": (6, 7),
            "SpawnDynamicCylinderR":(6, 7),
            "SpawnStaticBox":       (6, 6),  # lx ly lz w h d
            "SpawnStaticSphere":    (4, 4),  # lx ly lz r
            "SpawnStaticCapsule":   (5, 5),  # lx ly lz halfH r
            "SpawnStaticCylinder":  (5, 5),  # lx ly lz halfH r
            "SpawnStaticMesh":      (4, 8),  # lx ly lz yaw path [sx sy sz]
            "SpawnStaticBoxR":      (7, 7),
            "SpawnStaticSphereR":   (5, 5),
            "SpawnStaticCapsuleR":  (6, 6),
            "SpawnStaticCylinderR": (6, 6),
            "SpawnKinematicBox":    (6, 6),
            "SpawnKinematicSphere": (4, 4),
            "SpawnKinematicCapsule":(5, 5),
            "SpawnKinematicCylinder":(5, 5),
            "SpawnKinematicBoxR":   (7, 7),
            "SpawnSensorBox":       (6, 6),
            "SpawnSensorSphere":    (4, 4),
        }
        def _balanced_spawn_args(text: str, start_pos: int) -> str:
            """Extract the full argument string between balanced parentheses
            starting at the open-paren at position start_pos.
            
            Replaces the naive regex '([^)]{0,200})' which breaks on inline
            arithmetic with nested parentheses like (i * ball_radius).
            
            NOTE: The opening '(' is NOT included in the returned string,
            and the closing ')' is stripped. This ensures comma-splitting
            logic (which tracks depth) does not see depth==1 immediately,
            which would cause all internal commas to be skipped.
            """
            _depth = 0
            _result = []
            for _ch in text[start_pos:]:
                if _ch == '(':
                    _depth += 1
                    if _depth == 1:
                        # Skip the outermost '(' — do NOT add it to _result
                        continue
                elif _ch == ')':
                    _depth -= 1
                    if _depth == 0:
                        break
                    # Closing paren at depth > 0 is a nested close — keep it
                if _depth >= 1:
                    _result.append(_ch)
            return "".join(_result).strip()

        if domain == "Lua":
            for _spawn_m in re.finditer(
                r'MidwayPhysics\.(Spawn\w+)\s*\(',
                content, re.IGNORECASE
            ):
                _fn_name = _spawn_m.group(1)
                # Use depth-tracker instead of naive [^)] regex for args extraction
                _args_str = _balanced_spawn_args(content, _spawn_m.start() + len(_spawn_m.group(0)) - 1)
                _expected = _SPAWN_SIGS.get(_fn_name)
                if _expected is None:
                    # Unknown spawn call — flag as phantom API (C9 overlap)
                    ctx.pre_flight_errors += (
                        f"\n## Static Pattern Violation — Task {tid} [Lua]\n"
                        f"**Rule:** phantom spawn API MidwayPhysics.{_fn_name}\n"
                        f"**Why this is always wrong:** {_fn_name} is not in the bridge contract. "
                        f"Check docs/engine_lua_bridge_contract.md for the approved list.\n"
                        f"Fix this before the reviewer sees the code.\n"
                    )
                    print(f"  [Static Guard] ❌ Task {tid} [Lua]: phantom spawn API {_fn_name}")
                    continue
                if _args_str:
                    # Count top-level commas (ignore commas inside nested parens
                    # AND nested Lua table literals like {x=0, y=0, z=0})
                    _depth = 0
                    _commas = 0
                    for _ch in _args_str:
                        if _ch in '({[':
                            _depth += 1
                        elif _ch in ')}]':
                            _depth -= 1
                        elif _ch == ',' and _depth == 0:
                            _commas += 1
                    _actual = _commas + 1
                    _min_exp, _max_exp = _expected
                    if not (_min_exp <= _actual <= _max_exp):
                        # Build a human-readable positional signature so the fix
                        # agent can copy the exact call pattern without guessing.
                        _POS_LABELS = {
                            "SpawnDynamicSphere":   "lx, ly, lz, radius [, mass]",
                            "SpawnDynamicBox":      "lx, ly, lz, w, h, d [, mass]",
                            "SpawnDynamicCapsule":  "lx, ly, lz, halfHeight, radius [, mass]",
                            "SpawnDynamicCylinder": "lx, ly, lz, halfHeight, radius [, mass]",
                            "SpawnDynamicMesh":     "lx, ly, lz, yaw, mass, path",
                            "SpawnDynamicBoxR":     "lx, ly, lz, w, h, d, mass [, yawDeg]",
                            "SpawnDynamicSphereR":  "lx, ly, lz, radius, mass [, yawDeg]",
                            "SpawnStaticBox":       "lx, ly, lz, w, h, d",
                            "SpawnStaticSphere":    "lx, ly, lz, radius",
                            "SpawnStaticCapsule":   "lx, ly, lz, halfHeight, radius",
                            "SpawnStaticCylinder":  "lx, ly, lz, halfHeight, radius",
                            "SpawnStaticMesh":      "lx, ly, lz, yaw, path [, sx, sy, sz]",
                            "SpawnKinematicBox":    "lx, ly, lz, w, h, d",
                            "SpawnKinematicSphere": "lx, ly, lz, radius",
                            "SpawnSensorBox":       "lx, ly, lz, w, h, d",
                            "SpawnSensorSphere":    "lx, ly, lz, radius",
                        }
                        _pos_hint = _POS_LABELS.get(_fn_name, f"{_min_exp}–{_max_exp} positional args")
                        # ── Fix G: Deterministic auto-patch ──────────────────────
                        # Instead of relying on the LLM fix loop (which wastes 4 cycles
                        # repeatedly getting arg counts wrong), apply a DIRECT string
                        # replacement to the task output right here. We know the exact
                        # bad call string from the regex match, and we know the correct
                        # min arg count.  We extract the first _min_exp positional args
                        # (ignoring trailing table literals, booleans, and other extras),
                        # then rebuild the call with exactly _min_exp args.
                        #
                        # Strategy:
                        #   1. Parse the args string into top-level tokens (comma-split
                        #      at depth 0, ignoring nested parens/braces).
                        #   2. Take only the first _min_exp positional args (these are
                        #      always numbers or variable names — never tables or strings).
                        #   3. If fewer than _min_exp were provided, pad with the last
                        #      usable value (e.g. if only 4 args for a 6-arg box, use
                        #      the 4th arg to fill h and d).
                        #   4. Build the corrected call and do a literal string replace
                        #      in ctx.all_results_dict[tid].
                        #
                        # This runs BEFORE the arch-fix cycle, so the fix model sees
                        # correct arg counts and can focus on real logic errors.
                        _bad_call_raw = _spawn_m.group(0) + _args_str + ")"  # e.g. "MidwayPhysics.SpawnKinematicBox(1.0, 1.0, 1.0, 0.5)"
                        _bad_args_raw = _args_str
                        if _bad_args_raw and _bad_call_raw in ctx.all_results_dict.get(tid, ""):
                            # Split args at depth 0 (respects table literals)
                            _depth_g = 0
                            _tokens_g: list = []
                            _current_g = ""
                            for _ch_g in _bad_args_raw:
                                if _ch_g in '({[':
                                    _depth_g += 1
                                elif _ch_g in ')}]':
                                    _depth_g -= 1
                                elif _ch_g == ',' and _depth_g == 0:
                                    _tokens_g.append(_current_g.strip())
                                    _current_g = ""
                                    continue
                                _current_g += _ch_g
                            if _current_g.strip():
                                _tokens_g.append(_current_g.strip())
                            # Keep only the first _min_exp positional args
                            _valid_tokens = _tokens_g[:_min_exp]
                            # If short, pad by repeating the last usable value.
                            # "Usable" = a number or identifier (not a string/table).
                            _last_usable = 1.0  # safe default for any missing dimension
                            for _tk in reversed(_valid_tokens):
                                try:
                                    _last_usable = float(_tk)
                                    break
                                except (ValueError, TypeError):
                                    # Variable name like 'BALL_RADIUS' — use its value as hint
                                    if _tk.isidentifier():
                                        _last_usable = _tk  # keep as var name
                                        break
                            while len(_valid_tokens) < _min_exp:
                                _valid_tokens.append(str(_last_usable))
                            _corrected_args = ", ".join(_valid_tokens)
                            _corrected_call = f"MidwayPhysics.{_fn_name}({_corrected_args})"
                            # Apply the fix directly to output
                            _old_content_g = ctx.all_results_dict[tid]
                            _new_content_g = _old_content_g.replace(_bad_call_raw, _corrected_call, 1)
                            if _new_content_g != _old_content_g:
                                ctx.all_results_dict[tid] = _new_content_g
                                _fg_found = False
                                for _fg_i, _fg_e in enumerate(ctx.all_results):
                                    if _fg_e.get("task_id") == tid:
                                        ctx.all_results[_fg_i] = {"task_id": tid, "output": _new_content_g}
                                        _fg_found = True
                                        break
                                if not _fg_found:
                                    ctx.all_results.append({"task_id": tid, "output": _new_content_g})
                                print(f"  [Fix G] ✅ Auto-patched {_fn_name} arg count "
                                      f"({_actual}→{_min_exp}) in task {tid}")
                                # Update ledger signatures for the corrected call
                                try:
                                    from ledger import update_internal_api_ledger
                                    update_internal_api_ledger(_corrected_call, domain)
                                except Exception:
                                    pass
                        # ── End Fix G ─────────────────────────────────────────────

                        ctx.pre_flight_errors += (
                            f"\n## Static Pattern Violation — Task {tid} [Lua]\n"
                            f"**Rule:** MidwayPhysics.{_fn_name} wrong argument count "
                            f"(got {_actual}, expected {_min_exp}–{_max_exp})\n"
                            f"**Why this is always wrong:** Wrong argument count causes a "
                            f"runtime error or silent incorrect physics.\n"
                            f"**Required call signature:** "
                            f"MidwayPhysics.{_fn_name}({_pos_hint})\n"
                            f"**CRITICAL — NO handle or label parameter:** The first argument "
                            f"is ALWAYS the world X position (a number). "
                            f"There is NO handle, name string, or label argument. "
                            f"The function RETURNS a handle; it does NOT accept one as input. "
                            f"NEVER write MidwayPhysics.{_fn_name}('label', ...) "
                            f"or MidwayPhysics.{_fn_name}(handle, ...).\n"
                            f"Fix this before the reviewer sees the code.\n"
                        )
                        print(f"  [Static Guard] ❌ Task {tid} [Lua]: {_fn_name} arg count {_actual}≠{_min_exp}–{_max_exp}")

        # ── C9: Contract-driven API validation ────────────────────────────────
        # Instead of a growing blacklist of known-bad names, we validate
        # positively against the authoritative bridge contract from the
        # cartridge.  This catches both phantom (unknown namespaced) calls
        # AND bare calls (missing namespace prefix) for every symbol in the
        # contract — universally, without per-symbol maintenance.
        if domain == "Lua":
            try:
                from contract_validator import build_lua_contract, validate_lua_content
                # Resolve the bridge contract from the active cartridge.
                _bc_fn = getattr(ctx, '_cartridge_build_bridge_contract', None)
                _raw_bc = _bc_fn() if callable(_bc_fn) else {}
                if _raw_bc:
                    _lua_contract = build_lua_contract(
                        _raw_bc,
                        extra_engine_namespaces={"sol"},
                    )
                    _cv_violations = validate_lua_content(content, _lua_contract)
                    _cv_phantom_names: set = set()
                    for _viol in _cv_violations:
                        ctx.pre_flight_errors += (
                            f"\n## Static Pattern Violation — Task {tid} [Lua]\n"
                            f"**Rule:** {_viol.label}\n"
                            f"**Why this is always wrong:** {_viol.explanation}\n"
                            f"Fix this before the reviewer sees the code.\n"
                        )
                        print(f"  [Static Guard] ❌ Task {tid} [Lua]: {_viol.label}")
                        if _viol.kind in ("phantom_api", "bare_call"):
                            _cv_phantom_names.add(_viol.call_text.split(".")[-1])
                    if _cv_violations:
                        # Prepend a single approved-API hint block before the first
                        # violation for this task so the fix agent always has the
                        # complete approved surface visible at the top of the errors.
                        _hint_header = (
                            f"\n## Approved Bridge API — Task {tid} [Lua] "
                            f"(use ONLY these exact names)\n"
                            f"{_lua_contract.approved_names_hint}\n"
                        )
                        _marker = f"\n## Static Pattern Violation — Task {tid} [Lua]\n"
                        _insert_pos = ctx.pre_flight_errors.rfind(_marker)
                        if _insert_pos == -1:
                            ctx.pre_flight_errors += _hint_header
                        else:
                            ctx.pre_flight_errors = (
                                ctx.pre_flight_errors[:_insert_pos]
                                + _hint_header
                                + ctx.pre_flight_errors[_insert_pos:]
                            )
                    if _cv_phantom_names:
                        try:
                            from ledger import retract_ledger_entries
                            retract_ledger_entries(_cv_phantom_names)
                        except Exception:
                            pass
            except Exception as _cv_err:
                print(f"  [Static Guard] ⚠ C9 contract validator error: {_cv_err}")

        # ── C10: Duplicate top-level function definition in Lua ───────────────
        if domain == "Lua":
            _fn_names_seen: dict = {}
            # Match both declaration styles:
            #   function Foo()   — classic style
            #   Foo = function() — assignment style
            for _fn_m in re.finditer(
                r'^(?:(?:local\s+)?function\s+(\w+)\s*\(|(\w+)\s*=\s*function\s*\()',
                content, re.MULTILINE
            ):
                _name = _fn_m.group(1) or _fn_m.group(2)
                _fn_names_seen[_name] = _fn_names_seen.get(_name, 0) + 1
            for _name, _count in _fn_names_seen.items():
                if _count > 1:
                    ctx.pre_flight_errors += (
                        f"\n## Static Pattern Violation — Task {tid} [Lua]\n"
                        f"**Rule:** duplicate function definition '{_name}' ({_count}×)\n"
                        f"**Why this is always wrong:** Lua silently overwrites the first "
                        f"definition. The second definition wins, causing unpredictable "
                        f"behaviour. Remove the duplicate.\n"
                        f"Fix this before the reviewer sees the code.\n"
                    )
                    print(f"  [Static Guard] ❌ Task {tid} [Lua]: duplicate function '{_name}'")

        # ── C11: Local variable used outside its defining scope ───────────────
        # Detect: local X declared inside OnLoad/OnLoadStatic body,
        # then referenced inside OnUnload body without re-declaration.
        if domain == "Lua":
            _func_bodies: dict = {}
            for _fb_m in re.finditer(
                r'^(?:local\s+)?function\s+(\w+)\s*\([^)]*\)(.*?)^end\b',
                content, re.DOTALL | re.MULTILINE
            ):
                _func_bodies[_fb_m.group(1)] = _fb_m.group(2)
            _spawn_fns = {"OnLoad", "OnLoadStatic"}
            _cleanup_fns = {"OnUnload"}
            _defined_locals: set = set()
            for _fn in _spawn_fns:
                body = _func_bodies.get(_fn, "")
                for _loc_m in re.finditer(r'\blocal\s+(\w+)\s*=', body):
                    _defined_locals.add(_loc_m.group(1))
            for _fn in _cleanup_fns:
                body = _func_bodies.get(_fn, "")
                if not body:
                    continue
                for _ref_m in re.finditer(r'\b(\w+)\b', body):
                    _ref = _ref_m.group(1)
                    if _ref in _defined_locals:
                        # Check it's not re-declared locally in OnUnload
                        if not re.search(r'\blocal\s+' + re.escape(_ref) + r'\b', body):
                            ctx.pre_flight_errors += (
                                f"\n## Static Pattern Violation — Task {tid} [Lua]\n"
                                f"**Rule:** local variable '{_ref}' used in {_fn} but declared in OnLoad/OnLoadStatic\n"
                                f"**Why this is always wrong:** Local variables are scoped to their "
                                f"function. '{_ref}' will be nil in {_fn}.\n"
                                f"**How to fix:** Remove the 'local' keyword from the declaration inside "
                                f"OnLoad/OnLoadStatic and instead declare '{_ref}' at the TOP of the file, "
                                f"above all function definitions, like this:\n"
                                f"  local {_ref}  -- module-level, accessible from all lifecycle functions\n"
                                f"Then assign it inside OnLoad/OnLoadStatic without the 'local' keyword.\n"
                                f"Fix this before the reviewer sees the code.\n"
                            )
                            print(f"  [Static Guard] ❌ Task {tid} [Lua]: local '{_ref}' out-of-scope in {_fn}")
                            break  # one error per function pair is sufficient

        # ── C12: Bare OnStep defined without MidwayPhysics.OnStep registration ─
        # Two-pass: (1) a non-local (module-level) bare global OnStep exists,
        #           (2) MidwayPhysics.OnStep registration is absent.
        # Scoping fix: 'local function OnStep' is valid as an upvalue passed to
        # MidwayPhysics.OnStep — do NOT flag it.  Only flag a *non-local* bare
        # global 'function OnStep' that has no corresponding registration call.
        if domain == "Lua":
            _has_bare_onstep = bool(re.search(
                r'^function\s+OnStep\s*\(',
                content, re.MULTILINE
            ))
            _has_registered_onstep = bool(re.search(
                r'MidwayPhysics\.OnStep\s*\(',
                content, re.IGNORECASE
            ))
            if _has_bare_onstep and not _has_registered_onstep:
                ctx.pre_flight_errors += (
                    f"\n## Static Pattern Violation — Task {tid} [Lua]\n"
                    f"**Rule:** OnStep defined as bare global but MidwayPhysics.OnStep never registered\n"
                    f"**Why this is always wrong:** The engine ignores bare OnStep globals. "
                    f"You MUST register via MidwayPhysics.OnStep(function(dt) ... end) inside OnLoad().\n"
                    f"Fix this before the reviewer sees the code.\n"
                )
                print(f"  [Static Guard] ❌ Task {tid} [Lua]: bare OnStep without MidwayPhysics.OnStep")


def _run_preflight_checks(ctx: PipelineContext) -> PipelineContext:
    """
    Phase 6 pre-flight: run background compilers (C++ / Make), Lua syntax
    checks, and Pro Mode unit test compilation/execution. If errors are
    detected, invoke the Architect Syntax Fix cycle to patch them.
    """
    print("  [Pre-Flight] Flushing results to workspace before compilation...")
    _flush_results_to_workspace(ctx)
    ctx.pre_flight_errors = ""

    # Universal guard: catch empty or prose-only outputs before any compiler runs.
    _inject_empty_output_errors(ctx)

    # Static pattern guard: catch known-bad patterns that are always wrong
    # regardless of compiler availability.
    _inject_static_pattern_errors(ctx)

    # Coverage check: every task in task_map must have a non-empty result.
    # Tasks that were never executed (e.g. the LLM dropped them) are surfaced
    # here so the fix loop has a concrete mandate to generate the missing output.
    if ctx.task_map:
        for _cov_tid, _cov_task in ctx.task_map.items():
            _cov_out = ctx.all_results_dict.get(_cov_tid, "")
            if not _cov_out or not _cov_out.strip():
                _cov_dom = getattr(_cov_task, "agent", "?")
                _cov_desc = getattr(_cov_task, "description", "") or getattr(_cov_task, "title", "")
                ctx.pre_flight_errors += (
                    f"\n## Missing Output — Task {_cov_tid} [{_cov_dom}]\n"
                    f"Task {_cov_tid} ({_cov_desc[:120]}) was planned but produced no output. "
                    f"A complete implementation is required for every planned task.\n"
                )
                print(f"  [Coverage Check] ⛔ Task {_cov_tid} [{_cov_dom}] has no output — injecting fix demand.")

    # Integration schema conflict check: handle ownership collisions across tasks.
    try:
        from integration_schema import validate_schema_conflicts
        _schema_conflict_text = validate_schema_conflicts(ctx)
        if _schema_conflict_text:
            ctx.pre_flight_errors += _schema_conflict_text
            print(f"  [IntegrationSchema] ⚡ Schema conflicts detected — injecting into preflight errors.")
    except Exception:
        pass

    # Feature coverage gap check: compare agent outputs against AttractionDesign checklist.
    _design = getattr(ctx, 'attraction_design', None)
    if _design and _design.feature_checklist:
        _all_output = "\n".join(ctx.all_results_dict.values())
        _gaps = []
        for _feature in _design.feature_checklist:
            # Heuristic: look for any 2+ consecutive non-stop-words from the
            # feature string appearing in the combined output.
            import re as _re_cov
            _keywords = [w for w in _re_cov.findall(r'\b\w{4,}\b', _feature.lower())
                         if w not in {'must', 'should', 'that', 'with', 'this', 'from', 'have', 'when', 'been', 'into', 'each'}]
            if _keywords and not any(kw in _all_output.lower() for kw in _keywords[:3]):
                _gaps.append(_feature)
        if _gaps:
            ctx.coverage_gaps = _gaps
            _gap_lines = "\n".join(f"  - {g}" for g in _gaps)
            ctx.pre_flight_errors += (
                f"\n## ⚠️  Feature Coverage Gaps (from Attraction Design checklist)\n"
                f"The following features from the design document appear to be missing from agent outputs:\n"
                f"{_gap_lines}\n"
                "Each agent responsible MUST ensure these features are implemented.\n"
            )
            print(f"  [Coverage Check] 📋 {len(_gaps)} design checklist item(s) appear missing.")
        else:
            print("  [Coverage Check] ✅ All design checklist items appear covered.")

    # Platform-aware compilation check
    _cmake_cache = ctx.project_root / "CMakeCache.txt"
    _makefile = ctx.project_root / "Makefile"
    try:
        if sys.platform == "win32":
            if not _cmake_cache.is_file():
                print("  [Pre-Flight] No CMakeCache.txt found — skipping cmake build check.")
            else:
                cmake_build = subprocess.run(
                    ["cmake", "--build", "."],
                    capture_output=True, text=True, cwd=ctx.project_root,
                    shell=True, timeout=30,
                )
                if cmake_build.returncode != 0:
                    err_tail = "\n".join(cmake_build.stderr.splitlines()[-50:])
                    # Discard cmake infrastructure errors — only propagate real compiler diagnostics.
                    _infra_error = any(
                        kw in err_tail.lower()
                        for kw in ("could not load cache", "no cmake_cache", "run cmake first",
                                   "cmake error", "cmake warning", "configuring incomplete")
                    )
                    if not _infra_error:
                        ctx.pre_flight_errors += f"\n## C++ Compiler Errors:\n```\n{err_tail}\n```"
                        # Circuit Breaker: isolate retry strikes to matching domain agents
                        failing_domains = ("C++", "PHYS", "NET", "SHADER")
                        for tid in list(ctx.all_results_dict.keys()):
                            task_obj = ctx.task_map.get(tid)
                            if task_obj and task_obj.agent in failing_domains:
                                ctx.retry_counts[tid] = ctx.retry_counts.get(tid, 0) + 1
                                print(f"  [Circuit Breaker] {tid} ({task_obj.agent}) retry_count incremented to {ctx.retry_counts[tid]} (build failure)")
                    else:
                        print(f"  [Pre-Flight] cmake infrastructure error suppressed (not a compiler diagnostic): {err_tail[:120]}")
        else:
            if not _makefile.is_file():
                print("  [Pre-Flight] No Makefile found — skipping make build check.")
            else:
                make_process = subprocess.run(
                    ["make", "-j4"], capture_output=True, text=True,
                    cwd=ctx.project_root, timeout=30,
                )
                if make_process.returncode != 0:
                    err_tail = "\n".join(make_process.stderr.splitlines()[-50:])
                    _infra_error = any(
                        kw in err_tail.lower()
                        for kw in ("no targets", "nothing to be done", "no rule to make")
                    )
                    if not _infra_error:
                        ctx.pre_flight_errors += f"\n## C++ Compiler Errors:\n```\n{err_tail}\n```"
                        # Circuit Breaker: isolate retry strikes to matching domain agents
                        failing_domains = ("C++", "PHYS", "NET", "SHADER")
                        for tid in list(ctx.all_results_dict.keys()):
                            task_obj = ctx.task_map.get(tid)
                            if task_obj and task_obj.agent in failing_domains:
                                ctx.retry_counts[tid] = ctx.retry_counts.get(tid, 0) + 1
                                print(f"  [Circuit Breaker] {tid} ({task_obj.agent}) retry_count incremented to {ctx.retry_counts[tid]} (build failure)")
                    else:
                        print(f"  [Pre-Flight] make infrastructure message suppressed: {err_tail[:120]}")
    except subprocess.TimeoutExpired:
        ctx.pre_flight_errors += "\n## Compiler Timeout:\n```\nC++ build timed out after 30s\n```\n"
    except Exception:
        pass

    # ── Pro Mode: Unit Test Compilation & Execution ────────────────────
    # Only runs when at least one task was actually flagged as math-heavy
    # and the user opted in (ctx.math_heavy_tasks is non-empty).
    if ctx.math_heavy_tasks:
        print("  [Pro Mode] Compiling and executing test suite...")
        test_build_dir = ctx.project_root / "build" / "tests"
        test_binary = test_build_dir / "run_tests"
        if sys.platform == "win32":
            test_binary = test_build_dir / "run_tests.exe"

        # Only attempt to build tests if a configured cmake build tree exists.
        _cmake_cache = ctx.project_root / "CMakeCache.txt"
        if not _cmake_cache.is_file():
            print("  [Pro Mode] No CMakeCache.txt — test binary build skipped (no configured build tree).")
            # Informational only — do NOT append to ctx.pre_flight_errors.
            # Writing here makes pre_flight_errors non-empty even after all real
            # violations are resolved, causing the arch-fix loop to fire on clean code.
        else:
            # Build test target if cmake project is configured
            test_build_ok = True
            try:
                test_build_proc = subprocess.run(
                    ["cmake", "--build", ".", "--target", "run_tests"],
                    capture_output=True, text=True, cwd=ctx.project_root,
                    shell=True, timeout=60,
                )
                if test_build_proc.returncode != 0:
                    test_build_ok = False
                    err_tail = "\n".join(test_build_proc.stderr.splitlines()[-50:])
                    _infra_kws = (
                        "could not load cache", "no cmake_cache", "run cmake first",
                        "cmake error", "cmake warning", "configuring incomplete",
                    )
                    if any(kw in err_tail.lower() for kw in _infra_kws):
                        print(f"  [Pro Mode] cmake infrastructure error suppressed during test build: {err_tail[:120]}")
                    else:
                        ctx.pre_flight_errors += (
                            f"\n## Test Suite Compilation Errors:\n```\n{err_tail}\n```\n"
                        )
                        print(f"  [Pro Mode] ⚠ Test suite failed to compile — treating as [VETO]")
            except (subprocess.TimeoutExpired, Exception) as e:
                test_build_ok = False
                ctx.pre_flight_errors += (
                    f"\n## Test Suite Compilation Exception:\n```\n{e}\n```\n"
                )

            if test_build_ok and test_binary.is_file():
                try:
                    test_run = subprocess.run(
                        [str(test_binary)], capture_output=True, text=True,
                        timeout=60,
                    )
                    if test_run.returncode != 0:
                        test_stderr = test_run.stderr.strip() or test_run.stdout.strip()
                        ctx.pre_flight_errors += (
                            f"\n## Unit Test Failures:\n```\n{test_stderr[:2000]}\n```\n"
                        )
                        print(f"  [Pro Mode] ⛔ Tests FAILED — feeding errors back to domain agents")
                    else:
                        print(f"  [Pro Mode] ✅ All unit tests passed!")
                except subprocess.TimeoutExpired:
                    ctx.pre_flight_errors += (
                        "\n## Unit Test Timeout:\n```\nTest binary timed out after 60s\n```\n"
                    )
                except Exception as e:
                    ctx.pre_flight_errors += (
                        f"\n## Unit Test Execution Error:\n```\n{e}\n```\n"
                    )

    # Scan both the real project tree and .staging_workspace/ so that files
    # flushed to staging (when staging mode is active) are also syntax-checked.
    from _helpers_io import is_staging_active, get_staging_path
    _lua_roots = [ctx.project_root]
    _staging_root = ctx.project_root / ".staging_workspace"
    if is_staging_active() and _staging_root.is_dir():
        _lua_roots.append(_staging_root)

    _luac_missing = False
    for _lua_root in _lua_roots:
        if _luac_missing:
            break
        for lf in _lua_root.rglob("*.lua"):
            try:
                lua_proc = subprocess.run(
                    ["luac", "-p", str(lf)], capture_output=True, text=True, timeout=30,
                )
                if lua_proc.returncode != 0:
                    ctx.pre_flight_errors += (
                        f"\n## Lua Syntax Error in {lf.name}:\n```\n{lua_proc.stderr}\n```"
                    )
            except subprocess.TimeoutExpired:
                ctx.pre_flight_errors += (
                    f"\n## Lua Syntax Error in {lf.name}:\n```\nluac timed out after 30s\n```\n"
                )
            except FileNotFoundError:
                # luac is not installed — warn once and skip file-level Lua syntax checks.
                # Static guards above still run; this is a degraded but not silent path.
                print("  [Pre-Flight] ⚠ luac not found — Lua file-level syntax check skipped. "
                      "Install luac for full Lua syntax coverage.")
                _luac_missing = True
                break
            except Exception:
                pass

    # ── Headless Runtime Simulation ─────────────────────────────────────────
    # Run the lightweight Lua tick harness against all Lua/physics task outputs.
    # Surfaces nil-handle access, bad API calls, and global pollution before
    # the output reaches the reviewer.  Non-fatal: errors become pre_flight_errors.
    try:
        from runtime_sim import run_runtime_sim
        _sim_errors = run_runtime_sim(ctx)
        if _sim_errors:
            ctx.pre_flight_errors += (
                "\n## ⚡ Runtime Simulation Errors\n"
                + "\n".join(f"  {e}" for e in _sim_errors)
                + "\n"
            )
            print(f"  [RuntimeSim] ⛔ {len(_sim_errors)} runtime error(s) detected.")
        else:
            print("  [RuntimeSim] ✅ No runtime simulation errors detected.")
    except Exception as _sim_ex:
        print(f"  [RuntimeSim] ⚠ Simulation skipped: {_sim_ex}")
    # Catches generated .py files with invalid syntax before the reviewer sees them.
    # Vendor / build directories are excluded to avoid false positives from
    # third-party code with deliberately non-standard syntax.
    _PY_SCAN_EXCLUDE = {
        "build", "vcpkg_installed", ".staging_workspace",
        "__pycache__", ".venv", "venv", ".git",
    }
    import ast as _ast
    for pf in ctx.project_root.rglob("*.py"):
        # Skip vendor/build trees and the pipeline's own top-level source.
        try:
            _pf_rel = pf.relative_to(ctx.project_root)
        except ValueError:
            continue
        # Exclude any file whose path contains a blacklisted directory component.
        if any(part in _PY_SCAN_EXCLUDE for part in _pf_rel.parts):
            continue
        # Only check files at least two levels deep so pipeline source itself
        # (e.g. _finalize_preflight.py at the root) is never re-checked.
        if len(_pf_rel.parts) < 2:
            continue
        try:
            _py_src = pf.read_text(encoding="utf-8", errors="replace")
            _ast.parse(_py_src, filename=str(pf))
        except SyntaxError as _se:
            ctx.pre_flight_errors += (
                f"\n## Python Syntax Error in {pf.name} (line {_se.lineno}):\n"
                f"```\n{_se.msg}: {_se.text}\n```\n"
            )
            print(f"  [Pre-Flight] ⛔ Python SyntaxError in {pf.name}:{_se.lineno} — {_se.msg}")
        except Exception:
            pass

    # ── Architect Syntax Fix Cycle ─────────────────────────────────────
    if ctx.pre_flight_errors:
        print("  [Pre-Flight] Syntax errors detected. Forcing Architect Syntax Fix (per-domain).")

        # E16: Snapshot outputs that are both non-empty AND free of static guard
        # violations as the anchor for the arch fix.  Anchoring to a broken output
        # (the previous behaviour) re-introduces the same errors on every cycle.
        # Strategy: re-run the guard patterns against each output; only outputs
        # with zero matches are eligible as anchors.
        _GUARD_PATTERNS_QUICK = [
            re.compile(r'MidwayPhysics\.SpawnStaticPlane', re.IGNORECASE),
            re.compile(r'MidwayPhysics\.ApplyForce\b', re.IGNORECASE),
            re.compile(r'MidwayPhysics\.SpawnStaticMesh\s*\(\s*[\'"]', re.IGNORECASE),
            re.compile(r'^(?!\s*--).*(?<!MidwayPhysics\.)\bDestroyBody\s*\(', re.IGNORECASE | re.MULTILINE),
            re.compile(r'\bsol\s*\.\s*(?:set_function|new_usertype|state)\s*\(', re.IGNORECASE),
            # Phantom APIs that the Lua system prompt previously taught the model to use.
            # If any of these appear in an output it must never become a clean anchor.
            re.compile(r'\bsol\s*\.\s*log(?:_message)?\s*\(', re.IGNORECASE),
            re.compile(r'\bMidwayPhysics\s*\.\s*log(?:_message)?\s*\(', re.IGNORECASE),
            re.compile(r'\bMidwayPhysics\s*\.\s*FindBodyByLabel\s*\(', re.IGNORECASE),
            re.compile(r'\bMidwayPhysics\s*\.\s*SetPosition\s*\(', re.IGNORECASE),
            # Undefined booth globals — never defined in the Midway runtime.
            re.compile(r'\b(?:BUTTON|SLOT_[XYZ]|SharedBooth)\b', re.MULTILINE),
        ]
        _last_good: dict = {}
        for _tid, _out in ctx.all_results_dict.items():
            if not _out or not _out.strip() or _is_comment_only(_out):
                continue
            if not _task_has_code(_out):
                continue
            # Reject if any quick guard fires on this output
            if any(p.search(_out) for p in _GUARD_PATTERNS_QUICK):
                continue
            _last_good[_tid] = _out

        # Guard: skip any block the LLM filled with an [ERROR] stub, a bare
        # delegation token, or a placeholder — these would poison the next cycle.
        _error_stub_re = re.compile(
            r'^\s*(\[ERROR\]|\[DELEGATE|\[QUERY:DOC|\[CONF:|\[REVISE|<<<<<<|import sys'
            r'|name .sys. is not defined'
            r'|int task_\d+_solution|void task_\d+_solution)',
            re.IGNORECASE | re.MULTILINE
        )

        # Group failing tasks by domain so each call is scoped to one language.
        # This prevents qwen2.5-coder from being asked to write Lua, C++, and
        # PHYS simultaneously and falling back to language-agnostic placeholders.
        # NOTE: Task uses .agent (canonical domain key like "Lua", "C++") not .domain.
        from collections import defaultdict
        domain_task_groups: dict = defaultdict(list)
        for tid, output in ctx.all_results_dict.items():
            task_obj = ctx.task_map.get(tid)
            domain = (task_obj.agent if task_obj and getattr(task_obj, 'agent', None)
                      else "Unknown")
            domain_task_groups[domain].append((tid, output))

        for domain, task_pairs in domain_task_groups.items():
            # Build labelled task blocks — LLM is explicitly instructed to
            # echo back the same ### task_N header so the extraction regex works.
            task_blocks = [f"### {tid}\n{output}" for tid, output in task_pairs]
            domain_code_str = "\n\n".join(task_blocks)
            failing_tids = [tid for tid, _ in task_pairs]

            # E16: Build per-task anchor blocks from the last-good snapshot.
            # Collapsed to a safe budget via block-aware paging so VRAM is not
            # blown by injecting multiple large outputs simultaneously.
            # 2026-05-21: Increased from 1500 → 3000 because the original
            # budget collapsed to 65-75% truncation for 2-task Lua scenarios,
            # causing the fix model to discard real code and regenerate stubs.
            _ANCHOR_BUDGET = 3000  # chars per task anchor (was 1500)
            _anchor_blocks = []
            for _atid, _aout in task_pairs:
                _anchor = _last_good.get(_atid)
                if _anchor:
                    _collapsed_anchor = TokenBudget._block_aware_collapse(_anchor, _ANCHOR_BUDGET)
                    _anchor_blocks.append(
                        f"### {_atid} [ANCHOR — repair this, do NOT rewrite from scratch]\n"
                        f"{_collapsed_anchor}"
                    )
                elif _aout and _aout.strip() and _task_has_code(_aout):
                    # No clean anchor — provide the current (dirty) output so the
                    # model has concrete code to patch rather than generating stubs.
                    _dirty_anchor = TokenBudget._block_aware_collapse(
                        _strip_search_replace_metadata(_aout), _ANCHOR_BUDGET
                    )
                    _anchor_blocks.append(
                        f"### {_atid} [CURRENT CODE — fix ONLY the listed violations, do NOT discard]\n"
                        f"{_dirty_anchor}"
                    )
            _anchor_str = (
                "\n\n## Previous Implementation (ANCHOR)\n"
                "The following is the last known implementation for each task.\n"
                "You MUST base your fix on this code. Do NOT discard it and write an empty scaffold.\n"
                + "\n\n".join(_anchor_blocks)
                + "\n"
            ) if _anchor_blocks else ""

            # Build a compact bridge cheatsheet for the fix model so it has
            # approved names in front of it — not just error messages.
            # Consolidated: delegate to the shared builder in _finalize_review
            # instead of maintaining a second inline copy here.
            _fix_cheatsheet = ""
            try:
                from pipeline import _CTX as _pf_ctx
                if _pf_ctx is not None:
                    from _finalize_review import build_fix_bridge_snippet as _bfbs
                    _base_snippet = _bfbs(_pf_ctx)
                    if _base_snippet:
                        _fix_cheatsheet = (
                            "\n## \u26a1 Approved Bridge API (exhaustive \u2014 use ONLY these names)\n"
                            + _base_snippet
                            + "Substitution quick-ref:\n"
                            + "  SetPosition/Teleport \u2192 MoveKinematic(h,lx,ly,lz,dt)\n"
                            + "  ApplyForce \u2192 ApplyImpulse(handle,ix,iy,iz)\n"
                            + "  CheckCollision \u2192 IsSensorTriggered(handle) \u2192 bool\n"
                            + "  table.clear(t) \u2192 for k in pairs(t) do t[k]=nil end\n"
                        )
            except Exception:
                pass

            # E17: Extract the exact required signature from the error block so the
            # fix model sees the CORRECT call pattern directly — not buried inside
            # a 3000-char collapsed error blob.  Without this, the model guesses
            # (e.g. adding a boolean flag) instead of copying the known-correct form.
            _signature_extract = ""
            _sig_marker = "**Required call signature:** "
            for _line in ctx.pre_flight_errors.splitlines():
                if _sig_marker in _line:
                    _signature_extract += _line.strip() + "\n"
            if _signature_extract:
                _signature_extract = (
                    "\n## ⚡ CORRECT SIGNATURE (from pre-flight checker — copy this EXACTLY)\n"
                    + _signature_extract
                    + "Use the EXACT positional signature shown above. "
                    "Do NOT add extra arguments, boolean flags, table literals, or labels.\n"
                )

            fix_input = (
                f"Domain: [{domain}]\n"
                f"The following {domain} task(s) failed pre-flight checks.\n"
                f"Errors:\n{TokenBudget._block_aware_collapse(ctx.pre_flight_errors, 3000)}\n"
                f"{_fix_cheatsheet}"
                f"{_signature_extract}"
                f"{_anchor_str}"
                f"CRITICAL OUTPUT FORMAT RULES (violations will be discarded):\n"
                f"1. Output ONLY working {domain} code. Do NOT output any [DELEGATE:...],"
                f" [QUERY:DOC:...], [CONF:...], prose explanations, or placeholder stubs.\n"
                f"2. Prefix EACH task's fixed code block with its exact header line: ### task_N\n"
                f"   Example for two tasks:\n"
                f"   ### task_1\n   ```lua\n   -- corrected code here\n   ```\n\n"
                f"   ### task_2\n   ```lua\n   -- corrected code here\n   ```\n"
                f"3. Do NOT invent new API names. Use ONLY the exact names from the Approved Bridge API list above.\n"
            )

            # Build a compact, repair-specific system prompt for the fix call.
            # Deliberately does NOT use get_agent_system(domain): that function
            # injects the full Lua runtime prompt + mesh protocol + virtual-memory
            # protocol (~11.8 k chars), leaving only ~469 chars for user content
            # and causing the model to produce scaffolds instead of real repairs.
            # The fix model already receives the approved API cheatsheet and anchor
            # code in the user message, so a short repair mandate is sufficient.
            _base_fix_system = _prompts_mod.ARCHITECT_FIX_SYSTEM
            # Append the domain-specific prohibitions from the cartridge so the
            # fix model still knows phantom APIs and lifecycle rules, but skip
            # the full ledger, mesh, and virtual-memory protocols.
            _domain_prohibitions = ""
            try:
                from pipeline import _CTX as _pf_ctx2
                if _pf_ctx2:
                    _cart = getattr(_pf_ctx2, 'mounted_cartridge', None)
                    if _cart:
                        _cart_domain = _cart.domains.get(domain)
                        if _cart_domain:
                            _sp = _cart_domain.system_prompt or ""
                            # Extract only up to 1800 chars from the domain prompt
                            # (enough for lifecycle + bridge rules) — no mesh/ledger.
                            _domain_prohibitions = "\n\n## Domain Rules (summary)\n" + _sp[:1800]
                    if not _domain_prohibitions:
                        _ctx_reg = getattr(_pf_ctx2, 'domain_registry', None) or {}
                        _dreg = _ctx_reg.get(domain, {})
                        if isinstance(_dreg, dict) and _dreg.get('system_prompt'):
                            _domain_prohibitions = "\n\n## Domain Rules (summary)\n" + _dreg['system_prompt'][:1800]
            except Exception:
                pass
            domain_system = _base_fix_system + _domain_prohibitions

            # Use domain-specific model: prefer live cartridge registry, then kernel dict
            _live_registry = getattr(ctx, 'domain_registry', None) or {}
            from pipeline import ALL_DOMAINS as _kernel_domains
            fix_model = (
                _live_registry.get(domain, {}).get('model')
                or _kernel_domains.get(domain, {}).get('model')
                or __import__('pipeline').EXECUTION_MODEL
            )

            fixed_str = call_ollama(
                domain_system, fix_input,
                f"Architect Syntax Fix [{domain}]", fix_model
            )

            # Strip the <fix-plan> reasoning block so it never contaminates
            # SEARCH/REPLACE extraction or gets stored as generated code.
            fixed_str = re.sub(r"<fix-plan>.*?</fix-plan>", "", fixed_str, flags=re.DOTALL).strip()

            # ── Primary extraction: LLM used required ### task_N headers ────
            # The regex tolerates any trailing label the model echoes after the
            # task ID (e.g. "### task_1 [ANCHOR — repair this...]") because the
            # prompt injects such labels and the model verbatim-echoes them.
            # Previously the regex required ONLY whitespace after the ID, causing
            # every arch-fix round to produce "no valid block" and retain bad code.
            applied_tids: set = set()
            for match in re.finditer(
                r"###\s*(task_\d+)[^\n]*\n(.*?)(?=###\s*task_\d+|\Z)", fixed_str, re.DOTALL
            ):
                tid = match.group(1).strip()
                fixed_code = match.group(2).strip()
                if not fixed_code or _error_stub_re.search(fixed_code):
                    print(f"  [Arch Fix] Skipping stub/error block for {tid} ({domain}) "
                          f"— LLM did not produce valid code.")
                    continue
                if _is_comment_only(fixed_code) or not _task_has_code(fixed_code):
                    print(f"  [Arch Fix] Skipping comment-only/empty block for {tid} ({domain}) "
                          f"— output contains no real implementation.")
                    continue
                if tid in applied_tids:
                    print(f"  [Arch Fix] ⚠ Duplicate block for {tid} ({domain}) — ignoring second copy.")
                    continue
                if tid in ctx.all_results_dict:
                    _af_code = _strip_search_replace_metadata(fixed_code)
                    ctx.all_results_dict[tid] = _af_code
                    # Keep all_results list in sync.
                    _af_found = False
                    for _af_i, _af_e in enumerate(ctx.all_results):
                        if _af_e.get("task_id") == tid:
                            ctx.all_results[_af_i] = {"task_id": tid, "output": _af_code}
                            _af_found = True
                            break
                    if not _af_found:
                        ctx.all_results.append({"task_id": tid, "output": _af_code})
                    applied_tids.add(tid)
                    print(f"  [Arch Fix] ✅ Applied fix for {tid} ({domain})")
                    # Ledger: record final committed signatures (arch-fix may be
                    # the first time real code appears for a task that delegated)
                    try:
                        from ledger import update_internal_api_ledger
                        update_internal_api_ledger(_af_code, domain)
                    except Exception:
                        pass


            # ── SEARCH/REPLACE conflict-marker extraction ──
            # Check for Git-style <<<<<<< SEARCH / ======= / >>>>>>> REPLACE blocks
            # before falling back to standard markdown code fences.
            # Compute unapplied HERE so the SR loop below can reference it.
            # (It is also recomputed after the SR block in case more tids were
            # applied; the final fallback path computes it one more time too.)
            unapplied = [t for t in failing_tids if t not in applied_tids]
            sr_blocks = list(SEARCH_REPLACE_PATTERN.finditer(fixed_str))
            if sr_blocks:
                # Map SR blocks to unapplied tasks by target_file matching.
                # When a SEARCH/REPLACE block matches a target file, broadcast
                # the fix to ALL unapplied tasks targeting that same file.
                for sr_match in sr_blocks:
                    search_text = sr_match.group(1).strip()
                    replace_text = sr_match.group(2).strip()
                    if not search_text or not replace_text:
                        continue
                    # Pack as a dict-like tuple for _strip_search_replace_metadata
                    sr_payload = f"<<<<<<< SEARCH\n{search_text}\n=======\n{replace_text}\n>>>>>>> REPLACE"
                    matched_file = None
                    # Try to find a task whose file contains search_text
                    for tid in unapplied:
                        task = ctx.task_map.get(tid)
                        if task and task.target_file:
                            target_path = ctx.project_root / task.target_file
                            if target_path.is_file():
                                file_content = target_path.read_text(encoding="utf-8", errors="replace")
                                if search_text in file_content:
                                    matched_file = task.target_file
                                    break
                    if matched_file:
                        # Broadcast the patch to ALL unapplied tasks targeting this file
                        for tid in list(unapplied):
                            task = ctx.task_map.get(tid)
                            if task and task.target_file == matched_file:
                                _sr_code = _strip_search_replace_metadata(sr_payload)
                                ctx.all_results_dict[tid] = _sr_code
                                _sr_f = False
                                for _sr_i, _sr_e in enumerate(ctx.all_results):
                                    if _sr_e.get("task_id") == tid:
                                        ctx.all_results[_sr_i] = {"task_id": tid, "output": _sr_code}
                                        _sr_f = True
                                        break
                                if not _sr_f:
                                    ctx.all_results.append({"task_id": tid, "output": _sr_code})
                                applied_tids.add(tid)
                                print(f"  [Arch Fix] ✅ SEARCH/REPLACE matched for {tid} ({task.target_file})")
                    # No file match — try all unapplied tasks if only one SR block
                    if len(sr_blocks) == 1 and len(unapplied) > 0:
                        # Single SR block: apply to first unapplied task
                        tid = unapplied[0]
                        if tid in ctx.all_results_dict:
                            _srnm_code = _strip_search_replace_metadata(sr_payload)
                            ctx.all_results_dict[tid] = _srnm_code
                            _srnm_f = False
                            for _srnm_i, _srnm_e in enumerate(ctx.all_results):
                                if _srnm_e.get("task_id") == tid:
                                    ctx.all_results[_srnm_i] = {"task_id": tid, "output": _srnm_code}
                                    _srnm_f = True
                                    break
                            if not _srnm_f:
                                ctx.all_results.append({"task_id": tid, "output": _srnm_code})
                            applied_tids.add(tid)
                            print(f"  [Arch Fix] ✅ SEARCH/REPLACE applied (no file match) for {tid}")
                # Recompute unapplied after SEARCH/REPLACE extraction
                unapplied = [t for t in failing_tids if t not in applied_tids]
                if not unapplied:
                    continue

            # ── Fallback extraction: LLM ignored headers but produced code ──
            # When only one task is failing OR the LLM produced exactly one
            # fenced code block, apply it to all un-patched failing tasks.
            unapplied = [t for t in failing_tids if t not in applied_tids]
            if unapplied:
                _fence_re = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
                code_blocks = [
                    m.group(1).strip() for m in _fence_re.finditer(fixed_str)
                    if m.group(1).strip()
                    and not _error_stub_re.search(m.group(1))
                    and not _is_comment_only(m.group(1))
                    and _task_has_code(m.group(1))
                ]
                if len(code_blocks) == 1 and len(unapplied) == 1:
                    # Single block, single un-patched task — safe 1:1 apply.
                    tid = unapplied[0]
                    if tid in ctx.all_results_dict:
                        _sb_code = _strip_search_replace_metadata(code_blocks[0])
                        ctx.all_results_dict[tid] = _sb_code
                        _sb_found = False
                        for _sb_i, _sb_e in enumerate(ctx.all_results):
                            if _sb_e.get("task_id") == tid:
                                ctx.all_results[_sb_i] = {"task_id": tid, "output": _sb_code}
                                _sb_found = True
                                break
                        if not _sb_found:
                            ctx.all_results.append({"task_id": tid, "output": _sb_code})
                        print(f"  [Arch Fix] ✅ Applied single-block fallback fix for {tid} ({domain})")
                        try:
                            from ledger import update_internal_api_ledger
                            update_internal_api_ledger(code_blocks[0], domain)
                        except Exception:
                            pass
                elif len(code_blocks) == 1 and len(unapplied) > 1:
                    # One block returned for multiple un-patched tasks.
                    # NEVER broadcast a single skeleton to all tasks — doing so
                    # silently collapses a full multi-section implementation into
                    # the last task's narrow fix snippet.  Instead, reject the
                    # response and inject a hard error demanding one ### task_N
                    # block per task so the fix model must re-emit all sections.
                    from collections import defaultdict
                    file_tasks: dict = defaultdict(list)
                    for tid in unapplied:
                        task = ctx.task_map.get(tid)
                        tf = task.target_file if task else None
                        file_tasks[tf or "__no_file__"].append(tid)
                    resolved_any = False
                    for target_file, tids in file_tasks.items():
                        if target_file == "__no_file__" or len(tids) <= 1:
                            continue
                        # Multiple tasks share this file — refuse the single block.
                        print(
                            f"  [Arch Fix] \u26a0 Refused to broadcast single block to "
                            f"{len(tids)} task(s) for {target_file} ({domain}) — "
                            "would collapse implementation. Demanding per-task blocks."
                        )
                        ctx.pre_flight_errors += (
                            f"\n## Arch Fix Output Rejected — {domain} ({target_file})\n"
                            f"The fix agent returned exactly 1 code block for {len(tids)} "
                            f"tasks ({', '.join(tids)}) that all write to `{target_file}`.\n"
                            f"Broadcasting a single block would overwrite all tasks with "
                            f"only one task's implementation, discarding the rest.\n"
                            f"**Required:** Output one `### task_N` block per task, each "
                            f"containing the COMPLETE implementation for that task. "
                            f"Tasks: {', '.join(tids)}.\n"
                        )
                    # Single-task file groups are still safe to apply 1:1
                    for target_file, tids in file_tasks.items():
                        if target_file == "__no_file__" or len(tids) != 1:
                            continue
                        tid = tids[0]
                        if tid not in ctx.all_results_dict:
                            continue
                        patched_content = _strip_search_replace_metadata(code_blocks[0])
                        ctx.all_results_dict[tid] = patched_content
                        applied_tids.add(tid)
                        _bc_found = False
                        for _bc_i, _bc_e in enumerate(ctx.all_results):
                            if _bc_e.get("task_id") == tid:
                                ctx.all_results[_bc_i] = {"task_id": tid, "output": patched_content}
                                _bc_found = True
                                break
                        if not _bc_found:
                            ctx.all_results.append({"task_id": tid, "output": patched_content})
                        resolved_any = True
                        print(f"  [Arch Fix] \u2705 Applied fix for {tid} ({domain})")
                    # Legacy broadcast path removed — see comment above.
                    if False:
                        first_tid = ""
                        if first_tid in ctx.all_results_dict:
                            applied_tids.add(first_tid)
                            # Mark ALL remaining tasks for this file as applied.
                            for tid in tids:
                                if tid != first_tid:
                                    applied_tids.add(tid)
                                    if tid in ctx.all_results_dict:
                                        ctx.all_results_dict[tid] = patched_content
                                        _bc_f2 = False
                                        for _bc_i2, _bc_e2 in enumerate(ctx.all_results):
                                            if _bc_e2.get("task_id") == tid:
                                                ctx.all_results[_bc_i2] = {"task_id": tid, "output": patched_content}
                                                _bc_f2 = True
                                                break
                                        if not _bc_f2:
                                            ctx.all_results.append({"task_id": tid, "output": patched_content})
                            resolved_any = True
                            print(f"  [Arch Fix] ✅ Broadcast single block to {len(tids)} task(s)"
                                  f" for {target_file} ({domain})")
                            try:
                                from ledger import update_internal_api_ledger
                                update_internal_api_ledger(code_block, domain)
                            except Exception:
                                pass
                            break
                    if not resolved_any:
                        print(f"  [Arch Fix] ⚠ Single block cannot be broadcast — "
                              f"no matching file found for {len(unapplied)} tasks ({domain})")
                elif len(code_blocks) == len(unapplied):
                    # One block per un-patched task — zip in order
                    for tid, blk in zip(unapplied, code_blocks):
                        if tid in ctx.all_results_dict:
                            _oz_code = _strip_search_replace_metadata(blk)
                            ctx.all_results_dict[tid] = _oz_code
                            _oz_found = False
                            for _oz_i, _oz_e in enumerate(ctx.all_results):
                                if _oz_e.get("task_id") == tid:
                                    ctx.all_results[_oz_i] = {"task_id": tid, "output": _oz_code}
                                    _oz_found = True
                                    break
                            if not _oz_found:
                                ctx.all_results.append({"task_id": tid, "output": _oz_code})
                            print(f"  [Arch Fix] ✅ Applied ordered fallback fix for {tid} ({domain})")
                            try:
                                from ledger import update_internal_api_ledger
                                update_internal_api_ledger(_oz_code, domain)
                            except Exception:
                                pass
                else:
                    # The batch arch-fix model returned nothing usable for these
                    # tasks.  Rather than retaining known-broken output and cycling
                    # into the next review/fix loop with the same errors, re-execute
                    # each failing task individually through its original scripter
                    # agent — the same path that produced the first output — but
                    # with the preflight errors injected as targeted feedback into
                    # task.context so the model knows exactly what to fix.
                    #
                    # Guard rails:
                    #   - Circuit breaker: if retry_counts[tid] >= 3 we stop
                    #     re-executing (same threshold as the wave validator) to
                    #     prevent an infinite spin on a task the model genuinely
                    #     cannot solve.  The broken output is retained as-is and
                    #     the review cycle will surface it as a hard failure.
                    #   - execute_task is imported lazily to avoid circular imports
                    #     (preflight → _helpers_exec → pipeline → preflight).
                    #   - task.context is temporarily extended with a focused error
                    #     summary then restored, so the retry is self-contained and
                    #     doesn't pollute later iteration context.
                    #   - If execute_task itself raises, the broken output is kept
                    #     and a warning is printed — we never crash the pipeline.
                    try:
                        from _helpers_exec import execute_task as _exec_task
                    except ImportError:
                        _exec_task = None

                    for tid in unapplied:
                        _strike = ctx.retry_counts.get(tid, 0)
                        if _strike >= 3:
                            print(f"  [Arch Fix] ⛔ Circuit breaker: {tid} ({domain}) "
                                  f"has {_strike} strike(s) — skipping re-execution, "
                                  f"retaining broken output.")
                            continue

                        if _exec_task is None:
                            print(f"  [Arch Fix] ⚠ execute_task unavailable for {tid} "
                                  f"({domain}) — retaining previous output.")
                            continue

                        task_obj = ctx.task_map.get(tid)
                        if not task_obj:
                            print(f"  [Arch Fix] ⚠ No task object for {tid} "
                                  f"({domain}) — retaining previous output.")
                            continue

                        # Build a compact, focused error summary for this task only.
                        # Pull only the violation blocks that mention this tid so
                        # the model isn't distracted by sibling errors.
                        _per_task_errors = "\n".join(
                            line for line in (ctx.pre_flight_errors or "").splitlines()
                            if tid in line or "## Static Pattern" in line
                               or "## Arg Count" in line or "## Phantom" in line
                               or "## Duplicate" in line or "## Scope" in line
                        )
                        if not _per_task_errors.strip():
                            _per_task_errors = TokenBudget._block_aware_collapse(
                                ctx.pre_flight_errors, 600
                            )

                        _error_injection = (
                            f"\n\n## ⚠ Pre-Flight Failures — You MUST fix ALL of these\n"
                            f"{_per_task_errors}\n"
                            f"Re-implement the task correctly. "
                            f"Do NOT repeat any of the violations listed above."
                        )

                        # Temporarily extend task.context with the error feedback.
                        _saved_context = task_obj.context or ""
                        task_obj.context = _saved_context + _error_injection

                        ctx.retry_counts[tid] = _strike + 1
                        print(f"  [Arch Fix] 🔁 Re-executing {tid} ({domain}) via "
                              f"original scripter (strike {ctx.retry_counts[tid]}/3)...")

                        try:
                            _new_output = _exec_task(
                                task_obj,
                                user_prompt=ctx.user_prompt,
                                director_output=ctx.director_output,
                                all_results=ctx.all_results_dict,
                                file_context="",
                                gdd_context=ctx.gdd_context,
                            )
                            # If Ollama is down, execute_task returns a fatal
                            # error sentinel.  Stop retrying this task — the
                            # circuit breaker will already be armed, and we
                            # must not overwrite existing output with an error
                            # string that looks like code to downstream phases.
                            try:
                                from ollama_client import is_fatal_ollama_error as _is_fatal
                            except ImportError:
                                _is_fatal = None
                            if _is_fatal and _is_fatal(_new_output):
                                print(f"  [Arch Fix] ⛔ Ollama error during re-execution of "
                                      f"{tid} ({domain}) — aborting retries for this task.")
                                ctx.retry_counts[tid] = 3  # arm circuit breaker
                            elif (_new_output and _new_output.strip()
                                    and not _is_comment_only(_new_output)
                                    and _task_has_code(_new_output)):
                                _re_code = _strip_search_replace_metadata(_new_output)
                                ctx.all_results_dict[tid] = _re_code
                                # Sync all_results list.
                                _re_found = False
                                for _re_i, _re_e in enumerate(ctx.all_results):
                                    if _re_e.get("task_id") == tid:
                                        ctx.all_results[_re_i] = {"task_id": tid, "output": _re_code}
                                        _re_found = True
                                        break
                                if not _re_found:
                                    ctx.all_results.append({"task_id": tid, "output": _re_code})
                                print(f"  [Arch Fix] ✅ Re-execution produced valid output "
                                      f"for {tid} ({domain}).")
                                try:
                                    from ledger import update_internal_api_ledger
                                    update_internal_api_ledger(_re_code, domain)
                                except Exception:
                                    pass
                            else:
                                print(f"  [Arch Fix] ⚠ Re-execution output for {tid} "
                                      f"({domain}) is empty/comment-only — retaining "
                                      f"previous output.")
                        except Exception as _re_err:
                            print(f"  [Arch Fix] ⚠ Re-execution raised for {tid} "
                                  f"({domain}): {_re_err} — retaining previous output.")
                        finally:
                            # Always restore context so later iterations are clean.
                            task_obj.context = _saved_context

    # ── Post-Fix Re-Validation ─────────────────────────────────────────────
    # After the arch-fix cycle patches task outputs, re-run the static checks
    # so ctx.pre_flight_errors reflects only violations that are STILL present.
    # Without this, the review loop's PASS-override at line ~535 of
    # _finalize_review.py would block approval on errors that were already fixed.
    if ctx.pre_flight_errors:
        print("  [Pre-Flight] Re-validating after arch-fix to clear resolved errors...")
        ctx.pre_flight_errors = ""
        _inject_empty_output_errors(ctx)
        _inject_static_pattern_errors(ctx)
        if ctx.pre_flight_errors:
            print(f"  [Pre-Flight] ⚠ {ctx.pre_flight_errors.count('## Static') + ctx.pre_flight_errors.count('## Empty')} "
                  f"violation(s) remain after arch-fix.")
        else:
            print("  [Pre-Flight] ✅ All violations resolved after arch-fix.")

    return ctx
