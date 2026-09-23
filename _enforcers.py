"""
_enforcers.py — the enforcer taxonomy for the deterministic post-processor.

Phase 2 preparation (see `docs/DETERMINISTIC_HARDENING_PLAN.md`): maps every
existing deterministic fix to one of six invariant-enforcing buckets. This
module is DATA ONLY — it is not wired into the pipeline yet.

The bucket grouping is the *target* of the Phase 2 re-organization. The current
flat order in `post_process_lua()` is intentionally preserved until output
parity is proven over a corpus (Phase 2b); Phase 2a only locks the manifest and
the critical orderings with tests.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

# bucket -> {"note": str, "fixes": [(fix_number, function_name), ...]}
ENFORCER_BUCKETS: Dict[str, dict] = {
    "sanitize": {
        "note": "strip non-code (prose, artifacts, markers) at the boundary",
        "fixes": [
            (3, "_strip_pipeline_artifacts"),
            (11, "_strip_comment_monologues"),
            (7, "search_exactly_once_gate"),  # gate, applied by callers
        ],
    },
    "structure": {
        "note": "balance blocks + neutralize structural residue",
        "fixes": [
            (13, "_repair_duplicate_underscore_locals"),
            (23, "_strip_local_in_tables"),
            (24, "_strip_broken_local_declarations"),
            (25, "_fix_json_colon_tables"),
            (26, "_strip_stray_closing_parens"),
            (27, "_repair_lua_structure"),
            (28, "_repair_orphaned_then"),
            (33, "_neutralize_orphaned_else"),
            (15, "_repair_bare_expression_statements"),
        ],
    },
    "contract": {
        "note": "namespace/phantom/domain idiom conformance",
        "fixes": [
            (6, "_add_midwayphysics_prefix"),
            (8, "_strip_phantom_api_calls"),
            (16, "_strip_engine_redefinitions"),
            (21, "_neutralize_method_calls"),
            (22, "_strip_phantom_engine_calls"),
            (29, "_neutralize_roblox"),
        ],
    },
    "lifecycle": {
        "note": "exactly-one hooks + injected invariants",
        "fixes": [
            (1, "_strip_duplicate_functions"),
            (4, "_inject_onload_static"),
            (5, "_inject_slot_id"),
            (34, "_inject_onunload"),
            (12, "_dedupe_onstep_registrations"),
            (17, "_dedupe_spawn_shared_booth"),
        ],
    },
    "scope": {
        "note": "locals/globals/handles keyed off the symbol table",
        "fixes": [
            (10, "_auto_declare_handles"),
            (19, "_auto_declare_read_before_write"),
            (20, "_localize_bare_assignments"),
            (32, "_hoist_cross_lifecycle_locals"),
        ],
    },
    "semantic": {
        "note": "modifier + pool canonicalization",
        "fixes": [
            (2, "_strip_module_level_mod"),
            (9, "_sanitize_modifier_keys"),
            (14, "_normalize_pool_name_arguments"),
            (18, "_align_createpool_names"),
            (31, "_repair_modifier_access"),
        ],
    },
}

# Fix #30 does not exist — it was never implemented. Keep this explicit so a
# future author who adds Fix #30 updates the manifest instead of silently
# leaving it unmapped.
ABSENT_FIX_NUMBERS = frozenset({30})

# Highest fix number in the manifest. Tests use this to assert there are no
# unmapped gaps.
MAX_FIX_NUMBER = 34


def all_fix_numbers() -> List[int]:
    """Return every mapped fix number (no duplicates)."""
    out: List[int] = []
    for _b in ENFORCER_BUCKETS.values():
        for _num, _fn in _b["fixes"]:
            if _num not in out:
                out.append(_num)
    return sorted(out)


def bucket_of(fix_number: int) -> str:
    for _name, _b in ENFORCER_BUCKETS.items():
        if any(_n == fix_number for _n, _f in _b["fixes"]):
            return _name
    raise KeyError(f"fix #{fix_number} is not mapped to any enforcer bucket")


def function_names() -> List[str]:
    """Return every mapped function name."""
    return [f for _b in ENFORCER_BUCKETS.values() for _n, f in _b["fixes"]]
