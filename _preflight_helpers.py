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
from patch_regexes import SEARCH_REPLACE_PATTERN
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
    for tid, content in ctx.all_results_dict.items():
        if tid.startswith("merged:"):
            continue  # already handled above
        task = ctx.task_map.get(tid)
        if task and task.target_file:
            if str(task.target_file).replace("\\", "/") in {p.replace("\\", "/") for p in merged_rel_paths}:
                continue  # merged version takes priority
            target_path = ctx.project_root / task.target_file
            clean_content = _strip_search_replace_metadata(content)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(target_path, clean_content)


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
                            re.compile(r'\bSLOT_[XYZ]\b', re.MULTILINE),
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
                        for _otid, _otask in ctx.task_map.items():
                            if _otid != tid and _otask.target_file == _task_obj.target_file:
                                ctx.all_results_dict[_otid] = _patched_content
                                _fp_f1 = False
                                for _fp_i1, _fp_e1 in enumerate(ctx.all_results):
                                    if _fp_e1.get("task_id") == _otid:
                                        ctx.all_results[_fp_i1] = {"task_id": _otid, "output": _patched_content}
                                        _fp_f1 = True
                                        break
                                if not _fp_f1:
                                    ctx.all_results.append({"task_id": _otid, "output": _patched_content})
                        # Write patched content to disk
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


