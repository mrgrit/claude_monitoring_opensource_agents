#!/usr/bin/env python3
"""Hermes supervisor: drives the small local model through queue.json one repo at a time,
watches its live stream-json, intervenes automatically, and escalates to the human/Claude supervisor.

  files
    queue.json            task list + status (pending|running|done|needs_sudo|skip|review|failed)
    logs/<name>/rN.jsonl  raw hermes stream per round
    events.log            human timeline (everything)
    alerts.log            things needing supervisor attention (AUTO = auto-intervened, ESCALATE = needs a human/Claude)
    control/              commands from ctl.py (nudge / skip / pause / stop)
    run/current.json      what is running now (task, round, pid, counters)
"""
import collections, contextlib, fcntl, hashlib, json, os, re, signal, subprocess, sys, threading, time

KIT = '/home/thor-01/offensive'
SUP = f'{KIT}/supervisor'
Q = f'{SUP}/queue.json'
CTL = f'{SUP}/control'
HERMES = os.path.expanduser('~/.local/bin/hermes')
WID = os.environ.get('WID') or str(os.getpid())  # worker id (parallel-safe current file)

def _envint(k, d):
    try:
        return int(os.environ.get(k, d))
    except ValueError:
        return d

MAX_ROUNDS = _envint('KIT_MAX_ROUNDS', 8)        # hermes invocations per task (initial + nudges + gate retries)
MAX_AUTO_NUDGES = _envint('KIT_MAX_NUDGES', 5)   # beyond this -> escalate, park task as "review"
MAX_GATE_FAILS = _envint('KIT_MAX_GATEFAILS', 3)
# No stream event while the model is generating (no tool running). With N workers sharing one
# inference slot, a single large-context response can legitimately take many minutes, so this must
# be generous enough not to false-trigger under contention. Only a TRUE hang should cross it.
STALL_SEC = _envint('KIT_STALL_SEC', 900)
TOOL_HANG_SEC = _envint('KIT_TOOL_HANG_SEC', 1500)  # a single tool call running this long
TOOL_CAP = _envint('KIT_TOOL_CAP', 90)              # tool calls per task
ROUND_BUDGET = _envint('KIT_ROUND_BUDGET', 3000)    # hermes --run-budget per round

FORBID = [
    # sudo only when it actually INVOKES a privileged action (not when "sudo" is a grep/cat argument or appears in a recipe's text).
    (re.compile(r'\bsudo\s+(?:-\w+\s+)*(?:apt|apt-get|dpkg|systemctl|service|yum|dnf|snap|pip\d?|npm|make|tee|cp|mv|rm|chmod|chown|mkdir|ln|bash|sh|docker|mount|modprobe|sysctl|useradd|usermod|visudo|passwd|kill)\b'),
     'sudo is not available/allowed. Put required apt packages into NEEDS_SUDO_APT in the recipe and continue without them.'),
    # apt install/remove/etc. only in command position (start or after a separator), not inside a grep/cat of some text.
    (re.compile(r'(?:^|[;&|(]|&&|\|\|)\s*(?:timeout\s+\d+\s+)?apt(?:-get)?\s+(?:install|remove|purge|upgrade)'),
     'apt is not allowed (no sudo). List packages in NEEDS_SUDO_APT instead.'),
    (re.compile(r'--break-system-packages'), 'Never pip-install into system python. Use a venv: python3 -m venv "$VENVS/<name>".'),
    (re.compile(r'(?:^|[;&|(]|&&|\|\|)\s*(?:timeout\s+\d+\s+)?git\s+push\b'), 'git push is forbidden.'),
    (re.compile(r'(>>?|tee\s+-a?)\s*~?/?(home/thor-01/)?\.(bashrc|profile|zshrc)'), 'Do not edit shell rc files. Put binaries/wrappers in kit/bin.'),
    (re.compile(r'\brm\s+-[a-zA-Z]*r[a-zA-Z]*\s+(-[a-z]+\s+)*(/|~|\$HOME|/home/thor-01/?|/home/thor-01/offensive/?|\$KIT/?|\.\.?/?)(\s|$|;)'), 'Destructive rm on a top-level directory is forbidden.'),
    # Match only the rm's OWN argument segment ([^;&|] stops the match crossing && / ; / | into an
    # unrelated `cd .../gh` later in the chain). Blocks `rm -rf .../gh` (the whole dir), not `.../gh/<subdir>`.
    (re.compile(r'\brm\s+-[a-zA-Z]*r[a-zA-Z]*[^;&|]*?(/home/thor-01/offensive/(gh|bin|install|docs|supervisor|victim|state)/?)(\s|$|;|&|\|)'), 'Never delete kit top-level dirs (gh, bin, install, docs, supervisor, victim, state).'),
]
SCANNERS = re.compile(r'\b(nmap|masscan|naabu|nuclei|sqlmap|hydra|nikto|ffuf|gobuster|dirb|wfuzz|httpx|katana|dalfox|amass|subfinder|puredns|massdns)\b')
# Well-known public DNS resolvers: used AS resolvers by DNS tools (massdns/puredns/dnsx) to look up the
# safe test domain example.com — a benign lookup, not an attack. Allowed so DNS tools can be verified.
PUBLIC_RESOLVERS = {'8.8.8.8', '8.8.4.4', '1.1.1.1', '1.0.0.1', '9.9.9.9', '149.112.112.112', '208.67.222.222'}
SAFE_HOST = re.compile(r'^(127\.\d+\.\d+\.\d+|localhost|::1|0\.0\.0\.0|example\.(com|org|net)|.*\.example\.(com|org|net)|scanme\.nmap\.org)$')
HOSTISH = re.compile(r'(?<![\w./-])((?:\d{1,3}\.){3}\d{1,3}(?:/\d+)?|(?:[a-z0-9-]+\.)+(?:com|net|org|io|kr|gov|edu|co|me|ai|xyz))(?![\w-])', re.I)

GUIDE = {
    'stall': "SUPERVISOR: You have produced no action for {sec}s — you are over-thinking. Stop deliberating. "
             "State in ONE sentence what you will do next, then immediately run exactly one concrete command. "
             "Current goal for {name}: {goal}",
    'loop': "SUPERVISOR: You ran the same command {n} times: `{cmd}`. It will not give a different result. "
            "Do something different: read the error, check the README/build file, try another install method, "
            "or if this repo cannot work on linux/arm64, set KIND=skip with a SKIP_REASON and write the short manual.",
    'errs': "SUPERVISOR: {n} commands in a row failed. Step back. Summarise the root cause in one sentence, "
            "then choose a simpler path (e.g. a plain venv + requirements.txt, `go build ./...`, or a prebuilt release). "
            "If it is truly impossible here, use KIND=skip. Last error: {err}",
    'hang': "SUPERVISOR: Your command `{cmd}` ran {sec}s and was killed. Always wrap long commands in `timeout 600` "
            "and avoid interactive programs (pass --help / -h, use -y, never open a REPL or TUI).",
    'cap': "SUPERVISOR: You have used {n} tool calls on {name}. Wrap up NOW: write the recipe with what works "
           "(or KIND=skip with reason), write the manual, run the gate, reply DONE.",
    'budget': "SUPERVISOR: The previous round hit its time budget. Continue from where you are; do not redo finished steps. "
              "Next: {goal}",
    'noresult': "SUPERVISOR: You stopped without passing the gate. Run `bash {sup}/gate.sh {name}` and fix every FAIL reason. "
                "Reply DONE only after it prints PASS (or NEEDS_SUDO).",
    'gatefail': "SUPERVISOR: Independent gate re-check FAILED for {name}. Fix exactly these problems, rerun the gate, then reply DONE:\n{why}",
}


def now():
    return time.strftime('%F %T')


def log(kind, name, msg, alert=None):
    line = f'[{now()}] {kind:<9} {name:<28} {msg}'
    with open(f'{SUP}/events.log', 'a') as f:
        f.write(line + '\n')
    if alert:
        with open(f'{SUP}/alerts.log', 'a') as f:
            f.write(f'[{now()}] {alert:<8} {name:<28} {kind}: {msg}\n')
    print(line, flush=True)


@contextlib.contextmanager
def flock(path):
    """Cross-process advisory lock so parallel workers don't clobber queue.json / git."""
    f = open(path, 'w')
    try:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(f, fcntl.LOCK_UN)
        f.close()


def load_q():
    return json.load(open(Q))


def save_q(q):
    tmp = f'{Q}.{WID}.tmp'
    json.dump(q, open(tmp, 'w'), indent=1, ensure_ascii=False)
    os.replace(tmp, Q)


def set_task(name, **kw):
    with flock(f'{SUP}/.queue.lock'):
        q = load_q()
        for t in q:
            if t['name'] == name:
                t.update(kw)
        save_q(q)


def claim_next(only):
    """Atomically take the first pending task and mark it running for this worker."""
    with flock(f'{SUP}/.queue.lock'):
        q = load_q()
        for t in q:
            if t['status'] == 'pending' and (not only or t['name'] in only):
                t['status'] = 'running'
                t['attempts'] = t.get('attempts', 0) + 1
                t['started'] = now()
                t['worker'] = WID
                save_q(q)
                return t
    return None


def ctl_take(name):
    """Return list of (cmd, payload) control requests and delete them."""
    out = []
    if not os.path.isdir(CTL):
        return out
    for f in sorted(os.listdir(CTL)):
        p = f'{CTL}/{f}'
        # filename is "<cmd>.<target>.<ts>"; target may itself contain dots (e.g. cheat.sh,
        # cvecrowd.com, Mr.Holmes), so cmd is the first field, ts the last, target the middle.
        parts = f.split('.')
        if len(parts) < 3:
            continue
        cmd, target = parts[0], '.'.join(parts[1:-1])
        if cmd in ('pause', 'stop'):
            continue  # level-triggered, handled by caller
        if target not in ('*', name):
            continue
        out.append((cmd, open(p).read().strip()))
        os.remove(p)
    return out


def goal_hint(name):
    rf = f'{KIT}/install/recipes/{name}.sh'
    mf = f'{KIT}/docs/manuals/{name}.md'
    if not os.path.exists(rf):
        return 'get the tool running, then write the recipe install/recipes/%s.sh' % name
    if not os.path.exists(mf):
        return 'write the Korean manual docs/manuals/%s.md, then run the gate' % name
    return f'run `bash {SUP}/gate.sh {name}` and fix what it reports'


def render_prompt(t):
    p = open(f'{SUP}/PROMPT.md').read()
    extra = ''
    if t.get('guidance'):
        extra = '\n\nSUPERVISOR GUIDANCE FOR THIS REPO (from a previous attempt — follow it):\n' + t['guidance']
    return (p.replace('__NAME__', t['name']).replace('__URL__', t['url']).replace('__HINT__', t['hint'])
             .replace('__LANGS__', ','.join(t['langs']) or '-').replace('__MARKERS__', ','.join(t['markers']) or '-')) + extra


class Round:
    """One hermes invocation + live watchdog."""

    def __init__(self, t, rnd, query, stats):
        self.t, self.name, self.rnd, self.query, self.st = t, t['name'], rnd, query, stats
        os.makedirs(f'{SUP}/logs/{self.name}', exist_ok=True)
        self.logf = f'{SUP}/logs/{self.name}/r{rnd}.jsonl'
        self.last_ev = time.time()
        self.pending_tool = None  # (cmd, started)
        self.recent = collections.deque(maxlen=10)
        self.err_streak = 0
        self.last_err = ''
        self.final = None
        self.intervention = None  # (reason_key, message, alert_level)
        self.self_passed = None   # (ts, tools) when the agent's own gate self-test first printed PASS
        self.lock = threading.Lock()

    def start(self):
        qf = f'{SUP}/run/query.{WID}.r{self.rnd}.txt'  # per-worker+round: never shared across parallel workers
        open(qf, 'w').write(self.query)
        cmd = [HERMES, 'chat', '--query-file', qf, '--format', 'stream-json', '--yolo',
               '--max-turns', '60', '--run-budget', str(ROUND_BUDGET),
               '--continue', f'kitp-{self.name}', '--create-if-missing']
        self.p = subprocess.Popen(cmd, cwd=KIT, stdout=subprocess.PIPE, stderr=open(self.logf + '.err', 'a'),
                                  stdin=subprocess.DEVNULL, text=True, start_new_session=True)
        threading.Thread(target=self.reader, daemon=True).start()

    def kill(self):
        try:
            os.killpg(self.p.pid, signal.SIGTERM)
            for _ in range(20):
                if self.p.poll() is not None:
                    return
                time.sleep(0.5)
            os.killpg(self.p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def intervene(self, key, msg, level='AUTO'):
        with self.lock:
            if self.intervention is None:
                self.intervention = (key, msg, level)

    def reader(self):
        with open(self.logf, 'a') as lf:
            for line in self.p.stdout:
                lf.write(line)
                lf.flush()
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                self.on_event(ev)

    def on_event(self, ev):
        self.last_ev = time.time()
        typ = ev.get('type')
        if typ == 'tool_use':
            self.st['tools'] += 1
            inp = ev.get('input', {})
            cmd = inp.get('command') or json.dumps(inp, ensure_ascii=False)
            cmd = ' '.join(str(cmd).split())
            self.pending_tool = (cmd, time.time())
            log('tool', self.name, f'#{self.st["tools"]} {ev.get("name")}: {cmd[:160]}')
            self.check_cmd(ev.get('name'), cmd)
            # Re-running the gate after editing the recipe is legitimate iteration, not a loop
            # (same command string, but the recipe it tests changed). Only flag true repetition.
            # Exclude recipe re-testing from loop detection: re-running the gate, or sourcing the recipe
            # and calling do_verify/do_install, is legitimate fix-and-retry (same command string, but the
            # recipe it exercises changed each time) — not a true repetition loop.
            if not any(s in cmd for s in ('gate.sh', 'gate.py', 'do_verify', 'do_install')):
                key = hashlib.md5(cmd.encode()).hexdigest()
                self.recent.append((key, cmd))
                n = sum(1 for k, _ in self.recent if k == key)
                if n >= 3:
                    self.intervene('loop', GUIDE['loop'].format(n=n, cmd=cmd[:200]))
            if self.st['tools'] >= TOOL_CAP and not self.st.get('capped'):
                self.st['capped'] = True
                self.intervene('cap', GUIDE['cap'].format(n=self.st['tools'], name=self.name), 'ESCALATE')
        elif typ == 'tool_result':
            self.pending_tool = None
            out = str(ev.get('output', ''))
            rc = None
            try:
                rc = json.loads(out).get('exit_code')
            except Exception:
                pass
            failed = ev.get('is_error') or (rc not in (None, 0))
            if failed:
                self.err_streak += 1
                self.last_err = out[-300:]
                if self.err_streak >= 6:
                    self.intervene('errs', GUIDE['errs'].format(n=self.err_streak, err=self.last_err))
            else:
                self.err_streak = 0
            if 'PASS ' + self.name in out or 'NEEDS_SUDO ' + self.name in out:
                log('gate', self.name, 'agent self-test reports ' + ('PASS' if 'PASS ' + self.name in out else 'NEEDS_SUDO'))
                if not self.self_passed:
                    self.self_passed = (time.time(), self.st['tools'])
        elif typ == 'result':
            self.final = ev

    # a command whose ONLY action is reading/inspecting files can't execute a privileged op,
    # even if the text it prints happens to contain "sudo"/"apt"/"rm -rf" (e.g. grepping a recipe).
    READONLY = re.compile(r'^(?:cat|less|more|head|tail|grep|rg|egrep|fgrep|awk|sed\s+-n|wc|diff|ls|find|file|stat|cut|sort|uniq|echo|printf|column)\b')

    def check_cmd(self, tool, cmd):
        if tool not in ('terminal', 'shell', 'bash', 'execute_code', 'run'):
            return
        stripped = cmd.strip().lstrip('(').strip()
        if self.READONLY.match(stripped) and not re.search(r'[;&|]|&&|\|\||\$\(|`', stripped):
            return  # pure single read-only inspection; no execution possible
        for rx, why in FORBID:
            if rx.search(cmd):
                self.intervene('forbid', f'SUPERVISOR: Command blocked: `{cmd[:160]}`. {why}', 'ESCALATE')
                return
        if SCANNERS.search(cmd):
            for h in HOSTISH.findall(cmd):
                host = h.split('/')[0]
                if not SAFE_HOST.match(host) and host not in PUBLIC_RESOLVERS and not h.startswith(('gh.', 'github.')):
                    if re.search(r'(github\.com|go\.dev|golang\.org|pypi\.org|projectdiscovery\.io)', h):
                        continue
                    self.intervene('forbid', f'SUPERVISOR: Command blocked: `{cmd[:160]}` targets external host {h}. '
                                   'Only 127.0.0.1 / localhost / example.com may be used for functional tests.', 'ESCALATE')
                    return

    def watch(self):
        """Block until the round ends. Returns ('exit', final) or ('intervene', (key,msg,level)) or ('ctl', ...)."""
        while True:
            if self.p.poll() is not None:
                time.sleep(1)  # let reader drain
                return 'exit', self.final
            # supervisor commands
            for cmd, payload in ctl_take(self.name):
                if cmd == 'nudge':
                    self.intervene('manual', 'SUPERVISOR: ' + payload, 'MANUAL')
                elif cmd == 'skip':
                    self.kill()
                    return 'skip', payload
                elif cmd == 'abort':
                    self.kill()
                    return 'abort', payload
            if os.path.exists(f'{CTL}/stop'):
                self.kill()
                return 'stop', None
            t = time.time()
            # Auto-finalize: the agent's own gate self-test printed PASS but it keeps working instead of
            # replying DONE (a recurring small-model pattern: over-polishing the manual). Once it has had a
            # grace window to stop on its own, end the round and let run_task run the authoritative gate.
            if self.self_passed and not self.pending_tool:
                ts0, tools0 = self.self_passed
                if t - ts0 > 150 or self.st['tools'] - tools0 >= 4:
                    log('autofinal', self.name, 'agent self-PASSed but did not stop; finalizing round', 'AUTO')
                    self.kill()
                    return 'exit', (self.final or {'exit_code': 0, 'text': 'DONE (auto-finalized after self-PASS)'})
            if self.pending_tool:
                cmd, started = self.pending_tool
                if t - started > TOOL_HANG_SEC:
                    self.intervene('hang', GUIDE['hang'].format(cmd=cmd[:160], sec=int(t - started)))
            elif t - self.last_ev > STALL_SEC:
                self.intervene('stall', GUIDE['stall'].format(sec=int(t - self.last_ev), name=self.name,
                                                              goal=goal_hint(self.name)))
            if self.intervention:
                self.kill()
                return 'intervene', self.intervention
            json.dump({'task': self.name, 'round': self.rnd, 'pid': self.p.pid, 'tools': self.st['tools'],
                       'nudges': self.st['nudges'], 'err_streak': self.err_streak,
                       'idle_s': int(t - self.last_ev), 'pending_tool': self.pending_tool and self.pending_tool[0][:120],
                       'since': self.st['since'], 'worker': WID},
                      open(f'{SUP}/run/current.{WID}.json', 'w'), ensure_ascii=False)
            time.sleep(3)


def run_gate(name):
    p = subprocess.run(['bash', f'{SUP}/gate.sh', name], capture_output=True, text=True, timeout=2000)
    return p.returncode, (p.stdout + p.stderr).strip()


def publish(name, status):
    """Commit ONLY this task's gate-checked artifacts to the kit repo (mrgrit/offensive_system) and push."""
    try:
        with flock(f'{SUP}/.git.lock'):  # serialize index/push across parallel workers
            subprocess.run(['python3', f'{SUP}/assemble.py'], capture_output=True, timeout=120)
            files = [f for f in (f'install/recipes/{name}.sh', f'docs/manuals/{name}.md', 'install/install_all.sh',
                                 'reports/install_verdicts.json') if os.path.exists(f'{KIT}/{f}')]
            g = lambda *a: subprocess.run(['git', '-C', KIT, *a], capture_output=True, text=True, timeout=300)
            g('add', '--', *files)
            if g('diff', '--cached', '--quiet').returncode == 0:
                return
            msg = (f'[hermes] {name}: {status}\n\nRecipe and Korean manual written by hermes-agent (local Qwen 27B).\n'
                   f'Re-installed and verified in a clean shell by supervisor/gate.py before commit.')
            r = g('commit', '-q', '-m', msg, '--', *files)
            if r.returncode:
                log('git', name, 'commit failed: ' + r.stderr.strip()[:200], 'ESCALATE')
                return
            r = g('push', '-q', 'origin', 'HEAD:main')
            if r.returncode:
                g('pull', '-q', '--rebase', 'origin', 'main')
                r = g('push', '-q', 'origin', 'HEAD:main')
            log('git', name, 'pushed to offensive_system' if r.returncode == 0 else 'push failed: ' + r.stderr.strip()[:200],
                None if r.returncode == 0 else 'ESCALATE')
    except Exception as e:
        log('git', name, f'publish error {e}', 'ESCALATE')


# Repos whose PRIMARY purpose is attacking third parties: never installed or functionally
# verified — the supervisor writes a documentation-only skip itself (the small model never
# gets to build them). name -> reason. Keep this tight; dual-use scanners/PoCs are NOT here.
DENY = {
    'DogeRat': 'Telegram-controlled Android RAT built to covertly control victim devices — real-victim malware, not dual-use.',
    'GhostManSec': 'Backdoor generator plus layer4/layer7 DDoS flooder — primary purpose is attacking third parties.',
    'OctoC2': 'Offensive C2 framework for controlling real victim hosts — out of scope for an install-and-verify kit.',
    'router-kill': 'Network-disruption / deauth-flood tool whose purpose is knocking targets offline (DoS).',
}


def force_skip(name, reason):
    """Supervisor writes the skip recipe + Korean manual directly. No install, no hermes."""
    rf = f'{KIT}/install/recipes/{name}.sh'
    mf = f'{KIT}/docs/manuals/{name}.md'
    url = next((t['url'] for t in load_q() if t['name'] == name), '')
    open(rf, 'w').write(
        f'# recipe: {name}  (POLICY SKIP — supervisor)\nNAME="{name}"\nKIND="skip"\nREPO="{url}"\n'
        f'SRC="$GHD/$NAME"\nNEEDS_SUDO_APT=""\nSKIP_REASON="{reason}"\n\n'
        'do_install() { echo "policy skip: not installed"; return 0; }\n'
        'do_verify()  { echo "policy skip"; return 0; }\n')
    open(mf, 'w').write(
        f'# {name} — 설치 제외(SKIP)\n\n## 개요\n\n이 저장소는 **감독자 정책에 의해 설치·동작검증을 수행하지 않고 문서로만 남긴다.**\n\n'
        f'제외 사유: {reason}\n\n본 킷은 인가된 실험실 환경에서의 방어·점검용 도구만 설치한다. '
        f'제3자(타인의 기기·네트워크)를 공격·감염·마비시키는 것이 주목적인 도구는 설치 대상에서 제외한다. '
        f'아래는 저장소의 성격을 식별하기 위한 참고 기록일 뿐, 실행을 전제로 하지 않는다.\n\n'
        f'## 구성\n\n- 원본 저장소: {url}\n- 로컬 클론: `gh/{name}` (참고용, 빌드/실행하지 않음)\n'
        f'- 분류: 공격 전용(offense-only) — dual-use 아님\n\n'
        f'## 활용\n\n설치하지 않는다. 연구·식별 목적이라면 격리된 오프라인 분석 환경에서 정적 분석만 수행하고, '
        f'실제 네트워크·기기를 대상으로 구동하지 않는다. 원샷 설치 스크립트(`install/install_all.sh`)에는 포함되지 않는다.\n')
    set_task(name, status='skip', note='policy: ' + reason, finished=now())
    log('policyskip', name, reason, 'MANUAL')
    run_gate(name)  # records a verdict (kind=skip) so assemble.py excludes it cleanly
    publish(name, 'policy skip (offense-only, documentation only)')


def run_task(t):
    name = t['name']
    if name in DENY:
        force_skip(name, DENY[name])
        return 'next'
    st = {'tools': 0, 'nudges': 0, 'gate_fails': 0, 'since': now()}
    log('start', name, f'[w{WID}] hint={t["hint"]} langs={t["langs"]}')
    query = render_prompt(t)
    for rnd in range(1, MAX_ROUNDS + 1):
        r = Round(t, rnd, query, st)
        r.start()
        what, data = r.watch()
        if what == 'stop':
            set_task(name, status='pending')
            log('stop', name, 'runner stop requested')
            return 'stop'
        if what == 'skip':
            set_task(name, status='skip', note=data, finished=now())
            log('skip', name, f'supervisor skipped: {data}', 'MANUAL')
            return 'next'
        if what == 'abort':
            set_task(name, status='review', note=data, finished=now())
            log('abort', name, f'supervisor parked task: {data}', 'MANUAL')
            return 'next'
        if what == 'intervene':
            key, msg, level = data
            if key != 'manual':
                st['nudges'] += 1
            log('intervene', name, f'[{key}] {msg[:220]}', level)
            if st['nudges'] > MAX_AUTO_NUDGES:
                set_task(name, status='review', note=f'too many auto-interventions (last: {key})', finished=now())
                log('park', name, 'auto-interventions exhausted -> review', 'ESCALATE')
                return 'next'
            query = msg
            continue
        # hermes exited by itself
        fin = data or {}
        txt = (fin.get('text') or '').strip()
        log('exit', name, f'round {rnd} exit={fin.get("exit_code")} tools={st["tools"]} said: {txt[-160:]!r}')
        rc, out = run_gate(name)
        if rc == 0:
            set_task(name, status='done', finished=now(), tools=st['tools'], nudges=st['nudges'])
            log('PASS', name, out.splitlines()[0], 'OK')
            publish(name, 'verified (' + out.split('kind=')[-1].strip() + ')')
            return 'next'
        if rc == 2:
            set_task(name, status='needs_sudo', finished=now(), note=out.splitlines()[-1])
            log('needsudo', name, out.splitlines()[-1], 'SUDO')
            publish(name, 'recipe ok, needs apt packages')
            return 'next'
        st['gate_fails'] += 1
        log('gatefail', name, out.replace('\n', ' | ')[:300])
        if st['gate_fails'] >= MAX_GATE_FAILS:
            set_task(name, status='review', note='gate failed %d times' % st['gate_fails'], finished=now())
            log('park', name, 'gate failures exhausted -> review: ' + out.replace('\n', ' | ')[:300], 'ESCALATE')
            return 'next'
        budget_hit = 'budget' in (fin.get('text') or '').lower() or fin.get('exit_code') not in (0, None)
        if 'DONE' in txt:
            query = GUIDE['gatefail'].format(name=name, why=out)
        elif budget_hit:
            query = GUIDE['budget'].format(goal=goal_hint(name)) + '\nGate status:\n' + out
        else:
            query = GUIDE['noresult'].format(sup=SUP, name=name) + '\nCurrent gate output:\n' + out
    set_task(name, status='review', note='rounds exhausted', finished=now())
    log('park', name, 'max rounds exhausted -> review', 'ESCALATE')
    return 'next'


def main():
    os.makedirs(CTL, exist_ok=True)
    os.makedirs(f'{SUP}/run', exist_ok=True)
    only = sys.argv[1:]
    # crash recovery: reclaim only THIS worker's orphaned running tasks (don't touch a live sibling's).
    with flock(f'{SUP}/.queue.lock'):
        q = load_q()
        for t in q:
            if t['status'] == 'running' and t.get('worker') in (WID, None, ''):
                t['status'] = 'pending'
        save_q(q)
    log('runner', '-', f'started w{WID} pid={os.getpid()} only={only or "all"}', 'INFO')
    while True:
        if os.path.exists(f'{CTL}/stop'):
            break
        if os.path.exists(f'{CTL}/pause'):
            time.sleep(5)
            continue
        t = claim_next(only)
        if not t:
            break
        if run_task(t) == 'stop':
            break
    cf = f'{SUP}/run/current.{WID}.json'
    if os.path.exists(cf):
        os.remove(cf)
    log('runner', '-', f'w{WID} finished / stopped', 'INFO')


if __name__ == '__main__':
    main()
