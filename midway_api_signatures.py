"""
midway_api_signatures.py -- Single source of truth for Midway Lua API arity.

Both _preflight_static (Static Guard / Fix G) and runtime_sim import their
argument-count tables from here so they can never drift apart again.  Keep in
sync with docs/engine_lua_bridge_contract.md.
"""

from __future__ import annotations

from typing import Dict, Tuple

# name -> (min_args, max_args)
SPAWN_ARITY: Dict[str, Tuple[int, int]] = {
    "SpawnDynamicSphere":    (4, 5),   # lx ly lz r [mass]
    "SpawnDynamicBox":       (6, 7),   # lx ly lz w h d [mass]
    "SpawnDynamicCapsule":   (5, 6),   # lx ly lz halfH r [mass]
    "SpawnDynamicCylinder":  (5, 6),   # lx ly lz halfH r [mass]
    "SpawnDynamicMesh":      (6, 6),   # lx ly lz yaw mass path
    "SpawnDynamicBoxR":      (7, 8),   # lx ly lz w h d mass [yawDeg]
    "SpawnDynamicSphereR":   (5, 6),   # lx ly lz r mass [yawDeg]
    "SpawnDynamicCapsuleR":  (6, 7),
    "SpawnDynamicCylinderR": (6, 7),
    "SpawnStaticBox":        (6, 6),   # lx ly lz w h d
    "SpawnStaticSphere":     (4, 4),   # lx ly lz r
    "SpawnStaticCapsule":    (5, 5),   # lx ly lz halfH r
    "SpawnStaticCylinder":   (5, 5),   # lx ly lz halfH r
    "SpawnStaticMesh":       (4, 8),   # lx ly lz yaw path [sx sy sz]
    "SpawnStaticBoxR":       (7, 7),
    "SpawnStaticSphereR":    (5, 5),
    "SpawnStaticCapsuleR":   (6, 6),
    "SpawnStaticCylinderR":  (6, 6),
    "SpawnKinematicBox":     (6, 6),
    "SpawnKinematicSphere":  (4, 4),
    "SpawnKinematicCapsule": (5, 5),
    "SpawnKinematicCylinder": (5, 5),
    "SpawnKinematicBoxR":    (7, 7),
    "SpawnSensorBox":        (6, 6),
    "SpawnSensorSphere":     (4, 4),
}

# name -> (min_args, max_args)
BODY_ARITY: Dict[str, Tuple[int, int]] = {
    "MoveKinematic":       (5, 5),
    "GetPosition":         (1, 1),
    "GetVelocity":         (1, 1),
    "GetRotation":         (1, 1),
    "IsActive":            (1, 1),
    "IsSensorTriggered":   (1, 1),
    "SetLinearVelocity":   (4, 4),
    "AddLinearVelocity":   (4, 4),
    "ApplyImpulse":        (4, 4),
    "ApplyAngularImpulse": (4, 4),
    "SetFriction":         (2, 2),
    "SetRestitution":      (2, 2),
    "SetGravityFactor":    (2, 2),
    "SetMass":             (2, 2),
    "SetLinearDamping":    (2, 2),
    "SetAngularDamping":   (2, 2),
    "CreatePool":          (3, 4),
    "PoolAcquire":         (4, 4),
    "PoolReturn":          (2, 2),
    "PoolCullBelow":       (2, 2),
    "PoolFree":            (1, 1),
    "PoolTotal":           (1, 1),
    "DestroyBody":         (1, 1),
    "OnStep":              (1, 1),
}

# name -> (min_args, max_args)
ECONOMY_ARITY: Dict[str, Tuple[int, int]] = {
    "AwardTickets": (1, 2),
    "AwardTokens":  (1, 2),
    "GetTickets":   (0, 0),
    "GetTokens":    (0, 0),
    "GetStreak":    (0, 0),
}
