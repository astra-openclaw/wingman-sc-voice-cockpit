"""Payload-only AST/import/subprocess and privacy-boundary regression checks."""
import ast
import json
import os
from pathlib import Path
import re
import sys
ROOT=Path(__file__).resolve().parents[2]
source=ROOT/'sc_voice_cockpit/keybindings'
allowed=set(sys.stdlib_module_names)|{'skills'}
# Maintainers can add their own private words (comma-separated) through the environment.
private=[w.strip() for w in os.environ.get('SC_KB_PRIVATE_WORDS','').split(',') if w.strip()]
private_pattern=r'/home/|[A-Z]:\\\\Users\\\\'+(r'|\b(?:'+'|'.join(map(re.escape,private))+r')\b' if private else '')
shipped=[ROOT/'sc_voice_cockpit'/n for n in ('README.md','CHANGELOG.md','NOTICE.md','LICENSE')]+sorted(source.iterdir())
for p in shipped:
    if p.is_file() and p.suffix in {'','.md','.py','.json','.yaml','.mjs'}:
        assert not re.search(private_pattern,p.read_text(encoding="utf-8")),(p,'private wording')
for p in source.glob('*.py'):
    text=p.read_text(encoding="utf-8");tree=ast.parse(text,feature_version=(3,11))
    for n in ast.walk(tree):
        if isinstance(n,(ast.Import,ast.ImportFrom)):
            names=[x.name for x in n.names] if isinstance(n,ast.Import) else [n.module or '']
            for name in names:
                root=name.split('.')[0];assert root in allowed,(p,root)
                assert root not in {'socket','http','urllib','requests','websockets','keyboard','mouse','pynput','pyautogui'},(p,root)
                if root == 'ctypes':assert p.name=='self_heal.py'
        if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute):
            if n.func.attr in ('read_text','write_text'):
                assert any(k.arg=='encoding' for k in n.keywords),(p,n.lineno,'implicit encoding')
            if n.func.attr=='WinDLL':
                assert p.name=='self_heal.py' and n.args[0].value == 'kernel32'
        if isinstance(n,ast.Call) and isinstance(n.func,ast.Name):
            assert n.func.id not in {'eval','exec','input','__import__'},(p,n.func.id)
main=ast.parse((source/'main.py').read_text(encoding="utf-8"))
subprocesses=[n for n in ast.walk(main) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and isinstance(n.func.value,ast.Name) and n.func.value.id=='subprocess' and n.func.attr=='run']
assert len(subprocesses)==1
run=subprocesses[0];assert isinstance(run.args[0],ast.List) and len(run.args[0].elts)==6
assert not any(k.arg=='shell' for k in run.keywords)
assert any(k.arg=='timeout' and isinstance(k.value,ast.Constant) and k.value.value==180 for k in run.keywords)
facade_calls=[]
for n in ast.walk(main):
    if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute):
        text=ast.unparse(n.func)
        if text.startswith('self.wingman.commands.'):facade_calls.append(text);assert text.endswith(('.all','.save')),text
assert facade_calls
js=(source/'extract_defaults.mjs').read_text(encoding="utf-8")
imports=re.findall(r"from '([^']+)'",js)
assert set(imports)=={'node:fs','node:path','node:zlib','node:url'}
assert not re.search(r'child_process|fetch\(|https?:|net\.',js)
assert not (source/'command_manifest.json').exists()
license_text=(ROOT/'sc_voice_cockpit/NOTICE.md').read_text(encoding="utf-8")
assert 'MIT License' in license_text and 'Copyright (c) 2026 Dominicus' in license_text
assert not (ROOT/'LICENSE-PENDING.md').exists()
assert re.search(r"^author: Dominicus$",(source/'default_config.yaml').read_text(encoding="utf-8"),re.M)
assert set(p.name for p in source.iterdir() if p.is_file())=={'__init__.py','main.py','keybinds_core.py','self_heal.py','p4k_reader.py','vocabulary.json','default_config.yaml','extract_defaults.mjs'}
print(json.dumps(dict(status='PASS',python='3.11 syntax; stdlib plus public Wingman Skill API',network_or_input_APIs='none',node_subprocesses=1,node_arguments='fixed helper, --game path, --out path',node_timeout_seconds=180,commands_facade='supported .all()/.save(); mutations on captured Wingman loop',process_probe='read-only ctypes Toolhelp32 snapshot, ABI self-check, fail-closed',payload_privacy='eight source/config files only; no manifest, game data, logs or personal identifiers'),indent=2))
