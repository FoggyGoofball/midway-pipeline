"""
_finalize_conflicts.py — [DEPRECATED] Shared-File Merge has been abolished.

Phase 5 conflict resolution is obsolete. Real-time SEARCH/REPLACE patching
in _helpers_exec.py applies each task's output immediately after execution
(Step 4 of the Skeleton → Search/Replace flow).

This module provides no-op stubs so downstream imports don't break. All
merge/conflict-resolution functions return ctx unchanged with a log line.
"""


def _run_conflict_resolution(ctx):
    """No-op: shared-file merge abolished in favor of real-time SEARCH/REPLACE.

    Logs "merge skipped (real-time patch mode)" and returns ctx unchanged.
    """
    print("  [Merge] SKIPPED — shared-file merge abolished (real-time patch mode).")
    ctx.output_parts.append(
        "\n### Phase 5: Conflict Resolution (SKIPPED — "
        "real-time SEARCH/REPLACE patching occurred per-task)\n"
    )
    return ctx


def merge_shared_file_outputs(shared_file_path, context, task_map):
    """No-op: returns empty string. Shared-file merge is obsolete.

    Kept only for reverse-compatibility with _finalize_review.py's
    lazy import pathway.
    """
    return ""
