#!/usr/bin/env python3
"""
Midway Agent Journal — Persistent Memory & Pattern Tracking
=============================================================
Records every pipeline attempt per-agent with self-assessment,
failure analysis, and cross-session pattern detection.

Tiered Memory System (for 8B models with ~20K context):
  L1: Active project rules — always injected (~3K tokens)
  L2: Hot patterns (repeat_count >= 2) — tag-filtered (~2K tokens)
  L3: Recent history — last 3 sessions for matched tags (~3K tokens)
  L4: Cold archive — never injected, kept for pruning analysis

Pruning: Weekly / on-demand consolidation via consolidate_and_prune()
"""

import json
import os
import re
import hashlib
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

JOURNAL_ROOT = Path(__file__).parent.resolve()
HISTORY_DIR = JOURNAL_ROOT / "history"
INDEX_PATH = JOURNAL_ROOT / "INDEX.json"

# Token budget for each memory tier
L1_BUDGET_TOKENS = 3000   # active rules
L2_BUDGET_TOKENS = 2000   # hot patterns
L3_BUDGET_TOKENS = 3000   # recent history
MAX_TOTAL_JOURNAL_TOKENS = L1_BUDGET_TOKENS + L2_BUDGET_TOKENS + L3_BUDGET_TOKENS

# Pruning thresholds
MAX_SESSIONS_BEFORE_PRUNE = 50
MAX_DAYS_BEFORE_ARCHIVE = 30
PATTERN_REPEAT_THRESHOLD = 2  # patterns with >= this many repeats are "hot"


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~3 chars per token for code-heavy text."""
    return len(text) // 3


def _make_session_id(user_prompt: str) -> str:
    """Create a deterministic session ID from the prompt."""
    slug = re.sub(r'[^a-z0-9]+', '_', user_prompt.lower())[:40].strip('_')
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{slug}_{ts}"


def _hash_prompt(prompt: str) -> str:
    return hashlib.sha256(prompt.encode()).hexdigest()[:12]


def _extract_tags(text: str) -> list:
    """Simple keyword tag extraction from request text."""
    tags = set()
    text_lower = text.lower()

    # Domain keywords
    domain_map = {
        "physics": ["jolt", "box2d", "physics", "body", "collision", "kinematic", "sensor", "pool"],
        "attraction": ["attraction", "booth", "gameplay", "coin", "plinko", "cascade", "facade"],
        "rendering": ["shader", "glsl", "opengl", "vbo", "render", "bloom", "vertex"],
        "networking": ["udp", "multiplayer", "sync", "network", "rpc", "packet", "latency"],
        "lua": ["lua", "script", "sol2", "onload", "onstep"],
        "engine": ["c++", "engine", "core", "memory", "lifecycle", "vicious", "cycle"],
        "economy": ["token", "ticket", "economy", "award", "streak", "modifier"],
        "debug": ["bug", "fix", "error", "crash", "memory leak", "segfault", "diagnostic"],
    }

    for domain, keywords in domain_map.items():
        for kw in keywords:
            if kw in text_lower:
                tags.add(domain)
                break

    return sorted(tags)


class JournalDB:
    """Persistent journal for pipeline attempts with intelligent retrieval."""

    def __init__(self):
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        self._ensure_index()

    def _ensure_index(self):
        """Create INDEX.json if it doesn't exist."""
        if not INDEX_PATH.is_file():
            default = {
                "entries": [],
                "knowledge_base": [],
                "last_pruned": None,
                "total_sessions": 0,
            }
            INDEX_PATH.write_text(json.dumps(default, indent=2), encoding="utf-8")

    def _load_index(self) -> dict:
        try:
            return json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, FileNotFoundError):
            return {"entries": [], "knowledge_base": [], "last_pruned": None, "total_sessions": 0}

    def _save_index(self, index: dict):
        INDEX_PATH.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")

    # ── Session Management ──────────────────────────────────────────────────

    def start_session(self, user_prompt: str) -> str:
        """Create a new session directory and return the session_id."""
        session_id = _make_session_id(user_prompt)
        session_dir = HISTORY_DIR / session_id
        session_dir.mkdir(parents=True, exist_ok=True)

        manifest = {
            "session_id": session_id,
            "timestamp": datetime.now().isoformat(),
            "request": user_prompt,
            "request_hash": _hash_prompt(user_prompt),
            "request_tags": _extract_tags(user_prompt),
            "agents_involved": [],
            "agent_results": {},
            "final_verdict": "IN_PROGRESS",
            "key_failures": [],
        }
        (session_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
        return session_id

    def record_agent_attempt(
        self,
        session_id: str,
        agent_name: str,
        prompt: str,
        output: str,
        status: str = "UNKNOWN",
        failure_reason: str = "",
        signal: str = "",
        signal_reason: str = "",
    ):
        """Record a single agent's attempt in a session."""
        session_dir = HISTORY_DIR / session_id
        if not session_dir.is_dir():
            return

        # Find next sequence number
        existing = list(session_dir.glob("*.md"))
        seq = len(existing) + 1
        seq_str = f"{seq:02d}"

        # Write agent entry
        entry_path = session_dir / f"{seq_str}_{agent_name.replace(' ', '_')}.md"
        content = (
            f"# Agent: {agent_name}\n"
            f"**Session:** {session_id}\n"
            f"**Timestamp:** {datetime.now().isoformat()}\n"
            f"**Status:** {status}\n"
            f"**Signal:** {signal}\n\n"
            f"## Prompt\n```\n{prompt}\n```\n\n"
            f"## Output\n```\n{output}\n```\n\n"
        )
        if failure_reason:
            content += f"## Failure Reason\n{failure_reason}\n\n"
        if signal_reason:
            content += f"## Signal Reason\n{signal_reason}\n\n"
        entry_path.write_text(content, encoding="utf-8")

        # Update manifest
        manifest_path = session_dir / "manifest.json"
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if agent_name not in manifest["agents_involved"]:
                    manifest["agents_involved"].append(agent_name)
                manifest["agent_results"][agent_name] = {
                    "status": status,
                    "failure_reason": failure_reason,
                    "signal": signal,
                }
                if status == "FAILURE" and failure_reason:
                    if failure_reason not in manifest["key_failures"]:
                        manifest["key_failures"].append(failure_reason)
                manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            except (json.JSONDecodeError, KeyError):
                pass

        # Update INDEX
        index = self._load_index()
        self._update_entry_in_index(index, session_id, manifest_path)
        self._extract_patterns(index, session_id, agent_name, failure_reason)
        self._save_index(index)

    def finalize_session(self, session_id: str, verdict: str):
        """Mark a session as complete with final verdict."""
        session_dir = HISTORY_DIR / session_id
        if not session_dir.is_dir():
            return

        manifest_path = session_dir / "manifest.json"
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest["final_verdict"] = verdict
                manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            except (json.JSONDecodeError, KeyError):
                pass

        # Update INDEX
        index = self._load_index()
        self._update_entry_in_index(index, session_id, manifest_path)
        self._save_index(index)

    def _update_entry_in_index(self, index: dict, session_id: str, manifest_path: Path):
        """Update or create the INDEX entry for a session."""
        if not manifest_path.is_file():
            return
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, FileNotFoundError):
            return

        # Find existing entry
        entry = None
        for e in index["entries"]:
            if e["session_id"] == session_id:
                entry = e
                break

        if entry is None:
            entry = {
                "session_id": session_id,
                "date": datetime.now().strftime("%Y-%m-%d"),
                "request_summary": manifest.get("request", "")[:80],
                "request_tags": manifest.get("request_tags", []),
                "agents_involved": manifest.get("agents_involved", []),
                "final_verdict": manifest.get("final_verdict", "IN_PROGRESS"),
                "key_failures": manifest.get("key_failures", []),
            }
            index["entries"].append(entry)
        else:
            entry["agents_involved"] = manifest.get("agents_involved", entry["agents_involved"])
            entry["final_verdict"] = manifest.get("final_verdict", entry["final_verdict"])
            entry["key_failures"] = manifest.get("key_failures", entry["key_failures"])

        index["total_sessions"] = len(index["entries"])

    def _extract_patterns(self, index: dict, session_id: str, agent_name: str, failure_reason: str):
        """Extract and consolidate failure patterns into the knowledge_base."""
        if not failure_reason:
            return

        # Normalize failure reason for pattern matching
        reason_lower = failure_reason.lower()
        kb = index["knowledge_base"]

        # Try to match against existing patterns
        matched = False
        for pattern in kb:
            pattern_lower = pattern["pattern"].lower()
            # Check if words overlap significantly
            pattern_words = set(pattern_lower.split())
            reason_words = set(reason_lower.split())
            overlap = len(pattern_words & reason_words)
            if overlap >= 3 or reason_lower.startswith(pattern_lower[:20]):
                pattern["repeat_count"] = pattern.get("repeat_count", 0) + 1
                pattern["last_seen"] = session_id
                if session_id not in pattern.get("sessions", []):
                    pattern.setdefault("sessions", []).append(session_id)
                matched = True
                break

        if not matched:
            kb.append({
                "pattern": failure_reason[:120],
                "agent": agent_name,
                "root_cause": "",
                "rules_reference": "",
                "first_seen": session_id,
                "last_seen": session_id,
                "repeat_count": 1,
                "sessions": [session_id],
                "resolved": False,
            })

    # ── Intelligent Retrieval ──────────────────────────────────────────────

    def query_relevant_context(
        self,
        request_text: str,
        agent_name: str = "",
        max_tokens: int = L2_BUDGET_TOKENS + L3_BUDGET_TOKENS,
    ) -> str:
        """Query journal for context relevant to the current request.

        Returns a compact, formatted string suitable for injection into agent prompts.
        Respects token budget to avoid overflowing 8B model context windows.
        """
        if max_tokens <= 0:
            return ""

        index = self._load_index()
        request_tags = _extract_tags(request_text)
        parts = []

        # ── L2: Hot Patterns (tag-filtered) ────────────────────────────────
        hot_patterns = [
            p for p in index.get("knowledge_base", [])
            if p.get("repeat_count", 0) >= PATTERN_REPEAT_THRESHOLD and not p.get("resolved", False)
        ]

        # Filter by tag relevance
        if request_tags:
            hot_patterns = [
                p for p in hot_patterns
                if any(tag in p.get("pattern", "").lower() for tag in request_tags)
                or p.get("agent", "").lower() in [a.lower() for a in request_tags]
            ]

        if hot_patterns:
            pattern_lines = ["╔═ KNOWLEDGE BASE ─────────────────────────────╗"]
            for p in hot_patterns[:5]:  # Max 5 patterns
                pattern_lines.append(
                    f"║ ⚠ Pattern: {p['pattern'][:80]}"
                )
                pattern_lines.append(
                    f"║   Agent: {p.get('agent', '?')} | "
                    f"Seen: {p.get('repeat_count', 1)}x | "
                    f"Resolved: {p.get('resolved', False)}"
                )
                if p.get("rules_reference"):
                    pattern_lines.append(f"║   Rules: {p['rules_reference']}")
            pattern_lines.append("╚═══════════════════════════════════════════╝")
            patterns_text = "\n".join(pattern_lines) + "\n"

            # Track token usage
            budget_remaining = max_tokens
            if _estimate_tokens(patterns_text) <= budget_remaining:
                parts.append(patterns_text)
                budget_remaining -= _estimate_tokens(patterns_text)
            else:
                # Truncate patterns to fit budget
                budget_remaining = max(budget_remaining, 200)

        # ── L3: Recent History (tag-matched, limited) ──────────────────────
        # Score entries by tag overlap + recency
        scored_entries = []
        for entry in index.get("entries", []):
            if entry.get("final_verdict") == "IN_PROGRESS":
                continue
            entry_tags = set(entry.get("request_tags", []))
            request_tag_set = set(request_tags)

            # Tag match score
            tag_overlap = len(entry_tags & request_tag_set)
            if tag_overlap == 0 and agent_name:
                # Also check if this agent was involved
                if agent_name not in entry.get("agents_involved", []):
                    continue
                tag_score = 0.1
            else:
                tag_score = tag_overlap / max(len(request_tag_set), 1) * 0.6

            # Recency score (decay over 30 days)
            try:
                entry_date = datetime.strptime(entry.get("date", "2000-01-01"), "%Y-%m-%d")
                days_ago = (datetime.now() - entry_date).days
                recency_score = max(0, 1.0 - days_ago / 30.0) * 0.3
            except (ValueError, TypeError):
                recency_score = 0

            # Failure relevance (FAIL verdicts get slight bonus)
            fail_bonus = 0.1 if entry.get("final_verdict") == "FAIL" else 0.0

            total_score = tag_score + recency_score + fail_bonus
            if total_score > 0:
                scored_entries.append((total_score, entry))

        # Sort by score descending, take top entries that fit budget
        scored_entries.sort(key=lambda x: -x[0])
        history_lines = []
        for score, entry in scored_entries[:5]:  # Max 5 sessions
            line = (
                f"📋 {entry.get('request_summary', '?')[:60]} | "
                f"Verdict: {entry.get('final_verdict', '?')} | "
                f"Tags: {', '.join(entry.get('request_tags', []))}"
            )
            if entry.get("key_failures"):
                failure_line = f"   Failures: {'; '.join(entry['key_failures'][:2])}"
                if _estimate_tokens(line + "\n" + failure_line) <= budget_remaining:
                    history_lines.append(line)
                    history_lines.append(failure_line)
                elif _estimate_tokens(line) <= budget_remaining:
                    history_lines.append(line)
            else:
                if _estimate_tokens(line) <= budget_remaining:
                    history_lines.append(line)

            budget_remaining -= _estimate_tokens(line + "\n")

        if history_lines:
            history_block = "╔═ RECENT HISTORY ──────────────────────────╗\n"
            history_block += "\n".join(f"║ {l}" for l in history_lines)
            history_block += "\n╚═══════════════════════════════════════════╝\n"
            parts.append(history_block)

        if not parts:
            return ""

        return "\n".join(parts)

    # ── Pruning ─────────────────────────────────────────────────────────────

    def consolidate_and_prune(self, force: bool = False) -> dict:
        """Consolidate related patterns, archive old sessions, prune INDEX.

        Returns a summary dict of what was done.
        """
        index = self._load_index()
        now = datetime.now()
        result = {
            "patterns_consolidated": 0,
            "sessions_archived": 0,
            "sessions_deleted": 0,
            "patterns_pruned": 0,
        }

        # Check if pruning is needed
        if not force:
            if index.get("total_sessions", 0) < MAX_SESSIONS_BEFORE_PRUNE:
                last_pruned = index.get("last_pruned")
                if last_pruned:
                    try:
                        last = datetime.fromisoformat(last_pruned)
                        if (now - last).days < 7:
                            return result  # Pruned within the week
                    except (ValueError, TypeError):
                        pass

        # 1. Consolidate similar patterns in knowledge_base
        kb = index.get("knowledge_base", [])
        consolidated = []
        used_indices = set()

        for i, p1 in enumerate(kb):
            if i in used_indices:
                continue
            merged = dict(p1)
            for j, p2 in enumerate(kb):
                if j <= i or j in used_indices:
                    continue
                # Check similarity
                words1 = set(p1.get("pattern", "").lower().split())
                words2 = set(p2.get("pattern", "").lower().split())
                if len(words1 & words2) >= 3 or p1.get("agent") == p2.get("agent"):
                    # Merge
                    merged["repeat_count"] = merged.get("repeat_count", 1) + p2.get("repeat_count", 1)
                    merged["last_seen"] = max(
                        merged.get("last_seen", ""),
                        p2.get("last_seen", ""),
                    )
                    sessions = merged.get("sessions", [])
                    for s in p2.get("sessions", []):
                        if s not in sessions:
                            sessions.append(s)
                    merged["sessions"] = sessions
                    used_indices.add(j)
                    result["patterns_consolidated"] += 1
            consolidated.append(merged)
            used_indices.add(i)

        index["knowledge_base"] = consolidated

        # 2. Archive/delete old sessions
        entries_to_keep = []
        for entry in index.get("entries", []):
            try:
                entry_date = datetime.strptime(entry.get("date", "2000-01-01"), "%Y-%m-%d")
                days_old = (now - entry_date).days

                # Delete very old sessions with no cross-refs
                if days_old > MAX_DAYS_BEFORE_ARCHIVE and not entry.get("key_failures"):
                    # Check if this session is referenced in any pattern
                    referenced = any(
                        entry["session_id"] in p.get("sessions", [])
                        for p in consolidated
                    )
                    if not referenced:
                        # Delete the session directory
                        session_dir = HISTORY_DIR / entry["session_id"]
                        if session_dir.is_dir():
                            import shutil
                            shutil.rmtree(session_dir)
                            result["sessions_deleted"] += 1
                        continue
                    else:
                        # Keep but mark as archived
                        entry["archived"] = True
                        result["sessions_archived"] += 1

            except (ValueError, TypeError):
                pass
            entries_to_keep.append(entry)

        index["entries"] = entries_to_keep
        index["total_sessions"] = len(entries_to_keep)

        # 3. Prune resolved patterns that are very old
        kb = index.get("knowledge_base", [])
        kb = [
            p for p in kb
            if not p.get("resolved", False) or p.get("repeat_count", 0) > 1
        ]
        index["knowledge_base"] = kb
        result["patterns_pruned"] = len(kb)

        index["last_pruned"] = now.isoformat()
        self._save_index(index)

        return result

    # ── Summary ─────────────────────────────────────────────────────────────

    def get_summary(self) -> dict:
        """Get a summary of the journal state."""
        index = self._load_index()
        return {
            "total_sessions": index.get("total_sessions", 0),
            "active_patterns": len([
                p for p in index.get("knowledge_base", [])
                if not p.get("resolved", False)
            ]),
            "hot_patterns": len([
                p for p in index.get("knowledge_base", [])
                if p.get("repeat_count", 0) >= PATTERN_REPEAT_THRESHOLD
            ]),
            "last_pruned": index.get("last_pruned", "never"),
        }


# ── Standalone CLI ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    db = JournalDB()

    if len(sys.argv) > 1 and sys.argv[1] == "--prune":
        result = db.consolidate_and_prune(force=True)
        print("Pruning complete:")
        for k, v in result.items():
            print(f"  {k}: {v}")
        print(f"\nJournal summary: {json.dumps(db.get_summary(), indent=2)}")

    elif len(sys.argv) > 1 and sys.argv[1] == "--summary":
        print(json.dumps(db.get_summary(), indent=2))

    elif len(sys.argv) > 1 and sys.argv[1] == "--query":
        query = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else ""
        if query:
            context = db.query_relevant_context(query)
            print(context if context else "No relevant context found.")
        else:
            print("Usage: python journal_core.py --query <request text>")
    else:
        print("Usage: python journal_core.py [--prune|--summary|--query <text>]")
        print(f"\nCurrent journal summary:")
        print(json.dumps(db.get_summary(), indent=2))
