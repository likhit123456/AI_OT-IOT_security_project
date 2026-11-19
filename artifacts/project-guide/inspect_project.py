"""Read-only project inventory used to build the learning guide."""
import ast
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
paths = subprocess.check_output(['git', '-c', f'safe.directory={ROOT.as_posix()}', 'ls-files'], cwd=ROOT, text=True).splitlines()
records = []
for name in paths:
    p = ROOT / name
    raw = p.read_bytes()
    rec = dict(path=name, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    try:
        s = raw.decode('utf-8-sig')
        if '\0' in s: raise ValueError('binary')
    except (UnicodeError, ValueError):
        rec['kind'] = 'binary'
        records.append(rec)
        continue
    rec.update(kind='text', lines=len(s.splitlines()))
    if p.suffix == '.py':
        try:
            tree = ast.parse(s)
        except SyntaxError as exc:
            rec['parse_note'] = f'Legacy syntax is incompatible with Python 3.13 AST: {exc.msg}, line {exc.lineno}'
            tree = ast.parse(s.replace('server.async import', 'server.async_compat import'))
        rec['doc'] = ast.get_docstring(tree) or ''
        rec['imports'] = [ast.unparse(n) for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
        def symbols(body, prefix=''):
            out = []
            for n in body:
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    d = dict(name=prefix+n.name, line=n.lineno, end=n.end_lineno, kind=type(n).__name__, doc=ast.get_docstring(n) or '')
                    if not isinstance(n, ast.ClassDef):
                        d['signature'] = ast.unparse(n.args)
                        d['calls'] = sorted(set(ast.unparse(x.func) for x in ast.walk(n) if isinstance(x, ast.Call)))
                        d['branches'] = [dict(line=x.lineno, condition=ast.unparse(x.test)) for x in ast.walk(n) if isinstance(x, (ast.If, ast.While))]
                    out.append(d)
                    out.extend(symbols(n.body, prefix+n.name+'.'))
            return out
        rec['symbols'] = symbols(tree.body)
    elif p.suffix in ('.json', '.ipynb'):
        try:
            obj = json.loads(s)
            rec['schema'] = list(obj) if isinstance(obj, dict) else {'type':type(obj).__name__, 'length':len(obj)}
        except ValueError: pass
    elif p.suffix == '.jsonl':
        rows = [json.loads(x) for x in s.splitlines() if x.strip()]
        rec['rows'] = len(rows)
        rec['schema'] = list(rows[0]) if rows and isinstance(rows[0], dict) else []
    records.append(rec)
OUT.mkdir(exist_ok=True, parents=True)
(OUT/'inventory.json').write_text(json.dumps(records, indent=2), encoding='utf-8')
overview = []
for r in records:
    if 'symbols' in r:
        overview += [r['path'], r['doc'][:2300], '; '.join(x['name'] for x in r['symbols']), '']
(OUT/'module-overview.txt').write_text('\n'.join(overview), encoding='utf-8')
print(json.dumps({'tracked_files':len(records),'python_files':sum('symbols' in r for r in records),'text_lines':sum(r.get('lines',0) for r in records)}))
