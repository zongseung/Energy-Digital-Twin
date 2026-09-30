# GPU 서버 인프라 1차 구현

**Goal:** 브릿지 완성 전에 Rust API·앱 Redis 실행 및 HF 모델 준비 경로를 검증한다.
**Architecture:** 현재 A6000 두 장이 있는 작업 공간에 단일 Rust binary를 둔다. 브릿지와 모델 추론은 별도 프로세스다.
**Tech Stack:** Rust 1.94.1, axum, tokio, reqwest, redis, serde; Docker Compose; 공식 hf CLI.

## 범위와 결정

- 사용자 최신 요청에 따라 기존 구현 계획의 Task 1과 Task 5의 모델 준비만 먼저 구현한다.
- bridge/, 원천 DB, GPU 드라이버, 기존 컨테이너는 수정하지 않는다.
- 제품은 CPU, 생성 GPU 0, 렌더러 GPU 1. 기획의 이전 호스트 경로 대신 확인된 현재 GPU 작업 공간을 사용한다.
- 모델 대용량 다운로드는 사용자가 실행한다. 이 작업에서는 revision·접근 조건·dry-run을 확인한다.
- 기존 .env를 읽거나 복사하지 않는다. compose 전용 예제 설정을 사용한다.
- 기획과 이번 요청으로 구현이 승인된 범위이므로 별도 설계 승인/커밋/배포 없이 작업 트리에 결과를 남긴다.

## 작업

- [x] `Cargo.toml`, `src/{main,config,health}.rs`: 설정 파싱, loopback bind, live/ready 분리. HTTP 503(브릿지 미설정/장애), 200+degraded(Redis 장애), timeout·비밀정보 비노출을 테스트한다.
- [x] `Dockerfile`, `compose.yaml`, `.env.example`, ignore 파일: API+전용 Redis, loopback 8090/6380, 모델/자산 영속 경로. `docker compose --env-file .env.example config --quiet`, 실제 build/up/HTTP 검사.
- [x] `models/manifest.tsv`, `scripts/models.sh`, `docs/infrastructure.md`, `README.md`: 4개 모델 revision, CLI plan/download/verify, 라이선스·GPU·엔진·데이터 준비 요건. help/잘못된 입력/dry-run 확인.
- [x] `cargo fmt --check`, clippy, test, build 및 실제 HTTP 상태 전환, GPU 컨테이너 노출을 기록하고 완료 체크한다.

## 증거

- 최초 확인: Rust 1.94.1, RTX A6000 49140MiB ×2, driver 535.183.01, Docker Compose v5.1.3.
- GPU 0: 기존 CUDA 12.0 컨테이너에서 장치 한 개만 노출됨. CUDA extension/추론 성공을 의미하지 않는다.
- Rust 기능 부재 상태 `cargo test` 실패 확인 후 구현. 기본 테스트 5개 통과, 실제 전용 Redis 테스트 1개 별도 실행 통과. clippy 경고 없음.
- Docker build 성공, compose API/Redis healthy. live 200, bridge 미설정 ready 503 + redis ok.
- 모델 공개 dry-run: TRELLIS.2 22개 파일 16.2GB, decoder 2개 파일 147.6MB. DINOv3 dry-run은 접근 승인 필요로 exit 1. 모델 가중치 다운로드는 실행하지 않음.
- LSP daemon은 진단 재요청도 timeout; Rust 컴파일·clippy로 검증. Docker/YAML/Bash LSP 미설치, 설치 없이 compose/build/bash -n으로 검사.
- 최종 `TEST_REDIS_URL=redis://127.0.0.1:6380/0 cargo test --locked -- --include-ignored`: 7 passed, 0 failed, 0 ignored (timeout 3초 포함). fmt/clippy/build/bash 구문 검사 및 git diff --check 통과.
- 실제 HTTP fixture + 전용 Redis: 정상 200/ok → 원천 unavailable 503 → Redis 중단 200/degraded → Redis 복구 200/ok. 원문은 `var/verification/http-smoke.log`. fixture와 시험 API는 종료했다.
- Docker 최종 재빌드/up --wait 성공. API 127.0.0.1:8090 및 Redis 127.0.0.1:6380 healthy 상태로 실행 중. 최종 준비 상태는 bridge 미설정으로 503이다.
- RMBG도 승인 없는 dry-run exit 1. public dry-run 성공, 잘못된 CLI 인자 exit 2, 모델 파일 없는 verify exit 1, 비밀 포함 잘못된 config exit 1 및 값 미노출 확인.
- 독립 작업 트리 코드 검토: [검토 기록](../../../.omo/evidence/infrastructure-code-review.md), blocker 없음. 검토 이후 timeout 테스트 한 개와 shell 지역변수 선언 보완, 전체 재검증 완료. 커밋 SHA에 대한 승인으로 재사용하지 않는다.

## 완료 범위와 남은 외부 연결

이 계획의 인프라·다운로드 준비 구현은 완료했다. 실제 브릿지 연결·관측/WS·시뮬레이션, 모델 전체 가중치 다운로드/검증·CUDA 추론, 엔진 설치·GLB/USD import는 이 단계의 완료 주장에 포함하지 않는다. 모델 가중치는 사용자가 다운로드하며 gated 모델 동의/승인은 사용자 HF 계정에서 진행한다. 기존 DB·서비스·드라이버는 변경하지 않았다.
