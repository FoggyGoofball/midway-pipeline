#!/usr/bin/env python3
"""
Single-source-of-truth line-number regeneration for pipeline reference docs.

Strategy:
  1. Scan pipeline.py for ALL definitions (functions, classes, constants)
  2. Build a fresh anchor index with accurate line numbers
  3. Overwrite docs/pipeline_anchor_index.md with the fresh data
  4. Update docs/pipeline_master_checklist.md line references
  5. Update docs/pipeline_agent_todo.md line references

This avoids "which doc is correct" reasoning — just use one ground truth.
"""

import re
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PIPELINE_PATH = os.path.join(SCRIPT_DIR, "pipeline.py")
ANCHOR_PATH = os.path.join(SCRIPT_DIR, "docs", "pipeline_anchor_index.md")
CHECKLIST_PATH = os.path.join(SCRIPT_DIR, "docs", "pipeline_master_checklist.md")
TODO_PATH = os.path.join(SCRIPT_DIR, "docs", "pipeline_agent_todo.md")

with open(PIPELINE_PATH, encoding="utf-8") as f:
    pipeline_lines = f.readlines()

total_lines = len(pipeline_lines)

# ============================================================
# STEP 1: Scan pipeline.py for ALL definitions
# ============================================================
def scan_definitions(lines):
    """Return dict of {name: line_number} for functions and classes."""
    defs = {}
    class_pattern = re.compile(r'^\s*class\s+(\w+)\s*(?:\(|:)')
    func_pattern = re.compile(r'^\s*def\s+(\w+)\s*\(')

    for i, line in enumerate(lines):
        lineno = i + 1
        m = class_pattern.match(line)
        if m:
            if not line.strip().startswith('"""') and not line.strip().startswith("'''"):
                defs[m.group(1)] = lineno
            continue
        m = func_pattern.match(line)
        if m:
            defs[m.group(1)] = lineno
            continue
    return defs


def scan_constants(lines):
    """Return dict of {name: line_number} for all module-level UPPERCASE assignments.
    
    Works by tracking indentation level: returns to 0 after class/function.
    """
    consts = {}
    assign = re.compile(r'^([A-Z][A-Z0-9_]+)\s*=')

    indent_level = 0
    for i, line in enumerate(lines):
        lineno = i + 1
        stripped = line.rstrip('\n\r')

        # Skip blank, comment-only, and inside-class/def lines
        if not stripped or stripped.lstrip().startswith('#'):
            continue

        # Count leading whitespace to detect indent changes
        leading = len(stripped) - len(stripped.lstrip())

        if leading == 0:
            indent_level = 0
            # Module-level assignment
            m = assign.match(stripped)
            if m:
                name = m.group(1)
                # Skip things that are actually class/def names (caught above)
                if name not in consts:  # first occurrence wins
                    consts[name] = lineno
                    continue
        elif leading >= 4:
            # Inside a class/function - not a constant
            pass

    return consts


# ============================================================
# STEP 2: Build complete anchor dict
# ============================================================

defs = scan_definitions(pipeline_lines)
consts = scan_constants(pipeline_lines)

print(f"pipeline.py: {total_lines} lines")
print(f"Found {len(defs)} definitions, {len(consts)} constants")
print()

# Ground truth anchor lookup
al = {}
al.update(defs)
al.update(consts)

def get_line(name):
    if name in al:
        return al[name]
    return None

# Print all found constants for verification
print("=== ALL CONSTANTS ===")
for name, ln in sorted(consts.items(), key=lambda x: x[1]):
    print(f"  L{ln}: {name}")

print()
print("=== ALL DEFINITIONS ===")
for name, ln in sorted(defs.items(), key=lambda x: x[1]):
    print(f"  L{ln}: {name}")

# Build sections data
sections_data = [
    ("Configuration constants", get_line("OLLAMA_HOST")),
    ("Model Registry", get_line("CODER_MODEL")),
    ("PROJECT_ROOT", get_line("PROJECT_ROOT")),
    ("Token Budget Tracker", get_line("TokenBudget")),
    ("Overflow Block Collapse (_block_aware_collapse)", get_line("_block_aware_collapse")),
    ("OffloadStore class", get_line("OffloadStore")),
    ("LRU Doc Cache (_get_doc_cached)", get_line("_get_doc_cached")),
    ("Session Timeline", get_line("SESSION_TIMELINE_PATH")),
    ("log_to_session_timeline()", get_line("log_to_session_timeline")),
    ("Doc Format: Anchor-TOC Builder", get_line("build_anchor_toc")),
    ("Memory Ledger: Table of Contents", get_line("ledger_toc")),
    ("ALL_DOMAINS registry", get_line("ALL_DOMAINS")),
    ("AGENT_ALIAS_MAP", get_line("AGENT_ALIAS_MAP")),
    ("PERSONA_MAP", get_line("PERSONA_MAP")),
    ("REASONING_GATE_DOMAINS", get_line("REASONING_GATE_DOMAINS")),
    ("System Prompts area", get_line("DIRECTOR_SYSTEM")),
    ("CHAT_SYSTEM", get_line("CHAT_SYSTEM")),
    ("INTENT_CLASSIFIER_SYSTEM", get_line("INTENT_CLASSIFIER_SYSTEM")),
    ("SignalType Enum", get_line("SignalType")),
    ("call_ollama_streamed()", get_line("call_ollama_streamed")),
    ("call_ollama()", get_line("call_ollama")),
    ("Signal Parsing (extract_signals)", get_line("extract_signals")),
    ("Memory Fetch: handle_fetch_signal", get_line("handle_fetch_signal")),
    ("read_offloaded_file()", get_line("read_offloaded_file")),
    ("handle_read_offloaded_signal()", get_line("handle_read_offloaded_signal")),
    ("_page_out_context()", get_line("_page_out_context")),
    ("ensure_ledger_header (Ledger Guard)", get_line("ensure_ledger_header")),
    ("_append_to_ledger (Disk-Write Interceptor)", get_line("_append_to_ledger")),
    ("Checkpoint System (save_checkpoint/load_checkpoint)", get_line("save_checkpoint")),
    ("run_mesh_pipeline()", get_line("run_mesh_pipeline")),
    ("generate_failure_report()", get_line("generate_failure_report")),
    ("Mesh Work Queue API (submit_mesh_task)", get_line("submit_mesh_task")),
    ("MeshSignal class", get_line("MeshSignal")),
    ("ConsensusResult class", get_line("ConsensusResult")),
    ("Cross-Agent Edit Detection", get_line("detect_cross_agent_edits")),
    ("Conflict Resolution (resolve_conflict)", get_line("resolve_conflict")),
    ("Progressive Output Support", get_line("register_progress_listener")),
    ("TagSuggester", get_line("TagSuggester")),
    ("Entry Point (run_pipeline)", get_line("run_pipeline")),
]

# === Write ANCHOR INDEX ===
anchor_content = f"""# Pipeline Anchor Index

> **File:** `pipeline.py` ({total_lines} lines)
> **Last Updated:** 2026-05-03
> **Generated by:** `_regenerate_and_align_all.py`

> **Purpose:** Quick-reference line anchor index for agents navigating the pipeline.

---

## Sections (Phase Boundaries)

| Line | Section |
|------|---------|
"""

for name, ln in sections_data:
    if ln:
        anchor_content += f"| L{ln} | {name} |\n"
    else:
        anchor_content += f"| ? | {name} | *(not found)*\n"

# === CLASSES TABLE ===
classes_order = ["TokenBudget", "OffloadStore", "Task", "SignalType", "MeshSignal", "ConsensusResult", "TagSuggester"]
class_purposes = {
    "TokenBudget": "Track/enforce token budget across pipeline execution",
    "OffloadStore": "Disk-based overflow buffer for Virtual Context Management",
    "Task": "Represents a single mesh task (agent + spec + signals)",
    "SignalType": "Enum: VETO, APPROVE, QUERY, DELEGATE, RESULT, etc.",
    "MeshSignal": "Data class for inter-agent signals",
    "ConsensusResult": "Result of conflict resolution check",
    "TagSuggester": "Post-pipeline tag auto-detection: [Stable Core Concept] / [Likely Regression]",
}

anchor_content += """
---

## Classes

| Line | Class | Purpose |
|------|-------|---------|
"""

for name in classes_order:
    ln = get_line(name)
    if ln:
        purpose = class_purposes.get(name, "")
        anchor_content += f"| L{ln} | `{name}` | {purpose} |\n"

# === FUNCTIONS TABLE (alphabetical) ===
anchor_content += """
---

## Functions (Alphabetical)

| Line | Function | Called By |
|------|----------|-----------|
"""

func_names = [n for n in sorted(defs.keys(), key=lambda x: x.lower()) if n[0].islower() and n not in classes_order]
for name in func_names:
    ln = get_line(name)
    if ln:
        anchor_content += f"| L{ln} | `{name}()` | (see code) |\n"

# === CONSTANTS TABLE (alphabetical) ===
anchor_content += """
---

## Key Configuration Constants

| Line | Constant | Value |
|------|----------|-------|
"""

const_names = sorted(consts.keys(), key=lambda x: x.lower())
for name in const_names:
    ln = get_line(name)
    if ln:
        anchor_content += f"| L{ln} | `{name}` | (see code) |\n"

anchor_content += """
---

*Generated by `_regenerate_and_align_all.py` — 2026-05-03*
"""

os.makedirs(os.path.dirname(ANCHOR_PATH), exist_ok=True)
with open(ANCHOR_PATH, "w", encoding="utf-8") as f:
    f.write(anchor_content)
print(f"\n✅ Wrote {ANCHOR_PATH}")

# ============================================================
# STEP 3: Print key lookups for verification
# ============================================================
print()
print("=== KEY LOOKUPS ===")
key_refs = [
    "classify_intent", "search_memory", "call_ollama", "call_ollama_streamed",
    "extract_signals", "handle_fetch_signal", "run_mesh_pipeline",
    "log_to_session_timeline", "build_anchor_toc", "ensure_ledger_header",
    "_append_to_ledger", "save_checkpoint", "load_checkpoint",
    "submit_mesh_task", "get_mesh_task_status", "list_mesh_tasks",
    "cancel_mesh_task", "get_mesh_work_queue", "get_mesh_results",
    "SignalType", "MeshSignal", "ConsensusResult", "detect_cross_agent_edits",
    "resolve_conflict", "generate_failure_report", "TagSuggester",
    "register_progress_listener", "run_pipeline",
    "read_offloaded_file", "_page_out_context",
    "TokenBudget", "OffloadStore", "Task",
    "get_project_state", "curate_project_structure", "parse_file_references",
    "fetch_referenced_files", "handle_file_read", "handle_file_list",
    "resolve_agent_name", "get_agent_system",
    "get_available_domains_text", "get_unavailable_domains_text",
    "extract_gdd_sections", "recursive_librarian",
    "DIRECTOR_SYSTEM", "REVIEW_SYSTEM", "REVIEW_PROMPT",
    "FINAL_APPROVAL_SYSTEM", "SELF_CORRECT_SYSTEM", "ARCHITECT_FIX_SYSTEM",
    "LIBRARIAN_SYSTEM", "DIAGNOSTIC_ORACLE_SYSTEM", "INTENT_ROUTER_SYSTEM",
    "CHAT_SYSTEM", "INTENT_CLASSIFIER_SYSTEM", "SEARCH_MEMORY_SYSTEM",
    "MESH_AGENT_SYSTEM_EXTENSION", "AGENT_FILE_TOOLS_PROMPT",
    "ALL_DOMAINS", "AGENT_ALIAS_MAP", "PERSONA_MAP",
    "REASONING_GATE_DOMAINS", "REASONING_GATE_SYSTEM",
    "SIGNAL_PATTERNS", "DOUBLE_CHECK_PATTERN",
    "GDD_PATH", "GDD_SECTION_MAP", "KEYWORD_TO_SECTION",
    "FILE_REF_PATTERN", "LEDGER_HEADER_PATTERN", "PROGRESS_LISTENERS",
    "OLLAMA_HOST", "CODER_MODEL", "REVIEWER_MODEL", "DIRECTOR_MODEL",
    "OLLAMA_TIMEOUT", "OLLAMA_NUM_CTX", "MAX_TOKENS",
    "is_likely_chat",
]

for ref in key_refs:
    ln = get_line(ref)
    if ln:
        print(f"  {ref}: L{ln}")
    else:
        print(f"  {ref}: NOT FOUND ⚠️")

print("\nDone.")
