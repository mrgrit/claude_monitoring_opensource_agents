#!/usr/bin/env python3
"""Deterministic pre-triage of gh/* repos -> supervisor/queue.json (one task per repo).
Gives the small model hard hints (build system, language mix) so it does not guess."""
import json, os, subprocess, collections
H = '/home/thor-01/offensive'; GH = f'{H}/gh'
CODE = {'.py':'python','.go':'go','.js':'node','.ts':'node','.rs':'rust','.c':'c','.cpp':'c','.cc':'c',
        '.java':'java','.kt':'java','.cs':'dotnet','.ps1':'powershell','.sh':'shell','.rb':'ruby','.php':'php'}
DATA = {'.md','.txt','.json','.yaml','.yml','.csv','.lst','.html','.xml','.png','.jpg','.gif','.svg'}
def scan(d):
    ext = collections.Counter(); markers = set(); n = 0
    for root, dirs, files in os.walk(d):
        dirs[:] = [x for x in dirs if x not in ('.git','node_modules','vendor','.github')]
        depth = root[len(d):].count(os.sep)
        for f in files:
            n += 1; e = os.path.splitext(f)[1].lower(); ext[e] += 1
            if depth <= 1 and f in ('go.mod','setup.py','pyproject.toml','requirements.txt','package.json','Cargo.toml',
                                    'Makefile','CMakeLists.txt','Dockerfile','docker-compose.yml','docker-compose.yaml',
                                    'compose.yml','pom.xml','build.gradle','Gemfile','composer.json','install.sh','setup.sh'):
                markers.add(f)
            if e in ('.sln','.csproj','.exe','.dll','.apk'): markers.add(e)
        if n > 40000: break
    return ext, markers, n
out = []
for name in sorted(os.listdir(GH)):
    d = f'{GH}/{name}'
    if not os.path.isdir(d) or name.startswith('_'): continue
    url = subprocess.run(['git','-C',d,'remote','get-url','origin'],capture_output=True,text=True).stdout.strip()
    ext, mk, n = scan(d)
    code = collections.Counter()
    for e, c in ext.items():
        if e in CODE: code[CODE[e]] += c
    data_n = sum(c for e, c in ext.items() if e in DATA)
    langs = [l for l, _ in code.most_common(3)]
    if mk & {'.sln','.csproj','.exe','.dll'} and not (mk & {'go.mod','setup.py','pyproject.toml','requirements.txt'}):
        hint = 'windows'
    elif sum(code.values()) < 3 or data_n > 20 * max(1, sum(code.values())):
        hint = 'data'
    elif mk & {'docker-compose.yml','docker-compose.yaml','compose.yml'} and n > 300:
        hint = 'lab'
    else:
        hint = 'tool'
    out.append({'name': name, 'url': url, 'hint': hint, 'langs': langs, 'markers': sorted(mk), 'files': n,
                'status': 'pending', 'attempts': 0})
order = {'tool':0,'lab':1,'windows':2,'data':3}
out.sort(key=lambda x: (order[x['hint']], x['files']))
qf = f'{H}/supervisor/queue.json'
if os.path.exists(qf):          # preserve progress on re-triage
    old = {x['name']: x for x in json.load(open(qf))}
    for x in out:
        if x['name'] in old: x['status'], x['attempts'] = old[x['name']]['status'], old[x['name']]['attempts']
json.dump(out, open(qf,'w'), indent=1)
print(collections.Counter(x['hint'] for x in out))
