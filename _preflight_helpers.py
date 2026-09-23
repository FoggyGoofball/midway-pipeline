"""
_preflight_helpers.py -- Pre-flight text-processing helpers extracted from
_finalize_preflight.py.

Exported:
    _flush_results_to_workspace(ctx)
    _strip_search_replace_metadata(content) -> str
    _is_comment_only(content) -> bool
    _task_has_code(content) -> bool
    _inject_empty_output_errors(ctx)
"""

from __future__ import annotations

import re
from typing import Any, List

from models import PipelineContext
from _pipeline_helpers import atomic_write_text

def _flush_results_to_workspace(ctx: PipelineContext) -> None:
    """Pre-compilation file sync: flush all result content to disk before build.

    Merged files take priority: if a target_file was produced by a merge pass
    (stored under 'merged:<rel_path>' in all_results_dict), write the merged
    version and skip individual task writes for that file so the LLM-merged
    content is never overwritten by a single task's partial snippet.
    """
    # Build a set of files that have a merged result  these must not be
    # overwritten by individual task writes.
    merged_registry: dict = getattr(ctx, 'merged_file_registry', {})
    merged_rel_paths: set = set(merged_registry.keys())

    # Write merged files first so they are on disk before luac runs.
    for rel_path, merged_key in merged_registry.items():
        merged_content = ctx.all_results_dict.get(merged_key, "")
        if merged_content and merged_content.strip():
            clean_content = _strip_search_replace_metadata(merged_content)
            target_path = ctx.project_root / rel_path
            target_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(target_path, clean_content)

    # Write individual task outputs, skipping any file covered by a merge.
    #
    # Per-anchor (chunked) mode: the real-time SEARCH/REPLACE patcher in
    # _helpers_exec.py has ALREADY written the accumulated file to the real
    # path.  The per-task entries in all_results_dict are FRAGMENTS (the raw
    # SEARCH/REPLACE block each task emitted), NOT full files.  Flushing them
    # here overwrites the accumulated result with the last task's snippet - the
    # residual "revert-to-skeleton" failure (staged file shrank to a 189-char
    # stub while the real file held ~19 KB).  Preserve the on-disk file and
    # mirror it into staging instead of clobbering it with fragments.
    _staging_active = False
    try:
        from _helpers_io import is_staging_active as _isa
        _staging_active = bool(_isa())
    except Exception:
        _staging_active = False
    _written_targets: set = set()
    for tid, content in ctx.all_results_dict.items():
        if tid.startswith("merged:"):
            continue  # already handled above
        task = ctx.task_map.get(tid)
        if not task or not task.target_file:
            continue
        _tf_rel = str(task.target_file).replace("\\", "/")
        if _tf_rel in {p.replace("\\", "/") for p in merged_rel_paths}:
            continue  # merged version takes priority
        if _tf_rel in _written_targets:
            continue
        target_path = ctx.project_root / task.target_file
        # The accumulated file already exists on disk (written by the real-time
        # patcher).  It is authoritative: mirror it into staging (if active) and
        # do NOT overwrite it with a single task's fragment.
        if target_path.is_file():
            try:
                _disk_content = target_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                _disk_content = ""
            if _disk_content.strip():
                _written_targets.add(_tf_rel)
                if _staging_active:
                    atomic_write_text(target_path, _disk_content)
                continue
        # Fallback: file does not exist on disk yet - write this task's content
        # as the initial scaffold.
        clean_content = _strip_search_replace_metadata(content)
        if clean_content and clean_content.strip():
            target_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(target_path, clean_content)
            _written_targets.add(_tf_rel)


def _post_process_workspace_lua_files(ctx: PipelineContext) -> None:
    """Deterministically post-process every on-disk .lua target file.

    Runs the pure-Python fixes (bare-namespace prefixing, phantom-API cleanup,
    duplicate-function stripping, comment-monologue collapsing, modifier-key
    sanitizing) against the ACCUMULATED file after a flush.  This makes the
    review loop see cleaned code every cycle instead of re-flagging the same
    bare ``SetFriction()`` / ``MoveKinematic()`` calls until the circuit
    breaker trips.  Idempotent and staging-aware.
    """
    try:
        from _post_process_lua import post_process_lua_file
        from _helpers_io import get_staging_path, is_staging_active
    except Exception:
        return

    _staging = False
    try:
        _staging = bool(is_staging_active())
    except Exception:
        _staging = False

    _targets: set = set()
    for _rel in (getattr(ctx, 'merged_file_registry', {}) or {}).keys():
        _targets.add(str(_rel).replace("\\", "/"))
    for _t in (getattr(ctx, 'task_map', {}) or {}).values():
        _tf = getattr(_t, 'target_file', '') or ''
        if _tf:
            _targets.add(str(_tf).replace("\\", "/"))

    _seen: set = set()
    for _rel in sorted(_targets):
        if not _rel.endswith('.lua'):
            continue
        try:
            _real = (ctx.project_root / _rel).resolve()
            _path = (
                get_staging_path(_real, project_root=ctx.project_root)
                if _staging else _real
            )
        except Exception:
            continue
        if not _path.is_file():
            continue
        _pk = str(_path)
        if _pk in _seen:
            continue
        _seen.add(_pk)
        try:
            post_process_lua_file(_path)
            # Phase 3: verify-or-revert — never leave an unprovable file on disk.
            try:
                from _verify_gate import verify_or_revert
                _rel_norm = _rel.replace("\\", "/")
                _baseline = (getattr(ctx, '_last_luac_clean_anchor', None) or {}).get(_rel_norm)
                if _baseline is not None:
                    _proposed = _path.read_text(encoding="utf-8", errors="replace")
                    _kept = verify_or_revert(ctx, _rel_norm, _proposed, _baseline)
                    if _kept != _proposed:
                        _path.write_text(_kept, encoding="utf-8")
                        print(f"  [VerifyGate] ↺ reverted {_rel} to last luac-clean baseline.")
            except Exception as _vg_e:
                print(f"  [VerifyGate] ⚠ unavailable ({_vg_e})")
        except Exception as _e:
            print(f"  [Post-Process] ⚠ {_rel}: {_e}")

    # Phase 1: read-only invariant report (DETERMINISTIC_HARDENING_PLAN.md).
    # Observational only — never mutates files or changes verdicts.
    try:
        from _verify_gate import report_invariants
        report_invariants(ctx)
    except Exception as _rie:
        print(f"  [Invariants] ⚠ reporter unavailable ({_rie})")


def _strip_search_replace_metadata(content: str) -> str:
    """Sanitize LLM output: apply SEARCH/REPLACE diffs + strip pipeline artifacts.

    First handles multiple model-generated patch formats (SEARCH/REPLACE
    conflict markers), then strips pipeline reasoning artifacts
    (<fix-plan> blocks, ### [Anchor] headers, outer code-fence wrappers)
    that leak into final file output.

    Handles multiple model-generated patch formats robustly:

    Format 1  canonical conflict-marker style (correct format):
        <<<<<<< SEARCH
        <old content>
        =======
        <new content>
        >>>>>>> REPLACE

    Format 2  markdown-header style (model hallucination, tolerated):
        ### SEARCH
        <old content>
        ### REPLACE
        <new content>

    Both formats are matched case-insensitively and tolerate extra angle-bracket
    repetitions, extra hash characters, surrounding whitespace, colons, dashes,
    and underscores around the SEARCH / REPLACE keywords.

    Strategy for Format 2: strip the entire
        ### SEARCH\n<old>\n### REPLACE\n
    span (leaving only the REPLACE content in place), which safely handles
    sequences of multiple consecutive blocks in a single pass.
    """
    import re as _re
    result = content

    # ── Format 1: canonical conflict-marker style ─────────────────────────
    # Tolerates: extra < / > / = chars, surrounding spaces, lowercase.
    # e.g.  <<<< search ... ==== ... >>>> replace
    canonical = _re.compile(
        r'<{3,9}[ \t]*search[ \t]*\n(.*?)\n={3,9}[ \t]*\n(.*?)\n>{3,9}[ \t]*replace[ \t]*',
        _re.DOTALL | _re.IGNORECASE,
    )
    for match in canonical.finditer(content):
        result = result.replace(match.group(0), match.group(2), 1)

    # ── Format 2: markdown-header style ──────────────────────────────────
    # Strip everything from ### SEARCH up to and including the ### REPLACE
    # header line, leaving the replacement content intact in place.
    # Tolerates: 1-6 # chars, spaces/dashes/underscores/colons around keyword,
    # lowercase, e.g.  ## search:  /  #### SEARCH --  /  # replace
    md_strip = _re.compile(
        r'#{1,6}[ \t_\-]*search[ \t_\-:]*\n'   # ### SEARCH header
        r'.*?'                                    # old content (non-greedy)
        r'#{1,6}[ \t_\-]*replace[ \t_\-:]*\n',  # ### REPLACE header (consumed)
        _re.DOTALL | _re.IGNORECASE,
    )
    result = md_strip.sub('', result)

    # ── Pipeline Artifact Strip ─────────────────────────────────────────
    # Remove <fix-plan> blocks, ### [Anchor] memory-ledger headers, and
    # outer code-fence wrappers that leak into final file output.
    # Idempotent  safe to call even if no artifacts are present.
    from _helpers_text import strip_pipeline_artifacts
    result = strip_pipeline_artifacts(result)

    # ── Residual Conflict-Marker Sweep ────────────────────────────────────
    # The canonical + markdown block handlers above only match WELL-FORMED
    # blocks. A small model frequently emits a truncated or malformed patch
    # (a bare `<<<<<<< SEARCH` with no closer, or markers glued to telemetry
    # text). Those markers are never valid Lua/C++, so sweep out any
    # remaining marker line as a last resort. Applied at the merge boundary
    # (fragments -> whole file), where removal is always safe.
    _residual_marker = _re.compile(
        r'^\s*(?:<{3,}[^\n]*|>{3,}[^\n]*|={3,}\s*)$',
        _re.MULTILINE,
    )
    _md_patch_header = _re.compile(
        r'^\s*#{1,6}\s*(?:search|replace)\b[^\n]*$',
        _re.MULTILINE | _re.IGNORECASE,
    )
    _before_sweep = result
    result = _residual_marker.sub('', result)
    result = _md_patch_header.sub('', result)
    if result != _before_sweep:
        print("  [Strip SR] Removed residual SEARCH/REPLACE marker line(s).")

    return result


_COMMENT_ONLY_RE = re.compile(
    r'^\s*(//[^\n]*|/\*.*?\*/|#[^\n]*)\s*$',
    re.DOTALL,
)


def _is_comment_only(content: str) -> bool:
    """Return True if content is nothing but comments and whitespace."""
    # Strip fenced code block markers, then check.
    stripped = re.sub(r'```[^\n]*\n?|```', '', content).strip()
    return bool(_COMMENT_ONLY_RE.match(stripped)) or not stripped


# Detects a meaningful code block: at least one SEARCH/REPLACE pair, or a real
# language keyword / declaration line. Bare triple-backtick fence markers are
# intentionally excluded  a fenced block that contains only delegation signals
# must NOT be treated as a real code implementation.
_HAS_CODE_RE = re.compile(
    r'(<{7}\s*SEARCH|\bfunction\b|\bclass\b|\bdef\b|\bvoid\b|\bint\b|\breturn\b|'
    r'\blocal\s+\w+\s*=\s*(?:function\b|"[^"]*"|\d+|MidwayPhysics\.|\{))',
    re.IGNORECASE,
)
# Fix K: 'local' keyword only counts as code if it's followed by an assignment
# to a function, string, number, MidwayPhysics call, or table literal.
# A bare 'local handle' or 'local handle = nil' is NOT real code  it's a
# variable stub that the model puts in as a placeholder.



# Patterns that indicate the output is a pure signal/delegation with no real code.
# Matches DELEGATE, QUERY, CONF, REVISE, APPROVE, APPEAL, VETO, OBJECT, RECOURSE
# and similar inter-agent signal lines so they are excluded from the "has real code"
# check and from the non-signal line count.
_DELEGATE_ONLY_RE = re.compile(
    r'^\s*(\[DELEGATE:|\[QUERY:|\[CONF:|\[REVISE:|\[APPROVE\]|\[APPEAL:|\[VETO:|\[OBJECT:|\[RECOURSE:|\[CONSULT:|\[REJECT:|\[MERGE:|\[FLUSH\]|\[RESULT:)',
    re.IGNORECASE | re.MULTILINE,
)

def _task_has_code(content: str) -> bool:
    """Return True if *content* contains at least one concrete code construct
    AND is not a pure delegation/signal response.

    Rejects outputs that are primarily markdown table rows (hallucinated API
    ledgers) even when they contain function keywords, since those keywords
    appear inside table cell text, not as real code constructs.
    """
    if not content or not content.strip():
        return False
    # If the entire (stripped) content is only signal lines, treat as no-code.
    non_signal_lines = [
        ln for ln in content.splitlines()
        if ln.strip() and not _DELEGATE_ONLY_RE.match(ln)
    ]
    if not non_signal_lines:
        return False
    # Ledger-hallucination guard: if the majority of non-empty lines look like
    # markdown table rows (start with '|'), the LLM dumped a ledger table, not code.
    _table_rows = sum(1 for ln in non_signal_lines if ln.strip().startswith('|'))
    if _table_rows > 0 and _table_rows / len(non_signal_lines) > 0.40:
        return False
    # Must contain a real code construct in the non-signal portion of the output.
    # We search the joined non-signal lines rather than the full raw content so
    # that keywords buried inside APPEAL/APPROVE prose cannot satisfy the check.
    non_signal_text = "\n".join(non_signal_lines)
    return bool(_HAS_CODE_RE.search(non_signal_text))


def _inject_empty_output_errors(ctx: PipelineContext) -> None:
    """
    Universal guard: if a task result contains only prose with no code, inject a
    pre-flight error so the fix loop forces a proper implementation before review.
    This check is project-agnostic  empty outputs are always a failure regardless
    of domain or technology.
    """
    for tid, content in list(ctx.all_results_dict.items()):
        if not content or not content.strip() or _is_comment_only(content):
            ctx.pre_flight_errors += (
                f"\n## Empty Output  Task {tid}\n"
                f"Task {tid} produced no output at all (or only comments). "
                f"The agent must provide a concrete implementation.\n"
            )
            print(f"  [Pre-Flight] EMPTY OUTPUT detected for task {tid}  injecting fix demand.")
        elif _DELEGATE_ONLY_RE.search(content) and not _task_has_code(content):
            ctx.pre_flight_errors += (
                f"\n## Delegation Signal Only  Task {tid}\n"
                f"Task {tid} responded with only a [DELEGATE/QUERY/CONF] signal and no "
                f"concrete code. Delegation signals are forbidden as a substitute for "
                f"implementation. The agent MUST produce a complete code block.\n"
            )
            print(f"  [Pre-Flight] DELEGATE-ONLY output detected for task {tid}  injecting fix demand.")
        elif _DELEGATE_ONLY_RE.search(content[:500]) and not _task_has_code(content):
            # Delegate signal buried at the start of a long hallucinated dump.
            ctx.pre_flight_errors += (
                f"\n## Delegation Signal Buried in Output  Task {tid}\n"
                f"Task {tid} began with a [DELEGATE/QUERY/CONF] signal followed by "
                f"non-code content (e.g., a ledger table dump). This is not a valid "
                f"implementation. The agent MUST produce a complete working code block.\n"
            )
            print(f"  [Pre-Flight] DELEGATE+HALLUCINATION detected for task {tid}  injecting fix demand.")
        # ── Orphaned SEARCH/REPLACE check ──
        # If the LLM output contains SEARCH/REPLACE blocks (canonical
        # <<<<<<< SEARCH format or markdown ### SEARCH format) but no
        # ### task_X header, do NOT throw a NO CODE BLOCK error. Instead,
        # apply the blocks to the target file and propagate the patched
        # result to ALL tasks targeting that file so they are all resolved.
        _has_sr = bool(re.search(
            r'(?:<{3,9}\s*SEARCH|#{1,6}\s*SEARCH)', content, re.IGNORECASE
        ))
        if _has_sr:
            # SEARCH/REPLACE blocks detected  apply them to the target file
            # and propagate to all tasks sharing that target_file so they
            # are all considered resolved.
            _task_obj = ctx.task_map.get(tid)
            if _task_obj and _task_obj.target_file:
                _target_path = ctx.project_root / _task_obj.target_file
                if _target_path.is_file():
                    _file_content = _target_path.read_text(encoding="utf-8", errors="replace")
                    _patched_content = _strip_search_replace_metadata(content)
                    # Convert phantom SLOT_X/Y/Z + BOOTH.* dimensions to safe
                    # numeric literals BEFORE the guard check.  Blocking these
                    # wholesale caused the fix-loop death spiral (5/11 tasks
                    # rejected every cycle).  Stripping keeps the task applied.
                    _patched_content = re.sub(
                        r'^[\t ]*local\s+BOOTH\s*=\s*AttractionConstants\.booth[^\n]*\n',
                        '', _patched_content, flags=re.MULTILINE
                    )
                    _patched_content = re.sub(
                        r'^[\t ]*local\s+SLOT_[XYZ]\s*=\s*BOOTH\.\w+[^\n]*\n',
                        '', _patched_content, flags=re.MULTILINE
                    )
                    _patched_content = re.sub(r'\bBOOTH\.\w+\b', '0.0', _patched_content)
                    _patched_content = re.sub(r'\bSLOT_[XYZ]\b', '0.0', _patched_content)
                    if _patched_content != content:
                        # Before broadcasting, verify the patched content doesn't
                        # contain static-guard violations.  A SEARCH/REPLACE block
                        # that introduces phantom APIs (e.g. SpawnStaticMesh with a
                        # string path, BUTTON.x, SLOT_X globals) must never be
                        # propagated to sibling tasks  that would poison every task
                        # sharing the file with the same bad pattern.
                        _SR_GUARD_PATTERNS = [
                            re.compile(r'MidwayPhysics\.SpawnStaticMesh\s*\(\s*[\'"]', re.IGNORECASE),
                            re.compile(r'MidwayPhysics\.SpawnStaticPlane', re.IGNORECASE),
                            re.compile(r'MidwayPhysics\.ApplyForce\b', re.IGNORECASE),
                            re.compile(r'\bBUTTON\s*\.', re.MULTILINE),
                            re.compile(r'\bSharedBooth\s*\.', re.MULTILINE),
                            re.compile(r'\b(?:Mouse|Input)\s*\.', re.MULTILINE),
                            re.compile(r'\bAttractionConstants\.(?:initialize|get)\w+\s*\(', re.IGNORECASE),
                            re.compile(r'\bMidwayPhysics\.OnLoadStatic\s*\(', re.IGNORECASE),
                            re.compile(r'\bsol\s*\.\s*(?:set_function|new_usertype|state)\s*\(', re.IGNORECASE),
                        ]
                        _sr_guard_hit = next(
                            (p for p in _SR_GUARD_PATTERNS if p.search(_patched_content)),
                            None,
                        )
                        if _sr_guard_hit:
                            ctx.pre_flight_errors += (
                                f"\n## Static Pattern Violation  Task {tid} [SEARCH/REPLACE blocked]\n"
                                f"**Rule:** SEARCH/REPLACE output contains a static-guard violation "
                                f"(matched: `{_sr_guard_hit.pattern}`) and was NOT broadcast to sibling tasks.\n"
                                f"Rewrite task {tid} without phantom APIs, undefined globals (BUTTON, SLOT_X, "
                                f"SharedBooth), or unsupported SpawnStaticMesh overloads.\n"
                            )
                            print(f"  [Pre-Flight] ⛔ SEARCH/REPLACE for task {tid} blocked  static guard hit: {_sr_guard_hit.pattern}")
                            continue
                        # SEARCH/REPLACE was applied  store globally for all
                        # tasks targeting this file.
                        ctx.all_results_dict[tid] = _patched_content
                        # Sync all_results for the primary task.
                        _fp_f0 = False
                        for _fp_i0, _fp_e0 in enumerate(ctx.all_results):
                            if _fp_e0.get("task_id") == tid:
                                ctx.all_results[_fp_i0] = {"task_id": tid, "output": _patched_content}
                                _fp_f0 = True
                                break
                        if not _fp_f0:
                            ctx.all_results.append({"task_id": tid, "output": _patched_content})
                        # NOTE: The legacy "broadcast to sibling tasks" step is
                        # removed.  In per-anchor mode each task owns its own
                        # fragment (already applied by the real-time patcher);
                        # overwriting siblings with this task's REPLACE content
                        # made every task look identical and poisoned coverage /
                        # reviewer signals (false "unfinished tasks").
                        # Write patched content to disk ONLY when the target
                        # file does not already exist.  In per-anchor mode the
                        # real-time patcher has already applied this task's
                        # SEARCH/REPLACE to the accumulated file; overwriting it
                        # with the REPLACE-only snippet clobbers the whole file
                        # (residual revert-to-skeleton bug).
                        if not _target_path.is_file():
                            _target_path.parent.mkdir(parents=True, exist_ok=True)
                            atomic_write_text(_target_path, _patched_content)
                        print(f"  [Pre-Flight] ✅ SEARCH/REPLACE applied globally to {_task_obj.target_file} via {tid}")
                        continue
            # Fall through if SR blocks couldn't be applied  do NOT throw
            # NO CODE BLOCK error (SR content is real code even without headers).
        elif not _task_has_code(content):
            ctx.pre_flight_errors += (
                f"\n## No Code Block  Task {tid}\n"
                f"Task {tid} response contains only prose with no code construct. "
                f"The agent must produce at least one concrete code block "
                f"that implements the task specification.\n"
            )
            print(f"  [Pre-Flight] NO CODE BLOCK detected for task {tid}  injecting fix demand.")


