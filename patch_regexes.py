"""
patch_regexes.py — Regex Robustness Patch
==========================================
Hardens brittle regex patterns across the pipeline codebase to prevent
catastrophic parsing failures from LLM formatting hallucinations.
Applies exact string replacements using standard .replace(old, new).
"""

import os
import sys
import re

# ── Target files (relative to this script's directory) ────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

files_to_patch = {}

# ── mesh_loops.py: task_regex — flexible spacing and dash variants ─────
mesh_loops_path = os.path.join(BASE_DIR, "mesh_loops.py")
files_to_patch[mesh_loops_path] = []

# Replacement 1: task_regex — tolerate extra spaces and any dash variant
files_to_patch[mesh_loops_path].append((
    'task_regex = r"### Task (\\d+): \\[([^\\]]+)\\] — (.+?)(?:\\s*\\(DependsOn:\\s*(.+?)\\))?\\s*$"',
    'task_regex = r"### Task (\\d+):\\s*\\[([^\\]]+)\\]\\s*[-—–]\\s*(.+?)(?:\\s*\\(DependsOn:\\s*(.+?)\\))?\\s*$"',
))

# Replacement 2: dep_match — case-insensitive matching
files_to_patch[mesh_loops_path].append((
    "dep_match = re.search(r'Task\\s*(\\d+)', dep)",
    "dep_match = re.search(r'Task\\s*(\\d+)', dep, re.IGNORECASE)",
))

# Replacement 3: test_match code block — tolerate language variants and whitespace
files_to_patch[mesh_loops_path].append((
    'test_match = re.search(r"```(?:cpp)?\\s*\\n(.*?)```", test_code, re.DOTALL)',
    'test_match = re.search(r"```(?:cpp|C\\+\\+|cxx)?\\s*\\n(.*?)```", test_code, re.DOTALL)',
))

# ── mesh_finalize.py: blueprint task regex — flexible spacing ──────────
mesh_finalize_path = os.path.join(BASE_DIR, "mesh_finalize.py")
files_to_patch[mesh_finalize_path] = []

# Replacement 4: blueprint next_match — tolerate variable whitespace
files_to_patch[mesh_finalize_path].append((
    "next_match = re.search(r\"- \\[ \\] (Task \\d+: .+)\", bp_content)",
    "next_match = re.search(r\"- \\[ \\]\\s*(Task \\d+:\\s*.+)\", bp_content)",
))

# Replacement 5: FLUSH entries split — multiline flag stability
entries_path = os.path.join(BASE_DIR, "mesh_finalize.py")
# Check if this file already has the pattern
# This replacement ensures entries split is more robust
files_to_patch[entries_path].append((
    "entries = re.split(r'(?=^## Session Event)', content, flags=re.MULTILINE)",
    "entries = re.split(r'(?=^##\\s*Session\\s*Event)', content, flags=re.MULTILINE)",
))


# ── Apply all replacements ─────────────────────────────────────────────
# ── SEARCH/REPLACE conflict-marker regex (for Architect agent output) ──
SEARCH_REPLACE_PATTERN = re.compile(r"<<<<<<<\s*SEARCH\n(.*?)\n=======\n(.*?)\n>>>>>>>\s*(?:REPLACE)?", re.DOTALL)


def apply_patches():
    patched_count = 0
    error_count = 0

    for filepath, replacements in files_to_patch.items():
        if not os.path.isfile(filepath):
            print(f"  [SKIP] File not found: {filepath}")
            continue

        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

        original = content
        file_patched = False

        for old, new in replacements:
            # Count occurrences before replacement
            count = content.count(old)
            if count > 0:
                content = content.replace(old, new)
                print(f"  [PATCH] {os.path.basename(filepath)}: replaced {count} occurrence(s)")
                print(f"    OLD: {old[:70]}...")
                print(f"    NEW: {new[:70]}...")
                file_patched = True
                patched_count += 1
            else:
                print(f"  [SKIP] {os.path.basename(filepath)}: pattern not found")
                print(f"    PATTERN: {old[:70]}...")

        if file_patched:
            # Write back atomically: write to temp, then rename
            tmp_path = filepath + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp_path, filepath)
            print(f"  [WRITE] {os.path.basename(filepath)}: saved ({len(content)} bytes)")

    print(f"\n{'='*50}")
    print(f"  Patch Results: {patched_count} replacements applied")
    if error_count:
        print(f"  Errors: {error_count}")
    print(f"{'='*50}")
    return patched_count


if __name__ == "__main__":
    print(f"{'='*50}")
    print(f"  Regex Hardening Patch Script")
    print(f"  Base directory: {BASE_DIR}")
    print(f"{'='*50}")
    apply_patches()
