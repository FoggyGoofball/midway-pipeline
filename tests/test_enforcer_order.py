"""
test_enforcer_order.py — Phase 2a preparation: lock the enforcer manifest and
the critical fix orderings so a future re-organization can't silently change
behavior.

These tests do NOT require a fixture corpus — they introspect the live
`post_process_lua` source to verify the ordering dependencies, and validate the
`_enforcers` manifest against the real functions.
"""

import inspect
import re

import _post_process_lua as _pp
from _enforcers import (
    ABSENT_FIX_NUMBERS,
    ENFORCER_BUCKETS,
    MAX_FIX_NUMBER,
    all_fix_numbers,
    bucket_of,
    function_names,
)


def _ordered_calls() -> list:
    """Extract the ordered fixer function names from the ``_FIX_SEQUENCE``
    manifest — the single source of truth after the ``post_process_lua``
    refactor consolidated the ordered calls into ``_run_fix_sequence``."""
    return [fn.__name__ for _label, _invariant, fn in _pp._FIX_SEQUENCE]


class TestManifestCoverage:
    def test_no_gaps_or_duplicates_except_absent(self):
        nums = all_fix_numbers()
        expected = sorted(set(range(1, MAX_FIX_NUMBER + 1)) - set(ABSENT_FIX_NUMBERS))
        assert nums == expected, f"mapped={nums} expected={expected}"

    def test_no_fix_30(self):
        # Fix #30 never existed; keep this as a canary for future authors.
        assert 30 in ABSENT_FIX_NUMBERS

    def test_every_function_exists(self):
        for fn in function_names():
            assert callable(getattr(_pp, fn, None)), f"missing function {fn}"

    def test_bucket_lookup(self):
        assert bucket_of(3) == "sanitize"
        assert bucket_of(27) == "structure"
        assert bucket_of(6) == "contract"
        assert bucket_of(1) == "lifecycle"
        assert bucket_of(19) == "scope"
        assert bucket_of(9) == "semantic"

    def test_six_buckets(self):
        assert set(ENFORCER_BUCKETS) == {
            "sanitize", "structure", "contract", "lifecycle", "scope", "semantic",
        }


class TestCriticalOrderings:
    def _calls(self):
        return _ordered_calls()

    def test_prefix_before_phantom_strip(self):
        c = self._calls()
        assert c.index("_add_midwayphysics_prefix") < c.index("_strip_phantom_api_calls")

    def test_phantom_strip_before_engine_strip(self):
        c = self._calls()
        assert c.index("_strip_phantom_api_calls") < c.index("_strip_phantom_engine_calls")

    def test_dedupe_before_orphaned_else(self):
        c = self._calls()
        assert c.index("_dedupe_onstep_registrations") < c.index("_neutralize_orphaned_else")

    def test_orphaned_else_before_final_structure_pass(self):
        c = self._calls()
        first = c.index("_neutralize_orphaned_else")
        # the 2nd _repair_lua_structure pass must come AFTER _neutralize_orphaned_else
        second_struct = len(c) - 1 - c[::-1].index("_repair_lua_structure")
        assert first < second_struct

    def test_structure_runs_early_and_late(self):
        c = self._calls()
        assert c.count("_repair_lua_structure") >= 2

    def test_bare_expression_before_conditional_injections(self):
        # #15 is the last unconditional transform; the lifecycle injections
        # (#4 OnLoadStatic / #5 SLOT_ID / #10 handles) are grouped into the
        # `_apply_lifecycle_invariants` composite and must run AFTER it.
        c = self._calls()
        assert c.index("_repair_bare_expression_statements") < c.index("_apply_lifecycle_invariants")
