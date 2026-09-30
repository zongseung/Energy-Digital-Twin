# GPU 인프라와 다음 연결 단계

## 현재 실행 범위

`jeju-twin`은 단일 Rust binary다. CPU에서 HTTP 상태 API를 제공하며 DB에 직접 접속하지 않는다. 앱 Redis는 재생성 가능한 캐시로서 디스크 영속화를 끄고 메모리를 256MB로 제한했다. 모델·생성 자산은 캐시에 저장하지 않는다.

| 요청 | 의미 | 상태 코드 |
|---|---|---|
| `GET /health/live` | API 프로세스 실행 | 200 |
| `GET /api/v1/health` | 브릿지 원천 준비 및 Redis PING | 브릿지 미설정·실패 503; Redis만 실패 200 + degraded |

`feat/async-data-bridge@9a94596`과 대조한 health 계약은 `{"status":"ready","hub_ready":true,"demand_ready":true}`다. 두 원천이 모두 true일 때만 준비 완료다. 원천 실패, 알 수 없는 status, JSON 오류, 16KiB 초과, 3초 초과, HTTP 실패는 503으로 처리한다. 앱 health와 관측의 schema/신선도 검증은 별개이며 조회 경로의 검증은 [브릿지 연결 문서](bridge-client.md)를 따른다.

응답 예: `{"schema_version":1,"status":"unavailable","bridge":"not_configured","redis":"ok"}`.

### 실행과 설정

```bash
docker compose --env-file .env.example config --quiet
docker compose --env-file .env.example up --build -d
docker compose --env-file .env.example ps
curl -i http://127.0.0.1:8090/api/v1/health
docker compose --env-file .env.example down
```

이 명령은 이 프로젝트의 API·Redis만 관리한다. 기존 DB·수집기·Redis에는 연결하지 않는다. `.dockerignore`는 Cargo 파일과 Rust 소스만 build context에 허용한다.

실제 설정은 `.env.example`을 **새 파일** `.env.runtime`으로 복사해 편집하고 모든 compose 명령에 `--env-file .env.runtime`을 사용한다. 기존 `.env`는 사용하지 않는다. 원천 DB 연결 문자열과 SSH 비밀번호는 제품 설정에 필요하지 않다.

| 변수 | 기본값 | 제약 |
|---|---|---|
| `BIND_ADDR` | `127.0.0.1:8090` | loopback·0 이외 포트만 허용; 인증 없는 개발 API |
| `BRIDGE_BASE_URL` | 비어 있음 | HTTP(S) origin만 허용; userinfo·query·fragment·하위 경로 거부 |
| `REDIS_URL` | `redis://127.0.0.1:6380/0` | 앱 전용 캐시 |
| `RUST_LOG` | `jeju_twin=info` | 로그에 설정 URL·응답 원문은 기록하지 않음 |
| `ALLOWED_ORIGINS` | 비어 있음 | 사용자 WS의 브라우저 Origin 허용 목록, 쉼표 구분·정확히 일치 |

Compose API는 Linux host network를 사용해 호스트의 SSH 터널을 사용한다. Redis 공개 포트는 loopback 6380에 고정한다. 포트 변경 시 Compose의 Redis port와 `REDIS_URL`을 함께 변경한다. 원격 접속은 SSH local forwarding으로 시작한다.

Rust 단독 실행은 `.env`를 자동으로 로드하지 않는다:

```bash
cargo run --locked -- --help
cargo run --locked -- check-config
cargo run --locked -- serve
```

## 모델 준비

공식 [TRELLIS.2 pipeline](https://huggingface.co/microsoft/TRELLIS.2-4B/blob/af44b45f2e35a493886929c6d786e563ec68364d/pipeline.json)의 의존성을 [manifest](../models/manifest.tsv)에 SHA로 고정했다. 공식 Python/CUDA 배치 프로세스를 Rust API와 분리해 실행한다.

| 키 | HF 모델 | 역할 / 조건 |
|---|---|---|
| trellis2 | [microsoft/TRELLIS.2-4B](https://huggingface.co/microsoft/TRELLIS.2-4B) | 사진→mesh/PBR, MIT |
| decoder | [microsoft/TRELLIS-image-large](https://huggingface.co/microsoft/TRELLIS-image-large) | sparse structure decoder의 JSON·safetensors 두 파일, MIT |
| dinov3 | [facebook/dinov3-vitl16-pretrain-lvd1689m](https://huggingface.co/facebook/dinov3-vitl16-pretrain-lvd1689m) | 이미지 특징 추출, DINOv3 조건 및 접근 승인 필요 |
| rmbg | [briaai/RMBG-2.0](https://huggingface.co/briaai/RMBG-2.0) | 배경 제거, 별도 BRIA 조건 및 접근 동의 필요; 상업적 사용 조건을 모델 카드에서 확인 |

TRELLIS 본체의 MIT 라이선스가 추가 모델에 적용되는 것은 아니다. HF 계정에서 해당 모델의 조건을 확인·동의하고, 승인 후 터미널에서 로그인한다. 토큰을 채팅·명령 인자·저장소에 넣지 않는다.

```bash
uvx --from huggingface-hub==2.0.0 hf auth login
./scripts/models.sh plan all
./scripts/models.sh download all
./scripts/models.sh verify all
```

먼저 공개 모델만 받으려면 `all` 대신 `public`, 특정 모델만 받으려면 표의 키를 사용한다. 공식 CLI가 중단된 다운로드를 재사용한다. `download`는 전체 선택 범위의 dry-run 성공 후 시작하며, `verify`는 원격 checksum을 검사한다. 모델 다운로드는 코드를 실행하거나 추론 환경을 설치하지 않는다.

기본 위치는 `var/models/<키>/<revision>/`이며 `MODEL_ROOT`로 변경할 수 있다. 모델을 API 이미지에 넣지 않는다. 생성 결과는 `var/generated/`에 저장한다. 두 경로는 Git에서 제외한다. 모델 변경 시 manifest의 SHA와 검증 기록을 함께 갱신한다.

2026-09-29 이 호스트에서 네 모델 모두 다운로드와 공식 checksum 검증을 완료했다. TRELLIS.2 22개, decoder 지정 2개, DINOv3 6개, RMBG 19개 파일이다. 기록은 `var/verification/model-download.log`, `decoder-http.log`, `dinov3-download.log`에 있다. 모델 접근 승인과 파일 검증은 추론 실행 성공을 뜻하지 않는다.

**추론 연결:** [GPU 배치 runner](../inference/README.md)가 임시 pipeline 설정에서 decoder·DINOv3·RMBG 참조를 각 로컬 revision 경로로 연결한다. 고정 코드/native extension 이미지 빌드와 네트워크 없는 컨테이너의 공식 예제 추론·GLB 생성/검사가 성공했다. RMBG는 고정한 로컬 `trust_remote_code=True` 코드를 사용한다. 실제 제주 시설 복원·치수/축 정렬·엔진 import는 아직 미검증이다.

추론 코드 기준 SHA: `75fbf0183001ed9876c8dbb35de6b68552ee08bd` ([공식 소스](https://github.com/microsoft/TRELLIS.2/tree/75fbf0183001ed9876c8dbb35de6b68552ee08bd)). PyTorch 2.6.0/cu124 기반으로 실제 빌드한 일반 의존성은 `inference/requirements.lock`, CUDA extension과 이미지 revision은 `inference/Dockerfile`에 고정했다.

## GPU와 엔진

현재 확인: A6000 49140MiB 두 장, driver `535.183.01`. PyTorch 2.6.0/CUDA 12.4 컨테이너에서 GPU 0과 1을 각각 단독 노출해 행렬 연산을 확인했다. CUDA native extension 이미지 빌드도 완료했다. 실제 모델/GLB 결과 검증과 구분한다.

- GPU 0: 자산 한 개씩 생성. GPU 1: 렌더러. Rust API·첫 수급 계산은 CPU.
- [공식 모델](https://github.com/microsoft/TRELLIS.2)은 Linux·최소 24GB VRAM·CUDA Toolkit을 요구하며 A100/H100에서 검증했다. A6000 속도·최대 해상도는 실제 자산으로 측정한다.
- [Omniverse Kit 106.5 Mock](omniverse-mock.md)을 격리 환경에 설치하고 기존 535 드라이버의 GPU 1에서 실제 RTX PNG/USD 출력을 확인했다. 이후 [탐라 추정 시설 GLB→OpenUSD](omniverse-twin.md)에서 시설 10기·부품·재질 보존과 RTX 두 시점을 확인했다. 최신 Kit 호환을 가정하거나 호스트 드라이버를 변경하지 않았다. 정밀 현장 정합은 남아 있다.
- Unity glTFast·Unreal은 동일 GLB의 추가 import 검증 대상이다. 엔터프라이즈 기능·라이선스·runtime 지원은 자산 import와 별도다.

## 브릿지·엔진 연결에 필요한 데이터

| 구간 | 필요한 데이터 | 검증 기준 |
|---|---|---|
| 브릿지 health | 원천 준비 여부와 실패 상태 | 위 health 계약 대조, 원천 실패 503 |
| 관측 snapshot | `schema_version=1`, offset 포함 `observed_at`, `source`, `quality_flags`, nullable `demand_mw`, `supply_capacity_mw`, `wind_mw`, `solar_mw`, `renewable_total_mw` | 0/NULL 구분, 비유한값 NULL+flag, 15분 지연 표시 |
| GIS | GeoJSON, `hub:<table>:<source_id>` ID, 시설 종류, 기준일·출처·단위, HVDC geometry | 동일 ID 보존, 지역 합계를 개별 발전량으로 배분하지 않음 |
| 이력·WS | `[start,end)` 최대 7일/2016시점, 정확한 시점 state, 같은 시각 정정 version | UTC offset 동치, 없음 404, 입력 오류 422, 원천 실패 503 |
| 자산 입력 | 시설 ID·사진 ID·사용 조건·해시·대상 전체 모습; 실측 치수 | 생성 허용 사진만 사용. 현재 참고 사진 두 장은 허용 미확인으로 생성 입력에 사용하지 않음 |
| 공간 정합 | WGS84 위치, ENU 원점, 고도 기준, meter, up/forward axis, 실측 scale | 임의 GIS 배치나 물리 크기 추정 금지; 독립 기준점과 확인 |
| 자산 manifest | 시설/사진 ID, 생성 모델·code SHA, seed·해상도, GLB/USD hash, 생성시간·VRAM, 재질/축 변환 | renderer prim/node ↔ 시설 ID 일대일 보존 |
| 지역 수급·ESS | 5분 프로파일, 도내 비재생 G, HVDC별 H·가용성·한계, ESS MWh/MW·효율·초기에너지 | 하루 288구간, 결측 거부, 에너지 보존. supply capacity를 발전량으로 사용하지 않음 |
| 향후 선로 계산 | bus/branch 연결, R/X, 정격, baseMVA, slack, tap, Q 한계 | 별도 확보 전에는 실제 선로 흐름·전압을 제공하지 않음 |

브릿지 HTTP/WS 수신·캐시 구현과 실행 절차는 [브릿지 연결 문서](bridge-client.md)에 있다. GPU→데이터 서버 SSH local forward로 실제 두 시점·GIS·WS 대조와 [관측 순부하 계산](simulation.md)을 확인했다. [구역별 12종 지리 자료](source-geography.md)는 `data` 작업 공간에 전송·검증했다. [두 Mock 렌더러](mock-rendering.md)는 실행했으며 실제 ESS 운전·엔진 실측 데이터 연결은 미검증이다.
