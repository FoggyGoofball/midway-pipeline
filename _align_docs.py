#!/usr/bin/env python3
"""
Align docs/pipeline_master_checklist.md and docs/pipeline_agent_todo.md
with the fresh line numbers from docs/pipeline_anchor_index.md.

Strategy:
  1. Load the fresh anchor index as a {name: line_number} lookup
  2. For each doc, find every L\d+ and L\d+-\d+ reference
  3. Map the surrounding context to a name in the anchor lookup
  4. Replace stale line numbers with fresh ones
  5. Also update the "4489 lines" count in the header

This is a one-pass replacement: generate fresh numbers from pipeline.py,
paste them into all docs. No reconciliation needed.
"""

import re
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PIPELINE_PATH = os.path.join(SCRIPT_DIR, "pipeline.py")
ANCHOR_PATH = os.path.join(SCRIPT_DIR, "docs", "pipeline_anchor_index.md")
CHECKLIST_PATH = os.path.join(SCRIPT_DIR, "docs", "pipeline_master_checklist.md")
TODO_PATH = os.path.join(SCRIPT_DIR, "docs", "pipeline_agent_todo.md")

# ============================================================
# STEP 1: Count total lines in pipeline.py
# ============================================================
with open(PIPELINE_PATH, encoding="utf-8") as f:
    pipeline_lines = f.readlines()
total_lines = len(pipeline_lines)

print(f"pipeline.py: {total_lines} lines")

# ============================================================
# STEP 2: Build fresh anchor lookup from pipeline.py scan
# ============================================================
# Reuse the same logic from _regenerate_and_align_all.py

def scan_definitions(lines):
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
    consts = {}
    assign = re.compile(r'^([A-Z][A-Z0-9_]+)\s*=')
    for i, line in enumerate(lines):
        lineno = i + 1
        stripped = line.rstrip('\n\r')
        if not stripped or stripped.lstrip().startswith('#'):
            continue
        leading = len(stripped) - len(stripped.lstrip())
        if leading == 0:
            m = assign.match(stripped)
            if m:
                name = m.group(1)
                if name not in consts:
                    consts[name] = lineno
    return consts

defs = scan_definitions(pipeline_lines)
consts = scan_constants(pipeline_lines)

# Fresh anchor lookup: name → line_number
al = {}
al.update(defs)
al.update(consts)

def get_line(name):
    return al.get(name)

print(f"Anchor lookup has {len(defs)} definitions + {len(consts)} constants = {len(al)} entries")

# ============================================================
# STEP 3: Build function/class ranges (start_line, end_line)
# ============================================================
# Sort all definitions by line number
all_items = [(name, line) for name, line in al.items()]
all_items.sort(key=lambda x: x[1])

# Build ranges: for each item, end is the line before the next item, or total_lines
ranges = {}
for idx, (name, start) in enumerate(all_items):
    if idx + 1 < len(all_items):
        end = all_items[idx + 1][1] - 1
    else:
        end = total_lines
    ranges[name] = (start, end)

# ============================================================
# STEP 4: Build name-to-pattern mapping for context matching
# ============================================================
# Map common textual references to anchor names
name_aliases = {
    # Function aliases (what the docs call them)
    "TokenBudget.add()": "add",
    "TokenBudget._block_aware_collapse()": "_block_aware_collapse",
    "TokenBudget.estimate_tokens()": "estimate_tokens",
    "TokenBudget.hard_limit": "TokenBudget",
    "_MAX_OUTPUT_CHARS": None,  # internal constant, may not exist  
    "_DOC_CACHE_TTL": None,
    "_DOC_CACHE_MAX": None,
    "search_memory()": "search_memory",
    "log_to_session_timeline()": "log_to_session_timeline",
    "handle_fetch_signal()": "handle_fetch_signal",
    "ensure_ledger_header()": "ensure_ledger_header",
    "_append_to_ledger()": "_append_to_ledger",
    "save_checkpoint()": "save_checkpoint",
    "load_checkpoint()": "load_checkpoint",
    "classify_intent()": "classify_intent",
    "build_anchor_toc()": "build_anchor_toc",
    "build_director_prompt()": "build_director_prompt",
    "extract_gdd_sections()": "extract_gdd_sections",
    "recursive_librarian()": "recursive_librarian",
    "parse_file_references()": "parse_file_references",
    "fetch_referenced_files()": "fetch_referenced_files",
    "get_project_state()": "get_project_state",
    "curate_project_structure()": "curate_project_structure",
    "get_available_domains_text()": "get_available_domains_text",
    "get_unavailable_domains_text()": "get_unavailable_domains_text",
    "resolve_agent_name()": "resolve_agent_name",
    "get_agent_system()": "get_agent_system",
    "handle_file_read()": "handle_file_read",
    "handle_file_list()": "handle_file_list",
    "submit_mesh_task()": "submit_mesh_task",
    "get_mesh_task_status()": "get_mesh_task_status",
    "list_mesh_tasks()": "list_mesh_tasks",
    "cancel_mesh_task()": "cancel_mesh_task",
    "get_mesh_work_queue()": "get_mesh_work_queue",
    "get_mesh_results()": "get_mesh_results",
    "detect_cross_agent_edits()": "detect_cross_agent_edits",
    "resolve_conflict()": "resolve_conflict",
    "generate_failure_report()": "generate_failure_report",
    "read_offloaded_file()": "read_offloaded_file",
    "_page_out_context()": "_page_out_context",
    "_build_offload_placeholder()": "_build_offload_placeholder",
    "_generate_module_name()": "_generate_module_name",
    "_normalize_fix_fingerprint()": "_normalize_fix_fingerprint",
    "register_progress_listener()": "register_progress_listener",
    "call_ollama()": "call_ollama",
    "call_ollama_streamed()": "call_ollama_streamed",
    "extract_signals()": "extract_signals",
    "run_mesh_pipeline()": "run_mesh_pipeline",
    "run_pipeline()": "run_pipeline",
    "class SignalType(Enum)": "SignalType",
    "TagSuggester": "TagSuggester",
    "classify_intent(user_prompt)": "classify_intent",
    "OffloadStore": "OffloadStore",
    "Task": "Task",
    "MeshSignal": "MeshSignal",
    "ConsensusResult": "ConsensusResult",
    
    # Constant aliases
    "INTENT_CLASSIFIER_MODEL (llama-3.2-1b micro-model)": "INTENT_CLASSIFIER_MODEL",
    "REASONING_MODEL": "REASONING_MODEL",
    "DIRECTOR_SYSTEM": "DIRECTOR_SYSTEM",
    "REVIEW_SYSTEM": "REVIEW_SYSTEM",
    "REVIEW_PROMPT": "REVIEW_PROMPT",
    "FINAL_APPROVAL_SYSTEM": "FINAL_APPROVAL_SYSTEM",
    "SELF_CORRECT_SYSTEM": "SELF_CORRECT_SYSTEM",
    "ARCHITECT_FIX_SYSTEM": "ARCHITECT_FIX_SYSTEM",
    "LIBRARIAN_SYSTEM": "LIBRARIAN_SYSTEM",
    "DIAGNOSTIC_ORACLE_SYSTEM": "DIAGNOSTIC_ORACLE_SYSTEM",
    "INTENT_ROUTER_SYSTEM": "INTENT_ROUTER_SYSTEM",
    "INTENT_CLASSIFIER_SYSTEM": None,  # Doesn't exist as standalone constant
    "SEARCH_MEMORY_SYSTEM": "SEARCH_MEMORY_SYSTEM",
    "MESH_AGENT_SYSTEM_EXTENSION": "MESH_AGENT_SYSTEM_EXTENSION",
    "AGENT_FILE_TOOLS_PROMPT": "AGENT_FILE_TOOLS_PROMPT",
    "REASONING_GATE_DOMAINS": "REASONING_GATE_DOMAINS",
    "REASONING_GATE_SYSTEM": "REASONING_GATE_SYSTEM",
    "ALL_DOMAINS": "ALL_DOMAINS",
    "AGENT_ALIAS_MAP": "AGENT_ALIAS_MAP",
    "PERSONA_MAP": "PERSONA_MAP",
    "CHAT_SYSTEM": "CHAT_SYSTEM",
    "CHAT_PATTERNS": "CHAT_PATTERNS",
    "CHAT_MODEL": "CHAT_MODEL",
    "PROJECT_ROOT": "PROJECT_ROOT",
    "SCOPE_FILE_LIMIT": "SCOPE_FILE_LIMIT",
    "SCOPE_LINE_LIMIT": "SCOPE_LINE_LIMIT",
    "OLLAMA_HOST": "OLLAMA_HOST",
    "CODER_MODEL": "CODER_MODEL",
    "REVIEWER_MODEL": "REVIEWER_MODEL",
    "DIRECTOR_MODEL": "DIRECTOR_MODEL",
    "EXECUTION_MODEL": "EXECUTION_MODEL",
    "FALLBACK_REVIEWER_MODEL": "FALLBACK_REVIEWER_MODEL",
    "LIBRARIAN_MODEL": "LIBRARIAN_MODEL",
    "SYNTAX_GATE_MODEL": "SYNTAX_GATE_MODEL",
    "MAX_ITERATIONS": "MAX_ITERATIONS",
    "MAX_CONSENSUS_ITERATIONS": "MAX_CONSENSUS_ITERATIONS",
    "MAX_SUBTASKS_PER_AGENT": "MAX_SUBTASKS_PER_AGENT",
    "REVIEW_MAX_ITERATIONS": "REVIEW_MAX_ITERATIONS",
    "OLLAMA_TIMEOUT": "OLLAMA_TIMEOUT",
    "OLLAMA_NUM_CTX": "OLLAMA_NUM_CTX",
    "MAX_TOKENS": "MAX_TOKENS",
    "GDD_PATH": "GDD_PATH",
    "GDD_SECTION_MAP": "GDD_SECTION_MAP",
    "KEYWORD_TO_SECTION": "KEYWORD_TO_SECTION",
    "SIGNAL_PATTERNS": "SIGNAL_PATTERNS",
    "DOUBLE_CHECK_PATTERN": "DOUBLE_CHECK_PATTERN",
    "FILE_REF_PATTERN": "FILE_REF_PATTERN",
    "LEDGER_HEADER_PATTERN": "LEDGER_HEADER_PATTERN",
    "PROGRESS_LISTENERS": "PROGRESS_LISTENERS",
    "BOILERPLATE_TITLES": "BOILERPLATE_TITLES",
    "Session Timeline": "log_to_session_timeline",
    "session_timeline.md": None,
    "qa_ledger.md": None,
    "active_run_ledger.md": None,
    "project_blueprint.md": None,
    "SESSION_TIMELINE_PATH": "SESSION_TIMELINE_PATH",
    "SESSION_TIMELINE_LOCK": None,  # Removed in generator refactor
    "LEDGER_WRITE_LOCK": None,  # Removed
    "GLOBAL_MESH_LOCK": None,  # Removed
    "CHECKPOINT_DIR": "CHECKPOINT_DIR",
    "MEMORY_DIR": "MEMORY_DIR",
    "LEDGER_MEMORY_RULE": "LEDGER_MEMORY_RULE",
    "CHECKPOINT_TTL": None,  # In pipeline_stream_server.py
    "TokenBudget": "TokenBudget",
    "token_budget": "TokenBudget",
    "offload_store": "OffloadStore",
}

# Also build reverse lookup: function/class name → anchor name
reverse_alias = {}
for alias, canon in name_aliases.items():
    if canon:
        reverse_alias[canon] = alias

# ============================================================
# STEP 5: Known reference mappings for complex references
# ============================================================
# These are explicit (context_string, expected_name) pairs found in the docs
manual_refs = [
    # Checklist Phase 0.03
    ("L57-L57", "INTENT_CLASSIFIER_MODEL"),
    ("L61-1175", "call_ollama"),
    ("L1376-2480", "classify_intent"),
    ("L672-2478", "log_to_session_timeline"),
    
    # Phase 0.05
    ("L60-L60", "REASONING_MODEL"),
    ("L2426-2097", "save_checkpoint"),  # Checkpoint system ref
    ("L2426-2121", "save_checkpoint"),
    ("L2087-2091, L2113-2118", "qa_ledger.md"),  # No anchor for file
    ("L2426-2094", "save_checkpoint"),
    
    # Phase 0.2
    ("L2126-2136", "extract_signals"),
    ("L2138-2141", "extract_signals"),
    ("L61-L60", "REASONING_MODEL"),
    
    # Phase 0.5
    ("L2467-2234", "run_mesh_pipeline"),
    ("L61-L2246", "REASONING_MODEL"),
    ("L2467-2246", "run_mesh_pipeline"),
    
    # Phase 1
    ("L1451-1194", "extract_gdd_sections"),
    ("L1672-1221", "recursive_librarian"),
    
    # Phase 2
    ("L63-1067", "get_project_state"),
    ("L61-L1161", "curate_project_structure"),
    ("L1708-L1788", "fetch_referenced_files"),
    
    # Phase 3
    ("L1561-1110", "build_director_prompt"),
    ("L2467", "run_mesh_pipeline"),
    ("L2467-2304", "run_mesh_pipeline"),
    ("L2467-2316", "run_mesh_pipeline"),
    
    # Phase 4
    ("L2467-2568", "run_mesh_pipeline"),
    ("L2467-1950", "run_mesh_pipeline"),
    ("L2332-1947", "ensure_ledger_header"),
    ("L2467-2385", "run_mesh_pipeline"),
    ("L2080-2426, L2080-2496", "run_mesh_pipeline"),
    ("L2341, L2402-2408", "run_mesh_pipeline"),
    ("L2137-2546", "handle_fetch_signal"),
    ("L2467-2444", "run_mesh_pipeline"),
    ("L4140-2462", "resolve_conflict"),
    ("L2467-2479", "run_mesh_pipeline"),
    ("L2481-2483", "run_mesh_pipeline"),
    ("L1841-L1815", "AGENT_FILE_TOOLS_PROMPT"),
    ("L2558-2568", "run_mesh_pipeline"),
    ("L2467-L64", "run_mesh_pipeline"),
    
    # Phase 5
    ("L2570-2623", "run_mesh_pipeline"),
    ("L4140-3342", "resolve_conflict"),
    ("L4072-3294", "detect_cross_agent_edits"),
    ("L4140-2623", "resolve_conflict"),
    
    # Phase 6
    ("L2633-2663", "run_mesh_pipeline"),
    ("L1291-L1291", "ARCHITECT_FIX_SYSTEM"),
    ("L2686-2697", "run_mesh_pipeline"),
    ("L675, L675-718, L2051-L2051", "_normalize_fix_fingerprint"),
    ("L2766-2771", "run_mesh_pipeline"),
    ("L2137-2729", "handle_fetch_signal"),
    ("L2116-1603", "get_verdict"),
    ("L64-2777", "MAX_ITERATIONS"),
    ("L2718, L2733", "run_mesh_pipeline"),
    
    # Phase 7
    ("L2790-2800", "run_mesh_pipeline"),
    ("L2116-2807", "get_verdict"),
    
    # Phase 8
    ("L2810-2847", "run_mesh_pipeline"),
    ("L2426-2847", "save_checkpoint"),
    ("L3831-2919", "run_mesh_pipeline"),
    ("L2849-2877", "run_mesh_pipeline"),
    ("L2467-2945", "run_mesh_pipeline"),
    ("L2957-2962", "pipeline_snapshot.py"),  # External file
    ("L672-2979", "log_to_session_timeline"),
    
    # Session Timeline
    ("L71-465", "log_to_session_timeline"),
    ("L455-464", "log_to_session_timeline"),
    ("L438-440", "log_to_session_timeline"),
    ("L442-450", "log_to_session_timeline"),
    ("L2044-2049", "run_mesh_pipeline"),
    ("L2964-2979", "run_mesh_pipeline"),
    
    # Librarian
    ("L1363-900", "search_memory"),
    ("L61-2022", "CHAT_SYSTEM"),
    ("L2023-2037", "run_mesh_pipeline"),
    ("L2040-2055", "run_mesh_pipeline"),
    ("L672-2049", "log_to_session_timeline"),
    
    # Resurrection
    ("L2061-2062", "run_mesh_pipeline"),
    ("L2185-2201", "run_mesh_pipeline"),
    ("L2467-2201", "run_mesh_pipeline"),
    ("L2206-2214", "run_mesh_pipeline"),
    ("L2307-2316", "run_mesh_pipeline"),
    ("L2467-2316", "run_mesh_pipeline"),
    
    # System Prompts table
    ("L2467-1067", "DIRECTOR_SYSTEM"),
    ("L1255-1074", "REVIEW_SYSTEM"),
    ("L1262-1091", "REVIEW_PROMPT"),
    ("L1279-1097", "FINAL_APPROVAL_SYSTEM"),
    ("L64-1103", "SELF_CORRECT_SYSTEM"),
    ("L1291-1111", "ARCHITECT_FIX_SYSTEM"),
    ("L1299-1118", "LIBRARIAN_SYSTEM"),
    ("L1306-1127", "DIAGNOSTIC_ORACLE_SYSTEM"),
    ("L1315-1134", "INTENT_ROUTER_SYSTEM"),
    ("L1344-1143", "INTENT_CLASSIFIER_SYSTEM"),
    ("L1363-1151", "SEARCH_MEMORY_SYSTEM"),
    ("L1397-1208", "MESH_AGENT_SYSTEM_EXTENSION"),
    ("L1800-1590", "AGENT_FILE_TOOLS_PROMPT"),
    
    # Helper Functions
    ("L1545-1338", "get_available_domains_text"),
    ("L1553-1346", "get_unavailable_domains_text"),
    ("L778-719", "build_anchor_toc"),
    ("L2488-2234", "resolve_agent_name"),
    ("L2518-2249", "get_agent_system"),
    ("L2332-2092", "ensure_ledger_header"),
    ("L2361-2108", "_generate_module_name"),
    ("L4220-3862", "register_progress_listener"),
    ("L4226-3870", "_emit_progress"),
    ("L1708-1508", "parse_file_references"),
    ("L1731-1563", "fetch_referenced_files"),
    
    # Mesh Work Queue
    ("L2467-3103", "submit_mesh_task"),
    ("L2467-3108", "get_mesh_task_status"),
    ("L2467-3113", "list_mesh_tasks"),
    ("L2467-3131", "cancel_mesh_task"),
    ("L3977-3139", "get_mesh_work_queue"),
    ("L3985-3148", "get_mesh_results"),
    
    # Signal/Routing
    ("L3997-3165", "SignalType"),
    ("L4010-3186", "MeshSignal"),
    ("L4010-3203", "MeshSignal"),
    ("L4051-3227", "ConsensusResult"),
    
    # Cross-Agent
    ("L4072-3294", "detect_cross_agent_edits"),
    ("L4051-3342", "resolve_conflict"),
    ("L2467-3369", "run_mesh_pipeline"),
    
    # CLI Entry
    ("L3402-3413", "run_pipeline"),
    ("L3408-3409", "run_pipeline"),
    ("L3410-3411", "run_pipeline"),
    ("L4366-3399", "run_pipeline"),
    
    # Model Registry
    ("L61-51", None),  # These are old-style, will be handled by proximity
]


def resolve_line_for_ref(ref_text, line_num):
    """Given a L\d+ pattern, try to resolve its context to determine what it refers to."""
    return None


def replace_line_refs(text, doc_name):
    """Replace L\d+ and L\d+-\d+ patterns with fresh line numbers using the anchor lookup."""
    
    # Pattern for L\d+-\d+ and L\d+
    range_pattern = re.compile(r'L(\d+)-(\d+)')
    single_pattern = re.compile(r'(?<!L)\bL(\d+)\b(?!-)')
    
    lines = text.split('\n')
    new_lines = []
    replacements_made = 0
    
    for line_num, line in enumerate(lines):
        new_line = line
        
        # Replace L\d+-\d+ ranges
        def replace_range(m):
            nonlocal replacements_made
            start = int(m.group(1))
            end = int(m.group(2))
            old_ref = f"L{start}-{end}"
            
            # Look up in manual_refs
            for ref, name in manual_refs:
                if ref == old_ref:
                    if name and name in al:
                        s = al[name]
                        r = ranges.get(name, (s, s))
                        new_ref = f"L{s}-{r[1]}"
                        replacements_made += 1
                        if replacements_made <= 5 or replacements_made % 10 == 0:
                            print(f"  {doc_name}:{line_num+1}: {old_ref} → {new_ref} ({name})")
                        return new_ref
                    elif name is None:
                        return old_ref  # Keep as-is (no mapping)
            return old_ref
        
        new_line = range_pattern.sub(replace_range, new_line)
        
        # Replace standalone L\d+
        def replace_single(m):
            nonlocal replacements_made
            ln = int(m.group(1))
            old_ref = f"L{ln}"
            
            # Check if this is part of a range or code reference
            # Look up in manual_refs
            for ref, name in manual_refs:
                if ref == old_ref:
                    if name and name in al:
                        s = al[name]
                        new_ref = f"L{s}"
                        replacements_made += 1
                        if replacements_made <= 5 or replacements_made % 10 == 0:
                            print(f"  {doc_name}:{line_num+1}: {old_ref} → {new_ref} ({name})")
                        return new_ref
                    elif name is None:
                        return old_ref
            return old_ref
        
        new_line = single_pattern.sub(replace_single, new_line)
        
        # Also update the total line count
        new_line = new_line.replace("4,440 lines", f"{total_lines:,} lines")
        new_line = new_line.replace("~178 KB", f"{total_lines * 0.04:.0f} KB")  # rough estimate
        
        new_lines.append(new_line)
    
    return '\n'.join(new_lines)


# ============================================================
# STEP 6: Process master checklist
# ============================================================
print("\n=== Processing docs/pipeline_master_checklist.md ===")
with open(CHECKLIST_PATH, encoding="utf-8") as f:
    checklist_text = f.read()

old_checklist = checklist_text
checklist_text = replace_line_refs(checklist_text, "checklist")

# Additional doc-specific replacements
# Fix common messy references
checklist_text = checklist_text.replace("L92-333", f"L{ranges['_block_aware_collapse'][0]}-{ranges['_block_aware_collapse'][1]}")
checklist_text = checklist_text.replace("L92-421", f"L{ranges['add'][0]}-{ranges['add'][1]}")
checklist_text = checklist_text.replace("L847-1013", f"L{ranges['ALL_DOMAINS'][0]}-{ranges['ALL_DOMAINS'][1]}")
checklist_text = checklist_text.replace("L637-657", f"L{ranges['_get_doc_cached'][0]}-{ranges['_get_doc_cached'][1]}")

# Phase 4 specific: L2240 references for offload functions
checklist_text = checklist_text.replace("L2240", f"L{al.get('read_offloaded_file', '?')}")
checklist_text = checklist_text.replace("L2260", f"L{al.get('read_offloaded_file', '?')}")
checklist_text = checklist_text.replace("L2291", f"L{al.get('_page_out_context', '?')}")

# Invariants
for inv_old, inv_name in [
    ("L61", "call_ollama"),
    ("L72-L71", "MAX_TOKENS"),
    ("L2051 (comment)", "_normalize_fix_fingerprint"),
    ("L478-623", "OffloadStore"),
    ("L2291-2099", "_page_out_context"),
    ("L2240-387", "_build_offload_placeholder"),
    ("L92-116", "TokenBudget"),
    ("L637-657", "_get_doc_cached"),
    ("L675-718, L2051-L2051", "_normalize_fix_fingerprint"),
    ("L92", "TokenBudget"),
    ("L61-52", "CODER_MODEL"),
    ("L478", "OffloadStore"),
    ("L675, L675-718", "_normalize_fix_fingerprint"),
    ("L92:", "TokenBudget"),
    ("L2137", "handle_fetch_signal"),
    ("L2956-2978", "run_mesh_pipeline"),
    ("L478-L478", "OffloadStore"),
    ("L2051", "_normalize_fix_fingerprint"),
]:
    if inv_name in al:
        checklist_text = checklist_text.replace(inv_old, f"L{al[inv_name]}")

# Fix the `_offload_single_block` reference
if "_offload_single_block" in al:
    checklist_text = checklist_text.replace("_offload_single_block()", f"_offload_single_block() (L{al['_offload_single_block']})")

if checklist_text != old_checklist:
    with open(CHECKLIST_PATH, "w", encoding="utf-8") as f:
        f.write(checklist_text)
    print(f"\n✅ Updated {CHECKLIST_PATH}")
else:
    print(f"\n  No changes made to {CHECKLIST_PATH}")

# ============================================================
# STEP 7: Process agent TODO
# ============================================================
print("\n=== Processing docs/pipeline_agent_todo.md ===")
with open(TODO_PATH, encoding="utf-8") as f:
    todo_text = f.read()

old_todo = todo_text
todo_text = replace_line_refs(todo_text, "todo")

# Fix specific TODO references
todo_refs = [
    ("L91-102", "estimate_tokens"),
    ("L478-623", "OffloadStore"),
    ("L370", "_build_offload_placeholder"),
    ("L2240", "read_offloaded_file"),
    ("L2291", "_page_out_context"),
    ("L2291", "_page_out_context"),
    ("L2260", "read_offloaded_file"),
    ("L675-718", "_normalize_fix_fingerprint"),
    ("L2051", "_normalize_fix_fingerprint"),
    ("L2765", "detect_cross_agent_edits"),  # near where the set() logic is
    ("L674-679", "SESSION_TIMELINE_PATH"),
    ("L703-766", "_normalize_fix_fingerprint"),
    ("L2215-2225", "run_mesh_pipeline"),
    ("L117-148", None),  # pipeline_stream_server.py
    ("L286-292", None),  # pipeline_stream_server.py
    ("L71", "SESSION_TIMELINE_PATH"),
    ("L2383", "ensure_ledger_header"),
    ("L71 :", "SESSION_TIMELINE_PATH"),
    ("L2383 :", "ensure_ledger_header"),
    ("L478-623", "OffloadStore"),
    ("L370", "_build_offload_placeholder"),
    ("L2240)", "read_offloaded_file"),
    ("L2291)", "_page_out_context"),
    ("L2291", "_page_out_context"),
    ("L478-L478", "OffloadStore"),
]

for old_ref, name in todo_refs:
    if name and name in al:
        todo_text = todo_text.replace(old_ref, f"L{al[name]}")
    elif name is None:
        pass  # keep as-is

if todo_text != old_todo:
    with open(TODO_PATH, "w", encoding="utf-8") as f:
        f.write(todo_text)
    print(f"\n✅ Updated {TODO_PATH}")
else:
    print(f"\n  No changes made to {TODO_PATH}")

print("\n=== Alignment complete ===")
print(f"  Total anchor entries: {len(al)}")
print(f"  pipeline.py lines: {total_lines}")
