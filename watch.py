#!/usr/bin/env python3
"""Attach mode: supervise a hermes session the user already started (needs the cc_watch plugin).

  python3 watch.py list                      live hermes sessions that can be attached
  python3 watch.py attach [SID] [--no-auto]  pick a session (interactive if SID omitted) and watch it
  python3 watch.py say "text" [--now]        send a supervisor message to the attached session
  python3 watch.py show [N]                  last N lines of the readable transcript
  python3 watch.py think                     what the model is streaming right now (text/reasoning tail)

The task itself is the user's business — this only watches *how* hermes works: over-thinking, loops,
failure streaks, hung commands, "can't, tool not installed" excuses, unverified "done" claims.
Mechanical problems get an automatic nudge; every finished turn raises a REVIEW alert so the upper
supervisor (Claude Code, tailing attach_state/alerts.log) can judge quality / hallucination and `say` more.
"""
import collections, json, os, re, sqlite3, sys, time

SUP = os.path.dirname(os.path.abspath(__file__))
BUS = os.environ.get('CC_WATCH_DIR') or os.path.expanduser('~/.hermes/cc_watch')
STATE_DB = os.path.expanduser('~/.hermes/state.db')
OUT = f'{SUP}/attach_state'
CUR = f'{OUT}/current'

STALL_SEC = int(os.environ.get('WATCH_STALL_SEC', 420))          # running, no event at all
THINK_SEC = int(os.environ.get('WATCH_THINK_SEC', 300))          # streaming but no tool call
HANG_SEC = int(os.environ.get('WATCH_HANG_SEC', 1500))           # one tool call still running
REPEAT_N, ERR_N = 3, 5
COOLDOWN = 300
MAX_AUTO = int(os.environ.get('WATCH_MAX_AUTO', 8))

NUDGE = {
    'think': ("You have been thinking for {sec}s ({chars} reasoning chars) without running anything. Stop deliberating: state in ONE "
              "sentence what you will do next, then immediately take that step (run the command / write the answer).", 'now'),
    'stall': ("No progress for {sec}s. State in one sentence where you are and run the next concrete step now.", 'now'),
    'loop': ("You ran the same command {n} times: `{cmd}`. It will not give a different result. Read the error, "
             "and try a different approach.", 'note'),
    'errs': ("{n} commands in a row failed. Step back: state the root cause in one sentence, then take a simpler "
             "path. Last error: {err}", 'note'),
    'hang': ("`{cmd}` has been running {sec}s. If it is waiting for input or will not finish, abort it and rerun with "
             "`timeout` and non-interactive flags.", 'now'),
    'excuse': ("A missing tool/package is not a reason to stop. Install it (pip/pipx/npm/go install/cargo/build from "
               "source/download a release) and finish the task. Only report impossibility after you actually tried "
               "and show the exact error.", 'note'),
}

EXCUSE_RE = re.compile(
    r"(not installed|command not found|isn'?t installed|is not available|are not available|no such (tool|command)|"
    r"unable to (install|access|run|find|use)|(i|we) (can'?t|cannot|couldn'?t|could not|don'?t have)|"
    r"lack(s|ing)? (the )?(tool|access|permission)|beyond my capabilit|"
    r"설치(되어|돼) ?있지 않|설치가 안|도구가 없|툴이 없|명령(어)?(을|를)? 찾을 수 없|할 수 없|불가능|권한이 없|지원하지 않|없어서)",
    re.I)
CLAIM_RE = re.compile(r"(완료|성공|끝났|다 했|해결(했|됨|되었)|\bdone\b|successfully|completed|all tests pass|works now|fixed)", re.I)


def now():
    return time.strftime('%Y-%m-%d %H:%M:%S')


def hms(ts):
    return time.strftime('%H:%M:%S', time.localtime(ts))


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, TypeError):
        return False
    except PermissionError:
        return True


def db_info(sid):
    try:
        c = sqlite3.connect(f'file:{STATE_DB}?mode=ro', uri=True, timeout=2)
        r = c.execute('select title, source, message_count, tool_call_count, last_activity_at from sessions where id=?',
                      (sid,)).fetchone()
        u = c.execute("select content from messages where session_id=? and role='user' order by id desc limit 1",
                      (sid,)).fetchone()
        return (r or (None,) * 5), (u[0] if u else '')
    except Exception:
        return (None,) * 5, ''


def sessions():
    out = []
    if not os.path.isdir(BUS):
        return out
    for sid in os.listdir(BUS):
        try:
            meta = json.load(open(f'{BUS}/{sid}/meta.json'))
        except Exception:
            continue
        ev = f'{BUS}/{sid}/events.jsonl'
        last = os.path.getmtime(ev) if os.path.exists(ev) else meta.get('attached_at', 0)
        out.append({'sid': sid, 'pid': meta.get('pid'), 'cwd': meta.get('cwd'), 'last': last,
                    'alive': alive(meta.get('pid'))})
    return sorted(out, key=lambda s: -s['last'])


def resolve(prefix):
    m = [s for s in sessions() if s['sid'].startswith(prefix)]
    if len(m) != 1:
        sys.exit(f'no unique session matches {prefix!r} ({len(m)} matches) — see `watch.py list`')
    return m[0]['sid']


def current():
    try:
        return open(CUR).read().strip()
    except FileNotFoundError:
        sys.exit('not attached — run `watch.py attach` first (or pass --sid)')


def cmd_list(show_dead=False):
    live = [s for s in sessions() if s['alive'] or show_dead]
    if not live:
        print('No live hermes session with the cc_watch plugin.\n'
              '(Sessions started before the plugin was enabled cannot be attached — restart hermes with '
              '`hermes -c` to continue the same conversation.)')
    for i, s in enumerate(live, 1):
        (title, source, nmsg, ntool, _), umsg = db_info(s['sid'])
        print(f"[{i}] {s['sid']}  pid={s['pid']}{'' if s['alive'] else ' (dead)'}  {source or ''}  "
              f"last={hms(s['last'])}  msgs={nmsg} tools={ntool}\n    title: {title or '-'}\n    cwd:   {s['cwd']}\n"
              f"    task:  {(umsg or '').strip().splitlines()[0][:140] if umsg else '-'}")
    return live


def say(sid, text, mode='note'):
    d = f'{BUS}/{sid}/inbox'
    os.makedirs(d, exist_ok=True)
    p = f'{d}/{time.time_ns()}.json'
    with open(p + '.tmp', 'w') as f:
        json.dump({'text': text, 'mode': mode}, f, ensure_ascii=False)
    os.replace(p + '.tmp', p)


def tool_cmd(tool, args):
    try:
        a = json.loads(args) if isinstance(args, str) else args
    except Exception:
        a = None
    if isinstance(a, dict):
        for k in ('command', 'cmd', 'code', 'path', 'query', 'url'):
            if isinstance(a.get(k), str):
                return f'{tool}: {a[k]}'
    return f'{tool}: {str(args)[:300]}'


class Watcher:
    def __init__(self, sid, auto):
        self.sid, self.auto = sid, auto
        self.dir = f'{OUT}/{sid}'
        os.makedirs(self.dir, exist_ok=True)
        self.tr = open(f'{self.dir}/transcript.log', 'a', encoding='utf-8')
        self.running = False
        self.last_ev = self.last_tool = time.time()
        self.pending = None            # (cmd, started)
        self.recent = collections.deque(maxlen=10)
        self.err_streak, self.last_err = 0, ''
        self.turn_tools = self.turn_errs = 0
        self.turn_start = time.time()
        self.r_chars = self.t_chars = 0  # reasoning vs visible text streamed since the last tool activity
        self.cool = {}
        self.n_auto = 0
        self.gen = None                # (tool, started): model is writing a tool call's arguments
        self.turn_now = 0              # interrupting (--now) nudges sent this turn

    def w(self, line):
        self.tr.write(line + '\n')
        self.tr.flush()

    def alert(self, kind, msg):
        line = f'{now()} [{kind}] {self.sid}: {msg}'
        for p in (f'{self.dir}/alerts.log', f'{OUT}/alerts.log'):
            with open(p, 'a', encoding='utf-8') as f:
                f.write(line + '\n')
        print(line, flush=True)

    def nudge(self, key, **kw):
        if time.time() - self.cool.get(key, 0) < COOLDOWN:
            return
        self.cool[key] = time.time()
        text, mode = NUDGE[key]
        text = text.format(**kw)
        # A --now nudge cancels the in-flight generation. Repeating it can cancel the whole turn
        # (hermes gives up after every retry is redirected), so allow one per turn and escalate after that.
        if not self.auto or self.n_auto >= MAX_AUTO or (mode == 'now' and self.turn_now):
            self.alert('ESCALATE' if self.auto else key.upper(), f'(no auto) {text}')
            return
        self.n_auto += 1
        self.turn_now += mode == 'now'
        say(self.sid, text, mode)
        self.alert(f'AUTO:{key}', text)

    def on(self, ev, live=True):
        t, ts = ev.get('type'), ev.get('ts', time.time())
        if ev.get('bg'):  # hermes' background self-review: log it, but it is not the user's task
            if t == 'tool_start':
                self.w(f'{hms(ts)} (bg) ▶ {tool_cmd(ev.get("tool"), ev.get("args"))[:300]}')
            return
        self.last_ev = time.time() if live else ts
        if t in ('stream', 'tool_start') and not self.running:  # a new turn begins: restart the clocks
            self.last_tool = self.last_ev
            self.turn_start = ts
        if t in ('tool_start', 'tool_end', 'turn_end'):
            self.r_chars = self.t_chars = 0
            self.gen = None
        if t == 'tool_start':
            self.running = True
            cmd = tool_cmd(ev.get('tool'), ev.get('args'))
            self.pending = (cmd, ts)
            self.last_tool = time.time() if live else ts
            self.turn_tools += 1
            self.recent.append(cmd)
            self.w(f'{hms(ts)} ▶ {cmd[:600]}')
            if live and self.recent.count(cmd) >= REPEAT_N:
                self.nudge('loop', n=self.recent.count(cmd), cmd=cmd[:200])
        elif t == 'tool_end':
            self.pending = None
            self.last_tool = time.time() if live else ts
            st = ev.get('status') or 'ok'
            res = (ev.get('result') or '').replace('\n', ' ⏎ ')
            self.w(f'{hms(ts)} ◀ {st} {((ev.get("duration_ms") or 0) / 1000):.1f}s  {res[:400]}')
            if st == 'ok':
                self.err_streak = 0
            else:
                self.err_streak += 1
                self.turn_errs += 1
                self.last_err = res[:300]
                if live and self.err_streak >= ERR_N:
                    self.nudge('errs', n=self.err_streak, err=self.last_err)
        elif t == 'tool_gen':
            self.running = True
            self.gen = (ev.get('tool') or '?', time.time() if live else ts)
            self.w(f'{hms(ts)} ✎ writing {self.gen[0]} arguments')
        elif t == 'stream':
            self.running = True
            c = ev.get('chars') or {}
            self.r_chars += c.get('reasoning', 0)
            self.t_chars += c.get('text', 0)
        elif t == 'turn_end':
            self.running = False
            self.pending = None
            dur = int(ts - self.turn_start)
            a = ev.get('assistant') or ''
            self.w(f'{hms(ts)} ══ TURN END  tools={self.turn_tools} errs={self.turn_errs} {dur}s ══\n'
                   f'  USER: {(ev.get("user") or "")[:1500]}\n  ASSISTANT: {a}\n')
            if live:
                tail = a.strip().replace('\n', ' ')[-300:]
                self.alert('REVIEW', f'turn done tools={self.turn_tools} errs={self.turn_errs} {dur}s — "{tail}"')
                if EXCUSE_RE.search(a):
                    self.alert('EXCUSE', f'gives up / blames missing tool: "{EXCUSE_RE.search(a).group(0)}"')
                    self.nudge('excuse')
                elif CLAIM_RE.search(a) and (self.turn_tools == 0 or self.err_streak > 0):
                    self.alert('CLAIM', f'claims success with tools={self.turn_tools}, last tool failed='
                                        f'{self.err_streak > 0} — verify')
            self.turn_tools = self.turn_errs = 0
            self.turn_now = 0
            self.turn_start = ts
        elif t in ('delivered', 'inject_failed'):
            self.w(f'{hms(ts)} >> SUPERVISOR {t} via {ev.get("via")}/{ev.get("why", "")}: {ev.get("texts")}')
            if t == 'delivered' and live:  # give the model a fresh window to act on the message
                self.last_tool = time.time()
            if t == 'inject_failed' and live:
                self.alert('INJECT_FAILED', f'{ev.get("texts")} (kept for next tool result)')
        elif t in ('session_start', 'session_end'):
            self.w(f'{hms(ts)} -- {t} {ev.get("model", "")}{ev.get("reason", "")}')

    def tick(self):
        n = time.time()
        if self.pending and n - self.pending[1] > HANG_SEC:
            self.nudge('hang', cmd=self.pending[0][:200], sec=int(n - self.pending[1]))
        elif self.gen:  # silent while writing tool arguments: never interrupt, just flag if it takes too long
            if n - self.gen[1] > HANG_SEC and time.time() - self.cool.get('gen', 0) > COOLDOWN:
                self.cool['gen'] = time.time()
                self.alert('SLOWGEN', f'writing {self.gen[0]} arguments for {int(n - self.gen[1])}s')
        elif self.running and not self.pending:
            if n - self.last_ev > STALL_SEC:
                self.nudge('stall', sec=int(n - self.last_ev))
            elif n - self.last_tool > THINK_SEC and self.r_chars > self.t_chars:  # long answers are not over-thinking
                self.nudge('think', sec=int(n - self.last_tool), chars=self.r_chars)

    def run(self):
        ev_path = f'{BUS}/{self.sid}/events.jsonl'
        while not os.path.exists(ev_path):
            time.sleep(1)
        f = open(ev_path, encoding='utf-8')
        backlog = f.readlines()
        self.w(f'\n===== attach {now()} (replaying {len(backlog)} past events) =====')
        for line in backlog[-300:]:
            try:
                self.on(json.loads(line), live=False)
            except Exception:
                pass
        self.last_ev = self.last_tool = time.time()
        self.alert('ATTACH', f'watching {self.sid} auto={self.auto} transcript={self.dir}/transcript.log')
        buf = ''
        while True:
            chunk = f.readline()
            if chunk:
                buf += chunk
                if buf.endswith('\n'):
                    try:
                        self.on(json.loads(buf))
                    except Exception:
                        pass
                    buf = ''
                continue
            self.tick()
            pid = json.load(open(f'{BUS}/{self.sid}/meta.json')).get('pid')
            if not alive(pid):
                self.alert('DETACH', f'hermes pid {pid} exited')
                return
            time.sleep(1)


def main():
    a = sys.argv[1:]
    cmd = a.pop(0) if a else 'list'
    flag = lambda f: f in a and not a.remove(f)
    if cmd == 'list':
        cmd_list(flag('--all'))
    elif cmd == 'attach':
        auto = not flag('--no-auto')
        if a:
            sid = resolve(a[0])
        else:
            live = cmd_list()
            if not live:
                sys.exit(1)
            pick = input('attach to # (Enter=1): ').strip() or '1'
            sid = live[int(pick) - 1]['sid']
        os.makedirs(OUT, exist_ok=True)
        open(CUR, 'w').write(sid)
        Watcher(sid, auto).run()
    elif cmd == 'say':
        mode = 'now' if flag('--now') else 'note'
        sid = current()
        if '--sid' in a:
            i = a.index('--sid')
            a.pop(i)
            sid = resolve(a.pop(i))
        try:
            pid = json.load(open(f'{BUS}/{sid}/meta.json')).get('pid')
        except Exception:
            pid = None
        if not alive(pid):
            sys.exit(f'session {sid} is not live (hermes pid {pid} exited) — nothing would deliver it')
        say(sid, ' '.join(a), mode)
        print(f'queued ({mode}) for {sid}')
    elif cmd == 'show':
        n = int(a[0]) if a else 40
        os.system(f'tail -n {n} "{OUT}/{current()}/transcript.log"')
    elif cmd == 'think':
        p = f'{BUS}/{current()}/stream_tail.txt'
        print(open(p).read() if os.path.exists(p) else '(nothing streamed yet)')
    else:
        sys.exit(__doc__)


if __name__ == '__main__':
    main()
