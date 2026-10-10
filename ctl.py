#!/usr/bin/env python3
"""Supervisor control (used by the human or by Claude while monitoring).
  ctl.py status                     queue summary + what is running now
  ctl.py tail [N]                   last N timeline events
  ctl.py show <name> [N]            last N stream events of a task (what the model is actually doing)
  ctl.py nudge "<guidance>"         kill current round, resume same hermes session with this guidance
  ctl.py skip  "<reason>"           mark current task skip, move on
  ctl.py abort "<reason>"           park current task as review, move on
  ctl.py requeue <name> "<guidance>"  put a task back to pending with guidance injected into its prompt
  ctl.py pause | resume | stop      pause after current task / continue / stop runner now
"""
import collections, json, os, sys, time

SUP = '/home/thor-01/offensive/supervisor'
CTL = f'{SUP}/control'


def put(cmd, payload='', target='*'):
    os.makedirs(CTL, exist_ok=True)
    open(f'{CTL}/{cmd}.{target}.{int(time.time()*1000)}', 'w').write(payload)


def main():
    a = sys.argv[1:] or ['status']
    c = a[0]
    q = json.load(open(f'{SUP}/queue.json'))
    if c == 'status':
        import glob
        cnt = collections.Counter(t['status'] for t in q)
        print('queue:', dict(cnt), f'total={len(q)}')
        for cur in sorted(glob.glob(f'{SUP}/run/current.*.json')):
            try:
                d = json.load(open(cur))
                print(f"  w{d.get('worker')}: {d.get('task')} r{d.get('round')} tools={d.get('tools')} "
                      f"nudges={d.get('nudges')} idle={d.get('idle_s')}s "
                      f"{'TOOL:'+d['pending_tool'] if d.get('pending_tool') else ''}")
            except Exception:
                pass
        for s in ('review', 'needs_sudo'):
            xs = [f"{t['name']}({t.get('note','')[:60]})" for t in q if t['status'] == s]
            if xs:
                print(f'{s}:', '; '.join(xs))
        print('paused' if os.path.exists(f'{CTL}/pause') else '')
    elif c == 'tail':
        n = int(a[1]) if len(a) > 1 else 30
        print(''.join(open(f'{SUP}/events.log').readlines()[-n:]), end='')
    elif c == 'show':
        name, n = a[1], int(a[2]) if len(a) > 2 else 25
        d = f'{SUP}/logs/{name}'
        fs = sorted((f for f in os.listdir(d) if f.endswith('.jsonl')), key=lambda f: int(f[1:].split('.')[0]))
        evs = []
        for f in fs:
            for l in open(f'{d}/{f}'):
                try:
                    evs.append((f, json.loads(l)))
                except Exception:
                    pass
        text = ''
        for f, e in evs[-n * 4:]:
            t = e.get('type')
            if t == 'text':
                text += e.get('text', '')
                continue
            if text:
                print(f'{f} text: {text.strip()[:400]}')
                text = ''
            if t == 'tool_use':
                print(f'{f} >> {e.get("name")}: {json.dumps(e.get("input"), ensure_ascii=False)[:300]}')
            elif t == 'tool_result':
                print(f'{f} << {"ERR " if e.get("is_error") else ""}{str(e.get("output"))[:300]}')
            elif t == 'result':
                print(f'{f} == result: {str(e.get("text"))[:300]}')
        if text:
            print('text:', text.strip()[:400])
    elif c in ('nudge', 'skip', 'abort'):
        put(c, ' '.join(a[1:]))
        print(f'{c} queued')
    elif c == 'requeue':
        for t in q:
            if t['name'] == a[1]:
                t['status'] = 'pending'
                if len(a) > 2:
                    t['guidance'] = ' '.join(a[2:])
        json.dump(q, open(f'{SUP}/queue.json', 'w'), indent=1, ensure_ascii=False)
        print('requeued', a[1])
    elif c == 'pause':
        open(f'{CTL}/pause', 'w').write('')
    elif c == 'resume':
        if os.path.exists(f'{CTL}/pause'):
            os.remove(f'{CTL}/pause')
    elif c == 'stop':
        open(f'{CTL}/stop', 'w').write('')
    else:
        print(__doc__)


if __name__ == '__main__':
    main()
