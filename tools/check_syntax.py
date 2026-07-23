"""Script to syntax-check all Python files in the project."""
import os
import sys

count = 0
errors = []
skip_dirs = {'.git', '__pycache__', '.idea', '.reasonix'}

for root, dirs, files in os.walk('.'):
    # Skip hidden dirs and common non-project dirs
    dirs[:] = [d for d in dirs if not d.startswith('.') and d not in skip_dirs]
    for f in files:
        if f.endswith('.py'):
            path = os.path.join(root, f)
            try:
                compile(open(path, encoding='utf-8').read(), path, 'exec')
                count += 1
            except SyntaxError as e:
                errors.append(f'{path}: {e}')

print(f'Total Python files: {count}')
if errors:
    print(f'Syntax errors: {len(errors)}')
    for e in errors:
        print(f'  FAIL: {e}')
    sys.exit(1)
else:
    print('All files pass syntax check.')
