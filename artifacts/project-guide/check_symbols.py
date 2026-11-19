import ast
import json
from pathlib import Path

inventory = json.loads(Path('artifacts/project-guide/inventory.json').read_text())
total = 0
for record in inventory:
    if not record['path'].endswith('.py'):
        continue
    source = Path(record['path']).read_text(encoding='utf-8-sig')
    tree = ast.parse(source.replace('server.async import', 'server.async_compat import'))
    count = sum(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) for n in ast.walk(tree))
    total += count
    if count != len(record['symbols']):
        print(record['path'], count, len(record['symbols']))
print('Total AST symbols:', total)
