"""
auditor.py — Active Rule Auditor (Governance)
==============================================
Scans all .md files in docs/memory/ and extracts markdown headers into
a list of Approved Tags. Provides conflict scanning and reconciliation
functions for the mesh pipeline governance system.

No async/await — purely synchronous.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


# ── Tag Harvester ──────────────────────────────────────────────────────────

def harvest_approved_tags(memory_dir: Optional[Path] = None) -> Dict[str, List[str]]:
    """Scan all .md files in docs/memory/ and extract markdown headers.

    Returns:
        Dict mapping filename (stem) to list of header strings found.
        Headers include the full markdown header text (e.g., "### [ModuleName]").
    """
    if memory_dir is None:
        # Default: relative to this file's parent project root
        memory_dir = Path(__file__).resolve().parent.with_name("midway") / "docs" / "memory"

    if not memory_dir.is_dir():
        print(f"  [Auditor] Memory directory not found: {memory_dir}")
        return {}

    tags: Dict[str, List[str]] = {}
    header_pattern = re.compile(r'^(#{1,6})\s+(.+)$', re.MULTILINE)

    for md_file in sorted(memory_dir.glob("*.md")):
        try:
            content = md_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        headers = []
        for match in header_pattern.finditer(content):
            level = len(match.group(1))
            header_text = match.group(2).strip()
            headers.append(f"{'#' * level} {header_text}")

        if headers:
            tags[md_file.stem] = headers

    return tags


# ── Conflict Scanner ──────────────────────────────────────────────────────

def scan_for_conflicts(memory_dir: Optional[Path] = None) -> List[Dict[str, str]]:
    """Cross-reference all memory ledgers and find conflicting logic.

    Scans headers across all ledgers. If the same or similar header name
    appears in multiple ledgers with contradictory content, flag it.

    Returns:
        List of conflict dicts with keys: 'headers', 'files', 'suggestion'.
    """
    tags = harvest_approved_tags(memory_dir)
    conflicts: List[Dict[str, str]] = []

    # Build a reverse index: normalized header -> list of files
    header_to_files: Dict[str, List[str]] = {}
    for filename, headers in tags.items():
        for header in headers:
            # Normalize: strip markdown, lowercase, collapse whitespace
            normalized = re.sub(r'^#+\s*', '', header).lower().strip()
            normalized = re.sub(r'\s+', ' ', normalized)
            if normalized not in header_to_files:
                header_to_files[normalized] = []
            header_to_files[normalized].append(filename)

    # Find headers that appear in multiple files
    for header, files in header_to_files.items():
        if len(files) > 1:
            # If the same header is in multiple ledgers, it might indicate overlap
            # Check if all files are of the same domain type
            domain_types = set(f.split('_')[0] for f in files)
            if len(domain_types) > 1:
                conflicts.append({
                    "headers": header,
                    "files": ", ".join(files),
                    "suggestion": (
                        f"Header '{header}' appears in multiple domain ledgers "
                        f"({', '.join(files)}). Consider consolidating or distinguishing "
                        f"these entries to prevent cross-domain confusion."
                    ),
                })

    return conflicts


# ── Audit Report ──────────────────────────────────────────────────────────

def generate_audit_report(memory_dir: Optional[Path] = None) -> str:
    """Generate a full audit report for the pipeline output.

    Returns:
        Formatted markdown string with tag summary and conflict warnings.
    """
    tags = harvest_approved_tags(memory_dir)
    conflicts = scan_for_conflicts(memory_dir)

    lines = ["## Active Rule Auditor Report\n"]

    # Tag Summary
    lines.append("### Approved Tags (by Ledger)\n")
    total_tags = 0
    for filename, headers in sorted(tags.items()):
        lines.append(f"**{filename}.md:** {len(headers)} header(s)")
        for h in headers[:10]:
            lines.append(f"  - {h}")
        if len(headers) > 10:
            lines.append(f"  - ... ({len(headers) - 10} more)")
        lines.append("")
        total_tags += len(headers)

    lines.append(f"**Total:** {total_tags} tags across {len(tags)} ledger(s)\n")

    # Conflict Summary
    if conflicts:
        lines.append("\n### ⚠ Cross-Ledger Conflicts\n")
        for c in conflicts:
            lines.append(f"- **Header:** `{c['headers']}`")
            lines.append(f"  **Files:** {c['files']}")
            lines.append(f"  **Suggestion:** {c['suggestion']}")
            lines.append("")
    else:
        lines.append("\n### ✅ No Cross-Ledger Conflicts Detected\n")

    return "\n".join(lines)


# ── CLI Entry Point ───────────────────────────────────────────────────────

if __name__ == "__main__":
    report = generate_audit_report()
    print(report)
