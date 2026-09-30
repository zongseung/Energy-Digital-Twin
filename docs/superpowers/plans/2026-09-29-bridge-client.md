# GPU 브릿지 수신 구현

**Goal:** 승인된 기획서 16.4–16.5, 18.6에 따라 GPU 앱을 완성된 브릿지 계약에 연결한다.
**Architecture:** 기존 Rust 프로세스가 HTTP를 조회하고 단일 WS 연결을 watch로 공유한다. Redis TTL은 GIS 3600초, 최신 30초, 시점/목록 300초다. WS 상태 변경·재접속마다 관측 캐시 세대를 바꾸고 WS 단절 시 관측 캐시를 우회한다.
**Tech Stack:** 기존 axum/reqwest/Redis/serde, tokio-tungstenite 및 futures-util.

기준 브릿지: 사용자 지정 `origin/feat/async-data-bridge`, `9a94596ee74addf3652341cf23ae9fd293b1b245`. 이 커밋의 브릿지는 앞서 확인한 d4e2aa7과 동일하며 기획서에 Task 0 완료 기록이 추가됐다. 기존 기획서가 충분하며 새 제품 기획서는 필요 없다. 인프라 구현은 사용자 지시에 따라 현재 `feat/gpu-infra`에서 계속하고 원천 DB·브릿지 코드는 변경하지 않는다.

- [x] `src/health.rs`: 실제 `ready/hub_ready/demand_ready` 계약을 검증한다. 준비 성공 및 모순된 상태에 대한 회귀 검사를 실행한다.
- [x] `src/bridge.rs`, `src/bridge/{protocol,http,stream}.rs`: state/timeline/assets 조회, UTC offset·범위·응답 크기·schema 검증, GIS 캐시 hit/miss/장애 우회, 단일 WS 재접속·초기 상태·정정·장애 전달을 구현한다. 요청한 정확한 시각과 다른 응답은 거부한다.
- [x] `src/config.rs`, `src/main.rs`, `Cargo.toml`, `compose.yaml`, `.env.example`: origin 허용 목록, WS 종료 신호와 프로세스 생명주기를 연결한다. loopback과 기존 오프라인 계산은 유지한다.
- [x] 실제 TCP의 계약 재현 서버로 두 시점·NULL/0·정정·재접속·잘못된 응답·장애, 실제 앱 Redis로 cache hit/miss/단절을 검사한다. 합성 fixture는 실측 연결 성공으로 보고하지 않는다.
- [x] fmt/clippy/tests, Docker build와 실제 서비스 health를 확인하고 `docs/infrastructure.md`와 README를 갱신한다.
- [x] DINOv3 다운로드와 공식 checksum 검증 결과를 기록한다.

실제 원천 연결 검증은 데이터 서버에서 reverse SSH tunnel을 연 뒤 수행한다. 현 시점 GPU `127.0.0.1:18091` 연결은 거부된다. 모델 추론·엔진 import·HTTP 시뮬레이션은 이 수신 단계와 별도다. 커밋·푸시는 요청받지 않았다.


## 검증 결과 (2026-09-29)

- `feat/gpu-infra`를 유지했다. `feat/async-data-bridge@9a94596`은 읽기 전용 계약/기획 기준이며 merge/cherry-pick/commit/push하지 않았다.
- fmt·Clippy `-D warnings`, 실제 앱 Redis를 포함한 31개 unit + 실제 프로세스 종료 1개 + CLI 3개, 총 35개 통과. `var/verification/bridge-tests.log`.
- 독립 코드 리뷰에서 발견한 WS 종료 race와 GeoJSON 형상 검증을 수정했다. 회귀 검사는 수정 전 실패/수정 후 성공. 최종 리뷰 APPROVE: `.omo/evidence/bridge-code-review.md`.
- Docker 최종 build·Compose 설정·API 갱신 및 healthy 확인. 실제 컨테이너의 live 200, 터널 부재 health/state/assets 503, 잘못된 시간/range 422, WS disconnected 및 허용 밖 Origin 403 확인. `var/verification/bridge-docker-build.log`, `bridge-docker-smoke.log`.
- 서비스는 `BRIDGE_BASE_URL=http://127.0.0.1:18091`로 실행한다. 확인 당시 이 포트의 listener가 없어 실제 원천 GIS/두 시점 대조는 미완료다. 데이터 서버의 reverse SSH tunnel 준비가 필요하다.
- DINOv3 6개 파일 다운로드/공식 checksum 검증 완료. 나머지 모델도 검증 완료이며 추론은 실행하지 않았다.
- 사진/지형 작업자는 별도 `data` 브랜치의 `.worktrees/data`에서 사진 3장과 지형 원본/파생 6개를 수집했다. root가 두 검증 스크립트를 재실행해 통과했다. 상세는 해당 작업 공간의 `data/README.md`. 원본 바이너리는 Git에서 제외했다.
- OMO LSP는 daemon 30초 timeout으로 검증하지 못했다. Cargo 진단을 LSP 성공으로 대체 보고하지 않는다.

자체 검토: HTTP 전송/캐시/WS/protocol 책임을 분리했고, 원천 JSON 검증 뒤에만 앱 응답과 캐시에 전달한다. GIS 원천 속성은 경계에서 검증 후 원문 byte를 보존한다. 새 production unwrap/unsafe와 새 큐/DB는 없다. `protocol.rs` 208, `health.rs` 242 pure LOC로 경고 구간이며 다음 기능 추가 시 GIS validator/health tests를 별도 파일로 분리한다. 실측 연결·HTTP 시뮬레이션·CUDA 추론·엔진 import 완료를 주장하지 않는다.
