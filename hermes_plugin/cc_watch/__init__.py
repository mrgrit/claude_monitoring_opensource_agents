"""cc_watch — attach bus between a live hermes session and an external supervisor.

Per session (keyed by hermes session_id) under ~/.hermes/cc_watch/<sid>/:
  meta.json      pid / cwd / argv of the hermes process that owns the session
  events.jsonl   session_start, tool_start, tool_end, stream (rate-limited), turn_end, delivered, ...
  stream_tail.txt last ~4KB of streamed text/reasoning (overwritten)
  inbox/*.json   {"text": ..., "mode": "note"|"now"} written by the supervisor (watch.py say)

Delivery (live agent found via the CLI/TUI host): while a turn runs, "note" → agent.steer() (arrives
after the current tool batch, no interruption) and "now" → agent.redirect() (cancels only the in-flight
model request, keeps the task and context); when idle → inject_message() starts a new turn. If neither
works the message rides on the next tool result (transform_tool_result) and is retried every NOTE_MAX_WAIT.
Observer only: never blocks or rewrites anything except appending supervisor notes.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

try:
    from hermes_constants import get_hermes_home
    BUS = Path(os.environ.get('CC_WATCH_DIR') or (get_hermes_home() / 'cc_watch'))
except Exception:  # pragma: no cover
    BUS = Path(os.environ.get('CC_WATCH_DIR') or os.path.expanduser('~/.hermes/cc_watch'))

NOTE_MAX_WAIT = float(os.environ.get('CC_WATCH_NOTE_MAX_WAIT', '90'))
STREAM_EMIT_SEC = 5.0
TAIL = 4000

_lock = threading.Lock()
_S: dict[str, dict] = {}
_ctx = None
_last_sid = ''


def _clip(v: Any, n: int) -> str:
    if not isinstance(v, str):
        try:
            v = json.dumps(v, ensure_ascii=False, default=str)
        except Exception:
            v = str(v)
    return v if len(v) <= n else v[:n] + f'…[+{len(v) - n}]'


def _sess(sid: str) -> dict | None:
    global _last_sid
    sid = sid or _last_sid
    if not sid:
        return None
    _last_sid = sid
    st = _S.get(sid)
    if st is None:
        d = BUS / sid
        (d / 'inbox').mkdir(parents=True, exist_ok=True)
        (d / 'meta.json').write_text(json.dumps({
            'session_id': sid, 'pid': os.getpid(), 'cwd': os.getcwd(), 'argv': sys.argv,
            'attached_at': time.time()}, ensure_ascii=False))
        st = _S[sid] = {'dir': d, 'running': False, 'pending': [], 'stream': {}, 'tail': '',
                        'stream_emit': 0.0}
    return st


def _emit(st: dict, typ: str, **kw) -> None:
    kw = {'ts': time.time(), 'type': typ, **kw}
    with open(st['dir'] / 'events.jsonl', 'a', encoding='utf-8') as f:
        f.write(json.dumps(kw, ensure_ascii=False, default=str) + '\n')


def _fmt(msgs: list[dict]) -> str:
    return '\n'.join(f"[SUPERVISOR] {m['text']}" for m in msgs)


def _live_agent(sid: str):
    """(agent, running) for the live agent owning *sid* in this process, or (None, None)."""
    try:
        cli = getattr(getattr(_ctx, '_manager', None), '_cli_ref', None)
        ag = getattr(cli, 'agent', None)
        if ag is not None and getattr(ag, 'session_id', None) == sid:
            return ag, bool(getattr(cli, '_agent_running', False))
    except Exception:
        pass
    mod = sys.modules.get('tui_gateway.server')
    try:
        for s in list(getattr(mod, '_sessions', {}).values()):
            ag = s.get('agent') if isinstance(s, dict) else None
            if ag is not None and sid in (getattr(ag, 'session_id', None), s.get('session_key')):
                return ag, bool(s.get('running'))
    except Exception:
        pass
    return None, None


def _deliver(sid: str, st: dict, msgs: list[dict], why: str) -> bool:
    """Call without holding _lock. note → steer (after the current tool batch, no interrupt);
    now → redirect (cancels only the in-flight model request, keeps the task); idle → new user turn."""
    text = _fmt(msgs)
    urgent = any(m.get('mode') == 'now' for m in msgs)
    ag, running = _live_agent(sid)
    via, ok = '', False
    try:
        if ag is not None and running:
            if urgent and hasattr(ag, 'redirect'):
                via, ok = 'redirect', bool(ag.redirect(text))
            if not ok and hasattr(ag, 'steer'):
                via, ok = 'steer', bool(ag.steer(text))
        if not ok and not running:
            via, ok = 'inject', bool(_ctx and _ctx.inject_message(text, session_key=sid))
    except Exception as e:  # noqa: BLE001
        _emit(st, 'inject_error', error=str(e))
    _emit(st, 'delivered' if ok else 'inject_failed', via=via or 'none', why=why,
          agent_found=ag is not None, running=running, texts=[m['text'] for m in msgs])
    return ok


def _requeue(st: dict, msgs: list[dict]) -> None:
    with _lock:
        for m in msgs:
            m['queued'] = time.time()
        st['pending'] = msgs + st['pending']


def _poll() -> None:
    while True:
        time.sleep(1.0)
        jobs = []  # delivered outside the lock (a host may start a turn inline)
        try:
            with _lock:
                for sid, st in list(_S.items()):
                    for p in sorted((st['dir'] / 'inbox').glob('*.json')):
                        try:
                            m = json.loads(p.read_text(encoding='utf-8'))
                        except Exception:
                            m = None
                        p.unlink(missing_ok=True)
                        if not m or not str(m.get('text', '')).strip():
                            continue
                        m['queued'] = time.time()
                        _emit(st, 'inbox', mode=m.get('mode', 'note'), text=m['text'])
                        jobs.append((sid, st, [m], m.get('mode', 'note')))
                    if st['pending'] and time.time() - st['pending'][0]['queued'] > NOTE_MAX_WAIT:
                        jobs.append((sid, st, st['pending'], 'retry'))
                        st['pending'] = []
        except Exception:
            pass
        for sid, st, msgs, why in jobs:
            if not _deliver(sid, st, msgs, why):
                _requeue(st, msgs)  # fallback: rides on the next tool result, retried every NOTE_MAX_WAIT


# ---- hooks -------------------------------------------------------------------------------

def _on_session_start(*, session_id: str = '', model: str = '', platform: str = '', **_: Any) -> None:
    with _lock:
        st = _sess(session_id)
        if st:
            _emit(st, 'session_start', model=model, platform=platform)


def _on_session_end(*, session_id: str = '', **kw: Any) -> None:
    with _lock:
        st = _sess(session_id)
        if st:
            st['running'] = False
            _emit(st, 'session_end', reason=kw.get('reason', ''))


def _on_pre_tool_call(*, tool_name: str = '', args: Any = None, session_id: str = '', **_: Any) -> None:
    with _lock:
        st = _sess(session_id)
        if st:
            st['running'] = True
            _emit(st, 'tool_start', tool=tool_name, args=_clip(args, 3000))
    return None


def _on_post_tool_call(*, tool_name: str = '', result: Any = None, session_id: str = '', **kw: Any) -> None:
    status = kw.get('status') or 'ok'
    try:
        r = json.loads(result) if isinstance(result, str) else result
        if isinstance(r, dict):
            if r.get('error'):
                status = 'error'
            elif isinstance(r.get('exit_code'), int) and r['exit_code'] != 0:
                status = f"exit {r['exit_code']}"
    except Exception:
        pass
    with _lock:
        st = _sess(session_id)
        if st:
            _emit(st, 'tool_end', tool=tool_name, status=status, duration_ms=kw.get('duration_ms'),
                  result=_clip(result, 2500))


def _on_transform_tool_result(*, result: Any = None, session_id: str = '', **_: Any):
    with _lock:
        st = _sess(session_id)
        if not st or not st['pending'] or not isinstance(result, str):
            return None
        msgs, st['pending'] = st['pending'], []
        _emit(st, 'delivered', via='tool_result', texts=[m['text'] for m in msgs])
    return result + '\n\n' + _fmt(msgs)


def _on_post_llm_call(*, session_id: str = '', user_message: Any = None, assistant_response: Any = None,
                      **_: Any) -> None:
    with _lock:
        st = _sess(session_id)
        if not st:
            return
        st['running'] = False
        _emit(st, 'turn_end', user=_clip(user_message, 3000), assistant=_clip(assistant_response, 12000))
        for m in st['pending']:  # undelivered fallbacks: retry a few seconds after the turn settles
            m['queued'] = time.time() - NOTE_MAX_WAIT + 3


def _on_stream_delta(*, delta: str = '', kind: str = 'text', session_id: str = '', **_: Any) -> None:
    with _lock:
        st = _sess(session_id)
        if not st or not isinstance(delta, str):
            return
        st['running'] = True
        st['stream'][kind] = st['stream'].get(kind, 0) + len(delta)
        st['tail'] = (st['tail'] + delta)[-TAIL:]
        now = time.time()
        if now - st['stream_emit'] >= STREAM_EMIT_SEC:
            st['stream_emit'] = now
            _emit(st, 'stream', chars=dict(st['stream']))
            st['stream'] = {}
            try:
                (st['dir'] / 'stream_tail.txt').write_text(st['tail'], encoding='utf-8')
            except Exception:
                pass


def register(ctx) -> None:
    global _ctx
    _ctx = ctx
    for name, fn in (('on_session_start', _on_session_start), ('on_session_end', _on_session_end),
                     ('pre_tool_call', _on_pre_tool_call), ('post_tool_call', _on_post_tool_call),
                     ('transform_tool_result', _on_transform_tool_result),
                     ('post_llm_call', _on_post_llm_call), ('on_stream_delta', _on_stream_delta)):
        ctx.register_hook(name, fn)
    threading.Thread(target=_poll, name='cc_watch-inbox', daemon=True).start()
