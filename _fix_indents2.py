"""Fix indentation in _finalize_review.py after the else: block.
Lines 928-1028 need correct nesting inside the else: block.
"""
import sys

with open(r'c:\Users\Admin\source\repos\midway-pipeline\_finalize_review.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Print current state around the problem area
print("=== CURRENT STATE (928-960) ===")
for i in range(927, 960):
    if i < len(lines):
        indent = len(lines[i]) - len(lines[i].lstrip())
        print(f"{i+1:4d} ({indent:2d}sp): {lines[i].rstrip()}")

# The structure should be:
#   else:                -> 12 spaces (line 731)
#     code at 16 spaces  
#       for tid:         -> 16 spaces (line 736)  
#         body at 20 spaces
#       if not _found_rv: -> 16 spaces - after the for loop
#         body at 20 spaces
#       fix_output = ...  -> 16 spaces
#       # Post-Fix ...    -> 16 spaces
#         import re...    -> 16 spaces  
#         _code_fence_re  -> 16 spaces
#         for _ftid:      -> 16 spaces
#           body at 20 spaces
#         try:            -> 16 spaces
#           body at 20 spaces
#         except:         -> 16 spaces
#         # Insanity...   -> 16 spaces
#         normalized =    -> 16 spaces
#         continue        -> 16 spaces
#     break               -> 12 spaces (end of while)

# Current buggy state:
# Line 928: `if not _found_rv:` at 12 spaces - wrong, should be 16
# Lines 931-1028: all at 12 spaces - mostly wrong, should be different depths

# Strategy: wrap the entire post-fix section in a `if domain_fix_outputs:` block
# at 12-space indent. Then re-indent all lines to correct depths.

# Actually, the simplest approach: 
# 1. The `else:` block starts at line 731 (12 spaces)
# 2. All code inside should be at >= 16 spaces
# 3. Lines 928-1028 should all be at 16+ spaces (inside the else:)

# Lines that start new blocks:
# - `if not _found_rv:`     -> 16 spaces (inside else:, after for loop)
# - `fix_output = ...`      -> 16 spaces (inside else:)
# - `for _ftid, _fout...`   -> 16 spaces (inside else:)  
# - `try:`                  -> 16 spaces (inside else:)
# - `except Exception...`   -> 16 spaces (inside else:)
# - `if _merged_reg:`        -> 16 spaces (inside else:)
# - `if _needs_remerge:`     -> 16 spaces (inside else:)
# - `normalized = ...`      -> 16 spaces (inside else:)
# - `continue`              -> 16 spaces (inside else:)

# Body of those blocks -> 20 spaces

indent_map = {}

# Build the correct indentation map
for i in range(927, 1028):
    raw = lines[i]
    stripped = raw.lstrip()
    if not stripped:
        indent_map[i] = raw  # Empty line, preserve
        continue
    
    # Line 928: `if not _found_rv:` 
    if stripped.startswith('if not _found_rv:'):
        indent_map[i] = '                ' + stripped  # 16 spaces
    # Lines that start import or top-level statements in the else:
    elif stripped.startswith('import ') or stripped.startswith('_code_fence_re') or stripped.startswith('_delegate_only_re'):
        indent_map[i] = '                ' + stripped  # 16 spaces
    # `for _ftid, _fout` at 16 spaces
    elif stripped.startswith('for _ftid'):
        indent_map[i] = '                ' + stripped  # 16 spaces
    # Lines at 20 spaces inside the for loop
    elif any(stripped.startswith(p) for p in ['_has_code', '_is_delegate', 'if _is_delegate', 
                                               'print(f"  [Post-Fix]', 'if _ftid in _pre_fix_snapshot',
                                               '_reverted =', 'ctx.all_results_dict[',
                                               '_found_rv2 = False',
                                               'for _i_rv2', 'if _e_rv2.get', 'ctx.all_results[',
                                               '_found_rv2 = True', 'break',
                                               'if not _found_rv2:', 'ctx.all_results.append']):
        indent_map[i] = '                    ' + stripped  # 20 spaces
    # try: at 16 spaces
    elif stripped.startswith('try:'):
        indent_map[i] = '                ' + stripped  # 16 spaces
    # Lines at 20 spaces inside try
    elif any(stripped.startswith(p) for p in ['from _finalize_preflight import',
                                               '_flush_results_to_workspace',
                                               'ctx.pre_flight_errors = ""',
                                               '_inject_empty_output_errors',
                                               '_inject_static_pattern_errors',
                                               'if ctx.pre_flight_errors.strip():',
                                               'print(f"  [Post-Fix Preflight]',
                                               'else:',
                                               'except Exception']):
        if stripped == 'else:':
            indent_map[i] = '                ' + stripped  # 16 spaces
        elif stripped.startswith('except Exception'):
            indent_map[i] = '                ' + stripped  # 16 spaces
        else:
            indent_map[i] = '                    ' + stripped  # 20 spaces
    # f-strings continuation at 20+1=20 spaces
    elif all(c == ' ' or c == '\n' for c in stripped) and '(' not in stripped:
        indent_map[i] = stripped  # blank
    elif stripped.startswith('f"') and 'Static guard' in stripped:
        indent_map[i] = '                    ' + stripped  # 20 spaces
    elif stripped.startswith('f"') and 'All static guards' in stripped:
        indent_map[i] = '                    ' + stripped  # 20 spaces
    elif stripped.startswith('f"') and 'retaining previous' in stripped:
        indent_map[i] = '                    ' + stripped  # 20 spaces
    # Merged_reg section
    elif stripped.startswith('_merged_reg'):
        indent_map[i] = '                ' + stripped  # 16 spaces
    elif stripped.startswith('if _merged_reg:'):
        indent_map[i] = '                ' + stripped  # 16 spaces
    elif stripped.startswith('_fixed_tids'):
        indent_map[i] = '                    ' + stripped  # 20 spaces
    elif stripped.startswith('_needs_remerge'):
        indent_map[i] = '                    ' + stripped  # 20 spaces
    elif stripped.startswith('if _needs_remerge:'):
        indent_map[i] = '                    ' + stripped  # 20 spaces
    elif stripped.startswith('print(f"  [Post-Fix Re-Merge]'):
        indent_map[i] = '                    ' + stripped  # 20 spaces
    elif stripped.startswith('from _finalize_conflicts'):
        indent_map[i] = '                    ' + stripped  # 20 spaces
    elif stripped.startswith('ctx = _remerge'):
        indent_map[i] = '                    ' + stripped  # 20 spaces
    elif stripped.startswith('print(f"  [Post-Fix Re-Merge]'):
        indent_map[i] = '                    ' + stripped  # 20 spaces
    # Insanity Detector
    elif stripped.startswith('normalized ='):
        indent_map[i] = '                ' + stripped  # 16 spaces
    elif stripped.startswith('if check_insanity_similarity'):
        indent_map[i] = '                ' + stripped  # 16 spaces
    elif stripped.startswith('print('):
        indent_map[i] = '                    ' + stripped  # 20 spaces
    elif stripped.startswith('f"\\n  [Insanity Detector]'):
        indent_map[i] = '                    ' + stripped  # 20 spaces
    elif stripped.startswith('ctx.review_verdict'):
        indent_map[i] = '                    ' + stripped  # 20 spaces
    elif stripped == 'break':
        indent_map[i] = '                    ' + stripped  # 20 spaces
    elif stripped.startswith('ctx.seen_code_hashes_set'):
        indent_map[i] = '                    ' + stripped  # 20 spaces
    elif stripped == 'continue':
        indent_map[i] = '                ' + stripped  # 16 spaces
    # fix_output = ...
    elif stripped.startswith('fix_output'):
        indent_map[i] = '                ' + stripped  # 16 spaces
    elif stripped.startswith('f"### {tid}'):
        indent_map[i] = '            ' + stripped  # but this is inside a join... complex
    elif stripped.startswith('for tid, output'):
        indent_map[i] = '            ' + stripped
    elif stripped.startswith('"""') or stripped.startswith('#') or stripped.startswith('            #'):
        indent_map[i] = '                ' + stripped  # 16 for comments
    else:
        # Default: assume 12 spaces (same level as else:)
        indent_map[i] = '            ' + stripped

# Apply
for i, new_line in indent_map.items():
    lines[i] = new_line

with open(r'c:\Users\Admin\source\repos\midway-pipeline\_finalize_review.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

print("\n=== AFTER FIX (928-960) ===")
with open(r'c:\Users\Admin\source\repos\midway-pipeline\_finalize_review.py', 'r', encoding='utf-8') as f:
    new_lines = f.readlines()
for i in range(927, 960):
    if i < len(new_lines):
        indent = len(new_lines[i]) - len(new_lines[i].lstrip())
        print(f"{i+1:4d} ({indent:2d}sp): {new_lines[i].rstrip()}")
