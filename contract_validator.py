"""
contract_validator.py - Universal Contract-Driven Code Validator
=================================================================
Validates generated code against a structured API contract rather than
maintaining an ever-growing blacklist of known-bad names.

Design goals
------------
* **Positive-match only** - if you call an engine-namespaced symbol and it is
  not in the contract, it is wrong.  No list of bad names is needed.
* **Bare-call detection** - if a symbol that belongs to a namespace is called
  without that namespace prefix, flag it as a missing-namespace violation.
* **Codebase-agnostic** - the validator accepts any ``contract`` dict that
  maps namespace names to sets of approved symbols.  The Midway cartridge
  passes its own bridge contract; a future project passes its own.
* **No pipeline imports** - this module is intentionally import-free so it
  can be unit-tested and reused outside the pipeline with zero side-effects.

Contract dict shape (as produced by ``build_bridge_contract()``)
----------------------------------------------------------------
The validator does NOT require a specific top-level layout.  It walks the
dict recursively and extracts entries of the form::

    "Namespace.SymbolName(..." : <anything>      # economy_api style
    "SymbolName(..."           : <anything>      # midwayphysics_spawn_api style
                                                 # (namespace supplied by section name)

Callers that need finer control can also pass a pre-built
``LuaContract`` object directly.

Public API
----------
``build_lua_contract(bridge_contract, namespace_map)``
    Parse a raw bridge-contract dict into a ``LuaContract``.

``validate_lua_content(content, lua_contract)``
    Scan Lua source text and return a list of ``ContractViolation``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, NamedTuple, Optional, Set, Tuple


# -- Data structures ----------------------------------------------------------

class ContractViolation(NamedTuple):
    """A single detected violation."""
    kind: str          # "phantom_api" | "bare_call" | "phantom_global"
    call_text: str     # the exact text that triggered the violation, e.g. "MidwayPhysics.SetDensity"
    label: str         # short human-readable label for pipeline error headers
    explanation: str   # longer explanation for the fix agent


@dataclass
class LuaContract:
    """Compiled view of an API contract for Lua validation.

    Attributes
    ----------
    engine_namespaces
        Lowercase set of namespace prefixes whose calls we validate, e.g.
        ``{"midwayphysics", "engine", "attractionconstants"}``.
        Any ``Namespace.Foo(`` call where ``namespace`` is in this set but
        ``namespace.foo`` is NOT in ``approved_calls`` is a phantom API.

    approved_calls
        Lowercase set of ``"namespace.symbol"`` strings that are known-good.
        e.g. ``{"midwayphysics.spawnstaticbox", "engine.awardtickets", ...}``.

    bare_name_to_namespace
        Maps a lowercase bare symbol name to its required namespace prefix
        (original case), e.g. ``{"destroybody": "MidwayPhysics", ...}``.
        Used to detect bare calls like ``DestroyBody(handle)`` without the
        ``MidwayPhysics.`` prefix.

    approved_names_hint
        Human-readable string listing all approved calls grouped by namespace.
        Prepended once to error output so the fix agent always knows the
        complete correct API surface.
    """
    engine_namespaces: FrozenSet[str] = field(default_factory=frozenset)
    approved_calls: FrozenSet[str] = field(default_factory=frozenset)
    bare_name_to_namespace: Dict[str, str] = field(default_factory=dict)
    approved_names_hint: str = ""


# -- Contract builder ---------------------------------------------------------

# Lua standard library namespaces - never flagged as phantom APIs.
_LUA_STDLIB_NAMESPACES: FrozenSet[str] = frozenset({
    "math", "string", "table", "io", "os", "coroutine",
    "package", "debug", "utf8", "bit", "bit32",
})

# Regex that splits a raw contract key like
#   "SpawnStaticBox(lx, ly, lz, w, h, d) -> handle"
# or "Engine.AwardTickets(n, label)"
# into at most the function-name part before the first '(' or whitespace.
_KEY_NAME_RE = re.compile(r'^([A-Za-z_][\w.]*?)(?:\s*\(|$|\s+>)')


def _extract_symbol_name(raw_key: str) -> Optional[str]:
    """Return the bare function/symbol name from a raw contract key string.

    Handles:
    * ``"SpawnStaticBox(lx, ly, lz, w, h, d) -> handle"``  -> ``"SpawnStaticBox"``
    * ``"Engine.AwardTickets(n, label)"``                  -> ``"Engine.AwardTickets"``
    * ``"SetFriction/Restitution/GravityFactor/..."``      -> skipped (returns None)
    * ``"Naming convention"``                              -> skipped (returns None)
    * ``"OnLoadStatic"``                                   -> ``"OnLoadStatic"``
    """
    m = _KEY_NAME_RE.match(raw_key.strip())
    if not m:
        return None
    name = m.group(1)
    # Skip keys that look like prose or contain slashes (compound entries)
    if '/' in name or ' ' in name:
        return None
    return name


def build_lua_contract(
    bridge_contract: dict,
    *,
    namespace_map: Optional[Dict[str, str]] = None,
    extra_engine_namespaces: Optional[Set[str]] = None,
) -> LuaContract:
    """Build a ``LuaContract`` from a raw bridge-contract dict.

    Parameters
    ----------
    bridge_contract
        The dict returned by ``build_bridge_contract()``.  The validator
        inspects keys at every depth level that look like function signatures.

    namespace_map
        Maps a contract section name to the namespace prefix used in Lua code.
        Defaults to the Midway-specific mapping below; callers can override
        for different cartridges.

        Example::

            {
                "midwayphysics_spawn_api": "MidwayPhysics",
                "object_pools":            "MidwayPhysics",
                "economy_api":             "Engine",
            }

        Sections not listed here are treated as globals (no namespace prefix
        required in code), e.g. ``script_lifecycle`` callback names.

    extra_engine_namespaces
        Additional namespace strings to treat as engine namespaces beyond
        those derived from the namespace_map.  Useful for adding ``"sol"``
        or other injected namespaces that have no approved symbols but
        should still cause phantom-API errors if called from Lua.
    """
    # -- Default namespace map for the Midway cartridge -----------------------
    if namespace_map is None:
        namespace_map = {
            "midwayphysics_spawn_api": "MidwayPhysics",
            "object_pools":            "MidwayPhysics",
            "economy_api":             "Engine",
            # AttractionConstants are read as plain table values in Lua, not
            # function calls, but register the namespace so phantom calls are
            # still caught.
            "modifier_globals":        "AttractionConstants",
        }

    approved_calls: Set[str] = set()
    bare_name_to_ns: Dict[str, str] = {}          # lowercase bare -> original-case namespace
    engine_namespaces: Set[str] = set()

    # Collect engine namespaces from the map
    for ns in namespace_map.values():
        engine_namespaces.add(ns.lower())

    if extra_engine_namespaces:
        engine_namespaces.update(ns.lower() for ns in extra_engine_namespaces)

    def _register(symbol_name: str, namespace: str) -> None:
        """Register one approved symbol under its namespace."""
        ns_lower = namespace.lower()
        sym_lower = symbol_name.lower()
        approved_calls.add(f"{ns_lower}.{sym_lower}")
        bare_name_to_ns[sym_lower] = namespace  # original case for error messages

    # -- Walk every section of the bridge contract ----------------------------
    for section_key, section_value in bridge_contract.items():
        namespace = namespace_map.get(section_key)

        if not isinstance(section_value, dict):
            continue  # skip lists and scalars

        for raw_key in section_value:
            extracted = _extract_symbol_name(str(raw_key))
            if not extracted:
                continue

            if "." in extracted:
                # Key already contains a namespace prefix, e.g. "Engine.AwardTickets"
                parts = extracted.split(".", 1)
                ns_from_key = parts[0]
                sym = parts[1]
                engine_namespaces.add(ns_from_key.lower())
                _register(sym, ns_from_key)
            elif namespace:
                # Bare symbol name under a known-namespace section
                _register(extracted, namespace)
            # else: no namespace context - register as a known global but don't
            # add to bare_name_to_ns (globals don't need a prefix)

    # -- Also register Lua stdlib as approved so they are never flagged --------
    # (belt-and-suspenders: the engine_namespaces check already excludes them,
    #  but being explicit avoids future surprises)
    for _stdlib_ns in _LUA_STDLIB_NAMESPACES:
        # Ensure stdlib namespaces are NOT in engine_namespaces
        engine_namespaces.discard(_stdlib_ns)

    # -- Build the human-readable hint string ---------------------------------
    ns_groups: Dict[str, List[str]] = {}
    for full_call in sorted(approved_calls):
        ns, sym = full_call.split(".", 1)
        if ns in engine_namespaces:
            ns_groups.setdefault(ns, []).append(sym)

    approved_names_hint = "; ".join(
        f"{ns.capitalize() if ns.islower() else ns}: {', '.join(sorted(syms))}"
        for ns, syms in sorted(ns_groups.items())
    )

    return LuaContract(
        engine_namespaces=frozenset(engine_namespaces),
        approved_calls=frozenset(approved_calls),
        bare_name_to_namespace=bare_name_to_ns,
        approved_names_hint=approved_names_hint,
    )


# -- Lua content validator ----------------------------------------------------

# Matches any `Identifier.Method(` call in Lua source.
_NAMESPACED_CALL_RE = re.compile(r'\b([A-Za-z_]\w*\.[A-Za-z_]\w*)\s*\(')

# Matches a bare function call `Identifier(` that is NOT preceded by a dot
# (ruling out method calls like `self.handle` or `pool.acquire(`).
# The negative lookbehind on '.' and word-chars avoids matching method tails.
_BARE_CALL_RE = re.compile(r'(?<![.\w])([A-Z][A-Za-z_]\w*)\s*\(')


def validate_lua_content(
    content: str,
    lua_contract: LuaContract,
) -> List[ContractViolation]:
    """Scan Lua source text and return a list of ``ContractViolation``.

    Performs two independent passes:

    1. **Phantom API pass** - finds every ``Namespace.Symbol(`` call where
       ``namespace`` is a known engine namespace but ``namespace.symbol`` is
       NOT in the approved contract.

    2. **Bare-call pass** - finds every ``Symbol(`` call (without a namespace
       prefix) where ``symbol`` is a known contract function that *requires*
       a namespace prefix.

    Comments are stripped before scanning so that documentation lines never
    produce false positives.
    """
    violations: List[ContractViolation] = []

    # Strip single-line Lua comments (``-- ...``) before scanning.
    # Block comments (``--[[ ... ]]``) are rare in generated code; single-line
    # stripping is sufficient for false-positive suppression.
    stripped = re.sub(r'--[^\n]*', '', content)

    seen_phantoms: Set[str] = set()
    seen_bare: Set[str] = set()

    # -- Pass 1: phantom API (unknown namespaced call) -------------------------
    for m in _NAMESPACED_CALL_RE.finditer(stripped):
        call_text = m.group(1)           # e.g. "MidwayPhysics.SetDensity"
        call_lower = call_text.lower()
        ns_lower = call_lower.split(".")[0]

        if ns_lower not in lua_contract.engine_namespaces:
            continue  # not an engine call - user-defined table method, skip
        if ns_lower in _LUA_STDLIB_NAMESPACES:
            continue
        if call_lower in lua_contract.approved_calls:
            continue  # known-good

        if call_lower in seen_phantoms:
            continue  # deduplicate per content block
        seen_phantoms.add(call_lower)

        sym = call_text.split(".", 1)[1]
        violations.append(ContractViolation(
            kind="phantom_api",
            call_text=call_text,
            label=f"phantom API '{call_text}' - not in bridge contract",
            explanation=(
                f"'{call_text}' is not registered in the engine bridge contract. "
                f"Any call not on the approved list will crash at runtime. "
                f"Do NOT invent a replacement name. "
                f"Approved {call_text.split('.')[0]} calls: "
                + ", ".join(
                    s for s in sorted(lua_contract.approved_calls)
                    if s.startswith(ns_lower + ".")
                )
                + "."
            ),
        ))

    # -- Pass 2: bare call (missing namespace prefix) -------------------------
    # Build an exemption set: symbols that appear as Lua function *definitions*
    # (local or global) are valid bare names in their own file and must never be
    # flagged as missing a namespace prefix, even if the engine has a same-named
    # API.  This prevents false positives on patterns like:
    #   local function OnStep(dt) ... end
    #   MidwayPhysics.OnStep(function(dt) OnStep(dt) end)
    # where `OnStep(dt)` inside the lambda correctly refers to the local, not to
    # a missing `MidwayPhysics.OnStep` call.
    _defined_fn_names_lower: Set[str] = set()
    for _def_m in re.finditer(
        r'(?:^|\s)(?:local\s+)?function\s+([A-Za-z_]\w*)\s*\(',
        stripped,
        re.MULTILINE,
    ):
        _defined_fn_names_lower.add(_def_m.group(1).lower())

    for m in _BARE_CALL_RE.finditer(stripped):
        bare_name = m.group(1)           # e.g. "DestroyBody"
        bare_lower = bare_name.lower()

        if bare_lower not in lua_contract.bare_name_to_namespace:
            continue  # not a known engine symbol - skip

        # If the same name is defined as a local/global function in this file,
        # the call is a valid intra-file call, not a missing-namespace API call.
        if bare_lower in _defined_fn_names_lower:
            continue

        if bare_lower in seen_bare:
            continue
        seen_bare.add(bare_lower)

        required_ns = lua_contract.bare_name_to_namespace[bare_lower]
        violations.append(ContractViolation(
            kind="bare_call",
            call_text=bare_name,
            label=f"bare '{bare_name}()' - missing {required_ns}. namespace prefix",
            explanation=(
                f"'{bare_name}' is not a global function. "
                f"It belongs to the {required_ns} namespace. "
                f"Use {required_ns}.{bare_name}(...) instead."
            ),
        ))

    # -- Pass 3: business logic violation - static modifier caching ----------
    # Detect module-level or OnLoad-scoped caching of AttractionConstants.modifiers.
    # Modifiers change every frame and MUST be read inside the OnStep closure.
    # Pattern: ``local <name> = AttractionConstants.modifiers``
    # outside an ``function(<dt>)`` / ``OnStep(function(dt)`` context.
    if "AttractionConstants.modifiers" in stripped:
        # First, find all OnStep closure boundaries
        # A valid OnStep closure looks like:
        #   OnStep(function(dt) ... end)    or
        #   MidwayPhysics.OnStep(function(dt) ... end)
        # Extract the text inside the closure body for each match.
        onstep_ranges: List[Tuple[int, int]] = []
        for _os_m in re.finditer(
            r'(?:MidwayPhysics\.)?OnStep\s*\(\s*function\s*\([^)]*\)\s*',
            stripped,
        ):
            # Find the matching ``end`` for this closure by counting nesting depth
            _start = _os_m.end()
            _depth = 0
            _pos = _start
            while _pos < len(stripped):
                _ch = stripped[_pos]
                if _ch == '\n':
                    _pos += 1
                    continue
                # Skip comments and strings (simplified - enough for generated code)
                if stripped[_pos:_pos+2] == '--':
                    _next_newline = stripped.find('\n', _pos)
                    if _next_newline == -1:
                        break
                    _pos = _next_newline + 1
                    continue
                if _ch == 't' and stripped[_pos:_pos+3] == 'end':
                    if _depth == 0:
                        onstep_ranges.append((_os_m.start(), _pos + 3))
                        break
                    _depth -= 1
                    _pos += 3
                    continue
                if _ch == 'f' and stripped[_pos:_pos+8] == 'function':
                    _depth += 1
                    _pos += 8
                    continue
                # Handle nested ``if ... end``, ``for ... end`` etc. so depth
                # tracking stays correct
                # Simpler approach: just track 'function' entries
                _pos += 1
            else:
                # Unclosed function - skip this match
                pass

        # -- Extend OnStep ranges to cover called helper functions ----------
        # If OnStep calls a named helper (e.g. ``GameLogic(dt)``), the local
        # MOD = ... inside that helper is semantically "inside" OnStep even
        # though it's not syntactically within the closure body.
        # Scan for function definitions whose names appear as function calls
        # inside any OnStep range, and extend the range to cover those defs.
        _onstep_called_funcs: set = set()
        for _range_start, _range_end in onstep_ranges:
            _onstep_body = stripped[_range_start:_range_end]
            for _call_m in re.finditer(
                r'\b([A-Za-z_]\w*)\s*\(',
                _onstep_body,
            ):
                _fn_name = _call_m.group(1)
                if _fn_name.lower() in (
                    'if', 'for', 'while', 'function', 'local', 'return',
                    'end', 'then', 'else', 'elseif', 'do', 'in', 'not',
                    'and', 'or', 'nil', 'true', 'false', 'repeat',
                    'until', 'break', 'print', 'type', 'pairs', 'ipairs',
                    'next', 'select', 'tonumber', 'tostring', 'pcall',
                    'xpcall', 'rawget', 'rawset', 'setmetatable',
                    'getmetatable', 'assert', 'error', 'require', 'dofile',
                    'loadfile', 'load', 'math', 'string', 'table', 'os',
                    'io', 'debug', 'coroutine', 'Engine', 'MidwayPhysics',
                    'AttractionConstants',
                ):
                    continue
                _onstep_called_funcs.add(_fn_name)
        # Find definitions of called functions and extend ranges
        if _onstep_called_funcs:
            _called_pattern = '|'.join(re.escape(f) for f in _onstep_called_funcs)
            for _def_m in re.finditer(
                r'(?:local\s+)?function\s+(' + _called_pattern + r')\s*\(',
                stripped,
            ):
                _def_start = _def_m.start()
                _depth = 0
                _pos = _def_m.end()
                while _pos < len(stripped):
                    _ch = stripped[_pos]
                    if _ch == '\n':
                        _pos += 1
                        continue
                    if stripped[_pos:_pos+2] == '--':
                        _next_newline = stripped.find('\n', _pos)
                        if _next_newline == -1:
                            break
                        _pos = _next_newline + 1
                        continue
                    if _ch == 'e' and stripped[_pos:_pos+3] == 'end':
                        if _depth == 0:
                            onstep_ranges.append((_def_start, _pos + 3))
                            break
                        _depth -= 1
                        _pos += 3
                        continue
                    if _ch == 'f' and stripped[_pos:_pos+8] == 'function':
                        _depth += 1
                        _pos += 8
                        continue
                    _pos += 1

        # Detect static caching: ``local <name> = AttractionConstants.modifiers``
        # This regex matches patterns like:
        #   local mods = AttractionConstants.modifiers
        #   local MOD = AttractionConstants.modifiers
        #   local _m = AttractionConstants.modifiers
        for _cache_m in re.finditer(
            r'local\s+([A-Za-z_]\w*)\s*=\s*AttractionConstants\.modifiers\b',
            stripped,
        ):
            _cache_pos = _cache_m.start()
            _variable_name = _cache_m.group(1)

            # Check if this caching is inside an OnStep closure body
            # (now includes extended ranges for called helper functions)
            _inside_onstep = any(
                _range_start <= _cache_pos <= _range_end
                for _range_start, _range_end in onstep_ranges
            )

            if not _inside_onstep:
                # Also check if it's inside a ``function(dt)`` that might be
                # the OnStep callback argument (belt-and-suspenders)
                # Look for the nearest enclosing function:
                # Walk backward to find if there's an OnStep(function(dt) before
                # this position at a lower nesting depth.
                _pre_text = stripped[:_cache_pos]
                _pre_onstep_count = len(list(re.finditer(
                    r'(?:MidwayPhysics\.)?OnStep\s*\(\s*function\s*\([^)]*\)',
                    _pre_text,
                )))
                _pre_function_count = len(list(re.finditer(
                    r'\bfunction\s*\([^)]*\)',
                    _pre_text,
                )))
                # Simple heuristic: if the number of function(...) before this
                # point exceeds the number of OnStep(function(...), it is
                # outside any OnStep closure (e.g. inside OnLoad or module-level).
                if _pre_function_count <= _pre_onstep_count:
                    # Edge case: could be inside a nested inside OnStep closure
                    # but with no extra functions between. Fall through to check
                    # the range-based check.
                    pass  # already handled by range check above

                if not _inside_onstep:
                    violations.append(ContractViolation(
                        kind="business_logic_violation",
                        call_text=f"local {_variable_name} = AttractionConstants.modifiers",
                        label=(
                            f"static cache of modifiers at module/OnLoad scope "
                            f"(`{_variable_name}`)"
                        ),
                        explanation=(
                            f"AttractionConstants.modifiers was cached into local "
                            f"variable `{_variable_name}` at module level or inside "
                            f"OnLoad(). Modifier values change every frame based on "
                            f"the player's Karma, streak, and NPC interactions. "
                            f"Caching them once at load time means the attraction "
                            f"will read stale values for the entire session. "
                            f"MUST read AttractionConstants.modifiers inside the "
                            f"MidwayPhysics.OnStep(function(dt) ... end) closure "
                            f"every frame instead."
                        ),
                    ))

    return violations


# -- Convenience: approved-hint string from an existing contract --------------

def approved_api_hint(lua_contract: LuaContract) -> str:
    """Return the human-readable approved-API hint string."""
    return lua_contract.approved_names_hint


# -- Self-test (run directly: python contract_validator.py) -------------------

if __name__ == "__main__":
    # Minimal smoke test - no pytest dependency required.
    _sample_contract = {
        "midwayphysics_spawn_api": {
            "SpawnStaticBox(lx, ly, lz, w, h, d) -> handle": "static box",
            "SpawnDynamicSphere(lx, ly, lz, radius [, mass]) -> handle": "dynamic sphere",
            "DestroyBody(handle)": "remove body",
            "IsSensorTriggered(handle) -> bool": "sensor state",
            "ApplyImpulse(handle, ix, iy, iz)": "impulse",
            "GetPosition(handle) -> lx, ly, lz": "position",
        },
        "economy_api": {
            "Engine.AwardTickets(n, label)": "award tickets",
            "Engine.GetStreak() -> int": "get streak",
        },
    }

    _contract = build_lua_contract(_sample_contract)
    print("Engine namespaces:", sorted(_contract.engine_namespaces))
    print("Approved calls:", sorted(_contract.approved_calls))
    print("Bare name map:", dict(sorted(_contract.bare_name_to_namespace.items())))
    print()

    _lua_source = """
-- good usage
local h = MidwayPhysics.SpawnStaticBox(0,0,0,1,1,1)
MidwayPhysics.DestroyBody(h)
Engine.AwardTickets(10, "win")

-- bad: phantom API
MidwayPhysics.SetDensity(h, 5.0)
MidwayPhysics.SpawnStaticPlane(0,0,0,10,10)

-- bad: bare calls (missing namespace)
DestroyBody(h)
IsSensorTriggered(sensorHandle)
ApplyImpulse(h, 0, 5, 0)
"""

    _violations = validate_lua_content(_lua_source, _contract)
    print(f"Violations found: {len(_violations)}")
    for v in _violations:
        print(f"  [{v.kind}] {v.label}")

    assert len(_violations) == 5, f"Expected 5, got {len(_violations)}"
    kinds = [v.kind for v in _violations]
    assert kinds.count("phantom_api") == 2
    assert kinds.count("bare_call") == 3
    print("\nAll assertions passed.")
