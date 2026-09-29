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

Rust 비동기 CLI `collect_geography`가 기존 Hub GIS·DEM을 추출하고 루트 `.env`의 `vworld_key`로 VWorld 도로명주소 건물·건물통합정보·해안선·시군구 경계를 받는다. API·DB·DEM 작업은 병렬로 실행하며 API 페이지 요청은 순서대로 처리한다. GPU는 필요 없다. 결과는 **`/mnt/iscsi/energy-digital-twin/geography/jeju`**에 저장한다.

프로젝트 루트에서 실행한다. 기존 `bridge/.env`의 `HUB_DATABASE_URL`을 사용하되 호스트 실행을 위해 DB 주소를 `127.0.0.1:5437`로 연결한다. DEM 처리는 이미 설치된 `/mnt/nvme/Energy-hub/.venv/bin/python`의 rasterio를 호출한다. 새 서버에서는 해당 도구와 원천 DEM 경로를 먼저 준비해야 한다. 대상 폴더는 현재 사용자에게 쓰기 권한이 있어야 한다.

```bash
cargo build --manifest-path bridge/Cargo.toml --release --locked --bin collect_geography
flock -n /mnt/iscsi/energy-digital-twin/geography/jeju/.collector.lock \
  ./bridge/target/release/collect_geography
flock -n /mnt/iscsi/energy-digital-twin/geography/jeju/.islands-collector.lock \
  ./bridge/target/release/collect_geography --islands
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
| `building_info.geojsonl` | VWorld `LT_C_BLDGINFO` 건물 형상·높이·용도·대장 속성 |
| `dem_jeju.tif` | 기존 `dem_korea.tif`의 제주 GeoTIFF 부분 추출 |

기본 범위는 경도 126–127°, 위도 33–33.7°, CRS는 EPSG:4326이다. `--islands`는 경도 126.2–126.7°, 위도 33.7–34.05°를 `supplements/northern_islands/`에 별도 저장한다. 기존 공식 시군구 경계에 포함된 북쪽 부속 도서를 보완하는 범위다. Hub는 bbox 후보를 선택하고 전체 geometry를 보존하므로 범위를 벗어난 geometry도 포함될 수 있다. 북쪽의 기존 Hub 경계 후보에는 완도·신안도 포함되어 있으므로 장면 준비에서 VWorld의 제주 경계로 실제 영역을 필터링한다. 전력 선로는 제주 속성도 포함해 본토 연결을 유지하며 두 구역 파일을 합칠 때 원천 ID로 중복 제거한다. 건물 두 레이어는 API 면적 제한에 맞춘 기본 816개·북쪽 204개 격자에서 모든 페이지를 받고 각 레이어 안에서 원천 ID로 중복 제거한다. `.geojsonl`은 줄마다 GeoJSON Feature 하나인 UTF-8 JSON Lines이며 FeatureCollection 전체 JSON이나 RFC 8142의 RS 구분 형식이 아니다.

각 파일의 `.metadata.json`에 출처·수집 시각·원천 ID·건수·바이트·SHA-256·품질 정보를 기록한다. 각 구역의 구성된 12종 자료가 모두 성공해야 `manifest.json`의 `complete`가 true다. 제주 관련 모든 종류의 데이터나 3D 복원 완료를 뜻하지 않는다. `.part`는 미완료 결과다. 완료 metadata와 해시가 일치하는 자료는 재사용한다. 완료 metadata가 없는 파일은 다시 생성하고, 기존 완료 파일의 해시가 다르면 오류로 종료한다. `vworld_pages/`의 검증된 API 페이지는 재시작 시 재사용한다. 페이지의 총건수나 ID가 충돌하면 해당 격자 checkpoint를 지우고 실패하며, 같은 명령을 다시 실행하면 그 구역을 새로 받는다. 원천 변경을 자동 추적하는 주기적 수집기는 아니다.

기존 Hub 제주 행정경계는 2018년의 `39010/39020`을 사용한다. API 경계를 별도 보존하며 API 수집일을 자료 기준일로 단정하지 않는다. 주소 건물의 층수는 실측 높이가 아니다. 건물통합정보의 `height`도 현장 측량을 별도 검증한 값은 아니며 원문을 그대로 보존한다. 유한한 양수 높이만 `positive_height_count`에 집계하고 0·누락·음수·비유한값은 높이 미확보로 처리한다. 서로 다른 두 건물 레이어의 ID나 배열 순서를 같은 시설로 간주하지 않고 GPU 준비 단계에서 형상·위치로 대조한다. DEM의 수직 기준·사용 조건, 발전/태양광 중복 시설과 선로의 전기적 연결은 추가 확인 대상이다.

2026-09-29 추가 수집: 기본 구역 건물통합정보 463,524개 중 양수 높이 117,008개, 북쪽 주소 건물 1,621개·건물통합정보 2,333개 중 양수 높이 573개, 도로 36개·토지피복 669개·해안선 244개·DEM 1,801×1,260 픽셀. 양수 높이 여부가 자료의 최신성이나 측량 정확성을 보장하지 않는다.

### 현장 참조 사진

`reference_photos/`에 [신창풍차해안도로](https://www.visitjeju.net/kr/detail/view?contentsid=CNTS_200000000007676), [신창~차귀해안도로](https://www.visitjeju.net/kr/detail/view?contentsid=CONT_000000000500403), [추자도](https://www.visitjeju.net/kr/detail/view?contentsid=CNTS_000000000018441)의 공식 장소 갤러리 사진을 각각 12장씩 저장했다. 페이지의 해당 장소 `photo` 배열만 선택하고 사진 ID로 중복 제거한 뒤 받는다. 리뷰·주변 장소 사진을 해당 장소 사진으로 섞지 않는다. 기존 Python 환경의 Pillow로 파일을 검증하며 네트워크/저장은 `asyncio.to_thread`로 최대 3개 병렬 실행한다. 키는 필요 없다.

```bash
/mnt/nvme/Energy-hub/.venv/bin/python bridge/scripts/collect_reference_photos.py --check
flock -n /mnt/iscsi/energy-digital-twin/geography/jeju/.photos-collector.lock \
  /mnt/nvme/Energy-hub/.venv/bin/python bridge/scripts/collect_reference_photos.py
```

사진별 원본 URL·출처 페이지·크기·해시·수집 시각을 보존한다. 대표 사진 3장을 육안 확인했고 36장 모두 이미지 파일 검증을 통과했다. 기존 파일은 해시 대조 후 재사용한다. 현재 참고용이며 사진별 사용 조건·촬영 시각·카메라 위치·겹치는 촬영 세트는 확인되지 않았다. 텍스처나 생성 모델 입력에 사용하기 전에 조건과 적합성을 확인한다. 사진 갤러리 표본은 제주 전체 현장 촬영이나 정밀 다중 시점 복원 세트를 대신하지 않는다.

정적 지리 파일은 GPU 장면 준비 단계에서 파일 전송 후 해시를 대조한다. 이 CLI가 만든 파일은 현재 브릿지 HTTP/WS에 자동 공개되지 않는다. 실시간 관측은 기존 브릿지 HTTP/WS를 사용한다.

### 추가 전력 자료 수집

`scripts/collect_power_data.py`는 준비용 Python 표준 라이브러리 스크립트다. Rust 브릿지/제품 백엔드에 새 실행 의존성을 추가하지 않는다. DB와 공개 파일을 최대 3개 병렬 수집하고 자체 파일 잠금으로 중복 실행을 막는다. 저장 위치는 쓰기 가능한 기존 iSCSI 폴더의 `/mnt/iscsi/energy-digital-twin/geography/jeju/power/`다.

```bash
python3 -m unittest discover -s bridge/scripts -p 'test_*.py'
python3 bridge/scripts/collect_power_data.py
```

첫 실행에는 공식 논문 Table A2에서 확인한 Markdown 표를 `--routes /path/to/source-table.md`로 제공한다. 수집된 `routes_2023_source_table.md`가 있으면 다음 실행부터 그 파일과 검증된 결과를 재사용한다. 별도 API 키가 없는 공개 다운로드와 로컬 `docker exec energy-hub-db psql`을 사용한다. DB는 명시적인 읽기 전용 트랜잭션으로 조회한다.

2026-09-30에 아래 **9개 파일, 58,603,088바이트**의 내용·해시·metadata를 검사했다. manifest의 `complete`는 이 수집 작업들에만 적용하며 `electrical_twin_ready=false`와 필요한 미확보 항목을 함께 기록한다.

| 결과 | 실제 확보 내용 | 적용 한계 |
|---|---|---|
| `kepco_connections_20260930.jsonl` | 제주 주소 99,860건, 변전소 코드 15개·변압기 코드 쌍 54개·배전선 코드 조합 146개 | 2026-03-23~26 수집 캐시. 좌표·R/X·계량 부하가 없으며 용량 필드의 단위는 미검증 |
| `generation_20251231.csv` | [KPX 연료원별 거래량](https://www.data.go.kr/data/15100214/fileData.do), 2025년 전체 43,800행·5연료원·24시간 라벨, MWh | 개별 발전기 SCADA가 아닌 시장 정산 거래량. 음수 12건을 그대로 보존하고 품질 표시. 시간 라벨의 구간 기준은 별도 확인 |
| `wind_20241231.csv` | [제주 풍력시설 목록](https://www.data.go.kr/data/15047557/fileData.do) 25건, 주소·설비용량 MW | 비식별화된 이름·주소이며 실제 터빈 좌표·높이나 운전 출력이 아님 |
| `curtailment_20260630.zip`, 아래 `01.csv`·`02.csv` | [공식 출력제어 ZIP](https://www.data.go.kr/data/15132422/fileData.do)과 내부 제주 CSV 두 개. PV 125행(2021-10-17~2024-06-03), 풍력 336행(2021-01-13~2024-05-30) | 목록 기준일 2026-06-30과 내부 관측 기간이 다름. PV는 제어 표식, 풍력은 제어 MWh. 연속 시계열로 보간하거나 과거 버전끼리 합산하지 않음 |
| `kpx_jeju_operations_2024.pdf` | KPX 공식 2024년 계통 운영실적, 567,999바이트 | 과거 설비·운영 대조용이며 완전한 현재 계통 case가 아님 |
| `routes_2023_source_table.md`, `routes_2023.jsonl` | [Son & Jang (2023)](https://www.mdpi.com/1996-1073/16/15/5699) Table A2의 번호별 39회선·연결 이름·정격 MVA | 이름 반복·원문 표기를 보존. 현재 GIS와 자동 연결하지 않으며 R/X·정식 bus ID는 없음 |

원본 파일을 수정하지 않고 `.part`에 저장한 뒤 원자적으로 공개한다. 완료 metadata와 SHA-256이 맞는 파일은 재사용하고, 해시가 다르면 중단한다. 같은 날짜의 기존 캐시/공개 스냅샷을 자동 갱신하는 수집기는 아니다. 거래량은 날짜·시간·연료원 중복, 시간창 누락, 비유한값과 단위 변경을 검사한다. 음수 정산값은 의미가 확인되지 않아 시뮬레이션에 바로 대입하지 않는다. ZIP 원본을 보존하면서 제주 CSV만 고정된 출력 파일명으로 꺼내므로 원본 경로를 파일시스템 경로로 사용하지 않는다.

다음 확보 대상은 실제 모선/회선/변압기 연결과 R/X/B·정격·tap, bus P/Q·HVDC별 계측이다. 현장 사진은 겹치는 여러 방향과 치수/위치 기준을 갖춘 별도 촬영 세트가 필요하다.

### 승인된 ASOS·건축HUB API 수집

`scripts/collect_registered_api.py`는 루트 `.env`의 `api_key`로 [ASOS 시간 API](https://www.data.go.kr/data/15057210/openapi.do)와 [건축HUB 표제부 API](https://www.data.go.kr/data/15134735/openapi.do)를 수집한다. 2026-09-30 두 서비스의 `resultCode=00`을 실제 확인했다. 해당 서비스별 활용신청이 필요하며 VWorld 키와는 별개다. 공백·따옴표와 URL 인코딩 키를 처리하고, 키가 포함된 요청 URL·응답 오류 본문을 출력하거나 metadata에 저장하지 않는다. HTTPS 공식 주소만 호출하고 redirect를 거부한다.

```bash
python3 -m unittest discover -s bridge/scripts -p 'test_*.py'
python3 bridge/scripts/collect_registered_api.py --year 2025 --snapshot 20260930
python3 bridge/scripts/collect_registered_api.py --snapshot 20260930 --check
```

저장 위치는 `/mnt/iscsi/energy-digital-twin/geography/jeju/registered_api/20260930/`다. `asos_2025_<지점번호>.jsonl` 4개는 [기상청 공식 지점 목록](https://www.kma.go.kr/jeju/html/observation/observation_info.jsp)의 제주184·고산185·성산188·서귀포189의 2025년 전체 시간 자료이며 풍속·풍향·기압 등 원문 필드와 QC를 보존한다. 시간대는 Asia/Seoul로 해석하며 0과 공란을 구분한다. [공식 QC 설명](https://data.kma.go.kr/data/grnd/selectAsosRltmList.do?pgmNo=36)은 0=정상·1=오류·9=결측이지만 실제 API의 공란 QC를 정상으로 바꾸지 않는다. 지상 풍속을 터빈 허브 풍속이나 미래 예보로 간주하지 않는다.

`buildings_<법정동코드>.jsonl`은 기존 기본·북쪽 주소 건물 파일에서 확인된 186개 법정동 코드를 조회한 표제부다. [공식 건물관리번호 구성](https://eng.juso.go.kr/addrlink/qna/qnaDetail.do?bulletinRefSn=92607&noticeMgtSn=92607&noticeType=QNA)에 따라 `bd_mgt_sn` 첫 10자리를 조회 범위로 사용한다. 이 범위는 현재 제주 전체 법정동 목록의 완전성을 보증하지 않는다. 공식 명세의 `heit` 단위는 m이며 0·공란·비유한값은 높이 미확보다. 대장 PK·지번·도로명주소·층수·용도도 보존한다. **수집된 등록 높이 건수는 기존 GIS 높이 결측을 채운 건수가 아니다.** 대장 PK와 GIS ID를 자동 연결하지 않았으며 지번·주소와 실제 형상으로 대응을 검증해야 한다.

최대 3개 작업을 비동기로 실행하고 ASOS는 999건, 건축HUB는 실제 응답 한도인 100건씩 받는다. 요청별 socket timeout은 30초, 응답 한도는 4MiB다. 페이지별 SHA-256·질의 identity를 검증하고 중복 ID·다른 지역/지점·페이지 누락·총건수 변경을 거부한다. 검증이 끝난 페이지와 집계만 원자 저장하며, 완료 집계는 해시 검사 후 재사용한다. 중단된 집계는 기존 페이지를 API에서 다시 대조한다. 원천이 바뀌면 오류로 종료하고 새 `--snapshot YYYYMMDD`로 별도 수집한다. 제공 API의 페이지 조회 자체는 동일 시점의 원자 스냅샷이 아니므로 그 한계는 metadata에 보존한다.

`--check`는 키나 네트워크 없이 선언된 모든 작업·페이지·결과의 해시와 건수·QC를 다시 검사한다. manifest의 `complete`는 설정된 수집 범위만 뜻하며 `electrical_twin_ready=false`다. 파일과 키는 Git에 넣지 않는다. 정적 파일의 GPU 전송과 브릿지 HTTP/WS 공개는 후속 작업이다.

2026-09-30 전수 검증: JSONL 190개 파일 391,938,526바이트·2,309페이지. ASOS 35,040행 중 풍속/풍향 공란은 고산 10·성산 27시간. 건축물대장 217,844행 중 양수 높이 115,226행, 높이0은 102,616행·음수 2행. 양수라도 250m 초과 4행(최대 4,970m)은 원천 검토가 필요하다. 등록 높이를 실제 형상에 적용하기 전에 주소/동과 geometry 대응을 확인한다. [품질·범위 기록](../jeju_power_grid_data_and_modeling_review.md#20-등록-키를-이용한-asos건축hub-본수집--2026-09-30).
