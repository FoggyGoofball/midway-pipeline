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

        for (guard_domain, pattern, label, explanation) in _GUARDS:
            if guard_domain and domain and domain != guard_domain:
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
                r'MidwayPhysics\.(Spawn\w+)\s*\(',
                content, re.IGNORECASE
            ):
                _fn_name = _spawn_m.group(1)
                # Use depth-tracker instead of naive [^)] regex for args extraction
                _args_str = _balanced_spawn_args(content, _spawn_m.start() + len(_spawn_m.group(0)) - 1)
                _expected = _SPAWN_SIGS.get(_fn_name)
                if _expected is None:
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
                        }
                        _pos_hint = _POS_LABELS.get(_fn_name, f"{_min_exp}{_max_exp} positional args")
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
                            f"\n## Static Pattern Violation  Task {tid} [Lua]\n"
                            f"**Rule:** MidwayPhysics.{_fn_name} wrong argument count "
                            f"(got {_actual}, expected {_min_exp}{_max_exp})\n"
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
                        print(f"  [Static Guard] ❌ Task {tid} [Lua]: {_fn_name} arg count {_actual}≠{_min_exp}{_max_exp}")

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
                    _cv_violations = validate_lua_content(content, _lua_contract)
                    _cv_phantom_names: set = set()
                    for _viol in _cv_violations:
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
                content, re.MULTILINE
            ))
            if not _has_load_static:
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
            _lines = content.splitlines()
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
