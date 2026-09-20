"""
_preflight_static.py  Deterministic static guard checks for generated code
============================================================================
Extracted from _finalize_preflight.py to keep individual files under 1 000 lines.

Contains:
  - _inject_static_pattern_errors(ctx)  fires before any LLM reviewer sees
    generated code and injects pre_flight_errors for each pattern violation.
"""

from __future__ import annotations

import re
from models import PipelineContext
from midway_api_signatures import SPAWN_ARITY as _SPAWN_SIGS, BODY_ARITY as _BODY_SIGS


def _balanced_spawn_args(text: str, start_pos: int) -> str:
    """Extract the argument string between balanced parentheses, starting at
    the open-paren at start_pos. The outer parens are NOT included.
    """
    _depth = 0
    _result = []
    for _ch in text[start_pos:]:
        if _ch == '(':
            _depth += 1
            if _depth == 1:
                continue
        elif _ch == ')':
            _depth -= 1
            if _depth == 0:
                break
        if _depth >= 1:
            _result.append(_ch)
    return "".join(_result).strip()


# Combined arity map: Spawn* + body/pool APIs, so ONE deterministic pass can
# repair over-arg calls across the whole MidwayPhysics surface.  The tribunal's
# first precise verdict flagged CreatePool called with 10 args, which a
# Spawn-only pass could never catch.
_ARITY_SIGS = {**_SPAWN_SIGS, **_BODY_SIGS}


def _fix_spawn_arities_in_text(text: str):
    """Deterministically truncate over-arg MidwayPhysics.* calls down to the
    minimum arg count. Returns (fixed_text, n_fixed). Only shrinks over-arg
    calls; never pads under-arg calls or rewrites unknown functions. Covers
    Spawn* plus body/pool APIs (CreatePool, PoolAcquire, MoveKinematic, ...).
    """
    if not text:
        return text, 0
    _fixed = text
    _count = 0
    for _m in re.finditer(r'MidwayPhysics\.(\w+)\s*\(', text, re.IGNORECASE):
        _fn = _m.group(1)
        _sig = _ARITY_SIGS.get(_fn)
        if not _sig:
            continue
        _min_exp, _max_exp = _sig
        _args = _balanced_spawn_args(text, _m.start() + len(_m.group(0)) - 1)
        if not _args:
            continue
        _depth = 0
        _commas = 0
        for _ch in _args:
            if _ch in '({[':
                _depth += 1
            elif _ch in ')}]':
                _depth -= 1
            elif _ch == ',' and _depth == 0:
                _commas += 1
        _actual = _commas + 1
        if _min_exp <= _actual <= _max_exp:
            continue
        if _actual < _min_exp:
            continue  # under-arg — leave for the LLM fix loop
        _depth = 0
        _tokens = []
        _cur = ""
        for _ch in _args:
            if _ch in '({[':
                _depth += 1
            elif _ch in ')}]':
                _depth -= 1
            elif _ch == ',' and _depth == 0:
                _tokens.append(_cur.strip())
                _cur = ""
                continue
            _cur += _ch
        if _cur.strip():
            _tokens.append(_cur.strip())
        # Truncate over-arg calls to the MAX arity (not min), so optional args
        # (CreatePool's paramsTable, SpawnDynamic* mass) survive the repair.
        # Spawn* functions RETURN a handle and never TAKE one — the coder
        # repeatedly prepends a phantom handle (`SpawnStaticBox(tower_base,
        # lx, ly, ...)`), inflating the count by 1.  Drop that first arg rather
        # than the trailing positional arg (which would corrupt the call).
        if (_fn in _SPAWN_SIGS and _tokens
                and re.fullmatch(r'[A-Za-z_]\w*', _tokens[0])):
            _valid = _tokens[1:_max_exp + 1]
        else:
            _valid = _tokens[:_max_exp]
        if not _valid:
            continue
        _bad_call = _m.group(0) + _args + ")"
        _good_call = f"MidwayPhysics.{_fn}({', '.join(_valid)})"
        if _bad_call in _fixed:
            _fixed = _fixed.replace(_bad_call, _good_call, 1)
            _count += 1
    return _fixed, _count


def _inject_static_pattern_errors(ctx: PipelineContext) -> None:
    """Deterministic, compiler-free checks for patterns that are always wrong.

    These fire before any LLM reviewer sees the code, so they cannot be
    talked past by a permissive reviewer or skipped when no CMakeCache exists.
    Each guard targets a failure pattern witnessed in real pipeline runs.
    """
    # ── Guard patterns ────────────────────────────────────────────────────
    # Each entry: (domain_filter, regex, short_label, explanation)
    # domain_filter: None = all domains, otherwise only tasks for that agent.
    # Per-run dedupe: file-level rules (OnLoadStatic missing, static MOD
    # cache) fire ONCE per target file, not once per task sharing the file.
    _reported = getattr(ctx, '_static_guard_reported', None)
    if _reported is None:
        _reported = set()
        ctx._static_guard_reported = _reported

    # -- Fix G2: deterministic arity repair on the ACCUMULATED file ----------
    # Fix G patches the per-task fragment, but RuntimeSim checks the merged
    # on-disk file, so an over-arg Spawn call in the accumulated file survived
    # every cycle and deadlocked the review loop (task_8 / SpawnStaticBox 7→6).
    # Repair the file first so the guards and RuntimeSim both see clean arities.
    for _t in (ctx.task_map or {}).values():
        _tfa = getattr(_t, 'target_file', '') or ''
        if not _tfa.endswith('.lua'):
            continue
        _tfp = (ctx.project_root / _tfa).resolve()
        try:
            from _helpers_io import get_staging_path, is_staging_active
            if is_staging_active():
                _tfp = get_staging_path(_tfp, project_root=ctx.project_root)
        except Exception:
            pass
        if not _tfp.is_file():
            continue
        _tf_text = _tfp.read_text(encoding="utf-8", errors="replace")
        _tf_fixed, _tf_n = _fix_spawn_arities_in_text(_tf_text)
        if _tf_n:
            _tfp.write_text(_tf_fixed, encoding="utf-8")
            print(f"  [Fix G2] ✅ auto-patched {_tf_n} spawn arity issue(s) in {_tfa}")
            # Mirror the repaired file into the real target so the review-loop
            # luac check (which reads the real path) also sees the fix.
            try:
                _tfp_real = (ctx.project_root / _tfa).resolve()
                if _tfp_real != _tfp:
                    _tfp_real.parent.mkdir(parents=True, exist_ok=True)
                    _tfp_real.write_text(_tf_fixed, encoding="utf-8")
            except Exception:
                pass

    _GUARDS = [
        # Lua: require('nlohmann.json')  nlohmann is a C++ library.
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
        # C++: lua.set_function("X.Y", ...)  sol2 dot-notation table paths.
        (
            "C++",
            re.compile(r'\.set_function\s*\(\s*"[A-Za-z_]\w*\.[A-Za-z_]\w*"', re.IGNORECASE),
            "wrong sol2 table registration (dot-notation in set_function)",
            'sol2 set_function() does not accept "Table.Method" dot-path strings. '
            'Use lua["Table"]["Method"] = ... to register table-scoped functions.',
        ),
        # C7: Lua calling any sol.* method  sol is a C++ binding layer with no Lua-side object.
        # Covers sol.set_function, sol.new_usertype, sol.state, sol.script, sol.log_message,
        # and chained calls like sol.input.is_action_pressed(...), sol.state.open_libraries(...).
        (
            "Lua",
            re.compile(r'\bsol\s*(?:\.[A-Za-z_]\w*)+\s*\(', re.IGNORECASE),
            "sol.* called from Lua  sol is a C++ binding layer with no Lua-side object",
            "'sol' is a C++ namespace/object and does not exist at Lua runtime. "
            "Remove all sol.* calls (including sol.input.*, sol.state.*, etc.) from Lua code. "
            "Use print() for logging; player input is handled by engine callbacks, not sol.",
        ),
        # C8 / C8b / C8c / C8d: Bare engine calls (DestroyBody, IsSensorTriggered,
        # SpawnXxx, ApplyImpulse, etc.) without the required namespace prefix are now
        # caught universally by the contract validator (C9 / bare-call pass).
        # F19: SpawnStaticPlane and other phantom MidwayPhysics.* names are also
        # caught by the contract validator.
        # C15: MidwayPhysics.log / .log_message  subsumed by contract validator.
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
            "Engine.Method() dot-notation in C++  should be Engine::Method()",
            "C++ uses the :: scope operator, not the dot operator. "
            "Replace Engine.GetStreak() with Engine::GetStreak() (or via the singleton accessor).",
        ),
        # F18b: MidwayPhysics.Method() dot-notation in C++ (not Lua).
        (
            "C++",
            re.compile(r'\bMidwayPhysics\.[A-Z][A-Za-z_0-9]*\s*\(', re.IGNORECASE),
            "MidwayPhysics.Method() dot-notation in C++  should be MidwayPhysics::Method()",
            "C++ uses the :: scope operator. "
            "Replace MidwayPhysics.ApplyImpulse(...) with MidwayPhysics::ApplyImpulse(...) etc.",
        ),
        # C10: Duplicate top-level function definition in Lua.
        # Detected via post-guard check below  regex finds all names first.
        # C12: Bare OnStep defined  reliable two-pass check (see below).
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
            "MidwayInput.Method() dot-notation in C++  should be MidwayInput::Method()",
            "C++ uses the :: scope operator. "
            "Replace MidwayInput.Register(...) with MidwayInput::Register(...) etc.",
        ),
        # C19: Unknown MidwayInput method (phantom API).  The Lua bridge exposes
        # only IsActionDown(action) and IsKeyDown(name); any other MidwayInput.*
        # method is a hallucination (e.g. SetActionState) that fails at runtime.
        (
            "Lua",
            re.compile(
                r'\bMidwayInput\.(?!IsActionDown\s*\(|IsKeyDown\s*\()[A-Za-z_]\w*\s*\(',
                re.IGNORECASE,
            ),
            "unknown MidwayInput method",
            "MidwayInput only exposes IsActionDown(action) and IsKeyDown(name). "
            "Replace with one of those (e.g. MidwayInput.IsActionDown(\"fire\")).",
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
        # S1: Lua scaffold function  body is only a TODO comment or return nil stub.
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
            "scaffold/stub Lua function  body is a TODO, return nil, or ... pass-through",
            "The function contains only a placeholder body and is not a real implementation. "
            "Replace the stub body with a complete, working implementation.",
        ),
        # S2: C++ scaffold function  body contains only a TODO comment or a bare return.
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
            "scaffold/stub C++ function  body is a TODO comment or empty return",
            "The C++ function body is a placeholder and not a real implementation. "
            "Provide a complete function body with actual logic.",
        ),
        # G1: Undefined booth globals  BUTTON, SLOT_X/Y/Z, SharedBooth.
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
            "Do NOT call SharedBooth.ButtonZ() or access BOOTH.width_x/height_y/depth_z  "
            "those table fields do not exist in the runtime either.",
        ),
        # G2: Undefined input globals  Mouse.Position() / Input.Pressed().
        # These namespaces are not in the engine bridge contract and will error at runtime.
        (
            "Lua",
            re.compile(r'\b(?:Mouse|Input)\s*\.', re.MULTILINE),
            "undefined input global (Mouse.* / Input.*)",
            "Mouse and Input are NOT in the Midway engine bridge contract. "
            "Remove all calls to Mouse.Position(), Input.Pressed(), and similar. "
            "Player input is delivered through the OnStep dt callback and "
            "AttractionConstants.modifiers  do not poll a Mouse or Input namespace.",
        ),
    ]

    # ── Pre-pass: deterministically strip known phantom AttractionConstants / MidwayPhysics
    # calls that the model repeatedly hallucinates.  These are stripped before the guard
    # loop so the guards see clean content and do not fire on already-removed phantoms.
    # Each entry: (regex_to_match_full_statement, replacement_string, description)
    _PHANTOM_STRIP_PATTERNS = [
        # AttractionConstants.initializeSkeeballMachine()  no such method in contract
        (
            re.compile(
                r'\bAttractionConstants\.initialize\w+\(\s*\)[^\n]*\n?',
                re.MULTILINE,
            ),
            "",
            "phantom AttractionConstants.initialize*()",
        ),
        # AttractionConstants.getSkeeballMachinePos()  no such method in contract
        (
            re.compile(
                r'\bAttractionConstants\.get\w+\([^)]*\)[^\n]*\n?',
                re.MULTILINE,
            ),
            "",
            "phantom AttractionConstants.get*()",
        ),
        # MidwayPhysics.OnLoadStatic()  not a real bridge function
        (
            re.compile(
                r'\bMidwayPhysics\.OnLoadStatic\(\s*\)[^\n]*\n?',
                re.MULTILINE,
            ),
            "",
            "phantom MidwayPhysics.OnLoadStatic()",
        ),
        # AttractionConstants.booth / BOOTH table  these fields don't exist
        (
            re.compile(
                r'^[\t ]*local\s+BOOTH\s*=\s*AttractionConstants\.booth[^\n]*\n',
                re.MULTILINE,
            ),
            "",
            "phantom AttractionConstants.booth table",
        ),
        # local SLOT_X/Y/Z = BOOTH.*  depends on the stripped BOOTH line
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
        # BUTTON.* properties
        (
            re.compile(
                r'\bBUTTON\.(x|y|z|width|height|depth)\b',
                re.MULTILINE,
            ),
            "0.0",
            "phantom BUTTON properties",
        ),
        # SLOT_X, SLOT_Y, SLOT_Z
        (
            re.compile(
                r'\bSLOT_[XYZ]\b',
                re.MULTILINE,
            ),
            "0.0",
            "phantom SLOT_X/Y/Z globals",
        ),
        # SharedBooth bare reference
        (
            re.compile(
                r'\bSharedBooth\b',
                re.MULTILINE,
            ),
            "nil",
            "phantom SharedBooth reference",
        ),
        # MidwayPhysics.SpawnDynamicBoxR -> MidwayPhysics.SpawnDynamicBox
        (
            re.compile(r'\b(?:MidwayPhysics\.)?SpawnDynamicBoxR\b', re.IGNORECASE),
            r"MidwayPhysics.SpawnDynamicBox",
            "phantom SpawnDynamicBoxR",
        ),
        # MidwayPhysics.SpawnStaticBoxR -> MidwayPhysics.SpawnStaticBox
        (
            re.compile(r'\b(?:MidwayPhysics\.)?SpawnStaticBoxR\b', re.IGNORECASE),
            r"MidwayPhysics.SpawnStaticBox",
            "phantom SpawnStaticBoxR",
        ),
        # MidwayPhysics.FireBall -> MidwayPhysics.ApplyImpulse
        (
            re.compile(r'\b(?:MidwayPhysics\.)?FireBall\b', re.IGNORECASE),
            r"MidwayPhysics.ApplyImpulse",
            "phantom FireBall",
        ),
        # MidwayPhysics.GetBodies -> strip (not a real bridge API)
        (
            re.compile(r'\b(?:MidwayPhysics\.)?GetBodies\b', re.IGNORECASE),
            r"",
            "phantom GetBodies",
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
                for _fp_i, _fp_e in enumerate(ctx.all_results):
                    if _fp_e.get("task_id") == tid:
                        ctx.all_results[_fp_i] = {"task_id": tid, "output": content}
                        break

    for tid, content in list(ctx.all_results_dict.items()):
        if not content:
            continue
        task_obj = ctx.task_map.get(tid) if ctx.task_map else None
        domain = getattr(task_obj, "agent", None) if task_obj else None

        # FILE-LEVEL checks (C13 OnLoadStatic, C14 module-level spawn/poll)
        # must run against the MERGED file, not this task's SEARCH/REPLACE
        # fragment (which legitimately lacks OnLoadStatic and contains
        # OnStep-bound code that reads as "module level" in isolation).
        _file_content = content
        if task_obj is not None and getattr(task_obj, 'target_file', None):
            _merged_block = ctx.all_results_dict.get("merged:" + str(task_obj.target_file))
            if _merged_block:
                _file_content = _merged_block
            else:
                try:
                    _mf = (ctx.project_root / str(task_obj.target_file)).resolve()
                    # Staging-aware: the deterministic post-processor writes the
                    # CLEANED accumulated file to staging; read that copy so the
                    # guard sees the same content the post-processor produced.
                    try:
                        from _helpers_io import get_staging_path, is_staging_active
                        if is_staging_active():
                            _sp = get_staging_path(_mf, project_root=ctx.project_root)
                            if _sp.is_file():
                                _mf = _sp
                    except Exception:
                        pass
                    if _mf.is_file():
                        _file_content = _mf.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    pass

        for (guard_domain, pattern, label, explanation) in _GUARDS:
            if guard_domain and domain and domain != guard_domain:
                continue
            # File-type scoping: the declared task domain can disagree with its
            # target file (the task_2 Lua→C++ re-tag bug). The target file's
            # language is authoritative — C++-only guards must never run on Lua
            # content, or the F18b `::` rule fires on valid `MidwayPhysics.X()`.
            if guard_domain and task_obj is not None:
                _g_tf = str(getattr(task_obj, 'target_file', '') or '').lower()
                _g_is_lua = _g_tf.endswith('.lua') or bool(re.search(r'\bMidwayPhysics\.[A-Z]\w*\s*\(', content))
                _g_is_cpp = _g_tf.endswith(('.cpp', '.h', '.hpp', '.cc', '.cxx'))
                if guard_domain == "C++" and _g_is_lua:
                    continue
                if guard_domain == "Lua" and _g_is_cpp:
                    continue
            # When task_obj is absent (e.g. merged file or monolithic), infer domain from
            # the task_id extension so C++-only guards don't fire on Lua files.
            if guard_domain and domain is None:
                _tid_lower = tid.lower()
                _inferred_lua = (
                    _tid_lower.endswith(".lua")
                    or "lua" in _tid_lower
                    or "monolithic" in _tid_lower  # monolithic generation is always Lua
                )
                _inferred_cpp = (
                    _tid_lower.endswith(".cpp") or _tid_lower.endswith(".h")
                    or "cpp" in _tid_lower
                )
                if guard_domain == "C++" and _inferred_lua:
                    continue
                if guard_domain == "Lua" and _inferred_cpp:
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
        # _SPAWN_SIGS is imported at module top from midway_api_signatures so
        # the argument-count table can never drift from runtime_sim's.
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
                        # Skip the outermost '('  do NOT add it to _result
                        continue
                elif _ch == ')':
                    _depth -= 1
                    if _depth == 0:
                        break
                    # Closing paren at depth > 0 is a nested close  keep it
                if _depth >= 1:
                    _result.append(_ch)
            return "".join(_result).strip()

        if domain == "Lua":
            for _spawn_m in re.finditer(
                r'MidwayPhysics\.(Spawn\w+|CreatePool|Pool\w+)\s*\(',
                content, re.IGNORECASE
            ):
                _fn_name = _spawn_m.group(1)
                # Use depth-tracker instead of naive [^)] regex for args extraction
                _args_str = _balanced_spawn_args(content, _spawn_m.start() + len(_spawn_m.group(0)) - 1)
                _expected = _ARITY_SIGS.get(_fn_name)
                if _expected is None:
                    if _fn_name == "SpawnSharedBooth":
                        # SpawnSharedBooth() is a BARE global helper from
                        # attractions/booth_shared.lua - it is NOT a MidwayPhysics.*
                        # API.  The correct fix is to drop the prefix, not to
                        # substitute a different spawn primitive (that substitution
                        # is what drove the reviewer death-spiral).
                        ctx.pre_flight_errors += (
                            f"\n## Static Pattern Violation - Task {tid} [Lua]\n"
                            f"**Rule:** SpawnSharedBooth() must be called BARE (no MidwayPhysics. prefix).\n"
                            f"**Fix:** write `SpawnSharedBooth()`, not `MidwayPhysics.SpawnSharedBooth()`. "
                            f"It is a shared helper from attractions/booth_shared.lua.\n"
                        )
                        print(f"  [Static Guard] Task {tid} [Lua]: SpawnSharedBooth must be bare "
                              f"(drop MidwayPhysics. prefix)")
                        continue
                    # Unknown spawn call  flag as phantom API (C9 overlap)
                    ctx.pre_flight_errors += (
                        f"\n## Static Pattern Violation  Task {tid} [Lua]\n"
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
                            "CreatePool":           "name, hotN, coldN [, paramsTable]",
                            "PoolAcquire":          "name, lx, ly, lz",
                            "PoolReturn":           "name, handle",
                            "PoolCullBelow":        "name, yThreshold",
                            "PoolFree":             "name",
                            "PoolTotal":            "name",
                        }
                        _pos_hint = _POS_LABELS.get(_fn_name, f"{_min_exp}..{_max_exp} positional args")
                        _fg_patched = False
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
                        #      always numbers or variable names  never tables or strings).
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
                            # Keep only the first _max_exp positional args
                            # (max, not min, so optional args like CreatePool's
                            # paramsTable survive the truncation).
                            _valid_tokens = _tokens_g[:_max_exp]
                            # If short, pad by repeating the last usable value.
                            # "Usable" = a number or identifier (not a string/table).
                            _last_usable = 1.0  # safe default for any missing dimension
                            for _tk in reversed(_valid_tokens):
                                try:
                                    _last_usable = float(_tk)
                                    break
                                except (ValueError, TypeError):
                                    # Variable name like 'BALL_RADIUS'  use its value as hint
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
                                _fg_patched = True
                                content = _new_content_g
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

                        if _fg_patched:
                            continue

                        ctx.pre_flight_errors += (
                            f"\n## Static Pattern Violation  Task {tid} [Lua]\n"
                            f"**Rule:** MidwayPhysics.{_fn_name} wrong argument count "
                            f"(got {_actual}, expected {_min_exp}..{_max_exp})\n"
                            f"**Why this is always wrong:** Wrong argument count causes a "
                            f"runtime error or silent incorrect physics.\n"
                            f"**Required call signature:** "
                            f"MidwayPhysics.{_fn_name}({_pos_hint})\n"
                            f"**CRITICAL  NO handle or label parameter:** The first argument "
                            f"is ALWAYS the world X position (a number). "
                            f"There is NO handle, name string, or label argument. "
                            f"The function RETURNS a handle; it does NOT accept one as input. "
                            f"NEVER write MidwayPhysics.{_fn_name}('label', ...) "
                            f"or MidwayPhysics.{_fn_name}(handle, ...).\n"
                            f"Fix this before the reviewer sees the code.\n"
                        )
                        print(f"  [Static Guard] ❌ Task {tid} [Lua]: {_fn_name} arg count {_actual}≠{_min_exp}..{_max_exp}")

        # ── C9: Contract-driven API validation ────────────────────────────────
        # Instead of a growing blacklist of known-bad names, we validate
        # positively against the authoritative bridge contract from the
        # cartridge.  This catches both phantom (unknown namespaced) calls
        # AND bare calls (missing namespace prefix) for every symbol in the
        # contract  universally, without per-symbol maintenance.
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
                    # Validate the ACCUMULATED (already post-processed) file, not
                    # the raw per-task fragment, so bare calls / phantoms that the
                    # deterministic prefixer already fixed are not re-flagged every
                    # cycle.  Dedupe per target file: every task shares one file.
                    _c9_target = str(getattr(task_obj, 'target_file', None) or tid)
                    _c9_key = ("c9_file", _c9_target)
                    if _c9_key in _reported:
                        _cv_violations = []
                    else:
                        _reported.add(_c9_key)
                        _cv_source = (
                            _file_content
                            if (_file_content and _file_content.strip())
                            else content
                        )
                        _cv_violations = validate_lua_content(_cv_source, _lua_contract)
                    _cv_phantom_names: set = set()
                    for _viol in _cv_violations:
                        if _viol.label.startswith("static cache of modifiers"):
                            _dedup_key = ("mod_cache", getattr(task_obj, 'target_file', None) or tid)
                            if _dedup_key in _reported:
                                continue
                            _reported.add(_dedup_key)
                        ctx.pre_flight_errors += (
                            f"\n## Static Pattern Violation  Task {tid} [Lua]\n"
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
                            f"\n## Approved Bridge API  Task {tid} [Lua] "
                            f"(use ONLY these exact names)\n"
                            f"{_lua_contract.approved_names_hint}\n"
                        )
                        _marker = f"\n## Static Pattern Violation  Task {tid} [Lua]\n"
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
            #   function Foo()    classic style
            #   Foo = function()  assignment style
            for _fn_m in re.finditer(
                r'^(?:(?:local\s+)?function\s+(\w+)\s*\(|(\w+)\s*=\s*function\s*\()',
                content, re.MULTILINE
            ):
                _name = _fn_m.group(1) or _fn_m.group(2)
                _fn_names_seen[_name] = _fn_names_seen.get(_name, 0) + 1
            for _name, _count in _fn_names_seen.items():
                if _count > 1:
                    ctx.pre_flight_errors += (
                        f"\n## Static Pattern Violation  Task {tid} [Lua]\n"
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
                                f"\n## Static Pattern Violation  Task {tid} [Lua]\n"
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
        # MidwayPhysics.OnStep  do NOT flag it.  Only flag a *non-local* bare
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
                    f"\n## Static Pattern Violation  Task {tid} [Lua]\n"
                    f"**Rule:** OnStep defined as bare global but MidwayPhysics.OnStep never registered\n"
                    f"**Why this is always wrong:** The engine ignores bare OnStep globals. "
                    f"You MUST register via MidwayPhysics.OnStep(function(dt) ... end) inside OnLoad().\n"
                    f"Fix this before the reviewer sees the code.\n"
                )
                print(f"  [Static Guard] ❌ Task {tid} [Lua]: bare OnStep without MidwayPhysics.OnStep")

        # ── C13: Missing OnLoadStatic() definition ────────────────────────────
        # Every Lua attraction script must define OnLoadStatic() so the host
        # can call it during slot mounting.  An absent definition causes a
        # missing-hook engine error at runtime.
        if domain == "Lua":
            _has_load_static = bool(re.search(
                r'\bfunction\s+OnLoadStatic\s*\(',
                _file_content, re.MULTILINE
            ))
            if not _has_load_static:
                _dedup_key = ("lua:onloadstatic", getattr(task_obj, 'target_file', None) or tid)
                if _dedup_key not in _reported:
                    _reported.add(_dedup_key)
                    ctx.pre_flight_errors += (
                        f"\n## Static Pattern Violation  Task {tid} [Lua]\n"
                        f"**Rule:** OnLoadStatic() is missing\n"
                        f"**Why this is always wrong:** Every attraction script MUST define "
                        f"OnLoadStatic(). If there is no permanent geometry, add an empty stub:\n"
                        f"  function OnLoadStatic() end\n"
                        f"Omitting OnLoadStatic() will cause a missing-hook engine error at runtime.\n"
                        f"Fix this before the reviewer sees the code.\n"
                    )
                    print(f"  [Static Guard] ❌ Task {tid} [Lua]: OnLoadStatic() is missing")

        # ── C14: Module-level physics spawn or live-poll at script load time ──
        # SpawnDynamic* / SpawnStatic* calls and Engine.GetStreak() at the top
        # level of the script (outside any function) are always wrong:
        #   - Spawn at module load happens before the slot is mounted → crash/nil.
        #   - GetStreak() / AttractionConstants.modifiers must be re-read every
        #     frame inside OnStep, not cached once at load time.
        if domain == "Lua":
            # Strip all function bodies so we only inspect module-level lines.
            # Strategy: remove lines that are inside any 'function  end' block.
            # A simple heuristic: mark lines inside a function scope.
            _lines = _file_content.splitlines()
            _depth = 0
            _module_lines: list[str] = []
            for _ln in _lines:
                # strip inline comments before depth analysis
                _stripped = re.sub(r'--.*$', '', _ln).strip()
                if not _stripped:
                    continue
                # detect any function / block opener (including 'local function')
                _is_opener = bool(
                    re.search(r'\bfunction\b', _stripped) or
                    re.match(r'\b(do|if|for|while|repeat)\b', _stripped)
                )
                _is_closer = bool(
                    re.match(r'\bend\b', _stripped) or
                    re.match(r'\buntil\b', _stripped)
                )
                if _is_opener:
                    if _depth == 0:
                        # opener at module level — record it so spawn checks
                        # on the signature line still run, but do NOT treat
                        # the body as module-level code
                        _module_lines.append(_stripped)
                    _depth += 1
                    continue
                if _is_closer:
                    _depth = max(0, _depth - 1)
                    continue
                if _depth == 0:
                    _module_lines.append(_stripped)
            _module_src = "\n".join(_module_lines)

            _module_spawn = re.search(
                r'\bMidwayPhysics\.(SpawnDynamic\w+|SpawnStatic\w+)\s*\(',
                _module_src
            )
            _module_getstreak = re.search(
                r'\bEngine\.GetStreak\s*\(',
                _module_src
            )
            if _module_spawn:
                ctx.pre_flight_errors += (
                    f"\n## Static Pattern Violation  Task {tid} [Lua]\n"
                    f"**Rule:** MidwayPhysics.{_module_spawn.group(1)}() called at module level "
                    f"(outside any function)\n"
                    f"**Why this is always wrong:** The engine slot is not mounted at script-load "
                    f"time. Every Spawn* call MUST be inside OnLoad(). A module-level spawn will "
                    f"crash or silently return 0/nil.\n"
                    f"Move all SpawnDynamic* / SpawnStatic* calls into OnLoad().\n"
                    f"Fix this before the reviewer sees the code.\n"
                )
                print(f"  [Static Guard] ❌ Task {tid} [Lua]: {_module_spawn.group(0)} at module level")
            if _module_getstreak:
                ctx.pre_flight_errors += (
                    f"\n## Static Pattern Violation  Task {tid} [Lua]\n"
                    f"**Rule:** Engine.GetStreak() called at module level or cached once outside OnStep\n"
                    f"**Why this is always wrong:** GetStreak() returns a live value that changes "
                    f"during gameplay. It MUST be called inside the MidwayPhysics.OnStep closure "
                    f"every frame. A module-level call captures the value before the player has "
                    f"even started and it will never update.\n"
                    f"Move Engine.GetStreak() inside the OnStep closure.\n"
                    f"Fix this before the reviewer sees the code.\n"
                )
                print(f"  [Static Guard] ❌ Task {tid} [Lua]: Engine.GetStreak() polled at module level")

        # ── C19: Pre-registered module state re-declared by a task ───────────
        # The Skeleton Builder deterministically declares the Architect's handles
        # + module_state_variables at file root.  A task that writes `local <name>`
        # again (inside a function, or as a duplicate at root) shadows the
        # module-level declaration — the #1 deadlock cause in the execution mesh.
        if domain == "Lua":
            _design_c19 = getattr(ctx, 'attraction_design', None)
            _predeclared: set = set()
            if _design_c19 is not None:
                for _h in (getattr(_design_c19, 'handles', None) or []):
                    _hn = getattr(_h, 'name', '')
                    if _hn:
                        _predeclared.add(str(_hn).strip())
                for _sv in (getattr(_design_c19, 'module_state_variables', None) or []):
                    _sn = getattr(_sv, 'name', '')
                    if _sn:
                        _predeclared.add(str(_sn).strip())
            if _predeclared:
                _state_depth = 0
                _state_decls: dict = {}  # name -> [depths where `local name` appears]
                for _sln in _file_content.splitlines():
                    _sclean = re.sub(r'--.*$', '', _sln).strip()
                    if not _sclean:
                        continue
                    _is_open = bool(
                        re.search(r'\bfunction\b', _sclean)
                        or re.match(r'\b(do|if|for|while|repeat)\b', _sclean)
                    )
                    _is_close = bool(
                        re.match(r'\bend\b', _sclean)
                        or re.match(r'\buntil\b', _sclean)
                    )
                    if not _is_open:
                        _loc_m = re.search(
                            r'\blocal\s+([A-Za-z_]\w*)\s*(?:=|---|$)', _sclean
                        )
                        if _loc_m and _loc_m.group(1) in _predeclared:
                            _state_decls.setdefault(_loc_m.group(1), []).append(_state_depth)
                    if _is_open:
                        _state_depth += 1
                    if _is_close:
                        _state_depth = max(0, _state_depth - 1)
                for _pre_name in sorted(_predeclared):
                    _depths = _state_decls.get(_pre_name)
                    if not _depths:
                        continue  # never re-declared — canonical declaration is present
                    _non_root = any(d > 0 for d in _depths)
                    _dup_count = len(_depths)
                    if not _non_root and _dup_count <= 1:
                        continue  # the single canonical module-root declaration
                    _dedup_key = (
                        "lua:state_redeclare", _pre_name,
                        getattr(task_obj, 'target_file', None) or tid,
                    )
                    if _dedup_key in _reported:
                        continue
                    _reported.add(_dedup_key)
                    if _non_root:
                        _why = ("declared inside a function (not at module root) — it will be "
                                "re-initialized every frame and be nil in OnUnload")
                        _fix = (f"remove the `local` keyword and assign to the module-level "
                                f"`{_pre_name}` instead")
                    else:
                        _why = ("declared more than once — it is already declared at module root "
                                "by the skeleton builder")
                        _fix = f"remove the duplicate `local {_pre_name}` declaration"
                    ctx.pre_flight_errors += (
                        f"\n## Static Pattern Violation  Task {tid} [Lua]\n"
                        f"**Rule:** pre-registered variable `{_pre_name}` re-declared ({_dup_count}×)\n"
                        f"**Why this is always wrong:** {_why}.\n"
                        f"**How to fix:** {_fix}.\n"
                        f"Fix this before the reviewer sees the code.\n"
                    )
                    print(f"  [Static Guard] ❌ Task {tid} [Lua]: `{_pre_name}` re-declared "
                          f"(module-level state already exists)")

        # ── C20: Scalar read-before-write + global-leak (symbol-table guards) ──
        # One scope-aware symbol pass over the ACCUMULATED file catches two
        # classes of variable-state bugs that deterministic post-processing can
        # only partially repair (non-numeric defaults are ambiguous):
        #   - read-before-write scalars (nil-in-arithmetic runtime crash)
        #   - bare assignments that leak a _G global shared by every attraction
        # Both dedupe once per target file (11 tasks share one .lua file).
        if domain == "Lua":
            try:
                from _post_process_lua import _lua_symbol_table, _handle_identifiers
                _sym_src = _file_content if (_file_content and _file_content.strip()) else content
                _sym_declared, _sym_assigned, _sym_read = _lua_symbol_table(_sym_src)
                _sym_target = str(getattr(task_obj, 'target_file', None) or tid)

                _leak = sorted(n for n in _sym_assigned if n not in _sym_declared)
                if _leak:
                    _lk = ("lua:global_leak", _sym_target)
                    if _lk not in _reported:
                        _reported.add(_lk)
                        ctx.pre_flight_errors += (
                            f"\n## Static Pattern Violation — Task {tid} [Lua]\n"
                            f"**Rule:** assignment without `local` leaks a global: {', '.join(_leak)}\n"
                            f"**Why this is always wrong:** A bare `name = ...` creates a _G global "
                            f"shared by EVERY attraction in the slot, corrupting other rides.\n"
                            f"**How to fix:** prefix the assignment with `local` (or declare it at "
                            f"module root if it is meant to persist across frames).\n"
                            f"Fix this before the reviewer sees the code.\n"
                        )
                        print(f"  [Static Guard] ❌ Task {tid} [Lua]: global leak(s): {', '.join(_leak)}")

                _handles = _handle_identifiers(_sym_src)
                _rw = sorted(
                    n for n in _sym_read
                    if n not in _sym_declared and n not in _sym_assigned and n not in _handles
                )
                if _rw:
                    _rk = ("lua:read_before_write", _sym_target)
                    if _rk not in _reported:
                        _reported.add(_rk)
                        ctx.pre_flight_errors += (
                            f"\n## Static Pattern Violation — Task {tid} [Lua]\n"
                            f"**Rule:** variable read before it is declared or assigned: {', '.join(_rw)}\n"
                            f"**Why this is always wrong:** Reading an undeclared scalar yields nil; "
                            f"using it in arithmetic crashes at runtime.\n"
                            f"**How to fix:** declare each at module root with the correct default "
                            f"(0 for counters/scores, false for flags, \"\" for strings).\n"
                            f"Fix this before the reviewer sees the code.\n"
                        )
                        print(f"  [Static Guard] ❌ Task {tid} [Lua]: read-before-write: {', '.join(_rw)}")
            except Exception as _sym_err:
                print(f"  [Static Guard] ⚠ symbol-table guard error: {_sym_err}")
