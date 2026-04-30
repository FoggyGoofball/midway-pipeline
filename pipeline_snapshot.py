#!/usr/bin/env python3
"""
Midway to Nowhere — Pipeline Snapshot Manager
==============================================
Per-file versioned snapshot store with SHA-256 integrity, 4-revision depth,
tamper detection, and git-style log/checkout/seal workflow.

Directory structure:
    .pipeline_snapshots/
        <run_id>/
            global_manifest.json          # run-level metadata
            files/
                src_Engine.cpp/
                    file_manifest.json    # per-file revision manifest
                    v0_20260429_170000    # original capture
                    v1_20260429_171000    # proposal v1
                    v2_20260429_172000    # proposal v2
                    v3_20260429_173000    # proposal v3 (oldest evicted on v4+)
                src_Engine.h/
                    ...

CLI:
    python pipeline_snapshot.py list
    python pipeline_snapshot.py log [run_id] [file]
    python pipeline_snapshot.py checkout <run_id> <file> <version>
    python pipeline_snapshot.py seal <run_id> <file> <version>
    python pipeline_snapshot.py verify <run_id>
    python pipeline_snapshot.py diff <run_id> [file]
    python pipeline_snapshot.py revert <run_id>
    python pipeline_snapshot.py advance <run_id>
    python pipeline_snapshot.py show <run_id>

Usage (Python API):
    from pipeline_snapshot import SnapshotManager
    snap = SnapshotManager("my-feature", "Add jackpot to plinko")
    snap.save_originals(["src/Engine.cpp", "src/Engine.h"])
    snap.save_proposal("C++ Core", 1, "src/Engine.cpp", new_content)
    snap.save_proposal("Lua", 2, "attractions/plinko.lua", lua_content)
    snap.generate_diff("src/Engine.cpp")
    snap.apply_proposals()
    snap.revert_all()
"""

import difflib
import hashlib
import json
import os
import shutil
import sys
import re
from datetime import datetime, timezone
from pathlib import Path


# ── Configuration ──────────────────────────────────────────────────────────

SNAPSHOT_DIR = Path(__file__).parent / ".pipeline_snapshots"
PROJECT_ROOT = Path(__file__).parent.resolve()
MAX_REVISIONS = 4


# ── Helpers ────────────────────────────────────────────────────────────────

def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _iso_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_path(project_rel_path: str) -> str:
    """Convert a relative project path to a safe filesystem name.
    E.g. 'src/Engine.cpp' -> 'src_Engine.cpp'"""
    return project_rel_path.replace("/", "_").replace("\\", "_")


def _sha256_of(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _sha256_of_file(path: Path) -> str:
    """Compute SHA-256 of a file on disk."""
    h = hashlib.sha256()
    try:
        h.update(path.read_bytes())
    except Exception:
        return ""
    return h.hexdigest()


def _parse_code_blocks(text: str) -> list:
    """Extract all fenced code blocks from agent output.
    Returns list of (language, code) tuples."""
    blocks = []
    fence_pattern = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)
    for match in fence_pattern.finditer(text):
        lang = match.group(1).strip()
        code = match.group(2).strip()
        blocks.append((lang, code))
    return blocks


def _try_infer_filepath(lang: str, code: str, known_files: set) -> str:
    """Try to infer the intended file path from a code block."""
    ext_map = {
        "cpp": ".cpp", "c++": ".cpp", "h": ".h", "hpp": ".hpp",
        "lua": ".lua", "python": ".py", "py": ".py",
        "json": ".json", "glsl": ".glsl", "vert": ".vert", "frag": ".frag",
        "markdown": ".md", "md": ".md", "yaml": ".yml", "xml": ".xml",
        "cmake": ".cmake", "bash": ".sh", "sh": ".sh", "bat": ".bat",
    }
    first_line = code.split("\n")[0].strip()
    shebang_map = {
        "python": ".py", "bash": ".sh", "sh": ".sh",
        "lua": ".lua", "node": ".js",
    }
    for interpreter, ext in shebang_map.items():
        if first_line.startswith("#!") and interpreter in first_line:
            return f"script{ext}"
    ext = ext_map.get(lang.lower(), "")
    if not ext:
        return ""
    candidates = [f for f in known_files if f.endswith(ext)]
    if len(candidates) == 1:
        return candidates[0]
    return ""


# ── Per-File Revision Store ────────────────────────────────────────────────

class FileRevisionStore:
    """
    Manages versioned revisions for a single file within a snapshot run.

    Directory:
        <run_dir>/files/<safe_name>/
            file_manifest.json
            v0_20260429_170000
            v1_20260429_171000
            ...
    """

    def __init__(self, run_dir: Path, rel_path: str):
        self.rel_path = rel_path
        self.safe_name = _safe_path(rel_path)
        self.store_dir = _ensure_dir(run_dir / "files" / self.safe_name)
        self.manifest_path = self.store_dir / "file_manifest.json"
        self._manifest = self._load_manifest()

    def _load_manifest(self) -> dict:
        if self.manifest_path.exists():
            try:
                return json.loads(self.manifest_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {
            "file": self.rel_path,
            "revisions": [],
            "active_version": None,
            "has_originals": False,
        }

    def _save_manifest(self):
        self.manifest_path.write_text(
            json.dumps(self._manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # ── Revision Queries ──────────────────────────────────────────────

    def list_revisions(self) -> list:
        """Return list of revision dicts, newest first."""
        return sorted(
            self._manifest["revisions"],
            key=lambda r: r["version"],
            reverse=True,
        )

    def get_revision(self, version: int) -> dict | None:
        for r in self._manifest["revisions"]:
            if r["version"] == version:
                return r
        return None

    def get_active_version(self) -> int | None:
        return self._manifest.get("active_version")

    def get_active_revision(self) -> dict | None:
        v = self.get_active_version()
        if v is None:
            return None
        return self.get_revision(v)

    def get_revision_count(self) -> int:
        return len(self._manifest["revisions"])

    def has_originals(self) -> bool:
        return self._manifest.get("has_originals", False)

    def get_stored_path(self, version: int) -> Path | None:
        rev = self.get_revision(version)
        if rev is None:
            return None
        return self.store_dir / rev["stored_at"]

    def get_active_content(self) -> str | None:
        """Read the active revision's content from disk."""
        rev = self.get_active_revision()
        if rev is None:
            return None
        path = self.store_dir / rev["stored_at"]
        if path.exists():
            return path.read_text(encoding="utf-8", errors="replace")
        return None

    # ── Adding Revisions ──────────────────────────────────────────────

    def add_revision(
        self,
        content: str,
        message: str,
        committed_by: str = "pipeline_snapshot",
        is_original: bool = False,
    ) -> int:
        """
        Store a new revision. Returns the version number.
        Automatically rotates to MAX_REVISIONS (oldest evicted).
        Sealed revisions are never evicted.
        """
        # Determine next version number
        existing = self._manifest["revisions"]
        if existing:
            next_ver = max(r["version"] for r in existing) + 1
        else:
            next_ver = 0

        # Build stored filename
        stored_at = f"v{next_ver}_{_timestamp()}"
        dest = self.store_dir / stored_at
        dest.write_text(content, encoding="utf-8")

        # Compute hash
        file_hash = _sha256_of(content)

        # Build revision entry
        entry = {
            "version": next_ver,
            "stored_at": stored_at,
            "timestamp": _iso_timestamp(),
            "sha256": file_hash,
            "message": message,
            "committed_by": committed_by,
            "sealed": False,
        }
        self._manifest["revisions"].append(entry)

        # Set as active
        self._manifest["active_version"] = next_ver

        if is_original:
            self._manifest["has_originals"] = True

        # Rotate to MAX_REVISIONS (skip sealed)
        self._rotate_oldest()

        self._save_manifest()
        return next_ver

    def _rotate_oldest(self):
        """Remove oldest unsealed revisions beyond MAX_REVISIONS.
        v0 (original) is never evicted — it's the safety net for revert_all()."""
        unsealed = [r for r in self._manifest["revisions"] if not r["sealed"] and r["version"] != 0]
        sealed = [r for r in self._manifest["revisions"] if r["sealed"]]
        keep_count = MAX_REVISIONS - len(sealed)
        if keep_count < 0:
            keep_count = 0  # all slots taken by sealed — no eviction possible

        if len(unsealed) <= keep_count:
            return

        # Sort unsealed by version (ascending), remove oldest
        unsealed.sort(key=lambda r: r["version"])
        to_remove = unsealed[:-keep_count] if keep_count > 0 else unsealed


        for rev in to_remove:
            stored_path = self.store_dir / rev["stored_at"]
            if stored_path.exists():
                stored_path.unlink()
            self._manifest["revisions"].remove(rev)

    # ── Integrity ─────────────────────────────────────────────────────

    def verify_integrity(self) -> tuple[bool, list[str]]:
        """
        Verify SHA-256 of every stored revision against manifest.
        Returns (all_ok, [error_messages]).
        """
        errors = []
        for rev in self._manifest["revisions"]:
            stored_path = self.store_dir / rev["stored_at"]
            if not stored_path.exists():
                errors.append(
                    f"  {self.rel_path} rev{rev['version']}: file missing "
                    f"(expected {rev['stored_at']})"
                )
                continue
            actual_hash = _sha256_of_file(stored_path)
            expected_hash = rev["sha256"]
            if actual_hash != expected_hash:
                status = "SEALED TAMPERED" if rev["sealed"] else "TAMPERED"
                errors.append(
                    f"  {self.rel_path} rev{rev['version']} [{status}]: "
                    f"hash mismatch (expected {expected_hash[:16]}..., "
                    f"got {actual_hash[:16]}...)"
                )
        return len(errors) == 0, errors

    # ── Seal ──────────────────────────────────────────────────────────

    def seal_version(self, version: int) -> bool:
        """Seal a revision so it cannot be evicted. Returns True on success."""
        rev = self.get_revision(version)
        if rev is None:
            return False
        rev["sealed"] = True
        self._save_manifest()
        return True

    # ── Checkout ──────────────────────────────────────────────────────

    def checkout(self, version: int) -> tuple[bool, str]:
        """
        Restore a specific version to the working tree.
        Returns (success, message).
        """
        rev = self.get_revision(version)
        if rev is None:
            return False, f"Version {version} not found for {self.rel_path}"

        stored_path = self.store_dir / rev["stored_at"]
        if not stored_path.exists():
            return False, f"Stored file missing for {self.rel_path} rev{version}"

        # Verify integrity before checkout
        actual_hash = _sha256_of_file(stored_path)
        if actual_hash != rev["sha256"]:
            return False, (
                f"INTEGRITY FAILURE: {self.rel_path} rev{version} hash mismatch. "
                f"File may have been tampered with."
            )

        # Write to project
        dest = PROJECT_ROOT / self.rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(stored_path), str(dest))

        # Update active version
        self._manifest["active_version"] = version

        # Record checkout as a new revision entry (like git reflog)
        content = stored_path.read_text(encoding="utf-8", errors="replace")
        checkout_entry = {
            "version": version,
            "stored_at": rev["stored_at"],
            "timestamp": _iso_timestamp(),
            "sha256": actual_hash,
            "message": f"Checkout: restored rev{version} to working tree",
            "committed_by": "user:checkout",
            "sealed": rev["sealed"],
        }
        # Update the existing entry's timestamp to reflect checkout time
        # (We don't add a new version — checkout reuses the existing stored file)
        rev["last_checkout"] = _iso_timestamp()

        self._save_manifest()
        return True, f"Restored {self.rel_path} to revision {version}"

    # ── Diff ──────────────────────────────────────────────────────────

    def diff_between(self, version_a: int, version_b: int) -> str:
        """Unified diff between two stored versions."""
        path_a = self.get_stored_path(version_a)
        path_b = self.get_stored_path(version_b)
        text_a = path_a.read_text(encoding="utf-8", errors="replace") if path_a and path_a.exists() else ""
        text_b = path_b.read_text(encoding="utf-8", errors="replace") if path_b and path_b.exists() else ""
        diff = difflib.unified_diff(
            text_a.splitlines(keepends=True),
            text_b.splitlines(keepends=True),
            fromfile=f"a/{self.rel_path} (rev{version_a})",
            tofile=f"b/{self.rel_path} (rev{version_b})",
        )
        return "".join(diff)

    def diff_active_vs_original(self) -> str:
        """Diff between active version and original (v0)."""
        return self.diff_between(0, self._manifest["active_version"])


# ── SnapshotManager ────────────────────────────────────────────────────────

class SnapshotManager:
    """
    Manages a single pipeline run's snapshot directory with per-file versioning.

    Directory structure:
        .pipeline_snapshots/<run_id>/
            global_manifest.json
            files/
                <safe_name>/
                    file_manifest.json
                    v0_...  v1_...  v2_...  v3_...
    """

    def __init__(self, run_id: str = None, description: str = ""):
        self.run_id = run_id or _timestamp()
        self.description = description
        self.root = _ensure_dir(SNAPSHOT_DIR / self.run_id)
        self.files_dir = _ensure_dir(self.root / "files")
        self.global_manifest_path = self.root / "global_manifest.json"
        self._global = self._load_global()
        self._global["run_id"] = self.run_id
        self._global["description"] = description
        self._global.setdefault("created", _iso_timestamp())
        self._global.setdefault("tasks", [])
        self._global.setdefault("applied", False)
        self._global.setdefault("reverted", False)
        self._global.setdefault("files", [])
        self._save_global()

        # Cache of FileRevisionStore instances
        self._file_stores: dict[str, FileRevisionStore] = {}

    def _load_global(self) -> dict:
        if self.global_manifest_path.exists():
            try:
                return json.loads(self.global_manifest_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def _save_global(self):
        self.global_manifest_path.write_text(
            json.dumps(self._global, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _get_file_store(self, rel_path: str) -> FileRevisionStore:
        """Get or create a FileRevisionStore for the given path."""
        if rel_path not in self._file_stores:
            self._file_stores[rel_path] = FileRevisionStore(self.root, rel_path)
        return self._file_stores[rel_path]

    def _register_file(self, rel_path: str):
        """Track file in global manifest."""
        if rel_path not in self._global["files"]:
            self._global["files"].append(rel_path)
            self._save_global()

    # ── Saving Originals ──────────────────────────────────────────────

    def save_originals(self, file_paths: list) -> list:
        """
        Save copies of original files before any modifications.
        Each file gets a v0 revision. Skips if v0 already exists.
        Returns list of (rel_path, version) tuples.
        """
        saved = []
        for rel_path in file_paths:
            store = self._get_file_store(rel_path)
            if store.has_originals():
                continue  # already captured

            full = PROJECT_ROOT / rel_path
            if not full.is_file():
                continue

            content = full.read_text(encoding="utf-8", errors="replace")
            ver = store.add_revision(
                content=content,
                message="Original pre-pipeline capture",
                committed_by="pipeline_snapshot",
                is_original=True,
            )
            self._register_file(rel_path)
            saved.append((rel_path, ver))
            print(f"  [Snapshot] Saved original: {rel_path} (v{ver})")
        return saved

    def save_originals_from_context(self, file_context: str):
        """Parse file paths from a formatted file context block and save originals."""
        paths = re.findall(r"### File: ([^\n]+)", file_context)
        if paths:
            self.save_originals(paths)

    # ── Saving Proposals ──────────────────────────────────────────────

    def save_proposal(
        self,
        persona: str,
        task_id: int,
        rel_path: str,
        content: str,
        message: str = None,
    ) -> int:
        """
        Save a proposed file write from an agent as a new revision.
        Auto-generates a commit message if none provided.
        Returns the version number.
        """
        store = self._get_file_store(rel_path)
        self._register_file(rel_path)

        if message is None:
            message = f"{persona}: proposed change (task {task_id})"

        ver = store.add_revision(
            content=content,
            message=message,
            committed_by=f"agent:{persona}",
        )
        print(f"  [Snapshot] Saved proposal for {rel_path} (task {task_id}, {persona}) -> v{ver}")

        return ver

    def save_agent_output(self, persona: str, task_id: int, output_text: str):
        """
        Parse code blocks from agent output and save each as a proposal.
        Also saves the raw output for reference.
        """
        # Save raw output
        raw_dir = _ensure_dir(self.root / "raw_outputs")
        raw_path = raw_dir / f"task{task_id}_{persona.replace(' ', '_')}.txt"
        raw_path.write_text(output_text, encoding="utf-8")

        # Track task in global manifest
        task_entry = {
            "task_id": task_id,
            "persona": persona,
            "raw_output": str(raw_path),
            "timestamp": _iso_timestamp(),
        }
        self._global["tasks"].append(task_entry)
        self._save_global()

        # Parse code blocks
        blocks = _parse_code_blocks(output_text)
        known_files = set(self._global["files"])

        for lang, code in blocks:
            filepath = _try_infer_filepath(lang, code, known_files)
            if filepath:
                self.save_proposal(
                    persona=persona,
                    task_id=task_id,
                    rel_path=filepath,
                    content=code,
                    message=f"{persona}: agent output (task {task_id})",
                )

    # ── Diff Generation ───────────────────────────────────────────────

    def generate_diff(self, rel_path: str) -> str:
        """Generate a unified diff between original (v0) and active revision."""
        store = self._get_file_store(rel_path)
        if store.get_revision_count() < 2:
            return f"No revisions to diff for {rel_path}"

        return store.diff_active_vs_original()

    def generate_all_diffs(self) -> dict:
        """Generate diffs for all files with revisions. Returns {rel_path: diff_text}."""
        result = {}
        for rel_path in self._global["files"]:
            diff = self.generate_diff(rel_path)
            result[rel_path] = diff
        return result

    # ── Apply / Revert ────────────────────────────────────────────────

    def apply_proposals(self, rel_paths: list = None) -> list:
        """
        Apply the active revision for each file to the working tree.
        If rel_paths is None, applies all tracked files.
        Returns list of (rel_path, success, message) tuples.
        """
        if rel_paths is None:
            rel_paths = self._global["files"]

        # Verify integrity of all files first
        ok, errors = self.verify_integrity()
        if not ok:
            print("  [Snapshot] INTEGRITY CHECK FAILED — refusing to apply:")
            for e in errors:
                print(f"    {e}")
            return [(p, False, "Integrity check failed") for p in rel_paths]

        results = []
        for rel_path in rel_paths:
            store = self._get_file_store(rel_path)
            content = store.get_active_content()
            if content is None:
                results.append((rel_path, False, "No active revision"))
                continue

            dest = PROJECT_ROOT / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
            results.append((rel_path, True, ""))
            print(f"  [Snapshot] Applied: {rel_path} (v{store.get_active_version()})")

        self._global["applied"] = True
        self._global["applied_at"] = _iso_timestamp()
        self._save_global()
        return results

    def revert_all(self) -> list:
        """
        Restore all files to their original (v0) revision.
        Returns list of (rel_path, success, message) tuples.
        """
        # Verify integrity first
        ok, errors = self.verify_integrity()
        if not ok:
            print("  [Snapshot] INTEGRITY CHECK FAILED — refusing to revert:")
            for e in errors:
                print(f"    {e}")
            return [(p, False, "Integrity check failed") for p in self._global["files"]]

        results = []
        for rel_path in self._global["files"]:
            store = self._get_file_store(rel_path)
            rev = store.get_revision(0)
            if rev is None:
                results.append((rel_path, False, "No original (v0) revision"))
                continue

            stored_path = store.get_stored_path(0)
            if stored_path is None or not stored_path.exists():
                results.append((rel_path, False, "Original file missing"))
                continue

            dest = PROJECT_ROOT / rel_path
            shutil.copy2(str(stored_path), str(dest))
            store._manifest["active_version"] = 0
            store._save_manifest()
            results.append((rel_path, True, ""))
            print(f"  [Snapshot] Reverted: {rel_path} (v0)")

        self._global["reverted"] = True
        self._global["reverted_at"] = _iso_timestamp()
        self._save_global()
        return results

    # ── Integrity ─────────────────────────────────────────────────────

    def verify_integrity(self) -> tuple[bool, list[str]]:
        """
        Verify SHA-256 of every stored revision across all files.
        Returns (all_ok, [error_messages]).
        """
        all_ok = True
        all_errors = []
        for rel_path in self._global["files"]:
            store = self._get_file_store(rel_path)
            ok, errors = store.verify_integrity()
            if not ok:
                all_ok = False
                all_errors.extend(errors)
        return all_ok, all_errors

    # ── Seal ──────────────────────────────────────────────────────────

    def seal_version(self, rel_path: str, version: int) -> bool:
        """Seal a specific revision of a file so it cannot be evicted."""
        store = self._get_file_store(rel_path)
        return store.seal_version(version)

    # ── Checkout ──────────────────────────────────────────────────────

    def checkout_version(self, rel_path: str, version: int) -> tuple[bool, str]:
        """Restore a specific version of a file to the working tree."""
        store = self._get_file_store(rel_path)
        return store.checkout(version)

    # ── Log / History ─────────────────────────────────────────────────

    def get_file_log(self, rel_path: str) -> list:
        """Get revision history for a file, newest first."""
        store = self._get_file_store(rel_path)
        return store.list_revisions()

    def get_summary(self) -> dict:
        """Get a summary of all files and their revision counts."""
        summary = {}
        for rel_path in self._global["files"]:
            store = self._get_file_store(rel_path)
            summary[rel_path] = {
                "revision_count": store.get_revision_count(),
                "active_version": store.get_active_version(),
                "has_originals": store.has_originals(),
            }
        return summary

    # ── Status / Info ─────────────────────────────────────────────────

    def summary(self) -> str:
        """Return a human-readable summary of this snapshot run."""
        lines = [
            f"Run ID: {self.run_id}",
            f"Description: {self.description}",
            f"Created: {self._global.get('created', '?')}",
            f"Files tracked: {len(self._global['files'])}",
            f"Tasks executed: {len(self._global['tasks'])}",
            f"Applied: {self._global.get('applied', False)}",
            f"Reverted: {self._global.get('reverted', False)}",
            "",
            "Files:",
        ]
        for rel_path in self._global["files"]:
            store = self._get_file_store(rel_path)
            revs = store.get_revision_count()
            active = store.get_active_version()
            sealed_count = sum(1 for r in store._manifest["revisions"] if r["sealed"])
            lines.append(
                f"  {rel_path}: {revs} revision(s), "
                f"active=v{active}"
                f"{', sealed=' + str(sealed_count) if sealed_count else ''}"
            )
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

    print(f"{'RUN ID':<22} {'DESCRIPTION':<38} {'FILES':<7} {'TASKS':<7} {'APPLIED':<9} {'CREATED':<20}")
    print("-" * 105)
    for run_dir in runs:
        if not run_dir.is_dir():
            continue
        gmp = run_dir / "global_manifest.json"
        if not gmp.exists():
            continue
        try:
            m = json.loads(gmp.read_text(encoding="utf-8"))
            created = m.get("created", "?")[:19]
            print(
                f"{m.get('run_id', run_dir.name):<22} "
                f"{m.get('description', '')[:36]:<38} "
                f"{len(m.get('files', [])):<7} "
                f"{len(m.get('tasks', [])):<7} "
                f"{'Yes' if m.get('applied') else 'No':<9} "
                f"{created:<20}"
            )
        except Exception:
            pass


def _do_log(args: list):
    """Show revision history. Usage: log [run_id] [file]"""
    if len(args) < 1:
        # No args: list all runs (same as list)
        _list_snapshots()
        return

    run_id = args[0]
    run_dir = SNAPSHOT_DIR / run_id
    if not run_dir.exists():
        print(f"Run '{run_id}' not found.")
        return

    gmp = run_dir / "global_manifest.json"
    if not gmp.exists():
        print(f"No global manifest for run '{run_id}'.")
        return

    global_m = json.loads(gmp.read_text(encoding="utf-8"))

    if len(args) >= 2:
        # Per-file log
        file_path = args[1]
        store = FileRevisionStore(run_dir, file_path)
        revs = store.list_revisions()
        if not revs:
            print(f"No revisions for {file_path}")
            return
        active = store.get_active_version()
        print(f"\n  File: {file_path}")
        print(f"  Active: v{active}")
        print(f"  {'─' * 70}")
        for r in revs:
            v = r["version"]
            marker = "→" if v == active else " "
            sealed = "🔒" if r["sealed"] else " "
            ts = r["timestamp"][:19]
            msg = r["message"]
            by = r["committed_by"]
            hash_short = r["sha256"][:12]
            print(f"  {marker} v{v} {sealed}  {ts}  {msg}")
            print(f"           by {by}  sha256:{hash_short}")
        print()
    else:
        # Per-run summary
        print(f"\n  Run: {run_id} — {global_m.get('description', '')}")
        print(f"  Created: {global_m.get('created', '?')[:19]}")
        print(f"  {'─' * 70}")
        for rel_path in global_m.get("files", []):
            store = FileRevisionStore(run_dir, rel_path)
            revs = store.list_revisions()
            active = store.get_active_version()
            sealed_count = sum(1 for r in revs if r["sealed"])
            rev_info = f"{len(revs)} rev(s), active=v{active}"
            if sealed_count:
                rev_info += f", {sealed_count} sealed"
            # Show last message
            last_msg = revs[0]["message"][:50] if revs else "(no revisions)"
            print(f"  {rel_path:<40} {rev_info:<25} {last_msg}")
        print()


def _do_checkout(args: list):
    """Restore a specific version. Usage: checkout <run_id> <file> <version>"""
    if len(args) < 3:
        print("Usage: python pipeline_snapshot.py checkout <run_id> <file> <version>")
        return

    run_id, file_path, ver_str = args[0], args[1], args[2]
    try:
        version = int(ver_str)
    except ValueError:
        print(f"Invalid version: {ver_str}")
        return

    run_dir = SNAPSHOT_DIR / run_id
    if not run_dir.exists():
        print(f"Run '{run_id}' not found.")
        return

    snap = SnapshotManager(run_id)
    ok, msg = snap.checkout_version(file_path, version)
    if ok:
        print(f"  ✓ {msg}")
    else:
        print(f"  ✗ {msg}")


def _do_seal(args: list):
    """Seal a version. Usage: seal <run_id> <file> <version>"""
    if len(args) < 3:
        print("Usage: python pipeline_snapshot.py seal <run_id> <file> <version>")
        return

    run_id, file_path, ver_str = args[0], args[1], args[2]
    try:
        version = int(ver_str)
    except ValueError:
        print(f"Invalid version: {ver_str}")
        return

    run_dir = SNAPSHOT_DIR / run_id
    if not run_dir.exists():
        print(f"Run '{run_id}' not found.")
        return

    snap = SnapshotManager(run_id)
    ok = snap.seal_version(file_path, version)
    if ok:
        print(f"  ✓ Sealed {file_path} revision {version}")
    else:
        print(f"  ✗ Version {version} not found for {file_path}")


def _do_verify(args: list):
    """Verify integrity. Usage: verify [run_id]"""
    if len(args) < 1:
        # Verify all runs
        if not SNAPSHOT_DIR.exists():
            print("No snapshots found.")
            return
        all_ok = True
        for run_dir in sorted(SNAPSHOT_DIR.iterdir()):
            if not run_dir.is_dir():
                continue
            gmp = run_dir / "global_manifest.json"
            if not gmp.exists():
                continue
            m = json.loads(gmp.read_text(encoding="utf-8"))
            run_id = m.get("run_id", run_dir.name)
            snap = SnapshotManager(run_id)
            ok, errors = snap.verify_integrity()
            if ok:
                print(f"  ✓ {run_id}: integrity OK")
            else:
                all_ok = False
                print(f"  ✗ {run_id}: INTEGRITY FAILURE")
                for e in errors:
                    print(f"    {e}")
        if all_ok:
            print("\n  All runs verified successfully.")
        return

    run_id = args[0]
    run_dir = SNAPSHOT_DIR / run_id
    if not run_dir.exists():
        print(f"Run '{run_id}' not found.")
        return

    snap = SnapshotManager(run_id)
    ok, errors = snap.verify_integrity()
    if ok:
        print(f"  ✓ {run_id}: all files integrity OK")
    else:
        print(f"  ✗ {run_id}: INTEGRITY FAILURE")
        for e in errors:
            print(f"    {e}")


def _show_diff(run_id: str, file_filter: str = None):
    """Show diffs for a snapshot run."""
    run_dir = SNAPSHOT_DIR / run_id
    if not run_dir.exists():
        print(f"Run '{run_id}' not found.")
        return

    gmp = run_dir / "global_manifest.json"
    if not gmp.exists():
        print(f"No global manifest for run '{run_id}'.")
        return

    global_m = json.loads(gmp.read_text(encoding="utf-8"))
    files_to_diff = global_m.get("files", [])

    if file_filter:
        files_to_diff = [f for f in files_to_diff if file_filter in f]

    if not files_to_diff:
        print("No matching files found.")
        return

    for rel_path in files_to_diff:
        store = FileRevisionStore(run_dir, rel_path)
        if store.get_revision_count() < 2:
            continue
        diff_text = store.diff_active_vs_original()
        if diff_text.strip():
            print(f"\n{'='*70}")
            print(f"  Diff: {rel_path}")
            print(f"{'='*70}")
            print(diff_text)


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
    args = sys.argv[2:]

    if command == "list":
        _list_snapshots()

    elif command == "log":
        _do_log(args)

    elif command == "checkout":
        _do_checkout(args)

    elif command == "seal":
        _do_seal(args)

    elif command == "verify":
        _do_verify(args)

    elif command == "diff":
        if len(args) < 1:
            print("Usage: python pipeline_snapshot.py diff <run_id> [file_filter]")
            return
        run_id = args[0]
        file_filter = args[1] if len(args) > 1 else None
        _show_diff(run_id, file_filter)

    elif command == "revert":
        if len(args) < 1:
            print("Usage: python pipeline_snapshot.py revert <run_id>")
            return
        _do_revert(args[0])

    elif command == "advance":
        if len(args) < 1:
            print("Usage: python pipeline_snapshot.py advance <run_id>")
            return
        _do_advance(args[0])

    elif command == "show":
        if len(args) < 1:
            print("Usage: python pipeline_snapshot.py show <run_id>")
            return
        run_dir = SNAPSHOT_DIR / args[0]
        if not run_dir.exists():
            print(f"Run '{args[0]}' not found.")
            return
        snap = SnapshotManager(args[0])
        print(snap.summary())

    else:
        print(f"Unknown command: {command}")
        print("Available: list, log, checkout, seal, verify, diff, revert, advance, show")


if __name__ == "__main__":
    main()


