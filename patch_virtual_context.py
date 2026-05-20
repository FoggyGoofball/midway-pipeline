#!/usr/bin/env python3
"""
patch_virtual_context.py — Virtual Context Management System Patcher
=====================================================================
Surgically modifies pipeline.py to implement OS-level memory paging
for the TokenBudget system. Replaces destructive pruning with lossless
disk-based offloading and agent-retrievable context blocks.

Usage:
    python patch_virtual_context.py          # Patch pipeline.py in place
    python patch_virtual_context.py --dry-run  # Preview changes without writing
    python patch_virtual_context.py --revert   # Restore from backup
    python patch_virtual_context.py --verify   # Verify patches are applied

Large string payloads live in patch_payloads.py to keep this file under
1 000 lines. Import direction: patch_virtual_context -> patch_payloads
(one-way; patch_payloads has no imports from the pipeline runtime).
"""

import sys
import shutil
from pathlib import Path

from patch_payloads import (
    OFFLOAD_STORE_CLASS,
    BLOCK_AWARE_COLLAPSE_REPLACEMENT,
    READ_OFFLOADED_FUNCTION,
    READ_OFFLOADED_SIGNAL_PATTERN_REPLACEMENT,
    READ_OFFLOADED_HANDLER_BLOCK,
    AGENT_PROMPT_EXTENSION,
)

PROJECT_ROOT = Path(__file__).parent.resolve()
PIPELINE_PATH = PROJECT_ROOT / "pipeline.py"
BACKUP_PATH   = PROJECT_ROOT / "pipeline.py.virtual_context_backup"
OFFLOAD_DIR   = PROJECT_ROOT / "offload_store"
GITIGNORE_PATH = PROJECT_ROOT / ".gitignore"


# ── Filesystem helpers ────────────────────────────────────────────────────────

def create_backup() -> bool:
    """Create a backup of the original pipeline.py."""
    if not PIPELINE_PATH.is_file():
        print(f"  [Error] {PIPELINE_PATH} not found.")
        return False
    try:
        shutil.copy2(str(PIPELINE_PATH), str(BACKUP_PATH))
        print(f"  [Backup] Created: {BACKUP_PATH}")
        return True
    except OSError as e:
        print(f"  [Error] Backup failed: {e}")
        return False


def add_to_gitignore() -> None:
    """Ensure offload_store/ is in .gitignore."""
    if not GITIGNORE_PATH.is_file():
        return
    content = GITIGNORE_PATH.read_text(encoding="utf-8")
    patterns = ["offload_store/", "offload_store/*.json"]
    added = []
    for pat in patterns:
        if pat not in content:
            content += f"\n{pat}"
            added.append(pat)
    if added:
        GITIGNORE_PATH.write_text(content, encoding="utf-8")
        print(f"  [GitIgnore] Added: {', '.join(added)}")


def create_offload_store_dir() -> None:
    """Create the offload_store directory with a .gitkeep."""
    OFFLOAD_DIR.mkdir(parents=True, exist_ok=True)
    gitkeep = OFFLOAD_DIR / ".gitkeep"
    if not gitkeep.is_file():
        gitkeep.write_text("", encoding="utf-8")
    print(f"  [OffloadStore] Created directory: {OFFLOAD_DIR}")


# ── Patch application ────────────────────────────────────────────────────────

def apply_patches(dry_run: bool = False) -> bool:
    """Apply all surgical patches to pipeline.py.

    Args:
        dry_run: If True, only report what would be changed without writing.

    Returns:
        True if all patches were applied successfully.
    """
    if not PIPELINE_PATH.is_file():
        print(f"  [Error] {PIPELINE_PATH} not found.")
        return False

    original = PIPELINE_PATH.read_text(encoding="utf-8")
    patched = original
    modifications = 0

    # ── PATCH 1: Add OffloadStore class ──────────────────────────────────────
    anchor1 = "# ── LRU Doc Cache ───────────────────────────────────────────"
    if anchor1 in patched:
        insertion_point = patched.find(anchor1)
        patched = patched[:insertion_point] + OFFLOAD_STORE_CLASS + "\n\n" + patched[insertion_point:]
        modifications += 1
        print(f"  [Patch 1/6] ✓ Inserted OffloadStore class before LRU Doc Cache")
    else:
        print(f"  [Patch 1/6] ✗ Could not find anchor: '{anchor1}'")

    # ── PATCH 2: Replace _block_aware_collapse ────────────────────────────────
    old_method_start = "    def _block_aware_collapse(self, text: str, available_chars: int) -> str:"
    if old_method_start in patched:
        start_idx = patched.find(old_method_start)
        def_add_idx = patched.find(
            "\n    def add(self, text: str, label: str = \"\") -> str:", start_idx
        )
        if def_add_idx > start_idx:
            before = patched[:start_idx]
            after  = patched[def_add_idx:]
            patched = before + BLOCK_AWARE_COLLAPSE_REPLACEMENT + "\n" + after
            modifications += 1
            print(f"  [Patch 2/6] ✓ Replaced _block_aware_collapse with offloading version")
        else:
            print(f"  [Patch 2/6] ✗ Could not find 'def add(' after _block_aware_collapse")
    else:
        print(f"  [Patch 2/6] ✗ Could not find _block_aware_collapse method")

    # ── PATCH 3: Insert read_offloaded_file + helpers ─────────────────────────
    anchor3 = "def handle_fetch_signal(fetch_tag: str) -> str:"
    if anchor3 in patched:
        fetch_func_end = patched.find("\n# ── Rule-Breaker Accommodation:", patched.find(anchor3))
        if fetch_func_end == -1:
            fetch_func_end = patched.find("\ndef ", patched.find(anchor3) + 10)
        if fetch_func_end == -1:
            fetch_func_end = len(patched)
        patched = patched[:fetch_func_end] + "\n" + READ_OFFLOADED_FUNCTION + patched[fetch_func_end:]
        modifications += 1
        print(f"  [Patch 3/6] ✓ Inserted read_offloaded_file and helper functions")
    else:
        print(f"  [Patch 3/6] ✗ Could not find handle_fetch_signal")

    # ── PATCH 4: Add READ_OFFLOADED to SIGNAL_PATTERNS ───────────────────────
    old_fetch_pattern = '    "FETCH": r"\\[FETCH:([^\\]]+)#([^\\]]+)\\"],\n}'
    if old_fetch_pattern in patched:
        patched = patched.replace(old_fetch_pattern, READ_OFFLOADED_SIGNAL_PATTERN_REPLACEMENT)
        modifications += 1
        print(f"  [Patch 4/6] ✓ Added READ_OFFLOADED to SIGNAL_PATTERNS")
    else:
        sp_start = patched.find("SIGNAL_PATTERNS = {")
        if sp_start > 0:
            sp_end = patched.find("\n}", sp_start)
            if sp_end > sp_start and "READ_OFFLOADED" not in patched[sp_start:sp_end]:
                patched = (
                    patched[:sp_end]
                    + ',\n    "READ_OFFLOADED": r"\\[READ_OFFLOADED:([^\\]]+)\\"],\n}'
                    + patched[sp_end+2:]
                )
                modifications += 1
                print(f"  [Patch 4/6] ✓ Added READ_OFFLOADED to SIGNAL_PATTERNS (fallback)")
            elif "READ_OFFLOADED" in patched[sp_start:sp_end]:
                print(f"  [Patch 4/6] ⚠ READ_OFFLOADED already in SIGNAL_PATTERNS")
            else:
                print(f"  [Patch 4/6] ✗ Could not parse SIGNAL_PATTERNS structure")
        else:
            print(f"  [Patch 4/6] ✗ Could not find SIGNAL_PATTERNS definition")

    # ── PATCH 5: Add READ_OFFLOADED handler in mesh loop ─────────────────────
    fetch_handler_end = (
        "                print(f\"  [FETCH] Routed to DOC oracle "
        "(depth {fetch_depth+1}/3): {fetch_target[:80]}...\")"
    )
    if fetch_handler_end in patched:
        insert_pos = patched.find(fetch_handler_end) + len(fetch_handler_end)
        patched = patched[:insert_pos] + READ_OFFLOADED_HANDLER_BLOCK + patched[insert_pos:]
        modifications += 1
        print(f"  [Patch 5/6] ✓ Added READ_OFFLOADED handler in mesh execution loop")
    else:
        fetch_close = "                continue\n\n        # Check double-check for unresolved items"
        if fetch_close in patched:
            insert_pos = patched.find(fetch_close)
            patched = patched[:insert_pos] + READ_OFFLOADED_HANDLER_BLOCK + patched[insert_pos:]
            modifications += 1
            print(f"  [Patch 5/6] ✓ Added READ_OFFLOADED handler (fallback anchor)")
        else:
            print(f"  [Patch 5/6] ✗ Could not find FETCH handler location")

    # ── PATCH 6: Extend MESH_AGENT_SYSTEM_EXTENSION ──────────────────────────
    fetch_signal_line = (
        '    "- [FETCH:<filepath>#<HeaderName>] — Recall context from your '
        'disk-based memory ledger. Does NOT count against iteration limit.\\n"'
    )
    if fetch_signal_line in patched:
        insert_pos = patched.find(fetch_signal_line) + len(fetch_signal_line)
        patched = patched[:insert_pos] + "\n" + AGENT_PROMPT_EXTENSION + patched[insert_pos:]
        modifications += 1
        print(f"  [Patch 6/6] ✓ Extended MESH_AGENT_SYSTEM_EXTENSION with READ_OFFLOADED tool")
    else:
        print(f"  [Patch 6/6] ✗ Could not find FETCH signal description in agent prompt")

    # ── Write output ──────────────────────────────────────────────────────────
    if modifications == 0:
        print(f"\n  [Result] No patches applied. Is the file already patched?")
        return False

    if dry_run:
        print(f"\n  [Dry-Run] {modifications}/6 patches would be applied. No files written.")
        print(f"  [Dry-Run] To apply, run: python patch_virtual_context.py")
        return True

    try:
        PIPELINE_PATH.write_text(patched, encoding="utf-8")
        print(f"\n  [Result] Successfully applied {modifications}/6 patches to {PIPELINE_PATH.name}")
        create_offload_store_dir()
        add_to_gitignore()
        print(f"  [Result] offload_store/ directory created and added to .gitignore")
        return True
    except OSError as e:
        print(f"\n  [Error] Failed to write patched file: {e}")
        return False


# ── Backup/revert ─────────────────────────────────────────────────────────────

def revert_backup() -> bool:
    """Restore pipeline.py from backup."""
    if not BACKUP_PATH.is_file():
        print(f"  [Error] No backup found at {BACKUP_PATH}")
        return False
    try:
        shutil.copy2(str(BACKUP_PATH), str(PIPELINE_PATH))
        print(f"  [Revert] Restored {PIPELINE_PATH} from backup")
        return True
    except OSError as e:
        print(f"  [Error] Revert failed: {e}")
        return False


# ── Verification ──────────────────────────────────────────────────────────────

def verify_patches() -> bool:
    """Verify that all patches were applied correctly."""
    if not PIPELINE_PATH.is_file():
        return False
    content = PIPELINE_PATH.read_text(encoding="utf-8")
    checks = [
        ("OffloadStore class",           "class OffloadStore:" in content),
        ("_OFFLOAD_STORE singleton",      "_OFFLOAD_STORE = OffloadStore()" in content),
        ("get_offload_store()",           "def get_offload_store()" in content),
        ("_offload_single_block method",  "def _offload_single_block" in content),
        ("OFFLOADED_CONTEXT placeholder", "<OFFLOADED_CONTEXT>" in content),
        ("read_offloaded_file()",         "def read_offloaded_file" in content),
        ("READ_OFFLOADED signal pattern", "READ_OFFLOADED" in content and 'r"\\[READ_OFFLOADED:' in content),
        ("READ_OFFLOADED handler",        'elif stype == "READ_OFFLOADED":' in content),
        ("Agent prompt extension",        "[READ_OFFLOADED:<block_id>]" in content),
        ("Bi-directional paging",         "_page_out_context" in content or "page_out" in content),
    ]
    passed = sum(1 for _, ok in checks if ok)
    total  = len(checks)
    print(f"\n  [Verification] {passed}/{total} checks passed:")
    for label, ok in checks:
        print(f"    {'✓' if ok else '✗'} {label}")
    return passed == total


# ── CLI entry point ───────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Apply Virtual Context Management patches to pipeline.py"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview changes without modifying files")
    parser.add_argument("--revert",  action="store_true",
                        help="Restore pipeline.py from backup")
    parser.add_argument("--verify",  action="store_true",
                        help="Verify that patches are applied correctly")
    args = parser.parse_args()

    if args.revert:
        sys.exit(0 if revert_backup() else 1)

    if args.verify:
        sys.exit(0 if verify_patches() else 1)

    if not create_backup():
        sys.exit(1)

    success = apply_patches(dry_run=args.dry_run)
    if success:
        verify_patches()
        print(f"\n  [Done] Virtual Context Management system installed.")
        print(f"  [Info] Backup saved to: {BACKUP_PATH.name}")
        print(f"  [Info] Offload store:   offload_store/")
        if args.dry_run:
            print(f"  [Info] Run without --dry-run to apply changes.")
    else:
        print(f"\n  [Failed] Some patches could not be applied.")
        print(f"  [Info] Original file preserved at: {PIPELINE_PATH.name}")
        sys.exit(1)


if __name__ == "__main__":
    main()
