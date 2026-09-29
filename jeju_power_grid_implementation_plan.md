# Jeju Power Grid Digital Twin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 현재 서버에는 데이터 브릿지·방법론만 구현하고, GPU 서버에서 Rust 앱·Redis·수급/ESS 시뮬레이션·사진 기반 3D 환경을 작성·빌드·실행한다.

**Architecture:** 현재 서버의 작은 Rust 브릿지가 기존 DB에서 SELECT한 GIS·관측·과거 자료를 전달한다. `user@192.9.59.208:10000`의 GPU 서버에 제품 Rust 백엔드·앱 Redis·시뮬레이션·3D 제작·렌더러를 구현한다. 브릿지와 앱은 SSH 터널의 HTTP/WebSocket으로 연결한다. TRELLIS.2는 GPU 0, 렌더러는 GPU 1, API·전력 계산은 GPU 서버의 CPU를 사용한다. TRELLIS.2와 Omniverse 우선 연동은 추천안이며 Unity/Unreal은 교환 자산 검증 대상이다.

**Tech Stack:** 브릿지: Rust·axum/ws·tokio·serde·sqlx/Postgres. GPU 앱: Rust·axum/ws·tokio·serde·HTTP/WS 클라이언트·redis/async·chrono·tracing. 각각 작은 Cargo package와 Docker Compose. GPU 자산 제작은 별도 Python/CUDA 배치 컨테이너, 교환 자산은 GLB/OpenUSD. 현재 서버의 Rust 1.96.0과 GPU 서버의 Rust 환경은 별개이며 실제 호환 버전은 각 첫 빌드에서 확정하고 lockfile에 고정한다.

**Spec:** [기획서 v0.3](jeju_power_grid_digital_twin_design.md), 특히 7·10·11·16·17·18절.

**Status:** 2026-09-29 현재 서버의 Task 0 브릿지 구현·Docker 실행·실제 DB/HTTP/WS 검증 완료. Task 0A는 기본·북쪽 부속 도서 두 구역의 지리 데이터 각 12종과 현장 참조 사진 36장을 확보했다. Tasks 1–7의 GPU 제품·상시 터널·추론·렌더링 연결은 후속 작업이다. [브릿지 실행·비동기 수신·지리/사진 수집 방법](bridge/README.md).

## Global Constraints

- 현재 작업 루트 `/home/dlwhdtmd/energy-digital-twin`에는 `bridge/` 코드·방법론·기획·데이터 감사만 둔다. 제품 원본 코드 작성·빌드·실행은 GPU 서버의 `~/energy-digital-twin`에서 수행하며 실제 home·기존 파일·Git 경계를 확인한 뒤 경로를 생성한다. 기존 원격 파일을 덮어쓰지 않는다. 각 프로젝트 경계만 stage/commit하며 `.env`는 제외한다.
- 원천 DB에는 SELECT만 수행한다. 기존 서비스·수집기·DB schema·Redis 설정을 변경하지 않는다. iSCSI의 PostgreSQL 데이터 디렉터리를 새 DB에 마운트하지 않는다.
- `.env`의 값은 로그·브라우저·이미지·문서에 포함하지 않는다. 루트 `.env`에는 `higs_key`, `vworld_key`, SSH용 `password`가 있고, 브릿지 DB 연결은 별도 `bridge/.env`에 보관한다. SSH 비밀번호는 접속에만 사용하고 `.env` 전체를 원격 서버로 복사하지 않는다. 현재 브릿지에는 DB 설정만, GPU 앱에는 브릿지 주소·앱 Redis 설정만 주입한다.
- `timeline`은 최대 7일, 정확한 시각의 `state`가 없으면 404, 유효하지 않은 입력은 422, 필수 원천 실패는 503으로 구분한다.
- 원천 확인 주기는 60초, 원천 시각이 15분 이상 오래되면 지연으로 표시한다. 같은 시각의 값 정정도 WS로 전송한다. 시간 입력에는 UTC offset이 필요하다.
- 캐시는 GPU 서버 앱 Redis의 `edt:dev:v1:`; GIS TTL 3600초, 최신 HTTP 30초, 특정 시각/시간 목록 300초. Redis 실패 시 브릿지로 조회한다. 브릿지 서비스에는 캐시·시뮬레이션·3D 기능을 추가하지 않는다. 별도 지리 수집 CLI의 iSCSI 페이지 checkpoint는 중단 후 재수집을 위한 파일이다.
- 0과 NULL을 구분하고, 비유한값은 NULL+quality flag로 전달한다. `supply_mw`의 API 이름은 `supply_capacity_mw`다. 지역 집계값을 개별 발전량으로 배분하지 않는다.
- API·전력 계산은 GPU 서버의 CPU로, 이미지 자산 생성은 A6000 GPU 0에서, 렌더링은 GPU 1에서 시작한다. 첫 시뮬레이션은 지역 집계 S0–S1, 최대 하루·5분 간격 288구간이다. 선로별 실제 흐름·전압·배전망은 추가 모델 데이터 확보 후 별도 계획으로 구현한다.
- Higgsfield를 실행 의존성에서 제외한다. TRELLIS.2의 실제 mesh/PBR 생성과 엔진 import를 검증한다. 코드/모델은 MIT지만 의존성·엔진의 별도 조건을 보존하고, 엔터프라이즈 기능 완벽 호환을 검증 없이 주장하지 않는다.
- SSH 읽기 전용 확인에서 원격 RTX A6000 두 장·각 49,140MiB·드라이버 `535.183.01`을 확인했다. 컨테이너 GPU 노출과 모델 실행은 미확인이다. GPU 서버는 브릿지 데이터를 사용하며 원천 DB/Redis에 직접 접속하지 않는다. 앱 전용 Redis는 GPU 서버에서 Docker로 실행한다.

## Review Focus

1. 같은 관측 시각에 정정된 값 → WS 버전/내용이 갱신되어야 한다. Task 3.
2. KST와 UTC로 표현한 동일 시각·0/NULL·비유한값 → 같은 관측을 조회하고 의미를 보존해야 한다. Task 2.
3. Redis/브릿지/DB 단절 → 캐시 장애는 브릿지 조회로 복구하고 원천 장애는 새 정상 관측으로 위장하지 않아야 한다. Tasks 0–3.
4. ESS 빈 상태·가득 찬 상태·누락 구간 → 에너지를 만들거나 결측을 0으로 채우지 않아야 한다. Task 4.
5. 역사/시나리오 화면에서 늦게 도착한 최신 WS·이전 HTTP 응답 → 선택 시점과 계산 결과를 덮어쓰지 않아야 한다. Task 6.

## Files and Interfaces

아래 제품 파일 표는 **GPU 서버 작업 공간**의 예정 경로다. 현재 서버에는 `bridge/`의 데이터 전송 서비스·별도 지리 수집 CLI와 방법론·기획서를 작성한다. 브릿지 서비스에는 원천 SELECT·자료 정규화·HTTP/WS 전송을, 수집 CLI에는 원천/API 지리 추출을 구현한다.

| 파일 | 책임 |
|---|---|
| `Cargo.toml`, `Cargo.lock` | 단일 binary/library package `jeju-twin`, 재현 가능한 의존성 |
| `src/main.rs` | 설정 로드, 연결 초기화, 갱신 task와 HTTP 서버 시작/종료 |
| `src/lib.rs` | 모듈 export와 `Settings::from_env()`; 연결 문자열을 Debug로 출력하지 않음 |
| `src/api.rs` | `AppState`, router, 오류→HTTP 응답, 요청 크기/Origin 검증 |
| `src/data.rs` | 브릿지 HTTP 조회, DTO 검증, GPU Redis cache-aside |
| `src/live.rs` | 최신 snapshot 공유, 60초 확인, WS 초기 snapshot/정정/단절 처리 |
| `src/simulation.rs` | 순수 수급·ESS 계산, 입력 검증, 결과·가정 반환 |
| `Dockerfile`, `compose.yaml`, `.dockerignore`, `.gitignore`, `.env.example` | GPU 앱·앱 Redis 개발 실행, secret/build artifact 제외 |
| `tests/integration.rs` | 실제 설정을 명시했을 때만 수행하는 앱/브릿지/Redis/WS 통합 확인 |
| `inference/Dockerfile`, `inference/run.py`, `inference/requirements.lock` | 고정한 TRELLIS.2 코드/모델을 사용하는 GPU 배치 추론 |
| `assets/jeju/manifest.json` | photo·model·시설 ID·scale/axis·원천·생성 설정·자산 hash |
| `renderers/omniverse/jeju_twin.py`, `renderers/omniverse/test_state.py` | Kit 실행 환경의 장면/선택·Rust HTTP/WS 연결, 상태 전환 테스트 |
| `README.md` | 실행·설정·검증 명령, 관측/시나리오와 현재 3D 연결 상태 |

단순 함수 테스트는 해당 모듈에 둔다. 새 원천 DB·ORM 계층·플러그인 시스템·작업 큐를 만들지 않는다. 첫 렌더러는 GPU 서버의 Omniverse Kit에서 실행할 작은 연결 스크립트로 계획한다. Unity/Unreal 운영 앱을 동시에 구현하지 않고 교환 자산 검증부터 수행한다.

공통 계약:

- 브릿지 설정: `HUB_DATABASE_URL`, `DEMAND_DATABASE_URL`, 필요 시 `PV_DATABASE_URL`. GPU 앱 `Settings`: `bridge_base_url`, `redis_url`, `bind_addr`, `allowed_origins`. 필수 설정 부재 오류는 변수명만 포함한다. 개발 bind는 bridge가 현재 `127.0.0.1:8091`, 앱이 GPU `127.0.0.1:8090`이다. `BRIDGE_BASE_URL=http://127.0.0.1:18091`은 SSH 터널로 연결한다.
- `Snapshot`: `schema_version=1`, `observed_at: DateTime<Utc>`, `source`, `quality_flags`, `demand_mw/supply_capacity_mw/wind_mw/solar_mw/renewable_total_mw: Option<f64>`. 직렬화는 timezone offset를 포함한다.
- `AssetCollection`: GeoJSON FeatureCollection. ID는 `hub:<table>:<source_id>`이며 원천 ID·시설 종류·단위·자료 기준일·quality를 properties에 보존한다.
- 제품 `DataStore`: 브릿지의 `latest/at/timeline/assets`를 HTTP로 조회하고 Redis에 검증한 결과를 캐시한다. 결과는 `Result<T, DataError>`이며 `NotFound`, `InvalidInput`, `Unavailable`을 구분한다. 반환 타입은 각각 Snapshot, Snapshot, `Vec<DateTime<Utc>>`, AssetCollection이다.
- `LiveEnvelope`: `type`, `schema_version`, `observed_at`, `sent_at`, `state_version`, `source`, `quality_flags`, `data: Snapshot`. 재시작을 걸친 이력 재전송을 약속하지 않고 접속할 때 전체 snapshot을 보낸다.
- `AppState`: DataStore와 최신 상태의 `tokio::sync::watch` receiver, 제한된 CPU 실행 permit을 공유한다. `api::router(state: AppState) -> axum::Router`.

## Task 0: Data bridge on the current server

**Files:** 현재 서버의 `bridge/`와 방법론·연결 문서.

- [x] 감사의 표/컬럼·ts 타입/시간대·단위를 원천과 대조하고 읽기 전용 SELECT를 준비한다. 오류/로그에 DB 문자열을 포함하지 않는다.
- [x] offset 동치·0/NULL/NaN·시점 부재·최대 7일 제한·같은 시점 정정을 확인하는 테스트를 먼저 작성하고 최소 브릿지를 구현한다.
- [x] `/api/v1/health`, `/api/v1/jeju/assets`, `/timeline`, `/state`, `/ws`의 조회/전송 계약을 제공한다. Hub GIS·Demand 수급의 안정 ID·HVDC 전체 geometry·출처/품질을 보존한다. 실제 병렬 회선을 임의 병합하지 않는다.
- [x] 60초 원천 확인 task 하나와 초기 snapshot·정정·DB 오류 알림을 구현한다. 느린 소비자/메시지 상한/timeout을 처리하며 마지막 성공 관측 시각은 유지한다.
- [x] 브릿지 Compose는 기존 DB의 `src_energy-hub-net`, `pv-pipeline-network`를 external로 참조하고 호스트 `127.0.0.1:8091`에만 공개한다. 기존 서비스와 schema는 변경하지 않는다.
- [x] 실제 두 시점을 브릿지 응답과 DB에서 대조한다. 원천 장애·정정 테스트 때문에 운영 DB를 변경하거나 정지하지 않는다.

**검증 기록 (2026-09-29):** 자동 검사 7개 통과, 별도 실제 원천 검사 1개 통과, `cargo clippy --all-targets -- -D warnings`와 release 빌드 통과. 실제 DB의 두 시점·각 5개 관측값과 KST/UTC 시각을 HTTP 응답에 대조했다. GIS 2,748개·고유 원천 ID·HVDC 전체 경로를 확인했다. Docker의 실제 health/state/timeline/assets·404/422·WS 초기 수신을 확인하고 활성 WS 상태의 SIGTERM 종료 코드 0과 재시작 후 health를 검증했다. 원천 장애·정정·재접속은 격리한 테스트 상태로 검사했고 운영 DB를 수정/정지하지 않았다. 서버 간 상시 터널·GPU 수신 검증은 Task 2/7에 남아 있다.

## Task 0A: Geography collection on the current server

**Files:** `bridge/src/bin/collect_geography.rs`, `bridge/scripts/export_dem.py`, `bridge/scripts/collect_reference_photos.py`, Cargo 파일·수집 방법. 결과는 `/mnt/iscsi/energy-digital-twin/geography/jeju`이며 Git에 넣지 않는다.

- [x] 기존 Hub의 시군구 경계·도로·토지피복·전력 GIS와 DEM을 재사용한다. 원천 DB에는 읽기 전용 SELECT만 수행한다.
- [x] 루트 `.env`의 `vworld_key`로 실제 VWorld 해안선·시군구 경계·도로명주소 건물을 수집한다. 건물 요청은 면적 제한을 충족하는 816개 격자에서 모든 페이지를 확인한다.
- [x] 출처·수집 시각·원천 ID·건수·SHA-256을 보존한다. 전체 성공 manifest와 미완료 `.part`를 구분하고, 완료 파일 재사용·중단 시 checkpoint 재사용·충돌 구역 재수집을 구현한다.
- [x] 원천 DB 건수, 초기 API 994페이지·격자 전체·ID 중복 제거, GeoJSON 좌표·파일 해시와 DEM 원천 픽셀을 실제 자료에서 대조한다.
- [x] Collection에 경로·경계를 모으고 Pagination에 검증 상태를 모아 경계/페이지 인자 중복을 제거한다. 원천 SQL은 bbox 값을 bind하고 API 목록에서 수집·manifest 파일 목록을 함께 만든다.
- [x] 북쪽 부속 도서 204개 격자를 별도 폴더에 추가 수집하고 두 구역 모두 건물통합정보 `LT_C_BLDGINFO`를 확보한다. 주소 레이어와 혼합하지 않고 양수 높이·높이 미확보 건수를 구분한다.
- [x] 신창·신창~차귀 해안·추자 장소 갤러리에서 고유 사진 각 12장과 출처·해시를 저장한다. 중복 ID가 같은 파일에 병렬 저장되는 오류를 회귀 검사로 수정한다.
- [ ] GPU 장면 준비 단계에서 정적 파일을 전송하고 해시를 대조한다. 현재 브릿지 HTTP/WS에는 이 수집 파일을 자동 공개하지 않는다.

**초기 수집 기록 (2026-09-29):** 건물 260,169개, 도로 18,007개, 토지피복 132,225개, 해안선 650개, 기존/API 시군구 경계 각 2개, 전력 선로 54개·변전소 13개·발전 시설 890개·태양광 시설 1,791개, DEM 3,601×2,521 픽셀. 데이터 파일 481,226,655바이트이며 API checkpoint를 포함한 디렉터리는 약 921MiB다. 자동 검사 11개 통과(수집 4개·기존 브릿지 7개), 기본 실행에서 제외하는 기존 실제 원천 검사 1개는 이번 수집 검사에 포함하지 않았다. 기존 경계의 2018년 코드, 건물 실측 높이 미제공, DEM 수직 기준·사용 조건 미확인은 metadata/방법에 표시했다. GIS 개수를 실제 독립 회선·중복 없는 발전소 수로 해석하지 않는다.

**추가 수집 기록 (2026-09-29):** 기본 건물통합정보 463,524개 중 양수 높이 117,008개, 북쪽 주소 건물 1,621개·건물통합정보 2,333개 중 양수 높이 573개. 북쪽 도로 36개·토지피복 669개·해안선 244개·DEM 1,801×1,260 픽셀, 지리 데이터 파일 7,210,137바이트를 확보했다. 실제 사진 36장은 장소별 출처·해시와 참고용 상태를 보존했다. 북쪽의 기존 Hub 경계 후보에는 완도·신안도 있어 장면에서 공식 제주 경계로 필터링하며 두 구역의 같은 원천 ID를 병합 시 중복 제거한다. 높이 양수 항목도 측량 검증·건물 레이어 간 매칭을 추가 확인한다. 전기적 bus/회선/변압기 연결·R/X/B·정격·개별 설비/HVDC 운전 계측은 여전히 별도 확보 대상이다.

**추가 검증:** Rust 기본 검사 13개, 별도 실제 DB/HTTP 원천 검사 1개, 사진 갤러리의 장소 구분·중복 회귀 검사가 통과했다. 코드 검토에서 발견한 사진 중복 저장 경쟁을 수정했다. 두 구역의 SHA-256·GeoJSON 좌표·API checkpoint 2,599페이지·높이 통계와 DEM 전체 픽셀을 원천과 대조했다. 공식 제주 경계 87개 폴리곤의 32,995개 꼭짓점이 두 수집 범위에 포함되는 것을 확인했다. 이 검증은 자료 범위·전송 계약의 확인이며 정밀 측량·전기적 모델·GPU 장면 통합 완료를 뜻하지 않는다.

## Task 0B: Supplementary power inputs on the current server

**Files:** `bridge/scripts/collect_power_data.py`, 표준 라이브러리 회귀 검사, 수집 방법. 결과는 `/mnt/iscsi/energy-digital-twin/geography/jeju/power/`에 저장하며 Git에 넣지 않는다. Rust 브릿지/제품 실행부는 유지하고 준비용 수집 스크립트만 추가한다.

- [x] 기존 Hub 제주 접속정보를 읽기 전용 export하고 주소/변전소/변압기/배전선 코드의 실제 건수·캐시 시점을 기록한다.
- [x] 공식 2025 시간별 전력거래량과 2024 풍력시설 목록을 내려받아 날짜·연료원·단위·레코드 수를 검사한다. 음수 정산값은 품질 표시로 보존한다.
- [x] 출력제어 파일의 실제 ZIP 형식을 확인하고 원본 및 제주 CSV 두 개를 보존한다. 목록 기준일과 실제 기록 기간을 구분한다.
- [x] 공개 논문 부록 39회선·연결 이름·정격과 원문 표를 함께 보존한다. 같은 연결 이름의 별도 번호를 병합하지 않는다.
- [x] 공식 2024 제주 운영실적 PDF를 보존한다. 동시 수집 3개·잠금·원자 저장·해시 재사용·미완료 manifest를 검증한다.
- [ ] 공공데이터포털 ServiceKey와 건축HUB·ASOS 시간자료 활용신청 후 높이 및 풍속/풍향/QC를 수집한다. 기존 VWorld 키와 구분한다.
- [ ] 현재 실제 from/to bus·회선 번호·R/X/B·변압기·tap·정격·P/Q·HVDC별 계측을 확보하고 원천 버전/단위/운전시점을 검증한다. 공개 경로가 확인되지 않은 항목은 기관 제공 가능 여부를 확인해야 한다.
- [ ] 다중 시점 현장 사진·카메라/치수 기준을 확보하고 시설 ID와 연결한다. 기존 관광 갤러리 사진을 측량 세트로 간주하지 않는다.

**검증 기록 (2026-09-30):** 9개 파일 58,603,088바이트의 해시·metadata·CSV/JSONL 레코드를 전수 확인했다. 제주 접속정보 99,860건, 변전소 코드 15개·변압기 코드 쌍 54개·배전선 코드 조합 146개, 2025 거래량 43,800행(음수 12건), 풍력시설 25건, 공개 회선 39개다. 출력제어 ZIP 기준일은 2026-06-30이지만 내부 제주 PV 125행은 2021-10-17~2024-06-03, 풍력 336행은 2021-01-13~2024-05-30이다. 수집 manifest는 성공이며 `electrical_twin_ready=false`다. 추가 데이터가 실제 전력망 모델 완성을 뜻하지 않는다.

## Task 1: Rust product and Docker runtime on the GPU server

**Files:** Cargo 파일, `src/main.rs`, `src/lib.rs`, `src/api.rs`, Docker/ignore/env 파일, `README.md`.

**Interfaces:** `Settings::from_env() -> Result<Settings, ConfigError>`; 앱 `GET /api/v1/health`는 브릿지 원천 준비 여부와 앱 Redis 상태를 반환한다. 브릿지/DB 실패는 503, Redis만 실패하면 200+degraded다.

- [ ] GPU의 home·기존 파일·Git 경계·Rust·Docker를 확인하고 제품 작업 공간을 만든다. 설정/health 테스트에서 브릿지 주소 부재·원천 실패 503·캐시 실패 degraded와 secret 미노출을 확인한다.
- [ ] `cargo test --lib`를 실행해 해당 기능 부재로 실패하는지 확인한다.
- [ ] GPU 작업 공간에서 단일 제품 crate와 최소 서버를 구현한다. 원천 DB 대신 브릿지 HTTP/WS 클라이언트를 사용하며 연결 문자열을 출력하지 않는다.
- [ ] GPU Compose에 `jeju-twin-api`와 앱 전용 Redis를 둔다. 개발 시 앱은 Linux host network의 `127.0.0.1:8090`, Redis는 GPU 호스트 `127.0.0.1:6380`으로만 공개한다. 앱은 호스트의 SSH 터널 `127.0.0.1:18091`에 접근한다. 실제 포트 충돌과 컨테이너 접근을 검증한다.
- [ ] GPU `.env.example`에는 `BRIDGE_BASE_URL`, 앱 `REDIS_URL`, `ALLOWED_ORIGINS`의 형식만 기록한다. 원천 DB 설정·SSH 비밀번호·현재 `.env` 전체를 GPU로 복사하지 않는다.
- [ ] `cargo test --lib`와 `docker compose config --quiet`를 실행한다. 환경 값이 치환된 Compose 전체 출력은 남기지 않는다.
- [ ] 결과를 검토한다. 커밋은 프로젝트 Git 경계가 확정된 경우에만 해당 파일을 대상으로 한다.

## Task 2: Bridge transport, observation API and GPU Redis cache

**Files:** `src/data.rs`, `src/api.rs`, `tests/integration.rs`.

**Interfaces:** 위 DataStore/Snapshot/AssetCollection을 구현한다. endpoints는 `/api/v1/jeju/assets`, `/timeline`, `/state`다.

- [ ] 기획서 18.6절의 SSH reverse forwarding으로 GPU `127.0.0.1:18091`을 현재 `127.0.0.1:8091`에 연결한다. forwarding 허용·loopback listener·포트 충돌·터널 단절을 확인하고 GPU 앱에서 bridge health·GIS·두 시점을 조회한다.
- [ ] `equivalent_offsets_lookup_same_instant`, `missing_state_is_404`, `range_over_seven_days_is_422`, `zero_null_and_nonfinite_are_distinct`를 작성한다. 핵심 assertion은 `0 -> Some(0.0)`, `NULL -> None`, `NaN -> None + nonfinite flag`, `09:00+09:00 == 00:00Z`다.
- [ ] GPU에서 `cargo test data::`로 실패를 확인하고 브릿지 HTTP 조회·DTO 검증을 구현한다. 날짜 범위는 `[start,end)`, start<end, timeline 행 수는 최대 2016으로 제한한다.
- [ ] 브릿지가 전달한 GIS의 원천 ID·aliases·단위·자료 기준일·HVDC geometry가 앱 API에서도 보존되는지 확인한다. GPU 앱이 source ID나 원천 계량을 임의 재배분하지 않는다.
- [ ] GIS/시계열 캐시를 구현한다. cache key는 정규화한 UTC 입력·schema/원천 버전을 포함하고, 오류·404·비유한 원문은 정상 응답처럼 캐시하지 않는다. 지연 여부는 응답 시각 기준으로 재평가한다.
- [ ] `redis_unavailable_falls_back_to_bridge`와 cache hit/miss 동일 payload 검사를 실행한다. 앱 Redis 장애·브릿지 장애·원천 DB 장애를 구분한다. 기존 운영 Redis/DB를 정지하지 않고 테스트 입력을 이용한다. 고정 행 수나 고정 최신 시각을 assertion에 쓰지 않는다.
- [ ] 원천에서 고른 실제 두 시점의 응답과 DB 값을 대조한다. `supply_capacity_mw`의 의미와 개별 설비 출력 미확보를 기록한다.

## Task 3: WebSocket live observations

**Files:** `src/live.rs`, `src/api.rs`, `src/main.rs`, `tests/integration.rs`.

**Interfaces:** GPU 앱이 브릿지 WS를 수신하고 사용자 `/api/v1/jeju/ws`에 LiveEnvelope를 전달한다. `state_version`은 관측 시각·정규화 값·품질이 달라지면 증가한다. 브릿지 재시작/버전 재설정이 실제 정정을 숨기지 않아야 한다.

- [ ] `same_timestamp_correction_is_sent`, `unchanged_snapshot_does_not_emit_new_observation`, `reconnect_receives_full_snapshot`를 작성하고 실패를 확인한다.
- [ ] GPU 앱의 브릿지 수신 task 하나와 Tokio watch를 구현한다. 클라이언트마다 source 조회나 별도 bridge 연결을 만들지 않는다. 터널/브릿지/원천 실패는 상태 알림으로 보내고 이전 성공값의 시각을 유지한다.
- [ ] 연결 시 요청 Origin을 허용 목록과 대조한다. 클라이언트 메시지 상한 16KiB, 송신 timeout 5초, heartbeat 30초로 시작한다. 이 연결은 최신 관측 구독만 제공한다.
- [ ] 느린 소비자 테스트에서 메시지 큐가 무한 증가하지 않고 최신 snapshot으로 따라잡거나 연결 종료되는지 확인한다. watch의 현재 값 조회와 변경 대기 사이 정정이 누락되지 않게 한다.
- [ ] 원천 지연이 15분 경계에 도달하는 경우와 원천 실패/복구를 검사한다. heartbeat를 새 계측으로 세지 않는다.
- [ ] `cargo test live::`와 WS 통합 검사를 실행한다. Redis Pub/Sub나 durable stream은 추가하지 않는다.

## Task 4: CPU-based regional and ESS simulation

**Files:** GPU 작업 공간의 `src/simulation.rs`, `src/api.rs`, `tests/integration.rs`. 브릿지에는 계산 코드를 추가하지 않는다.

**Interfaces:** `simulate(input: SimulationInput) -> Result<SimulationResult, SimulationError>`는 DB/Redis를 직접 접근하지 않는 순수 함수다. `POST /api/v1/jeju/simulate`가 원천 프로파일을 준비해 호출한다.

`SimulationInput`은 offset 포함 start/end, 수요/풍력/태양광 배율, 해당 구간의 원천 Snapshot 배열, 선택적인 `DispatchAssumptions`, 선택적인 `EssConfig`를 갖는다. `DispatchAssumptions`은 시점별 도내 비재생 MW와 3개 HVDC의 ID·MW·가용성·허용 하한/상한을 포함한다. `EssConfig`는 용량 MWh, 충/방전 MW 상한, 두 효율, 초기/최소/최대 에너지 MWh다.

`SimulationResult`는 모델 버전 `regional-ess-v1`, 원천 시점/버전, 사용 가정, 시점별 기준/변경 순부하·ESS 전력/에너지·남은 잔차, 최종 에너지와 status를 반환한다. G/H가 없으면 net-load-only 결과이며 잔차·ESS는 계산하지 않는다. G/H 없이 ESS를 요청하면 422다. 누락 관측은 `incomplete`와 누락 시각을 반환한다.

- [ ] 에너지 보존 테스트를 작성한다. Δt=1/12h, η_ch=0.9, η_dis=0.8일 때 12MW 충전은 +0.9MWh, 12MW 방전은 −1.25MWh인지 확인한다.
- [ ] `empty_ess_cannot_discharge`, `full_ess_cannot_charge`, `charging_and_discharging_are_exclusive`, `missing_interval_is_incomplete`, `disabled_hvdc_is_zero`, `invalid_efficiency_is_422`를 작성하고 `cargo test simulation::`으로 실패를 확인한다.
- [ ] 17.3절의 순부하/잔차와 제한된 충방전 규칙을 그대로 구현한다. `supply_capacity_mw`는 발전량 식에 넣지 않는다. 기준과 변경 사례는 같은 원천/초기 조건으로 계산한다.
- [ ] 입력은 start<end≤start+24h, 5분 grid, 최대 288구간과 유한한 수치/비음수 배율·전력 한계를 확인한다. ESS 용량/Δt는 양수, 효율은 `(0,1]`, 에너지는 설정 범위 안이어야 한다. 시점별 링크 가정 288개를 수용하도록 요청 body는 256KiB로 제한한다.
- [ ] 계산을 bounded blocking 실행으로 연결한다. 동시에 2개까지만 허용하고 포화 시 429를 반환한다. API는 결과를 HTTP로 반환하며 관측 WS에 시나리오 값을 섞지 않는다.
- [ ] 실제 하루 자료로 수요 +10%/풍력 −20%와 명시적 가정 사례를 실행한다. 숫자의 의미·잔차·최종 SOC를 확인하고 모델 결과를 실측으로 표시하지 않는다.

## Task 5: GPU asset generation and GLB/OpenUSD exchange

**Files:** GPU 작업 공간의 `inference/Dockerfile`, `inference/run.py`, `inference/requirements.lock`, `assets/jeju/manifest.json`, `README.md`.

**Interfaces:** 배치 명령은 `python inference/run.py --image <local-image> --output <asset-directory> --seed <integer>`다. 선택 GPU는 컨테이너 장치 노출로 제한한다. 출력은 `asset.glb`와 생성 metadata다. 원천 사진은 수정하지 않는다.

- [ ] 새 서버의 `nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv`와 컨테이너 내부 장치 접근을 확인한다. GPU 0/1을 독립적으로 지정하고 단일 96GB VRAM을 전제하지 않는다.
- [ ] TRELLIS.2 공식 코드와 모델 revision을 고정하고, 해당 PyTorch/CUDA와 native extension을 Docker 안에서 설치한다. upstream 요구사항과 license를 보존한다. 모델 cache·생성 결과는 영속 디렉터리에 둔다.
- [ ] 이용 조건이 확인되고 설비의 전체 형상이 보이는 사진 한 장으로 원본 예제의 추론을 검증한다. 현재 배너 사진 두 장만으로 정밀 시설 복원을 완료했다고 판단하지 않는다.
- [ ] 공식 실행 경로를 얇게 감싼 `run.py`를 작성한다. 존재하는 출력 파일은 기본 덮어쓰지 않고, 실패한 작업에 완료 manifest를 남기지 않는다. 테스트는 없는 입력·출력 충돌·추론 실패 시 성공 metadata 부재를 확인한다.
- [ ] GLB를 PNG/JPEG PBR 재질과 지원 가능한 mesh 크기로 정리한다. 면 수·텍스처 크기는 첫 엔진 FPS에 맞춰 정하고, 공식 예제의 고밀도 자산 설정을 제품 기본값으로 가정하지 않는다.
- [ ] 모델/사진 ID·seed·설정·소요시간·최대 VRAM·출력 hash를 기록한다. 시설 ID·scale·axis·추정 후면·라이선스를 manifest에 보존한다. 실제 치수/축·회전자 pivot은 별도 검증한다.
- [ ] 공식 Asset Converter 또는 확보한 제작 도구로 GLB→OpenUSD를 변환하고, geometry·재질·texture reference·시설 node/ID가 보존되는지 검사한다. 아직 성공하지 않은 엔진 조합은 미검증으로 기록한다.

## Task 6: First renderer and cross-engine compatibility

**Files:** GPU 작업 공간의 `renderers/omniverse/jeju_twin.py`, `renderers/omniverse/test_state.py`, `assets/jeju/manifest.json`, `README.md`.

**Interfaces:** Omniverse 첫 연동은 추천안을 채택한 경우의 계획이다. `jeju_twin.py`는 일반 Python이 아닌 고정한 Kit 실행 환경에서 실행한다. 입력은 USD scene·manifest·Rust API 주소다. 다른 렌더러를 우선 선택하면 이 task의 표시 실행부만 교체한다.

- [ ] Omniverse 실행 버전과 확장을 고정하고, GPU 1에서 대표 자산/1m 기준체를 로드한다. 소스 자산의 길이 대비 scale 오차 1% 이하, 재질 누락 없음, 시설별 선택을 확인한다.
- [ ] Task 2–4의 HTTP 계약과 Task 3의 WS envelope를 연결한다. 시설은 manifest의 안정 ID로 찾으며 개별 계량이 없으면 제주 집계 HUD와 해당 시설의 자료 미확보를 표시한다.
- [ ] 상태 적용을 `should_apply(mode, requested_at, response_at, is_live, requested_seq, response_seq) -> bool` 함수로 분리해 필요한 한 테스트 파일에서 검사한다. history에서는 live=false·선택 시점 일치·요청 sequence 일치를 모두 요구한다. scenario에서 관측 응답 거부, latest에서 최신 관측 적용을 확인한다. 같은 시점의 오래된 HTTP 요청 응답도 최신 선택을 덮어쓰지 않아야 한다.
- [ ] Kit UI/scene thread에 안전하게 반영하고, 입력/선택·카메라·시간축·WS 재접속·느린 서버/오류 표시를 확인한다. 모델을 프레임마다 재생성하지 않는다.
- [ ] 동일 GLB를 지정 Unity/glTFast와 Unreal 버전에서 가져와 형상·scale·텍스처·node/ID 보존을 비교한다. GLB/USD import 성공과 해당 엔진의 완성된 운영 UI/WS 연결을 구분해 기록한다.
- [ ] 렌더러는 GPU 앱의 `127.0.0.1:8090`에서 실제 health·snapshot·WS를 사용한다. 브릿지/터널 단절이 앱과 렌더러에 전달되는지 확인한다. 렌더러 컨테이너에서는 호스트 앱 접근도 확인한다. 서버 렌더링의 화면 스트리밍은 첫 장면 실행 뒤 별도 배포 범위로 결정한다.
- [ ] 카메라·선택·시점 갱신과 1920×1080에서 60초 FPS p5≥30 목표를 시험한다. 목표 미달은 자산/장면 복잡도를 조정하고 결과를 기록한다. 협업·SSO·권한·클라우드 배포는 구현한 항목만 지원으로 보고한다.

## Task 7: Integration verification and handoff

**Files:** `tests/integration.rs`, `README.md`, 확정된 제품 파일.

- [ ] `cargo fmt --check`, `cargo clippy --all-targets -- -D warnings`, `cargo test`를 실행한다. 실패를 해결한 뒤 관련 검사를 다시 실행한다.
- [ ] 현재 서버의 bridge↔DB와 GPU 앱↔bridge/앱 Redis, renderer↔GPU 앱을 각각 검사한다. 양쪽에서 필요한 `cargo test`를 실행한다. 설정 부재로 건너뛴 검사를 통과했다고 보고하지 않는다.
- [ ] `docker compose config --quiet`, `docker compose build`, `docker compose up -d`를 실행하고 `curl --fail http://127.0.0.1:8090/api/v1/health` 및 두 시점/WS/시뮬레이션을 확인한다. 다른 프로젝트의 컨테이너는 재시작하지 않는다.
- [ ] 현재 README에는 브릿지·전송·방법론을, GPU README에는 제품 실행·추론·렌더링·실제 검사·미확보 입력을 기록한다. `.env`·연결 문자열이 빌드 context·로그·브라우저 산출물에 없는지 확인한다.
- [ ] 완료 기준: 백엔드 계약·수급/ESS 정확성·Docker 실행은 각각 확인하고, 전체 디지털 트윈 완료는 Tasks 5–6의 실제 사진 기반 3D 통합까지 충족했을 때만 선언한다.

## Self-review and Execution Handoff

- 계획은 원천 관측/집계 시나리오를 먼저 구현하고, 기획서 G4/G5 및 S2–S4의 전기 계통 모델은 후속 범위로 유지한다.
- 사용자의 실제 3D 요구는 유지한다. GPU 모델 추론·자산 교환·엔진/API 연동을 실제로 확인하는 것이 남은 통합 조건이다.
- 전체 기획서의 광역 지형 확대·전체 시설 상세·FPS 기준은 첫 장면 통합 다음 단계다. 이 계획의 백엔드 검증만으로 G0–G3 전체가 완료되지는 않는다.
- 작성자가 인터페이스·상수·실패 경로·기획서 대응을 자체 검토한다. 문서 검토 후 **본 세션 직접 구현** 또는 **하위 에이전트별 구현/검토**를 선택한다. 직접 구현은 API·snapshot·WS·시뮬레이션이 같은 계약을 공유하는 이 규모에서 권장한다.
- 서버별 작업 위치: Tasks 0·0A는 현재 서버, Tasks 1–6의 제품 코드 작성·빌드·실행은 GPU 서버, Task 7은 양쪽 연결 검증이다. 현재 프로젝트에 제품 백엔드·시뮬레이션·3D 코드를 구현하지 않는다.
