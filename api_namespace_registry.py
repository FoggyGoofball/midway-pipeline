"""
api_namespace_registry.py — Language-agnostic API namespace registry.
=====================================================================

Single source of truth for the *correctly-cased* API surface that generated
code must call.  Two consumers read from here so the knowledge never forks:

  1. ``_post_process_lua.py`` — the deterministic bare-call prefixer (Fix #6)
     rewrites ``PoolAcquire(...)`` -> ``MidwayPhysics.PoolAcquire(...)`` using
     the namespaces + globals + aliases below.
  2. ``lora generator/negative_api_lora_generator.py`` — builds the negative
     training examples that teach the coder NOT to emit bare calls, phantom
     APIs, or misspelled namespaces.

Why not derive from ``docs/engine_lua_bridge_contract.md`` at runtime?
The bridge contract is parsed into LOWERCASE symbol names (see
``contract_validator.build_lua_contract``); re-casing "spawnstaticbox" back to
"SpawnStaticBox" is lossy.  This registry is the authority for the EXACT
casing the fixer writes and the datasets teach.

Adding a language later is one dict entry keyed by language tag.  Every
consumer takes ``language="lua"`` and ignores unknown languages gracefully.

Keys per language:
    namespaces  { Namespace: [Symbol, ...] }   bare ``Symbol(...)`` -> ``Namespace.Symbol(...)``
    globals     { Symbol, ... }                standalone globals that must NOT be prefixed
    aliases     { WrongNs: RightNs }           ``WrongNs.X`` -> ``RightNs.X`` (hallucinated/typo namespaces)
    phantoms    { "Ns.Symbol": None | "Ns.Other" }  known-hallucinated calls; None = neutralize, str = rewrite
"""

from __future__ import annotations

from typing import Dict, FrozenSet, Mapping, Optional

# ─────────────────────────────────────────────────────────────────────────────
# Lua (Midway to Nowhere — the reference cartridge)
# ─────────────────────────────────────────────────────────────────────────────
_LUA_MIDWAYPHYSICS = frozenset({
    # Spawn functions
    "SpawnDynamicMesh", "SpawnDynamicBox", "SpawnDynamicSphere",
    "SpawnDynamicCapsule", "SpawnDynamicCylinder",
    "SpawnDynamicBoxR", "SpawnDynamicSphereR",
    "SpawnDynamicCapsuleR", "SpawnDynamicCylinderR",
    "SpawnStaticMesh", "SpawnStaticBox", "SpawnStaticSphere",
    "SpawnStaticCapsule", "SpawnStaticCylinder",
    "SpawnStaticBoxR", "SpawnStaticSphereR",
    "SpawnStaticCapsuleR", "SpawnStaticCylinderR",
    "SpawnKinematicBox", "SpawnKinematicSphere",
    "SpawnKinematicCapsule", "SpawnKinematicCylinder",
    "SpawnKinematicBoxR",
    "SpawnSensorBox", "SpawnSensorSphere",
    # Pool operations
    "CreatePool", "PoolAcquire", "PoolReturn",
    "PoolCullBelow", "PoolFree", "PoolTotal",
    # Physics manipulation
    "ApplyImpulse", "ApplyAngularImpulse",
    "SetLinearVelocity", "AddLinearVelocity",
    "DestroyBody", "GetVelocity",
    "SetVelocity", "MoveKinematic", "IsSensorTriggered",
    "IsActive",
    "GetPosition", "SetPosition", "GetRotation",
    "SetFriction", "SetRestitution",
    "SetGravityFactor", "SetMass",
    "SetLinearDamping", "SetAngularDamping",
    # Callback registration
    "OnStep", "OnCollision", "OnSensorEnter", "OnSensorExit",
    # Query
    "RayCast", "OverlapSphere", "OverlapBox",
})

_LUA_MIDWAYINPUT = frozenset({"IsActionDown", "IsKeyDown"})
_LUA_ENGINE = frozenset({
    "AwardTickets", "AwardTokens", "GetTickets", "GetTokens", "GetStreak",
})

#: Standalone globals provided by the engine bridge — these must NEVER be
#: prefixed, even though they look like bare calls.
_LUA_GLOBALS = frozenset({"SpawnSharedBooth"})

#: Hallucinated / misspelled namespaces the model is known to emit, mapped to
#: the canonical namespace.  ``Engineer.AwardTickets`` -> ``Engine.AwardTickets``.
_LUA_ALIASES: Dict[str, str] = {
    "Physics": "MidwayPhysics",
    "Engineer": "Engine",
}

#: Known-hallucinated calls.  ``None`` = neutralize/comment (not a real API);
#: a string = rewrite to that canonical ``Namespace.Symbol``.
_LUA_PHANTOMS: Dict[str, Optional[str]] = {
    "MidwayPhysics.BodyGetAabb": None,
    "MidwayPhysics.SpawnDynamicBall": "MidwayPhysics.SpawnDynamicSphere",
    "MidwayPhysics.RemoveBody": "MidwayPhysics.DestroyBody",
}

# ─────────────────────────────────────────────────────────────────────────────
# The registry
# ─────────────────────────────────────────────────────────────────────────────
REGISTRY: Dict[str, Dict[str, object]] = {
    "lua": {
        "namespaces": {
            "MidwayPhysics": _LUA_MIDWAYPHYSICS,
            "MidwayInput": _LUA_MIDWAYINPUT,
            "Engine": _LUA_ENGINE,
        },
        "globals": _LUA_GLOBALS,
        "aliases": _LUA_ALIASES,
        "phantoms": _LUA_PHANTOMS,
    },
    # Future languages slot in here with the same shape, e.g.
    # "python": {
    #     "namespaces": {"torch": frozenset({"tensor", "nn"}), ...},
    #     "globals": frozenset({...}),
    #     "aliases": {"np": "numpy"},
    #     "phantoms": {...},
    # },
}


def supported_languages() -> tuple[str, ...]:
    """All languages the registry knows about."""
    return tuple(sorted(REGISTRY))


def _entry(language: str, key: str) -> dict:
    lang = REGISTRY.get((language or "lua").lower())
    if lang is None:
        return {}
    value = lang.get(key)
    return value if isinstance(value, dict) else {}


def get_namespaces(language: str = "lua") -> Dict[str, FrozenSet[str]]:
    """Return ``{Namespace: frozenset(Symbols)}`` for a language."""
    namespaces = _entry(language, "namespaces")
    return {
        ns: frozenset(symbols)
        for ns, symbols in namespaces.items()
    }


def get_globals(language: str = "lua") -> FrozenSet[str]:
    """Symbols that are standalone globals and must NOT be prefixed."""
    lang = REGISTRY.get((language or "lua").lower())
    if lang is None:
        return frozenset()
    return frozenset(lang.get("globals", frozenset()))


def get_aliases(language: str = "lua") -> Dict[str, str]:
    """Return ``{wrong_namespace: canonical_namespace}``."""
    return dict(_entry(language, "aliases"))


def get_phantoms(language: str = "lua") -> Dict[str, Optional[str]]:
    """Return ``{phantom_call: canonical_or_None}``."""
    return dict(_entry(language, "phantoms"))


def bare_name_to_qualified(language: str = "lua") -> Dict[str, str]:
    """Return ``{BareSymbol: QualifiedName}`` for the fixer + datasets.

    A bare symbol resolves to the namespace it is first declared under.
    Symbols listed in ``globals`` are deliberately omitted (they must not be
    prefixed).  Namespace order is preserved from the registry dict.
    """
    out: Dict[str, str] = {}
    globals_ = get_globals(language)
    for ns, symbols in get_namespaces(language).items():
        for sym in symbols:
            if sym in globals_:
                continue
            out.setdefault(sym, f"{ns}.{sym}")
    return out


def qualified_to_namespace(language: str = "lua") -> Dict[str, str]:
    """Return ``{lowercase 'ns.sym': 'Ns.Sym'}`` for phantom/approval checks."""
    out: Dict[str, str] = {}
    for ns, symbols in get_namespaces(language).items():
        for sym in symbols:
            out[f"{ns.lower()}.{sym.lower()}"] = f"{ns}.{sym}"
    return out
