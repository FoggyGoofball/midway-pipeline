"""Fix indentation: move post-fix section inside the else: block.
Lines 931-1028 must be at 16-space indent (inside the `else:` for `tid in sorted(...)` loop).
"""
with open(r'c:\Users\Admin\source\repos\midway-pipeline\_finalize_review.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Strategy: fix indentation from line 931 to line 1028 (0-indexed: 930 to 1027)
# These lines need 16-space indent (same as line 928's `if not _found_rv:`)
# Actually wait - let me re-examine. The else: block has:
#   else:              -> 12 spaces (line 731)
#     domain_fix_outputs = {}  -> 16 spaces (line 732)
#     for tid in sorted(...):  -> 16 spaces (line 736)
#       content...             -> 20 spaces
#     if not _found_rv:        -> 12 spaces (line 928) - WAIT, that's wrong!
#
# Actually, the `if not _found_rv:` at line 928 is at 12 spaces which means it's
# at SAME level as `else:`. That's a bug too - the `_found_rv` variable was set
# inside the `for tid` loop, so this check should be at 16 spaces.
#
# But let me look more carefully. The `if not _found_rv:` on line 928 matches
# against the `for tid in sorted(task_ids_in_review):` loop on line 736.
# In Python, a `for...else` construct means "if no break occurred". So `if not _found_rv:`
# being at 12 spaces would be OUTSIDE the for loop, at the else: block level.
# But `_found_rv` is set as `False` on line 922 inside the loop body (20 spaces),
# so it's a local variable that escapes. Then `if not _found_rv:` at 12 spaces
# would be inside the `else:` block but outside the `for` loop.
# That's actually correct for a for/else - the else clause runs if no break occurs.
# But wait, Python for-else uses `else:` not `if`. This is just a regular `if` statement.
# So 12 spaces for `if not _found_rv:` is correct - it's inside the `else:` block
# (12 spaces) but after the `for` loop (which was also at 12 spaces).
# 
# But then `fix_output = ...` at 8 spaces is OUTSIDE the `else:` block, which is the bug.
# Lines 938+ at 12 spaces are also outside the `else:` block because 12 != 8.

# Simplest fix: wrap lines 931-1027 inside a new `if domain_fix_outputs:` 
# check, indented to 12 spaces (same level as `else:`), so it only runs
# when we took the domain-fix path.

# Actually, even cleaner: just remove the dead code between the end of the 
# monolithic `if` and the monolithic `continue` on line 1028. Since the
# monolithic path now has a `continue` at line 730, lines 931-1027 only 
# execute for the domain-fix path. So the domain-fix code for `_found_rv`
# needs to be properly indented.

# Let me look at the exact indentation of lines 928-929 to confirm
print(f"Line 929 ({lines[928].rstrip()})")
print(f"Indent: {len(lines[928]) - len(lines[928].lstrip())} spaces")
print(f"Line 930 ({lines[930].rstrip()})" if len(lines) > 930 else "EOF")
print(f"Line 931 ({lines[931].rstrip()})" if len(lines) > 931 else "EOF")
print(f"Expected else: indent = {len(lines[731]) - len(lines[731].lstrip())} spaces")
print(f"Expected for indent = {len(lines[736]) - len(lines[736].lstrip())} spaces")

# Lines 928-929 are at 12-space indent (inside else:, after for:)
# Lines 930-1028 need to be at 12 spaces too (inside else:)
# But currently:
#   Line 931: 8 spaces (outside else: - BUG)
#   Lines 938+: 12 spaces (outside else:, but at wrong level - BUG)

# Fix: re-indent lines 930-1027 to 12 spaces (inside the else: block)
for i in range(930, 1028):
    stripped = lines[i].lstrip()
    if stripped:
        lines[i] = '            ' + stripped  # 12 spaces

with open(r'c:\Users\Admin\source\repos\midway-pipeline\_finalize_review.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)
print("Fixed!")
