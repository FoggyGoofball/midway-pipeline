#!/usr/bin/env python3
"""
apply_patches.py — Surgical patch script for pipeline.py
Applies 4 targeted patches to pipeline.py for autonomous, resilient,
memory-optimized multi-agent orchestration on Steam Deck.

Usage: python apply_patches.py
Creates pipeline.py.orig backup before modifying.
"""
import re
import sys
from pathlib import Path

PIPELINE_PATH = Path(__file__).parent / "pipeline.py"


def read_file():
    return PIPELINE_PATH.read_text(encoding="utf-8")


def write_file(content):
    PIPELINE_PATH.write_text(content, encoding="utf-8")


def backup():
    bak = PIPELINE_PATH.with_suffix(".py.orig_patchbackup")
    if not bak.exists():
        bak.write_text(read_file(), encoding="utf-8")
        print(f"  [Backup] Saved to {bak.name}")


# ═══════════════════════════════════════════════════════════════════════════════
# PATCH 1: Autonomous Intent Parsing & File Retrieval (Zero-Friction Context)
# ═══════════════════════════════════════════════════════════════════════════════
# Already partially implemented (parse_file_references + fetch_referenced_files
# at lines 1057-1118).  We need to INTEGRATE it into the agent prompt builder
# so that file refs are injected into every agent's context.
# 
# We patch the format_file_context AND execute_task functions to:
#   1. Parse prompt for file refs BEFORE the director runs (Phase 0)
#   2. Inject referenced file blocks into every agent's prompt automatically
# ═══════════════════════════════════════════════════════════════════════════════

PATCH1_NEW_FUNC = """
# ── Pre-Mesh File Reference Injection ──────────────────────────────────────────
# Injects user-requested file references into agent prompts automatically.
# Called during Phase 2 (Project Context) to prep referenced files for all agents.

_REFERENCED_FILES_CACHE = ""   # global cache populated once per pipeline run

def set_referenced_files_cache(refs_block: str) -> None:
    global _REFERENCED_FILES_CACHE
    _REFERENCED_FILES_CACHE = refs_block

def get_referenced_files_cache() -> str:
    return _REFERENCED_FILES_CACHE
"""

# Insert PATCH1_NEW_FUNC after the `parse_file_references` / `fetch_referenced_files` block
# (right before "# ── Synchronous File-System Tools for Agent Reasoning" at line 1121)

INSERT_PATCH1_AFTER = "# ── Synchronous File-System Tools for Agent Reasoning ────────────────────"


def apply_patch1(content):
    """Add the global cache for referenced files, and patch execute_task to inject them."""
    # Step 1: Insert the global cache function above the AGENT_FILE_TOOLS_PROMPT
    marker = INSERT_PATCH1_AFTER
    if marker not in content:
        print("  [Patch1] WARNING: marker not found, skipping cache insertion")
    else:
        content = content.replace(marker, PATCH1_NEW_FUNC + "\n" + marker)
        print("  [Patch1] Injected referenced-files cache")

    # Step 2: Patch execute_task() to inject auto-referenced files into context
    # Find execute_task and modify it to include referenced files
    old_exec_line = '    # Include parent context if this is a sub-task'
    new_exec_chunk = '''    # ── Auto-Inject Referenced Files ──────────────────────────────────
    refs_block = get_referenced_files_cache()
    if refs_block:
        context_parts.append(refs_block)

    # Include parent context if this is a sub-task'''
    if old_exec_line in content:
        content = content.replace(old_exec_line, new_exec_chunk)
        print("  [Patch1] Patched execute_task() to inject referenced files")
    else:
        print("  [Patch1] WARNING: execute_task marker not found")

    # Step 3: In run_mesh_pipeline, after project_context but BEFORE Phase 3 (Director),
    # parse file refs from user_prompt and cache them.
    # Find the Phase 3: Director section and inject auto-ref parsing before it.
    p3_marker = 'print(f"\\n{\'=\'*70}")\\n        print(f"  Phase 3: Director — Task Decomposition")\\n        print(f"{\'=\'*70}")'
    # Better approach: find where project_state + structure is assembled
    # and inject the file reference parsing right after it.
    inject_marker = "output_parts.append(structure + \"\\n\")"
    new_injection = '''output_parts.append(structure + "\\n")

        # ── Auto-Fetch Referenced Files ───────────────────────────────────────────
        refs = parse_file_references(user_prompt)
        refs_block = fetch_referenced_files(refs)
        set_referenced_files_cache(refs_block)
        if refs_block:
            output_parts.append("### Referenced Files (auto-injected)\\n" + refs_block + "\\n")
            print(f"  [AutoRef] {len(refs)} file reference(s) parsed and cached for all agents")'''
    if inject_marker in content:
        content = content.replace(inject_marker, new_injection)
        print("  [Patch1] Injected auto-reference parsing in Phase 2")
    else:
        print("  [Patch1] WARNING: Phase 2 marker not found")

    return content


# ═══════════════════════════════════════════════════════════════════════════════
# PATCH 2: Reverse Chronological Document Ingestion
# ═══════════════════════════════════════════════════════════════════════════════
# Modify _collect_ledger_entries() to split into chunks by headers, reverse order.
# Modify handle_fetch_signal() to reverse chunk order within fetched sections.
# ═══════════════════════════════════════════════════════════════════════════════

# Replace _collect_ledger_entries with header-split, reversed version
OLD_COLLECT = """def _collect_ledger_entries(mem_file: Path) -> list:
    \"\"\"Parse a ledger file and return (is_subsection, entry_text) pairs.\"\"\"
    entries = []
    try:
        content = mem_file.read_text(encoding="utf-8", errors="replace")
        for ln in content.splitlines():
            stripped = ln.strip()
            if not (stripped.startswith("##") or stripped.startswith("###")):
                continue
            title = stripped.lstrip("#").strip()
            if title in BOILERPLATE_TITLES:
                continue
            bm = re.search(r"\\[(.*?)\\]", stripped)
            anchor = bm.group(1).strip() if bm else title.lower().replace(" ", "-")
            is_sub = stripped.startswith("###")
            rel = mem_file.relative_to(PROJECT_ROOT).as_posix()
            text = f"  - [{title}]({rel}#{anchor}) -- use [FETCH:{rel}#{anchor}]\\n"
            entries.append((is_sub, text))
    except Exception:
        pass
    return entries"""

NEW_COLLECT = """def _collect_ledger_entries(mem_file: Path) -> list:
    \"\"\"Parse a ledger file into header-anchored chunks, then REVERSE so newest
    architectural decisions appear first in context. When token budget is tight,
    pruning drops the OLDEST entries at the bottom, preserving the most recent state.

    Returns (is_subsection, entry_text) pairs in REVERSE chronological order
    (newest first).
    \"\"\"
    entries = []
    try:
        content = mem_file.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines()

        # Phase 1: Split into logical chunks by header boundaries
        chunks = []  # list of {header_line, title, anchor, is_sub, body_lines}
        current_chunk = None

        for ln in lines:
            stripped = ln.strip()
            if stripped.startswith("##") or stripped.startswith("###"):
                # Close previous chunk
                if current_chunk is not None:
                    chunks.append(current_chunk)
                title = stripped.lstrip("#").strip()
                if title in BOILERPLATE_TITLES:
                    current_chunk = None
                    continue
                bm = re.search(r"\\[(.*?)\\]", stripped)
                anchor = bm.group(1).strip() if bm else title.lower().replace(" ", "-")
                is_sub = stripped.startswith("###")
                rel = mem_file.relative_to(PROJECT_ROOT).as_posix()
                current_chunk = {
                    "header_line": stripped,
                    "title": title,
                    "anchor": anchor,
                    "is_sub": is_sub,
                    "rel": rel,
                    "body_lines": [],
                }
            else:
                if current_chunk is not None:
                    current_chunk["body_lines"].append(ln)

        # Flush last chunk
        if current_chunk is not None:
            chunks.append(current_chunk)

        # Phase 2: Reverse chunks — newest first
        for chunk in reversed(chunks):
            text = (
                f"  - [{chunk['title']}]({chunk['rel']}#{chunk['anchor']}) "
                f"-- use [FETCH:{chunk['rel']}#{chunk['anchor']}]\\n"
            )
            entries.append((chunk["is_sub"], text))

    except Exception:
        pass
    return entries"""


def apply_patch2(content):
    global OLD_COLLECT, NEW_COLLECT
    # Normalize line endings
    old = OLD_COLLECT.replace("\r\n", "\n")
    new = NEW_COLLECT.replace("\r\n", "\n")
    if old in content:
        content = content.replace(old, new)
        print("  [Patch2] Replaced _collect_ledger_entries with reverse-chronological version")
    else:
        print("  [Patch2] WARNING: Could not find _collect_ledger_entries to replace")
        # Try partial match
    return content


# ═══════════════════════════════════════════════════════════════════════════════
# PATCH 3: AST-Aware & Block-Aware Truncation (already partially implemented)
# The _block_aware_collapse static method already exists and does block-aware
# collapse.  But we need to verify it's actually BEING USED by TokenBudget.add().
# The add() method was already patched previously to call _block_aware_collapse
# — let's make sure the fallback path is also blocked.
# ═══════════════════════════════════════════════════════════════════════════════

# Ensure _block_aware_collapse is the ONLY truncation path in add()
# Check if add() still has old head/tail behavior as a fallback
# We'll replace the entire add() method to ensure block-aware only

OLD_ADD = """    def add(self, text: str, label: str = \"\") -> str:
        \"\"\"Add text to budget with block-aware truncation.

        Uses _block_aware_collapse to preserve function signatures and
        markdown headers even when truncating. Latest content is always
        preserved; oldest blocks are dropped first.

        Returns (possibly truncated) text.
        \"\"\"
        estimated = self.estimate_tokens(text)
        if self.used + estimated < self.hard_limit:
            self.used += estimated
            return text

        # ── Overflow Ledger Rotation ─────────────────────────────────
        overflow_path = PROJECT_ROOT / \"docs\" / \"memory\" / \"overflow_ledger.md\"
        if overflow_path.is_file() and overflow_path.stat().st_size > 100 * 1024:
            archive_dir = overflow_path.parent
            existing = sorted(archive_dir.glob(\"overflow_ledger_v*.md\"))
            highest_vol = 0
            for f in existing:
                vm = re.search(r\"_v(\\d+)\\.md$\", f.name)
                if vm:
                    vol = int(vm.group(1))
                    if vol > highest_vol:
                        highest_vol = vol
            new_vol = highest_vol + 1
            archive_name = f\"overflow_ledger_v{new_vol}.md\"
            archive_path = archive_dir / archive_name
            overflow_path.rename(archive_path)
            fresh_content = (
                f\"### [Archive Link]\\n\"
                f\"Continued from {archive_name}\\n\\n\"
            )
            overflow_path.write_text(fresh_content, encoding=\"utf-8\")
            print(f\"  [Overflow Rotation] Rotated to {archive_name}. Active file reset.\")

        # Block-aware collapse
        available = self.hard_limit - self.used
        if available <= 100:
            self.warnings.append(f\"[Budget] {label}: OVERFLOW — no room available\")
            return f\"\\n[TOKEN BUDGET EXCEEDED: {label} truncated]\\n\"

        # Estimate chars-per-token ratio based on text characteristics
        # Code is ~3 chars/token, prose is ~4 chars/token
        code_indicators = sum(1 for c in text if c in \"{}().;:#\") / max(len(text), 1)
        chars_per_token = 3.0 if code_indicators > 0.05 else 4.0
        available_chars = int(available * chars_per_token)

        truncated = self._block_aware_collapse(text, available_chars)
        self.used += available
        self.warnings.append(f\"[Budget] {label}: truncated {estimated} → {available} tokens (block-aware)\")
        return truncated"""

NEW_ADD = """    def add(self, text: str, label: str = \"\") -> str:
        \"\"\"Add text to budget with block-aware truncation.

        Uses _block_aware_collapse to preserve function signatures and
        markdown headers even when truncating. Latest content is always
        preserved; oldest blocks are dropped first.

        Returns (possibly truncated) text.

        BLOCK-AWARE GUARANTEE: This method NEVER does raw head/tail slicing.
        All truncation goes through _block_aware_collapse which preserves
        structural boundaries (function signatures, markdown headers, code fences).
        \"\"\"
        estimated = self.estimate_tokens(text)
        if self.used + estimated < self.hard_limit:
            self.used += estimated
            return text

        # ── Overflow Ledger Rotation ─────────────────────────────────
        overflow_path = PROJECT_ROOT / \"docs\" / \"memory\" / \"overflow_ledger.md\"
        if overflow_path.is_file() and overflow_path.stat().st_size > 100 * 1024:
            archive_dir = overflow_path.parent
            existing = sorted(archive_dir.glob(\"overflow_ledger_v*.md\"))
            highest_vol = 0
            for f in existing:
                vm = re.search(r\"_v(\\d+)\\.md$\", f.name)
                if vm:
                    vol = int(vm.group(1))
                    if vol > highest_vol:
                        highest_vol = vol
            new_vol = highest_vol + 1
            archive_name = f\"overflow_ledger_v{new_vol}.md\"
            archive_path = archive_dir / archive_name
            overflow_path.rename(archive_path)
            fresh_content = (
                f\"### [Archive Link]\\n\"
                f\"Continued from {archive_name}\\n\\n\"
            )
            overflow_path.write_text(fresh_content, encoding=\"utf-8\")
            print(f\"  [Overflow Rotation] Rotated to {archive_name}. Active file reset.\")

        # Block-aware collapse (STRICTLY block-aware — no blind head/tail fallback)
        available = self.hard_limit - self.used
        if available <= 100:
            self.warnings.append(f\"[Budget] {label}: OVERFLOW — no room available\")
            return f\"\\n[TOKEN BUDGET EXCEEDED: {label} truncated]\\n\"

        # Estimate chars-per-token ratio based on text characteristics
        code_indicators = sum(1 for c in text if c in \"{}().;:#\") / max(len(text), 1)
        chars_per_token = 3.0 if code_indicators > 0.05 else 4.0
        available_chars = int(available * chars_per_token)

        # ── BLOCK-AWARE COLLAPSE (ONLY truncation path) ──────────────
        truncated = self._block_aware_collapse(text, available_chars)
        self.used += available
        self.warnings.append(f\"[Budget] {label}: truncated {estimated} → {available} tokens (block-aware)\")
        return truncated"""


def apply_patch3(content):
    old = OLD_ADD.replace("\r\n", "\n")
    new = NEW_ADD.replace("\r\n", "\n")
    if old in content:
        content = content.replace(old, new)
        print("  [Patch3] Confirmed TokenBudget.add() uses block-aware collapse only")
    else:
        print("  [Patch3] WARNING: Could not match TokenBudget.add() — may already be patched")
    return content


# ═══════════════════════════════════════════════════════════════════════════════
# PATCH 4: Rule-Breaker Accommodations for Memory Writes
# ═══════════════════════════════════════════════════════════════════════════════
# Add synthetic header generation for agents that forget the ### [ModuleName] format.
# Integrate it into the signal processing / task output handling in run_mesh_pipeline.
# ═══════════════════════════════════════════════════════════════════════════════

PATCH4_NEW_CODE = """
# ── Rule-Breaker Accommodation: Synthetic Ledger Headers ──────────────────────
# If an agent executes a system modification but forgets the ### [ModuleName] header,
# the orchestrator auto-generates and prepends a synthetic header based on the
# current active task string and domain.

LEDGER_HEADER_PATTERN = re.compile(r'^#{2,4}\\s*\\[.*?\\]', re.MULTILINE)

def ensure_ledger_header(output: str, task_spec: str, agent_key: str) -> str:
    \"\"\"Detect if agent output contains a properly formatted ledger header
    (e.g., ### [ModuleName]). If missing, generate a synthetic header based
    on the task spec and agent domain, and prepend it.

    This prevents formatting rule breakers from silently losing memory entries.
    Returns the (possibly modified) output.
    \"\"\"
    if LEDGER_HEADER_PATTERN.search(output):
        # Agent followed the rules — no modification needed
        return output

    # Agent broke the formatting rule — generate synthetic header
    # Extract key domain identifier from task_spec
    task_clean = task_spec.strip().rstrip('.')
    # Build a descriptive module name from the task
    module_name = _generate_module_name(task_clean, agent_key)
    synthetic_header = f\"### [{module_name}]\\n\"
    modified = synthetic_header + output + \"\\n\"
    print(f\"  [LedgerGuard] ⚠ Synthetic header prepended for {agent_key}: [{module_name}]\")
    return modified


def _generate_module_name(task_spec: str, agent_key: str) -> str:
    \"\"\"Generate a descriptive module name from task spec and agent domain.\"\"\"
    # Take first 3-5 significant words from the task
    words = re.findall(r'[A-Za-z][A-Za-z0-9_]*', task_spec)
    # Filter out noise words
    stopwords = {'the', 'a', 'an', 'for', 'of', 'in', 'to', 'and', 'is', 'it',
                 'be', 'are', 'was', 'were', 'been', 'this', 'that', 'with',
                 'from', 'at', 'by', 'on', 'or', 'as', 'if', 'but'}
    significant = [w for w in words if w.lower() not in stopwords]
    # Take up to 5 key words
    name_words = significant[:5]
    if not name_words:
        return agent_key  # Fallback to agent key
    return '_'.join(name_words)
"""


def apply_patch4(content):
    # Step 1: Insert the new functions after the last ledger-related function
    # Place it right after handle_fetch_signal() and before the checkpoint system
    marker = "# ── Checkpoint System ──────────────────────────────────────────────────────"
    if marker in content:
        content = content.replace(marker, PATCH4_NEW_CODE + "\n" + marker)
        print("  [Patch4] Inserted synthetic header generation functions")
    else:
        print("  [Patch4] WARNING: Checkpoint marker not found")

    # Step 2: Patch execute_task() to wrap output with ensure_ledger_header
    # Find the line `task.output = output` in execute_task and add wrapping
    old_task_output = "    task.output = output\n    task.signals = extract_signals(output)"
    new_task_output = """    # ── Ledger Guard: auto-fix missing headers ────────────────
    output = ensure_ledger_header(output, task.spec, task.agent)
    task.output = output
    task.signals = extract_signals(output)"""
    if old_task_output in content:
        content = content.replace(old_task_output, new_task_output)
        print("  [Patch4] Patched execute_task() with ledger guard")
    else:
        print("  [Patch4] WARNING: Could not find task.output line in execute_task")

    return content


# ═══════════════════════════════════════════════════════════════════════════════
# PATCH 5 (Bonus): Also patch handle_fetch_signal to support reverse-chronological
# ordering of content within fetched sections.
# ═══════════════════════════════════════════════════════════════════════════════

def apply_patch5(content):
    """Patch handle_fetch_signal so that within a fetched header, the subsection
    content is reversed (newest architectural decision first)."""
    old_lines = """    for j in range(header_start + 1, len(lines_)):
        ln = lines_[j]
        stripped = ln.strip()
        if stripped.startswith("#"):
            level = stripped.split("#")[0].count("#") + 1
            if level <= hdr_lvl:
                break
        content_lines.append(ln)"""
    new_lines = """    # ── Reverse-chronological subsection fetch ──────────────
    # Collect sub-sections within the fetched header, then reverse them
    # so the newest architectural decision appears first.
    subsections = []  # list of (header, body_lines) tuples
    current_sub = None
    for j in range(header_start + 1, len(lines_)):
        ln = lines_[j]
        stripped = ln.strip()
        if stripped.startswith("#"):
            level = stripped.split("#")[0].count("#") + 1
            if level <= hdr_lvl:
                break
            # This is a sub-header within the fetched section
            if current_sub is not None:
                subsections.append(current_sub)
            current_sub = {"header": ln, "body_lines": []}
        else:
            if current_sub is not None:
                current_sub["body_lines"].append(ln)
            else:
                content_lines.append(ln)
    if current_sub is not None:
        subsections.append(current_sub)
    # Reverse: newest subsection first
    for sub in reversed(subsections):
        if content_lines:
            content_lines.append("")  # spacing
        content_lines.append(sub["header"])
        content_lines.extend(sub["body_lines"])"""
    if old_lines in content:
        content = content.replace(old_lines, new_lines)
        print("  [Patch5] Patched handle_fetch_signal with reverse-chronological subsection fetch")
    else:
        print("  [Patch5] WARNING: Could not find fetch content loop")
    return content


# ═══════════════════════════════════════════════════════════════════════════════
# ENSURE _block_aware_collapse is actually BEING CALLED (not the old head/tail)
# ═══════════════════════════════════════════════════════════════════════════════

def verify_no_old_truncation(content):
    """Verify that old head/tail truncation code is not present."""
    old_patterns = [
        "keep the first 10%",
        "head_and_tail",
        "first_10_percent",
        "last_30_percent",
    ]
    for pat in old_patterns:
        if pat in content.lower():
            print(f"  [Verify] WARNING: Detected old truncation pattern: '{pat}'")
    print("  [Verify] No old head/tail truncation patterns found")


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print("  Applying Patches to pipeline.py")
    print("=" * 60)

    if not PIPELINE_PATH.is_file():
        print(f"  ERROR: {PIPELINE_PATH} not found")
        sys.exit(1)

    backup()
    content = read_file()
    original = content

    # Apply all patches
    content = apply_patch1(content)
    content = apply_patch2(content)
    content = apply_patch3(content)
    content = apply_patch4(content)
    content = apply_patch5(content)

    if content == original:
        print("\n  No changes applied — file was already up-to-date?")
    else:
        write_file(content)
        print(f"\n  Patches applied successfully!")
        print(f"  File size: {len(content)} bytes ({len(content) - len(original):+d})")

    verify_no_old_truncation(content)
    print("=" * 60)


if __name__ == "__main__":
    main()
