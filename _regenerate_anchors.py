#!/usr/bin/env python3
"""Extract ground-truth line anchors from pipeline.py, then regenerate all reference docs."""
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
DOCS_DIR = PROJECT_ROOT / "docs"

# ── Read pipeline.py as UTF-8 ──────────────────────────────────────────────
src_path = PROJECT_ROOT / "pipeline.py"
raw = src_path.read_bytes()
# Try to decode, handling encoding errors
try:
    text = raw.decode("utf-8")
except UnicodeDecodeError:
    text = raw.decode("utf-8", errors="replace")

lines = text.splitlines()
print(f"pipeline.py: {len(lines)} lines", file=sys.stderr)

# ── Helper: find first line matching pattern starting from a base ──────────
def find_line(pattern, start=0):
    """Return 1-indexed line number of first match at or after start."""
    for i in range(start, len(lines)):
        if re.search(pattern, lines[i]):
            return i + 1
    return None

def find_lines(pattern, start=0):
    """Return list of (line_num, line_text) for all matches."""
    results = []
    for i in range(start, len(lines)):
        if re.search(pattern, lines[i]):
            results.append((i + 1, lines[i]))
    return results

# ── 1. Extract ALL function/class definitions (def, class) ─────────────────
defs = []
for i, line in enumerate(lines):
    m = re.match(r'^(async\s+)?(def |class )(\w+)', line)
    if m and not line.strip().startswith('#'):
        defs.append((i + 1, m.group(3), 'async def' if m.group(1) else m.group(2).strip()))

print(f"\nAll definitions ({len(defs)}):", file=sys.stderr)
for ln, name, kind in defs:
    print(f"  L{ln}: {kind} {name}", file=sys.stderr)

# ── 2. Section comments / phase markers ────────────────────────────────────
sections = []
for i, line in enumerate(lines):
    stripped = line.strip()
    # Match ## comments that look like section headings
    if stripped.startswith('## ') or stripped.startswith('# -- ') or stripped.startswith('# === '):
        sections.append((i + 1, stripped))
    elif stripped.startswith('# Phase') or stripped.startswith('### Phase'):
        sections.append((i + 1, stripped))
    # Also find big comment blocks that serve as section delimiters
    m = re.match(r'^# [-=]{5,}\s*(.*?)\s*[-=]{5,}$', stripped)
    if m:
        sections.append((i + 1, f"# {'='*5} {m.group(1)}"))

# ── 3. Named constants (UPPER_CASE = value pattern at module level) ────────
constants = []
for i, line in enumerate(lines):
    stripped = line.strip()
    # Skip indented, comments, strings
    if line.startswith('    ') or line.startswith('\t'):
        continue
    if stripped.startswith('#') or stripped.startswith('"') or stripped.startswith("'"):
        continue
    m = re.match(r'^([A-Z][A-Z_0-9]+)\s*[=:]', stripped)
    if m:
        constants.append((i + 1, m.group(1)))

# ── 4. ALL_DOMAINS entries if it's a dict ──────────────────────────────────
all_domains_start = find_line(r'ALL_DOMAINS\s*=\s*\{')
if all_domains_start:
    print(f"\nALL_DOMAINS starts at L{all_domains_start}", file=sys.stderr)

# ── 5. PERSONA_MAP ─────────────────────────────────────────────────────────
persona_start = find_line(r'PERSONA_MAP\s*=\s*\{')
if persona_start:
    print(f"PERSONA_MAP starts at L{persona_start}", file=sys.stderr)

# ── 6. Signal types ────────────────────────────────────────────────────────
signal_start = find_line(r'class SignalType')
if signal_start:
    print(f"SignalType at L{signal_start}", file=sys.stderr)

# ── 7. System prompts ──────────────────────────────────────────────────────
system_prompts = []
for pattern in ['DIRECTOR_SYSTEM', 'REVIEW_SYSTEM', 'REVIEW_PROMPT', 'FINAL_APPROVAL_SYSTEM',
                'SELF_CORRECT_SYSTEM', 'ARCHITECT_FIX_SYSTEM', 'LIBRARIAN_SYSTEM',
                'DIAGNOSTIC_ORACLE_SYSTEM', 'INTENT_ROUTER_SYSTEM', 'INTENT_CLASSIFIER_SYSTEM',
                'SEARCH_MEMORY_SYSTEM', 'MESH_AGENT_SYSTEM_EXTENSION', 'AGENT_FILE_TOOLS_PROMPT',
                'CHAT_SYSTEM', 'REASONING_GATE_SYSTEM', 'AGENT_ALIAS_MAP']:
    ln = find_line(r"^{}\s*[=(]".format(re.escape(pattern)))
    if ln:
        system_prompts.append((ln, pattern))
        print(f"  {pattern} at L{ln}", file=sys.stderr)

# ── 8. REASONING_GATE_DOMAINS ──────────────────────────────────────────────
reasoning_gate = find_line(r'REASONING_GATE_DOMAINS\s*=')
if reasoning_gate:
    print(f"REASONING_GATE_DOMAINS at L{reasoning_gate}", file=sys.stderr)

# ── 9. AGENT_ALIAS_MAP ────────────────────────────────────────────────────
alias_map = find_line(r'AGENT_ALIAS_MAP\s*=')
if alias_map:
    print(f"AGENT_ALIAS_MAP at L{alias_map}", file=sys.stderr)

# ── 10. Checkpoint-related ─────────────────────────────────────────────────
checkpoint_save = find_line(r'def save_checkpoint')
checkpoint_load = find_line(r'def load_checkpoint')
checkpoint_list = find_line(r'def list_checkpoints')
if checkpoint_save: print(f"save_checkpoint at L{checkpoint_save}", file=sys.stderr)

# ── 11. Mesh pipeline ──────────────────────────────────────────────────────
run_mesh = find_line(r'def run_mesh_pipeline')
run_entry = find_line(r'def run_pipeline\(')
if run_mesh: print(f"run_mesh_pipeline at L{run_mesh}", file=sys.stderr)
if run_entry: print(f"run_pipeline (entry) at L{run_entry}", file=sys.stderr)

# ── 12. Key phases in run_mesh_pipeline ────────────────────────────────────
# Find phase comments
phase_lines = find_lines(r'# Phase\s+\d')
print(f"\nPhase markers ({len(phase_lines)}):", file=sys.stderr)
for ln, txt in phase_lines:
    print(f"  L{ln}: {txt.strip()}", file=sys.stderr)

# ── 13. OffloadStore and related ───────────────────────────────────────────
offload_store = find_line(r'class OffloadStore')
read_offloaded = find_line(r'def read_offloaded_file')
page_out = find_line(r'def _page_out_context')
handle_read_offload = find_line(r'def handle_read_offloaded_signal')
if offload_store: print(f"OffloadStore at L{offload_store}", file=sys.stderr)

# ── 14. TokenBudget class ──────────────────────────────────────────────────
token_budget = find_line(r'class TokenBudget')
if token_budget: print(f"TokenBudget at L{token_budget}", file=sys.stderr)

# ── 15. LRU doc cache ──────────────────────────────────────────────────────
doc_cache = find_line(r'_DOC_CACHE_TTL')
if doc_cache: print(f"_DOC_CACHE_TTL at L{doc_cache}", file=sys.stderr)

# ── 16. Session timeline ──────────────────────────────────────────────────
session_timeline = find_line(r'SESSION_TIMELINE_PATH')
log_timeline = find_line(r'def log_to_session_timeline')
if session_timeline: print(f"SESSION_TIMELINE_PATH at L{session_timeline}", file=sys.stderr)

# ── 17. TagSuggester ───────────────────────────────────────────────────────
tag_suggester = find_line(r'class TagSuggester')
if tag_suggester: print(f"TagSuggester at L{tag_suggester}", file=sys.stderr)

# ── 18. Consensus / conflict resolution ────────────────────────────────────
detect_cross = find_line(r'def detect_cross_agent_edits')
resolve_conflict_fn = find_line(r'def resolve_conflict\b')
consensus_result = find_line(r'class ConsensusResult')
mesh_signal = find_line(r'class MeshSignal')
if consensus_result: print(f"ConsensusResult at L{consensus_result}", file=sys.stderr)

# ── 19. Mesh work queue API ────────────────────────────────────────────────
submit_task = find_line(r'def submit_mesh_task')
list_mesh_tasks = find_line(r'def list_mesh_tasks\(\)')
if submit_task: print(f"submit_mesh_task at L{submit_task}", file=sys.stderr)

# ── 20. Failure report ─────────────────────────────────────────────────────
fail_report = find_line(r'def generate_failure_report\b')
fail_report_rest = find_line(r'def _generate_failure_report_rest')
if fail_report: print(f"generate_failure_report at L{fail_report}", file=sys.stderr)

# ── 21. Progressive output ─────────────────────────────────────────────────
progress_listener = find_line(r'def register_progress_listener')
emit_progress = find_line(r'def _emit_progress')
if progress_listener: print(f"register_progress_listener at L{progress_listener}", file=sys.stderr)

# ── 22. Other helper functions ─────────────────────────────────────────────
helpers = [
    'get_available_domains_text', 'get_unavailable_domains_text', 'build_anchor_toc',
    'resolve_agent_name', 'get_agent_system', 'ensure_ledger_header',
    '_generate_module_name', 'parse_file_references', 'fetch_referenced_files',
    'handle_file_read', 'handle_file_list', 'find_relevant_files', 'format_file_context',
    'call_ollama', 'call_ollama_streamed', 'extract_signals', 'extract_double_check',
    'handle_fetch_signal', 'get_verdict', 'search_memory', 'ledger_toc',
    'classify_intent', 'curate_project_structure', 'extract_gdd_sections',
    'recursive_librarian', 'get_project_state', 'build_director_prompt',
    'get_referenced_files_cache', 'set_referenced_files_cache',
    'get_offload_store', '_offload_single_block', '_build_offload_placeholder',
    '_timed_lock', '_append_to_ledger', '_normalize_fix_fingerprint',
    'get_mesh_work_queue', 'get_mesh_results', 'get_mesh_task_status',
    'cancel_mesh_task', 'parse_signal', 'register_progress_listener',
    '_emit_progress',
]

helper_lines = []
for h in helpers:
    ln = find_line(r'def {}\('.format(re.escape(h)))
    if ln:
        helper_lines.append((ln, h))

# ── 23. SIGNAL_PATTERNS ────────────────────────────────────────────────────
signal_patterns = find_line(r'SIGNAL_PATTERNS\s*=')
if signal_patterns: print(f"SIGNAL_PATTERNS at L{signal_patterns}", file=sys.stderr)

# ── 24. Entry point ────────────────────────────────────────────────────────
entry_main = find_line(r'if __name__\s*==\s*["\']__main__["\']')
if entry_main: print(f"__main__ at L{entry_main}", file=sys.stderr)

# ── 25. Model constants ────────────────────────────────────────────────────
model_constants_patterns = [
    'CODER_MODEL', 'REVIEWER_MODEL', 'FALLBACK_REVIEWER_MODEL', 'DIRECTOR_MODEL',
    'EXECUTION_MODEL', 'REASONING_MODEL', 'LIBRARIAN_MODEL', 'SYNTAX_GATE_MODEL',
    'INTENT_CLASSIFIER_MODEL', 'CHAT_MODEL', 'OLLAMA_HOST', 'OLLAMA_NUM_CTX',
    'MAX_TOKENS', 'OLLAMA_TIMEOUT', 'SCOPE_FILE_LIMIT', 'SCOPE_LINE_LIMIT',
    'MAX_ITERATIONS', 'MAX_CONSENSUS_ITERATIONS', 'MAX_SUBTASKS_PER_AGENT',
    'REVIEW_MAX_ITERATIONS', 'PROJECT_ROOT',
]
model_constants = []
for c in model_constants_patterns:
    for ln, name in constants:
        if name == c:
            model_constants.append((ln, c))
            break

print("\n\n=== GROUND TRUTH SUMMARY ===", file=sys.stderr)
print(f"All definitions: {len(defs)}", file=sys.stderr)
print(f"Sections/comments: {len(sections)}", file=sys.stderr)
print(f"Constants: {len(constants)}", file=sys.stderr)
print(f"System prompts: {len(system_prompts)}", file=sys.stderr)

# ===========================================================================
# GENERATE NEW ANCHOR INDEX
# ===========================================================================
output = []
output.append("# Pipeline Anchor Index")
output.append("")
output.append(f"> **File:** `pipeline.py` ({len(lines)} lines)")
output.append(f"> **Last Updated:** {__import__('datetime').datetime.now().strftime('%Y-%m-%d')}")
output.append(f"> **Generated by:** `_regenerate_anchors.py`")
output.append("")
output.append("> **Purpose:** Quick-reference line anchor index for agents navigating the pipeline.")
output.append("")
output.append("---")
output.append("")
output.append("## Sections (Phase Boundaries)")
output.append("")
output.append("| Line | Section |")
output.append("|------|---------|")

# Collect key section references
key_sections = [
    ("Configuration constants", find_line(r'^OLLAMA_HOST\s*=')),
    ("Model Registry", find_line(r'^CODER_MODEL\s*=')),
    ("PROJECT_ROOT", find_line(r'^PROJECT_ROOT\s*=')),
    ("Token Budget Tracker", token_budget),
    ("Overflow Block Collapse (_block_aware_collapse)", find_line(r'def _block_aware_collapse')),
    ("OffloadStore class", offload_store),
    ("LRU Doc Cache", doc_cache),
    ("Session Timeline", session_timeline),
    ("log_to_session_timeline()", log_timeline),
    ("Doc Format: Anchor-TOC Builder", find_line(r'def build_anchor_toc')),
    ("Memory Ledger: Table of Contents (ledger_toc)", find_line(r'def ledger_toc')),
    ("ALL_DOMAINS registry", all_domains_start),
    ("PERSONA_MAP", persona_start),
    ("System Prompts area", system_prompts[0][0] if system_prompts else None),
    ("AGENT_ALIAS_MAP", alias_map),
    ("REASONING_GATE_DOMAINS", reasoning_gate),
    ("CHAT_SYSTEM", find_line(r'^CHAT_SYSTEM\s*=')),
    ("INTENT_CLASSIFIER_SYSTEM", find_line(r'^INTENT_CLASSIFIER_SYSTEM\s*=')),
    ("Signal Types (SignalType enum)", signal_start),
    ("call_ollama()", find_line(r'def call_ollama\(')),
    ("call_ollama_streamed()", find_line(r'def call_ollama_streamed')),
    ("Signal Parsing (extract_signals)", find_line(r'def extract_signals\(')),
    ("Memory Fetch: handle_fetch_signal", find_line(r'def handle_fetch_signal')),
    ("read_offloaded_file", read_offloaded),
    ("handle_read_offloaded_signal", handle_read_offload),
    ("_page_out_context", page_out),
    ("ensure_ledger_header (Ledger Guard)", find_line(r'def ensure_ledger_header')),
    ("_append_to_ledger (Disk-Write Interceptor)", find_line(r'def _append_to_ledger')),
    ("Checkpoint System", find_line(r'def save_checkpoint')),
    ("run_mesh_pipeline()", run_mesh),
    ("Mesh Work Queue API", submit_task),
    ("Mesh Work Queue API: submit_mesh_task", submit_task),
    ("MeshSignal class", mesh_signal),
    ("ConsensusResult class", consensus_result),
    ("Cross-Agent Edit Detection", detect_cross),
    ("Conflict Resolution (resolve_conflict)", resolve_conflict_fn),
    ("Failure Report (generate_failure_report)", fail_report),
    ("Progressive Output Support", progress_listener),
    ("TagSuggester", tag_suggester),
    ("Entry Point (run_pipeline)", run_entry),
]

for name, ln in key_sections:
    if ln:
        output.append(f"| L{ln} | {name} |")

output.append("")
output.append("---")
output.append("")
output.append("## Classes")
output.append("")
output.append("| Line | Class | Purpose |")
output.append("|------|-------|---------|")

class_purposes = {
    'TokenBudget': 'Track/enforce token budget across pipeline execution',
    'OffloadStore': 'Disk-based overflow buffer for Virtual Context Management',
    'Task': 'Represents a single mesh task (agent + spec + signals)',
    'SignalType': 'Enum: VETO, APPROVE, QUERY, DELEGATE, RESULT, etc.',
    'MeshSignal': 'Data class for inter-agent signals',
    'ConsensusResult': 'Result of conflict resolution check',
    'TagSuggester': 'Post-pipeline tag auto-detection: [Stable Core Concept] / [Likely Regression]',
}

for ln, name, kind in defs:
    if kind == 'class' and name in class_purposes:
        output.append(f"| L{ln} | `{name}` | {class_purposes[name]} |")

output.append("")
output.append("---")
output.append("")
output.append("## Functions (Alphabetical)")
output.append("")
output.append("| Line | Function | Called By |")
output.append("|------|----------|-----------|")

# Known call relationships (from old doc) - we'll just list all functions
for ln, name, kind in defs:
    if kind in ('def', 'async def'):
        output.append(f"| L{ln} | `{name}()` | (see code) |")

output.append("")
output.append("---")
output.append("")
output.append("## Key Configuration Constants")
output.append("")
output.append("| Line | Constant | Value |")
output.append("|------|----------|-------|")

for ln, name in sorted(constants, key=lambda x: x[0]):
    val = lines[ln - 1].split('=', 1)[1].strip() if '=' in lines[ln - 1] else ''
    # Truncate long values
    if len(val) > 80:
        val = val[:77] + '...'
    output.append(f"| L{ln} | `{name}` | `{val}` |")

output.append("")
output.append("---")
output.append(f"\n*Generated by `_regenerate_anchors.py` — {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}*\n")

anchor_text = '\n'.join(output)

# Write the new anchor index
anchor_path = DOCS_DIR / "pipeline_anchor_index.md"
anchor_path.write_text(anchor_text, encoding="utf-8")
print(f"\n✅ Wrote {anchor_path} ({len(output)} lines)", file=sys.stderr)

# ===========================================================================
# GENERATE ALL LINE REFERENCES AS A MACHINE-PARSABLE JSON-LIKE FORMAT
# ===========================================================================
print("\n\n=== MACHINE-READABLE REFERENCE ===", file=sys.stderr)
print("=== FORMAT: label -> L<line> ===")
for name, ln in key_sections:
    if ln:
        print(f"SECTION|{name}|L{ln}", file=sys.stderr)
for ln, name, kind in defs:
    print(f"DEF|{kind}|{name}|L{ln}", file=sys.stderr)
for ln, name in constants:
    print(f"CONST|{name}|L{ln}", file=sys.stderr)
for ln, name in system_prompts:
    print(f"PROMPT|{name}|L{ln}", file=sys.stderr)
for ln, name in model_constants:
    print(f"MODEL|{name}|L{ln}", file=sys.stderr)

# ===========================================================================
# ALIGNMENT IS NOW HANDLED BY _repair_and_align.py (separate script)
# This prevents cascading corruption from proximity matching.
# ===========================================================================
print("\n\n  ℹ️  Alignment deferred to _repair_and_align.py (run separately)", file=sys.stderr)

print("\n\nDONE", file=sys.stderr)
