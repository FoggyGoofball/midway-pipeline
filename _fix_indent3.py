"""Fix indentation: move lines 931-1028 inside the else: block.

The else: block starts at line 731 (12 spaces). Lines 928-929 are at 16/20 
inside else:. But lines 931-1028 are OUTSIDE the else: at 8/12 spaces.
We need to add 4 more spaces to every line from 931-1028 to push them inside else:.
"""
with open(r'c:\Users\Admin\source\repos\midway-pipeline\_finalize_review.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# First, let's see current indentation of key lines
print("=== CURRENT INDENTATION ===")
for i in [730, 731, 732, 927, 928, 929, 930, 931, 932, 936, 937, 942, 943, 1027, 1028]:
    if i < len(lines):
        indent = len(lines[i]) - len(lines[i].lstrip())
        print(f"  {i+1:4d}: ({indent:2d}sp) {lines[i].rstrip()}")

# The fix: every non-empty line from 931 to 1028 gets 4 more spaces
# This pushes them from 8/12 to 12/16 (inside the else:)
for i in range(930, 1028):  # 0-indexed: 930 = line 931
    stripped = lines[i].lstrip()
    if stripped:
        lines[i] = '    ' + lines[i]  # add 4 spaces

# Now verify
print("\n=== AFTER FIX INDENTATION ===")
for i in [927, 928, 929, 930, 931, 932, 936, 937, 942, 943, 1027, 1028]:
    if i < len(lines):
        indent = len(lines[i]) - len(lines[i].lstrip())
        print(f"  {i+1:4d}: ({indent:2d}sp) {lines[i].rstrip()}")

with open(r'c:\Users\Admin\source\repos\midway-pipeline\_finalize_review.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

print("Done!")
