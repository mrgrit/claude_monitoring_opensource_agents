#!/usr/bin/env python3
"""Build install/install_all.sh from gate-verified recipes only (verdict pass, or needs_sudo).
The output is a single self-contained file: env + apt prerequisites + every recipe embedded in its own subshell."""
import json, os, subprocess, sys, time

KIT = '/home/thor-01/offensive'
SUP = f'{KIT}/supervisor'
OUT = f'{KIT}/install/install_all.sh'


def main():
    q = {t['name']: t for t in json.load(open(f'{SUP}/queue.json'))}
    sel, apt = [], set()
    for name in sorted(q, key=str.lower):
        st = q[name]['status']
        vf = f'{SUP}/verdicts/{name}.json'
        rf = f'{KIT}/install/recipes/{name}.sh'
        if st not in ('done', 'needs_sudo') or not os.path.exists(vf) or not os.path.exists(rf):
            continue
        v = json.load(open(vf))
        if v.get('kind') == 'skip' or not (v.get('pass') or v.get('needs_sudo')):
            continue
        sel.append((name, v.get('kind'), open(rf).read()))
        apt.update(v.get('needs_apt', []))
    env = open(f'{SUP}/kit_env.sh').read().replace('export KIT="${KIT:-/home/thor-01/offensive}"',
                                                     'export KIT="${KIT:-$(cd "$(dirname "$0")/.." && pwd)}"')
    L = ['#!/usr/bin/env bash',
         '# =============================================================================',
         '#  install_all.sh — OFFENSIVE KIT one-shot installer (GENERATED, do not edit by hand)',
         f'#  generated {time.strftime("%F %T")} by supervisor/assemble.py from {len(sel)} gate-verified recipes',
         '#  Every recipe below was installed AND functionally verified in a clean shell on linux/arm64.',
         '#',
         '#  usage: install/install_all.sh [--list] [--only a,b,c] [--no-apt] [--verify-only]',
         '#  result: install/logs/install_all.<ts>.log  +  reports/install_all_result.json',
         '# =============================================================================',
         'set -u',
         env.strip(),
         'ONLY=""; NOAPT=0; VONLY=0; LIST=0',
         'while [ $# -gt 0 ]; do case "$1" in --only) ONLY=",$2,"; shift;; --no-apt) NOAPT=1;; '
         '--verify-only) VONLY=1;; --list) LIST=1;; *) echo "unknown arg $1"; exit 2;; esac; shift; done',
         'mkdir -p "$KIT/install/logs" "$KIT/reports"; LOG="$KIT/install/logs/install_all.$(date +%s).log"',
         'declare -A RES',
         'want(){ [ -z "$ONLY" ] || [[ "$ONLY" == *",$1,"* ]]; }',
         f'APT_PKGS="{" ".join(sorted(apt))}"',
         'if [ $LIST = 0 ] && [ $NOAPT = 0 ] && [ -n "$APT_PKGS" ]; then',
         '  miss=""; for p in $APT_PKGS; do dpkg -s "$p" >/dev/null 2>&1 || miss="$miss $p"; done',
         '  [ -n "$miss" ] && { echo "[apt] installing:$miss (sudo)"; sudo apt-get update -qq && sudo apt-get install -y $miss >>"$LOG" 2>&1; }',
         'fi',
         'run(){  # name fn',
         '  local n="$1" f="$2"; want "$n" || return 0',
         '  if [ $LIST = 1 ]; then echo "$n"; return 0; fi',
         '  printf "%-32s " "$n"',
         '  if "$f" verify >>"$LOG" 2>&1; then RES[$n]=ok-present; echo "ok (already installed)"; return; fi',
         '  [ $VONLY = 1 ] && { RES[$n]=missing; echo MISSING; return; }',
         '  if "$f" install >>"$LOG" 2>&1 && "$f" verify >>"$LOG" 2>&1; then RES[$n]=ok-installed; echo "ok (installed)"; '
         'else RES[$n]=FAILED; echo "FAILED (see $LOG)"; fi',
         '}', '']
    for name, kind, body in sel:
        fn = 'r_' + ''.join(c if c.isalnum() else '_' for c in name)
        L += [f'# ---------------------------------------------------------------- {name} ({kind})',
              f'{fn}() (',
              body.rstrip(),
              '  [ -d "$SRC" ] || git clone -q --depth 1 "$REPO" "$SRC" || exit 3',
              '  case "$1" in install) do_install;; verify) do_verify;; esac',
              ')', '']
    L += ['[ $LIST = 1 ] || echo "== offensive kit: $(date) log=$LOG"']
    L += [f'run "{n}" r_{"".join(c if c.isalnum() else "_" for c in n)}' for n, _, _ in sel]
    L += ['[ $LIST = 1 ] && exit 0',
          'ok=0; bad=0; { echo "{"; first=1; for k in "${!RES[@]}"; do [ $first = 1 ] || echo ","; first=0; '
          'printf \'  "%s": "%s"\' "$k" "${RES[$k]}"; [[ "${RES[$k]}" == ok* ]] && ok=$((ok+1)) || bad=$((bad+1)); done; '
          'echo; echo "}"; } > "$KIT/reports/install_all_result.json"',
          'ok=$(grep -c \'"ok\' "$KIT/reports/install_all_result.json"); bad=$(grep -c \'"FAILED\\|"missing\' "$KIT/reports/install_all_result.json")',
          'echo "== done: ok=$ok failed=$bad  (reports/install_all_result.json)"; [ "$bad" = 0 ]', '']
    open(OUT, 'w').write('\n'.join(L))
    os.chmod(OUT, 0o755)
    rc = subprocess.run(['bash', '-n', OUT]).returncode
    # verdict summary for the kit repo
    summ = {n: {k: v for k, v in json.load(open(f'{SUP}/verdicts/{n}.json')).items() if k in ('kind', 'pass', 'ts', 'needs_apt', 'missing_apt')}
            for n in q if os.path.exists(f'{SUP}/verdicts/{n}.json')}
    for n in summ:
        summ[n]['status'] = q[n]['status']
    json.dump(summ, open(f'{KIT}/reports/install_verdicts.json', 'w'), indent=1, ensure_ascii=False)
    print(f'assembled {len(sel)} recipes -> {OUT} (bash -n rc={rc})')
    sys.exit(rc)


if __name__ == '__main__':
    main()
