#!/usr/bin/env python3
"""Independent acceptance gate. Never trusts what the agent says: re-runs the recipe in a clean shell.
usage: gate.py <name> [--no-install]   -> prints PASS/FAIL + reasons, writes supervisor/verdicts/<name>.json
exit 0 = PASS, 1 = FAIL, 2 = NEEDS_SUDO (recipe ok but apt packages missing)"""
import json, os, re, subprocess, sys, tempfile, time

KIT = '/home/thor-01/offensive'
SUP = f'{KIT}/supervisor'
ENV = f'{SUP}/kit_env.sh'
KINDS = {'tool', 'lab', 'data', 'skip'}
HEAD = {'tool': ['개요', '설치', '검증', '기본 사용법', '실전 예제', '주의사항'],
        'lab':  ['개요', '설치', '검증', '기본 사용법', '실전 예제', '주의사항'],
        'data': ['개요', '구성', '활용'], 'skip': ['개요', '구성', '활용']}
BAD_OUT = re.compile(r'Traceback \(most recent|command not found|No module named|ModuleNotFoundError|'
                     r'cannot execute|exec format error|No such file or directory|ImportError|panic:', re.I)
ASKPASS = os.path.expanduser('~/.config/kit_askpass.sh')  # trusted sudo for apt (supervisor only)


def sh(script, timeout, cwd):
    clean = {'HOME': os.environ['HOME'], 'USER': os.environ.get('USER', ''), 'LANG': 'C.UTF-8',
             'PATH': '/usr/bin:/bin', 'TERM': 'dumb'}
    try:
        p = subprocess.run(['bash', '-c', script], capture_output=True, text=True, timeout=timeout,
                           cwd=cwd, env=clean, stdin=subprocess.DEVNULL)
        return p.returncode, (p.stdout + p.stderr)
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b'') + (e.stderr or b'')
        return 124, (out.decode(errors='replace') if isinstance(out, bytes) else out) + f'\n[gate] TIMEOUT {timeout}s'


def gate(name, do_install=True):
    R = []  # reasons
    v = {'name': name, 'ts': time.strftime('%F %T'), 'pass': False}
    rf = f'{KIT}/install/recipes/{name}.sh'
    mf = f'{KIT}/docs/manuals/{name}.md'
    if not os.path.isfile(rf):
        R.append(f'recipe missing: {rf} (copy {SUP}/recipe_template.sh)')
        return v, R
    txt = open(rf, encoding='utf-8', errors='replace').read()
    rc, out = sh(f'bash -n "{rf}"', 20, KIT)
    if rc:
        R.append(f'recipe has bash syntax error: {out.strip()[:300]}')
        return v, R
    if '/home/thor-01' in txt:
        R.append('recipe hardcodes /home/thor-01 — use $KIT/$BIN/$VENVS/$GHD instead')
    if re.search(r'\bsudo\b', txt):
        R.append('recipe uses sudo — forbidden; list apt packages in NEEDS_SUDO_APT instead')
    rc, out = sh(f'source "{ENV}"; source "{rf}"; '
                 'printf "NAME=%s\\nKIND=%s\\nREPO=%s\\nNEEDS=%s\\nSKIP=%s\\n" "$NAME" "$KIND" "$REPO" "$NEEDS_SUDO_APT" "$SKIP_REASON"; '
                 'declare -F do_install >/dev/null || echo NOFUNC_install; declare -F do_verify >/dev/null || echo NOFUNC_verify; '
                 'echo ---; declare -f do_verify', 30, KIT)
    meta = dict(l.split('=', 1) for l in out.split('---')[0].splitlines() if '=' in l)
    body = out.split('---', 1)[-1]
    kind = meta.get('KIND', '').strip()
    v.update(kind=kind, repo=meta.get('REPO', ''), needs_apt=meta.get('NEEDS', '').split())
    if meta.get('NAME', '').strip() != name:
        R.append(f'NAME in recipe must be "{name}" (got "{meta.get("NAME")}")')
    if kind not in KINDS:
        R.append(f'KIND must be one of {sorted(KINDS)} (got "{kind}")')
        return v, R
    for f in ('install', 'verify'):
        if f'NOFUNC_{f}' in out:
            R.append(f'function do_{f}() not defined')
    if kind == 'skip':
        if len(meta.get('SKIP', '').strip()) < 15:
            R.append('KIND=skip needs a concrete SKIP_REASON (>=15 chars)')
    else:
        if re.search(r'\|\|\s*true|exit 0|return 0|^\s*:\s*;?\s*$', body, re.M):
            R.append('do_verify must not mask failures (no "|| true", "exit 0", "return 0", bare ":")')
        if re.search(r'^\s*false\s*;?\s*$', body, re.M):
            R.append('do_verify still contains the template placeholder `false`')
    # ---- manual
    if not os.path.isfile(mf):
        R.append(f'manual missing: {mf}')
    else:
        m = open(mf, encoding='utf-8', errors='replace').read()
        lines = [l for l in m.splitlines() if l.strip()]
        need_lines = 40 if kind in ('tool', 'lab') else 15
        if len(lines) < need_lines:
            R.append(f'manual too short: {len(lines)} non-empty lines < {need_lines}')
        hangul = len(re.findall(r'[가-힣]', m))
        if hangul < (300 if kind in ('tool', 'lab') else 80):
            R.append(f'manual must be written in Korean (only {hangul} Hangul chars)')
        han = re.findall(r'[一-鿿。，：（）]+', re.sub(r'```.*?```', '', m, flags=re.S))
        if len(''.join(han)) > 3:
            R.append('manual contains Chinese characters/punctuation (write Korean only, replace them): '
                     + ' '.join(sorted(set(han))[:15]))
        for h in HEAD[kind]:
            if not re.search(rf'^##\s*{re.escape(h)}', m, re.M):
                R.append(f'manual missing heading "## {h}"')
        if kind in ('tool', 'lab') and m.count('```bash') < 5:
            R.append(f'manual needs >=5 ```bash example blocks (has {m.count("```bash")})')
    if kind == 'skip':
        return v, R
    # ---- apt prerequisites the agent could not install itself
    missing = []
    for pkg in v['needs_apt']:
        rc, _ = sh(f'dpkg -s {pkg} 2>/dev/null | grep -q "Status: install ok installed"', 10, KIT)
        if rc:
            missing.append(pkg)
    v['missing_apt'] = missing
    if missing and not R:
        # Trusted path only: the supervisor (not the agent) installs declared apt packages via sudo.
        if os.path.exists(ASKPASS) and not os.environ.get('GATE_NO_SUDO'):
            inst = (f'export SUDO_ASKPASS="{ASKPASS}"; sudo -A -k apt-get update -qq && '
                    f'sudo -A apt-get install -y {" ".join(missing)}')
            rc, out = sh(inst, 1200, KIT)
            v['apt_install_rc'] = rc
            still = [p for p in missing if sh(f'dpkg -s {p} 2>/dev/null | grep -q "Status: install ok installed"', 10, KIT)[0]]
            if still:
                v['needs_sudo'] = True
                return v, [f'NEEDS_SUDO: apt install failed for: {" ".join(still)} (rc={rc})\n{out[-500:]}']
            v['apt_installed'] = missing
        else:
            v['needs_sudo'] = True
            return v, [f'NEEDS_SUDO: apt packages not installed: {" ".join(missing)} (recipe otherwise ok)']
    if R:
        return v, R
    # ---- reproduce: install + verify in a clean shell from a neutral cwd
    with tempfile.TemporaryDirectory(prefix='gate_') as tmp:
        pre = f'set -o pipefail; source "{ENV}"; source "{rf}"; '
        clone = '[ -d "$SRC" ] || git clone -q --depth 1 "$REPO" "$SRC" || exit 3; '
        if do_install:
            rc, out = sh(pre + clone + 'do_install', 1500, tmp)
            v['install_rc'], v['install_tail'] = rc, out[-1500:]
            if rc:
                R.append(f'do_install failed rc={rc} in clean shell. tail:\n{out[-800:]}')
                return v, R
        rc, out = sh(pre + 'do_verify', 240, tmp)
        v['verify_rc'], v['verify_out'] = rc, out[-2000:]
        if rc:
            R.append(f'do_verify failed rc={rc} in clean shell. output:\n{out[-800:]}')
        elif kind in ('tool', 'lab') and len(out.strip()) < 20 and not re.search(r'grep -q', body):
            R.append('do_verify printed (almost) nothing — make it run the tool and show/check its output')
        elif BAD_OUT.search(out):
            R.append(f'do_verify exited 0 but output shows an error: {BAD_OUT.search(out).group(0)}\n{out[-600:]}')
    return v, R


def main():
    name = sys.argv[1]
    v, R = gate(name, '--no-install' not in sys.argv)
    v['reasons'] = R
    v['pass'] = not R
    os.makedirs(f'{SUP}/verdicts', exist_ok=True)
    json.dump(v, open(f'{SUP}/verdicts/{name}.json', 'w'), ensure_ascii=False, indent=1)
    if not R:
        print(f'PASS {name} kind={v.get("kind")}')
        sys.exit(0)
    if v.get('needs_sudo'):
        print(f'NEEDS_SUDO {name} — recipe and manual are OK; supervisor will install apt packages later. Reply "DONE {name} {v.get("kind")}" now.')
        print(' -', R[0]); sys.exit(2)
    print(f'FAIL {name} — fix these and run the gate again:')
    for r in R:
        print(' -', r)
    sys.exit(2 if v.get('needs_sudo') else 1)


if __name__ == '__main__':
    main()
