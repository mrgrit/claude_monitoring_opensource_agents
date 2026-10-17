# claude_monitoring_opensource_agents

작은 로컬 오픈모델 에이전트(Hermes Agent + Qwen 27B Q4 on ollama)를 **장시간 무인 작업**에 투입하고,
상위 감독자(Claude Code 또는 사람)가 **감시 → 자동 개입 → 에스컬레이션 → 수동 지도**를 체계적으로 하는 감독 프레임워크.

첫 적용 사례: `gh/` 아래 153개 보안 도구 저장소를 하나씩 설치·동작검증하고, 한국어 매뉴얼을 쓰고,
**검증된 것만** 모아 원샷 설치 스크립트를 생성 → [mrgrit/offensive_system](https://github.com/mrgrit/offensive_system)에 자동 push.

## 왜 필요한가

작은 모델은 다음과 같은 실패 패턴을 반복한다:

| 실패 패턴 | 감지 방법 (runner.py 워치독) | 자동 개입 |
|---|---|---|
| 끝없는 리즈닝 (행동 없이 생각만) | stream 이벤트 없이 `STALL_SEC`(420s) 경과, 실행 중 도구 없음 | 세션 kill → "한 문장으로 다음 행동 말하고 즉시 명령 1개 실행" + 현재 목표 주입 |
| 같은 명령 반복 루프 | 최근 10개 호출 중 동일 명령 3회 | "다른 방법 시도 / 불가하면 KIND=skip" |
| 실패 연쇄 | 연속 6회 non-zero exit | "근본 원인 한 문장 요약 후 더 단순한 경로" + 마지막 에러 |
| 걸린 명령 (대화형/무한대기) | 단일 도구 호출 `TOOL_HANG_SEC`(1500s) 초과 | kill → "`timeout 600` 사용, 대화형 금지" |
| 작업 범위 폭주 | 작업당 도구 호출 90회 | 정리 지시 + **ESCALATE** |
| 금지 행동 (sudo, apt, rc 파일 수정, 위험한 rm, git push, 외부 호스트 스캔) | 명령 실행 *전* stream에서 정규식 검사 | 즉시 kill + 규칙 설명 + **ESCALATE** |
| "다 했어요" 거짓 완료 | 에이전트 말을 믿지 않음 — `gate.py`가 깨끗한 셸에서 레시피로 **재설치+재검증** | 실패 사유를 그대로 같은 세션에 되먹임 (최대 3회) |

개입은 모두 **같은 hermes 세션을 이어서**(`--continue kit-<name>`) `SUPERVISOR:` 메시지로 주입되므로 에이전트는 이전 맥락을 유지한 채 교정된다.
자동 개입 4회 초과, 게이트 3회 실패, 라운드 8회 소진 시 작업은 `review`로 보류되고 다음 작업으로 넘어간다 (전체가 한 저장소에 막히지 않음).

## 구성

```
runner.py          큐 구동 + 실시간 워치독 + 자동 개입 + 게이트 + git publish
gate.py / gate.sh  독립 수용 게이트 (레시피 문법, 하드코딩 경로/sudo 금지, 깨끗한 셸 재설치, 실행 검증, 매뉴얼 형식·분량·한국어)
ctl.py             감독자 콘솔: status / tail / show / nudge / skip / abort / requeue / pause / resume / stop / drain
                   drain [WID]: 현재 작업을 끝낸 뒤 워커 종료 → runner.py 수정 후 무손실 재시작용 (재기동 전 control/drain 삭제)
assemble.py        PASS 레시피만 모아 install/install_all.sh (단일 자급식 원샷 스크립트) 생성
triage.py          저장소 사전 분류 (빌드 파일·언어 마커) → queue.json
PROMPT.md          작업 1건 단위 프롬프트 (작은 모델용: 단계 고정, 산출물 계약 명시)
recipe_template.sh 레시피 계약: NAME/KIND/REPO/SRC/NEEDS_SUDO_APT/SKIP_REASON + do_install()/do_verify()
kit_env.sh         레시피·게이트·원샷 스크립트 공통 환경
```

런타임 상태(커밋 안 됨): `queue.json`, `events.log`(타임라인), `alerts.log`(감독자 주의 필요), `logs/<task>/rN.jsonl`(원본 stream), `verdicts/`, `run/current.json`.

## 상위 감독자 (Claude Code) 루프

1. `alerts.log`/`events.log`를 실시간 tail (Claude Code `Monitor`) — `ESCALATE`/`gatefail`/`park` 이벤트에 깨어남
2. `ctl.py show <task>` 로 모델이 실제로 무엇을 했는지 확인
3. 판단: 진행 중이면 `ctl.py nudge "구체적 지시"`, 불가능하면 `ctl.py skip "사유"`, 보류된 작업은 `ctl.py requeue <task> "다음 시도 지침"`
4. 반복되는 실패 패턴은 워치독 규칙/PROMPT.md 자체를 고쳐 **체계적으로** 재발 방지

## 사용

```bash
python3 triage.py                       # queue.json 생성 (진행 상태 보존)
nohup python3 runner.py > run/runner.out 2>&1 &   # 전체 큐 (인자로 작업명 주면 그것만)
python3 ctl.py status
python3 ctl.py show sherlock 30
python3 ctl.py nudge "requirements.txt 대신 pyproject로 pip install . 하라"
python3 assemble.py                     # 원샷 스크립트 재생성
```

hermes 호출: `hermes chat --query-file … --format stream-json --yolo --max-turns 60 --run-budget 2700 --continue kit-<task> --create-if-missing`

비밀값(PAT 등)은 이 저장소와 에이전트 프롬프트에 절대 넣지 않는다. git push는 에이전트가 아니라 runner가 게이트 통과 후 수행한다.

## Attach 모드 — 이미 돌고 있는 hermes를 감시 (`watch.py`)

위의 runner 방식과 반대로, **사용자가 hermes에 직접 일을 시켜두고** 나중에 감시기를 붙이는 방식.
작업 내용(무엇을 시키는지)에는 관여하지 않고, **어떻게 일하는지**만 본다: 생각만 하고 행동 안 함, 같은 명령 반복,
실패 연쇄, 멈춘 명령, "도구가 없어서 못 했습니다" 식 회피, 근거 없는 "완료" 주장, 품질/할루시네이션(→ Claude Code가 판단).

구성:
```
hermes_plugin/cc_watch/   hermes 플러그인 (hermes 프로세스 안에서 동작)
                          - 이벤트 기록: ~/.hermes/cc_watch/<session_id>/events.jsonl (도구 호출/결과, 턴 결과, 스트림 heartbeat)
                          - 감독 메시지 전달: inbox/ 에 들어온 메시지를
                              작업 중 + note  → agent.steer()    (다음 도구 결과 뒤에 끼워 넣음, 작업 안 끊김)
                              작업 중 + now   → agent.redirect() (진행 중인 모델 생성만 취소, 맥락 유지하고 교정)
                              대기 중         → inject_message() (새 턴으로 시작)
watch.py                  감시기: 세션 선택, 이상 감지, 자동 nudge, 알림(attach_state/alerts.log), 메시지 전송
```

설치 (1회):
```bash
ln -sfn "$PWD/hermes_plugin/cc_watch" ~/.hermes/plugins/cc_watch
hermes plugins enable cc_watch
hermes config set plugins.stream_reasoning_deltas true                      # 리즈닝 감시용
hermes config set plugins.entries.cc_watch.allow_gateway_injection true     # TUI 모드에서 메시지 주입 허용
```
플러그인은 **설치 이후에 시작한 hermes**에만 로드된다. 이미 떠 있던 hermes는 종료 후 `hermes -c`로 같은 대화를 이어서 열면 된다.
세션은 첫 턴이 시작돼야 목록에 나타난다.

사용:
```bash
python3 watch.py list                       # 붙을 수 있는 hermes 세션 (pid, 작업 첫 줄)
python3 watch.py attach                     # 번호로 선택 후 감시 시작 (--no-auto: 자동 nudge 끄고 알림만)
python3 watch.py say "pyproject로 설치해라"     # 작업 끊지 않고 지시 (steer)
python3 watch.py say --now "멈춰. 방향 바꿔서 …"  # 진행 중 생성 취소하고 즉시 교정 (redirect)
python3 watch.py show 50                    # 읽기 쉬운 transcript
python3 watch.py think                      # 지금 모델이 스트리밍 중인 리즈닝/텍스트 끝부분
```

감지 → 대응:

| 감지 | 조건 (env로 조정) | 자동 대응 |
|---|---|---|
| 과도한 리즈닝 | 도구 없이 `WATCH_THINK_SEC`(300s) 경과, 리즈닝 > 본문 텍스트 | `--now` "한 문장으로 다음 행동 말하고 바로 실행" |
| 무진행 | 이벤트 없이 `WATCH_STALL_SEC`(420s) | `--now` 현재 위치 보고 후 다음 단계 |
| 반복 루프 | 최근 10개 중 같은 명령 3회 | note "다른 접근" |
| 실패 연쇄 | 연속 5회 실패 | note "근본 원인 한 문장 + 더 단순한 경로" |
| 걸린 명령 | 도구 1개가 `WATCH_HANG_SEC`(1500s) 실행 중 | `--now` timeout/비대화형으로 재실행 |
| 회피 ("설치 안 돼 있어서 못함") | 턴 결과 문장 패턴 | note "없으면 설치해서 끝까지 해라" |
| 근거 없는 완료 주장 | "완료/성공" + 도구 0회 또는 마지막 도구 실패 | 알림 `CLAIM` (Claude Code가 확인) |
| 매 턴 종료 | 항상 | 알림 `REVIEW` (Claude Code가 품질·할루시네이션 판단 후 `say`) |

같은 종류 nudge는 5분 쿨다운, 자동 nudge는 attach당 `WATCH_MAX_AUTO`(8)회까지이고 이후는 `ESCALATE` 알림만 남긴다.
`--now` nudge(진행 중 생성 취소)는 **턴당 1회**만 보내고 이후는 `ESCALATE` 알림으로 돌린다 — 반복 redirect는 hermes가 턴 자체를 포기하게 만든다.
모델이 도구 인자를 쓰는 중(예: 긴 `write_file` 본문)에는 스트림 이벤트가 없으므로, 플러그인이 `tool_gen` 이벤트를 남기고
감시기는 그동안 think/stall nudge를 하지 않는다(`WATCH_HANG_SEC` 넘으면 `SLOWGEN` 알림만).

Claude Code 쪽: `tail -F attach_state/alerts.log`를 Monitor로 걸어두고, `REVIEW`/`CLAIM`/`EXCUSE`/`ESCALATE`에 깨어나
`watch.py show`로 실제 행동을 확인한 뒤 필요하면 `watch.py say`로 교정/보완 지시.

### 운영 메모 (ollama 로컬 모델, 2026-10-18 실전 감시에서 확인)

- **도구 인자는 스트리밍되지 않는다.** 긴 `write_file` 본문을 생성하는 동안 hermes에는 청크가 하나도 안 온다.
  hermes는 900s 동안 청크가 없으면 `Stream stale`로 연결을 끊고 재시도하므로, 큰 문서를 한 번에 쓰면 15분 생성 후 아무것도 안 남는다.
  → 감지되면 "계획은 그대로, 첫 섹션으로 파일 생성 후 나머지는 섹션별로 append" 힌트 한 번이면 해결된다 (`say --now`, 어차피 잘릴 생성이므로 손실 없음).
- 같은 이유로 그 구간에는 `THINK`/`STALL`이 오탐으로 뜬다. 실제 생성 중인지는 `journalctl -u ollama`의 `n_decoded`로 확인.
- 자동 nudge(`auto=True`)는 이 오탐 때문에 진행 중인 생성을 끊을 수 있다. ollama 환경에서는 `--no-auto`로 붙이고 사람/Claude Code가 판단하는 편이 안전하다.
- 턴이 끝난 뒤 hermes가 자체 리뷰(`bg-review` 스레드)로 도구를 호출한다 — 사용자 작업이 아니므로 플러그인이 `bg` 표시로 구분한다.

감시 보조 스크립트:
```bash
tools/alerts_filter.sh            # alerts.log 스트림. ollama가 놀고 있을 때의 STALL/THINK(오탐)는 버림
tools/silentgen.sh <SID> [480]    # ollama는 생성 중인데 hermes 이벤트가 480s 없으면 1줄 출력 (900s 컷 전에 개입할 시점)
```
