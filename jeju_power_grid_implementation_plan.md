# Jeju Power Grid Digital Twin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 현재 서버에는 데이터 브릿지·방법론만 구현하고, GPU 서버에서 Rust 앱·Redis·수급/ESS 시뮬레이션·사진 기반 3D 환경을 작성·빌드·실행한다.

**Architecture:** 현재 서버의 작은 Rust 브릿지가 기존 DB에서 SELECT한 GIS·관측·과거 자료를 전달한다. `user@192.9.59.208:10000`의 GPU 서버에 제품 Rust 백엔드·앱 Redis·시뮬레이션·3D 제작·렌더러를 구현한다. 브릿지와 앱은 SSH 터널의 HTTP/WebSocket으로 연결한다. TRELLIS.2는 GPU 0, 렌더러는 GPU 1, API·전력 계산은 GPU 서버의 CPU를 사용한다. TRELLIS.2와 Omniverse 우선 연동은 추천안이며 Unity/Unreal은 교환 자산 검증 대상이다.

**Tech Stack:** 브릿지: Rust·axum/ws·tokio·serde·sqlx/Postgres. GPU 앱: Rust·axum/ws·tokio·serde·HTTP/WS 클라이언트·redis/async·chrono·tracing. 각각 작은 Cargo package와 Docker Compose. GPU 자산 제작은 별도 Python/CUDA 배치 컨테이너, 교환 자산은 GLB/OpenUSD. 현재 서버의 Rust 1.96.0과 GPU 서버의 Rust 환경은 별개이며 실제 호환 버전은 각 첫 빌드에서 확정하고 lockfile에 고정한다.

**Spec:** [기획서 v0.3](jeju_power_grid_digital_twin_design.md), 특히 7·10·11·16·17·18절.

**Status (2026-09-30):** Task 0 bridge checks are from `feat/async-data-bridge@9a94596`; current source branch tip is `3cc3a3b` (5 commits pulled from `03e02ef` on 2026-09-30). The previously transferred geography snapshot remains `03e02ef`; newly documented supplementary power/ASOS/building-registry files remain on the source server. The earlier geography files were transferred and validated, and live bridge→GPU app health/GIS/timeline/exact-state/WS checks passed. The real daily profile correctly reports one missing interval as incomplete; its contiguous 287 intervals run net-load-only because G/H are absent. Tunnel disconnect/manual recovery passed; automatic tunnel startup is unimplemented. DEM vertical datum/license remain unknown. A separate user-approved prototype now places 10 estimated turbines at source GIS coordinates and connects a regional observation to the browser/Omniverse scene. The model-to-facility match and most dimensions are tentative, so recognizable actual-site appearance and physical validation remain unproven. Earlier source-GIS mock QA is runtime validation only. User acceptance and Task 6 remain incomplete against design §§1, 3, and 14.7; independent visual review is pending. Review records: [serving](docs/serving-review.md), [cache](docs/cache-review.md).

## Global Constraints

- 데이터 서버 작업 공간 `/home/dlwhdtmd/energy-digital-twin`에는 `bridge/` 코드·방법론·기획·데이터 감사만 둔다. GPU 제품 작업 공간은 `/home/user/Energy-Digital-Twin` (`feat/gpu-infra`)이며 수집 데이터 작업 공간은 별도 `.worktrees/data`다. 각 저장소·호스트의 경계를 지키고 기존 원격 파일을 덮어쓰지 않는다. 각 프로젝트 경계만 stage/commit하며 `.env`는 제외한다.
- 원천 DB에는 SELECT만 수행한다. 기존 서비스·수집기·DB schema·Redis 설정을 변경하지 않는다. iSCSI의 PostgreSQL 데이터 디렉터리를 새 DB에 마운트하지 않는다.
- `.env`의 값은 로그·브라우저·이미지·문서에 포함하지 않는다. 루트 `.env`의 `higs_key`, `vworld_key`, SSH 인증용 `pwd`는 해당 용도로만 사용한다. ASOS·건축HUB 준비 수집용 `api_key`는 원천 서버에서 사용한다. 브릿지 DB 연결은 별도 `bridge/.env`에 보관하고 `.env` 전체를 원격 서버로 복사하지 않는다. 현재 브릿지에는 DB 설정만, GPU 앱에는 브릿지 주소·앱 Redis 설정만 주입한다.
- `timeline`은 최대 7일, 정확한 시각의 `state`가 없으면 404, 유효하지 않은 입력은 422, 필수 원천 실패는 503으로 구분한다.
- 원천 확인 주기는 60초, 원천 시각이 15분 이상 오래되면 지연으로 표시한다. 같은 시각의 값 정정도 WS로 전송한다. 시간 입력에는 UTC offset이 필요하다.
- 캐시는 GPU 서버 앱 Redis의 `edt:dev:v1:`; GIS TTL 3600초, 최신 HTTP 30초, 특정 시각/시간 목록 300초. Redis 실패 시 브릿지로 조회한다. 브릿지에는 캐시·시뮬레이션·3D 기능을 추가하지 않는다.
- 0과 NULL을 구분하고, 비유한값은 NULL+quality flag로 전달한다. `supply_mw`의 API 이름은 `supply_capacity_mw`다. 지역 집계값을 개별 발전량으로 배분하지 않는다.
- API·전력 계산은 GPU 서버의 CPU로, 이미지 자산 생성은 A6000 GPU 0에서, 렌더링은 GPU 1에서 시작한다. 첫 시뮬레이션은 지역 집계 S0–S1, 최대 하루·5분 간격 288구간이다. 선로별 실제 흐름·전압·배전망은 추가 모델 데이터 확보 후 별도 계획으로 구현한다.
- Higgsfield를 실행 의존성에서 제외한다. TRELLIS.2의 실제 mesh/PBR 생성과 엔진 import를 검증한다. 코드/모델은 MIT지만 의존성·엔진의 별도 조건을 보존하고, 엔터프라이즈 기능 완벽 호환을 검증 없이 주장하지 않는다.
- 원격 RTX A6000 두 장(각 49,140MiB, driver `535.183.01`)을 확인했다. GPU 0/1 격리 CUDA probe와 공식 TRELLIS 예제 추론은 통과했다; Jeju 시설 추론은 미검증이다. GPU 서버는 브릿지 데이터를 사용하며 원천 DB/Redis에 직접 접속하지 않는다. 앱 전용 Redis는 GPU 서버에서 Docker로 실행한다.

## Review Focus

1. 같은 관측 시각에 정정된 값 → WS 버전/내용이 갱신되어야 한다. Task 3.
2. KST와 UTC로 표현한 동일 시각·0/NULL·비유한값 → 같은 관측을 조회하고 의미를 보존해야 한다. Task 2.
3. Redis/브릿지/DB 단절 → 캐시 장애는 브릿지 조회로 복구하고 원천 장애는 새 정상 관측으로 위장하지 않아야 한다. Tasks 0–3.
4. ESS 빈 상태·가득 찬 상태·누락 구간 → 에너지를 만들거나 결측을 0으로 채우지 않아야 한다. Task 4.
5. 역사/시나리오 화면에서 늦게 도착한 최신 WS·이전 HTTP 응답 → 선택 시점과 계산 결과를 덮어쓰지 않아야 한다. Task 6.

## Files and Interfaces

아래 제품 파일 표는 **GPU 서버 작업 공간**의 예정 경로다. 현재 서버에는 `bridge/Cargo.toml`, `bridge/src/main.rs`, `bridge/Dockerfile`, `bridge/compose.yaml`, `bridge/.env.example`과 방법론·기획서만 작성한다. 브릿지에는 원천 SELECT·자료 정규화·HTTP/WS 전송만 구현한다.

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

- 브릿지 설정: `HUB_DATABASE_URL`, `DEMAND_DATABASE_URL`, 필요 시 `PV_DATABASE_URL`. GPU 앱 `Settings`: `bridge_base_url`, `redis_url`, `bind_addr`, `allowed_origins`. 필수 설정 부재 오류는 변수명만 포함한다. bridge bind는 `127.0.0.1:8091`, 앱은 GPU `127.0.0.1:8090`이다. GPU→source SSH local forward가 GPU의 `127.0.0.1:18091`을 bridge loopback에 연결한다.
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

**검증 기록 (2026-09-29):** 자동 검사 7개 통과, 별도 실제 원천 검사 1개 통과, `cargo clippy --all-targets -- -D warnings`와 release 빌드 통과. 실제 DB의 두 시점·각 5개 관측값과 KST/UTC 시각을 HTTP 응답에 대조했다. GIS 2,748개·고유 원천 ID·HVDC 전체 경로를 확인했다. Docker의 실제 health/state/timeline/assets·404/422·WS 초기 수신을 확인하고 활성 WS 상태의 SIGTERM 종료 코드 0과 재시작 후 health를 검증했다. 원천 장애·정정·재접속은 격리한 테스트 상태로 검사했고 운영 DB를 수정/정지하지 않았다. 이 기록 작성 시점에는 GPU 연동이 남아 있었으며, 2026-09-29 live bridge→GPU 결과는 `var/verification/live-integration.log`에 있다.

## Task 0A: Source-side geography data collection

**Source and transfer record:** `feat/async-data-bridge` was fast-forwarded from `5d15ecbc` to `03e02ef`. Main-island data: 12 datasets, 763,403,223 bytes and 877,327 GeoJSON features. Northern-island data: 12 datasets, 7,210,137 bytes and 4,964 features. Both transferred snapshots pass validation (`.worktrees/data/var/data/geography/validation-source-03e02ef.json`, `validation-northern-islands-03e02ef.json`). Bridge API contract is unchanged; seven contract tests pass. 117,008 main-island and 573 northern-island `building_info` features have positive height fields. Heights are source attributes; floor count is not a height substitute. Thirty-six photo files (5,232,452 bytes) were hash-checked remotely; only metadata is local, image bytes remain on source, and image rights are unverified (`source-photo-audit-03e02ef.json`).

- [x] Remote branch fetch/fast-forward and source-side commit contents recorded.
- [x] Main and northern-island 12-dataset snapshots pass sidecar/hash/size, ID, geometry and coordinate/raster validation. Building-height fields exist for the counts above. Source DEM vertical datum/license remain unverified; polygon coordinates outside the selection bbox can occur because geometries are not clipped.
- [x] Snapshot data is in `.worktrees/data/var/data/geography/source-03e02ef` outside Git and remains separate from the bridge HTTP API. The live bridge API continues to provide the existing 2,748 power GIS features.
- [ ] Thirty-six photo records have remotely verified hashes and local metadata only; image rights remain unverified, so no photo is cleared for reconstruction.
- [ ] DEM vertical datum/license are unknown; floor count is not building height. Keep these limits attached to any use.

## Task 0B: Supplementary power inputs on the current server

**GPU 동기화 범위:** 코드·기획은 `.worktrees/async-data-bridge@3cc3a3b`에 pull했다. 아래 수집 건수는 원천 서버의 검증 기록이며, 이번 코드 동기화에서 iSCSI 데이터 파일을 새로 복사하지 않았다. 2025 ASOS는 과거 시간 자료로 현재 실시간 AWS 바람을 대체하지 않는다. 로컬 준비 수집 회귀 검사 14개 통과.

**Files:** `bridge/scripts/collect_power_data.py`, `bridge/scripts/collect_registered_api.py`, 표준 라이브러리 회귀 검사, 수집 방법. 결과는 `/mnt/iscsi/energy-digital-twin/geography/jeju/power/` 및 `registered_api/<snapshot>/`에 저장하며 Git에 넣지 않는다. Rust 브릿지/제품 실행부는 유지하고 준비용 수집 스크립트만 추가한다.

- [x] 기존 Hub 제주 접속정보를 읽기 전용 export하고 주소/변전소/변압기/배전선 코드의 실제 건수·캐시 시점을 기록한다.
- [x] 공식 2025 시간별 전력거래량과 2024 풍력시설 목록을 내려받아 날짜·연료원·단위·레코드 수를 검사한다. 음수 정산값은 품질 표시로 보존한다.
- [x] 출력제어 파일의 실제 ZIP 형식을 확인하고 원본 및 제주 CSV 두 개를 보존한다. 목록 기준일과 실제 기록 기간을 구분한다.
- [x] 공개 논문 부록 39회선·연결 이름·정격과 원문 표를 함께 보존한다. 같은 연결 이름의 별도 번호를 병합하지 않는다.
- [x] 공식 2024 제주 운영실적 PDF를 보존한다. 동시 수집 3개·잠금·원자 저장·해시 재사용·미완료 manifest를 검증한다.
- [x] KPX 발전기/모선 5분 상태추정 ZIP의 공개 다운로드와 실제 헤더를 확인한다. 발전기 월 자료는 전체 레코드·CRC를 검사하고 모선 자료는 표본 검사한다. 임시 확보와 iSCSI 본수집을 구분한다.
- [ ] 공개 발전기/모선 코드와 제주 설비의 매핑·시점별 안정성을 확인한 뒤 월별 자료를 iSCSI에 수집한다. 큰 압축 해제 파일은 스트리밍하고 시간 라벨·부호·누락/중복을 검사한다.
- [ ] 제주 포함 GIST 공개 연구망의 배포 버전을 고정·확보하고 CSV 명세·HVDC 경계 조건·제주 부분을 검증한다. 실제 운영망과 추정 R/X/B·tap을 구분한다.
- [ ] VWorld 회원 제주 일반/집합 SHP 또는 건축HUB 표제부 파일/API로 등록 높이의 표본 보완율을 확인한다. VWorld `A31=건물높이`의 실제 단위·결측·기존 레이어 매칭부터 검사한다.
- [x] 사용자가 ASOS·건축HUB 활용신청 승인을 확인하고 등록한 `api_key`로 두 API의 정상 응답을 검증한다. ASOS는 2025년 제주·고산·성산·서귀포 시간 자료로 범위를 정한다.
- [x] ASOS 4지점과 기존 주소 건물 파일에 관측된 186개 법정동 표제부를 iSCSI에 수집하고 페이지·결과·manifest를 전수 검증한다. 등록 높이 수집과 실제 GIS 결측 보완을 구분한다.
- [ ] 현재 실제 from/to bus·회선 번호·R/X/B·변압기·tap·정격·P/Q·HVDC별 계측을 확보하고 원천 버전/단위/운전시점을 검증한다. 공개 경로가 확인되지 않은 항목은 기관 제공 가능 여부를 확인해야 한다.
- [ ] 다중 시점 현장 사진·카메라/치수 기준을 확보하고 시설 ID와 연결한다. 기존 관광 갤러리 사진을 측량 세트로 간주하지 않는다.

**검증 기록 (2026-09-30):** 9개 파일 58,603,088바이트의 해시·metadata·CSV/JSONL 레코드를 전수 확인했다. 제주 접속정보 99,860건, 변전소 코드 15개·변압기 코드 쌍 54개·배전선 코드 조합 146개, 2025 거래량 43,800행(음수 12건), 풍력시설 25건, 공개 회선 39개다. 출력제어 ZIP 기준일은 2026-06-30이지만 내부 제주 PV 125행은 2021-10-17~2024-06-03, 풍력 336행은 2021-01-13~2024-05-30이다. 수집 manifest는 성공이며 `electrical_twin_ready=false`다. 추가 데이터가 실제 전력망 모델 완성을 뜻하지 않는다.

**후속 확보 경로 조사:** [자료 검토 19절](jeju_power_grid_data_and_modeling_review.md)의 KPX ZIP 두 개는 `/tmp`의 다운로드 검증 자료이며 위 9개 파일 집계에 포함하지 않는다. GIST 모델은 공개 목록/논문을 확인했지만 직접 원본 확보는 미완료다. VWorld 제주 높이 SHP는 회원 다운로드 조건을 확인했으며, 민간 제공 불가로 명시된 제주 1m DSM은 즉시 수집 후보에서 제외했다.

**등록 API 본수집 (2026-09-30):** `registered_api/20260930/`에 ASOS 2025년 4개 지점 35,040행과 건축물대장 217,844행을 저장했다. 양수 높이 115,226행 중 250m 초과 4행(최대 4,970m)은 검토 대상이며 실제 GIS 보완으로 세지 않는다. JSONL 190개 파일 391,938,526바이트·2,309페이지를 오프라인 전수 검증했고 준비 수집 회귀 검사 14개가 통과했다. 지상 관측의 풍속/풍향 공란 37시간과 원문 QC를 유지했다. [상세 품질·범위 기록](jeju_power_grid_data_and_modeling_review.md#20-등록-키를-이용한-asos건축hub-본수집--2026-09-30).

**잔여 입력 점검 (2026-09-30):** 지도 건물과 건축HUB를 필지·도로명주소로 전수 대조한 결과, 양수·250m 이하 높이의 주소 일치 후보는 48,648개다. 실제 GIS 높이 보완 건수는 미확정이므로 위 체크 항목은 유지한다. KPX 전국 상태추정의 제주 코드 대응, GIST 원본 CSV, 실제 R/X/B·P/Q 및 설비 사진/치수도 아직 미확보다. [검증 방법과 세부 건수](jeju_power_grid_data_and_modeling_review.md#21-잔여-데이터의-연결-가능성-점검--2026-09-30).

## Task 1: Rust product and Docker runtime on the GPU server

**Files:** Cargo 파일, `src/main.rs`, `src/lib.rs`, `src/api.rs`, Docker/ignore/env 파일, `README.md`.

**Interfaces:** `Settings::from_env() -> Result<Settings, ConfigError>`; 앱 `GET /api/v1/health`는 브릿지 원천 준비 여부와 앱 Redis 상태를 반환한다. 브릿지/DB 실패는 503, Redis만 실패하면 200+degraded다.

- [x] GPU 작업 공간·Rust·Docker를 확인하고 제품 crate를 구성했다. 설정/health 검사에서 브릿지 부재·원천 실패 503·캐시 degraded·secret 미노출을 확인했다. (기록: `docs/superpowers/plans/2026-09-29-infrastructure.md`)
- [ ] 계획에 적힌 초기 `cargo test --lib` 실패 재현은 증거 기록에서 확인되지 않았다.
- [x] 단일 제품 crate와 서버, 브릿지 HTTP/WS 수신기를 구현했다. 원천 DB에 직접 연결하지 않으며 연결 문자열을 출력하지 않는다.
- [x] Compose에 앱과 전용 Redis를 두고 loopback 포트를 검증했다. 앱 container healthy 및 실제 bridge 연결 통과 (`var/verification/live-integration.log`).
- [x] `.env.example`에는 브릿지 주소·앱 Redis·허용 Origin 설정만 둔다; 원천 DB/SSH 비밀정보를 복사하지 않는다.
- [x] Cargo 검사와 Compose 설정 검사를 실행했다. 최종 기록은 infrastructure/bridge-client 계획과 `var/verification/bridge-tests.log`에 있다.
- [x] 작업 트리 범위를 검토했다; 커밋/푸시는 하지 않았다.

## Task 2: Bridge transport, observation API and GPU Redis cache

**Files:** `src/data.rs`, `src/api.rs`, `tests/integration.rs`.

**Interfaces:** 위 DataStore/Snapshot/AssetCollection을 구현한다. endpoints는 `/api/v1/jeju/assets`, `/timeline`, `/state`다.

- [x] GPU→source SSH local forward (`127.0.0.1:18091` → source `127.0.0.1:8091`)로 연결했다. Bridge/app health 200, GIS 2,748개 전체 field/geometry/ID 보존, 2026-09-28 KST timeline 287개, 실제 두 시각 exact state 일치를 확인했다 (`var/verification/live-integration.log`). SSH master 단절 뒤 수동 재연결을 검증했다. 자동 재기동은 미구현 (`var/verification/live-tunnel-recovery.log`, `.json`).
- [ ] 계획의 테스트 함수명 그대로의 회귀 테스트는 없다. 합성 TCP로 offset/NULL·0/비유한값/범위를 확인했고, live integration은 실제 두 exact state를 대조했다. 실제 NaN 행 및 동치 offset 쌍은 확인되지 않았다.
- [x] 단일 binary 구조에 맞춰 브릿지 HTTP 조회·DTO 검증 및 half-open timeline 범위를 구현했다. 초기 실패 재현 명령은 실행 기록에 없다.
- [x] 브릿지 GIS 2,748개 전체 feature의 field·geometry·ID가 앱 API까지 보존됨을 실제 연결에서 확인했다. 새 11종 지리 자료는 별도 전송/검증 대상이며 bridge HTTP가 제공한다고 간주하지 않는다.
- [x] GIS/관측 캐시와 정정 시 invalidation, 오류 시 우회/단절 시 우회를 구현하고 합성 Redis 검사로 확인했다.
- [x] 실제 앱 Redis를 이용해 cache hit/miss·장애 우회와 payload 보존을 합성 source로 검사했다. 앱 Redis 장애·브릿지 장애·원천 DB 장애를 구분한다. 기존 운영 Redis/DB를 정지하지 않고 테스트 입력을 이용한다. 고정 행 수나 고정 최신 시각을 assertion에 쓰지 않는다.
- [x] 실제 두 시점의 모든 반환 필드를 source 응답과 대조했다. `supply_capacity_mw`는 용량이며 개별 설비 출력으로 간주하지 않는다 (`live-integration.log`).

## Task 3: WebSocket live observations

**Files:** `src/live.rs`, `src/api.rs`, `src/main.rs`, `tests/integration.rs`.

**Interfaces:** GPU 앱이 브릿지 WS를 수신하고 사용자 `/api/v1/jeju/ws`에 LiveEnvelope를 전달한다. `state_version`은 관측 시각·정규화 값·품질이 달라지면 증가한다. 브릿지 재시작/버전 재설정이 실제 정정을 숨기지 않아야 한다.

- [x] 동일 시각 정정·재접속·disconnect 동작을 합성 WS source로 검사했다. 계획의 테스트 이름과는 다르며 unchanged case는 별도 명시 증거가 없다.
- [x] 단일 upstream WS 수신 task와 watch 기반 fanout을 구현했다. 클라이언트마다 source 조회나 별도 bridge 연결을 만들지 않는다. 터널/브릿지/원천 실패는 상태 알림으로 보내고 이전 성공값의 시각을 유지한다.
- [x] 허용 Origin, 송신 timeout과 heartbeat를 적용했다. upstream 메시지 상한은 검사한다; 사용자 연결의 계획상 16KiB 제한은 별도 확인되지 않았다. 송신 timeout 5초, heartbeat 30초다. 이 연결은 최신 관측 구독만 제공한다.
- [ ] watch fanout·정정/재접속/종료 검사는 통과했다; 느린 consumer 전용 검사와 현재 값/변경 대기 race 전용 검사는 증거가 없어 부분 완료다.
- [x] 15분 지연 경계(899/900초·미래 시각·재설정)를 단위 검사했다 (`bridge::tests::delay_begins_at_exactly_fifteen_minutes`). Actual tunnel disconnect/recovery passed: status WS preserved the last observed_at and five MW values while health/state returned 503; after manual reconnect the same client received snapshot version 18 and health returned 200 (`live-tunnel-recovery.log`, `.json`).
- [x] 전체 suite의 WS 합성 통합 검사와 15분 지연 경계 단위 검사 통과. 실제 source WS 초기 snapshot도 source 관측과 대조했다 (`live-integration.log`). Tunnel disconnect/manual recovery passed (`var/verification/live-tunnel-recovery.log`, `.json`); automatic startup remains unimplemented. Redis Pub/Sub나 durable stream은 추가하지 않았다.

## Task 4: CPU-based regional and ESS simulation

**Files:** GPU 작업 공간의 `src/simulation.rs`, `src/api.rs`, `tests/integration.rs`. 브릿지에는 계산 코드를 추가하지 않는다.

**Interfaces:** `simulate(input: SimulationInput) -> Result<SimulationResult, SimulationError>`는 DB/Redis를 직접 접근하지 않는 순수 함수다. `POST /api/v1/jeju/simulate`가 원천 프로파일을 준비해 호출한다.

`SimulationInput`은 offset 포함 start/end, 수요/풍력/태양광 배율, 해당 구간의 원천 Snapshot 배열, 선택적인 `DispatchAssumptions`, 선택적인 `EssConfig`를 갖는다. `DispatchAssumptions`은 시점별 도내 비재생 MW와 3개 HVDC의 ID·MW·가용성·허용 하한/상한을 포함한다. `EssConfig`는 용량 MWh, 충/방전 MW 상한, 두 효율, 초기/최소/최대 에너지 MWh다.

`SimulationResult`는 모델 버전 `regional-ess-v1`, 원천 시점/버전, 사용 가정, 시점별 기준/변경 순부하·ESS 전력/에너지·남은 잔차, 최종 에너지와 status를 반환한다. G/H가 없으면 net-load-only 결과이며 잔차·ESS는 계산하지 않는다. G/H 없이 ESS를 요청하면 422다. 누락 관측은 `incomplete`와 누락 시각을 반환한다.

- [x] 에너지 보존 테스트에서 충전 +0.9MWh, 방전 −1.25MWh를 확인했다. Δt=1/12h, η_ch=0.9, η_dis=0.8일 때 12MW 충전은 +0.9MWh, 12MW 방전은 −1.25MWh인지 확인한다.
- [x] ESS empty/full, missing interval, disabled HVDC, invalid efficiency 경계 검사를 통과했다. 계획상 테스트 명칭 및 최초 실패 재현은 일부 다르며 실행 방식은 단일 binary의 simulation unit tests다.
- [x] 17.3절 기반 순부하/잔차·제한 dispatch/ESS 계산을 순수 함수로 구현했다. `supply_capacity_mw`는 발전량 식에 넣지 않는다. 기준과 변경 사례는 같은 원천/초기 조건으로 계산한다.
- [x] 계산 입력의 시간 범위/grid/최대 288구간, 유한 수치, ESS 효율·용량·에너지 경계를 검사한다. 오프라인 CLI JSON과 HTTP request body 모두 256KiB로 제한하며 oversized request 거부 검사를 통과했다.
- [x] `POST /api/v1/jeju/simulate` 구현: exact-time bridge snapshots를 bounded하게 조회하고 30초 deadline을 적용한다. 요청은 256KiB, 동시 계산 2개로 제한하며 초과 시 429, 계산은 permit을 유지한 `spawn_blocking`으로 실행한다. normalized source provenance/hash를 결과에 포함한다. 네 API 테스트에서 정상/ESS·시나리오·404·incomplete/503·입력 제한·429를 확인했다.
- [ ] 실제 2026-09-28 profile은 23:55 KST 한 구간 누락으로 `incomplete`와 빈 points를 반환했다. 00:00–23:55 KST half-open 287구간은 `net_load_only`로 완료했다. G/H 부재로 ESS/잔차를 실측 완료로 표시하지 않는다 (`live-integration.log`).

## Task 5: GPU asset generation and GLB/OpenUSD exchange

**진행 상태:** 네 모델의 선택 파일 다운로드/checksum 검증, GPU 0/1 CUDA probe, TRELLIS image/native extension build, upstream example inference와 GLB validation을 완료했다. Jeju facility inference/alignment, USD/engine import는 미완료 (`var/generated/upstream-smoke-03/metadata.json`, `var/verification/trellis-final-validation.log`).

**Files:** GPU 작업 공간의 `inference/Dockerfile`, `inference/run.py`, `inference/requirements.lock`, `assets/jeju/manifest.json`, `README.md`.

**Interfaces:** 배치 명령은 `python inference/run.py --image <local-image> --output <asset-directory> --seed <integer>`다. 선택 GPU는 컨테이너 장치 노출로 제한한다. 출력은 `asset.glb`와 생성 metadata다. 원천 사진은 수정하지 않는다.

- [x] GPU 0/1을 각각 단독 노출한 PyTorch 2.6.0+cu124 컨테이너 CUDA probe 통과 (`trellis-cuda-probe.log`, `trellis-gpu1-probe.log`). 양쪽은 RTX A6000 49,140MiB이며 단일 96GB로 취급하지 않는다.
- [x] TRELLIS.2 image와 native extensions 빌드 완료. 공식 코드/model revision 고정, PyTorch/CUDA 설정을 사용했다; upstream example inference는 아래 기록에 한정한다.
- [ ] Official TRELLIS upstream example inference succeeded: 8,186,968-byte GLB, 96,390 triangles, 137.7s, peak allocated/reserved 2.62/2.92GiB. This is not a Jeju facility photo or facility reconstruction; facility inference remains pending.
- [x] Thin `inference/run.py` wrapper and metadata implemented. Final validator passed; missing input, output collision, inference failure, and malformed GLB checks passed. Failed jobs leave no success metadata (`trellis-final-validation.log`).
- [ ] Validator confirms finite mesh and two embedded PNG/JPEG images (96,390 triangles, 1024 texture). PBR appearance, target-engine limits, and FPS-based optimization remain unverified.
- [ ] Upstream model/image revision, seed, sampler settings, elapsed time, peak VRAM, output hash, and estimated unseen surfaces are recorded in metadata. Facility ID/scale/pivot are null and axis is unvalidated; facility manifest and physical alignment remain pending.
- [ ] 공식 Asset Converter 또는 확보한 제작 도구로 GLB→OpenUSD를 변환하고, geometry·재질·texture reference·시설 node/ID가 보존되는지 검사한다. 아직 성공하지 않은 엔진 조합은 미검증으로 기록한다.

## Task 6: First renderer and cross-engine compatibility

**Files:** GPU 작업 공간의 `renderers/omniverse/jeju_twin.py`, `renderers/omniverse/test_state.py`, `assets/jeju/manifest.json`, `README.md`.

**Interfaces:** Omniverse 첫 연동은 추천안을 채택한 경우의 계획이다. `jeju_twin.py`는 일반 Python이 아닌 고정한 Kit 실행 환경에서 실행한다. 입력은 USD scene·manifest·Rust API 주소다. 다른 렌더러를 우선 선택하면 이 task의 표시 실행부만 교체한다.

**Acceptance status:** 사용자 승인 범위로 원천 GIS 좌표에 10기 터빈의 추정 geometry를 배치하고 실제 지역 집계를 연결한 browser/Omniverse prototype은 만들었다. 그러나 operator spec의 로터 직경/정격 외 치수는 추정이고 GIS 시설과 개별 터빈 모델의 대응도 잠정적이며, 사진은 모델 texture에 쓰지 않았다. 따라서 실제 현장 사진에서 알아볼 수 있는 시설 외형·배치와 물리 정합은 입증되지 않았다. 아래 검증은 기술/표현 확인이며 사용자 수용이나 Task 6 완료를 뜻하지 않는다. 기준은 [기획서](jeju_power_grid_digital_twin_design.md) §1, §3, §14.7에 있다.

**사용자 참고 이미지에 따른 정정 (2026-09-30):** 실제 공장과 그 설비 배치를 재현한 3D 장면을 나란히 보여 준 이미지는 요구 품질의 기준이다. 프로젝트 대상이 공장으로 바뀐 것은 아니다. 제주 설비를 독립 3D 객체로 구성하고 실제 형상·배치·확인된 치수를 맞춘 뒤, 안정 ID로 확보된 운전 데이터를 연결해야 한다. VWorld 영상 지도와 건물 높이 입체화 역시 공간 검증 보조물이며 이 기준의 대체 결과가 아니다. 로컬 제작 폴더와 원격 `/mnt/iscsi/energy-digital-twin/geography/jeju`에는 검증된 제조사 설비 GLB/USD/CAD/BIM이 없다. 아래 10기 GLB는 확인된 일부 사양과 사진 참고를 사용해 만든 추정 geometry이며 실제 터빈 대응·치수·사진 정합은 검증되지 않았다. 제조사/측량 자산과 정확한 정합이 여전히 필요하다.

- [ ] Omniverse 실행 버전과 확장을 고정하고, GPU 1에서 대표 자산/1m 기준체를 로드한다. 소스 자산의 길이 대비 scale 오차 1% 이하, 재질 누락 없음, 시설별 선택을 확인한다.
- [ ] Task 2–4의 HTTP 계약과 Task 3의 WS envelope를 연결한다. 시설은 manifest의 안정 ID로 찾으며 개별 계량이 없으면 제주 집계 HUD와 해당 시설의 자료 미확보를 표시한다.
- [ ] 상태 적용을 `should_apply(mode, requested_at, response_at, is_live, requested_seq, response_seq) -> bool` 함수로 분리해 필요한 한 테스트 파일에서 검사한다. history에서는 live=false·선택 시점 일치·요청 sequence 일치를 모두 요구한다. scenario에서 관측 응답 거부, latest에서 최신 관측 적용을 확인한다. 같은 시점의 오래된 HTTP 요청 응답도 최신 선택을 덮어쓰지 않아야 한다.
- [ ] Kit UI/scene thread에 안전하게 반영하고, 입력/선택·카메라·시간축·WS 재접속·느린 서버/오류 표시를 확인한다. 모델을 프레임마다 재생성하지 않는다.
- [ ] 동일 GLB를 지정 Unity/glTFast와 Unreal 버전에서 가져와 형상·scale·텍스처·node/ID 보존을 비교한다. GLB/USD import 성공과 해당 엔진의 완성된 운영 UI/WS 연결을 구분해 기록한다.
- [ ] 렌더러는 GPU 앱의 `127.0.0.1:8090`에서 실제 health·snapshot·WS를 사용한다. 브릿지/터널 단절이 앱과 렌더러에 전달되는지 확인한다. 렌더러 컨테이너에서는 호스트 앱 접근도 확인한다. 서버 렌더링의 화면 스트리밍은 첫 장면 실행 뒤 별도 배포 범위로 결정한다.
- [ ] 카메라·선택·시점 갱신과 1920×1080에서 60초 FPS p5≥30 목표를 시험한다. 목표 미달은 자산/장면 복잡도를 조정하고 결과를 기록한다. 협업·SSO·권한·클라우드 배포는 구현한 항목만 지원으로 보고한다.

**Mock renderer preliminary QA (separate from Task 6 completion):**

- [x] Browser and Omniverse mock paths consume the same source-GIS scene, using facility symbols and proxy terrain. The daily supply curve is synthetic preview data only and is never inserted into the source/API/Redis. Browser scenario QA passed at 375/768/1280 widths, including search, selection, empty results, layer toggles, time endpoints, camera controls, and context-loss list fallback (`var/verification/mock/browser-qa.json`). Independent final visual gate passed all 25 browser and Omniverse PNG checks (`.omo/evidence/mock-visual-gate-review.md`); rendering code review also APPROVE with no blockers (`.omo/evidence/mock-rendering-code-review.md`). Fullscreen reader, touch behavior, FPS, and WebRTC remain unverified.
- [x] Omniverse Kit 106.5 rendered on GPU 1 with driver 535.183.01; PNG/USD were generated and checked by reopening the USD, matching source/PNG hashes, and asserting 20,044 terrain triangles plus 2,694 facility markers (`var/rendering/omniverse/verified-v2/evidence.json`). See [browser mock](docs/mock-rendering.md) and [Omniverse mock](docs/omniverse-mock.md). This verifies the mock preview only, not facility reconstruction, scale/height accuracy, live WS, Unity/Unreal, or a 60-second FPS target.

**Site and estimated 10-turbine prototype evidence (narrow progress; not Task 6 completion):**

- [x] `renderers/site/prepare.py` self-test and source-hash regeneration pass for 31,752 terrain triangles in UTM 52N / EGM2008 at metre units and 1:1 scale. The site subset has 2,661 building-info records and 623 positive height values. The official VWorld SDK probe authenticated on localhost and rendered imagery/terrain (`var/verification/vworld-probe/result.json`; details: `docs/site-rendering.md`). These are spatial-data/runtime checks only; heights and imagery are not equipment reconstruction.
- [x] `renderers/twin/build.py` creates a component-based 10-turbine GLB at source positions for `hub:power_plant:5722`–`5731`. Operator-reference rotor diameter (91.59 m) and rated power (3 MW) are retained; hub height (80 m), jacket and other dimensions are estimates. Each facility-to-model match is tentative; individual telemetry is null. Details: [turbine twin](docs/omniverse-twin.md), `renderers/twin/spec.json`.
- [x] Browser QA passes at 375/768/1280 widths for the 10 facilities, selection, camera and rotor demo. It reads actual regional observations with five aggregate MW values, source time/quality and HTTP failure/recovery (`var/verification/twin/browser-qa.json`). Actual canvas picking selected T5723, keyboard selection and WebGL context-loss list fallback passed (`extra-qa.json`). The viewer implements HTTP plus WS updates; exact Origin acceptance/rejection and real WS initial snapshot were checked (`http-qa.json`, `live-ws.json`). Twenty rapid tab switches and context-loss races passed the regression replay. Individual turbine output remains null.
- [x] Omniverse Kit 106.5 on GPU 1 exported/reopened the GLB→USD scene with 193 meshes, 224,264 faces, 9 materials, stable facility IDs, metre units and Y-up; captures include the source regional snapshot. Evidence: `var/rendering/omniverse/twin-03/evidence.json`. Code review: `.omo/evidence/twin-code-review.md` PASS/CLEAR. Independent visual review: `.omo/evidence/twin-visual-review.md` PASS for estimated facility appearance at all three browser widths and both RTX views.
- [ ] Actual equipment geometry/photo correspondence, survey accuracy, per-turbine telemetry, scale validation against measurements, Unity/Unreal, fullscreen accessibility, touch behavior, 60-second FPS, WebRTC and continuous live Kit scene updates remain unverified. Keep Task 5 facility inference and the original Task 6 completion criteria open.

**송전 경로·부분 지형·PV 표본 확장 (2026-09-30; 좁은 범위의 추가 검증):**

- [x] 원천54선로에서 배전5개를 제외하고 완전히 같은 HVDC3쌍의 ID를 보존하여46경로를 표시했다. 변전소13개와 서로 다른 등록 좌표의 PV3곳을 추가했다. 대표선로3596의59철탑·도체, 변전소 설비와 PV32패널 배열은 추정이다. 전기적 접속/실측 흐름/개별 출력은 미확인이다.
- [x] 탐라–한림 일부 Copernicus DSM(약30m 원천, 약60m 표시)과 원천 토지피복 분류 색을1m/UTM52N/EGM2008 장면으로 생성했다. GLB 재개방·원천 SHA/좌표/ID/normal/단위와 지형 관통 샘플 검사 통과 (`var/rendering/grid/verification.json`).
- [x] 기존 풍력 화면에 grid lazy-load/재사용·목록 선택·레이어·전체/한림/지형/PV/HVDC 시점을 추가했다. 브라우저375/768/1280과 로딩 실패/재시도·풍력 복귀·context-loss 검사 통과 (`var/verification/grid/`). GIS 개요 경로는 지형 위 오버레이이며 물리 도체와 구분한다.
- [x] 같은 GLB를 GPU1/Kit106.5로 변환·렌더하고17시설 root/300mesh/827,926면·단위·ID·위치 보존을 재개방 검증했다. 송전 구조/부분 지형/PV1600×1000 PNG3장과 USD를 확보했다 (`var/rendering/omniverse/grid-01/`). 전 제주46경로는 browser/manifest 데이터이며 USD에는 대표 물리선로만 포함한다.
- [ ] PV 등록 용량(197.02/27/99kW)은 실측 출력이 아니며 동일 등록 좌표의 다중 레코드가 있다. 실제 설치 필지·패널 수/기종·현장 형상·개별 계측·배전망·전체 지형·계통 해석은 미완료다. 기존 Task5/6 전체를 완료로 체크하지 않는다. 상세: [시설 3D](docs/estimated-twin.md).

**통합 실제 영상·DSM 고도 수정 (2026-09-30; 기존 분리 모드 대체):**

- [x] VWorld 실제 영상375타일/6400×3840을 EPSG3857 UV로 DSM에 입히고, 풍력10기·PV3곳·한림변전소·대표선로를 같은 원천 좌표의 단일 GLB로 결합했다. 범위 밖 시설/표시선은 제외하며 일부7경로와21개 UI목록을 유지한다. DSM 약0–760m 높이·수직배율1·약511m 봉우리 근접시점을 확인했다.
- [x] 모드 탭을 없애고 같은 장면에서 카메라·선택·레이어·풍력 시연을 조작한다. PC/태블릿/모바일 및 컨텍스트 손실·실패 표시를 확인했다. 현재 실행 설명은 [통합 장면](docs/estimated-twin.md)이다.
- [x] GPU1 Kit106.5에서 동일 자산을4시점 RTX 렌더하고15시설 ID/좌표·418mesh·922,830면·단위·USD 재열기·PNG해시를 검증했다 (`var/rendering/omniverse/local-02`). 관측 GPU최대샘플100%/3401MiB이며 웹 실시간 서버 스트리밍은 아니다.
- [x] 다운로드한 TRELLIS.2/DINOv3/RMBG/decoder를 실제 Commons 신창09 원본으로 GPU0에서 실행했다. GLB97,616면 생성·검증 성공, 사진 전경 등대가 주 대상이어서 터빈 자산으로 채택하지 않았다 (`.omo/evidence/local-photo-inference.md`).
- [ ] 모델 기반 실제 터빈 복원·사진별 시설 정합·개별 계측·정밀 측량·Unity/Unreal·실시간 서버 스트리밍은 미완료다. 이 실행 기록으로 전체 Task5/6을 완료로 처리하지 않는다.

## Task 7: Integration verification and handoff

**Files:** `tests/integration.rs`, `README.md`, 확정된 제품 파일.

- [x] fmt, clippy `-D warnings`, locked tests(39 unit + 1 shutdown + 3 CLI = 43), Docker build/smoke 통과. `var/verification/cache-final-tests.log`, `cache-docker-build.log`, `cache-docker-smoke.log`, `cache-compose-up.log` 및 이전 단계 기록 참조.
- [ ] 앱↔실제 bridge/전용 Redis는 GIS·timeline·exact state·WS snapshot까지 통합 확인했다. Manual tunnel disconnect/recovery is verified; automatic startup and renderer connection remain unverified, so Task 7 completion criteria are unmet.
- [x] Compose/Docker HTTP simulation·CLI replay 및 fixture 오류 경계 통과 후, 실제 tunnel에서 health·GIS·timeline·두 exact state·WS snapshot 및 SSH master 단절/수동 복구를 확인했다. Real full-day gap handling은 위 기록과 같으며 renderer 미연결이다. 운영 DB/service는 변경·중지하지 않았다.
- [ ] 현재 README에는 브릿지·전송·방법론을, GPU README에는 제품 실행·추론·렌더링·실제 검사·미확보 입력을 기록한다. `.env`·연결 문자열이 빌드 context·로그·브라우저 산출물에 없는지 확인한다.
- [ ] 완료 기준: 백엔드 계약·수급/ESS 정확성·Docker 실행은 각각 확인하고, 전체 디지털 트윈 완료는 Tasks 5–6의 실제 사진 기반 3D 통합까지 충족했을 때만 선언한다.

## Self-review and Execution Handoff

- 계획은 원천 관측/집계 시나리오를 먼저 구현하고, 기획서 G4/G5 및 S2–S4의 전기 계통 모델은 후속 범위로 유지한다.
- 사용자의 실제 3D 요구는 유지한다. GPU 모델 추론·자산 교환·엔진/API 연동을 실제로 확인하는 것이 남은 통합 조건이다.
- 전체 기획서의 광역 지형 확대·전체 시설 상세·FPS 기준은 첫 장면 통합 다음 단계다. 이 계획의 백엔드 검증만으로 G0–G3 전체가 완료되지는 않는다.
- 작성자가 인터페이스·상수·실패 경로·기획서 대응을 자체 검토한다. 문서 검토 후 **본 세션 직접 구현** 또는 **하위 에이전트별 구현/검토**를 선택한다. 직접 구현은 API·snapshot·WS·시뮬레이션이 같은 계약을 공유하는 이 규모에서 권장한다.
- 서버별 작업 위치: Task 0 bridge는 데이터 서버 `/home/dlwhdtmd/energy-digital-twin`, Tasks 1–6 제품 코드는 GPU 작업 공간 `/home/user/Energy-Digital-Twin` (`feat/gpu-infra`), 수집 데이터는 별도 `.worktrees/data`다. Task 7은 양쪽 연결 검증이다. 데이터 서버 저장소에는 제품 백엔드·시뮬레이션·3D 코드를 구현하지 않는다.
