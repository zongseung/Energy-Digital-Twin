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

현재 기본 화면은 [탐라–한림 통합 3D](docs/estimated-twin.md)의 전체 폭 지도입니다. 실제 VWorld 영상과 Copernicus DSM 고도 위에 풍력10기·PV3곳·변전소·송전 구조를 함께 표시합니다. 시설 또는 지도 표식을 클릭하면 상세가 열리며, 개별 가동 상태와 출력은 계측 미연결로 표시합니다. 기상·레이어·시설 목록·제주 전체 수급은 지도 위 도구로 엽니다. 지역 분석에는 기존 최신·과거·시나리오(`/simulate`, 실측 아님) 기능이 있습니다. 모바일 상세는 아래 시트에서 펼칩니다. [승인 디자인](docs/superpowers/specs/2026-09-30-map-first-web-design.md)을 확인하세요.

지도 조작은 왼쪽 드래그 이동, 오른쪽 또는 Shift+왼쪽 드래그 회전, 커서 중심 휠 확대·축소입니다. 상세의 ‘가까이 보기’와 시점 메뉴는 기존 구역 안에서 이동합니다. 패널 위 휠은 정보를 스크롤하고, Esc 또는 닫기 버튼으로 지도에 돌아갑니다. 시설 외형·패널 수·배치는 추정이며 회전 시연은 실제 가동 상태가 아닙니다.

시설 선택 시 인근 기상청 관측소의 바람·기온·습도·이전 60분 누적 강수를 같은 관측소·시각 기준으로 표시합니다. 결측은 `—`, 원천 실패·관측/수신 지연은 마지막 값과 지연 상태로 구분합니다. 기존 `/api/v1/jeju/wind`, `/wind/stations`, `/wind/ws`에서 nullable 기상 필드와 `weather_status`를 제공합니다. 일사·시설 출력 계측은 이 관측으로 대체하지 않습니다. [실제 설비·기상 기반 기획서](jeju_power_grid_digital_twin_design.md#19-현재-우선-계약-실물-pv관측-기상물리-모델의-연결-2026-09-30)와 [자료 확보 조건](docs/real-facility-weather-sources.md)을 확인하세요.

‘제주 전체 관측소’를 펼치면 기온·습도·1시간 강수 열도 확인할 수 있습니다. 좁은 패널에서 표를 좌우로 스크롤합니다. 일사량 열은 현재 AWS 원천 미제공으로 `—`이며 별도 일사 자료는 아직 연결되지 않았습니다. 로컬 preview의 `http://localhost:8080/`와 `http://127.0.0.1:8080/` 모두 WS를 허용합니다.

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
