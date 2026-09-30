# 실제 설비·기상 기획 보완 및 구현 검증

2026-09-30 KST. Root integration: `/home/user/Energy-Digital-Twin`.

## 반영한 요청과 범위

현재 제작 중인 제주 한 구역에 집중하고 PV 한 곳부터 실제 형상·배치·방위·경사를 검증하는 방향으로 기획서를 v0.4로 수정했다. 렌더러와 발전량 모델이 같은 시설 ID·제원 버전을 사용하도록 정하고, 기상→경사면 일사→온도→DC/AC 및 시간순 통계 평가·신뢰구간의 착수/검증 조건을 추가했다. 기존 서버/모델 결정과 실행 기록은 과거 기록으로 보존했다. 구현 계획 Tasks 8–10과 v0.1의 최신 문서 안내도 갱신했다.

실제 구현은 기존 기상청 AWS 단일 60초 수집/HTTP/WS에 기온·습도·이전 60분 누적 강수량을 추가한 것이다. 값은 동일 관측소·시각을 사용한다. 결측은 null/—이며 강수0과 구분한다. 기존 바람 유효성과 별도 `weather_status`를 유지해 바람 결측 때도 유효 기상을 보존하고, 기상만으로 터빈 방향·RPM·출력을 움직이지 않는다. 출처·관측시각·지연은 화면에, 수신시각은 API/WS에 보존한다. 의존성·새 수집 스트림을 추가하지 않았다.

## Root가 직접 실행한 검사

| 명령 / 시나리오 | 실제 결과 | 근거 |
| --- | --- | --- |
| `cargo test --locked` | exit0; unit46 + shutdown1 + CLI3 = 50 passed, 기존 전용 Redis 검사4 ignored, 0 failed | 이 세션의 실행 출력. 제외 검사는 이번 통과 건수에 포함하지 않음 |
| `cargo fmt --all -- --check` | exit0 | root 실행 출력 |
| `cargo clippy --locked --all-targets -- -D warnings` | exit0 | root 실행 출력 |
| `node --test tests/wind-estimate.mjs tests/playback.mjs` | exit0; 2 script tests passed | root 실행 출력 |
| `docker compose build api` | exit0; release image built | image `sha256:528454154e1611c6b955114ccc5fc6db69fa4582d0c817e3e04f7c48e224b8c3` |
| `docker compose up -d --no-deps api` | exit0; 로컬 API 컨테이너 재생성/시작 | 이후 실제 HTTP/WS 관측 성공 |
| `node .omo/evidence/real-weather/runtime-qa.mjs` | exit0 / PASS; 실제 HTTP200, WS101, 원천43지점, 화면값 일치; browser weather HTTP GET0, runtime errors0 | [runtime-qa.json](runtime-qa.json), [실행 스크립트](runtime-qa.mjs) |
| 실제 desktop/mobile 시설 선택 | 실제 innerWidth1280/375, 가로 넘침 없음, PV 선택·관측소/시각·기온/습도/강수 표시 일치 | [1280px](live-1280-pv.png), [375px](live-375-pv.png) |
| 실제 렌더러에 날씨 WS만 대체한 입력 | 영하기온·습도100·강수0, 독립 null, 바람 결측 때 기상 유지/회전 미사용, 오래된 시각·소켓단절 지연, 새 유효 프레임 복구 PASS | runtime-qa.json의 mock_checks; 실제 원천 단절을 강제한 검사가 아님 |
| `python3 .omo/evidence/planning-spec-check.py` | exit0; 16/16 PASS, 과거14–18절 보존/추가 로컬 링크/장면 명세 일치 | root 실행 출력; Python 실행기는 `python3` |
| `git diff --check` | exit0 | root 실행 출력 |

첫 캡처에서 브라우저의 자동 탐색 후 viewport가1440px로 복귀한 것을 PNG 치수 검사로 발견했다. 탐색 완료 뒤 `repinViewport`를 적용하고 실제 `innerWidth`를 assert하여 다시 검사했다. 최종 PNG는1280×900 및375×3457이며 위 결과는 수정 후 실행만을 근거로 한다.

## 병렬 검토와 남은 입력

OMO native team `team-5ba25992`: `planning_spec`(설계), `facility_sources`(공식 원천 대장), `weather_implementation`(기상 구현), `weather_review`(독립 코드 검토). 제품 변경은 각 소유 파일에 한정했다. [원천 검증](../facility-sources-verification-20260930.md), [구현 red/green·parser/HTTP/WS 검증](implementation-evidence.md), [독립 코드 검토](code-review.md)의 CLEAR/APPROVE, blockers[]를 확인했다. 팀의 실행 상태는 종료 시 보존한 `team-archive.json`을 따른다. V2는 에이전트 런타임 archive 기능을 제공하지 않으며 에이전트 완료와 팀 상태 보존을 구분한다.

**미완료:** 현재 등록 PV3곳은 같은 좌표에 다중 등록이 있으며 실필지·시설별 배치도/준공도/CAD·설치 각도가 확보되지 않았다. 기존32패널·25°를 실제 형상으로 채택하지 않았다. 사이트 대응 일사 시계열, 실제 DC/AC 제원·발전 계량이 없어 PV 물리 계산·예측 정확도·통계/시나리오 효과 검증도 아직 구현하지 않았다. [최소 자료와 채택 조건](../../../docs/real-facility-weather-sources.md)을 충족해야 한다.

별도 사용자 작업의 지형·도로·식생·groundcover 변경은 이 검증 범위에서 제외했고 되돌리지 않았다. 커밋·푸시·외부 게시를 수행하지 않았다. 기본 화면은 로컬 `http://127.0.0.1:8080/`에서 확인한다.
