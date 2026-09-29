# 현재 서버 데이터 브릿지

기존 Hub GIS와 Demand 제주 수급 DB를 SELECT하고, GPU 서버에 HTTP/WebSocket으로 전달하는 작은 Rust 서비스다. DB 조회·폴링·전송은 Tokio에서 비동기로 실행한다. 현재 서버에는 앱 Redis·시뮬레이션·3D 제품 코드를 추가하지 않는다.

## 실행

프로젝트 루트에서 실행한다. 현재 서버의 `bridge/.env`에는 기존 DB 연결 설정을 준비했다. 새 환경에서는 `.env.example`을 복사하고 연결 값을 설정한다. 루트 `.env`의 API 키·SSH 비밀번호는 브릿지에 필요하지 않다. `.env`는 Git과 Docker 빌드 입력에서 제외한다.

```bash
cargo build --manifest-path bridge/Cargo.toml --release --locked
docker compose --env-file bridge/.env -f bridge/compose.yaml up -d --build
curl --fail http://127.0.0.1:8091/api/v1/health
```

Docker 이미지는 **현재 Linux 서버에서 빌드한 실행 파일**을 담는다. Rust 컴파일러를 이미지에 다시 설치하지 않는다. 다른 OS/아키텍처의 실행 파일은 그대로 복사하지 않고 해당 Linux 환경에서 다시 빌드한다. DB 컨테이너의 기존 `src_energy-hub-net`, `pv-pipeline-network`가 필요하다. 새 DB나 iSCSI 마운트를 만들지 않는다.

```bash
docker compose --env-file bridge/.env -f bridge/compose.yaml logs --tail 30
docker compose --env-file bridge/.env -f bridge/compose.yaml stop
```

## 조회 계약

| 경로 | 응답 |
|---|---|
| `GET /api/v1/health` | 두 원천의 준비 상태; 장애이면 503 |
| `GET /api/v1/jeju/state` | 최신 실제 관측 |
| `GET /api/v1/jeju/state?at=<RFC3339>` | 정확히 일치하는 관측; 없으면 404 |
| `GET /api/v1/jeju/timeline?start=...&end=...` | 실제 존재하는 시각만, `[start,end)`, 최대 7일·2016행 |
| `GET /api/v1/jeju/assets` | 원천 ID·속성·전체 geometry를 보존한 GeoJSON |
| `WS /api/v1/jeju/ws` | 접속 즉시 전체 상태, 이후 관측·같은 시점 정정·원천 상태 변화 |

```bash
curl --fail http://127.0.0.1:8091/api/v1/jeju/state
curl --fail --get http://127.0.0.1:8091/api/v1/jeju/state \
  --data-urlencode 'at=2026-09-29T21:15:00+09:00'
curl --fail --get http://127.0.0.1:8091/api/v1/jeju/timeline \
  --data-urlencode 'start=2026-09-29T00:00:00+09:00' \
  --data-urlencode 'end=2026-09-30T00:00:00+09:00'
```

입력 시각에는 offset이 필요하고 응답은 UTC다. 원천 `ts`는 timezone 없는 KST 컬럼으로 확인했으므로 `SOURCE_TIMEZONE=Asia/Seoul`을 필수로 지정한다. 누락 시각을 보간하거나 가까운 관측으로 대체하지 않는다. 최신 자료가 15분 이상 오래되면 `source_delayed`를 표시한다.

`supply_mw`는 공급 가능 용량이므로 API에서는 `supply_capacity_mw`로 전달한다. 모든 수급 값의 단위는 MW다. 0과 NULL을 구분하며 NaN/무한대는 NULL과 `quality_flags`로 전달한다. GIS ID는 `hub:<table>:<source_id>`다. 동일해 보이는 선로도 원천 ID별로 유지하고 HVDC 본토 경로를 자르지 않는다. GIS만으로 전기적 연결이나 선로별 전력을 만들어내지 않는다.

## GPU 서버의 비동기 수신

현재 서버는 `127.0.0.1:8091`에만 공개한다. 아래 터널은 **아직 상시 실행하지 않았으며**, GPU 앱 구현 단계에서 설정한다. SSH 키와 확인된 host key를 사용해 현재 서버에서 실행한다.

```bash
ssh -p 10000 -NT -o StrictHostKeyChecking=yes \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
  -R 127.0.0.1:18091:127.0.0.1:8091 user@192.9.59.208
```

GPU 서버의 Rust 앱은 `http://127.0.0.1:18091`과 `ws://127.0.0.1:18091/api/v1/jeju/ws`를 사용한다. Docker 앱에서는 Linux host 네트워크 등으로 이 loopback에 접근하도록 구성해야 한다. GPU 앱의 네트워크·재접속·Redis 구성은 후속 Tasks 1–3에서 구현한다.

수신 루프 예제다. GPU 앱에 `tokio`, `tokio-tungstenite`, `futures-util`, `serde_json`을 추가한 뒤 사용할 수 있다. 재접속마다 브릿지가 전체 상태를 보내므로 이력 큐를 만들 필요는 없다.

```rust
use futures_util::{SinkExt, StreamExt};
use tokio_tungstenite::{connect_async, tungstenite::Message};

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let (mut socket, _) =
        connect_async("ws://127.0.0.1:18091/api/v1/jeju/ws").await?;
    while let Some(message) = socket.next().await {
        match message? {
            Message::Text(text) => {
                let value: serde_json::Value = serde_json::from_str(&text)?;
                // status/source_unavailable이면 이전 값의 observed_at을 유지한다.
                println!("{} {}", value["type"], value["observed_at"]);
            }
            Message::Ping(data) => socket.send(Message::Pong(data)).await?,
            Message::Close(_) => break,
            _ => {}
        }
    }
    Ok(())
}
```

WS는 최신 상태 하나만 유지하고 최대 32개 연결·16KiB 수신·5초 송신 제한·30초 ping을 적용한다. `state_version`은 프로세스 안에서만 유효하며 재시작 후 초기화된다. DB 실패 시 `type=status`, `source_unavailable`과 마지막 성공 시각/값을 유지한다. 한 원천 폴링 task가 기본 60초마다 조회한다. `ALLOWED_ORIGINS`가 비어 있으면 Origin이 있는 브라우저 연결은 거부하고, SSH 터널의 native GPU 클라이언트는 허용한다.

DB마다 최대 4개 연결과 3초 획득 제한, SELECT에 12초 클라이언트 제한·10초 서버 제한을 둔다. 취소된 SQLx 연결이 풀을 점유하지 않도록 각 SELECT 연결을 종료한다. 현재 수신 규모에서는 단순한 정리 방식을 사용하며, 높은 요청량이 실제로 생기면 제한 시간 안에서 연결을 재사용하도록 개선한다. 종료 신호를 받으면 WS를 닫고 DB 풀 정리는 최대 3초만 기다린다.

## 검증

```bash
cargo test --manifest-path bridge/Cargo.toml --locked
cargo clippy --manifest-path bridge/Cargo.toml --locked --all-targets -- -D warnings
```

기본 검사는 offset·0/NULL/NaN·날짜 범위·같은 시각 정정·장애/복구·WS 재접속/종료·DB 대기 제한·오류의 비밀값 제거를 확인한다. 실제 DB 검사는 기본 실행에서 제외하며, 호스트에서 접근 가능한 `HUB_DATABASE_URL`, `DEMAND_DATABASE_URL`, `SOURCE_TIMEZONE`을 환경변수로 지정하고 실행한다. Compose용 DB 호스트명은 Docker 네트워크 내부 주소이므로 호스트 테스트에서는 기존 공개 포트인 5437/5433을 사용한다. URL이나 비밀번호를 출력하지 않는다.

```bash
cargo test --manifest-path bridge/Cargo.toml --locked -- --ignored --nocapture
```

실제 DB 검사는 읽기 전용 설정, 두 실제 시점의 다섯 관측값·KST/UTC 변환, 존재하는 시간 목록·404, GIS ID와 HVDC 전체 경로를 확인한다. 운영 DB 수정·정지 없이 수행한다. GPU 터널·앱 Redis·시뮬레이션·사진 기반 3D 통합은 이 브릿지 검사에 포함하지 않는다.

## 제주 지리 데이터 수집

Rust 비동기 CLI `collect_geography`가 기존 Hub GIS·DEM을 추출하고 루트 `.env`의 `vworld_key`로 VWorld 도로명주소 건물·해안선·시군구 경계를 받는다. API·DB·DEM 작업은 병렬로 실행하며 API 페이지 요청은 순서대로 처리한다. GPU는 필요 없다. 결과는 **`/mnt/iscsi/energy-digital-twin/geography/jeju`**에 저장한다.

프로젝트 루트에서 실행한다. 기존 `bridge/.env`의 `HUB_DATABASE_URL`을 사용하되 호스트 실행을 위해 DB 주소를 `127.0.0.1:5437`로 연결한다. DEM 처리는 이미 설치된 `/mnt/nvme/Energy-hub/.venv/bin/python`의 rasterio를 호출한다. 새 서버에서는 해당 도구와 원천 DEM 경로를 먼저 준비해야 한다. 대상 폴더는 현재 사용자에게 쓰기 권한이 있어야 한다.

```bash
cargo build --manifest-path bridge/Cargo.toml --release --locked --bin collect_geography
flock -n /mnt/iscsi/energy-digital-twin/geography/jeju/.collector.lock \
  ./bridge/target/release/collect_geography
```

| 파일 | 자료·출처 |
|---|---|
| `admin_boundary.geojsonl` | 기존 Hub 시군구 경계, 기준연도·역사 코드 보존 |
| `road.geojsonl`, `landcover.geojsonl` | 기존 Hub 도로·토지피복 |
| `power_line.geojsonl`, `substation.geojsonl` | 기존 Hub 전력 선로·변전소, HVDC 전체 경로 보존 |
| `power_plant.geojsonl`, `pv_facility.geojsonl` | 기존 Hub 발전 시설·태양광 시설 |
| `coastline.geojsonl` | VWorld `LT_L_TOISDEPCNTAH` 해안선 |
| `vworld_admin_boundary.geojsonl` | VWorld `LT_C_ADSIGG_INFO` 시군구 경계 |
| `buildings.geojsonl` | VWorld `LT_C_SPBD` 건물 footprint·주소·층수 |
| `dem_jeju.tif` | 기존 `dem_korea.tif`의 제주 GeoTIFF 부분 추출 |

범위는 경도 126–127°, 위도 33–33.7°, CRS는 EPSG:4326이다. Hub는 bbox 후보를 선택하고 전체 geometry를 보존하므로 범위를 벗어난 geometry도 포함될 수 있다. 전력 선로는 제주 속성도 포함해 본토 연결을 유지한다. 건물은 API 면적 제한에 맞춘 0.03° 격자 816개에서 모든 페이지를 받고 원천 ID로 중복 제거한다. `.geojsonl`은 줄마다 GeoJSON Feature 하나인 UTF-8 JSON Lines이며 FeatureCollection 전체 JSON이나 RFC 8142의 RS 구분 형식이 아니다.

각 파일의 `.metadata.json`에 출처·수집 시각·건수·바이트·SHA-256·품질 정보를 기록하고, 11종 전체 성공 시에만 `manifest.json`의 `complete`가 true다. `.part`는 미완료 결과다. 완료 metadata와 해시가 일치하는 자료는 재사용한다. 완료 metadata가 없는 파일은 다시 생성하고, 기존 완료 파일의 해시가 다르면 오류로 종료한다. `vworld_pages/`의 검증된 API 페이지는 재시작 시 재사용한다. 페이지의 총건수나 ID가 충돌하면 해당 격자 checkpoint를 지우고 실패하며, 같은 명령을 다시 실행하면 그 구역을 새로 받는다. 원천 변경을 자동 추적하는 주기적 수집기는 아니다.

기존 Hub 행정경계는 2018년의 `39010/39020`을 사용한다. API 경계를 별도 보존하며 API 수집일을 자료 기준일로 단정하지 않는다. 건물 층수는 실측 높이가 아니고 DEM의 수직 기준·사용 조건은 추가 확인이 필요하다. 발전/태양광 테이블의 중복 시설 여부와 선로의 전기적 연결은 별도 검증 대상이다. 실제 지역 사진·텍스처·측량 수준 3D·선로별 전력 모델은 이 수집에 포함되지 않는다.

정적 지리 파일은 GPU 장면 준비 단계에서 파일 전송 후 해시를 대조한다. 이 CLI가 만든 파일은 현재 브릿지 HTTP/WS에 자동 공개되지 않는다. 실시간 관측은 기존 브릿지 HTTP/WS를 사용한다.
