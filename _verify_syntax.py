"""Quick syntax check for modified files."""
import ast, sys

files = [
    r"C:\Users\Admin\source\repos\midway-pipeline\mesh_fetches_blueprint.py",
    r"C:\Users\Admin\source\repos\midway-pipeline\_finalize_preflight.py",
    r"C:\Users\Admin\source\repos\midway-pipeline\_finalize_review.py",
    r"C:\Users\Admin\source\repos\midway-pipeline\mesh_tasks.py",
]
all_ok = True
for f in files:
    try:
        with open(f, encoding="utf-8") as fh:
            ast.parse(fh.read())
        print(f"  ✅ {f.split(chr(92))[-1]}")
    except SyntaxError as e:
        print(f"  ❌ {f.split(chr(92))[-1]}: {e}")
        all_ok = False
sys.exit(0 if all_ok else 1)
