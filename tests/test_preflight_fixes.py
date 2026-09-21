"""
test_preflight_fixes.py — Unit tests for the two convergence fixes:

  1. `_fix_spawn_arities_in_text` (Fix G2) extended to CreatePool / pool calls,
     truncating over-arg calls to MAX arity so optional args survive.
  2. `_resolve_anchor_owner` line-aware luac error attribution, so whole-file
     syntax errors are routed to the task whose anchor owns the failing line.
"""

import pytest

from _preflight_static import _fix_spawn_arities_in_text
from _finalize_preflight import _resolve_anchor_owner, _ANCHOR_TOKEN_RE


# ---------------------------------------------------------------------------
# Fix G2: CreatePool / pool arity repair
# ---------------------------------------------------------------------------

class TestFixSpawnArities:
    def test_createpool_overarg_truncates_to_max_keeps_table(self):
        src = (
            'local p = MidwayPhysics.CreatePool("puck_pool", 2, 2, '
            '{shape="sphere", radius=0.3}, 9, 9, 9, 9, 9)'
        )
        fixed, n = _fix_spawn_arities_in_text(src)
        assert n == 1
        # 4 args kept: name, hotN, coldN, paramsTable
        assert 'MidwayPhysics.CreatePool("puck_pool", 2, 2, {shape="sphere", radius=0.3})' in fixed

    def test_spawnstaticbox_overarg_truncates(self):
        src = 'MidwayPhysics.SpawnStaticBox(0, 0, 0, 1, 1, 1, 99, 99, 99)'
        fixed, n = _fix_spawn_arities_in_text(src)
        assert n == 1
        assert 'MidwayPhysics.SpawnStaticBox(0, 0, 0, 1, 1, 1)' in fixed

    def test_in_range_createpool_unchanged(self):
        src = 'MidwayPhysics.CreatePool("p", 2, 2, {shape="sphere"})'
        fixed, n = _fix_spawn_arities_in_text(src)
        assert n == 0
        assert fixed == src

    def test_pool_acquire_overarg(self):
        src = 'MidwayPhysics.PoolAcquire("puck_pool", 0, 1, 2, "extra")'
        fixed, n = _fix_spawn_arities_in_text(src)
        assert n == 1
        assert 'MidwayPhysics.PoolAcquire("puck_pool", 0, 1, 2)' in fixed

    def test_spawn_handle_first_drops_handle(self):
        """SpawnStaticBox(tower_base, ...) — Spawn* returns a handle, never takes one."""
        src = 'MidwayPhysics.SpawnStaticBox(tower_base, 0, 0, 0, 10, 10, 10)'
        fixed, n = _fix_spawn_arities_in_text(src)
        assert n == 1
        assert 'MidwayPhysics.SpawnStaticBox(0, 0, 0, 10, 10, 10)' in fixed
        assert 'tower_base' not in fixed

    def test_spawn_capsule_handle_first_drops_handle(self):
        src = 'MidwayPhysics.SpawnStaticCapsule(platform, 0, 8, 0, 1.5, 0.3)'
        fixed, n = _fix_spawn_arities_in_text(src)
        assert n == 1
        assert 'MidwayPhysics.SpawnStaticCapsule(0, 8, 0, 1.5, 0.3)' in fixed

    def test_spawn_numeric_first_still_truncates_last(self):
        """A numeric first arg is a real lx — keep it and drop the trailing excess."""
        src = 'MidwayPhysics.SpawnStaticBox(0, 0, 0, 1, 1, 1, 99)'
        fixed, n = _fix_spawn_arities_in_text(src)
        assert n == 1
        assert 'MidwayPhysics.SpawnStaticBox(0, 0, 0, 1, 1, 1)' in fixed

    def test_applyimpulse_underarg_pads_to_4(self):
        """3-arg ApplyImpulse is missing iz — pad deterministically to 4 args."""
        src = 'MidwayPhysics.ApplyImpulse(bell_handle, 0, 1)'
        fixed, n = _fix_spawn_arities_in_text(src)
        assert n == 1
        assert 'MidwayPhysics.ApplyImpulse(bell_handle, 0, 1, 0)' in fixed

    def test_applyimpulse_bogus_numeric_first_still_pads(self):
        src = 'MidwayPhysics.ApplyImpulse(0, 0.5, 0)'
        fixed, n = _fix_spawn_arities_in_text(src)
        assert n == 1
        assert 'MidwayPhysics.ApplyImpulse(0, 0.5, 0, 0)' in fixed

    def test_spawn_underarg_not_padded(self):
        """Spawn* under-arg is NOT 0-safe — must remain untouched."""
        src = 'MidwayPhysics.SpawnStaticBox(0, 0, 0)'
        fixed, n = _fix_spawn_arities_in_text(src)
        assert n == 0
        assert fixed == src


# ---------------------------------------------------------------------------
# Line-aware anchor owner resolution
# ---------------------------------------------------------------------------

def _anchor_num_from(marker: str) -> str:
    m = _ANCHOR_TOKEN_RE.search(marker)
    return m.group(1) if m else ""


class TestResolveAnchorOwner:
    def test_owner_is_nearest_anchor_above(self, tmp_path):
        f = tmp_path / "strongman.lua"
        f.write_text(
            "-- [TASK_4_INSERT_HOOK] -- pool setup\n"
            "local p = CreatePool('x', 1, 1)\n"          # line 2 -> task 4
            "-- [TASK_7_INSERT_HOOK] -- scoring\n"
            "if broken( then\n"                           # line 4 -> task 7
            "end\n",
            encoding="utf-8",
        )
        anchor_map = {"4": "task_4", "7": "task_7"}
        assert _resolve_anchor_owner(f, 2, anchor_map) == "task_4"
        assert _resolve_anchor_owner(f, 4, anchor_map) == "task_7"
        assert _resolve_anchor_owner(f, 5, anchor_map) == "task_7"

    def test_before_first_anchor_returns_empty(self, tmp_path):
        f = tmp_path / "x.lua"
        f.write_text(
            "local broken = (\n"                        # line 1, before any anchor
            "-- [TASK_2_INSERT_HOOK] -- constants\n"
            "local X = {}\n",
            encoding="utf-8",
        )
        anchor_map = {"2": "task_2"}
        assert _resolve_anchor_owner(f, 1, anchor_map) == ""

    def test_missing_file_returns_empty(self, tmp_path):
        assert _resolve_anchor_owner(tmp_path / "nope.lua", 1, {"1": "task_1"}) == ""
