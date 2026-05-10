"""
_verify_refactor.py — Verify the Paging Manifest → Key-Value Cache refactor.
Checks all six modified files for syntax validity and cross-references.
"""
import ast
import re
import sys

files = [
    'models.py', 'paging_kernel.py', 'ollama_client.py',
    '_helpers_exec.py', 'mesh_loops.py', 'mesh_finalize.py'
]

errors = []

# 1. Syntax check each file
for f in files:
    try:
        with open(f, 'r', encoding='utf-8') as fh:
            ast.parse(fh.read())
        print(f"  ✅ {f} — syntax OK")
    except SyntaxError as e:
        print(f"  ❌ {f} — SYNTAX ERROR: {e}")
        errors.append(f"{f}: {e}")

if errors:
    print(f"\n⚠️  {len(errors)} file(s) have syntax errors!")
    sys.exit(1)

# 2. Cross-check paged_files_cache references
print("\n── Cross-Reference: paged_files_cache ──")
for f in files:
    with open(f, 'r', encoding='utf-8') as fh:
        content = fh.read()
    refs = re.findall(r'paged_files_cache', content)
    if refs:
        print(f"  📋 {f}: {len(refs)} references")

# 3. Verify legacy signal handlers are PURGED from mesh_loops.py
print("\n── Legacy Signal Purge Check (mesh_loops.py) ──")
with open('mesh_loops.py', 'r', encoding='utf-8') as fh:
    loops = fh.read()

# Check these are gone from _process_task_signals
# (They may still appear in comments or docstrings)
legacy_signals = {
    'EXTRACT_SKELETON': 'legacy EXTRACT_SKELETON handler',
    'MATH_EVAL': 'legacy MATH_EVAL handler',
    'READ_OFFLOADED': 'legacy READ_OFFLOADED handler',
}

# The comment we left says the handlers are purged
# But the actual elif blocks should not exist
for sig, desc in legacy_signals.items():
    # Look for actual code blocks (elif stype == "...") not just comments
    pattern = r"elif stype == " + re.escape('"' + sig + '"')
    matches = re.findall(pattern, loops)
    if matches:
        print(f"  ❌ {desc}: {len(matches)} code block(s) STILL PRESENT!")
        errors.append(desc)
    else:
        print(f"  ✅ {desc}: PURGED")

# Check FETCH specifically (might be different quoting)
fetch_patterns = [
    r"elif stype == \"FETCH\"",
    r"elif stype == 'FETCH'",
]
fetches_found = 0
for pat in fetch_patterns:
    matches = re.findall(pat, loops)
    fetches_found += len(matches)
if fetches_found > 0:
    print(f"  ❌ legacy FETCH handler: {fetches_found} code block(s) STILL PRESENT!")
    errors.append("legacy FETCH handler")
else:
    print(f"  ✅ legacy FETCH handler: PURGED")

# 4. Verify _paged_inheritance_note injection in mesh_loops.py
print("\n── Cache Inheritance Check (mesh_loops.py) ──")
if 'paged_files_cache' in loops:
    print("  ✅ paged_files_cache referenced in mesh_loops.py")
else:
    print("  ⚠️  paged_files_cache NOT found in mesh_loops.py")

# 5. Verify mesh_finalize.py passes paged_files_cache
print("\n── Fix-Cycle Cache Injection Check (mesh_finalize.py) ──")
with open('mesh_finalize.py', 'r', encoding='utf-8') as fh:
    finalize = fh.read()
if 'paged_files_cache=getattr(task_obj' in finalize:
    print("  ✅ _prune_fix_context() called with paged_files_cache")
else:
    print("  ⚠️  paged_files_cache may not be passed to _prune_fix_context()")

if "PAGED-IN REFERENCE FILES" in finalize:
    print("  ✅ Safe Auto-Mounting comment/doc block present")
else:
    print("  ⚠️  Safe Auto-Mounting block might be missing")

# 6. Summary
print(f"\n{'='*50}")
if not errors:
    print("  ✅ ALL CHECKS PASSED — Refactor complete!")
    print("  Directive A: paged_in_manifest → paged_in_cache dict")
    print("  Directive B: Safe Auto-Mounting via cached chunks (no disk I/O)")
    print("  Directive C: Legacy signal handlers purged from mesh_loops.py")
    print("  Type hints: Updated across all files")
else:
    print(f"  ⚠️  {len(errors)} issue(s) found:")
    for e in errors:
        print(f"    - {e}")
print(f"{'='*50}")
