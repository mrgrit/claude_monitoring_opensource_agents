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
ctl.py             감독자 콘솔: status / tail / show / nudge / skip / abort / requeue / pause / resume / stop
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
