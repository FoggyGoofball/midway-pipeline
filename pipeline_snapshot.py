#!/usr/bin/env python3
"""
Midway to Nowhere — Pipeline Snapshot Manager
==============================================
Creates a hidden mirror directory (.pipeline_snapshots/) that stores:
  - Original file contents (before any modifications)
  - Each agent's output as proposed file writes
  - Unified diffs between original and proposed
  - A manifest tracking every run, task, and file change

Provides CLI commands to:
  - List all snapshots
  - View diff for a specific snapshot/task/file
  - Revert (restore originals)
  - Advance (apply proposed changes)

Usage:
    from pipeline_snapshot import SnapshotManager
    snap = SnapshotManager("my-feature")
    snap.save_originals(["src/Engine.cpp", "src/Engine.h"])
    snap.save_proposal("C++ Core", "src/Engine.cpp", new_content)
    snap.generate_diff("src/Engine.cpp")
    print(snap.manifest_path)

CLI:
    python pipeline_snapshot.py list
    python pipeline_snapshot.py diff <run_id> [file]
    python pipeline_snapshot.py revert <run_id>
    python pipeline_snapshot.py advance <run_id>
"""

import difflib
import json
import os
import shutil
import sys
import re
import hashlib
from datetime import datetime
from pathlib import Path


# ── Configuration ──────────────────────────────────────────────────────────

SNAPSHOT_DIR = Path(__file__).parent / ".pipeline_snapshots"
PROJECT_ROOT = Path(__file__).parent.resolve()


# ── Helpers ────────────────────────────────────────────────────────────────

def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _safe_path(project_rel_path: str) -> str:
    """Convert a relative project path to a safe filesystem name.
    E.g. 'src/Engine.cpp' -> 'src_Engine.cpp'"""
    return project_rel_path.replace("/", "_").replace("\\", "_")


def _parse_code_blocks(text: str) -> list:
    """
    Extract all fenced code blocks from agent output.
    Returns list of (language, code) tuples.
    Also detects file paths from markdown headings like '### File: src/Engine.cpp'
    """
    blocks = []
    # Try to find file paths in markdown headings
    file_heading_pattern = re.compile(r"###\s*File:\s*([^\n]+)")
    headings = list(file_heading_pattern.finditer(text))

    # Find all fenced code blocks
    fence_pattern = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)
    for match in fence_pattern.finditer(text):
        lang = match.group(1).strip()
        code = match.group(2).strip()
        blocks.append((lang, code))

    return blocks


def _try_infer_filepath(lang: str, code: str, known_files: set) -> str:
    """
    Try to infer the intended file path from a code block.
    Checks: known files, language conventions, shebang lines.
    """
    ext_map = {
        "cpp": ".cpp", "c++": ".cpp", "h": ".h", "hpp": ".hpp",
        "lua": ".lua", "python": ".py", "py": ".py",
        "json": ".json", "glsl": ".glsl", "vert": ".vert", "frag": ".frag",
        "markdown": ".md", "md": ".md", "yaml": ".yml", "xml": ".xml",
        "cmake": ".cmake", "bash": ".sh", "sh": ".sh", "bat": ".bat",
    }

    # Check shebang
    first_line = code.split("\n")[0].strip()
    shebang_map = {
        "python": ".py", "bash": ".sh", "sh": ".sh",
        "lua": ".lua", "node": ".js",
    }
    for interpreter, ext in shebang_map.items():
        if first_line.startswith(f"#!") and interpreter in first_line:
            return f"script{ext}"

    # Use language hint
    ext = ext_map.get(lang.lower(), "")
    if not ext:
        return ""

    # If there's exactly one known file with this extension, use it
    candidates = [f for f in known_files if f.endswith(ext)]
    if len(candidates) == 1:
        return candidates[0]

    return ""


# ── SnapshotManager ────────────────────────────────────────────────────────

class SnapshotManager:
    """
    Manages a single pipeline run's snapshot directory.

    Directory structure:
        .pipeline_snapshots/
            <run_id>/
                manifest.json
                originals/
                    src_Engine.cpp      (copy of original before changes)
                    src_Engine.h
                proposals/
                    <task_id>_<persona>/
                        src_Engine.cpp   (proposed new content)
                diffs/
                    src_Engine.cpp.diff  (unified diff)
    """

    def __init__(self, run_id: str = None, description: str = ""):
        self.run_id = run_id or _timestamp()
        self.description = description
        self.root = _ensure_dir(SNAPSHOT_DIR / self.run_id)
        self.originals_dir = _ensure_dir(self.root / "originals")
        self.proposals_dir = _ensure_dir(self.root / "proposals")
        self.diffs_dir = _ensure_dir(self.root / "diffs")
        self.manifest_path = self.root / "manifest.json"
        self._manifest = self._load_manifest()
        self._manifest["run_id"] = self.run_id
        self._manifest["description"] = description
        self._manifest["created"] = _timestamp()
        self._manifest.setdefault("files_snapshotted", [])
        self._manifest.setdefault("tasks", [])
        self._manifest.setdefault("proposals", {})
        self._manifest.setdefault("applied", False)
        self._manifest.setdefault("reverted", False)

    def _load_manifest(self) -> dict:
        if self.manifest_path.exists():
            try:
                return json.loads(self.manifest_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def _save_manifest(self):
        self.manifest_path.write_text(
            json.dumps(self._manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # ── Saving Originals ──────────────────────────────────────────────

    def save_originals(self, file_paths: list) -> list:
        """
        Save copies of original files before any modifications.
        Only saves each file once per run.
        Returns list of (rel_path, saved_path) tuples.
        """
        saved = []
        for rel_path in file_paths:
            safe = _safe_path(rel_path)
            dest = self.originals_dir / safe
            if dest.exists():
                continue  # already saved
            full = PROJECT_ROOT / rel_path
            if full.is_file():
                shutil.copy2(str(full), str(dest))
                if rel_path not in self._manifest["files_snapshotted"]:
                    self._manifest["files_snapshotted"].append(rel_path)
                saved.append((rel_path, str(dest)))
                print(f"  [Snapshot] Saved original: {rel_path}")
        self._save_manifest()
        return saved

    def save_originals_from_context(self, file_context: str):
        """
        Parse file paths from a formatted file context block
        (as produced by format_file_context) and save originals.
        """
        paths = re.findall(r"### File: ([^\n]+)", file_context)
        if paths:
            self.save_originals(paths)

    # ── Saving Proposals ──────────────────────────────────────────────

    def save_proposal(self, persona: str, task_id: int, rel_path: str, content: str):
        """
        Save a proposed file write from an agent.
        Computes an MD5 content_hash stored in the manifest for later
        integrity verification during apply_proposals().
        """
        task_dir = _ensure_dir(self.proposals_dir / f"task{task_id}_{persona.replace(' ', '_')}")
        safe = _safe_path(rel_path)
        dest = task_dir / safe
        dest.write_text(content, encoding="utf-8")

        # Compute content fingerprint for integrity verification
        content_hash = hashlib.md5(content.encode("utf-8")).hexdigest()

        # Track in manifest
        if rel_path not in self._manifest["proposals"]:
            self._manifest["proposals"][rel_path] = []
        self._manifest["proposals"][rel_path].append({
            "task_id": task_id,
            "persona": persona,
            "file": str(dest),
            "timestamp": _timestamp(),
            "content_hash": content_hash,
        })
        self._save_manifest()
        print(f"  [Snapshot] Saved proposal for {rel_path} (task {task_id}, {persona})"
              f"  [hash={content_hash[:10]}...]")

    def save_agent_output(self, persona: str, task_id: int, output_text: str):
        """
        Parse code blocks from agent output and save each as a proposal.
        Also saves the raw output for reference.
        """
        # Save raw output
        raw_dir = _ensure_dir(self.proposals_dir / f"task{task_id}_{persona.replace(' ', '_')}")
        raw_path = raw_dir / "_raw_output.txt"
        raw_path.write_text(output_text, encoding="utf-8")

        # Track task in manifest
        task_entry = {
            "task_id": task_id,
            "persona": persona,
            "raw_output": str(raw_path),
            "timestamp": _timestamp(),
        }
        self._manifest["tasks"].append(task_entry)

        # Parse code blocks
        blocks = _parse_code_blocks(output_text)
        known_files = set(self._manifest["files_snapshotted"])

        for lang, code in blocks:
            # Try to find file path from context
            filepath = _try_infer_filepath(lang, code, known_files)
            if filepath:
                self.save_proposal(persona, task_id, filepath, code)

        self._save_manifest()

    # ── Diff Generation ───────────────────────────────────────────────

    def generate_diff(self, rel_path: str) -> str:
        """
        Generate a unified diff between the original and the latest proposal.
        Returns the diff text.
        """
        safe = _safe_path(rel_path)
        original = self.originals_dir / safe
        proposals = self._manifest["proposals"].get(rel_path, [])
        if not proposals:
            return f"No proposals for {rel_path}"

        latest = proposals[-1]
        proposed_path = Path(latest["file"])

        orig_text = original.read_text(encoding="utf-8", errors="replace") if original.exists() else ""
        prop_text = proposed_path.read_text(encoding="utf-8", errors="replace") if proposed_path.exists() else ""

        diff = difflib.unified_diff(
            orig_text.splitlines(keepends=True),
            prop_text.splitlines(keepends=True),
            fromfile=f"a/{rel_path}",
            tofile=f"b/{rel_path}",
        )
        diff_text = "".join(diff)

        # Save diff to file
        diff_dest = self.diffs_dir / f"{safe}.diff"
        diff_dest.write_text(diff_text, encoding="utf-8")

        return diff_text

    def generate_all_diffs(self) -> dict:
        """Generate diffs for all proposed files. Returns {rel_path: diff_text}."""
        result = {}
        for rel_path in self._manifest["proposals"]:
            diff = self.generate_diff(rel_path)
            result[rel_path] = diff
        return result

    # ── Apply / Revert ────────────────────────────────────────────────

    def apply_proposals(self, rel_paths: list = None) -> list:
        """
        Apply the latest proposal for each file.
        If rel_paths is None, applies all proposals.
        
        Before applying, verifies the MD5 content_hash stored in the manifest
        against the file on disk to detect silent file corruption.
        Returns list of (rel_path, success) tuples.
        """
        proposals = self._manifest["proposals"]
        if rel_paths is None:
            rel_paths = list(proposals.keys())

        results = []
        for rel_path in rel_paths:
            entries = proposals.get(rel_path, [])
            if not entries:
                results.append((rel_path, False, "No proposals"))
                continue

            latest = entries[-1]
            proposed_path = Path(latest["file"])
            if not proposed_path.exists():
                results.append((rel_path, False, "Proposal file missing"))
                continue

            # ── Integrity verification: hash check before copy ────────
            expected_hash = latest.get("content_hash")
            if expected_hash is not None:
                try:
                    disk_bytes = proposed_path.read_bytes()
                    disk_hash = hashlib.md5(disk_bytes).hexdigest()
                    if disk_hash != expected_hash:
                        msg = (f"Hash mismatch — possible silent file corruption. "
                               f"Expected {expected_hash[:10]}..., got {disk_hash[:10]}...")
                        print(f"  [Snapshot] ⛔ HASH MISMATCH: {rel_path}\n"
                              f"             {msg}")
                        results.append((rel_path, False, msg))
                        continue
                    else:
                        print(f"  [Snapshot] ✓ Hash verified: {rel_path}  "
                              f"[{disk_hash[:10]}...]")
                except Exception as e:
                    msg = f"Hash verification failed: {e}"
                    results.append((rel_path, False, msg))
                    continue
            else:
                # Legacy manifest entries (pre-hash) — warn and skip
                print(f"  [Snapshot] ⚠ No stored hash for {rel_path}"
                      f" — skipping integrity check (legacy entry)")

            dest = PROJECT_ROOT / rel_path
            # Ensure parent directory exists
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(proposed_path), str(dest))
            results.append((rel_path, True, ""))
            print(f"  [Snapshot] Applied: {rel_path}")

        self._manifest["applied"] = True
        self._manifest["applied_at"] = _timestamp()
        self._save_manifest()
        return results

    def revert_all(self) -> list:
        """
        Restore all original files.
        Returns list of (rel_path, success) tuples.
        """
        results = []
        for rel_path in self._manifest["files_snapshotted"]:
            safe = _safe_path(rel_path)
            original = self.originals_dir / safe
            if not original.exists():
                results.append((rel_path, False, "Original not found"))
                continue

            dest = PROJECT_ROOT / rel_path
            shutil.copy2(str(original), str(dest))
            results.append((rel_path, True, ""))
            print(f"  [Snapshot] Reverted: {rel_path}")

        self._manifest["reverted"] = True
        self._manifest["reverted_at"] = _timestamp()
        self._save_manifest()
        return results

    # ── Status / Info ─────────────────────────────────────────────────

    def summary(self) -> str:
        """Return a human-readable summary of this snapshot."""
        lines = [
            f"Run ID: {self.run_id}",
            f"Description: {self.description}",
            f"Created: {self._manifest.get('created', '?')}",
            f"Files snapshotted: {len(self._manifest['files_snapshotted'])}",
            f"Tasks executed: {len(self._manifest['tasks'])}",
            f"Files with proposals: {len(self._manifest['proposals'])}",
            f"Applied: {self._manifest.get('applied', False)}",
            f"Reverted: {self._manifest.get('reverted', False)}",
            "",
            "Files:",
        ]
        for f in self._manifest["files_snapshotted"]:
            proposals = self._manifest["proposals"].get(f, [])
            status = f"{len(proposals)} proposal(s)" if proposals else "original only"
            lines.append(f"  {f} ({status})")
        return "\n".join(lines)


# ── CLI ────────────────────────────────────────────────────────────────────

def _list_snapshots():
    """List all snapshot runs."""
    if not SNAPSHOT_DIR.exists():
        print("No snapshots found.")
        return

    runs = sorted(SNAPSHOT_DIR.iterdir())
    if not runs:
        print("No snapshots found.")
        return

    print(f"{'RUN ID':<20} {'DESCRIPTION':<40} {'FILES':<8} {'TASKS':<8} {'APPLIED':<10}")
    print("-" * 90)
    for run_dir in runs:
        if not run_dir.is_dir():
            continue
        manifest_path = run_dir / "manifest.json"
        if not manifest_path.exists():
            continue
        try:
            m = json.loads(manifest_path.read_text(encoding="utf-8"))
            print(
                f"{m.get('run_id', run_dir.name):<20} "
                f"{m.get('description', '')[:38]:<40} "
                f"{len(m.get('files_snapshotted', [])):<8} "
                f"{len(m.get('tasks', [])):<8} "
                f"{'Yes' if m.get('applied') else 'No':<10}"
            )
        except Exception:
            pass


def _show_diff(run_id: str, file_filter: str = None):
    """Show diffs for a snapshot run."""
    run_dir = SNAPSHOT_DIR / run_id
    if not run_dir.exists():
        print(f"Run '{run_id}' not found.")
        return

    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        print(f"No manifest for run '{run_id}'.")
        return

    m = json.loads(manifest_path.read_text(encoding="utf-8"))
    diffs_dir = run_dir / "diffs"

    if not diffs_dir.exists():
        print("No diffs generated yet.")
        return

    diff_files = sorted(diffs_dir.iterdir())
    if file_filter:
        safe_filter = _safe_path(file_filter)
        diff_files = [f for f in diff_files if safe_filter in f.name]

    if not diff_files:
        print("No matching diffs found.")
        return

    for diff_file in diff_files:
        print(f"\n{'='*70}")
        print(f"  Diff: {diff_file.stem.replace('_', '/')}")
        print(f"{'='*70}")
        print(diff_file.read_text(encoding="utf-8", errors="replace"))


def _do_revert(run_id: str):
    """Revert all files for a snapshot run."""
    run_dir = SNAPSHOT_DIR / run_id
    if not run_dir.exists():
        print(f"Run '{run_id}' not found.")
        return

    snap = SnapshotManager(run_id)
    results = snap.revert_all()
    failures = [(p, r) for p, s, r in results if not s]
    if failures:
        print(f"\n{len(failures)} failure(s):")
        for p, r in failures:
            print(f"  {p}: {r}")
    else:
        print(f"\nAll {len(results)} files reverted successfully.")


def _do_advance(run_id: str):
    """Apply all proposals for a snapshot run."""
    run_dir = SNAPSHOT_DIR / run_id
    if not run_dir.exists():
        print(f"Run '{run_id}' not found.")
        return

    snap = SnapshotManager(run_id)
    results = snap.apply_proposals()
    failures = [(p, r) for p, s, r in results if not s]
    if failures:
        print(f"\n{len(failures)} failure(s):")
        for p, r in failures:
            print(f"  {p}: {r}")
    else:
        print(f"\nAll {len(results)} files applied successfully.")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    command = sys.argv[1]

    if command == "list":
        _list_snapshots()

    elif command == "diff":
        if len(sys.argv) < 3:
            print("Usage: python pipeline_snapshot.py diff <run_id> [file_filter]")
            return
        run_id = sys.argv[2]
        file_filter = sys.argv[3] if len(sys.argv) > 3 else None
        _show_diff(run_id, file_filter)

    elif command == "revert":
        if len(sys.argv) < 3:
            print("Usage: python pipeline_snapshot.py revert <run_id>")
            return
        _do_revert(sys.argv[2])

    elif command == "advance":
        if len(sys.argv) < 3:
            print("Usage: python pipeline_snapshot.py advance <run_id>")
            return
        _do_advance(sys.argv[2])

    elif command == "show":
        if len(sys.argv) < 3:
            print("Usage: python pipeline_snapshot.py show <run_id>")
            return
        run_dir = SNAPSHOT_DIR / sys.argv[2]
        if not run_dir.exists():
            print(f"Run '{sys.argv[2]}' not found.")
            return
        snap = SnapshotManager(sys.argv[2])
        print(snap.summary())

    else:
        print(f"Unknown command: {command}")
        print("Available: list, diff, revert, advance, show")


if __name__ == "__main__":
    main()
