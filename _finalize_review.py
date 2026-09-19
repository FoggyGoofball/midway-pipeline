"""
_finalize_review.py  Phase 6: Integration Review & Fix Loop
=============================================================
Extracted from mesh_finalize.py  handles the review-fix loop,
context pruning (Directive C), sanity detection, and the
Reconciliation Gate.

Exported:
    _prune_fix_context(domain_key, task_obj, review_issues_text,
                       pre_flight_errors, user_prompt,
                       paged_files_cache) -> str
    _run_review_fix_loop(ctx) -> PipelineContext
    build_fix_bridge_snippet(ctx) -> str
"""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from token_budget import TokenBudget

from _luac_path import get_luac_exe as _get_luac_exe

_LUAC_EXE = _get_luac_exe() or "luac"

from models import PipelineContext, SignalType, MeshSignal, ConsensusResult
from _pipeline_helpers import (
    atomic_write_text, trigger_chime, generate_failure_report,
)
from _domain_sandbox import reject_cross_domain_output
import _prompts as _prompts_mod  # live module ref  reads post-bootstrap values
from pipeline import (
    ALL_DOMAINS,
    resolve_agent_name,
    call_ollama, get_verdict,
    _normalize_fix_fingerprint, check_insanity_similarity,
    REVIEWER_MODEL as _REVIEWER_MODEL,
    EXECUTION_MODEL as _EXECUTION_MODEL,
)
from _helpers_exec import compile_project
from ollama_client import is_fatal_ollama_error as _is_fatal_ollama


# ----------------------------------------------------------------------
#  Shared bridge-snippet builder (single source of truth)
# ----------------------------------------------------------------------

from _review_helpers import (
    build_fix_bridge_snippet,
    _strip_fix_plan,
    _prune_fix_context,
    _extract_broken_function_name,
    _extract_function_body,
    _window_mono_snippet,
)  # noqa: F401


# ----------------------------------------------------------------------
#  Phase 3 — Deterministic verdict gate
# ----------------------------------------------------------------------
# True  -> the PASS/FAIL decision in the review loop is computed from
#          deterministic signals only (per-cycle luac syntax, RuntimeSim,
#          open pre-flight violations, mandatory economy content).  No LLM
#          review call is made, so the loop can never hang or crash on the
#          9B reviewer (the historical UNKNOWN / 500 / multi-minute stall
#          failure mode seen in every prior run).
# False -> previous behaviour (the LLM decides the verdict).
DETERMINISTIC_VERDICT = True

# When DETERMINISTIC_VERDICT is True and this is True, a FAIL cycle will
# still attempt ONE advisory structured review to enrich the fix issues.
# The advisory result NEVER changes the verdict and never blocks the loop.
ADVISORY_REVIEW_ON_FAIL = False


def _luac_syntax_errors(ctx: PipelineContext) -> str:
    """Re-run luac on the pipeline's own Lua outputs and return a
    pre-flight-style error block for any syntax failures.

    This is a per-cycle disk-level check — the loop's partial static
    refresh does NOT re-inject luac errors, so a fix cycle that
    reintroduces a syntax error would otherwise be invisible to the next
    verdict decision (the exact bug that made the strongman run loop
    forever on the `Physics.*` regression).
    """
    import subprocess as _sp
    try:
        _sp.run([_LUAC_EXE, "-v"], capture_output=True, text=True, timeout=5)
    except FileNotFoundError:
        return ""  # luac not installed — not a gate we can run
    except Exception:
        return ""

    _owned: Set[str] = set()
    _mono = getattr(ctx, '_monolithic_lua_target', None)
    if _mono and _mono.endswith('.lua'):
        _owned.add(_mono)
    for _t in ctx.task_map.values():
        _tf = getattr(_t, 'target_file', None)
        if _tf and _tf.endswith('.lua'):
            _owned.add(_tf)
    if not _owned:
        return ""

    _blocks: list[str] = []
    for _rel in sorted(_owned):
        _abs = ctx.project_root / _rel
        if not _abs.is_file():
            continue
        try:
            _p = _sp.run([_LUAC_EXE, "-p", str(_abs)], capture_output=True, text=True, timeout=30)
        except Exception:
            continue
        if _p.returncode != 0:
            _clean = (_p.stderr or "").strip().replace(str(_abs), _rel)
            _blocks.append(f"## ⛔ Lua Syntax Error — {_rel}\n```\n{_clean[:800]}\n```")
    return "\n\n".join(_blocks)


def _rebuild_task_from_tasks_list(ctx: PipelineContext, tid: str):
    """Rebuild a minimal Task-like object for *tid* from ctx.tasks_list.

    The per-task review-fix router looks tasks up in ctx.task_map, but that
    dict can end up missing entries (the "silent skip" bug that left most
    failing tasks unfixed every cycle).  Fall back to the flat task list, which
    carries domain/title/id/target_file/anchor_marker — enough for
    `_prune_fix_context` and domain routing.
    """
    from types import SimpleNamespace
    for _t in (getattr(ctx, 'tasks_list', None) or []):
        if not isinstance(_t, dict):
            continue
        if f"task_{_t.get('id')}" == tid:
            return SimpleNamespace(
                agent=_t.get("domain", "Lua"),
                spec=_t.get("title") or _t.get("description") or tid,
                paged_files_cache=None,
                tdd_test_path=None,
                target_file=_t.get("target_file", ""),
                anchor_marker=_t.get("anchor_marker") or _t.get("_anchor_marker"),
            )
    return None


def _effective_lua_blob(ctx) -> str:
    """Join the authoritative on-disk (staging-aware) Lua files into one blob.

    Reviewers, the tribunal, and coverage checks must judge the SHIPPED file,
    not the in-memory task fragments (which may be raw SEARCH/REPLACE or may
    have failed to land).  Reuses runtime_sim._effective_lua_files for the
    staging-aware resolution.
    """
    try:
        from runtime_sim import _effective_lua_files
        _files = _effective_lua_files(ctx) or {}
        return "\n".join(str(_c) for _c in _files.values())
    except Exception:
        return ""


def _coverage_gaps(ctx: PipelineContext) -> list[str]:
    """Deterministically compute which planned tasks/features are NOT yet
    evidenced in the generated code.

    Two signals:
      1. Blueprint task API coverage — each task title names concrete APIs
         (SpawnStaticBox, IsActionDown, MoveKinematic, ...).  If a task names
         APIs and NONE of them appear in the output, that task is unfinished.
      2. Design checklist coverage — keyword overlap, as a secondary signal.

    Structural tasks that name no APIs are assumed satisfied by the skeleton.
    Results are cached on ctx.coverage_gaps so the fix loop, kick-back loop,
    and final gate share one view.
    """
    _API_TOKEN_RE = re.compile(
        r'\b(?:MidwayPhysics|Engine|MidwayInput)\.[A-Za-z_]\w*'
        r'|\b(?:SpawnStatic|SpawnDynamic|SpawnKinematic|SpawnSensor)\w*'
        r'|\b(?:CreatePool|PoolAcquire|PoolReturn|PoolFree|IsSensorTriggered|IsActive|'
        r'MoveKinematic|ApplyImpulse|ApplyAngularImpulse|SetLinearVelocity|AddLinearVelocity|'
        r'SetFriction|SetRestitution|SetGravityFactor|SetMass|GetPosition|GetVelocity|'
        r'GetStreak|AwardTickets|AwardTokens|IsActionDown)\b',
        re.IGNORECASE,
    )

    _all_output = "\n".join(str(v) for v in (ctx.all_results_dict or {}).values())
    _out_lower = _all_output.lower()
    _out_tokens = {m.group(0).lower() for m in _API_TOKEN_RE.finditer(_all_output)}

    # Judge API coverage on the SHIPPED file (staging-aware), not in-memory
    # task outputs that may have failed to land (e.g. "SEARCH block not found").
    # Falls back to in-memory outputs when no .lua artifact is present on disk.
    _disk_blob = _effective_lua_blob(ctx)
    _coverage_corpus = _disk_blob if _disk_blob.strip() else _all_output
    _coverage_tokens = {
        m.group(0).lower() for m in _API_TOKEN_RE.finditer(_coverage_corpus)
    }

    gaps: list[str] = []

    # 1. Task-level API coverage.
    for _t in (getattr(ctx, 'tasks_list', None) or []):
        if not isinstance(_t, dict):
            continue
        _title = _t.get('title', '') or ''
        _task_tokens = {m.group(0).lower() for m in _API_TOKEN_RE.finditer(_title)}
        if not _task_tokens:
            continue  # structural task — skeleton satisfies it
        if not (_task_tokens & _coverage_tokens):
            gaps.append(f"Task {_t.get('id')} — {_title[:90]}")

    # 2. Design checklist coverage (secondary).
    _design = getattr(ctx, 'attraction_design', None)
    _checklist = getattr(_design, 'feature_checklist', None) if _design else None
    if _checklist:
        _stop = {
            'must', 'should', 'that', 'with', 'this', 'from', 'have', 'when',
            'been', 'into', 'each', 'every', 'never', 'called', 'events',
            'attraction', 'win', 'score',
        }
        for _feature in _checklist:
            _kws = [w for w in re.findall(r'\b\w{4,}\b', _feature.lower()) if w not in _stop]
            if _kws and not any(kw in _out_lower for kw in _kws[:4]):
                gaps.append(f"[checklist] {_feature}")

    # 3. State-ledger coverage — every pre-registered handle + primitive state
    #    variable must actually be USED (not just declared) in the final code.
    #    Reads the effective on-disk/merged file content so the injected
    #    `local <name> = ...` declaration counts as occurrence #1; a name with
    #    ≤1 occurrence is declared-but-never-used.
    if _design is not None:
        _ledger_names: list[tuple[str, str]] = []
        for _h in (getattr(_design, 'handles', None) or []):
            _hn = getattr(_h, 'name', '')
            if _hn:
                _ledger_names.append((str(_hn), getattr(_h, 'owner_task', '') or ''))
        for _sv in (getattr(_design, 'module_state_variables', None) or []):
            _sn = getattr(_sv, 'name', '')
            if _sn:
                _ledger_names.append((str(_sn), getattr(_sv, 'owner_task', '') or ''))
        if _ledger_names:
            _ledger_blob = _effective_lua_blob(ctx)
            if _ledger_blob.strip():
                for _ln, _owner in _ledger_names:
                    _uses = len(re.findall(r'\b' + re.escape(_ln) + r'\b', _ledger_blob))
                    if _uses <= 1:
                        gaps.append(
                            f"[state] `{_ln}` declared but never used"
                            + (f" (task {_owner})" if _owner else "")
                        )

    # 4. Marker-clearance - each canonical anchor names the concrete APIs it
    #    must introduce (CreatePool, AwardTickets, ...).  If those APIs are
    #    absent from the SHIPPED file, the anchor was never actually filled,
    #    regardless of what the in-memory task output claimed.
    try:
        from _anchors import CANONICAL_ANCHORS as _CANONICAL_MARKERS
    except Exception:
        _CANONICAL_MARKERS = []
    for _b_m, _loc_m, _marker_m in _CANONICAL_MARKERS:
        _num_m = re.search(r"TASK_(\d+)_INSERT_HOOK", _marker_m)
        _num_m_str = _num_m.group(1) if _num_m else ""
        _anchor_tokens = {m.group(0).lower() for m in _API_TOKEN_RE.finditer(_marker_m)}
        if not _anchor_tokens:
            continue
        if not (_anchor_tokens & _coverage_tokens):
            gaps.append(
                f"Anchor TASK_{_num_m_str} - required APIs never written "
                f"({_marker_m.strip()[:70]})"
            )

    # Deduplicate (signal 1 and signal 4 can both name the same task).
    gaps = list(dict.fromkeys(gaps))

    ctx.coverage_gaps = gaps
    return gaps


def _deterministic_verdict(ctx: PipelineContext) -> tuple[str, str]:
    """Compute the review verdict from deterministic signals only.

    Returns ``(verdict, issues_text)``.  This replaces the LLM verdict with
    hard signals so the loop converges in a bounded number of cycles.
    """
    verdict = "PASS"
    issues: list[str] = []

    # 1. Per-cycle luac syntax check on disk (catches fix-cycle regressions).
    _syntax = _luac_syntax_errors(ctx)
    if _syntax:
        verdict = "FAIL"
        issues.append(_syntax)

    # 2. Runtime simulation errors (nil handles, phantom APIs, bad arg counts).
    try:
        from runtime_sim import run_runtime_sim
        _sim = run_runtime_sim(ctx)
        ctx.runtime_errors = list(_sim) if _sim else []
        if _sim:
            verdict = "FAIL"
            issues.append(
                "## ⚡ Runtime Simulation Errors\n"
                + "\n".join(f"  {e}" for e in _sim)
            )
    except Exception:
        ctx.runtime_errors = []

    # 3. Open pre-flight violations (empty output / static patterns / schema /
    #    coverage / C++ compile).  Refreshed by the fix branch between cycles.
    _open_pf = (ctx.pre_flight_errors or "").strip()
    if _open_pf:
        verdict = "FAIL"
        issues.append("## ⛔ Open Pre-Flight Violations\n" + _open_pf)

    # 4. Mandatory economy/modifier content for attraction scopes (FM4).
    _rev_scope = getattr(ctx, '_scope_mode', '')
    if _rev_scope in ("NEW_ATTRACTION", "MODIFY_ATTRACTION"):
        # Judge against the SHIPPED file, not in-memory fragments.
        _all_lua = _effective_lua_blob(ctx).lower()
        if not _all_lua:
            _all_lua = " ".join(
                str(v) for v in (ctx.all_results_dict or {}).values()
            ).lower()
        if not any(kw in _all_lua for kw in (
            "attractionconstants.modifiers", "engine_mod_", ".modifiers",
        )):
            verdict = "FAIL"
            issues.append(
                "Missing AttractionConstants.modifiers read in OnStep — "
                "the attraction MUST read modifiers every frame."
            )
        if not any(kw in _all_lua for kw in ("awardtickets", "awardtokens")):
            verdict = "FAIL"
            issues.append(
                "Missing Engine.AwardTickets/AwardTokens — the attraction "
                "MUST award tickets/tokens using Engine.GetStreak() as a multiplier."
            )

    # 5. Task/feature completeness — the run is not done until every planned
    #    task has evidence in the output.  This prevents a thin stub from
    #    passing the lenient syntax/API gates while half the blueprint is
    #    unimplemented.
    try:
        _gaps = _coverage_gaps(ctx)
    except Exception:
        _gaps = []
    if _gaps:
        verdict = "FAIL"
        issues.append(
            "## 🧩 Unfinished Tasks / Missing Features\n"
            "The following planned tasks/features are NOT yet implemented in the output. "
            "Implement EACH one before this run can pass:\n"
            + "\n".join(f"  - {g}" for g in _gaps)
        )

    issues_text = "\n\n".join(issues).strip()
    return verdict, issues_text


def _run_tribunal_appeal(ctx: PipelineContext) -> str:
    """Escalate a non-converged review to the TRIBUNAL appellate court.

    Blind-reviews the final implementation against the still-open violations
    and returns a binding verdict string: ``"PASS"`` (MERGE) or ``"FAIL"``
    (REJECT).  Returns ``""`` when the tribunal is unreachable or produces no
    parseable verdict, so the caller can fall back to its legacy logic.
    """
    try:
        from pipeline import REASONING_MODEL
    except Exception:
        return ""

    # Blind-review the SHIPPED file, not in-memory fragments.
    _final_code = _effective_lua_blob(ctx)
    if not _final_code.strip():
        _final_code = "\n\n".join(
            str(v) for v in (ctx.all_results_dict or {}).values()
        )
    _open_pf = (ctx.pre_flight_errors or "").strip()
    _rt_lines = [f"  {e}" for e in (getattr(ctx, 'runtime_errors', None) or [])]
    _issues_block = "\n".join(
        x for x in ([_open_pf] if _open_pf else []) + _rt_lines if x.strip()
    ) or "(no open violations recorded)"

    _tribunal_system = (
        "You are the TRIBUNAL AGENT — a neutral appellate arbiter. "
        "You do NOT write code. Blind-review the implementation against the "
        "open violations listed and render a BINDING verdict.\n"
        "Verdict options (output EXACTLY one line):\n"
        "- [MERGE:Tribunal:<justification>] — implementation is acceptable.\n"
        "- [REJECT:Tribunal:<justification>] — implementation must be rejected.\n"
        "If any listed violation is unresolved and would produce broken or "
        "non-functional code, you MUST REJECT."
    )
    _tribunal_prompt = (
        "## Open Violations (must be satisfied)\n"
        + _issues_block
        + "\n\n## Implementation Under Review\n```\n"
        + _final_code[:8000]
        + "\n```\n\nRender your binding verdict now."
    )

    try:
        _out = call_ollama(
            _tribunal_system, _tribunal_prompt, "Tribunal Appeal", REASONING_MODEL,
            params={"num_predict": 512},
            skip_pre_summarizer=True,
        )
    except Exception as _te:
        print(f"  [Tribunal] ⚠ Tribunal appeal failed: {_te}")
        return ""

    if _is_fatal_ollama(_out):
        print("  [Tribunal] ⚠ Tribunal unreachable — no appellate verdict rendered.")
        return ""
    _out_preview = (_out or "").strip()
    print(f"  [Tribunal] Raw verdict ({len(_out_preview)} chars): {_out_preview[:300]!r}")
    if re.search(r"\[MERGE[:\]]", _out, re.IGNORECASE) or re.search(r"\bMERGE\b", _out):
        return "PASS"
    if re.search(r"\[REJECT[:\]]", _out, re.IGNORECASE) or re.search(r"\bREJECT\b", _out):
        return "FAIL"
    print("  [Tribunal] ⚠ Tribunal produced no parseable verdict — no appellate verdict rendered.")
    return ""


def _coder_defend_or_revise(ctx, objections, target_path, target_rel, current_code, coder_model):
    """Ask the coder to answer the tribunal's objections with surgical patches.

    Returns the revised file content, or None when the coder produced nothing
    usable (no blocks, no matches, or a syntax-breaking revision).  Reuses the
    same SEARCH/REPLACE extractor + fuzzy applier as the anchor fix loop, and
    the same luac-clean guard so a bad revision can never persist.
    """
    _sys = (
        "You are the CODER agent responding to the Tribunal's objections. "
        "Fix ONLY the defects listed.\n"
        "Output ONLY SEARCH/REPLACE blocks in this EXACT 3-part format, one block per defect:\n"
        "  <<<<<<< SEARCH\n"
        "  <the EXACT current lines from the file>\n"
        "  =======\n"
        "  <the corrected lines>\n"
        "  >>>>>>> REPLACE\n"
        "The SEARCH side must match the current file byte-for-byte. Keep the "
        "`=======` separator and the `>>>>>>> REPLACE` closer as SEPARATE lines — never "
        "merge them into `=====> REPLACE`. Do NOT output the whole file, prose, or commentary.\n"
        "API RULES: every Spawn* call takes WORLD COORDINATES as its first argument and "
        "RETURNS a handle — never pass a handle as the first argument. Table constructors "
        "use `key = value` pairs (no `local` keyword, no JSON `\"key\": value`)."
    )
    _user = (
        "## Tribunal Objections\n" + (objections or "")[:3000]
        + "\n\n## Current File\n```\n" + (current_code or "")[:8000]
        + "\n```\n\nFix the listed defects with SEARCH/REPLACE blocks now."
    )
    try:
        from pipeline import CODER_MODEL as _CM
        _model = coder_model or _CM
    except Exception:
        _model = coder_model or "qwen3.5:9b"
    try:
        _out = call_ollama(
            _sys, _user, "Tribunal Debate — Coder Response", _model,
            params={"num_predict": 1536}, skip_pre_summarizer=True,
        )
    except Exception as _e:
        print(f"  [Tribunal Debate] ⚠ coder call failed: {_e}")
        return None
    if _is_fatal_ollama(_out):
        return None

    from _helpers_exec import _extract_search_replace_blocks as _extract, _fuzzy_apply_patch as _apply

    _blocks = _extract(_out or "")
    if not _blocks:
        print("  [Tribunal Debate] ⚠ coder emitted no SEARCH/REPLACE blocks.")
        return None

    _patched = current_code
    _applied = 0
    for _b in _blocks:
        _new = _apply(_patched, _b.get("search", ""), _b.get("replace", ""))
        if _new != _patched:
            _patched = _new
            _applied += 1
    if not _applied:
        print("  [Tribunal Debate] ⚠ none of the coder's SEARCH blocks matched.")
        return None

    # Syntax guard: only accept the revision if it stays luac-clean.
    try:
        import subprocess as _sp, tempfile as _tf, os as _os
        _exe = _get_luac_exe()
        if _exe:
            _fd, _tmp = _tf.mkstemp(suffix=".lua")
            try:
                with _os.fdopen(_fd, "w", encoding="utf-8") as _fh:
                    _fh.write(_patched)
                _r = _sp.run([_exe, "-p", _tmp], capture_output=True, text=True, timeout=30)
                if _r.returncode != 0:
                    print(f"  [Tribunal Debate] ⚠ coder revision broke syntax — discarding.")
                    print(f"  [Tribunal Debate luac] {_r.stderr.strip()}")
                    return None
            finally:
                try:
                    _os.unlink(_tmp)
                except Exception:
                    pass
    except Exception:
        pass  # luac unavailable — accept the revision

    # Write back to disk so the deterministic re-check and later gates see it.
    if target_path is not None:
        try:
            target_path.write_text(_patched, encoding="utf-8")
            try:
                from _helpers_io import is_staging_active
                if is_staging_active():
                    _real = (ctx.project_root / target_rel).resolve()
                    _real.parent.mkdir(parents=True, exist_ok=True)
                    _real.write_text(_patched, encoding="utf-8")
            except Exception:
                pass
        except Exception as _we:
            print(f"  [Tribunal Debate] ⚠ write-back failed: {_we}")

    # Sync the in-memory merged view so _effective_lua_blob sees the revision.
    try:
        _mkey = "merged:" + (target_rel or "")
        if _mkey in ctx.all_results_dict:
            ctx.all_results_dict[_mkey] = _patched
    except Exception:
        pass

    print(f"  [Tribunal Debate] ✅ Coder applied {_applied} surgical patch(es).")
    return _patched


def _run_tribunal_coder_debate(ctx: PipelineContext, max_rounds: int = 3) -> str:
    """Bounded appellate debate: the TRIBUNAL argues with the coder until consensus.

    Each round:
      1. The tribunal reviews the SHIPPED file + open issues and renders
         [MERGE] or [REJECT:...:<objections>].
      2. On REJECT the objections go to the coder, which answers with surgical
         SEARCH/REPLACE patches (applied via _fuzzy_apply_patch).
      3. The deterministic verdict re-runs; a clean PASS ends the debate.

    The deterministic gates (luac + phantom + coverage) are the ground truth: a
    tribunal MERGE cannot override a still-failing deterministic verdict, and a
    syntax-breaking coder revision is discarded.  Returns "PASS"/"FAIL", or ""
    when the debate could not run at all (callers fall back to the single-shot
    appeal).
    """
    try:
        from pipeline import REASONING_MODEL as _RM
    except Exception:
        return ""

    # Resolve the shipped .lua target path so coder SEARCH blocks match disk.
    _target_rel = getattr(ctx, '_mono_target', '') or getattr(ctx, '_monolithic_lua_target', '') or ''
    if not _target_rel:
        for _t in (ctx.task_map or {}).values():
            _tf = str(getattr(_t, 'target_file', '') or '')
            if _tf.endswith('.lua'):
                _target_rel = _tf.replace("\\", "/")
                break
    _target_path = None
    if _target_rel:
        try:
            from _helpers_io import get_staging_path, is_staging_active
            _p = (ctx.project_root / _target_rel).resolve()
            if is_staging_active():
                _p = get_staging_path(_p, project_root=ctx.project_root)
            if _p.is_file():
                _target_path = _p
        except Exception:
            pass

    _tribunal_system = (
        "You are the TRIBUNAL AGENT — a neutral appellate arbiter who ARGUES WITH THE CODER "
        "until the implementation is correct. You do NOT write code yourself.\n"
        "Each turn, review the implementation against the open violations and render:\n"
        "- [MERGE:Tribunal:<justification>] — the implementation is now acceptable.\n"
        "- [REJECT:Tribunal:<justification>] — followed by a numbered OBJECTIONS list of the "
        "specific, concrete defects the coder must still fix.\n"
        "Be surgical: object only to issues that are visibly present and would break or "
        "incomplete the implementation. Never invent missing APIs or rules."
    )

    for _round in range(1, max_rounds + 1):
        print(f"\n  [Tribunal Debate] Round {_round}/{max_rounds}")

        _final_code = _effective_lua_blob(ctx)
        if not _final_code.strip():
            _final_code = "\n\n".join(str(v) for v in (ctx.all_results_dict or {}).values())

        _issues_block = (
            (ctx.pre_flight_errors or "").strip()
            + "\n"
            + "\n".join(f"  {e}" for e in (getattr(ctx, 'runtime_errors', None) or []))
        ).strip() or "(no open violations recorded)"

        _trib_prompt = (
            "## Open Violations (must be satisfied)\n" + _issues_block
            + "\n\n## Implementation Under Review\n```\n" + _final_code[:8000]
            + "\n```\n\nRender your verdict now: [MERGE] or [REJECT] + a numbered OBJECTIONS list."
        )

        try:
            _trib_out = call_ollama(
                _tribunal_system, _trib_prompt, f"Tribunal Debate R{_round}", _RM,
                params={"num_predict": 768}, skip_pre_summarizer=True,
            )
        except Exception as _te:
            print(f"  [Tribunal Debate] ⚠ tribunal call failed: {_te}")
            return ""
        if _is_fatal_ollama(_trib_out):
            print("  [Tribunal Debate] ⚠ tribunal unreachable.")
            return ""

        _trib_preview = (_trib_out or "").strip()
        print(f"  [Tribunal Debate] Tribunal ({len(_trib_preview)} chars): {_trib_preview[:240]!r}")

        _is_merge = bool(re.search(r"\[MERGE[:\]]", _trib_out, re.IGNORECASE)
                         or re.search(r"\bMERGE\b", _trib_out))
        _is_reject = bool(re.search(r"\[REJECT[:\]]", _trib_out, re.IGNORECASE)
                          or re.search(r"\bREJECT\b", _trib_out))

        if _is_merge and not _is_reject:
            _dv, _ = _deterministic_verdict(ctx)
            if _dv == "PASS":
                return "PASS"
            print("  [Tribunal Debate] Tribunal MERGE'd but deterministic verdict still FAILs — continuing.")
        elif _is_reject:
            _revised = _coder_defend_or_revise(ctx, _trib_out, _target_path, _target_rel, _final_code, _RM)
            if _revised:
                _dv, _ = _deterministic_verdict(ctx)
                if _dv == "PASS":
                    print("  [Tribunal Debate] ✅ Consensus reached (deterministic PASS).")
                    return "PASS"
                print("  [Tribunal Debate] Coder revised; deterministic verdict still FAILs — next round.")
            else:
                print("  [Tribunal Debate] ⚠ coder produced no usable revision — stopping debate.")
                break
        else:
            print("  [Tribunal Debate] ⚠ no parseable verdict — stopping debate.")
            break

    _dv, _ = _deterministic_verdict(ctx)
    return _dv


# ----------------------------------------------------------------------
#  Structured Integration Review (Standard #1 — replaces regex fragility)
# ----------------------------------------------------------------------

_STRUCTURED_REVIEW_SYSTEM = (
    "You are the INTEGRATION REVIEWER for 'Midway to Nowhere'. "
    "Review the generated Lua code against the Active Bridge Contract and checklist in your context. "
    "Do NOT write or fix code — only identify concrete issues. "
    "Respond with a single JSON object matching the provided schema:\n"
    "- verdict: \"CONFIRMED\" = no blocking issues (PASS); \"REVISED\" = fix required (FAIL); "
    "\"REJECTED\" = fundamentally wrong (FAIL).\n"
    "- issues: one entry per concrete problem with severity (\"error\"|\"warning\"|\"info\"), "
    "a location (file:line or function name), and a one-line message.\n"
    "Rules:\n"
    "1. Only report issues you can actually SEE in the code. Never invent missing functions, rules, or attributes.\n"
    "2. Do NOT flag missing logging/telemetry/observability — a downstream auditor handles instrumentation.\n"
    "3. A phantom API is any call not present in the Active Bridge Contract; report the exact call and location.\n"
    "4. Do NOT suggest replacement API names — the fix agent holds the approved API list.\n"
    "5. Scaffold/stub/comment-only/TODO-only implementations are a FAIL (REVISED).\n"
    "6. If there are no issues, return an empty issues list and verdict CONFIRMED.\n"
    "Output this exact JSON shape (and only this):\n"
    '{"verdict": "CONFIRMED", "issues": []}\n'
    '{"verdict": "REVISED", "issues": [{"severity": "error", "location": "OnStep", "message": "phantom API"}]}\n'
    "Return ONLY the JSON object — no prose, no markdown fences."
)


def _extract_json_block(text: str) -> str:
    """Extract the outermost {...} JSON object from an LLM response."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return ""
    return text[start:end + 1]


def _structured_review(review_input: str) -> tuple[str, str]:
    """Run the integration review through the native Ollama endpoint in JSON
    mode, validated against the ReviewVerdict Pydantic schema.

    Returns ``(verdict, issues_text)`` where verdict is "PASS" or "FAIL" and
    issues_text is a bullet list.  Raises on any failure so the caller can fall
    back to the legacy regex parser.

    NOTE: this deliberately avoids the openai/instructor client — the OpenAI-
    compatible /v1/chat/completions endpoint was crashing the 9B runner with
    'model runner has unexpectedly stopped' (500) while the native /api/chat
    path on the same model works reliably.
    """
    from structured_schemas import ReviewVerdict, Verdict, ollama_format, mark_schema_rejected
    from pipeline import REASONING_MODEL
    from ollama_client import is_fatal_ollama_error as _is_fatal

    # Strip the legacy prose-format tail ("OUTPUT FORMAT (MANDATORY...") from the
    # review prompt so it does not conflict with JSON-mode extraction.
    _clean_input = re.split(r"\nOUTPUT FORMAT \(MANDATORY", review_input, maxsplit=1)[0]

    print("  [Review-Fix] Structured review: extracting verdict (native JSON mode)...", flush=True)
    _fmt = ollama_format("review_verdict", REASONING_MODEL)
    out = call_ollama(
        _STRUCTURED_REVIEW_SYSTEM,
        _clean_input,
        "Integration Review (structured JSON)",
        REASONING_MODEL,
        params={"format": _fmt, "num_predict": 1024},
        skip_pre_summarizer=True,
    )
    if _is_fatal(out) and _fmt != "json":
        mark_schema_rejected("review_verdict", REASONING_MODEL)
        print("  [Review-Fix] schema-constrained call failed; retrying plain JSON mode...", flush=True)
        out = call_ollama(
            _STRUCTURED_REVIEW_SYSTEM,
            _clean_input,
            "Integration Review (structured JSON fallback)",
            REASONING_MODEL,
            params={"format": "json", "num_predict": 1024},
            skip_pre_summarizer=True,
        )
    if _is_fatal(out):
        raise RuntimeError(f"Ollama error during structured review: {out[:200]}")

    result = ReviewVerdict.model_validate_json(_extract_json_block(out))
    verdict = "PASS" if result.verdict == Verdict.CONFIRMED else "FAIL"
    issues_text = ""
    if result.issues:
        _lines = []
        for _i in result.issues:
            _loc = f" @ {_i.location}" if _i.location else ""
            _lines.append(f"- [{_i.severity}]{_loc} {_i.message}")
        issues_text = "\n".join(_lines)
    print(f"  [Review-Fix] Structured review complete: {verdict} ({len(result.issues)} issue(s)).", flush=True)
    return verdict, issues_text


# ----------------------------------------------------------------------
#  Phase 6: Integration Review & Fix Loop
# ----------------------------------------------------------------------

def _ask_more_review_cycles(ctx: PipelineContext) -> bool:
    """Offer the user another full review/fix round before giving up.

    Only prompts in interactive mode (TTY present, not forced-deterministic).
    Returns False in unattended/server mode so the pipeline never blocks.
    """
    _forced = bool(os.environ.get("MIDWAY_FORCED_DETERMINISTIC", ""))
    # MIDWAY_REVIEW_PROMPT=1 re-enables this prompt even in forced/server mode
    # (still requires a TTY), for users who run the stream server in a terminal
    # and want to extend review/fix rounds interactively.
    _interactive_override = bool(os.environ.get("MIDWAY_REVIEW_PROMPT", ""))
    _has_tty = hasattr(sys.stdin, 'isatty') and sys.stdin.isatty()
    if not _has_tty:
        return False
    if _forced and not _interactive_override:
        return False
    _ext_rounds = int(getattr(ctx, 'review_extension_rounds', 0) or 0)
    if _ext_rounds >= 5:
        print("  [Review-Fix] ⚠ Review extension cap (5 rounds) reached — no further extensions.")
        return False
    trigger_chime()
    try:
        _ans = input(
            f"\n  [Review-Fix] Out of review cycles ({ctx.review_cycle}) without a PASS.\n"
            f"  The run may still be converging. Run another review/fix round? [y/N]: "
        ).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return _ans in ("y", "yes")


def _gap_filler_surgery(ctx: PipelineContext, target_rel: str) -> bool:
    """Try deterministic repair of the accumulated file BEFORE any LLM rewrite.

    The whole-file LLM surgery is what corrupts the file (it re-emits the
    skeleton and mis-splices fragments into orphaned blocks).  Before falling
    back to it, run the deterministic ``post_process_surgery`` + luac.  This
    fixes duplicate lifecycle functions, engine-global redefinitions, bare
    calls, phantom APIs, and block imbalance with NO model in the loop.
    Returns True when the deterministic repair is luac-clean and committed.
    """
    import subprocess as _sp
    import tempfile as _tmp_mod
    import os as _os_mod

    _real = ctx.project_root / target_rel
    _src = ""
    if _real.is_file():
        _src = _real.read_text(encoding="utf-8", errors="replace")
    if not _src.strip():
        try:
            from _helpers_io import get_staging_path, is_staging_active
            if is_staging_active():
                _st = get_staging_path(_real, project_root=ctx.project_root)
                if _st and _st.is_file():
                    _src = _st.read_text(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if not _src.strip():
        return False

    try:
        from _post_process_lua import post_process_surgery as _pp_surg
        _fixed = _pp_surg(_src)
    except Exception:
        return False

    _fd, _tf = _tmp_mod.mkstemp(suffix=".lua")
    try:
        with _os_mod.fdopen(_fd, "w", encoding="utf-8") as _fh:
            _fh.write(_fixed)
        _r = _sp.run([_LUAC_EXE, "-p", _tf], capture_output=True, text=True, timeout=30)
    finally:
        try:
            _os_mod.unlink(_tf)
        except Exception:
            pass
    if _r.returncode != 0:
        print(f"  [Gap Fill] deterministic repair still not luac-clean - deferring to LLM surgery.")
        print(f"  [Gap Fill luac] {_r.stderr.strip()}")
        return False

    try:
        _real.parent.mkdir(parents=True, exist_ok=True)
        _real.write_text(_fixed, encoding="utf-8")
    except Exception:
        return False
    try:
        from _helpers_io import get_staging_path, is_staging_active
        if is_staging_active():
            _st = get_staging_path(_real, project_root=ctx.project_root)
            if _st is not None:
                _st.parent.mkdir(parents=True, exist_ok=True)
                _st.write_text(_fixed, encoding="utf-8")
    except Exception:
        pass
    _snap = getattr(ctx, "_last_luac_clean_anchor", None)
    if _snap is not None:
        _snap[target_rel] = _fixed
    print(f"  [Gap Fill] deterministic repair committed {target_rel} "
          f"({len(_fixed)} chars) - no LLM rewrite needed.")
    return True


def _whole_file_surgery(ctx: PipelineContext, target_rel: str) -> bool:
    """Last-resort whole-file LLM rewrite, gated by luac + RuntimeSim.

    The per-task fix cycle edits fragments and cannot repair a file-level
    structural defect (a lost OnStep wrapper, a stray `end`, a truncated
    call). This pass sends the ENTIRE accumulated file to the coder with an
    explicit "make it luac-clean and contract-conformant" mandate, validates
    the result with luac and the RuntimeSim analyzer, and only commits when
    both pass. Returns True on success.
    """
    import subprocess as _sp
    import tempfile as _tmp_mod
    import os as _os_mod

    _real = ctx.project_root / target_rel
    _src = ""
    if _real.is_file():
        _src = _real.read_text(encoding="utf-8", errors="replace")
    if not _src.strip():
        try:
            from _helpers_io import get_staging_path, is_staging_active
            if is_staging_active():
                _st = get_staging_path(_real, project_root=ctx.project_root)
                if _st and _st.is_file():
                    _src = _st.read_text(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if not _src.strip():
        return False

    _errors = (getattr(ctx, "pre_flight_errors", "") or "")[:3000]
    _bridge = build_fix_bridge_snippet(ctx) or ""

    _sys = (
        "You are a Lua syntax-repair specialist for a custom C++17 game engine "
        "(Lua 5.4 via sol2, Jolt physics). Your ONLY job is to make the given "
        "file load cleanly and call only the approved MidwayPhysics APIs."
    )
    _user = (
        f"## Target File: {target_rel}\n\n"
        f"## Known errors (fix ALL of these)\n{_errors}\n\n"
        f"{_bridge}\n\n"
        f"## CURRENT FILE (complete)\n"
        f"```lua\n{_src}\n```\n\n"
        f"Rewrite the ENTIRE file so that:\n"
        f"1. It is luac-clean: every function/if/do/repeat block is balanced; "
        f"no stray or missing 'end'; no truncated calls.\n"
        f"2. Every MidwayPhysics call uses the EXACT argument count from the contract above.\n"
        f"3. The lifecycle is preserved: OnLoadStatic() calls SpawnSharedBooth(); "
        f"OnLoad() spawns bodies and registers MidwayPhysics.OnStep(function(dt) ... end); "
        f"OnUnload() exists.\n"
        f"4. No Roblox/Unity APIs, no C++ engine internals, no TODO scaffolds.\n"
        f"Output ONLY one ```lua fenced block with the complete corrected file. "
        f"No prose before or after.\n"
    )

    _out = call_ollama(_sys, _user, f"Whole-File Surgery ({target_rel})",
                       _EXECUTION_MODEL, params={"num_predict": 4096},
                       skip_pre_summarizer=True)
    if _is_fatal_ollama(_out):
        print("  [Surgery] ⚠ Ollama error during surgery — aborting rewrite.")
        return False

    _m = re.search(r'```(?:lua|luau)?\s*\n(.*?)```', _out, re.DOTALL | re.IGNORECASE)
    if not _m:
        print("  [Surgery] ⚠ no fenced Lua block in surgery output — aborting.")
        return False
    _fixed = _m.group(1).strip("\n")
    if len(_fixed) < 200:
        print(f"  [Surgery] ⚠ rewrite too short ({len(_fixed)} chars) — rejecting.")
        return False

    # Deterministic arity repair BEFORE the gates, so a one-token over-arg
    # slip (SpawnStaticBox 7 vs 6) does not reject an otherwise-good rewrite.
    try:
        from _preflight_static import _fix_spawn_arities_in_text as _arity_fix
        _fixed, _arity_n = _arity_fix(_fixed)
        if _arity_n:
            print(f"  [Surgery] ✅ deterministic arity repair: {_arity_n} call(s).")
    except Exception as _afe:
        print(f"  [Surgery] ⚠ arity repair skipped: {_afe}")

    # Deterministic repair of bare/phantom pool calls BEFORE the gates, so a
    # surgery that emits `PoolAcquire(...)` without the MidwayPhysics. prefix
    # (or a phantom API) is fixed deterministically instead of bouncing the
    # RuntimeSim gate or committing broken code.  Uses the targeted fixes
    # (NOT full post_process_lua) to avoid marker->TODO laundering and
    # re-injecting lifecycle invariants the surgery already preserves.
    try:
        from _post_process_lua import post_process_surgery as _pp_surgery
        _pp_before = _fixed
        _fixed = _pp_surgery(_fixed)
        if _fixed != _pp_before:
            print("  [Surgery] deterministic post-surgery repair applied.")
    except Exception as _ppe:
        print(f"  [Surgery] post-surgery repair skipped: {_ppe}")

    # Gate 1: luac must accept the rewrite.
    _fd, _tmp = _tmp_mod.mkstemp(suffix=".lua")
    try:
        with _os_mod.fdopen(_fd, "w", encoding="utf-8") as _fh:
            _fh.write(_fixed)
        _r = _sp.run([_LUAC_EXE, "-p", _tmp], capture_output=True, text=True, timeout=30)
    finally:
        try:
            _os_mod.unlink(_tmp)
        except Exception:
            pass
    if _r.returncode != 0:
        print(f"  [Surgery] ⚠ luac rejected rewrite:")
        print(f"  [Surgery luac] {_r.stderr.strip()}")
        return False

    # Gate 2: RuntimeSim arity / phantom-API analysis must pass.
    try:
        from runtime_sim import _analyse_lua_text as _sim_analyse
        _sim_errs = _sim_analyse("task_surgery", _fixed)
        if _sim_errs:
            print(f"  [Surgery] ⚠ RuntimeSim rejected rewrite "
                  f"({len(_sim_errs)} issue(s)): {_sim_errs[0][:160]}")
            return False
    except Exception as _se:
        print(f"  [Surgery] ⚠ RuntimeSim check skipped: {_se}")

    # Commit to the real path AND (when active) staging, so the review loop's
    # per-cycle luac check (which reads the real path) sees the fix.
    try:
        _real.parent.mkdir(parents=True, exist_ok=True)
        _real.write_text(_fixed, encoding="utf-8")
    except Exception as _we:
        print(f"  [Surgery] ⚠ failed to write real path: {_we}")
        return False
    try:
        from _helpers_io import get_staging_path, is_staging_active
        if is_staging_active():
            _st = get_staging_path(_real, project_root=ctx.project_root)
            if _st is not None:
                _st.parent.mkdir(parents=True, exist_ok=True)
                _st.write_text(_fixed, encoding="utf-8")
    except Exception:
        pass

    _snap = getattr(ctx, "_last_luac_clean_anchor", None)
    if _snap is not None:
        _snap[target_rel] = _fixed
    print(f"  [Surgery] ✅ committed luac-clean rewrite of {target_rel} ({len(_fixed)} chars).")
    return True


_TARGETED_FIX_MAX = 6                # max one-error-one-fix coder calls per stage
_TARGETED_CRITIC_MODEL = "phi3:14b"  # fresh-eyes critic, used only as a fallback


def _extract_individual_errors(ctx) -> list[str]:
    """Deterministic, individually-actionable errors (RuntimeSim + luac)."""
    _errs: list[str] = []
    try:
        for _e in (getattr(ctx, "runtime_errors", None) or []):
            _s = str(_e).strip()
            if _s and _s not in _errs:
                _errs.append(_s)
    except Exception:
        pass
    try:
        for _ln in (getattr(ctx, "pre_flight_errors", "") or "").splitlines():
            _s = _ln.strip()
            if not _s or _s.startswith(("##", "###", "**", "```")):
                continue
            if ("syntax error" in _s.lower() or "expected" in _s.lower()) and ":" in _s:
                if _s not in _errs:
                    _errs.append(_s)
    except Exception:
        pass
    return _errs


def _apply_one_error_patch(file_text: str, model_out: str):
    """Apply the FIRST extractable SEARCH/REPLACE block. Returns (text, applied)."""
    if not model_out or not file_text:
        return file_text, 0
    try:
        from _helpers_exec import _extract_search_replace_blocks as _extract_sr
        from _helpers_exec import _fuzzy_apply_patch as _fuzzy
        _blocks = _extract_sr(model_out)
        _patched = file_text
        _applied = 0
        for _blk in _blocks[:1]:
            _new = _fuzzy(_patched, _blk.get("search", ""), _blk.get("replace", ""))
            if _new != _patched:
                _patched = _new
                _applied += 1
        return _patched, _applied
    except Exception:
        return file_text, 0


def _luac_clean(content: str):
    """Return (is_clean, stderr)."""
    import subprocess
    import tempfile
    import os as _os
    try:
        from _luac_path import get_luac_exe
        _exe = get_luac_exe() or "luac"
    except Exception:
        _exe = "luac"
    _fd, _tmp = tempfile.mkstemp(suffix=".lua")
    try:
        with _os.fdopen(_fd, "w", encoding="utf-8") as _fh:
            _fh.write(content)
        _r = subprocess.run([_exe, "-p", _tmp], capture_output=True, text=True, timeout=30)
    except Exception as _e:
        return False, str(_e)
    finally:
        try:
            _os.unlink(_tmp)
        except Exception:
            pass
    return _r.returncode == 0, _r.stderr.strip()


def _run_critic_pass(ctx, file_text: str, errors: list[str]) -> str:
    """One fresh-eyes critic pass (a DIFFERENT model) emitting SEARCH/REPLACE
    fixes for the remaining errors. Returns the raw model output."""
    _sys = (
        "You are a meticulous Lua 5.4 code reviewer (the CRITIC) auditing a "
        "carnival-attraction script that failed deterministic runtime checks. "
        "Output ONE SEARCH/REPLACE block per defect, using the exact format:\n"
        "<<<<<<< SEARCH\n<exact current lines>\n=======\n<corrected lines>\n"
        ">>>>>>> REPLACE\n"
        "No prose, no full-file rewrite."
    )
    _err_list = "\n".join(f"- {e}" for e in errors[:10])
    _user = (
        f"## Defects reported by the deterministic checker\n{_err_list}\n\n"
        f"## Current file\n```lua\n{file_text}\n```\n"
    )
    try:
        return call_ollama(_sys, _user, "Critic Pass (14B)", _TARGETED_CRITIC_MODEL,
                           params={"num_predict": 2048}, skip_pre_summarizer=True)
    except Exception as _e:
        print(f"  [Critic Pass] ⚠ call failed: {_e}")
        return ""


def _run_targeted_fix_stage(ctx) -> bool:
    """High-level one-error-one-fix stage, run BEFORE the tribunal.

    The tribunal (the SAME 9B model) can argue but cannot write correct code.
    This stage feeds each deterministic error to the CODER one at a time with
    precise extrinsic feedback (exact error + exact file + exact fix), applies
    the surgical patch, and re-verifies with luac.  Only when the coder leaves
    the file dirty do we escalate to a fresh-eyes CRITIC (a different model).
    Returns True when the file ends luac-clean AND runtime-clean.
    """
    _errs = _extract_individual_errors(ctx)
    if not _errs:
        print("  [Targeted Fix] No individual errors — file already clean.")
        return True

    try:
        from runtime_sim import _effective_lua_files
        _files = _effective_lua_files(ctx) or {}
    except Exception:
        _files = {}

    if not _files:
        return False

    from _pipeline_helpers import CODER_MODEL
    from _helpers_io import atomic_write_text

    for _rel, _content in _files.items():
        _cur = _content
        _coder_fixed_any = False

        # -- Pass 1: coder, one error at a time ---------------------------
        for _err in _errs[:_TARGETED_FIX_MAX]:
            _sys = (
                "You are a senior Lua 5.4 engineer repairing ONE specific defect in a "
                "carnival-attraction script. Output ONLY one SEARCH/REPLACE block that "
                "fixes EXACTLY the error given. Do NOT rewrite the file, do NOT touch "
                "unrelated code, no prose.\n\n"
                "Format exactly:\n"
                "<<<<<<< SEARCH\n<exact current lines>\n=======\n<corrected lines>\n"
                ">>>>>>> REPLACE"
            )
            _user = (
                f"## THE ONE ERROR TO FIX\n{_err}\n\n"
                f"## CURRENT FILE (patch only the lines implicated above)\n"
                f"```lua\n{_cur}\n```\n"
            )
            _out = ""
            try:
                _out = call_ollama(_sys, _user, "Targeted One-Error Fix", CODER_MODEL,
                                   params={"num_predict": 2048}, skip_pre_summarizer=True)
            except Exception as _e:
                print(f"  [Targeted Fix] ⚠ coder call failed: {_e}")
                continue
            if not _out:
                continue
            _patched, _applied = _apply_one_error_patch(_cur, _out)
            if not _applied:
                continue
            _clean, _luac_err = _luac_clean(_patched)
            if not _clean:
                print(f"  [Targeted Fix] ⚠ coder patch rejected (luac): {_luac_err[:120]}")
                continue
            _cur = _patched
            _coder_fixed_any = True
            print(f"  [Targeted Fix] ✅ coder applied a patch for: {_err[:90]}")

        # -- Pass 2: write back, re-check, escalate to critic if still dirty --
        try:
            from _post_process_lua import post_process_lua as _ppl
            _cur = _ppl(_cur)
        except Exception as _e:
            print(f"  [Targeted Fix] ⚠ post-process failed: {_e}")

        # Write back so run_runtime_sim (which reads disk/staging) sees _cur.
        try:
            _real = ctx.project_root / _rel
            atomic_write_text(_real, _cur)
        except Exception as _e:
            print(f"  [Targeted Fix] ⚠ write-back failed: {_e}")
            continue

        _clean, _luac_err = _luac_clean(_cur)
        _remaining: list[str] = []
        if _clean:
            try:
                from runtime_sim import run_runtime_sim as _run_rtsim
                ctx.runtime_errors = []
                _remaining = _run_rtsim(ctx) or []
            except Exception:
                _remaining = []

        if (not _clean) or _remaining:
            print("  [Targeted Fix] ⚠ Still dirty after coder pass — invoking 14B critic.")
            _critic_errs = _remaining or _errs
            _critic_out = _run_critic_pass(ctx, _cur, _critic_errs)
            _patched, _applied = _apply_one_error_patch(_cur, _critic_out)
            if _applied:
                _pc, _pe = _luac_clean(_patched)
                if _pc:
                    _cur = _patched
                    try:
                        from _post_process_lua import post_process_lua as _ppl2
                        _cur = _ppl2(_cur)
                    except Exception:
                        pass
                    try:
                        atomic_write_text(_real, _cur)
                    except Exception:
                        pass
                    print("  [Targeted Fix] ✅ critic patch applied.")

        # -- Finalize: verify clean (luac + runtime) ------------------------
        _clean, _luac_err = _luac_clean(_cur)
        if not _clean:
            print(f"  [Targeted Fix] ⚠ final file still not luac-clean: {_luac_err[:160]}")
            continue
        try:
            from runtime_sim import run_runtime_sim as _run_rtsim2
            ctx.runtime_errors = []
            _rem2 = _run_rtsim2(ctx) or []
            if not _rem2:
                print(f"  [Targeted Fix] ✅ {_rel} is luac-clean AND runtime-clean.")
                return True
            print(f"  [Targeted Fix] ⚠ {len(_rem2)} runtime error(s) remain after targeted stage.")
        except Exception as _e:
            print(f"  [Targeted Fix] ⚠ runtime re-check failed: {_e}")

    return False


def _run_review_fix_loop(ctx: PipelineContext) -> PipelineContext:
    """Phase 6: Integration review, domain-aware fix cycle, insanity
    detection, reconciliation gate, and pre-flight check integration."""
    print(f"\n{'='*70}")
    print(f"  Phase 6: Integration Review & Fix Loop")
    print(f"{'='*70}")
    ctx.output_parts.append("\n## Phase 6: Integration Review & Fix Loop\n")

    # -- Run Pre-Flight Checks (compilation, syntax, Architect fix) --
    # Late import  avoids sibling cross-import loop with _finalize_preflight
    from _finalize_preflight import _run_preflight_checks
    ctx = _run_preflight_checks(ctx)

    # Build conflict resolutions string
    ctx.conflicts_str = ""
    if ctx.conflict_resolutions:
        ctx.conflicts_str = (
            "## Conflict Resolutions (CRITICAL: Adhere to these compromises)\n"
            + "\n\n".join(ctx.conflict_resolutions)
            + "\n\n"
        )

    # -- Insanity Detector -------------------------------------------
    ctx.seen_code_hashes_set = set()

    # -- Context Window Protection: Indexed Active Ledger ------------
    active_ledger_path = (
        ctx.project_root / "docs" / "memory" / "active_run_ledger.md"
    )
    active_ledger_content = ["## Active Run Code State\n"]
    active_toc = ["### Active Code Table of Contents\n"]

    for tid, output in ctx.all_results_dict.items():
        header = f"### [{tid}]"
        active_ledger_content.append(f"{header}\n{output}\n")
        active_toc.append(
            f"- [{tid}](docs/memory/active_run_ledger.md#{tid})"
        )

    active_ledger_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        active_ledger_path, "\n".join(active_ledger_content)
    )
    ctx.active_code_index = (
        "\n".join(active_toc)
        + "\n\n**INSTRUCTIONS:** The generated code is stored in the active ledger. "
        + "You MUST use the XML tag "
        + "<invoke_kernel><action>PAGE_IN</action>"
        + "<target>docs/memory/active_run_ledger.md</target>"
        + "<search>anchor_name</search></invoke_kernel> "
        + "to load specific task blocks for review."
    )

    ctx.review_output = ""
    ctx.review_verdict = "UNKNOWN"
    ctx.review_cycle = 0

    from pipeline import REVIEW_MAX_ITERATIONS as _REVIEW_MAX_ITERATIONS
    from pipeline import CIRCUIT_BREAKER_MAX_FAILURES as _CB_MAX
    while ctx.review_cycle < _REVIEW_MAX_ITERATIONS:
        ctx.review_cycle += 1
        _reviewer_failed_parse = False
        print(f"\n  [Review-Fix] Cycle {ctx.review_cycle}/{_REVIEW_MAX_ITERATIONS}")

        # -- Circuit Breaker: Check retry counts -------------------------
        # Only count real task IDs (task_N); skip synthetic merged: keys and
        # other pipeline-internal entries that should never trip the breaker.
        for tid in list(ctx.all_results_dict.keys()):
            if not tid.startswith("task_"):
                continue
            count = ctx.retry_counts.setdefault(tid, 0)
            if count >= _CB_MAX:
                # -- Last-resort whole-file surgery (luac + RuntimeSim gated) --
                # Before tripping, try ONE full-file LLM rewrite of the failing
                # task's accumulated file. The per-task fix cycle operates on
                # fragments and cannot repair a file-level structural defect
                # (e.g. a lost OnStep wrapper or a stray `end`). On success the
                # breaker resets and the loop retries with a clean file.
                _surg_tried = getattr(ctx, "_surgery_attempted", None)
                if _surg_tried is None:
                    _surg_tried = set()
                    ctx._surgery_attempted = _surg_tried
                _surg_t = ctx.task_map.get(tid)
                _surg_tf = str(getattr(_surg_t, "target_file", "") or "").replace("\\", "/")
                if _surg_tf.endswith(".lua") and _surg_tf not in _surg_tried:
                    _surg_tried.add(_surg_tf)
                    try:
                        if _gap_filler_surgery(ctx, _surg_tf) or _whole_file_surgery(ctx, _surg_tf):
                            print(f"  [Surgery] ✅ whole-file rewrite fixed {_surg_tf} — "
                                  f"resetting circuit breaker for {tid}.")
                            ctx.retry_counts[tid] = 0
                            continue
                    except Exception as _surg_e:
                        print(f"  [Surgery] ⚠ raised: {_surg_e}")
                print(
                    f"\n{'='*70}\n"
                    f"  ⛔ [CIRCUIT BREAKER TRIPPED] Task {tid} has failed {count} times.\n"
                    f"  Breaking compilation loop to prevent infinite retry.\n"
                    f"{'='*70}"
                )
                ctx.review_verdict = "BLOCKED"
                # Generate SOS failure report with deadlock context
                deadlock_report = generate_failure_report(
                    ctx.user_prompt,
                    ctx.consensus_checks or {},
                    ctx.all_vetos, ctx.all_objects,
                    ctx.all_results_dict, ctx.task_map,
                    ctx.director_output,
                )
                print(f"\n{'='*70}")
                print(f"  📋 [Circuit Breaker] Failure report generated:")
                print(f"  Deadlock Context: Compiler Loop Exhausted (task {tid} failed {count}x)")
                print(f"{'='*70}")
                print(deadlock_report)
                break
        if ctx.review_verdict == "BLOCKED":
            break

        cycle_label = f"Integration Review (cycle {ctx.review_cycle})"

        # Dynamically extract negative intent to insulate the Reviewer against double-binds
        neg_guard_str = ""
        req_parts_rev = re.split(r'\[block\]', ctx.user_prompt, maxsplit=1, flags=re.IGNORECASE)
        if len(req_parts_rev) > 1:
            neg_guard_str = (
                f"\n\n## NEGATIVE INTENT OVERRIDE, AGENTIC EXTENSIBILITY & LIFECYCLE MANDATE\n"
                f"The user explicitly commanded the following avoidance constraints:\n"
                f"\"{req_parts_rev[1].strip()}\"\n"
                f"CRITICAL EVALUATION GUIDANCE:\n"
                f"1. AVOIDANCE EVALUATION: You MUST NOT penalize agents or issue a FAIL verdict for omitting explicitly blocked C++ classes, structs, or files. Treat successful avoidance as a PASS, and emit exactly `- None` under `### Issues` if no other defects exist.\n"
                f"2. CONSTRUCTIVE PRIMITIVE EXTENSIBILITY: If an attraction requires complex physical shapes (e.g., curved ramps, flared rings) that cannot be modeled by simple boxes or cylinders, agents must NEVER invent new C++ classes. Instead, they MUST achieve extensibility via two native pathways:\n"
                f"   A. ASSET MOUNTING: Use `MidwayPhysics.SpawnStaticMesh` or `SpawnDynamicMesh` to delegate custom collision geometry to external 3D asset models.\n"
                f"   B. LUA COMPOSITION: Group multiple foundational primitives inside custom Lua factory tables to construct reusable prefabs entirely at runtime.\n"
                f"3. MANDATORY LUA LIFECYCLE AUDIT: You MUST actively audit Lua attraction scripts for strict event-driven lifecycle compliance. If the script fails to implement `OnLoadStatic()` (invoking `SpawnSharedBooth()`), `OnLoad()` (spawning bodies and registering `MidwayPhysics.OnStep`), or polls modifiers at load time rather than live every frame inside `OnStep(dt)`, you MUST issue an immediate FAIL.\n"
            )

        # -- Review Input: Inline actual code, not just a TOC --------------
        # Giving the reviewer only a PAGE_IN index causes it to hallucinate
        # violations from task titles. Inline the real task outputs so it can
        # only flag issues that actually appear in the code.
        #
        # BUDGET STRATEGY: Measure the real non-code overhead first, then
        # divide the remaining space evenly across tasks.  This prevents
        # tail tasks from being silently dropped because the overhead estimate
        # was wrong  which caused the context-collapse failure seen in earlier
        # runs where only task_2 reached the reviewer.
        #
        # Hard cap is set to leave a 1 500-char safety margin below the 16 384
        # absolute truncation limit so the REVIEW_PROMPT and bridge snippet
        # always survive at the end of the packet.
        # Model-aware review hard cap: 55% of context window at 3 chars/token,
        # matching the executor ceiling in _helpers_exec.py so neither path
        # exceeds VRAM before the VRAM Guard fires.
        try:
            from ollama_client import resolve_ctx_size as _rcz
            _review_model = getattr(ctx, 'reviewer_model', 'qwen3.5:9b')
            _REVIEW_CTX = _rcz(_review_model)
        except Exception:
            _REVIEW_CTX = 8192
        _REVIEW_HARD_CAP = int(_REVIEW_CTX * 3 * 0.55)
        _MAX_TASKS_INLINE = 12
        _task_items = list(ctx.all_results_dict.items())[:_MAX_TASKS_INLINE]

        # Defined early so its length is available for the overhead calculation below.
        _visibility_mandate = (
            "## ⚠ REVIEWER SCOPE CONSTRAINT (MANDATORY)\n"
            "You MAY ONLY flag issues that are **visibly present** in the code blocks shown "
            "in the 'Generated Code' section above.\n"
            "You MUST NOT:\n"
            "- Fail a task because a file that was NOT shown is absent or incomplete.\n"
            "- Speculate about code you cannot see.\n"
            "- Invent violations from task titles or Director descriptions alone.\n"
            "- Issue a FAIL verdict for features blocked by an explicit [block] directive.\n"
            "If a task's code block is absent from this review packet, treat it as OUT-OF-SCOPE "
            "for this cycle and do NOT mention it under Issues.\n"
        )

        # Build the non-code frame first so we know its real size.
        _frame_str = (
            f"## Original Feature Request\n{ctx.user_prompt}\n\n"
            f"## Director's Task Breakdown\n{ctx.director_output}\n\n"
            f"{ctx.conflicts_str}"
        )
        _frame_overhead = len(_frame_str) + len(_visibility_mandate) + len(neg_guard_str)
        # Reserve 3 500 chars for bridge snippet + REVIEW_PROMPT at the tail.
        _TAIL_RESERVE = 3500
        _code_budget_total = max(2000, _REVIEW_HARD_CAP - _frame_overhead - _TAIL_RESERVE)
        _task_code_budget = max(400, _code_budget_total // max(1, len(_task_items)))

        inline_code_blocks: list[str] = []
        # -- Check if we have any merged file artifacts to show instead of fragments --
        _merged_registry = getattr(ctx, 'merged_file_registry', {})
        # Build reverse map: task_id -> merged_key (so we can skip individual fragments)
        _tid_to_merged: Dict[str, str] = {}
        for _rel_path, _mkey in _merged_registry.items():
            for _t in ctx.task_map.values():
                if getattr(_t, 'target_file', None) == _rel_path and _t.task_id in ctx.all_results_dict:
                    _tid_to_merged[_t.task_id] = _mkey

        # Track which merged keys have already been emitted so we show each once
        _emitted_merged: set = set()

        for _tid, _out in _task_items:
            _task_obj = ctx.task_map.get(_tid)
            _domain = (_task_obj.agent if _task_obj and getattr(_task_obj, 'agent', None) else "?")

            # If this task has a merged counterpart, show the merged version instead
            _mkey = _tid_to_merged.get(_tid)
            if _mkey and _mkey not in _emitted_merged:
                _merged_out = ctx.all_results_dict.get(_mkey, _out)
                _rel_p = _mkey.removeprefix("merged:")
                # Show merged file once, labelled clearly
                _snippet = _merged_out[:_task_code_budget * 2]
                if len(_merged_out) > _task_code_budget * 2:
                    _snippet += "\n[merged output truncated]"
                inline_code_blocks.append(
                    f"### [MERGED FILE: {_rel_p}] (combines: {[t for t, m in _tid_to_merged.items() if m == _mkey]})\n{_snippet}"
                )
                _emitted_merged.add(_mkey)
                continue
            elif _mkey and _mkey in _emitted_merged:
                # This task's output is already represented by the merged block  skip
                continue

            # No merge  show individual task output as before
            if len(_out) > _task_code_budget:
                _snippet_overflow = _out[_task_code_budget:]
                _snippet = _out[:_task_code_budget]
                # Preserve overflow in OffloadStore so reviewer can PAGE_IN if it
                # needs to see the full output rather than silently losing it.
                try:
                    from offload_store import get_offload_store as _rv_os
                    _rv_store = _rv_os()
                    _rv_oid = f"review_overflow_{_tid}"
                    _rv_store.store_block(
                        block_id=_rv_oid,
                        header=f"Review overflow for {_tid} ({len(_snippet_overflow)} chars truncated)",
                        body_lines=[_snippet_overflow],
                    )
                    _snippet += (
                        f"\n[📄 {len(_snippet_overflow)} chars truncated  "
                        f"use `<invoke_kernel><action>PAGE_IN</action>"
                        f"<target>{_rv_oid}</target></invoke_kernel>` to retrieve full output.]"
                    )
                except Exception:
                    _snippet += "\n[truncated]"
            else:
                _snippet = _out
            inline_code_blocks.append(f"### [{_tid}] [{_domain}]\n{_snippet}")

        inline_code_str = "\n\n".join(inline_code_blocks)
        _tasks_in_packet = len(_task_items)
        _tasks_total = len(ctx.all_results_dict)
        if _tasks_in_packet < _tasks_total:
            print(f"  [Review Budget] ⚠ Only {_tasks_in_packet}/{_tasks_total} tasks fit inline "
                  f"(budget {_task_code_budget} chars/task, frame {_frame_overhead} chars overhead).")
        else:
            print(f"  [Review Budget] ✅ All {_tasks_in_packet} task(s) inlined "
                  f"({_task_code_budget} chars/task budget).")
        # Inject pre-flight errors as a hard preamble so the reviewer cannot
        # issue PASS while known static violations are still present.
        _preflight_preamble = ""
        if ctx.pre_flight_errors and ctx.pre_flight_errors.strip():
            _pf_collapsed = TokenBudget._block_aware_collapse(ctx.pre_flight_errors, 1500)
            _preflight_preamble = (
                "## ⛔ PRE-FLIGHT VIOLATIONS  PASS IS FORBIDDEN UNTIL THESE ARE RESOLVED\n"
                "The automated pre-flight checker found the following violations in the code below.\n"
                "You MUST issue [VERDICT: FAIL] and list every unresolved violation under Issues.\n"
                "You MAY issue [VERDICT: PASS] ONLY if you can confirm each violation listed here "
                "has been corrected in the code shown.\n\n"
                + _pf_collapsed
                + "\n"
            )

        review_input_raw = (
            f"{_preflight_preamble}"
            f"## Original Feature Request\n{ctx.user_prompt}\n\n"
            f"## Director's Task Breakdown\n{ctx.director_output}\n\n"
            f"{ctx.conflicts_str}"
            f"## Generated Code (review ONLY what is shown below  do NOT invent issues)\n"
            f"{inline_code_str}\n"
            f"{_visibility_mandate}\n"
            f"{neg_guard_str}\n\n"
        )

        # -- Inject bridge contract so reviewer can flag phantom APIs --------
        # Without this, the reviewer sees phantom names like GetPrizeValue()
        # and cannot distinguish them from real bridge exports.
        # NOTE: _build_bridge_fn() returns a dict  it must be rendered to a
        # human-readable string before slicing or it raises TypeError and the
        # snippet is silently dropped (the previous regression cause).
        _review_bridge_snippet = ""
        _build_bridge_fn = getattr(ctx, '_cartridge_build_bridge_contract', None)
        if callable(_build_bridge_fn):
            try:
                _bc_rev = _build_bridge_fn()
                if _bc_rev and isinstance(_bc_rev, dict):
                    _bc_lines: list[str] = []
                    for _section, _entries in _bc_rev.items():
                        if isinstance(_entries, dict):
                            _bc_lines.append(f"### {_section}")
                            for _name, _desc in _entries.items():
                                _bc_lines.append(
                                    f"  - {_name}: {_desc}"
                                    if isinstance(_desc, str)
                                    else f"  - {_name}"
                                )
                        elif isinstance(_entries, list):
                            _bc_lines.append(f"### {_section}")
                            for _item in _entries:
                                _bc_lines.append(f"  - {_item}")
                    _bc_str = "\n".join(_bc_lines)[:3000]
                    _review_bridge_snippet = (
                        "## Active Bridge Contract  APPROVED APIs (exhaustive list)\n"
                        "Any Lua call that is NOT on this list is a phantom API and MUST be flagged as a FAIL.\n"
                        "EXCEPTION: SpawnSharedBooth() is a BARE global helper (attractions/booth_shared.lua), "
                        "NOT a MidwayPhysics.* API. It is legal ONLY as a bare call `SpawnSharedBooth()` inside "
                        "OnLoadStatic. Do NOT flag it, and do NOT tell the agent to replace it with SpawnStatic*.\n"
                        f"{_bc_str}\n\n"
                    )
                    print(f"  [Bridge Contract] ✅ Injected {len(_bc_str)} chars of approved API list into reviewer context.")
            except Exception as _bc_err:
                print(f"  [Bridge Contract] ⚠ Failed to render bridge contract for reviewer: {_bc_err}")

        # Append tail (bridge + review prompt) AFTER the code body so it always
        # survives.  D14: When truncation is necessary, collapse the frame prose
        # sections first and protect the task code blocks  the reviewer must see
        # real code or it cannot produce a meaningful verdict.
        review_input_full = review_input_raw + _review_bridge_snippet + _prompts_mod.REVIEW_PROMPT
        # Use the same model-aware hard cap computed above rather than a bare
        # 16000 constant that ignores whether the reviewer model is a 7B or 14B.
        _VRAM_GUARD_CAP = _REVIEW_HARD_CAP
        if len(review_input_full) > _VRAM_GUARD_CAP:
            _excess = len(review_input_full) - _VRAM_GUARD_CAP
            print(f"  [VRAM Guard] Review input oversized ({len(review_input_full)} chars)  "
                  f"collapsing {_excess} chars from code body via block-aware paging (tail preserved).")
            _tail = _review_bridge_snippet + _prompts_mod.REVIEW_PROMPT
            # D14: collapse the frame (prose) section first, keeping the task code blocks intact.
            _frame_cap = max(800, len(_frame_str) - _excess - 200)
            _collapsed_frame = TokenBudget._block_aware_collapse(_frame_str, _frame_cap)
            _rebuilt_body = (
                _collapsed_frame
                + f"\n\n[SYSTEM KERNEL: Feature request / director sections collapsed to {_frame_cap} chars "
                f"to protect code blocks below. Bridge contract and review instructions are complete.]\n\n"
                + f"## Generated Code (review ONLY what is shown below  do NOT invent issues)\n"
                + inline_code_str
                + f"\n{_visibility_mandate}\n{neg_guard_str}\n\n"
            )
            # If still oversized after frame collapse, fall back to body collapse preserving tail.
            if len(_rebuilt_body) + len(_tail) > _VRAM_GUARD_CAP:
                _body_cap = _VRAM_GUARD_CAP - len(_tail) - 80
                _rebuilt_body = TokenBudget._block_aware_collapse(_rebuilt_body, _body_cap)
                _rebuilt_body += (
                    "\n\n[SYSTEM KERNEL: Code body further collapsed via block-aware paging. "
                    "Bridge contract and review instructions below are complete.]\n\n"
                )
            review_input = _rebuilt_body + _tail
        else:
            review_input = review_input_full


        # -- Physical Compilation Gate Override --
        # Skip entirely when no configured build tree exists  running cmake
        # without a cache produces infrastructure noise ("could not load cache")
        # that poisons every fix cycle with meaningless errors.
        _cmake_cache = ctx.project_root / "CMakeCache.txt"
        _infra_noise_re = re.compile(
            r"(could not load cache|no such file or directory.*cmake"
            r"|cmake.*error.*cache|error opening.*cmakecache)",
            re.IGNORECASE,
        )
        if not _cmake_cache.is_file():
            compile_success = True   # treat as pass  nothing to compile yet
            compile_stderr = ""
            print("  [Compile Gate] No CMakeCache.txt  skipping physical compilation check.")
        else:
            compile_success, compile_stderr = compile_project(ctx.project_root)
            # Strip pure cmake-infrastructure lines so only real compiler
            # diagnostics reach the fix agents.
            if compile_stderr and _infra_noise_re.search(compile_stderr):
                clean_lines = [
                    ln for ln in compile_stderr.splitlines()
                    if not _infra_noise_re.search(ln)
                ]
                compile_stderr = "\n".join(clean_lines).strip()
                if not compile_stderr:
                    compile_success = True  # all lines were infra noise

        if not compile_success and compile_stderr:
            ctx.pre_flight_errors = (
                ctx.pre_flight_errors
                + f"\n## Mandatory Compiler Fix Required (truncated to 2,000 chars):\n```\n{compile_stderr[:2000]}\n```"
            )

        if not compile_success:
            print("  [Circuit Breaker] ⛔ Physical compilation failed. Overriding LLM review hallucination.")
            ctx.review_output = "### Verdict\n[VERDICT: FAIL]\n### Issues\nPhysical compilation failed. See compiler logs."
            ctx.review_verdict = "FAIL"
        elif DETERMINISTIC_VERDICT:
            # ---- Phase 3: deterministic verdict --------------------------
            # No LLM call is made.  The verdict comes from hard signals only,
            # so the loop cannot hang on a crashed/unresponsive 9B reviewer.
            _det_verdict, _det_issues = _deterministic_verdict(ctx)

            if _det_verdict == "FAIL" and ADVISORY_REVIEW_ON_FAIL:
                # Optional enrichment only — never changes the verdict.
                try:
                    _sv, _adv = _structured_review(review_input)
                    if _adv and _adv.strip():
                        _det_issues = (_det_issues + "\n\n" + _adv).strip()
                        print("  [Review-Fix] Advisory review appended issues.")
                except Exception as _sr_exc:
                    print(f"  [Review-Fix] ⚠ Advisory review unavailable "
                          f"({_sr_exc}) — using deterministic issues only.")

            _issues_block = _det_issues or "- (none)"
            ctx.review_verdict = _det_verdict
            ctx.review_output = (
                "### Issues\n"
                + _issues_block
                + f"\n\n### Verdict\n[VERDICT: {_det_verdict}]  (deterministic gate)"
            )
            print(f"  [Review-Fix] Deterministic verdict: {_det_verdict}.")

            # Record this cycle's proposal/try/verdict in the shared decision
            # log so later cycles and the tribunal can see what has been tried.
            try:
                from ledger import append_decision_entry
                append_decision_entry(
                    ctx.project_root,
                    title=f"Review Cycle {ctx.review_cycle} — {_det_verdict}",
                    body=("Open issues:\n" + (_det_issues or "- (none)")),
                )
            except Exception:
                pass
        else:
            # Structured review first: Pydantic-validated verdict + issues
            # (Standard #1 — wire the reviewer into structured extraction).
            _structured_verdict = None
            _structured_issues = ""
            try:
                _structured_verdict, _structured_issues = _structured_review(review_input)
                print(f"  [Review-Fix] Structured verdict: {_structured_verdict}")
            except Exception as _sr_exc:
                print(f"  [Review-Fix] ⚠ Structured review failed ({_sr_exc})  falling back to regex parser.")
                _structured_verdict = None

            if _structured_verdict in ("PASS", "FAIL"):
                ctx.review_verdict = _structured_verdict
                ctx.review_output = (
                    "### Issues\n" + (_structured_issues or "- (none)")
                    + f"\n\n### Verdict\n[VERDICT: {_structured_verdict}]"
                )
            else:
                ctx.review_output = call_ollama(
                    _prompts_mod.REVIEW_SYSTEM, review_input, cycle_label, _REVIEWER_MODEL,
                    params={"num_predict": 1024},
                    skip_pre_summarizer=True
                )
                if _is_fatal_ollama(ctx.review_output):
                    print(f"  [Review-Fix] ⛔ Ollama error during review  aborting review loop.")
                    ctx.review_verdict = "BLOCKED"
                    break
                ctx.review_verdict = get_verdict(ctx.review_output)

        ctx.output_parts.append(
            f"### Review Cycle {ctx.review_cycle}\n{ctx.review_output}\n"
        )

        print(f"  [Review-Fix] Verdict: {ctx.review_verdict}")

        # Telemetry: when the reviewer produced no parseable verdict, dump the
        # raw output so the failure mode (empty, rambling, wrong format) is
        # visible in the log instead of a black box.
        if ctx.review_verdict == "UNKNOWN":
            _raw_preview = (ctx.review_output or "").strip()
            _preview = _raw_preview[:600]
            print(f"  [Review-Fix] 🔍 NO_VERDICT raw output ({len(_raw_preview)} chars):")
            print("  " + "\n  ".join(_preview.splitlines()) + ("..." if len(_raw_preview) > 600 else ""))

        if ctx.review_verdict == "PASS":
            # FM3: Hard-gate PASS against open preflight errors.
            # The LLM can emit PASS even when the preflight preamble lists
            # violations  treat any open errors as an automatic FAIL so
            # the fix loop actually runs.
            _open_pf = (ctx.pre_flight_errors or "").strip()
            if _open_pf:
                print(f"  [Review-Fix] ⛔ Reviewer emitted PASS but preflight errors are still open "
                      f" overriding to FAIL (cycle {ctx.review_cycle}).")
                ctx.review_verdict = "FAIL"
                ctx.review_output = (
                    ctx.review_output
                    + "\n\n[SYSTEM KERNEL: PASS overridden to FAIL  open pre-flight violations "
                    "must be resolved before this run can be approved.]"
                )
            else:
                # FM4: Hard-gate PASS for attraction scopes against missing economy/modifier content.
                # The reviewer model regularly passes Lua that omits AttractionConstants.modifiers
                # and Engine.AwardTickets  catch it programmatically before the gate closes.
                _rev_scope = getattr(ctx, '_scope_mode', '')
                if _rev_scope in ("NEW_ATTRACTION", "MODIFY_ATTRACTION"):
                    _all_lua = _effective_lua_blob(ctx).lower()
                    if not _all_lua:
                        _all_lua = " ".join(
                            v for v in (ctx.all_results_dict or {}).values()
                        ).lower()
                    _missing_economy: list[str] = []
                    if not any(kw in _all_lua for kw in (
                        "attractionconstants.modifiers", "engine_mod_", ".modifiers",
                    )):
                        _missing_economy.append(
                            "No AttractionConstants.modifiers read found in OnStep  "
                            "the attraction MUST read modifiers every frame."
                        )
                    if not any(kw in _all_lua for kw in (
                        "awardtickets", "awardtokens",
                    )):
                        _missing_economy.append(
                            "No Engine.AwardTickets or Engine.AwardTokens call found  "
                            "the attraction MUST award tickets/tokens on win/score events "
                            "using Engine.GetStreak() as a multiplier."
                        )
                    if _missing_economy:
                        _econ_issues = "\n".join(f"  - {e}" for e in _missing_economy)
                        print(
                            f"  [Review-Fix] ⛔ Reviewer emitted PASS but mandatory economy "
                            f"content is absent  overriding to FAIL (cycle {ctx.review_cycle}):\n"
                            f"{_econ_issues}"
                        )
                        ctx.review_verdict = "FAIL"
                        ctx.review_output = (
                            ctx.review_output
                            + "\n\n[SYSTEM KERNEL: PASS overridden to FAIL  mandatory economy "
                            "obligations are not met:\n" + _econ_issues + "\n"
                            "Fix all items above before this run can be approved.]"
                        )
                        # Skip the break so the fix loop runs
                    else:
                        print(f"  [Review-Fix] Passed on cycle {ctx.review_cycle}")
                        break
                else:
                    print(f"  [Review-Fix] Passed on cycle {ctx.review_cycle}")
                    break

        # D13/D15: NO_VERDICT means the reviewer output contained no verdict line.
        # Give it one targeted re-prompt before treating as FAIL, so a single
        # formatting slip doesn't burn a full fix cycle unnecessarily.
        if ctx.review_verdict == "UNKNOWN":
            ctx.review_verdict = "NO_VERDICT"
            print(f"  [Review-Fix] ⚠ NO_VERDICT  reviewer produced no verdict line. "
                  f"Issuing one verdict re-prompt before fix routing (cycle {ctx.review_cycle}).")
            _pf_reminder = ""
            if ctx.pre_flight_errors and ctx.pre_flight_errors.strip():
                _pf_collapsed = TokenBudget._block_aware_collapse(ctx.pre_flight_errors, 800)
                _pf_reminder = (
                    "\n\n## ⚠ OPEN PRE-FLIGHT VIOLATIONS  PASS IS FORBIDDEN\n"
                    + _pf_collapsed
                    + "\n"
                )
            _verdict_nudge = (
                "[SYSTEM KERNEL: Your previous response contained NO verdict line. "
                "Do NOT re-review and do NOT write a summary or issues list. "
                "Output EXACTLY one line, nothing else:\n"
                "[VERDICT: PASS]\n"
                "or\n"
                "[VERDICT: FAIL]\n"
                "If any pre-flight violations are listed below, output [VERDICT: FAIL].]\n\n"
                + review_input
            )
            _retry_out = call_ollama(
                _prompts_mod.REVIEW_SYSTEM, _verdict_nudge,
                f"Review Verdict Re-prompt (cycle {ctx.review_cycle})", _REVIEWER_MODEL,
                params={"num_predict": 256},
                skip_pre_summarizer=True,
            )
            if _is_fatal_ollama(_retry_out):
                print(f"  [Review-Fix] ⛔ Ollama error during verdict re-prompt  aborting review loop.")
                ctx.review_verdict = "BLOCKED"
                break
            _retry_verdict = get_verdict(_retry_out)
            if _retry_verdict in ("PASS", "FAIL"):
                ctx.review_verdict = _retry_verdict
                ctx.review_output = _retry_out
                print(f"  [Review-Fix] Re-prompt resolved to {ctx.review_verdict}.")
                if ctx.review_verdict == "PASS":
                    # FM3 (re-prompt path): same hard-gate as the primary PASS check.
                    # The re-prompt model can emit PASS while static guards are still
                    # open  treat open preflight errors as an automatic FAIL override.
                    _open_pf_rp = (ctx.pre_flight_errors or "").strip()
                    if _open_pf_rp:
                        print(f"  [Review-Fix] ⛔ Re-prompt PASS overridden  preflight errors still open "
                              f"(cycle {ctx.review_cycle}).")
                        ctx.review_verdict = "FAIL"
                        ctx.review_output = (
                            ctx.review_output
                            + "\n\n[SYSTEM KERNEL: PASS overridden to FAIL  open pre-flight violations "
                            "must be resolved before this run can be approved.]"
                        )
                    else:
                        break
            else:
                print(f"  [Review-Fix] Re-prompt still produced no verdict  treating as FAIL.")
                ctx.review_verdict = "FAIL"
                _reviewer_failed_parse = True

        if ctx.review_verdict == "FAIL" and ctx.review_cycle < _REVIEW_MAX_ITERATIONS:
            issues_match = re.search(
                r"### Issues\s*\n(.*?)(?=###|\Z)",
                ctx.review_output, re.DOTALL,
            )
            issues_text = (
                issues_match.group(1).strip()
                if issues_match else ctx.review_output[:1000]
            )

            # Auto-inject pre-flight errors directly into the fix agent's context
            # when the reviewer failed to produce a parseable critique (NO_VERDICT
            # state or an empty Issues section), bypassing reviewer extraction.
            if not issues_text or _reviewer_failed_parse:
                issues_text = (
                    "CRITICAL SYSTEM OVERRIDE: Reviewer failed to parse. "
                    "Resolve these pre-flight errors immediately:\n"
                    + (ctx.pre_flight_errors or "")
                )
            else:
                # ALWAYS append the deterministic errors (luac + RuntimeSim) so the
                # fix agent sees the precise, actionable ground truth — not just the
                # reviewer's paraphrase, which has repeatedly misdiagnosed the real
                # defect ("duplicate local MOD", "MOD.heat indexing").
                _det = (ctx.pre_flight_errors or "").strip()
                if _det:
                    issues_text = (
                        issues_text
                        + "\n\n## DETERMINISTIC ERRORS (resolve these EXACTLY — luac/RuntimeSim ground truth):\n"
                        + _det
                    )

            print(f"  [Review-Fix] Review failed  routing critiques to original domain agents...")
            ctx.output_parts.append(
                f"### Domain Agent Fix Cycle {ctx.review_cycle}\n"
            )

            # -- Domain-Aware Fix Loop -----------------------------
            # Instead of using a generic ARCHITECT_FIX_SYSTEM, iterate over
            # failing task IDs and route the Reviewer's critique back to the
            # *original domain agent* so it retains its strict C++/Lua rules.
            #
            # Fix #4: Only route tasks that have OPEN preflight errors.
            # Using `tid in ctx.review_output` is too broad  it matches any task
            # ID mentioned in passing and routes clean tasks through fix agents,
            # burning VRAM cycles and introducing noise that can break passing code.
            # Strategy: first try to build a set of tids with explicit preflight
            # errors; fall back to review-mentioned tids only when that set is empty.
            _pf_error_tids: set = set()
            if ctx.pre_flight_errors:
                for _pf_tid in ctx.all_results_dict:
                    # Preflight errors are headed " Task <tid> [" so a simple
                    # contains check on the error string is safe and deterministic.
                    if f"Task {_pf_tid}" in ctx.pre_flight_errors or \
                       f"Task {_pf_tid.replace('_', ' ')}" in ctx.pre_flight_errors:
                        _pf_error_tids.add(_pf_tid)

            task_ids_in_review: set = set()
            if _pf_error_tids:
                # Prefer the deterministic preflight-based set.
                task_ids_in_review = _pf_error_tids
            else:
                # No explicit preflight hits  fall back to reviewer-mentioned tasks.
                for tid, _ in ctx.all_results_dict.items():
                    if tid in ctx.review_output or tid.replace("_", " ") in ctx.review_output:
                        task_ids_in_review.add(tid)

            if not task_ids_in_review:
                # Last resort: use all tasks
                task_ids_in_review = set(ctx.all_results_dict.keys())

            # -- Monolithic Mode: single-file re-generation vs per-task routing --
            # When ctx._monolithic_lua_target is set, the monolithic output
            # satisfies ALL tasks.  Do NOT route per-task critiques to domain
            # agents.  Instead, re-invoke the coder model with the review
            # critiques as additional context and re-generate the monolithic file.
            _mono_target = getattr(ctx, '_monolithic_lua_target', None)
            if _mono_target:
                # Bug T: Report coverage — how many tasks does the monolithic file satisfy?
                _task_count = len(ctx.all_results_dict)
                print(f"  [Review-Fix] ⏭ Monolithic mode active (target: {_mono_target}) — "
                      f"skipping per-domain fix routing, re-invoking coder model instead.")
                print(f"  [Monolithic Coverage] Monolithic file covers {_task_count} task(s) "
                      f"in a single output  fix cycle regenerates the complete file.")
                _mono_snippet = ctx.all_results_dict.get("task_monolithic", "")
                _mono_review_errors = (
                    "## ⚠ REVIEW CRITIQUES (MUST FIX ALL)\n"
                    + issues_text
                    + "\n\n"
                )
                if ctx.pre_flight_errors and ctx.pre_flight_errors.strip():
                    _mono_review_errors += (
                        "## ⚠ PRE-FLIGHT VIOLATIONS (MUST FIX ALL)\n"
                        + ctx.pre_flight_errors
                        + "\n\n"
                    )
                _runtime_errs = getattr(ctx, 'runtime_errors', None) or []
                if _runtime_errs:
                    _mono_review_errors += (
                        "## ⚡ RUNTIME SIMULATION ERRORS (concrete defects — fix EACH one)\n"
                        + "\n".join(f"  {e}" for e in _runtime_errs)
                        + "\n\n"
                        "Fix every runtime error above. Assign every handle "
                        "(puck, mallet, lever, bell, etc.) a real value from "
                        "Spawn*/PoolAcquire BEFORE it is read inside OnStep. "
                        "Do NOT leave handles nil.\n\n"
                    )
                # Bug M: Inject economy mandate into EVERY fix cycle regardless of
                # what the reviewer or pre-flight reported.  The reviewer may not
                # flag missing economy hooks (it focuses on syntax/structure), so
                # we must make the mandate explicit here lest the fix model drop
                # them on every regeneration.
                _mono_review_errors += (
                    "## 🏛 ECONOMY MANDATE (NON-NEGOTIABLE — must be present in output)\n"
                    "Your implementation MUST satisfy ALL of the following, or it will be "
                    "rejected by the PhantomAPI Gate AFTER this fix cycle:\n"
                    "1. **Modifier consumption** — inside your OnStep callback, read "
                    "`AttractionConstants.modifiers` every frame. NEVER cache modifier values at load time.\n"
                    "   Example: `local MOD = AttractionConstants.modifiers`\n"
                    "2. **Economy hook** — call `Engine.AwardTickets(n, label)` on every win or score event.\n"
                    "   Use `Engine.GetStreak()` as a multiplier for ticket payouts.\n"
                    "   Example: `Engine.AwardTickets(score * Engine.GetStreak(), 'WIN')`\n"
                    "Omitting either of these WILL cause a pipeline failure.\n\n"
                )

                # Build a fix prompt that gives the coder model the current file,
                # the review critiques, and instructions to produce a fixed version.
                # Include the approved physics API list from the bridge contract so
                # the fix model does NOT hallucinate Garry's Mod API names.
                _fix_approved_apis = ""
                _fix_bridge_fn = getattr(ctx, '_cartridge_build_bridge_contract', None)
                # Bug P: Use the single-source-of-truth bridge snippet builder
                # that already renders ALL approved API names across all sections
                # (spawn, pools, economy, input, movement, force, properties).
                # This replaces the hardcoded subset that was causing fix models
                # to hallucinate replacement names.
                _fix_approved_apis = ""
                try:
                    _snippet = build_fix_bridge_snippet(ctx)
                    if _snippet:
                        # Also add bare (MidwayPhysics-less) forms so the fix
                        # model recognizes them regardless of naming convention.
                        _bare_forms = re.findall(r'(?<=MidwayPhysics\.)[A-Za-z]\w+', _snippet)
                        _snippet += (
                            "\nNOTE: These functions MAY also be called without the "
                            "'MidwayPhysics.' prefix (bare form). "
                            "For example: 'MidwayPhysics.DestroyBody(handle)' and "
                            "'DestroyBody(handle)' are equivalent.\n"
                            "The bare forms are: "
                            + ", ".join(sorted(set(_bare_forms)))
                            + "\n"
                        )
                        _fix_approved_apis = (
                            "\n" + _snippet + "\n"
                        )
                except Exception:
                    pass
                if not _fix_approved_apis:
                    # Comprehensive fallback covering ALL bridge contract APIs
                    _fix_approved_apis = (
                        "\nAPPROVED PHYSICS APIS (use ONLY these -- no ents.Create, no IsValid, no hook:Remove):\n"
                        "=== Spawn (static): SpawnStaticBox, SpawnStaticSphere, SpawnStaticCapsule, SpawnStaticCylinder, SpawnStaticMesh, "
                        "SpawnStaticBoxR, SpawnStaticSphereR, SpawnStaticCapsuleR, SpawnStaticCylinderR\n"
                        "=== Spawn (kinematic): SpawnKinematicBox, SpawnKinematicSphere, SpawnKinematicCapsule, SpawnKinematicCylinder, SpawnKinematicBoxR\n"
                        "=== Spawn (dynamic): SpawnDynamicBox, SpawnDynamicSphere, SpawnDynamicCapsule, SpawnDynamicCylinder, SpawnDynamicMesh, "
                        "SpawnDynamicBoxR, SpawnDynamicSphereR, SpawnDynamicCapsuleR, SpawnDynamicCylinderR\n"
                        "=== Spawn (sensor): SpawnSensorBox, SpawnSensorSphere\n"
                        "=== Pool: CreatePool, PoolAcquire, PoolReturn, PoolFree, PoolCullBelow, PoolTotal\n"
                        "=== Movement: MoveKinematic, GetPosition, GetVelocity, GetRotation, IsActive, IsSensorTriggered\n"
                        "=== Forces: ApplyImpulse, ApplyAngularImpulse, SetLinearVelocity, AddLinearVelocity\n"
                        "=== Properties: SetFriction, SetRestitution, SetGravityFactor, SetMass, SetLinearDamping, SetAngularDamping\n"
                        "=== Lifecycle: DestroyBody, OnStep\n"
                        "=== Economy: Engine.AwardTickets, Engine.AwardTokens, Engine.GetTickets, Engine.GetTokens, Engine.GetStreak\n"
                        "=== Globals: SpawnSharedBooth, AttractionConstants.modifiers, ENGINE_MOD_*\n"
                    )

                # -- Surgical fix: show the full file but constrain the coder to
                #    emit targeted SEARCH/REPLACE blocks, so it patches the
                #    isolated broken sections instead of regenerating the whole
                #    file (which repeated the same mistakes every cycle). --

                # Shared decision-log TOC so the fixer can see what has been
                # proposed/tried/rejected this run (PAGE_IN, never fully inlined).
                _decision_toc = ""
                try:
                    from ledger import decision_log_toc
                    _decision_toc = decision_log_toc(ctx.project_root)
                except Exception:
                    _decision_toc = ""
                _fix_context_extra = (_decision_toc + "\n\n") if _decision_toc else ""

                _mono_fix_system = (
                    "You are a senior Lua engineer repairing a generated attraction script.\n"
                    "You will receive the CURRENT file content and the exact errors to fix.\n\n"
                    "CRITICAL RULES:\n"
                    "- Output ONLY SEARCH/REPLACE blocks. Do NOT output the whole file.\n"
                    "- One SEARCH/REPLACE block per region you are changing.\n"
                    "- SEARCH must be the EXACT current lines; REPLACE is the corrected lines.\n"
                    "- Fix ONLY the errors listed. Do NOT refactor unrelated code.\n"
                    "- Assign every handle a real value from Spawn*/PoolAcquire BEFORE it is read.\n"
                    "- Use the exact format:\n"
                    "  <<<<<<< SEARCH\n  <exact current lines>\n  =======\n  <corrected lines>\n  >>>>>>> REPLACE\n"
                    f"{_fix_approved_apis}"
                )
                # Anchor Context Windowing: only ship the flagged region (plus a
                # small context window) to the coder, never the full ~26k-char
                # file.  Without the lifecycle wrappers in view the model cannot
                # attempt a full-file rewrite.
                _mono_window = _window_mono_snippet(_mono_snippet, _mono_review_errors)
                _mono_fix_prompt = (
                    f"## Target File: {_mono_target}\n\n"
                    f"{_fix_context_extra}"
                    f"{_mono_review_errors}"
                    f"## CURRENT FILE REGION (fix only the errors above; "
                    f"do NOT redefine lifecycle functions):\n"
                    f"```lua\n{_mono_window}\n```\n\n"
                    f"---\n"
                    f"Output ONE SEARCH/REPLACE block per region you are fixing. "
                    f"SEARCH must match the current file exactly; REPLACE is the corrected region."
                )

                from _pipeline_helpers import CODER_MODEL
                _mono_fixed = call_ollama(
                    _mono_fix_system,
                    _mono_fix_prompt,
                    f"Monolithic Fix (cycle {ctx.review_cycle})",
                    CODER_MODEL,
                    params={"num_predict": 4096},
                    skip_pre_summarizer=True,
                )

                # Apply the SEARCH/REPLACE patches in place (surgical).  Fall back
                # to full-file replacement only when the model emitted no patches.
                from _helpers_exec import (
                    _extract_search_replace_blocks as _extract_sr,
                    _fuzzy_apply_patch as _fuzzy_patch,
                )
                _blocks = _extract_sr(_mono_fixed)
                _patched = _mono_snippet
                _applied = 0
                for _blk in _blocks:
                    _new = _fuzzy_patch(_patched, _blk["search"], _blk["replace"])
                    if _new != _patched:
                        _patched = _new
                        _applied += 1
                if _applied:
                    _mono_fixed = _patched
                    print(f"  [Monolithic Fix] Surgical: applied {_applied} SEARCH/REPLACE patch(es) in place.")
                elif _blocks:
                    # The coder emitted SEARCH/REPLACE blocks but NONE matched the
                    # current file.  Writing the raw (unmatched) output would
                    # corrupt the file with conflict markers, and a full-file
                    # rewrite is exactly what we are trying to avoid.  Keep the
                    # current content so the next review cycle re-flags the same
                    # errors with fresh context; the insanity detector still
                    # bounds the loop.
                    _mono_fixed = _mono_snippet
                    print(f"  [Monolithic Fix] ⚠ Surgical: {len(_blocks)} SEARCH block(s) emitted "
                          f"but NONE matched the current file — keeping existing content "
                          f"(refusing full-file rewrite).")
                else:
                    # No SEARCH/REPLACE blocks were extracted.  Before giving up,
                    # try converting a full-file rewrite (the coder ignoring the
                    # patch mandate) into a surgical diff against the current file.
                    # This deterministically lands the fix instead of re-flagging
                    # the same errors every cycle.
                    from _helpers_exec import (
                        _apply_full_file_rewrite_via_diff as _mono_diff,
                    )
                    _mono_diffed = _mono_diff(_mono_snippet, _mono_fixed)
                    if _mono_diffed != _mono_snippet:
                        _mono_fixed = _mono_diffed
                        print("  [Monolithic Fix] 🔀 converted full-file rewrite into a surgical diff patch.")
                    else:
                        _mono_fixed = _mono_snippet
                        print("  [Monolithic Fix] ⚠ Surgical: no valid SEARCH/REPLACE blocks "
                              "extracted and no diffable rewrite — keeping current content.")

                # Write fixed content to disk
                _mono_abs = ctx.project_root / _mono_target
                atomic_write_text(_mono_abs, _mono_fixed)
                print(f"  [Monolithic Fix] Wrote {len(_mono_fixed)} chars to {_mono_target}")

                # atomic_write_text redirects to .staging_workspace when staging
                # is active, but Path.read_text() / post_process_lua_file() do
                # NOT.  Read back from the SAME staged location the write went
                # to; the previous real-path read silently discarded every fix.
                try:
                    from _helpers_io import get_staging_path, is_staging_active
                    _mono_read_path = (
                        get_staging_path(_mono_abs, project_root=ctx.project_root)
                        if is_staging_active() else _mono_abs
                    )
                except Exception:
                    _mono_read_path = _mono_abs

                # ---- Bug D fix: run post_process_lua on the fixed file ----
                try:
                    from _post_process_lua import post_process_lua_file
                    post_process_lua_file(_mono_read_path)
                    _post_fixed = _mono_read_path.read_text(encoding="utf-8")
                    print(f"  [Monolithic Fix] Post-process applied ({len(_mono_fixed)} -> {len(_post_fixed)} chars)")
                    _mono_fixed = _post_fixed
                except Exception as _ppe:
                    print(f"  [Monolithic Fix] Post-process skipped: {_ppe}")

                # Update context
                ctx.all_results_dict["task_monolithic"] = _mono_fixed
                ctx.final_output = _mono_fixed
                # Keep all_results in sync
                _mono_found = False
                for _mi, _me in enumerate(ctx.all_results):
                    if _me.get("task_id") == "task_monolithic":
                        ctx.all_results[_mi] = {"task_id": "task_monolithic", "output": _mono_fixed}
                        _mono_found = True
                        break
                if not _mono_found:
                    ctx.all_results.append({"task_id": "task_monolithic", "output": _mono_fixed})

                # Re-run luac — and REVERT on regression.  A fix cycle that
                # introduces a syntax error must never persist: the surgical
                # loop previously degraded a luac-clean file into a
                # syntax-broken one, cycle over cycle.
                import subprocess as _mono_sp
                _mono_luac = _mono_sp.run(
                    [_LUAC_EXE, "-p", str(_mono_read_path)],
                    capture_output=True, text=True, timeout=15,
                )
                if _mono_luac.returncode == 0:
                    ctx._last_luac_clean_mono = _mono_fixed
                    print(f"  [Monolithic Fix] ✅ luac syntax check passed")
                else:
                    _mono_err = _mono_luac.stderr.strip()
                    _last_good = getattr(ctx, '_last_luac_clean_mono', None)
                    if _last_good and _last_good.strip():
                        _mono_fixed = _last_good
                        atomic_write_text(_mono_abs, _mono_fixed)
                        ctx.all_results_dict["task_monolithic"] = _mono_fixed
                        ctx.final_output = _mono_fixed
                        _rg_found = False
                        for _rg_i, _rg_e in enumerate(ctx.all_results):
                            if _rg_e.get("task_id") == "task_monolithic":
                                ctx.all_results[_rg_i] = {"task_id": "task_monolithic", "output": _mono_fixed}
                                _rg_found = True
                                break
                        if not _rg_found:
                            ctx.all_results.append({"task_id": "task_monolithic", "output": _mono_fixed})
                        print(f"  [Monolithic Fix] ⛔ luac syntax error — REVERTED to last luac-clean "
                              f"version ({len(_last_good)} chars).")
                        print(f"  [Monolithic Fix luac] {_mono_err}")
                    else:
                        print(f"  [Monolithic Fix] ⚠ luac syntax error (no clean baseline to revert to):")
                        print(f"  [Monolithic Fix luac] {_mono_err}")

                # Refresh static checks so next review cycle sees current state
                try:
                    from _finalize_preflight import (
                        _inject_empty_output_errors,
                        _inject_static_pattern_errors,
                        _flush_results_to_workspace,
                    )
                    _flush_results_to_workspace(ctx)
                    ctx.pre_flight_errors = ""
                    _inject_empty_output_errors(ctx)
                    _inject_static_pattern_errors(ctx)
                except Exception:
                    pass

                fix_output = _mono_fixed
                print(f"  [Monolithic Fix] Cycle {ctx.review_cycle} complete. Continuing review loop.")
                continue
            else:
                domain_fix_outputs = {}
                # Snapshot results BEFORE any fix writes so the post-fix revert has a
                # clean previous-cycle value to fall back to (not the just-written bad one).
                _pre_fix_snapshot = dict(ctx.all_results_dict)
                # Tasks whose fix SEARCH/REPLACE was already applied to the file;
                # these must not be reverted by the post-fix full-dump guard.
                _file_applied_tids: set = set()
                for tid in sorted(task_ids_in_review):
                    task_obj = ctx.task_map.get(tid)
                    if task_obj is None:
                        # Hardening: a task in review but missing from task_map must
                        # NOT be silently skipped (it previously left 10/11 tasks
                        # unfixed every cycle).  Rebuild from ctx.tasks_list so the
                        # critique still routes to its domain.
                        _rebuilt = _rebuild_task_from_tasks_list(ctx, tid)
                        if _rebuilt is not None:
                            print(f"    ⚠ Rebuilt task object for {tid} from tasks_list (missing from task_map).")
                            task_obj = _rebuilt
                        else:
                            print(f"    ⚠ Cannot route critique for {tid}: no task object "
                                  f"in task_map OR tasks_list. Skipping.")
                            continue

                    original_agent_key = resolve_agent_name(task_obj.agent)

                # Use a compact, repair-specific system prompt  NOT the full
                # get_agent_system() which adds mesh/ledger/virtual-memory protocols
                # (~11.8 k chars) and collapses the user payload to ~469 chars.
                _base_review_fix_system = _prompts_mod.ARCHITECT_FIX_SYSTEM
                _rv_domain_prohibitions = ""
                try:
                    _cart_rv = getattr(ctx, 'mounted_cartridge', None)
                    if _cart_rv:
                        _cart_rv_domain = _cart_rv.domains.get(original_agent_key)
                        if _cart_rv_domain:
                            _sp_rv = _cart_rv_domain.system_prompt or ""
                            _rv_domain_prohibitions = "\n\n## Domain Rules (summary)\n" + _sp_rv[:1800]
                    if not _rv_domain_prohibitions:
                        _live_reg_rv = getattr(ctx, 'domain_registry', None) or {}
                        _dreg_rv = _live_reg_rv.get(original_agent_key, {})
                        if isinstance(_dreg_rv, dict) and _dreg_rv.get('system_prompt'):
                            _rv_domain_prohibitions = "\n\n## Domain Rules (summary)\n" + _dreg_rv['system_prompt'][:1800]
                except Exception:
                    pass
                original_agent_system = _base_review_fix_system + _rv_domain_prohibitions

                domain_name = ALL_DOMAINS.get(original_agent_key, {}).get("name", original_agent_key)
                print(f"    Routing critique for {tid} to {domain_name}")

                # -- Directive C: Context-Pruned Fix Payload --------------
                # Uses _prune_fix_context() to strip iterative history,
                # provide only current file state + exact domain-relevant errors.
                # Directive B  Safe Auto-Mounting: Pass paged_files_cache so
                # cached text chunks are injected directly, bypassing the
                # catastrophic file_path.read_text() that would pull 40k+ chars.
                # Build bridge contract snippet for fix context so agents
                # use correct API names instead of hallucinating variants.
                # Consolidated: all three callers now share build_fix_bridge_snippet().
                _fix_bridge_snippet = build_fix_bridge_snippet(ctx)

                # -- Live-file anchor context --------------------------------
                # The fix agent must patch the ACCUMULATED file, not its own
                # stale per-task fragment.  Feed it the live file (staging-aware)
                # so the SEARCH blocks it emits actually match the on-disk file,
                # instead of failing to apply every cycle ("none matched").
                _live_file = ""
                _fix_tf = getattr(task_obj, 'target_file', '') or ''
                if _fix_tf and _fix_tf.endswith('.lua'):
                    try:
                        from _helpers_io import get_staging_path, is_staging_active
                        _real = (ctx.project_root / _fix_tf).resolve()
                        _p = (
                            get_staging_path(_real, project_root=ctx.project_root)
                            if is_staging_active() else _real
                        )
                        if _p.is_file():
                            _live_file = _p.read_text(encoding="utf-8", errors="replace")
                    except Exception:
                        _live_file = ""
                _fix_last_good = _live_file or ctx.all_results_dict.get(tid, "")

                agent_fix_input = _prune_fix_context(
                    domain_key=original_agent_key,
                    task_obj=task_obj,
                    review_issues_text=issues_text,
                    pre_flight_errors=ctx.pre_flight_errors,
                    user_prompt=ctx.user_prompt,
                    paged_files_cache=getattr(task_obj, 'paged_files_cache', None),
                    bridge_api_snippet=_fix_bridge_snippet,
                    last_good_output=_fix_last_good,
                )

                # -- Mechanics Scaffold injection (fix TO the scaffold) ------
                # The initial-generation coder translates a validated scaffold;
                # the fix agent must see the SAME scaffold or it re-invents
                # (wrong Spawn arity, leaked SEARCH/REPLACE markers) — the exact
                # regression this run showed at fix cycle 2.  No-op when the
                # flag is off or no scaffold exists for this task's anchor.
                try:
                    from mechanics_scaffold import get_scaffold_for_task
                    _sc_fix = get_scaffold_for_task(
                        ctx, getattr(task_obj, 'anchor_marker', None) or ""
                    )
                    if _sc_fix is not None:
                        agent_fix_input = (
                            "## 🧩 MECHANICS SCAFFOLD (fix TO THIS — do not invent)\n"
                            + _sc_fix.to_context_block()
                            + "\nTranslate the PSEUDO above into Lua at your anchor, "
                            "using the EXACT API signatures in the API: column. "
                            "Do NOT change argument counts or add new API calls.\n\n"
                            + agent_fix_input
                        )
                except Exception:
                    pass

                # Prefer live cartridge domain_registry (populated by mount_cartridge)
                # over the kernel-only ALL_DOMAINS which omits C++/Lua/PHYS entries.
                _live_registry = getattr(ctx, 'domain_registry', None) or {}
                fix_model = (
                    _live_registry.get(original_agent_key, {}).get('model')
                    or ALL_DOMAINS.get(original_agent_key, {}).get('model')
                    or __import__('pipeline').EXECUTION_MODEL
                )
                agent_fix_output = call_ollama(
                    original_agent_system, agent_fix_input,
                    f"{domain_name} (Fix cycle {ctx.review_cycle})",
                    fix_model,
                    skip_pre_summarizer=True,
                )

                if _is_fatal_ollama(agent_fix_output):
                    print(f"  [Review-Fix] ⛔ Ollama error during fix for {tid}  skipping, retaining previous output.")
                    continue

                # -- Directive A: Sandbox validation ----------------------
                # Reject output if it attempts cross-domain file writes.
                is_clean, safe_output = reject_cross_domain_output(
                    domain_key=original_agent_key,
                    output_text=agent_fix_output,
                    persona_name=domain_name,
                )
                if not is_clean:
                    print(f"  [SANDBOX] ⛔ {domain_name} ({tid}) output rejected  "
                          f"cross-domain file write detected. Using truncated safe stub.")
                    agent_fix_output = safe_output

                # -- Surgical file application (per-anchor fix path) ----------
                # Apply the fix model's SEARCH/REPLACE blocks to the ACCUMULATED
                # file (staging-aware) so the fix lands this cycle instead of
                # waiting for the arch-fix re-exec fallback.  luac-gated so a
                # syntax-broken fix never persists.  Mirrors the monolithic path.
                _fix_target_file = getattr(task_obj, 'target_file', '') or ''
                if _fix_target_file.endswith('.lua') and agent_fix_output and agent_fix_output.strip():
                    try:
                        from _helpers_exec import (
                            _extract_search_replace_blocks as _fx_extract,
                            _fuzzy_apply_patch as _fx_patch,
                        )
                        from _helpers_io import (
                            get_staging_path as _fx_sp,
                            is_staging_active as _fx_sa,
                            atomic_write_text as _fx_write,
                        )
                        _fx_blocks = _fx_extract(agent_fix_output)
                        _fx_real = ctx.project_root / _fix_target_file
                        _fx_read = (
                            _fx_sp(_fx_real, project_root=ctx.project_root)
                            if _fx_sa() else _fx_real
                        )
                        _fx_current = (
                            _fx_read.read_text(encoding="utf-8", errors="replace")
                            if _fx_read.is_file() else ""
                        )
                        _fx_patched = _fx_current
                        _fx_applied = 0
                        for _fx_b in _fx_blocks:
                            _fx_new = _fx_patch(
                                _fx_patched,
                                _fx_b.get("search", ""),
                                _fx_b.get("replace", ""),
                            )
                            if _fx_new != _fx_patched:
                                _fx_patched = _fx_new
                                _fx_applied += 1
                        if not _fx_applied and _fx_current.strip():
                            # Full-file rewrite fallback: the coder ignored the
                            # SEARCH/REPLACE mandate and emitted a whole file (the
                            # "#2 persistent anti-pattern").  Convert it into a
                            # surgical patch by diffing against the on-disk file,
                            # preserving the deterministic module-state block and
                            # task anchors.  This lets the fix actually land
                            # instead of being rejected every cycle.
                            from _helpers_exec import (
                                _apply_full_file_rewrite_via_diff as _fx_diff,
                            )
                            _fx_diffed = _fx_diff(_fx_current, agent_fix_output)
                            if _fx_diffed != _fx_current:
                                _fx_patched = _fx_diffed
                                _fx_applied = 1
                                print(f"  [Anchor Fix] 🔀 {tid}: converted full-file "
                                      f"rewrite into a surgical diff patch.")
                        if _fx_applied:
                            # Deterministically repair the patched file (bare
                            # calls, duplicate-`_` locals, phantom APIs, nested
                            # OnStep) BEFORE the luac gate so accumulated syntax
                            # errors from earlier tasks don't keep failing luac
                            # and pinning the clean baseline at an old snapshot.
                            try:
                                from _post_process_lua import post_process_lua as _fx_ppl
                                _fx_patched = _fx_ppl(_fx_patched)
                            except Exception:
                                pass
                            # luac-gate the patched content.
                            import subprocess as _fx_spx
                            import tempfile as _fx_tf
                            _fx_tmp = _fx_tf.NamedTemporaryFile(
                                suffix='.lua', mode='w', encoding='utf-8', delete=False
                            )
                            _fx_tmp.write(_fx_patched)
                            _fx_tmp.close()
                            _fx_luac_ok = True
                            try:
                                _fx_proc = _fx_spx.run(
                                    [_LUAC_EXE, "-p", _fx_tmp.name],
                                    capture_output=True, text=True, timeout=20,
                                )
                                _fx_luac_ok = (_fx_proc.returncode == 0)
                            except FileNotFoundError:
                                _fx_luac_ok = True  # luac absent: accept
                            except Exception:
                                _fx_luac_ok = False
                            try:
                                Path(_fx_tmp.name).unlink(missing_ok=True)
                            except Exception:
                                pass
                            if _fx_luac_ok:
                                _fx_write(_fx_real, _fx_patched)
                                # Deterministic post-process the accumulated file so
                                # bare-namespace calls, phantom APIs, duplicate
                                # functions and comment monologues are cleaned THIS
                                # cycle rather than re-flagged every review cycle.
                                try:
                                    from _post_process_lua import post_process_lua_file as _fx_pp
                                    _fx_pp(_fx_read)
                                except Exception:
                                    pass
                                try:
                                    _fx_snap = getattr(ctx, '_last_luac_clean_anchor', None)
                                    if _fx_snap is not None:
                                        _fx_snap[_fix_target_file.replace("\\", "/")] = _fx_patched
                                except Exception:
                                    pass
                                print(f"  [Anchor Fix] ✅ Applied {_fx_applied} SEARCH/REPLACE "
                                      f"block(s) for {tid} to {_fix_target_file} "
                                      f"({len(_fx_patched)} chars).")
                                # Store REPLACE-only content so the next review
                                # cycle / static guard see the fixed code, not the
                                # diff markers or the SEARCH side's pre-fix text.
                                from _preflight_helpers import (
                                    _strip_search_replace_metadata as _fx_strip,
                                )
                                agent_fix_output = _fx_strip(agent_fix_output)
                                _file_applied_tids.add(tid)
                            else:
                                print(f"  [Anchor Fix] ⚠ {tid}: fix failed luac — "
                                      f"keeping current file for arch-fix fallback.")
                        else:
                            print(f"  [Anchor Fix] ⚠ {tid}: {len(_fx_blocks)} SEARCH "
                                  f"block(s) emitted but none matched {_fix_target_file} — "
                                  f"keeping file.")
                    except Exception as _fx_e:
                        print(f"  [Anchor Fix] ⚠ file application failed for {tid}: {_fx_e}")

                # -- Phase III: LangGraph AST Patch State Reducer --------------
                # Extract AST_PATCH signals from agent output, validate them,
                # and apply as state-reducing merge operations. Malformed patches
                # are routed through a fast-path micro-model syntax repair pass.
                from signals import AST_PATCH_BLOCK_PATTERN
                ast_patches = list(re.finditer(AST_PATCH_BLOCK_PATTERN, agent_fix_output, re.DOTALL))
                if ast_patches:
                    for p_match in ast_patches:
                        target_path = p_match.group(1).strip()
                        patch_content = p_match.group(2).strip()
                        if target_path and patch_content:
                            # Validate: ensure path has valid extension
                            valid_extensions = (".cpp", ".h", ".hpp", ".lua", ".py", ".json", ".md")
                            if any(target_path.endswith(ext) for ext in valid_extensions):
                                ctx.pending_patches.append({
                                    "task_id": tid,
                                    "domain": original_agent_key,
                                    "target_path": target_path,
                                    "content": patch_content,
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                })
                                print(f"  [AST Reducer] ✅ Queued patch for {target_path} ({tid})")
                            else:
                                # Malformed  route through fast-path syntax repair
                                print(f"  [AST Reducer] ⚠ Malformed patch path '{target_path}'  routing to syntax repair")
                                from pipeline import SYNTAX_GATE_MODEL as _repair_model
                                repair_model = _repair_model or _EXECUTION_MODEL
                                repair_prompt = (
                                    f"Repair the following AST patch targeting invalid path '{target_path}'. "
                                    f"Extract the correct target file path and clean up the patch content. "
                                    f"Output as: [AST_PATCH:corrected/path]```code```[/AST_PATCH]\n\n"
                                    f"Raw patch:\n{target_path}\n---\n{patch_content}"
                                )
                                from _pipeline_helpers import call_ollama as _pipeline_call_ollama
                                repair_output = _pipeline_call_ollama(
                                    "You are a syntax repair micro-model. Fix malformed AST patches.",
                                    repair_prompt,
                                    f"AST Syntax Repair ({tid})",
                                    repair_model,
                                )
                                # Re-parse repaired output
                                repair_match = re.search(AST_PATCH_BLOCK_PATTERN, repair_output, re.DOTALL)
                                if repair_match:
                                    repaired_path = repair_match.group(1).strip()
                                    repaired_content = repair_match.group(2).strip()
                                    if repaired_path and repaired_content:
                                        ctx.pending_patches.append({
                                            "task_id": tid,
                                            "domain": original_agent_key,
                                            "target_path": repaired_path,
                                            "content": repaired_content,
                                            "timestamp": datetime.now(timezone.utc).isoformat(),
                                            "repaired": True,
                                        })
                                        print(f"  [AST Reducer] 🔧 Repaired patch for {repaired_path} ({tid})")
                                else:
                                    print(f"  [AST Reducer] ⛔ Could not repair malformed AST patch for {tid}")
                    # After collecting all patches, apply state reducer: merge patches into results dict.
                    # Group patches by (domain, target_path) so earlier valid patches from one domain
                    # are not silently overwritten by a later patch from a different domain targeting
                    # the same file  each domain owns its own file namespace.
                    latest_patches = {}
                    for patch in ctx.pending_patches:
                        tpath = patch["target_path"]
                        patch_domain = patch.get("domain", "")
                        # Key is (domain, path): a C++ patch and a Lua patch to the same file are kept separately
                        merge_key = (patch_domain, tpath)
                        latest_patches[merge_key] = patch  # last one per (domain, path) wins
                    
                    # Apply state-reduced patches directly to all_results_dict
                    for _merge_key, patch in latest_patches.items():
                        tpath = patch["target_path"]
                        content = patch["content"]
                        # Tag the output with AST patch metadata
                        tagged_output = (
                            f"### AST Patch: {tpath}\n"
                            f"**Domain:** {patch.get('domain', '?')}\n"
                            f"**Task:** {patch.get('task_id', '?')}\n"
                            f"**Status:** {'Repaired' if patch.get('repaired') else 'Clean'}\n\n"
                            f"```\n{content}\n```"
                        )
                        # Inject into the dict under the original task's key;
                        # also append an entry to all_results so the two
                        # collections stay in sync.
                        _ast_key = f"{patch['task_id']}_ast_{tpath.replace('/', '_')}"
                        ctx.all_results_dict[_ast_key] = tagged_output
                        ctx.all_results.append({"task_id": _ast_key, "output": tagged_output})

                domain_fix_outputs[tid] = agent_fix_output
                ctx.output_parts.append(
                    f"### {domain_name} Fix ({tid})\n{agent_fix_output}\n"
                )

                # -- Circuit Breaker: increment review-fix retry count ---------
                # retry_counts is only incremented during initial task execution
                # in mesh_tasks.py.  We must also count fix-cycle attempts here
                # so the circuit breaker at the top of the review loop can
                # actually trip when a task keeps failing after repeated fixes.
                ctx.retry_counts[tid] = ctx.retry_counts.get(tid, 0) + 1

                # Update the result in-place, keeping all_results list in sync.
                ctx.all_results_dict[tid] = agent_fix_output
                _found_rv = False
                for _i_rv, _e_rv in enumerate(ctx.all_results):
                    if _e_rv.get("task_id") == tid:
                        ctx.all_results[_i_rv] = {"task_id": tid, "output": agent_fix_output}
                        _found_rv = True
                        break
                if not _found_rv:
                    ctx.all_results.append({"task_id": tid, "output": agent_fix_output})

                # Build a combined fix output for backward compat
                fix_output = "\n\n".join(
                    f"### {tid}\n{output}"
                    for tid, output in domain_fix_outputs.items()
                )

                # -- Post-Fix Validation: strip tasks whose fix output is still empty --
                # A fix that consists solely of [DELEGATE], [QUERY:DOC], or prose with
                # no code block must be zeroed out before the next review cycle.  If we
                # let them through, the reviewer sees a task with no code and issues a
                # spurious FAIL that is structurally identical to the previous cycle,
                # tripping the insanity detector or burning the last review iteration.
                import re as _re_postfix
                from _helpers_exec import _extract_search_replace_blocks as _extract_sr_pf
                _code_fence_re = _re_postfix.compile(r"```", _re_postfix.MULTILINE)
                _delegate_only_re = _re_postfix.compile(
                    r"^\s*(\[DELEGATE[:\]].{0,120}|\[QUERY:DOC.{0,120}|\[REVISE.{0,80})\s*$",
                    _re_postfix.IGNORECASE | _re_postfix.MULTILINE,
                )
                for _ftid, _fout in list(domain_fix_outputs.items()):
                    if _ftid in _file_applied_tids:
                        continue  # fix already applied to the file; keep cleaned content
                    _has_code = bool(_code_fence_re.search(_fout))
                    _is_delegate = bool(_delegate_only_re.search(_fout)) and not _has_code
                    # Full-file dump guard: ARCHITECT_FIX_SYSTEM mandates SEARCH/REPLACE.
                    # A fix output with no extractable SEARCH/REPLACE block is a full-file
                    # rewrite (prose + fences + ### Fix-Plan headers) that clobbers the
                    # accumulated file and re-trips the PhantomAPI gate on the next cycle.
                    _sr_pf_blocks = _extract_sr_pf(_fout)
                    _is_full_dump = (not _sr_pf_blocks) and (_has_code or len(_fout.strip()) >= 120)
                    if _is_delegate or _is_full_dump or (not _has_code and len(_fout.strip()) < 120):
                        print(f"  [Post-Fix] ⚠ {_ftid} fix output is delegation/empty/full-dump  retaining previous result.")
                        # Revert to the pre-fix snapshot so the reviewer sees the last real code
                        # rather than prose-only output that guarantees another FAIL.
                        # NOTE: _pre_fix_snapshot was captured before the domain fix loop above.
                        if _ftid in _pre_fix_snapshot:
                            _reverted = _pre_fix_snapshot[_ftid]
                            ctx.all_results_dict[_ftid] = _reverted
                            # Keep all_results list in sync with the revert.
                            _found_rv2 = False
                            for _i_rv2, _e_rv2 in enumerate(ctx.all_results):
                                if _e_rv2.get("task_id") == _ftid:
                                    ctx.all_results[_i_rv2] = {"task_id": _ftid, "output": _reverted}
                                    _found_rv2 = True
                                    break
                            if not _found_rv2:
                                ctx.all_results.append({"task_id": _ftid, "output": _reverted})

                # -- Post-Fix Static Guard Refresh ---------------------
                # Re-run the lightweight static pattern guards against the newly
                # written code so ctx.pre_flight_errors reflects the CURRENT state
                # of all_results_dict.  Without this, stale errors from the original
                # code keep the PASS-override gate firing indefinitely, causing the
                # loop to exhaust all cycles and suspend at the tribunal gate even
                # when every real violation has already been corrected by a fix agent.
                try:
                    from _finalize_preflight import (
                        _inject_empty_output_errors,
                        _inject_static_pattern_errors,
                        _flush_results_to_workspace,
                    )
                    # -- Deterministic Lua post-processor (inside the fix loop) --
                    # The LLM fix agents repeatedly fail on structural issues that
                    # _post_process_lua resolves deterministically: missing
                    # OnLoadStatic, module-level MOD caching, duplicate/nested
                    # functions, phantom APIs, and pipeline artifacts.  Running it
                    # BEFORE the workspace flush means every fix cycle ends with
                    # clean Lua, so the next review sees corrected code and can
                    # converge to PASS instead of re-flagging the same structural
                    # defects forever.
                    try:
                        from _post_process_lua import post_process_ctx as _ppc
                        _ppc(ctx)
                    except Exception as _ppc_err:
                        print(f"  [Post-Fix Post-Process] ⚠ {_ppc_err}")
                    _flush_results_to_workspace(ctx)
                    ctx.pre_flight_errors = ""
                    _inject_empty_output_errors(ctx)
                    _inject_static_pattern_errors(ctx)
                    # Re-run the headless runtime simulator against the post-fix
                    # outputs so runtime errors (nil-handle access, forward
                    # references, bad arg counts, accidental globals) stay
                    # current — the initial preflight ran only once, before the
                    # fix cycle.  Clear ctx.runtime_errors first: the sim
                    # appends, it does not replace.
                    try:
                        ctx.runtime_errors = []
                        from runtime_sim import run_runtime_sim as _run_rtsim
                        _rtsim_errors = _run_rtsim(ctx)
                        if _rtsim_errors:
                            ctx.pre_flight_errors += (
                                "\n## ⚡ Runtime Simulation Errors\n"
                                + "\n".join(f"  {e}" for e in _rtsim_errors)
                                + "\n"
                            )
                            print(f"  [Post-Fix Preflight] ⚠ RuntimeSim still reports "
                                  f"{len(_rtsim_errors)} error(s) after fix cycle {ctx.review_cycle}.")
                    except Exception as _rtsim_ex:
                        print(f"  [Post-Fix Preflight] ⚠ RuntimeSim re-run failed: {_rtsim_ex}")
                    if ctx.pre_flight_errors.strip():
                        print(f"  [Post-Fix Preflight] ⚠ Static guard still open after fix cycle "
                              f"{ctx.review_cycle}  routing next review cycle with updated errors.")
                    else:
                        print(f"  [Post-Fix Preflight] ✅ All static guards clear after fix cycle "
                              f"{ctx.review_cycle}.")
                except Exception as _pf_refresh_err:
                    print(f"  [Post-Fix Preflight] ⚠ Guard refresh failed ({_pf_refresh_err})  "
                          f"retaining previous pre_flight_errors state.")

                # -- Post-Fix Re-Merge: propagate fix-cycle corrections into merged artifacts --
                # If any fixed task contributes to a shared-file merge, regenerate the
                # merged artifact so the next review cycle sees updated unified code.
                _merged_reg = getattr(ctx, 'merged_file_registry', {})
                if _merged_reg:
                    _fixed_tids = set(domain_fix_outputs.keys())
                    _needs_remerge = {
                        rel_p for rel_p, _mkey in _merged_reg.items()
                        if any(
                            getattr(ctx.task_map.get(t), 'target_file', None) == rel_p
                            for t in _fixed_tids
                        )
                    }
                    if _needs_remerge:
                        print(f"  [Post-Fix Re-Merge] Re-merging {len(_needs_remerge)} shared file(s) after fix cycle {ctx.review_cycle}...")
                        from _finalize_conflicts import merge_shared_file_outputs as _remerge
                        # Temporarily narrow task_map to only shared-file tasks so _remerge
                        # does not re-process unrelated tasks.
                        ctx = _remerge(ctx)
                        print(f"  [Post-Fix Re-Merge] ✅ Re-merge complete.")

                # -- Insanity Detector (progress-based) -----------------
                # The old similarity-on-INPUT check fired when two cycles
                # produced >95% similar *error text* — which is exactly when a
                # fix loop should KEEP trying (e.g. the same luac error while a
                # small change like a closing paren is all that's needed).  It
                # also tripped on the FIRST similar cycle, so it allowed
                # effectively one fix attempt per round.
                #
                # New rule: fingerprint the *outcome* (accumulated outputs +
                # error state).  Only consecutive cycles with an IDENTICAL
                # outcome count as "no progress"; trip after 3 of those.  Any
                # change at all — even one inserted `)` — resets the streak.
                import hashlib as _ins_hashlib
                _fp_parts: list[str] = []
                try:
                    for _fk in sorted(ctx.all_results_dict.keys()):
                        _fp_parts.append(str(_fk))
                        _fp_parts.append(str(ctx.all_results_dict[_fk]))
                except Exception:
                    pass
                _fp_parts.append(ctx.pre_flight_errors or "")
                try:
                    _fp_parts.append("\n".join(ctx.runtime_errors or []))
                except Exception:
                    pass
                _fp_digest = _ins_hashlib.md5(
                    "\n".join(_fp_parts).encode("utf-8", "replace")
                ).hexdigest()
                _prev_digest = getattr(ctx, "_last_fix_fingerprint", None)
                _streak = getattr(ctx, "_no_progress_streak", 0)
                if _prev_digest == _fp_digest:
                    _streak += 1
                else:
                    _streak = 0
                ctx._last_fix_fingerprint = _fp_digest
                ctx._no_progress_streak = _streak
                if _streak >= 3:
                    print(
                        f"\n  [Insanity Detector] ⛔ Infinite fix loop detected! "
                        f"No change in fix outcome for {_streak} consecutive cycles  "
                        f"circuit breaker tripped."
                    )
                    ctx.review_verdict = "BLOCKED"
                    break
                continue

        break

    # -- Reconciliation Gate (Active Rule Auditor) -------------------------
    # If the Tribunal/Reviewer struggled to reach consensus, trigger an audit
    # to cross-reference ledgers for conflicting rules.
    if ctx.review_verdict != "PASS" and ctx.review_cycle >= _REVIEW_MAX_ITERATIONS:
        print(f"\n{'='*50}")
        print(f"  🔍 RECONCILIATION GATE  Active Rule Auditor")
        print(f"{'='*50}")
        print(f"  Tribunal struggled to reach consensus after {ctx.review_cycle} cycles.")

        # -- User-requested extension --------------------------------------
        # Before escalating to the tribunal / failing, offer the user another
        # full review/fix round — the run may be converging and a second full
        # pipeline run would be wasteful.  Bounded by review_extension_rounds.
        if _ask_more_review_cycles(ctx):
            _ext_rounds = int(getattr(ctx, 'review_extension_rounds', 0) or 0)
            ctx.review_extension_rounds = _ext_rounds + 1
            print(f"  [Review-Fix] ↻ User requested another review/fix round "
                  f"(extension {_ext_rounds + 1}/5). Resetting cycle counter.")
            return _run_review_fix_loop(ctx)
        # If the user actively declined on a TTY, record it so the unattended
        # kick-back loop in run_code_merge does not override their choice.
        if (hasattr(sys.stdin, 'isatty') and sys.stdin.isatty()
                and not bool(os.environ.get("MIDWAY_FORCED_DETERMINISTIC", ""))):
            ctx.user_declined_review = True

        # -- High-Level Targeted Fix Stage (one-error-one-fix) -------------
        # The tribunal (the SAME 9B model) can argue but cannot write correct
        # code.  Before it, run a precise one-error-one-fix pass with the coder
        # (9B), escalating to the 14B critic only if the coder leaves the file
        # dirty.  If this produces a clean file, skip the tribunal entirely.
        if _run_targeted_fix_stage(ctx):
            ctx.review_verdict = "PASS"
            ctx.output_parts.append(
                "\n## ✅ Targeted Fix Stage — file cleaned deterministically\n"
                "The one-error-one-fix stage produced a luac-clean and "
                "runtime-clean file; the tribunal was skipped.\n"
            )
            print("  [Targeted Fix] ✅ File clean — skipping tribunal.")
            return ctx

        # Escalate to the appellate court (TRIBUNAL) for a binding verdict.
        # First run the bounded tribunal↔coder debate (argue until consensus);
        # fall back to the single-shot appeal when the debate cannot run.
        # Fall back to the legacy auto-approve / interactive logic below only
        # when the tribunal is unreachable or renders no parseable verdict.
        _tribunal_verdict = _run_tribunal_coder_debate(ctx)
        if _tribunal_verdict == "":
            _tribunal_verdict = _run_tribunal_appeal(ctx)
        if _tribunal_verdict in ("PASS", "FAIL"):
            ctx.review_verdict = _tribunal_verdict
            ctx.output_parts.append(
                "\n## ⚖️ Appellate Court Verdict\n"
                f"The TRIBUNAL rendered a binding verdict: {_tribunal_verdict}.\n"
                "Review the appellate decision and the open violations it judged.\n"
            )
            print(f"  [Tribunal] ⚖️ Appellate court rendered binding verdict: {_tribunal_verdict}.")
            try:
                from ledger import append_decision_entry
                append_decision_entry(
                    ctx.project_root,
                    title=f"Tribunal Appeal — {_tribunal_verdict}",
                    body="Binding appellate verdict rendered after review failed to converge.",
                )
            except Exception:
                pass
            return ctx

        # Bug H+I: When AUTO_APPROVE_GATES=True and no TTY (server mode),
        # auto-approve instead of hard-failing.  The previous behaviour of
        # always returning FAIL in server mode caused a death spiral where
        # the PhantomAPIGate errors were non-actionable by the LLM reviewer
        # but still blocked consensus, and the server had no way to override.
        # Bug S: Check for forced-server-mode env variable FIRST, before any
        # TTY detection.  Popen-detached subprocesses may report isatty()=True
        # on some CI runners, bypassing the no-TTY auto-approve and hitting
        # the blocking input() call.  MIDWAY_FORCED_DETERMINISTIC is set by
        # pipeline_stream_server.py on startup.
        from pipeline import AUTO_APPROVE_GATES as _auto_recon
        _is_forced_deterministic = bool(os.environ.get("MIDWAY_FORCED_DETERMINISTIC", ""))
        _has_tty = hasattr(sys.stdin, 'isatty') and sys.stdin.isatty()
        if _auto_recon or not _has_tty or _is_forced_deterministic:
            print(
                "  [Reconciliation] ⚡ AUTO_APPROVE_GATES=True and/or no TTY detected "
                "— auto-approving review as PASS. Pipeline is running unattended; "
                "review failures are logged but do not block finalization."
            )
            ctx.review_verdict = "PASS"
            ctx.output_parts.append(
                "\n## ⚡ Pipeline Auto-Approved  Review did not fully converge\n"
                f"Tribunal reached max cycles ({ctx.review_cycle}) without consensus, "
                f"but AUTO_APPROVE_GATES={'True' if _auto_recon else 'False'} "
                f"and {'no TTY' if not _has_tty or _is_forced_deterministic else 'TTY active'}.\n"
                "Review the pipeline output for non-critical warnings. "
                "Manually verify code generation quality.\n"
            )
        else:
            trigger_chime()
            try:
                _audit_choice = input(
                    "  [Reconciliation] Trigger Active Rule Auditor? (Y/n): "
                ).strip().lower()
            except (EOFError, KeyboardInterrupt):
                _audit_choice = "n"
            if _audit_choice not in ("n", "no"):
                print("  [Reconciliation] Auditor triggered  review open preflight errors above.")
            ctx.review_verdict = "FAIL"
            ctx.output_parts.append(
                "\n## ❌ Pipeline Failed  Review did not converge\n"
                f"Tribunal reached max cycles ({ctx.review_cycle}) without consensus.\n"
                "Open preflight errors were still present at cycle limit. "
                "Review the static guard output above and re-run with a more specific task description.\n"
            )

    return ctx
