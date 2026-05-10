import ast, sys
for f in ("paging_kernel.py", "ollama_client.py"):
    try:
        with open(f, encoding="utf-8") as fh:
            ast.parse(fh.read())
        print(f"OK: {f} parses clean")
    except SyntaxError as e:
        print(f"FAIL: {f} syntax error: {e}")
        sys.exit(1)
print("All patches verified.")
