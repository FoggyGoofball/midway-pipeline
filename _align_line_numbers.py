#!/usr/bin/env python3
"""
STABLE line-number alignment — v5.1.
Pure regex pattern substitution — NO proximity matching.
Only replaces when entity name and L<digits> appear in a structured context together.
"""
import re, sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
DOCS_DIR = PROJECT_ROOT / "docs"

# ── Build truth table from anchor index ─────────────────────────────
anchor = (DOCS_DIR / "pipeline_anchor_index.md").read_text("utf-8")
truth = {}

# Functions: `func_name()` → L<N>
for m in re.finditer(r'\|\s*L(\d+)\s*\|\s*`(\w+)`\(\)', anchor):
    truth[m.group(2)] = int(m.group(1))

# Classes and UPPER_CASE names
for m in re.finditer(r'\|\s*L(\d+)\s*\|\s*`([A-Z][A-Z_0-9a-z]+)`', anchor):
    truth[m.group(2)] = int(m.group(1))

print(f"Truth table: {len(truth)} entries", file=sys.stderr)
for k, v in sorted(truth.items(), key=lambda x: (len(x[0]), x[0]), reverse=True):
    if len(k) > 2:
        print(f"  {k} → L{v}", file=sys.stderr)

def apply_substitutions(text, truth_table):
    """Pure regex-based substitution. NO proximity matching."""
    # Sort by name length (longest first) for greedy matching
    entries = sorted(truth_table.items(), key=lambda x: -len(x[0]))
    
    for name, correct in entries:
        if len(name) <= 2:
            continue
        
        escaped = re.escape(name)
        
        # Pattern 1: `name()` L<digits> → `name()` L<correct>
        text = re.sub(rf'`{escaped}\(\)`\s*L(\d+)', f'`{name}()` L{correct}', text)
        
        # Pattern 2: L<digits> for `name()` → L<correct> for `name()`
        text = re.sub(rf'L(\d+)\s+for\s+`{escaped}\(\)`', f'L{correct} for `{name}()`', text)
        
        # Pattern 3: `name()` at L<digits> → `name()` at L<correct>
        text = re.sub(rf'`{escaped}\(\)`\s+at\s+L(\d+)', f'`{name}()` at L{correct}', text)
        
        # Pattern 4: `Name` L<digits> → `Name` L<correct>
        text = re.sub(rf'`{escaped}`\s+L(\d+)', f'`{name}` L{correct}', text)
        
        # Pattern 5: | L<digits> | `name()` → | L<correct> | `name()`
        text = re.sub(rf'\|\s*L(\d+)\s*\|\s*`{escaped}\(\)`', f'| L{correct} | `{name}()`', text)
        
        # Pattern 6: `name()` in natural text: L<digits> → `name()` in natural text: L<correct>
        text = re.sub(rf'`{escaped}\(\)`[^L]*L(\d+)', lambda m: m.group(0).replace(m.group(1), str(correct), 1), text)
        
        # Pattern 7: Handle table cells with ranges like | L<N>-L<other> | Description with `name()`
        # Replace the FIRST L<digits> in the range if the cell contains the name
        text = re.sub(
            rf'(\|\s*)L(\d+)(-L\d+\s*\|\s*[^|]*`{escaped}[^`]*`)',
            lambda m: f'{m.group(1)}L{correct}{m.group(3)}',
            text
        )
        
        # Pattern 8: `name` at L<digits>
        text = re.sub(rf'`{escaped}`\s+at\s+L(\d+)', f'`{name}` at L{correct}', text)
        
        # Pattern 9: name followed by "class" or "function" near L<digits> (no backticks needed for class names)
        text = re.sub(rf'\b{escaped}\s+class\s+at\s+L(\d+)', f'{name} class at L{correct}', text)
    
    return text

def verify(text, truth_table, label):
    """Count how many function refs have WRONG line numbers."""
    errors = 0
    for name, correct in sorted(truth_table.items(), key=lambda x: -len(x[0])):
        for m in re.finditer(rf'`{re.escape(name)}\(\)`.*?L(\d+)', text):
            found = int(m.group(1))
            if found != correct:
                print(f"  ❌ [{label}] `{name}()` → L{found} (expected L{correct})", file=sys.stderr)
                errors += 1
    return errors

# ── Process each doc ─────────────────────────────────────────────────
results = []
for doc_label, doc_path in [("checklist", "pipeline_master_checklist.md"),
                             ("agent_todo", "pipeline_agent_todo.md")]:
    fpath = DOCS_DIR / doc_path
    original = fpath.read_text("utf-8")
    updated = apply_substitutions(original, truth)
    
    if updated != original:
        fpath.write_text(updated, "utf-8")
        print(f"\n  ✅ {doc_label}: changes applied", file=sys.stderr)
    else:
        print(f"\n  ⚠️  {doc_label}: no changes", file=sys.stderr)
    
    errs = verify(updated, truth, doc_label)
    results.append((doc_label, errs))

# ── Fix header ──────────────────────────────────────────────────────
mcl = (DOCS_DIR / "pipeline_master_checklist.md").read_text("utf-8")
mcl = re.sub(r'> \*\*File:\*\* `pipeline\.py` \(\d+[,\d]* lines, ~\d+ KB\)',
             '> **File:** `pipeline.py` (4,440 lines, ~178 KB)', mcl)
(DOCS_DIR / "pipeline_master_checklist.md").write_text(mcl, "utf-8")

# ── Summary ──────────────────────────────────────────────────────────
print("\n\n=== VERIFICATION SUMMARY ===", file=sys.stderr)
all_ok = True
for label, errs in results:
    status = "✅ ZERO ERRORS" if errs == 0 else f"❌ {errs} remaining"
    print(f"  {status} — {label}", file=sys.stderr)
    if errs > 0:
        all_ok = False

if all_ok:
    print("\n  🎉 ALL LINE NUMBERS VERIFIED CORRECT!", file=sys.stderr)
else:
    print("\n  ⚠️  Remaining errors require manual fix", file=sys.stderr)

print("\nDONE", file=sys.stderr)
