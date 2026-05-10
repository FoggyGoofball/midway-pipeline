Regex Hardening Protocol

Status: Active

Part 1: The Master Regex Patch Script

[x] Task 1: Create a temporary Python script named patch_regexes.py in the root directory.

[x] Task 2: Write logic in patch_regexes.py that reads the target files, applies the exact string replacements below, and writes the files back. Use standard string .replace(old, new).

Replacements for mesh_loops.py:

Target old string:
task_regex = r"### Task (\d+): \[([^\]]+)\] — (.+?)(?:\s*\(DependsOn:\s*(.+?)\))?\s*$"
New string:
task_regex = r"### Task (\d+):\s*\[([^\]]+)\]\s*[-—–]\s*(.+?)(?:\s*\(DependsOn:\s*(.+?)\))?\s*$"

Target old string:
dep_match = re.search(r'Task\s*(\d+)', dep)
New string:
dep_match = re.search(r'Task\s*(\d+)', dep, re.IGNORECASE)

Target old string:
test_match = re.search(r"```(?:cpp)?\s*\n(.*?)```", test_code, re.DOTALL)
New string:
test_match = re.search(r"```(?:cpp|C\+\+|cxx)?\s*\n(.*?)```", test_code, re.DOTALL)

Replacements for mesh_finalize.py:

Target old string:
next_match = re.search(r"- \[ \] (Task \d+: .+)", bp_content)
New string:
next_match = re.search(r"- \[ \]\s*(Task \d+:\s*.+)", bp_content)

Target old string:
entries = re.split(r'(?=^## Session Event)', content, flags=re.MULTILINE)
New string:
entries = re.split(r'(?=^##\s*Session\s*Event)', content, flags=re.MULTILINE)
