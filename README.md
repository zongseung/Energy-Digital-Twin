# Jeju Energy Digital Twin

Rust GPU 서버 앱, 브릿지 HTTP/WebSocket 수신, Redis 캐시와 수급·ESS 계산을 제공합니다. 브릿지 기준은 `feat/async-data-bridge@3cc3a3b`이며 HTTP/WS 계약은 `9a94596`과 같습니다. 인프라 코드는 `feat/gpu-infra`, 수집·검증 코드는 별도 작업 공간의 `data` 브랜치에서 개발합니다.

```bash
# 기존 .env 대신 예제 설정을 명시합니다. Linux host network 사용.
docker compose --env-file .env.example up --build -d
curl -i http://127.0.0.1:8090/health/live
curl -i http://127.0.0.1:8090/api/v1/health

# HF 접근 승인 후 모델을 다운로드·검증합니다.
./scripts/models.sh download all
./scripts/models.sh verify all

# 모델·브릿지 없이 합성 프로파일로 계산합니다.
cargo run --locked -- simulate examples/scenario.json > result.json
```

브릿지가 연결되지 않았으면 `/health/live`는 200, `/api/v1/health`는 503이 정상입니다. `state`, `timeline`, `assets`, `ws`와 `POST /api/v1/jeju/simulate` 경로를 제공합니다. [SSH local forward](docs/bridge-client.md)를 연 뒤 아래 설정으로 연결합니다. [HTTP/CLI 시뮬레이션](docs/simulation.md)은 동일한 Rust 계산기를 사용합니다.

현재 기본 화면은 [탐라–한림 통합 3D](docs/estimated-twin.md)입니다. 실제 VWorld 영상과 Copernicus DSM 고도 위에 풍력10기·PV3곳·변전소·송전 구조를 함께 표시합니다. 시점 버튼으로 같은 일부 구역을 탐색하고, 하단 패널에서 제주 수급 집계를 최신·과거(실제 존재 시각)·시나리오(`/simulate`, 실측 아님) 모드로 확인합니다. 시설 외형·패널 수·배치는 추정이며 개별 계측은 미확보입니다.

웹은 접속 기기의 WebGL을 사용하고, 같은 장면의 서버 GPU1 RTX 결과도 제공합니다. 다운로드한 TRELLIS.2/DINOv3/RMBG는 실제 신창 사진으로 GPU0 추론을 실행했지만, 결과가 전경 등대 중심이어서 풍력 자산으로 채택하지 않았습니다. [실행·원천·GPU·검증 범위](docs/estimated-twin.md)를 확인하세요.

```bash
BRIDGE_BASE_URL=http://127.0.0.1:18091 docker compose --env-file .env.example up --build -d api
curl -i http://127.0.0.1:8090/api/v1/health
curl -i http://127.0.0.1:8090/api/v1/jeju/state
```

- [인프라 실행, 모델·엔진·데이터 준비 안내](docs/infrastructure.md)
- [TRELLIS.2 오프라인 GPU 배치](inference/README.md)
- [Sol: vLLM·Nginx 검토](docs/serving-review.md)
- [Sol: 캐시 실측과 수정 검증](docs/cache-review.md)
- [수급·ESS 계산 입력과 결과](docs/simulation.md)
- [브릿지 연결 계약과 검증 범위](docs/bridge-client.md)
- [실제 지리 자료 전송·조회 결과](docs/source-geography.md)
- [브라우저 3D Mock 실행](docs/mock-rendering.md)
- [탐라 추정 시설 3D 실행·검증](docs/estimated-twin.md)
- [신창 실제 공간 자료 기반 3D 실행](docs/site-rendering.md)
- [Omniverse GPU Mock 실행](docs/omniverse-mock.md)
- [실제 건물 3D 데이터 원천 조사 (2026-09-30)](exa-results/jeju-real-building-3d-sources-2026-09-30.md)
- [기획서](jeju_power_grid_digital_twin_design.md)
- [이번 구현 범위와 검증 기록](docs/superpowers/plans/2026-09-29-infrastructure.md)

```bash
cargo fmt --all -- --check
cargo clippy --all-targets --locked -- -D warnings
cargo test --locked
TEST_REDIS_URL=redis://127.0.0.1:6380/0 cargo test --locked -- --include-ignored
```
