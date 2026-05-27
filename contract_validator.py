"""
contract_validator.py — Universal Contract-Driven Code Validator
=================================================================
Validates generated code against a structured API contract rather than
maintaining an ever-growing blacklist of known-bad names.

Design goals
------------
* **Positive-match only** — if you call an engine-namespaced symbol and it is
  not in the contract, it is wrong.  No list of bad names is needed.
* **Bare-call detection** — if a symbol that belongs to a namespace is called
  without that namespace prefix, flag it as a missing-namespace violation.
* **Codebase-agnostic** — the validator accepts any ``contract`` dict that
  maps namespace names to sets of approved symbols.  The Midway cartridge
  passes its own bridge contract; a future project passes its own.
* **No pipeline imports** — this module is intentionally import-free so it
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


# ── Data structures ──────────────────────────────────────────────────────────

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
        Maps a lowercase bare symbol name → its required namespace prefix
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


# ── Contract builder ─────────────────────────────────────────────────────────

# Lua standard library namespaces — never flagged as phantom APIs.
_LUA_STDLIB_NAMESPACES: FrozenSet[str] = frozenset({
    "math", "string", "table", "io", "os", "coroutine",
    "package", "debug", "utf8", "bit", "bit32",
})

# Regex that splits a raw contract key like
#   "SpawnStaticBox(lx, ly, lz, w, h, d) → handle"
# or "Engine.AwardTickets(n, label)"
# into at most the function-name part before the first '(' or whitespace.
_KEY_NAME_RE = re.compile(r'^([A-Za-z_][\w.]*?)(?:\s*\(|$|\s+→)')


def _extract_symbol_name(raw_key: str) -> Optional[str]:
    """Return the bare function/symbol name from a raw contract key string.

    Handles:
    * ``"SpawnStaticBox(lx, ly, lz, w, h, d) → handle"``  → ``"SpawnStaticBox"``
    * ``"Engine.AwardTickets(n, label)"``                  → ``"Engine.AwardTickets"``
    * ``"SetFriction/Restitution/GravityFactor/..."``      → skipped (returns None)
    * ``"Naming convention"``                              → skipped (returns None)
    * ``"OnLoadStatic"``                                   → ``"OnLoadStatic"``
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
        Maps a contract section name → the namespace prefix used in Lua code.
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
    # ── Default namespace map for the Midway cartridge ───────────────────────
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
    bare_name_to_ns: Dict[str, str] = {}          # lowercase bare → original-case namespace
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

    # ── Walk every section of the bridge contract ────────────────────────────
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
            # else: no namespace context — register as a known global but don't
            # add to bare_name_to_ns (globals don't need a prefix)

    # ── Also register Lua stdlib as approved so they are never flagged ────────
    # (belt-and-suspenders: the engine_namespaces check already excludes them,
    #  but being explicit avoids future surprises)
    for _stdlib_ns in _LUA_STDLIB_NAMESPACES:
        # Ensure stdlib namespaces are NOT in engine_namespaces
        engine_namespaces.discard(_stdlib_ns)

    # ── Build the human-readable hint string ─────────────────────────────────
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


# ── Lua content validator ────────────────────────────────────────────────────

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

    1. **Phantom API pass** — finds every ``Namespace.Symbol(`` call where
       ``namespace`` is a known engine namespace but ``namespace.symbol`` is
       NOT in the approved contract.

    2. **Bare-call pass** — finds every ``Symbol(`` call (without a namespace
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

    # ── Pass 1: phantom API (unknown namespaced call) ─────────────────────────
    for m in _NAMESPACED_CALL_RE.finditer(stripped):
        call_text = m.group(1)           # e.g. "MidwayPhysics.SetDensity"
        call_lower = call_text.lower()
        ns_lower = call_lower.split(".")[0]

        if ns_lower not in lua_contract.engine_namespaces:
            continue  # not an engine call — user-defined table method, skip
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
            label=f"phantom API '{call_text}' — not in bridge contract",
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

    # ── Pass 2: bare call (missing namespace prefix) ─────────────────────────
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
            continue  # not a known engine symbol — skip

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
            label=f"bare '{bare_name}()' — missing {required_ns}. namespace prefix",
            explanation=(
                f"'{bare_name}' is not a global function. "
                f"It belongs to the {required_ns} namespace. "
                f"Use {required_ns}.{bare_name}(...) instead."
            ),
        ))

    return violations


# ── Convenience: approved-hint string from an existing contract ──────────────

def approved_api_hint(lua_contract: LuaContract) -> str:
    """Return the human-readable approved-API hint string."""
    return lua_contract.approved_names_hint


# ── Self-test (run directly: python contract_validator.py) ───────────────────

if __name__ == "__main__":
    # Minimal smoke test — no pytest dependency required.
    _sample_contract = {
        "midwayphysics_spawn_api": {
            "SpawnStaticBox(lx, ly, lz, w, h, d) → handle": "static box",
            "SpawnDynamicSphere(lx, ly, lz, radius [, mass]) → handle": "dynamic sphere",
            "DestroyBody(handle)": "remove body",
            "IsSensorTriggered(handle) → bool": "sensor state",
            "ApplyImpulse(handle, ix, iy, iz)": "impulse",
            "GetPosition(handle) → lx, ly, lz": "position",
        },
        "economy_api": {
            "Engine.AwardTickets(n, label)": "award tickets",
            "Engine.GetStreak() → int": "get streak",
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
