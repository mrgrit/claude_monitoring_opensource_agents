#!/usr/bin/env python3
"""Generate offensive_system/CATALOG.md from the live queue + gate verdicts.
Groups every resolved repo by kind (tool / lab / data / skip) with notes, so the
whole kit is surveyable at a glance. Re-run any time: python3 supervisor/make_catalog.py"""
import json, glob, os, time, collections

KIT = '/home/thor-01/offensive'
SUP = f'{KIT}/supervisor'
q = {t['name']: t for t in json.load(open(f'{SUP}/queue.json'))}
V = {}
for f in glob.glob(f'{SUP}/verdicts/*.json'):
    v = json.load(open(f))
    V[v['name']] = v


def manual_blurb(name):
    """First substantive line of the Korean manual (role/skip reason), trimmed."""
    m = f'{KIT}/docs/manuals/{name}.md'
    if not os.path.isfile(m):
        return ''
    for l in open(m, encoding='utf-8', errors='replace'):
        l = l.strip()
        if l and not l.startswith('#') and '개요' not in l:
            return l[:90]
    return ''


def skip_reason(name):
    rf = f'{KIT}/install/recipes/{name}.sh'
    if os.path.isfile(rf):
        import re
        m = re.search(r'SKIP_REASON="([^"]*)"', open(rf).read())
        if m:
            return m.group(1)[:140]
    return q.get(name, {}).get('note', '')[:140]


kinds = collections.defaultdict(list)
for name, v in V.items():
    kinds[v.get('kind', '?')].append(name)
st = collections.Counter(t['status'] for t in q.values())
resolved = st['done'] + st['skip'] + st['needs_sudo']
needs_sudo = [t['name'] for t in q.values() if t['status'] == 'needs_sudo']

TITLES = {
    'tool': ('설치·실행 가능한 도구 (tool)', 'Installed and functionally verified (ran `--help`/the tool itself in a clean shell). Included in `install/install_all.sh`.'),
    'lab':  ('취약 훈련 환경 (lab)', 'Docker/VM-based vulnerable training apps. Verified by validating the compose/setup — NOT by starting the service. Run them yourself with `docker compose up` (see each manual).'),
    'data': ('참조 자료 (data)', 'Wordlists, payloads, cheatsheets, references — nothing to execute. Verified by checking the key files exist.'),
    'skip': ('설치 제외 (skip)', 'Documented only, never installed or run: either impossible on this headless linux/arm64 host (GUI/Windows/special hardware) or an offense-only weapon out of scope. Excluded from `install_all.sh`.'),
}
ORDER = ['tool', 'lab', 'data', 'skip']

out = []
out.append('# Offensive Kit — CATALOG')
out.append('')
out.append(f'_Auto-generated {time.strftime("%Y-%m-%d %H:%M")} by `supervisor/make_catalog.py` from gate verdicts. '
           'Each repo under `gh/` was installed/classified by a local small model (hermes/Qwen) and independently '
           're-verified in a clean shell by the supervisor gate before being recorded here._')
out.append('')
out.append(f'**Progress: {resolved} / {len(q)} resolved** '
           f'(tool {len(kinds.get("tool",[]))} · lab {len(kinds.get("lab",[]))} · '
           f'data {len(kinds.get("data",[]))} · skip {len(kinds.get("skip",[]))} · '
           f'needs_sudo {len(needs_sudo)} · pending {st["pending"]}).')
out.append('')
out.append('| kind | 뜻 | 수 |')
out.append('|---|---|---|')
for k in ORDER:
    out.append(f'| `{k}` | {TITLES[k][0]} | {len(kinds.get(k, []))} |')
out.append(f'| `needs_sudo` | apt 패키지 필요(미설치) | {len(needs_sudo)} |')
out.append('')

for k in ORDER:
    names = sorted(kinds.get(k, []), key=str.lower)
    if not names:
        continue
    title, desc = TITLES[k]
    out.append(f'## {title} — {len(names)}')
    out.append('')
    out.append(desc)
    out.append('')
    if k == 'skip':
        out.append('| repo | 제외 사유 |')
        out.append('|---|---|')
        for n in names:
            out.append(f'| `{n}` | {skip_reason(n) or manual_blurb(n)} |')
    else:
        out.append('| repo | 설명 |')
        out.append('|---|---|')
        for n in names:
            out.append(f'| `{n}` | {manual_blurb(n)} |')
    out.append('')

if needs_sudo:
    out.append('## apt 패키지 필요 (needs_sudo)')
    out.append('')
    out.append('Recipe + manual written and otherwise valid, but a required apt package is unavailable on this host, '
               'so it is **not** in `install_all.sh` yet.')
    out.append('')
    out.append('| repo | 사유 |')
    out.append('|---|---|')
    for n in needs_sudo:
        out.append(f'| `{n}` | {q[n].get("note","")[:140]} |')
    out.append('')

out.append('---')
out.append('')
out.append('## How this was produced')
out.append('')
out.append('- Driver + supervisor: [claude_monitoring_opensource_agents](https://github.com/mrgrit/claude_monitoring_opensource_agents) '
           '— a small local model does the work; a watchdog intervenes on stalls/loops/unsafe commands; '
           'an independent gate re-installs & re-verifies every recipe in a clean shell before publishing.')
out.append('- Per-repo install recipe: `install/recipes/<name>.sh` (`do_install` + `do_verify`).')
out.append('- Per-repo Korean manual: `docs/manuals/<name>.md`.')
out.append('- One-shot installer of everything verified: `install/install_all.sh` (skips and needs_sudo excluded).')
out.append('- Machine-readable verdicts: `reports/install_verdicts.json`.')
out.append('')

open(f'{KIT}/CATALOG.md', 'w').write('\n'.join(out))
print(f'wrote CATALOG.md: {resolved} resolved, {len(q)} total')
