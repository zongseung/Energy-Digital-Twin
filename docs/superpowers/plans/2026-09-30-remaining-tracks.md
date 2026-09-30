# Remaining G3 and Task 3/6 Tracks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:dispatching-parallel-agents (user-selected). Each track is one agent in the shared working tree; steps use checkbox (`- [ ]`) syntax.

**Goal:** 웹 뷰어의 과거/최신/시나리오 시간축, Omniverse Kit WS 라이브 연결, 1920×1080 FPS 측정, WS 느린 소비자·정정·중복 검사를 병렬로 완료한다.

**Architecture:** 네 트랙은 서로 다른 파일만 수정한다. Rust API 계약은 그대로 두고 웹(T1)과 Kit(T2)가 기존 `/state`·`/timeline`·`/simulate`·`/ws`를 소비한다. T3은 Git 밖 검증 스크립트이고, T4는 브릿지 WS fanout의 중복 전송 한 곳과 검사를 다룬다.

**Tech Stack:** 브라우저 ES module + Three.js(기존), Node 22 `node:assert`, Omniverse Kit 106.5 Python 3.10 + 내장 `websockets` 12.0, omowright CDP 드라이버 + Playwright Chromium 1234, Rust tokio/axum/tokio-tungstenite(기존).

**Spec:** `docs/superpowers/specs/2026-09-30-remaining-tracks-design.md`

## Global Constraints

- 작업 위치는 `/home/user/Energy-Digital-Twin`의 `feat/photo-scene` 작업 트리다. worktree를 만들지 않는다. **자기 트랙의 소유 파일만 수정한다.**
- 새 의존성 금지. 새 파일은 아래 표에 적힌 것만 만든다.
- `.env`의 값을 로그·문서·브라우저 산출물·커밋에 넣지 않는다. 운영 DB·브릿지·원천 서비스는 읽기만 한다.
- 좌표·장면 GLB·시설 ID·API 계약을 바꾸지 않는다. 없는 값을 0이나 보간값으로 채우지 않는다. 관측, 시나리오(시뮬레이션), 추정을 화면과 기록에서 구분한다.
- 커밋: 검증을 통과한 뒤 **자기 소유 파일만** `git add <paths>`로 stage하고 한 번 커밋한다. 메시지 끝에 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`를 붙인다. push는 하지 않는다. 다른 트랙이 동시에 커밋할 수 있으므로 `git add -A`나 `git commit -a`는 금지한다.
- 증거는 `var/` 아래(Git 밖)에 둔다. 소유 문서에는 실행 방법, 검증 결과, 확인하지 못한 한계만 짧게 적는다.

| 트랙 | 소유 파일 |
|---|---|
| T1 | `renderers/twin/app.js`, `renderers/twin/index.html`, `renderers/twin/style.css`, new `renderers/twin/playback.mjs`, new `tests/playback.mjs`, `docs/estimated-twin.md`, `var/verification/playback/` |
| T2 | `renderers/omniverse/twin.py`, `docs/omniverse-twin.md`, `var/rendering/omniverse/live-*/` |
| T3 | `var/verification/fps/` only |
| T4 | `src/bridge/stream.rs`, `src/bridge/protocol.rs` (derive만), `src/bridge/tests/websocket.rs`, `docs/bridge-client.md` |

## Review Focus

1. 과거 모드에서 A 선택 뒤 B 선택, A 응답이 늦게 도착 → 화면은 B의 시각과 값이어야 한다 (T1 브라우저 검사 + `tests/playback.mjs`).
2. 같은 순간을 다른 offset으로 표현한 요청/응답 시각(`09:00+09:00` vs `00:00Z`) → 같은 시각으로 인정해야 한다 (`tests/playback.mjs`).
3. 과거·시나리오 모드 중 WS snapshot 도착 → 화면을 덮지 않고, 최신 모드로 돌아오면 마지막 live 값이 보여야 한다 (T1 브라우저 검사).
4. `sent_at`만 다른 동일 프레임 재발행 → 클라이언트에 새 프레임이 가면 안 된다. 브릿지 재시작으로 버전이 1이 되어도 실제 정정은 전달돼야 한다 (T4 테스트).
5. Kit 라이브 중 API/WS 단절 → 마지막 관측 시각을 유지하고 `disconnected`로 표시해야 하며, 새 관측으로 위장하면 안 된다 (T2 검증).

---

### Task T1: 웹 시간축·과거·시나리오

**Files:** Create `renderers/twin/playback.mjs`, `tests/playback.mjs`. Modify `renderers/twin/app.js`(수급 패널 부분만: `showState`/`refreshState`/`connectState` 주변), `renderers/twin/index.html`(`<footer class="state-panel">` 안), `renderers/twin/style.css`, `docs/estimated-twin.md`.

**Interfaces:**
- Produces: `shouldApply(mode: 'latest'|'history'|'scenario', requestedAt: string|null, responseAt: string, isLive: boolean, requestedSeq: number, responseSeq: number) -> boolean`, `kstDay(date: 'YYYY-MM-DD') -> {start: string, end: string} | null`
- Consumes: `GET /api/v1/jeju/timeline?start&end` → `string[]`(UTC, 오름차순, 최대 2016); `GET /api/v1/jeju/state?at=` → Snapshot(404=없음); `POST /api/v1/jeju/simulate` body `{run_id,start,end,scales:{demand,wind,solar},dispatch?,ess?}` → `{status, missing_intervals, points:[{observed_at, baseline:{net_load_mw,residual_before_ess_mw,charge_mw,discharge_mw,energy_mwh,soc_percent,residual_after_ess_mw}, scenario:{…}}], final_baseline_mwh, final_scenario_mwh, model_version, input}`; 429 = 다른 실행 진행 중. `dispatch` 항목은 5분 시각마다 `{observed_at, nonrenewable_mw, hvdc:[{id,power_mw,available,min_mw,max_mw}×3]}`이고, `ess`는 `{capacity_mwh,charge_limit_mw,discharge_limit_mw,charge_efficiency,discharge_efficiency,initial_mwh,min_mwh,max_mwh}`다. 규칙은 `docs/simulation.md`에 있다.

- [x] **Step 1: 실패하는 검사 작성** — `tests/playback.mjs`:

```js
import assert from 'node:assert/strict';
import {shouldApply, kstDay} from '../renderers/twin/playback.mjs';

const A = '2026-09-28T00:00:00Z', A_KST = '2026-09-28T09:00:00+09:00', B = '2026-09-28T00:05:00Z';
assert.equal(shouldApply('latest', null, A, true, 0, 0), true);
assert.equal(shouldApply('latest', A, A, false, 1, 1), false); // late history reply after returning to latest
assert.equal(shouldApply('history', A, A_KST, false, 3, 3), true); // same instant, other offset
assert.equal(shouldApply('history', B, A, false, 3, 3), false); // A arrives after B was chosen
assert.equal(shouldApply('history', A, A, false, 4, 3), false); // stale request for the same time
assert.equal(shouldApply('history', A, A, true, 3, 3), false); // live WS during history
assert.equal(shouldApply('history', 'bad', 'bad', false, 3, 3), false);
assert.equal(shouldApply('scenario', A, A, true, 3, 3), false);
assert.equal(shouldApply('scenario', A, A, false, 3, 3), false);
assert.deepEqual(kstDay('2026-09-30'), {start:'2026-09-30T00:00:00+09:00', end:'2026-10-01T00:00:00+09:00'});
assert.deepEqual(kstDay('2026-12-31'), {start:'2026-12-31T00:00:00+09:00', end:'2027-01-01T00:00:00+09:00'});
assert.equal(kstDay('2026-02-30'), null);
assert.equal(kstDay(''), null);
console.log('playback: PASS');
```

- [x] **Step 2: 실패 확인** — `node tests/playback.mjs` → `ERR_MODULE_NOT_FOUND`.
- [x] **Step 3: 최소 구현** — `renderers/twin/playback.mjs`:

```js
// Pure guards for the state panel's time modes. No DOM.
export function shouldApply(mode, requestedAt, responseAt, isLive, requestedSeq, responseSeq) {
  if (mode === 'latest') return isLive;
  if (mode === 'history') return !isLive && requestedSeq === responseSeq && Date.parse(requestedAt) === Date.parse(responseAt);
  return false; // scenario: observations never overwrite a simulation view
}
export function kstDay(date) {
  const day = /^\d{4}-\d{2}-\d{2}$/.test(date) ? new Date(`${date}T00:00:00Z`) : null;
  if (!day || Number.isNaN(day.getTime()) || day.toISOString().slice(0, 10) !== date) return null;
  day.setUTCDate(day.getUTCDate() + 1);
  return {start:`${date}T00:00:00+09:00`, end:`${day.toISOString().slice(0, 10)}T00:00:00+09:00`};
}
```

- [x] **Step 4: 통과 확인** — `node tests/playback.mjs` → `playback: PASS`. `node tests/wind-estimate.mjs`도 계속 통과해야 한다.
- [x] **Step 5: 패널 UI** — `index.html`의 `.state-heading` 아래에 다음을 추가한다.
  - 모드 라디오 `name="mode"`(`latest` 기본, `history`, `scenario`).
  - `<input id="history-date" type="date">`와 `<input id="history-time" type="range">`, 시각 `<output id="history-label">`, `<button id="play">`.
  - 시나리오 `<form id="scenario-form" hidden>`:
    - 배율 `number` 3개(기본 1.10/0.80/1.00, min 0, step 0.01).
    - 선택 `<fieldset>` "가정 공급·ESS"(체크 시 활성): G MW, HVDC#1/#2/#3 MW, `#3 가용` 체크, ESS MWh/MW/초기 SOC %.
    - 실행 버튼.
  - `<dl id="scenario-metrics" hidden>`: 순부하 기준→변경, ESS 전 잔차, ESS 충/방전, SOC, ESS 후 잔차.
  - 기존 id(`demand_mw` 등)와 `#refresh`는 유지한다. 모바일 375px에서 줄바꿈하고 가로 넘침이 없어야 한다(`style.css`).
- [x] **Step 6: 모드 연결** — `app.js`에서 수급 패널 코드만 바꾼다.
  - 상태: `let mode = 'latest', seq = 0, times = [], lastLive = null, selectedAt = null, playTimer, scenario = null;`.
  - 날짜 기본값은 KST 전날이다.
  - **WS·최신 HTTP 수신:** 항상 `lastLive = data`로 보관한다. `shouldApply(mode, null, data.observed_at, true, 0, 0)`일 때만 `showState(data)`를 부른다.
  - **history 진입/날짜 변경**
    - `seq += 1`로 요청 번호를 올린다.
    - `kstDay`로 구간을 만들어 `/timeline`을 불러오고, 응답의 seq가 현재 seq와 다르면 버린다.
    - range의 `max = times.length - 1`로 두고 마지막 시각을 선택한다. 시각이 0개면 `자료 없음`이다.
    - 누락 수는 `288 - times.length`로 표시한다. 오늘 날짜는 "진행 중"으로 표시한다.
  - **range 변경:** `selectedAt = times[i]`, `const mine = ++seq`로 `/state?at=`를 요청한다. 응답은 `shouldApply(mode, selectedAt, data.observed_at, false, seq, mine)`일 때만 `showState`로 반영한다. 404는 `해당 시각 관측 없음`을 표시하고 값은 `—`로 둔다.
  - **재생:** 1초 간격 `setInterval`로 index를 +1 하고 끝에서 멈춘다. 모드를 바꾸거나 탭이 숨겨지면 멈춘다.
  - **scenario 실행**
    - 불러온 날짜의 `times`가 필요하다. 없으면 먼저 timeline을 불러온다.
    - 계산 범위는 `start = times[0]`, `end = last + 5분`이다.
    - dispatch를 체크하면 `times[0]`부터 `end` 전까지 **5분 격자의 모든 시각**에 같은 가정을 넣는다(`min_mw = min(0, v)`, `max_mw = max(0, v)`, #3 미가용이면 `power_mw = 0`). HVDC id는 `hvdc-1..3`이다.
    - ESS 가정: `min_mwh = 0`, `max_mwh = capacity`, 효율 0.9/0.9, `initial = capacity × SOC/100`이다. 효율은 화면에 "가정 효율 0.9"로 표시한다.
    - 결과 `r`은 `const mine = ++seq`와 현재 seq가 같고 `mode === 'scenario'`일 때만 반영한다.
    - `status`를 표시한다. `incomplete`면 `missing_intervals` 개수와 앞 3개 KST 시각만 보여 준다.
    - points가 있으면 range를 points 개수에 맞추고, 선택 point의 값을 `scenario-metrics`에 표시한다. null은 `—`다.
    - 429는 "다른 계산 진행 중 · 잠시 후 재시도"로 표시한다.
  - **최신 복귀:** `seq += 1`, 재생 정지, `lastLive`가 있으면 `showState(lastLive)`, 그다음 `refreshState()`를 부른다.
  - **표시 라벨:** 모드마다 제목 옆 라벨을 `실제 관측 · 최신` / `실제 관측 · 과거 KST 시각` / `시뮬레이션 · 실측 아님`으로 바꾼다.
- [x] **Step 7: 브라우저 검증** — `var/verification/playback/qa.mjs`는 `var/verification/local/browser-qa.mjs`의 omowright 실행부를 복사해 쓴다. `http://127.0.0.1:8080/`을 375×812와 1280×800에서 연다.
  - (a) 과거 모드에서 전날 timeline이 로드되고, 시각 두 개를 차례로 선택하면 값·라벨이 각각 원천 `/state?at=`와 같다.
  - (b) CDP `Fetch.enable` 패턴 `*state?at=*`에서 첫 요청(A)만 3초 늦게 `Fetch.continueRequest`하고, 곧바로 B를 선택한다. 최종 라벨과 값이 B여야 한다.
  - (c) 과거 모드에서 WS 프레임이 와도 표시가 그대로다. `window.WebSocket` 인스턴스에 `onmessage`로 가짜 snapshot을 dispatch한다. 최신으로 돌아오면 live 값이 복원된다.
  - (d) 시나리오를 실제 API로 실행한다. status 표시, 입력 비움 → `net_load_only`, 가정 체크 → SOC 표시.
  - (e) `scrollWidth <= innerWidth`, 페이지 오류 0, 스크린샷을 저장한다.
  - 결과는 `var/verification/playback/qa.json`이다.
- [x] **Step 8: 문서·커밋** — `docs/estimated-twin.md`에 모드 사용법, 시나리오 가정 입력의 의미, 검증 결과, 한계(개별 시설 값 없음, dispatch/ESS는 사용자 가정)를 짧게 추가한다. `git add renderers/twin/app.js renderers/twin/index.html renderers/twin/style.css renderers/twin/playback.mjs tests/playback.mjs docs/estimated-twin.md` → `feat: add history and scenario time modes to twin viewer`.

### Task T2: Omniverse Kit 라이브

**Files:** Modify `renderers/omniverse/twin.py`, `docs/omniverse-twin.md`.

**Interfaces:**
- Consumes: `ws://127.0.0.1:8090/api/v1/jeju/ws` LiveEnvelope `{type:'snapshot'|'status', observed_at, sent_at, state_version, quality_flags, data: Snapshot|null}`, `GET /api/v1/jeju/state[?at=]`, `GET /api/v1/jeju/timeline?start&end`.
- Produces: CLI `--live SECONDS`, `--replay T1,T2`, `--select FACILITY_ID`. evidence.json gets `applied: [{mode, state_version, observed_at, status, capture}]`, `selected_facility`.

- [x] **Step 1: 공통 적용 함수로 분리** — 현재 `urlopen` 블록(twin.py:178-193)의 속성 쓰기를 `apply_state(observation, state, status, version=None)`로 옮긴다. `state`가 None이거나 빈 dict이면 기존 값을 유지하고 status만 기록한다. 이 함수는 `snapshotJson`/`source`/`qualityFlags`/`observedAt`/MW 속성을 쓰고, 유한하지 않은 값은 속성을 제거하거나 쓰지 않는다. 추가로 `snapshotStatus`(`live`/`history_replay`/`disconnected`/`unavailable`)와 `stateVersion`(Int64)을 기록한다. `status='disconnected'`이면 기존 `observedAt`과 값을 **유지**하고 상태만 바꾼다. 인자가 없는 기본 실행 결과(evidence 키, 캡처)는 이전과 같아야 한다.
- [x] **Step 2: `--replay`** — 두 시각을 `/state?at=`(URL 인코딩)로 순서대로 조회한다. 각 결과를 `apply_state(..., 'history_replay')` 후 캡처하고, 적용한 USD 속성을 다시 읽어 API JSON 값과 같은지 assert한다.
- [x] **Step 3: `--live SECONDS`** — Kit 이벤트 루프 안에서 다음 코루틴을 돌린다.

```python
async def follow(observation, seconds, on_new_version):
    import websockets  # bundled by omni.kit.pip_archive 12.0
    deadline, applied, last = time.monotonic() + seconds, [], None
    while time.monotonic() < deadline:
        try:
            async with websockets.connect(os.environ.get('EDT_WS_URL', 'ws://127.0.0.1:8090/api/v1/jeju/ws'), open_timeout=10, max_size=64 * 1024) as ws:
                while True:
                    left = deadline - time.monotonic()
                    if left <= 0:
                        return applied
                    envelope = json.loads(await asyncio.wait_for(ws.recv(), timeout=left))
                    status = 'live' if envelope.get('type') == 'snapshot' else 'status'
                    apply_state(observation, envelope.get('data') or {}, status, envelope.get('state_version'))
                    key = (envelope.get('state_version'), envelope.get('observed_at'))
                    applied.append({'status': status, 'state_version': key[0], 'observed_at': key[1],
                                    'capture': await on_new_version() if key != last and status == 'live' else None})
                    last = key
        except (asyncio.TimeoutError, OSError, websockets.ConnectionClosed, json.JSONDecodeError):
            apply_state(observation, None, 'disconnected')
            applied.append({'status': 'disconnected', 'at': time.time()})
            await asyncio.sleep(5)
    return applied
```

  `main()`의 240초 제한은 `240 + live seconds`로 늘린다. 캡처는 기존 캡처 함수를 재사용한다(현재 카메라 하나, 1600×1000, 파일명에 순번). 모듈을 import하지 못하면 명확한 오류로 종료한다.
- [x] **Step 4: `--select`** — `facilityId` 속성이 같은 root prim을 **정확히 하나** 찾는다(없거나 여럿이면 ValueError). `omni.usd.get_context().get_selection().set_selected_prim_paths([path], True)`로 선택하고, evidence에 `{facility_id, prim_path, individual_actual_output: null, status: 'unavailable_null'}`를 기록한다.
- [x] **Step 5: GPU1 실행 검증** — `docs/omniverse-twin.md`의 기존 명령에 옵션만 더해 실행한다. 출력은 새 디렉터리 `var/rendering/omniverse/live-01/`이다. 조건: `OMNI_KIT_ACCEPT_EULA=yes`, `--gpu 1`, `timeout 600`.
  - (a) `--replay`: `/timeline`으로 얻은 실제 과거 두 시각의 속성이 API 값과 같다.
  - (b) `--live 420 --select hub:power_plant:5722`: 초기 snapshot을 1회 이상 적용한다. 5분 관측 주기상 새 버전이 없으면 없다고 기록한다.
  - (c) 단절: 브릿지·API를 멈추지 않는다. 대신 잘못된 포트로 접속하는 짧은 실행(URL을 환경변수 `EDT_WS_URL`로 덮을 수 있게 한다)으로 `disconnected` 기록과 기존 observedAt 유지를 확인한다.
  - T3가 같은 시간에 GPU를 쓸 수 있으니 `nvidia-smi` 샘플을 evidence에 남긴다.
- [x] **Step 6: 문서·커밋** — `docs/omniverse-twin.md`에 세 옵션, 검증 결과, 한계(PNG 속 HUD 없음, 화면 스트리밍 아님)를 적는다. `git add renderers/omniverse/twin.py docs/omniverse-twin.md` → `feat: follow live API state in Omniverse twin`.

### Task T3: 1920×1080 FPS 측정

**Files:** Create `var/verification/fps/fps.mjs`, outputs `var/verification/fps/result-*.json`. 저장소 코드는 수정하지 않는다. 커밋하지 않는다.

**Interfaces:** Produces `result-<gl>.json`: `{url, viewport:[1920,1080], duration_s, webgl_renderer, chromium, flags, gpu_samples, frames, interval_ms:{p50,p95,p99,max}, fps:{mean,p5}, target:{p5_min:30}, pass, concurrent_gpu_load_note}`.

- [x] **Step 1: 스크립트** — omowright 실행부는 `var/verification/local/browser-qa.mjs` 1-30행을 따른다. `Emulation.setDeviceMetricsOverride {width:1920,height:1080,deviceScaleFactor:1,mobile:false}` 후 `http://127.0.0.1:8080/`을 연다.
  - `document.body.dataset.ready === 'true'`와 페이지 오류 0을 기다린다. 실패하면 30초 뒤 최대 3회 재시도한다(T1이 같은 파일을 편집 중일 수 있음).
  - 측정 대상은 기본 시점이다.
  - 페이지에 `requestAnimationFrame` 기록기를 주입해 `performance.now()` 간격을 모은다.
  - 60초 동안 CDP `Input.dispatchMouseEvent`로 캔버스 중앙에서 좌클릭 드래그(원형 궤적, 16ms마다 mouseMoved)를 계속해 OrbitControls를 조작한다.
  - `p5 FPS = 1000 / p95 interval`로 계산한다.
  - WebGL renderer는 별도 canvas의 `WEBGL_debug_renderer_info`에서 읽는다.
- [x] **Step 2: 두 조건 측정** — (1) 기존 플래그 `--headless --no-sandbox --enable-unsafe-swiftshader`. (2) GPU 시도 `--headless=new --no-sandbox --ignore-gpu-blocklist --enable-gpu --use-angle=vulkan --enable-features=Vulkan`. renderer 문자열이 SwiftShader가 아니면 GPU 경로로 인정하고, 안 되면 `--use-gl=angle --use-angle=gl-egl`도 시도한다. 어느 조건이 실제 GPU였는지 renderer 문자열로 판정하고, 모두 실패하면 실패로 기록한다. 측정 중 `nvidia-smi --query-gpu=index,utilization.gpu,memory.used --format=csv -l 1` 샘플을 저장한다.
- [x] **Step 3: 보고** — 결과 JSON 두 개와, 목표 p5≥30 통과 여부·한계(서버 headless 수치이며 사용자 기기 수치가 아님, 동시 GPU 부하)를 담은 5줄 요약을 반환한다. 문서는 통합 단계에서 갱신하므로 여기서 쓰지 않는다.

### Task T4: WS 느린 소비자·정정 경쟁·중복 전송

**Files:** Modify `src/bridge/stream.rs`(`receive`의 `send_replace` 한 곳), `src/bridge/protocol.rs`(필요하면 `Snapshot`/`Envelope`에 `PartialEq` derive만), `src/bridge/tests/websocket.rs`, `docs/bridge-client.md`.

**Interfaces:** 외부 계약은 바꾸지 않는다. 동작 변경은 **내용이 같은 upstream 프레임을 클라이언트에 다시 보내지 않는 것** 하나다. 내용은 `type`·`schema_version`·`observed_at`·`state_version`·`source`·`quality_flags`·`data`이고 `sent_at`은 제외한다.

- [x] **Step 1: 실패하는 중복 검사** — `websocket.rs`의 기존 `envelope()`·`next()`·`version()`과 상류 `watch` 패턴을 재사용한다. 새 테스트 `ws_does_not_resend_identical_upstream_state`:
  - 상류가 v9를 보낸 뒤 `sent_at`만 바꾼 동일 v9를 보내고, 이어서 v10을 보낸다.
  - 클라이언트가 받은 텍스트 프레임은 v9 → v10 순서로 정확히 두 개여야 한다(사이에 v9 재수신 없음). 판정은 `version()`이 아니라 `next()`를 연속 호출해 받은 `state_version` 순서로 한다.
  - 이어서 상류가 재시작 상황을 흉내 내 v1(demand 101)을 보내면, 버전 번호가 작아도 전달돼야 한다.
- [x] **Step 2: 실패 확인** — `cargo test --locked ws_does_not_resend` → 중복 v9 때문에 FAIL.
- [x] **Step 3: 최소 구현** — `receive`에서 `send_replace`를 아래 코드로 바꾼다. `cache_epoch` 증가도 변경됐을 때만 한다.

```rust
let changed = bridge.live.send_if_modified(|current| {
    let mut same = current.clone();
    same.sent_at = envelope.sent_at;
    if same == envelope { return false; }
    *current = envelope;
    true
});
if changed { bridge.cache_epoch.fetch_add(1, std::sync::atomic::Ordering::SeqCst); }
```

  `Envelope`와 `Snapshot`에 `PartialEq`를 derive한다(검증을 거친 값이라 NaN이 없음). `refresh()`가 붙이는 지연 플래그가 바뀌면 다른 내용이므로 전송된다.
- [x] **Step 4: 정정 경쟁 검사** — `ws_initial_snapshot_never_hides_immediate_correction`: 30회 반복한다. 매번 새 클라이언트를 연결하고, **연결 직후(초기 snapshot 수신 전)** 상류에 `envelope(100 + i, …)`를 보낸 뒤, `version(&mut client, 100 + i)`가 6초 안에 도착하는지 확인한다.
- [x] **Step 5: 느린 소비자 검사** — `ws_slow_subscriber_is_dropped_without_blocking_others`:
  - 한 클라이언트는 연결 후 읽지 않는다. `tokio::net::TcpSocket`의 `set_recv_buffer_size(4096)` 후 tungstenite `client_async`로 업그레이드한다.
  - 상류는 `data.source`를 약 8KiB 문자열로 채운 envelope를 버전을 올려 가며 계속 보낸다. 상류 메시지 상한 64KiB, 클라이언트 16KiB는 수신 프레임에만 적용된다.
  - 합격 조건: (a) 동시에 연결한 빠른 클라이언트가 마지막 버전을 받는다. (b) 20초 안에 느린 세션이 정리되어 `bridge.ws_slots.available_permits()`가 빠른 클라이언트 몫만 빼고 복구된다. (c) 서버에 클라이언트별 큐가 없음을 확인한다(watch 기반, 코드 확인으로 충분).
  - 5회 반복해 흔들리지 않는지 확인한다. 흔들리면 버퍼·크기를 조정하고, 끝내 불안정하면 테스트를 넣지 말고 이유를 보고한다.
- [x] **Step 6: 전체 검증** — `cargo fmt --all -- --check`, `cargo clippy --all-targets --locked -- -D warnings`, `cargo test --locked`, `TEST_REDIS_URL=redis://127.0.0.1:6380/0 cargo test --locked -- --include-ignored`. 로그는 `var/verification/ws-tests/`에 둔다. api 컨테이너는 재빌드하지 않는다(통합 단계에서 함).
- [x] **Step 7: 문서·커밋** — `docs/bridge-client.md`에 중복 억제 규칙과 세 검사를 짧게 추가한다. `git add src/bridge/stream.rs src/bridge/protocol.rs src/bridge/tests/websocket.rs docs/bridge-client.md` → `fix: skip identical live frames and test slow WS consumers`.

### Task I: 통합 (메인 세션)

- [x] 네 트랙 커밋 확인. `git status`에 남은 변경 없음.
- [x] `docker compose --env-file .env.example up --build -d api` 후 `/api/v1/health` 200, 브라우저 최신/과거/시나리오를 한 번 재확인.
- [x] GPU가 한가할 때 T3 스크립트로 최종 페이지 FPS 재측정. 결과를 `docs/estimated-twin.md` 성능 절에 기록.
- [x] `jeju_power_grid_implementation_plan.md`의 해당 체크 항목(Task 3 느린 소비자/race, Task 6 `should_apply`·Rust 연결·FPS)과 README 링크 갱신. 커밋·push → PR 갱신.

## 실행 결과 (2026-09-30)

커밋: T4 `a776dda`, nginx 경로 `1be8042`, T2 `5afde8f`, T1 `ffceafe`. 계획과 다른 점은 두 가지다. 첫째, preview nginx가 `/twin/*`를 파일별로만 제공해 `/twin/playback.mjs` 경로를 추가했다. 단일 파일 bind mount라 preview를 재시작해야 반영됐다. 둘째, T4 느린 소비자 검사의 약 8KiB 부하를 `data.source`가 아닌 `quality_flags`에 넣었다. `Snapshot::validate`가 source 값을 고정하기 때문이다. 통합 단계에서 api를 재빌드하고 전체 테스트(49+1+3, Redis 포함), 브라우저 QA 375/1280, 유휴 GPU FPS(p5 59.5), 실제 WS 중복 프레임 감시를 다시 확인했다.
