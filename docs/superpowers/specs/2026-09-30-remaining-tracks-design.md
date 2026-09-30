# 남은 G3·Task 3/6 작업의 병렬 4트랙 설계

**근거:** [기획서 v0.3](../../../jeju_power_grid_digital_twin_design.md) §4.1-7, §10 G3, §11, §16.7, §17.4, §17.6; [구현 계획](../../../jeju_power_grid_implementation_plan.md) Task 3·6·7의 미체크 항목. 사진 기반 실제 시설 자산은 [채택 판정](../../photo-asset-adoption.md)대로 입력 자료가 없어 이번 범위에서 제외한다.

## 목표와 성공 기준

기획서 §1의 첫 성공 중 빠진 **Rust API의 시간별 데이터 갱신**과 G3의 과거/최신 모드·응답 역전 방지를 웹 뷰어에 넣는다. §17.6의 시나리오 화면, Omniverse Kit의 WS 라이브 연결, §11의 FPS 측정과 WS 느린 소비자·정정 경쟁 검사를 같은 회차에서 병렬로 끝낸다. 모든 결과는 관측/시나리오/추정을 구분하며, 없는 값을 0이나 보간으로 채우지 않는다.

## 공통 제약

- 현재 작업 트리(`feat/photo-scene`)에서 병렬 진행한다. 제품 코드 대부분이 기준 커밋 전까지 untracked여서 git worktree에는 없으므로 워크트리를 쓰지 않고 **트랙별 파일 소유권**으로 충돌을 막는다.
- 새 의존성 없음. 새 파일 최소. 트랙마다 실행 가능한 검사 하나.
- 좌표·장면 GLB·시설 ID·Rust API 계약(`/state`, `/timeline`, `/simulate`, `/ws`)은 바꾸지 않는다. 운영 DB·브릿지·원천 서비스는 읽기만 한다.
- 각 트랙은 소유 문서 하나만 갱신한다. README·구현 계획 상태는 통합 단계에서 한 번 갱신한다.

| 트랙 | 소유 파일 |
|---|---|
| T1 웹 시간축·과거·시나리오 | `renderers/twin/{app.js,index.html,style.css}`, 새 `renderers/twin/playback.mjs`, 새 `tests/playback.mjs`, `docs/estimated-twin.md`, `var/verification/playback/` |
| T2 Kit 라이브 | `renderers/omniverse/twin.py`, `docs/omniverse-twin.md`, `var/rendering/omniverse/live-*/` |
| T3 FPS | `var/verification/fps/` (저장소 코드 변경 없음) |
| T4 WS 검사 | `src/bridge/stream.rs`, `src/bridge/tests/*`, `docs/bridge-client.md` |

## T1 — 웹 시간축·과거·시나리오

하단 수급 패널에 모드 `최신 | 과거 | 시나리오`를 둔다. 3D 장면·시설 선택·바람 코드는 바꾸지 않고, 개별 시설 출력은 계속 `—`다.

- **과거:** 네이티브 날짜 입력(KST 하루, 기본 전날) → `/timeline?start=<D 00:00+09:00>&end=<D+1 00:00+09:00>` → 실제 존재 시각만 가진 range 입력. 이동하면 `/state?at=`를 조회한다. 재생/정지는 1초에 1구간(§17.4). 누락 구간은 채우지 않고 개수를 표시한다.
- **시나리오:** 배율 수요/풍력/태양광(기본 1.10/0.80/1.00). 선택 입력: 도내 비재생 G(MW), HVDC 3링크 교환 MW와 #3 가용 여부, 가상 ESS(MWh·MW·초기 SOC). 가정 입력을 비우면 순부하만(`net_load_only`). 범위는 불러온 날의 `[첫 시각, 마지막 시각+5분)`. `POST /api/v1/jeju/simulate` 결과를 같은 슬라이더로 재생하며 기준↔변경 순부하, ESS 전/후 잔차(+부족/−잉여), SOC를 보여 준다. `incomplete`면 누락 시각만 표시한다. 입력한 G/H/ESS는 "가정", 결과는 "시뮬레이션 · 실측 아님"으로 표시한다.
- **`shouldApply(mode, requestedAt, responseAt, isLive, requestedSeq, responseSeq) -> boolean`** (`playback.mjs`): `latest`는 live만, `history`는 비live·시각 일치·seq 일치를 모두 요구, `scenario`는 관측 응답을 모두 거부한다. 시나리오 결과는 최신 실행 seq와 일치할 때만 반영한다. WS는 계속 수신해 마지막 live 값을 보관하고, 최신 모드로 돌아오면 그 값을 복원한다.
- **검증:** `node tests/playback.mjs`(세 모드, 같은 시각의 오래된 seq 거부 포함). 실제 브라우저 375/1280px에서 과거 두 시각 선택 중 A 응답을 지연시켜도 최종 화면이 B, 과거 모드 중 WS 수신이 덮지 않음, 실제 API로 시나리오 실행, 가로 넘침·페이지 오류 없음.

## T2 — Omniverse Kit 라이브

`twin.py`에 옵션을 더한다. 장면·자산은 한 번만 만들고 프레임마다 재생성하지 않는다. GPU1, 기존 `twin.kit` 버전 고정을 유지한다.

- `--live SECONDS`: Kit 내장 `websockets` 12.0으로 `ws://127.0.0.1:8090/api/v1/jeju/ws` 구독, 끊기면 5초 후 재접속. snapshot마다 기존 `/World/Observations` 속성과 `stateVersion`을 갱신하고, 새 `state_version`에서만 PNG를 캡처한다. status/단절이면 마지막 관측 시각을 유지하고 상태를 기록한다.
- `--replay T1,T2`: 실제 과거 두 시각의 `/state?at=`를 순서대로 적용한다(§16.7-3). 현재 계측으로 표시하지 않는다.
- `--select FACILITY_ID`: `facilityId` 속성으로 prim을 찾아 선택하고 개별 출력 미확보 상태를 기록한다.
- headless에는 omni.ui HUD가 없으므로 PNG에 글자를 넣지 않고 evidence에 PNG↔적용 snapshot을 짝지어 기록한다.
- **검증:** GPU1에서 replay 두 시점의 USD 속성 = API 값, live 수신 1회 이상, API/WS 단절 중 마지막 관측 유지.

## T3 — FPS 측정

`var/verification/fps/`의 스크립트로 1920×1080, 60초 동안 CDP 마우스 드래그로 카메라를 연속 조작하고, 페이지 안 rAF 간격에서 p5 FPS(=1000/p95 간격)를 구한다. WebGL renderer 문자열·Chromium/드라이버 버전·동시 GPU 부하를 기록한다. SwiftShader와 가능하면 GPU 가속 두 조건을 측정한다. 서버 headless 수치는 사용자 기기 수치가 아니다. 목표 p5≥30 미달이면 미달로 기록한다(§11). 통합 단계에서 GPU가 한가할 때 최종 페이지로 재측정한다.

## T4 — WS 느린 소비자·정정 경쟁·중복

- (a) 읽지 않는 클라이언트: 5초 송신 제한으로 세션이 끝나고 큐가 쌓이지 않으며, 다른 클라이언트는 최신값을 받는다.
- (b) 초기 snapshot 직후 정정: 반복 실행해도 최종 버전이 유실되지 않는다.
- (c) 같은 내용 재발행: 현재는 `sent_at`만 다른 프레임을 다시 보낸다. `send_if_modified`로 `type`·`state_version`·`observed_at`·데이터·품질이 모두 같으면 보내지 않는다. 내용 비교이므로 브릿지 재시작으로 버전이 1이 되어도 정정은 전달된다.
- **검증:** `cargo fmt --check`, clippy `-D warnings`, `cargo test --locked`, `TEST_REDIS_URL=redis://127.0.0.1:6380/0 cargo test --locked -- --include-ignored`. 통합 단계에서 api 컨테이너를 재빌드한다.

## 통합과 커밋

기준 커밋(기존 untracked 제품 코드·문서·기획서 수정본, `.env`·`var/`·`target/` 제외) 뒤 트랙별 커밋을 `feat/photo-scene`에 쌓고 push하여 `feat/gpu-infra`로 PR한다. 통합 단계에서 api 재빌드·웹 재확인·FPS 재측정 후 README와 구현 계획의 해당 체크 항목만 갱신한다.

## 범위 밖

사진 기반 실제 시설 자산, Unity/Unreal, SSH 터널 자동 기동, PNG 속 HUD 글자, 실시간 서버 화면 스트리밍, 선로별 조류·전압.
