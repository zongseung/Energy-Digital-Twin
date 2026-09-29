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
