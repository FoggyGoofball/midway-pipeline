import py_compile, sys
try:
    py_compile.compile('_finalize_review.py', doraise=True)
    print('Syntax OK')
    sys.exit(0)
except py_compile.PyCompileError as e:
    print(f'Syntax Error: {e}')
    sys.exit(1)
