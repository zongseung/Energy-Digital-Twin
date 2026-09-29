# Jeju Power Grid Digital Twin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 기존 제주 DB에 연결한 CPU 기반 Rust API·Redis·WebSocket·지역 수급/ESS 시뮬레이션을 Docker에서 실행하고, 검증된 Higgsfield 3D 산출물과 연결한다.

**Architecture:** Rust 서비스 하나에서 데이터 조회와 시뮬레이션을 제공한다. PostgreSQL/PostGIS와 Redis는 기존 컨테이너를 재사용한다. 실제 사진 기반 3D 제작 경로는 현재 Higgsfield 클라이언트의 지원 한계를 해결한 뒤 연결하며, 백엔드는 그 경로와 독립적으로 검증한다.

**Tech Stack:** Rust edition 2024, 현재 설치된 Rust 1.96.0; axum/ws, tokio, serde/serde_json, sqlx/Postgres, redis/async, chrono, tracing. 단일 Cargo package와 Docker Compose. 의존성의 실제 호환 버전은 첫 빌드에서 확정하고 Cargo.lock에 고정한다.

**Spec:** [기획서 v0.2](jeju_power_grid_digital_twin_design.md), 특히 7·10·11·16·17절.

**Status:** 구현 전 검토용 계획. 현재 작성된 Rust 제품 코드·Compose 파일·실행된 수치 시험은 없다. 직접 구현 방식을 권장하며, 실행 방식은 계획 검토 때 선택한다.

## Global Constraints

- 작업 루트는 `/home/dlwhdtmd/energy-digital-twin`이다. 현재 `git rev-parse --show-toplevel`은 `/home/dlwhdtmd`를 반환하므로 홈 저장소를 stage/commit하거나 그 기준으로 worktree를 만들지 않는다. 프로젝트 저장소 경계를 확인하기 전에는 로컬 파일 작업만 한다.
- 원천 DB에는 SELECT만 수행한다. 기존 서비스·수집기·DB schema·Redis 설정을 변경하지 않는다. iSCSI의 PostgreSQL 데이터 디렉터리를 새 DB에 마운트하지 않는다.
- `.env`의 값은 로그·브라우저·이미지·문서에 포함하지 않는다. 현재 `higs_key`, `vworld_key`만 존재하고 DB/Redis 연결 설정은 없다. 기존 서비스 연결 설정을 확인할 때 값을 출력하지 않고 필요한 DB/Redis 항목만 사용한다.
- `timeline`은 최대 7일, 정확한 시각의 `state`가 없으면 404, 유효하지 않은 입력은 422, 필수 원천 실패는 503으로 구분한다.
- 원천 확인 주기는 60초, 원천 시각이 15분 이상 오래되면 지연으로 표시한다. 같은 시각의 값 정정도 WS로 전송한다. 시간 입력에는 UTC offset이 필요하다.
- 캐시 namespace는 `edt:dev:v1:`; GIS TTL 3600초, 최신 HTTP 30초, 특정 시각/시간 목록 300초. Redis 실패 시 원천 DB로 조회한다.
- 0과 NULL을 구분하고, 비유한값은 NULL+quality flag로 전달한다. `supply_mw`의 API 이름은 `supply_capacity_mw`다. 지역 집계값을 개별 발전량으로 배분하지 않는다.
- GPU 없이 시작한다. 첫 시뮬레이션은 지역 집계 S0–S1, 최대 하루·5분 간격 288구간이다. 선로별 실제 흐름·전압·배전망은 추가 모델 데이터 확보 후 별도 계획으로 구현한다.
- Higgsfield 요청은 실제 3D다. 설치된 website-builder 클라이언트의 2D 깊이 효과를 3D 메시·자유 카메라의 대체 완료로 처리하지 않는다. 자동 cloud 프로젝트 생성·공개 배포를 로컬 코드 구성 요청에서 추론하지 않는다.

## Review Focus

1. 같은 관측 시각에 정정된 값 → WS 버전/내용이 갱신되어야 한다. Task 3.
2. KST와 UTC로 표현한 동일 시각·0/NULL·비유한값 → 같은 관측을 조회하고 의미를 보존해야 한다. Task 2.
3. Redis/DB 단절 → 캐시 장애는 DB로 복구하고 원천 장애는 새 정상 관측으로 위장하지 않아야 한다. Tasks 1–3.
4. ESS 빈 상태·가득 찬 상태·누락 구간 → 에너지를 만들거나 결측을 0으로 채우지 않아야 한다. Task 4.
5. 역사/시나리오 화면에서 늦게 도착한 최신 WS·이전 HTTP 응답 → 선택 시점과 계산 결과를 덮어쓰지 않아야 한다. Task 5.

## Files and Interfaces

| 파일 | 책임 |
|---|---|
| `Cargo.toml`, `Cargo.lock` | 단일 binary/library package `jeju-twin`, 재현 가능한 의존성 |
| `src/main.rs` | 설정 로드, 연결 초기화, 갱신 task와 HTTP 서버 시작/종료 |
| `src/lib.rs` | 모듈 export와 `Settings::from_env()`; 연결 문자열을 Debug로 출력하지 않음 |
| `src/api.rs` | `AppState`, router, 오류→HTTP 응답, 요청 크기/Origin 검증 |
| `src/data.rs` | 읽기 전용 DB 질의, DTO·시간/품질 정규화, Redis cache-aside |
| `src/live.rs` | 최신 snapshot 공유, 60초 확인, WS 초기 snapshot/정정/단절 처리 |
| `src/simulation.rs` | 순수 수급·ESS 계산, 입력 검증, 결과·가정 반환 |
| `Dockerfile`, `compose.yaml`, `.dockerignore`, `.gitignore`, `.env.example` | 개발 실행, 기존 네트워크 연결, secret/build artifact 제외 |
| `tests/integration.rs` | 실제 설정을 명시했을 때만 수행하는 읽기 전용 API/DB/Redis/WS 통합 확인 |
| `README.md` | 실행·설정·검증 명령, 관측/시나리오와 현재 3D 연결 상태 |

단순 함수 테스트는 해당 모듈에 둔다. 새 DB·ORM 계층·플러그인 시스템·작업 큐를 만들지 않는다. UI 파일은 실제 Higgsfield 원본 구조를 확인한 뒤 기존 파일을 수정하며 임의의 별도 프런트를 먼저 만들지 않는다.

공통 계약:

- `Settings`: `hub_database_url`, `demand_database_url`, `redis_url`, `bind_addr`, `allowed_origins`. 필수 DB URL이 없으면 변수명만 포함한 설정 오류. Redis는 설정되었으나 접근 불가할 때 degraded 실행 허용. 개발 bind는 호스트 `127.0.0.1:8090`으로 공개한다.
- `Snapshot`: `schema_version=1`, `observed_at: DateTime<Utc>`, `source`, `quality_flags`, `demand_mw/supply_capacity_mw/wind_mw/solar_mw/renewable_total_mw: Option<f64>`. 직렬화는 timezone offset를 포함한다.
- `AssetCollection`: GeoJSON FeatureCollection. ID는 `hub:<table>:<source_id>`이며 원천 ID·시설 종류·단위·자료 기준일·quality를 properties에 보존한다.
- `DataStore`: `latest()`, `at(DateTime<Utc>)`, `timeline(start,end)`, `assets()`를 제공한다. 결과는 `Result<T, DataError>`이며 `NotFound`, `InvalidInput`, `Unavailable`을 구분한다. 반환 타입은 각각 Snapshot, Snapshot, `Vec<DateTime<Utc>>`, AssetCollection이다.
- `LiveEnvelope`: `type`, `schema_version`, `observed_at`, `sent_at`, `state_version`, `source`, `quality_flags`, `data: Snapshot`. 재시작을 걸친 이력 재전송을 약속하지 않고 접속할 때 전체 snapshot을 보낸다.
- `AppState`: DataStore와 최신 상태의 `tokio::sync::watch` receiver, 제한된 CPU 실행 permit을 공유한다. `api::router(state: AppState) -> axum::Router`.

## Task 1: Rust service and Docker runtime

**Files:** Cargo 파일, `src/main.rs`, `src/lib.rs`, `src/api.rs`, Docker/ignore/env 파일, `README.md`.

**Interfaces:** `Settings::from_env() -> Result<Settings, ConfigError>`; `GET /api/v1/health`는 Hub/Demand 준비 여부와 Redis 상태를 반환한다. DB 실패는 503, Redis만 실패하면 200+degraded다.

- [ ] 설정/health 테스트를 먼저 작성한다. `missing_database_url_names_variable_without_value`, `health_db_down_is_503`, `health_cache_down_is_degraded`에서 secret literal이 응답·오류에 없는지와 상태 코드를 확인한다.
- [ ] `cargo test --lib`를 실행해 해당 기능 부재로 실패하는지 확인한다.
- [ ] 단일 crate와 최소 서버를 구현한다. DB는 읽기 전용 transaction·statement timeout을 사용하고, 시작/종료 시 연결 문자열을 출력하지 않는다.
- [ ] Compose의 서비스 이름은 `jeju-twin-api`, 내부 포트 8090, 외부는 `127.0.0.1:8090`이다. `src_energy-hub-net`, `pv-pipeline-network`를 external로 참조한다. 새 DB/Redis 서비스는 추가하지 않는다.
- [ ] `.env.example`에 `HUB_DATABASE_URL`, `DEMAND_DATABASE_URL`, `REDIS_URL`, `ALLOWED_ORIGINS`의 형식을 기록한다. 실제 비밀번호는 넣지 않는다. 현재 생성 작업을 하지 않는 Rust API에는 Higgsfield 비밀키를 전달하지 않는다.
- [ ] `cargo test --lib`와 `docker compose config --quiet`를 실행한다. 환경 값이 치환된 Compose 전체 출력은 남기지 않는다.
- [ ] 결과를 검토한다. 커밋은 프로젝트 Git 경계가 확정된 경우에만 해당 파일을 대상으로 한다.

## Task 2: Real GIS, observation API and Redis cache

**Files:** `src/data.rs`, `src/api.rs`, `tests/integration.rs`.

**Interfaces:** 위 DataStore/Snapshot/AssetCollection을 구현한다. endpoints는 `/api/v1/jeju/assets`, `/timeline`, `/state`다.

- [ ] 기존 감사의 표/컬럼을 실제 information_schema와 대조하고 `ts` 타입·KST 의미를 수집 원천과 확인한다. 운영 데이터를 수정하지 않는다. 시간대가 확인되기 전 임의 UTC 변환으로 실행 검증을 통과시키지 않는다.
- [ ] `equivalent_offsets_lookup_same_instant`, `missing_state_is_404`, `range_over_seven_days_is_422`, `zero_null_and_nonfinite_are_distinct`를 작성한다. 핵심 assertion은 `0 -> Some(0.0)`, `NULL -> None`, `NaN -> None + nonfinite flag`, `09:00+09:00 == 00:00Z`다.
- [ ] `cargo test data::`로 실패를 확인하고 SQL parameter binding과 타입/단위 변환을 구현한다. 날짜 범위는 `[start,end)`, start<end, timeline 행 수는 최대 2016으로 제한한다.
- [ ] Hub의 `power_line`, `substation`, `power_plant`, `pv_facility`를 원천 ID별로 조회한다. HVDC는 제주에 교차하는 전체 geometry를 보존한다. 이미 확인한 중복 원천 geometry는 대표 ID와 aliases로 묶고 실제 병렬 회선을 임의 병합하지 않는다.
- [ ] GIS/시계열 캐시를 구현한다. cache key는 정규화한 UTC 입력·schema/원천 버전을 포함하고, 오류·404·비유한 원문은 정상 응답처럼 캐시하지 않는다. 지연 여부는 응답 시각 기준으로 재평가한다.
- [ ] `redis_unavailable_falls_back_to_database`와 cache hit/miss 동일 payload 검사를 실행한다. 운영 Redis를 정지하지 않고 테스트 연결 주소/대역을 이용한다. 통합 데이터의 고정 행 수나 고정 최신 시각을 assertion에 쓰지 않는다.
- [ ] 원천에서 고른 실제 두 시점의 응답과 DB 값을 대조한다. `supply_capacity_mw`의 의미와 개별 설비 출력 미확보를 기록한다.

## Task 3: WebSocket live observations

**Files:** `src/live.rs`, `src/api.rs`, `src/main.rs`, `tests/integration.rs`.

**Interfaces:** `refresh_once(store, previous) -> Result<Snapshot, DataError>`는 최신 캐시를 우회한다. `/api/v1/jeju/ws`는 LiveEnvelope를 전송한다. `state_version`은 관측 시각·정규화 값·품질이 달라지면 증가한다.

- [ ] `same_timestamp_correction_is_sent`, `unchanged_snapshot_does_not_emit_new_observation`, `reconnect_receives_full_snapshot`를 작성하고 실패를 확인한다.
- [ ] 서버당 하나의 60초 갱신 task와 Tokio watch를 구현한다. 클라이언트마다 DB 조회 task를 만들지 않는다. 원천 실패는 상태 알림으로 보내고 이전 성공값의 시각을 유지한다.
- [ ] 연결 시 요청 Origin을 허용 목록과 대조한다. 클라이언트 메시지 상한 16KiB, 송신 timeout 5초, heartbeat 30초로 시작한다. 이 연결은 최신 관측 구독만 제공한다.
- [ ] 느린 소비자 테스트에서 메시지 큐가 무한 증가하지 않고 최신 snapshot으로 따라잡거나 연결 종료되는지 확인한다. watch의 현재 값 조회와 변경 대기 사이 정정이 누락되지 않게 한다.
- [ ] 원천 지연이 15분 경계에 도달하는 경우와 원천 실패/복구를 검사한다. heartbeat를 새 계측으로 세지 않는다.
- [ ] `cargo test live::`와 WS 통합 검사를 실행한다. Redis Pub/Sub나 durable stream은 추가하지 않는다.

## Task 4: CPU-based regional and ESS simulation

**Files:** `src/simulation.rs`, `src/api.rs`, `tests/integration.rs`.

**Interfaces:** `simulate(input: SimulationInput) -> Result<SimulationResult, SimulationError>`는 DB/Redis를 직접 접근하지 않는 순수 함수다. `POST /api/v1/jeju/simulate`가 원천 프로파일을 준비해 호출한다.

`SimulationInput`은 offset 포함 start/end, 수요/풍력/태양광 배율, 해당 구간의 원천 Snapshot 배열, 선택적인 `DispatchAssumptions`, 선택적인 `EssConfig`를 갖는다. `DispatchAssumptions`은 시점별 도내 비재생 MW와 3개 HVDC의 ID·MW·가용성·허용 하한/상한을 포함한다. `EssConfig`는 용량 MWh, 충/방전 MW 상한, 두 효율, 초기/최소/최대 에너지 MWh다.

`SimulationResult`는 모델 버전 `regional-ess-v1`, 원천 시점/버전, 사용 가정, 시점별 기준/변경 순부하·ESS 전력/에너지·남은 잔차, 최종 에너지와 status를 반환한다. G/H가 없으면 net-load-only 결과이며 잔차·ESS는 계산하지 않는다. G/H 없이 ESS를 요청하면 422다. 누락 관측은 `incomplete`와 누락 시각을 반환한다.

- [ ] 에너지 보존 테스트를 작성한다. Δt=1/12h, η_ch=0.9, η_dis=0.8일 때 12MW 충전은 +0.9MWh, 12MW 방전은 −1.25MWh인지 확인한다.
- [ ] `empty_ess_cannot_discharge`, `full_ess_cannot_charge`, `charging_and_discharging_are_exclusive`, `missing_interval_is_incomplete`, `disabled_hvdc_is_zero`, `invalid_efficiency_is_422`를 작성하고 `cargo test simulation::`으로 실패를 확인한다.
- [ ] 17.3절의 순부하/잔차와 제한된 충방전 규칙을 그대로 구현한다. `supply_capacity_mw`는 발전량 식에 넣지 않는다. 기준과 변경 사례는 같은 원천/초기 조건으로 계산한다.
- [ ] 입력은 start<end≤start+24h, 5분 grid, 최대 288구간과 유한한 수치/비음수 배율·전력 한계를 확인한다. ESS 용량/Δt는 양수, 효율은 `(0,1]`, 에너지는 설정 범위 안이어야 한다. 시점별 링크 가정 288개를 수용하도록 요청 body는 256KiB로 제한한다.
- [ ] 계산을 bounded blocking 실행으로 연결한다. 동시에 2개까지만 허용하고 포화 시 429를 반환한다. API는 결과를 HTTP로 반환하며 관측 WS에 시나리오 값을 섞지 않는다.
- [ ] 실제 하루 자료로 수요 +10%/풍력 −20%와 명시적 가정 사례를 실행한다. 숫자의 의미·잔차·최종 SOC를 확인하고 모델 결과를 실측으로 표시하지 않는다.

## Task 5: Higgsfield 3D integration checkpoint

**Files:** 확보한 Higgsfield 산출물의 기존 소스 파일, `README.md`. 실제 소스 경로가 확인되기 전 별도 프런트 scaffold를 만들지 않는다.

**Interfaces:** 프런트는 Task 2–4의 HTTP 계약과 Task 3의 LiveEnvelope를 사용한다. 시설 ID는 AssetCollection의 안정 ID와 대응한다. GLB/scene manifest는 기획서 7.1절을 따른다.

- [ ] 현재 Higgsfield 플러그인이 installed/enabled이지만 실제 호출 도구는 미노출이고 website-builder 클라이언트에는 image-to-3D가 없다는 점을 기록한다. 이 상태에서 생성 성공을 가정하지 않는다.
- [ ] 사용 가능한 제작 경로의 실제 장면/GLB 및 프런트 원본을 확인한다. 자유 카메라·개별 시설 선택·외부 API/WS 수정·실행이 가능한지 작은 장면 하나로 확인한다. 별도 경로가 없으면 사용자에게 실제 3D 제작/표시 대안을 결정받는다.
- [ ] 현재 참고 사진 2장의 출처·이용 상태를 유지한다. 생성 입력/텍스처용 이용 조건이 확인된 사진으로 첫 장면을 만든다. 사진 한 장의 보이지 않는 뒷면을 측량 결과로 취급하지 않는다.
- [ ] 기존 프런트에서 `latest`, `history`, `scenario` 모드와 요청 순서를 구현한다. 테스트는 B 시점 선택 뒤 늦게 온 A 응답, 역사 모드의 최신 WS, 시나리오 모드의 관측 WS가 화면을 덮어쓰지 않는지 확인한다.
- [ ] 모델을 실제 좌표·단위·축에 맞추고 시설 ID를 연결한다. 대표 시설 선택과 두 실제 시점, 가상 ESS 결과를 표시한다. S1 결과로 선로별 MW/전압/부하율을 꾸며내지 않는다.
- [ ] 코드/자산의 자체 실행이 확인되면 Docker에 연결한다. Higgsfield 호스팅을 선택하면 외부에서 접근 가능한 HTTPS/WSS와 정확한 Origin이 필요하다. localhost 개발 서버를 공개 연결로 간주하지 않는다.
- [ ] 실제 장면에서 카메라·선택·재접속·모드 전환과 FPS를 검증한다. 실제 3D 산출물이 없으면 이 task는 미완료로 남기고 백엔드 완료와 구분한다.

## Task 6: Integration verification and handoff

**Files:** `tests/integration.rs`, `README.md`, 확정된 제품 파일.

- [ ] `cargo fmt --check`, `cargo clippy --all-targets -- -D warnings`, `cargo test`를 실행한다. 실패를 해결한 뒤 관련 검사를 다시 실행한다.
- [ ] 실제 DB/Redis 설정이 준비되면 `cargo test --test integration -- --ignored`로 읽기 전용 통합 검사를 실행한다. 설정 부재로 건너뛴 검사를 통과했다고 보고하지 않는다.
- [ ] `docker compose config --quiet`, `docker compose build`, `docker compose up -d`를 실행하고 `curl --fail http://127.0.0.1:8090/api/v1/health` 및 두 시점/WS/시뮬레이션을 확인한다. 다른 프로젝트의 컨테이너는 재시작하지 않는다.
- [ ] README에 최소 실행 명령, 필요한 환경 변수명, 실제 수행한 검사, 미확보 3D/계통 입력을 기록한다. `.env`·연결 문자열이 빌드 context·로그·브라우저 산출물에 없는지 확인한다.
- [ ] 완료 기준: 백엔드 계약·수급/ESS 정확성·Docker 실행은 각각 확인하고, 전체 디지털 트윈 완료는 Task 5의 실제 사진 기반 3D 통합까지 충족했을 때만 선언한다.

## Self-review and Execution Handoff

- 계획은 원천 관측/집계 시나리오를 먼저 구현하고, 기획서 G4/G5 및 S2–S4의 전기 계통 모델은 후속 범위로 유지한다.
- 사용자의 실제 3D 요구는 유지한다. 현재 플러그인의 지원 범위를 넘는 제작·코드 접근을 완료로 가정하지 않는 것이 남은 통합 조건이다.
- 전체 기획서의 광역 지형 확대·전체 시설 상세·FPS 기준은 첫 장면 통합 다음 단계다. 이 계획의 백엔드 검증만으로 G0–G3 전체가 완료되지는 않는다.
- 작성자가 인터페이스·상수·실패 경로·기획서 대응을 자체 검토한다. 문서 검토 후 **본 세션 직접 구현** 또는 **하위 에이전트별 구현/검토**를 선택한다. 직접 구현은 API·snapshot·WS·시뮬레이션이 같은 계약을 공유하는 이 규모에서 권장한다.
