#!/usr/bin/env python3
"""
REPAIR cascading digit-extension AND apply precise backtick-only alignment.
Strategy: 
  1. Build truth table from anchor index (backtick code-name → correct line)
  2. For each doc, first uncascade (repair Ldigits that got digit-extended)
  3. Then apply single-pass regex replacements using only backtick-anchored patterns
  4. Verify no remaining errors
"""
import re, sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
DOCS_DIR = PROJECT_ROOT / "docs"

# ── Phase 1: Build truth table ──────────────────────────────────────
anchor = (DOCS_DIR / "pipeline_anchor_index.md").read_text("utf-8")
truth = {}

# Functions: | L<N> | `func_name()` |
for m in re.finditer(r'\|\s*L(\d+)\s*\|\s*`(\w+)`\(\)', anchor):
    truth[m.group(2)] = int(m.group(1))

# Classes (CamelCase): | L<N> | `ClassName` |
for m in re.finditer(r'\|\s*L(\d+)\s*\|\s*`([A-Z][a-zA-Z0-9]+)`', anchor):
    truth[m.group(2)] = int(m.group(1))

# UPPER_CASE names (constants, system prompts): | L<N> | `NAME` |
for m in re.finditer(r'\|\s*L(\d+)\s*\|\s*`([A-Z][A-Z_0-9]+)`', anchor):
    truth[m.group(2)] = int(m.group(1))

print(f"Truth: {len(truth)} code-name entries", file=sys.stderr)

# ── Phase 2: Build correction map ────────────────────────────────────
# For each truth entry, derive all possible "corrupted" forms (digit extension patterns)
corrections = {}  # corrupted_string → correct_string

# All expected correct line number strings
correct_lines = {f"L{v}" for v in truth.values()}

for name, correct_line_num in truth.items():
    correct_str = f"L{correct_line_num}"
    
    # Pattern: L<N> gets extended to L<N><last_digit> (digit extension cascade)
    last_digit = str(correct_line_num % 10)
    corrupted_extended = f"L{correct_line_num}{last_digit}"
    if corrupted_extended != correct_str:
        corrections[corrupted_extended] = correct_str
    
    # Also handle 2-digit extensions like L12555 (from L1255 extended twice)
    corrupted_extended2 = f"L{correct_line_num}{last_digit}{last_digit}"
    if corrupted_extended2 != correct_str:
        corrections[corrupted_extended2] = correct_str
    
    # Pattern: L<N>0 (trailing zero extension - L422 already in truth, L4220 was correct)
    # Skip if the "corrupted" form is actually another correct reference
    corrupted_zero = f"L{correct_line_num}0"
    if corrupted_zero != correct_str and corrupted_zero not in correct_lines:
        corrections[corrupted_zero] = correct_str

# Add common cascading patterns observed:
cascade_fixes = {
    "L12555": "L1255", "L12417": "L1241", "L12622": "L1262",
    "L12799": "L1279", "L12854": "L1285", "L12911": "L1291",
    "L12999": "L1299", "L13066": "L1306", "L13155": "L1315",
    "L13444": "L1344", "L13553": "L1355", "L13977": "L1397",
    "L18000": "L1800", "L15455": "L1545", "L15533": "L1553",
    "L7788": "L778", "L24888": "L2488", "L25188": "L2518",
    "L23322": "L2332", "L23611": "L2361", "L42200": "L4220",
    "L42266": "L4226", "L17088": "L1708", "L17311": "L1731",
    "L24677": "L2467", "L20511": "L2051", "L2771": "L277",
    "L611": "L61", "L621": "L62", "L6722": "L672",
    "L7111": "L711", "L13633": "L1363", "L39777": "L3977",
    "L39855": "L3985", "L39977": "L3997", "L40100": "L4010",
    "L40310": "L4031", "L40511": "L4051", "L4140": "L4140",
    "L43666": "L4366", "L4788": "L478", "L922": "L92",
    "L6377": "L637", "L6751": "L675", "L22911": "L2291",
    "L39547": "L3954",
}
corrections.update(cascade_fixes)

# Remove any that would cause a correct reference to become wrong
for k in list(corrections.keys()):
    if k in correct_lines:
        del corrections[k]
    # Also check v is in truth
    target_num = int(corrections[k][1:])
    if target_num not in truth.values() and target_num > 0:
        # Only keep if target appears in ANY truth value
        pass  # keep it - might still be useful

print(f"Correction patterns: {len(corrections)}", file=sys.stderr)

def uncascade(text):
    """Uncascade digit-extended L<digits> references."""
    count = 0
    for corrupted, correct in sorted(corrections.items(), key=lambda x: -len(x[0])):
        n = text.count(corrupted)
        if n > 0:
            text = text.replace(corrupted, correct)
            count += n
    return text, count

def apply_backtick_alignment(text, truth_table):
    """Apply backtick-anchored replacements only."""
    entries = sorted(truth_table.items(), key=lambda x: -len(x[0]))
    count = 0
    
    for name, correct in entries:
        if len(name) <= 2:
            continue
        esc = re.escape(name)
        
        # Pattern: `name()` L<digits> → `name()` L<correct>
        pat = rf'`{esc}\(\)`\s*L(\d+)'
        def repl(m):
            nonlocal count
            old = int(m.group(1))
            if old != correct:
                count += 1
                return f'`{name}()` L{correct}'
            return m.group(0)
        text = re.sub(pat, repl, text)
        
        # Pattern: `name()` at L<digits> → `name()` at L<correct>
        pat2 = rf'`{esc}\(\)`\s+at\s+L(\d+)'
        def repl2(m):
            nonlocal count
            old = int(m.group(1))
            if old != correct:
                count += 1
                return f'`{name}()` at L{correct}'
            return m.group(0)
        text = re.sub(pat2, repl2, text)
        
        # Pattern: L<digits> for `name()` → L<correct> for `name()`
        pat3 = rf'L(\d+)\s+for\s+`{esc}\(\)`'
        def repl3(m):
            nonlocal count
            old = int(m.group(1))
            if old != correct:
                count += 1
                return f'L{correct} for `{name}()`'
            return m.group(0)
        text = re.sub(pat3, repl3, text)
        
        # Pattern: | L<digits> | `name()` → | L<correct> | `name()`
        pat4 = rf'\|\s*L(\d+)\s*\|\s*`{esc}\(\)`'
        def repl4(m):
            nonlocal count
            old = int(m.group(1))
            if old != correct:
                count += 1
                return f'| L{correct} | `{name}()`'
            return m.group(0)
        text = re.sub(pat4, repl4, text)
        
        # Pattern: `Name` L<digits> → `Name` L<correct>
        pat5 = rf'`{esc}`\s+L(\d+)'
        def repl5(m):
            nonlocal count
            old = int(m.group(1))
            if old != correct:
                count += 1
                return f'`{name}` L{correct}'
            return m.group(0)
        text = re.sub(pat5, repl5, text)
        
        # Pattern: `Name` at L<digits> → `Name` at L<correct>
        pat6 = rf'`{esc}`\s+at\s+L(\d+)'
        def repl6(m):
            nonlocal count
            old = int(m.group(1))
            if old != correct:
                count += 1
                return f'`{name}` at L{correct}'
            return m.group(0)
        text = re.sub(pat6, repl6, text)
        
        # Pattern: `name()` in proximity to L<digits> in a table cell (range refs)
        pat7 = rf'(\|\s*)L(\d+)(-L\d+\s*\|\s*[^|]*`{esc}[^`]*`)'
        def repl7(m):
            nonlocal count
            old = int(m.group(2))
            if old != correct:
                count += 1
                return f'{m.group(1)}L{correct}{m.group(3)}'
            return m.group(0)
        text = re.sub(pat7, repl7, text)
        
        # Pattern: Table cell with name and range | ... `name()` ... L<N>-L<M> |
        # Replace the FIRST L<digits> that appears before `name()`
        for _ in range(3):  # multiple passes for overlapping
            pat8 = rf'`{esc}\(\)`[^|\n]*?L(\d+)'
            def repl8(m):
                nonlocal count
                old = int(m.group(1))
                if old != correct:
                    count += 1
                    # Replace just the number in the match
                    s = m.group(0)
                    return s.replace(m.group(1), str(correct), 1)
                return m.group(0)
            text = re.sub(pat8, repl8, text)
        
        # Pattern: name class at L<digits> (natural text)
        pat9 = rf'\b{esc}\s+class\s+at\s+L(\d+)'
        def repl9(m):
            nonlocal count
            old = int(m.group(1))
            if old != correct:
                count += 1
                return f'{name} class at L{correct}'
            return m.group(0)
        text = re.sub(pat9, repl9, text)
    
    return text, count

def verify(text, truth_table):
    """Return list of errors."""
    errors = []
    for name, correct in sorted(truth_table.items(), key=lambda x: -len(x[0])):
        for m in re.finditer(rf'`{re.escape(name)}\(\)`.*?L(\d+)', text):
            found = int(m.group(1))
            if found != correct:
                errors.append((name, found, correct))
    return errors

# ── Process docs ─────────────────────────────────────────────────────
for doc_label, doc_path in [("checklist", "pipeline_master_checklist.md"),
                             ("agent_todo", "pipeline_agent_todo.md")]:
    fpath = DOCS_DIR / doc_path
    text = fpath.read_text("utf-8")
    
    # Step 1: Uncascade
    text, uncount = uncascade(text)
    
    # Step 2: Apply backtick alignment
    text, alcount = apply_backtick_alignment(text, truth)
    
    # Step 3: Fix header
    text = re.sub(
        r'> \*\*File:\*\* `pipeline\.py` \(\d+[,\d]* lines, ~\d+ KB\)',
        '> **File:** `pipeline.py` (4,440 lines, ~178 KB)',
        text
    )
    
    fpath.write_text(text, "utf-8")
    errors = verify(text, truth)
    
    print(f"\n{'='*50}", file=sys.stderr)
    print(f"  {doc_label}: {uncount} uncascades, {alcount} alignment changes", file=sys.stderr)
    if errors:
        for name, found, correct in errors[:10]:
            print(f"  ❌ `{name}()` L{found} → expected L{correct}", file=sys.stderr)
        if len(errors) > 10:
            print(f"  ... and {len(errors)-10} more errors", file=sys.stderr)
    else:
        print(f"  ✅ 0 verification errors", file=sys.stderr)

print(f"\n{'='*50}", file=sys.stderr)
print(f"\nDONE", file=sys.stderr)
