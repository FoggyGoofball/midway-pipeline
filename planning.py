"""
planning.py — Multi-turn conversational planning with persistent drafts.

The conversational planning path ("plan out X", "how should we go about Y")
is a back-and-forth: the planner produces a draft, separates loosely-related
workstreams, asks targeted clarifying questions, and refines across turns
until the user approves.  On approval the plan is published to
docs/plans/<slug>_PLAN.md where get_planning_docs() surfaces it to every
build entry point (blueprint, director, analyst, chat).

State is a small JSON draft (docs/plans/<slug>_draft.json) so the dialogue
survives between HTTP requests even when the caller does not pass a session_id.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

PLANS_DIR_NAME = "plans"

# Phrases that indicate the user wants to plan, not build or query.
PLANNING_INTENT_PATTERNS = [
    r'\bplan\b', r'\bplanning\b', r'\bdesign\b', r'\broadmap\b', r'\bstrategy\b',
    r'\bblueprint\b', r'\bspec\b', r'\barchitecture\b', r'\bapproach\b',
    r'\bscaffold\b', r'\bbreakdown\b',
    r'how\s+(?:should|do|would|might)\s+(?:we|you|i)\s+(?:go\s+about|approach|tackle)',
    r'what(?:\'s| is) the best way to',
    r'let(?:\'s| us) (?:plan|design|figure out)',
]

# Phrases that signal the user approves the draft and wants it executed.
APPROVAL_PATTERNS = [
    r'\blooks\s+good\b', r'\blgtm\b', r'\bsounds\s+good\b',
    r'\bcarry\s+(?:it\s+)?out\b', r'\bexecute\b', r'\bgo\s+ahead\b',
    r'\bbuild\s+it\b', r'\bstart\s+(?:building|coding|implementing)\b',
    r'\bproceed\b', r'\bapproved\b', r'\bapprove\b', r'\bship\s+it\b',
]

PLANNING_SYSTEM = (
    "You are the pipeline's iterative feature planner. You refine a human's idea "
    "into an actionable implementation plan through a back-and-forth dialogue. "
    "You are NOT writing code — you are producing a plan.\n\n"
    "RULES:\n"
    "1. If the request bundles MULTIPLE loosely-related workstreams (e.g. "
    "\"add sol2 and the billboarding mechanic for the barker\"), separate them "
    "into distinct workstreams and ask which to prioritise. Do NOT merge them "
    "into one indistinguishable blob.\n"
    "2. For anything under-documented or ambiguous, ask SPECIFIC clarifying "
    "questions, one per line, each prefixed with '- '. Do not ask about things "
    "already answered by the project context or already decided in the plan.\n"
    "3. Maintain a single rolling '## Plan' section that accumulates decisions "
    "across turns. Revise it in place each turn — never discard a prior decision "
    "unless the user overrides it.\n"
    "4. When the plan is complete, actionable, and the user has approved, end "
    "your response with the exact line '[PLAN_FINALIZED]'.\n"
    "5. Be concise. Never invent API names, file paths, or engine internals not "
    "given in the context.\n"
)

_STOP_WORDS = {
    "plan", "planning", "design", "roadmap", "strategy", "blueprint", "spec",
    "architecture", "breakdown", "approach", "scaffold", "feature", "mechanic",
    "attraction", "booth", "game", "create", "build", "make", "write", "add",
    "help", "with", "the", "this", "that", "for", "and", "out", "your", "new",
    "should", "could", "would", "about", "moving", "forward", "next", "phase",
    "sol", "sol2", "barker", "billboard", "billboarding",
}


def plans_dir(project_root) -> Path:
    return Path(project_root) / "docs" / PLANS_DIR_NAME


def _slugify(prompt: str) -> str:
    nouns = [w for w in re.findall(r'\b[a-z]{4,}\b', (prompt or "").lower())
             if w not in _STOP_WORDS]
    if not nouns:
        return "feature_plan"
    return re.sub(r'[^\w]+', '_', "_".join(nouns[:3])).strip('_') or "feature_plan"


def _draft_path(project_root, slug: str) -> Path:
    return plans_dir(project_root) / f"{slug}_draft.json"


def is_planning_intent(prompt: str) -> bool:
    return bool(prompt) and any(re.search(p, prompt.lower()) for p in PLANNING_INTENT_PATTERNS)


def is_plan_approval(prompt: str) -> bool:
    return bool(prompt) and any(re.search(p, prompt.lower()) for p in APPROVAL_PATTERNS)


def _new_draft(slug: str, session_id: str) -> dict:
    return {
        "slug": slug,
        "session_id": session_id or "",
        "status": "active",
        "history": [],
        "plan": "",
    }


def _save_draft(project_root, draft: dict) -> None:
    plans_dir(project_root).mkdir(parents=True, exist_ok=True)
    _draft_path(project_root, draft["slug"]).write_text(
        json.dumps(draft, indent=2, default=str), encoding="utf-8"
    )


def load_draft(project_root, slug: str) -> Optional[dict]:
    p = _draft_path(project_root, slug)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def list_active_drafts(project_root) -> list[dict]:
    d = plans_dir(project_root)
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob("*_draft.json")):
        try:
            draft = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if draft.get("status") == "active":
            out.append(draft)
    return out


def find_active_draft_by_session(project_root, session_id: str) -> Optional[dict]:
    if not session_id:
        return None
    for d in list_active_drafts(project_root):
        if d.get("session_id") == session_id:
            return d
    return None


def resolve_active_draft(project_root, session_id: str, prompt: str) -> Optional[dict]:
    """Return the draft that *prompt* continues, or None.

    A NEW planning request is never a continuation.  Otherwise, prefer the
    session match, then a slug match, then the single-active-draft fallback
    (so the back-and-forth works even when callers omit session_id).
    """
    if is_planning_intent(prompt or ""):
        return None
    d = find_active_draft_by_session(project_root, session_id or "")
    if d:
        return d
    actives = list_active_drafts(project_root)
    slug = _slugify(prompt or "")
    for d in actives:
        if d.get("slug") == slug:
            return d
    if len(actives) == 1:
        return actives[0]
    return None


def _build_transcript(draft: dict, context: str = "") -> str:
    parts = []
    if context:
        parts.append(f"## Project Context (already documented)\n{context}")
    if draft.get("plan"):
        parts.append(f"## Current Plan Draft (revise in place)\n{draft['plan']}")
    for m in draft.get("history", [])[-20:]:
        role = "User" if m.get("role") == "user" else "Assistant"
        parts.append(f"### {role}\n{m.get('content', '')}")
    return "\n\n".join(parts)


def publish_plan(project_root, slug: str, plan_text: str) -> str:
    """Write the final plan to docs/plans/<slug>_PLAN.md (consumed by builds)."""
    plans_dir(project_root).mkdir(parents=True, exist_ok=True)
    path = plans_dir(project_root) / f"{slug}_PLAN.md"
    header = (
        f"<!-- Saved from planning conversation, {datetime.now().strftime('%Y-%m-%d')}. -->\n"
        f"# Plan: {slug.replace('_', ' ').title()}\n\n"
    )
    try:
        from _helpers_io import atomic_write_text
        atomic_write_text(path, header + plan_text.strip() + "\n")
    except Exception:
        path.write_text(header + plan_text.strip() + "\n", encoding="utf-8")
    return str(path)


def run_planning_turn(
    user_prompt: str,
    session_id: str,
    project_root,
    call_func: Optional[Callable[[str, str, str], str]] = None,
    context: str = "",
):
    """Run one turn of the planning conversation.

    Returns (response_text, finalized).  On finalize the plan is published to
    docs/plans/ and the draft is removed.
    """
    if call_func is None:
        from ollama_client import call_ollama as _co, CHAT_MODEL as _cm
        call_func = lambda sys_, usr_, lbl_: _co(sys_, usr_, lbl_, _cm)

    slug = _slugify(user_prompt)
    if is_planning_intent(user_prompt):
        draft = load_draft(project_root, slug)
        if draft is None:
            draft = _new_draft(slug, session_id or "")
    else:
        draft = resolve_active_draft(project_root, session_id or "", user_prompt)
        if draft is None:
            return None, False
    # Canonical slug belongs to the draft, not the (possibly short) reply that
    # just continued it — publish/cleanup must use the draft's own slug.
    slug = draft.get("slug") or slug

    first_turn = not draft.get("history")
    draft.setdefault("history", []).append({"role": "user", "content": user_prompt})

    transcript = _build_transcript(draft, context if first_turn else "")
    response = call_func(PLANNING_SYSTEM, transcript, "Planner (iterative)")
    if not response:
        response = "(planner returned an empty response)"

    draft["history"].append({"role": "assistant", "content": response})
    draft["plan"] = response

    finalized = is_plan_approval(user_prompt) or "[PLAN_FINALIZED]" in response

    if finalized:
        plan_text = response
        if "[PLAN_FINALIZED]" in plan_text:
            plan_text = plan_text.split("[PLAN_FINALIZED]", 1)[0]
        publish_plan(project_root, slug, plan_text.strip())
        # Draft is done — remove it so it no longer captures follow-ups.
        try:
            _draft_path(project_root, slug).unlink(missing_ok=True)
        except OSError:
            pass
        print(f"  [Planning] ✅ Finalized and published plan: {slug}")
        return response, True

    _save_draft(project_root, draft)
    return response, False
