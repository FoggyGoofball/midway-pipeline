"""Fix indentation of post-fix section in _finalize_review.py"""
with open(r'c:\Users\Admin\source\repos\midway-pipeline\_finalize_review.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find the line with "if not _found_rv:" and re-indent everything until "continue"
start = None
for i, line in enumerate(lines):
    if 'if not _found_rv:' in line and lines[i-1].strip().endswith('break'):
        start = i
        break

if start is None:
    print("Could not find 'if not _found_rv:' block")
else:
    # Find the line with "continue" that ends the post-fix section
    # This is at the end of the else: block
    # Actually, let's find "ctx.seen_code_hashes_set.add(normalized)" followed by "continue"
    end = start
    for i in range(start, len(lines)):
        if lines[i].strip() == 'continue':
            end = i
            break
    
    print(f"Re-indenting lines {start+1}-{end+1}")
    
    # The for loop over task_ids_in_review starts the else: block
    # Code inside is at 16-space indent
    # We need everything from 'if not _found_rv:' to 'continue' at 16 spaces
    
    for i in range(start, end + 1):
        stripped = lines[i].lstrip()
        if stripped.startswith(('#', 'import ', '_code_fence_re', '_delegate_only_re', 
                               'for _ftid', '_has_code', '_is_delegate', 'if _is_delegate',
                               'print(', 'if _ftid', '_reverted', 'ctx.all_results_dict',
                               'for _i_rv2', 'if _e_rv2', 'if not _found_rv2',
                               'ctx.all_results.append', 'try:', 'from _finalize_preflight',
                               '_flush_results_to_workspace', '_inject_empty_output_errors',
                               '_inject_static_pattern_errors', 'if ctx.pre_flight_errors',
                               'print(', 'except Exception', 'print(f"  [Post-Fix Preflight]',
                               '_merged_reg', 'if _merged_reg:', '_fixed_tids =',
                               '_needs_remerge', 'if _needs_remerge:', 'print(f"  [Post-Fix Re-Merge]',
                               'from _finalize_conflicts', 'ctx = _remerge', 'print(f"  [Post-Fix Re-Merge]',
                               'normalized =', 'if check_insanity_similarity', 
                               'print(', 'ctx.review_verdict', 'break',
                               'ctx.seen_code_hashes_set', 'continue',
                               'fix_output =', 'f"### {tid}', 'for tid,', 
                               '"""')) or stripped.startswith('#') or stripped.startswith('"""')):
            lines[i] = '                ' + stripped
        elif stripped.startswith('    '):
            lines[i] = '                ' + stripped[4:]
        elif stripped:
            # Check if it starts with already 16-space indent
            if line[:16] == '                ' and len(line) > 16:
                pass  # Already at 16 spaces
            else:
                lines[i] = '                ' + stripped
    
    with open(r'c:\Users\Admin\source\repos\midway-pipeline\_finalize_review.py', 'w', encoding='utf-8') as f:
        f.writelines(lines)
    print("Done!")
