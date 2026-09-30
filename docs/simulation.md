# 수급·ESS 시뮬레이션

모델 다운로드나 브릿지 없이 실행하는 CPU 기반 Rust 계산이다. 기획서 17.3–17.4의 지역 집계 모델이며, 결과는 모두 `data_kind=scenario`다. 실제 조류·전압·정전·출력제어량 또는 예측 결과가 아니다.

```bash
cargo run --locked -- simulate examples/scenario.json > result.json
```

CLI `simulate`는 브릿지·Redis 환경변수를 읽거나 서버에 접속하지 않는다. HTTP API는 아래와 같이 브릿지의 관측을 직접 조회한다.

## HTTP 실행

`POST /api/v1/jeju/simulate`에 `run_id`, `start`, `end`, `scales`와 선택적인 `dispatch`, `ess`를 보낸다. `snapshots`와 `source_version`은 서버가 채우므로 요청에 넣으면 422다. 아래는 원천 관측으로 순부하를 계산하는 예다. 실제 관측이 있는 시간 범위로 바꿔 실행한다.

```bash
curl --fail-with-body http://127.0.0.1:8090/api/v1/jeju/simulate \
  -H 'Content-Type: application/json' \
  -d '{"run_id":"net-load-check","start":"2026-09-29T09:00:00+09:00","end":"2026-09-29T09:10:00+09:00","scales":{"demand":1,"wind":1,"solar":1}}'
```

서버는 정확한 5분 시각마다 브릿지 HTTP를 조회하며 캐시를 사용하지 않는다. 동시 실행은 2개, 실행당 원천 요청은 동시에 2개, 전체 수집 제한은 30초다. CPU 계산은 별도 blocking 작업에서 실행한다. 입력은 256KiB 이하이며, 관측 WS 상태를 바꾸지 않는다.

결과의 `input`은 사용한 관측·dispatch·ESS 조건을 포함한다. 서버의 `source_version=sha256:...`은 시간순으로 정렬·정규화한 관측 JSON의 내용 해시다. `acquisition`은 수집 시작/종료 시각과 `individual_uncached_http_reads`를 기록한다. 여러 HTTP 조회 사이에 원천이 정정될 수 있으므로 DB의 원자적 snapshot 버전을 뜻하지 않는다. 같은 입력은 CLI에서 다시 계산할 수 있다.

| HTTP 상태 | 의미 |
|---|---|
| 200 | complete / net_load_only / incomplete — body의 status 확인 |
| 413 / 415 / 422 | body 초과 / JSON Content-Type 아님 / 입력·계산 오류 |
| 429 | 두 실행이 처리 중; 완료 후 재시도 |
| 502 / 503 | 원천 응답·값 오류 / 원천 통신 실패·수집 시간 초과 |

원천의 특정 시각 404 또는 D/W/S의 null은 0으로 바꾸지 않고 `incomplete`로 반환한다. 원천 응답의 오류 본문은 전달하지 않는다.

2026-09-29 실제 터널을 통해 전날 KST 하루를 검사했다. 관측 287개와 23:55 결측 1개를 확인했으며 하루 실행은 `incomplete`, points는 빈 배열, 최종 ESS 에너지는 null이었다. 00:00~23:55 미만의 연속 287구간은 `net_load_only`로 계산했다. 원천 내용 해시는 `sha256:3da4810f5720ac6f6c6dc533680838599aa14e4cb3fc368c8f7ebd1bbc12c080`이다. 실제 G/H dispatch·ESS 제원이 없는 상태여서 ESS 검증은 아래 합성 예제로 한정한다. 증거: `var/verification/live-integration.log`, `live-simulation-day.json`, `live-simulation-contiguous.json`.

## 입력

[예제 JSON](../examples/scenario.json)은 **합성 데이터**이며 실제 제주 설비 제원이 아니다. `run_id`는 호출자가 정한 실행 식별자, `source_version`은 원천 자료의 버전이다. 결과의 `input`에 입력 전체를 보존한다. 파일은 최대 256KiB다.

| 항목 | 규칙 |
|---|---|
| start/end | offset 포함 RFC3339, `[start,end)`, UTC 기준 5분 정각, 최대 24시간/288구간 |
| scales | demand/wind/solar의 유한한 비음수 배율, 모두 명시 |
| snapshots | schema_version=1, source/quality_flags, 시각별 nullable MW; 순서 무관, 중복·범위 밖은 오류 |
| dispatch | 생략 또는 null이면 순부하만 계산. 지정하면 모든 구간의 nonrenewable_mw 및 고유·고정 ID의 HVDC 3개 필요 |
| HVDC | 양수 수입·음수 수출, 각 링크의 min/max 한계 사용. available=false이면 power_mw=0 필수 |
| ess | 선택 사항. capacity/initial/min/max는 MWh, charge/discharge 한계는 MW, 효율 `(0,1]` |

관측 D/W/S 중 하나라도 null이거나 구간의 snapshot/dispatch가 없으면 `incomplete`와 누락 시각을 반환한다. 이 경우 모든 points는 비우고 최종 에너지/SOC를 계산하지 않는다. 중간 결측을 건너뛰거나 0으로 채우지 않는다. 필요하지 않은 supply_capacity/renewable_total의 null은 결측으로 세지 않는다.

G/H 없이 ESS를 요청하거나 시간·수치·SOC 경계를 위반하면 오류다. NaN/Infinity 및 계산 overflow는 거부하며, 효율×5분이 부동소수점에서 0으로 소실되는 입력도 거부한다. 원천 quality_flags는 보존하지만 flag별 사용 가능 여부 판단은 아직 하지 않는다. 사용 가능한 프로파일을 제공하는 책임은 호출자에게 있다.

## 계산과 결과

- 순부하 = 수요 − 풍력 − 태양광. `supply_capacity_mw`를 발전량으로 사용하지 않는다.
- ESS 전 잔차 = 순부하 − 도내 비재생 발전 − HVDC 순수입 합계.
- 부족이면 방전, 잉여면 충전. 잔차·MW 한계·SOC 에너지 여유 중 최소로 제한한다.
- 다음 에너지 = 이전 에너지 + 충전MW×충전효율/12 − 방전MW/방전효율/12.
- ESS 후 잔차 = ESS 전 잔차 + 충전 − 방전. 양수는 추가 공급 필요, 음수는 잉여다.

`baseline`은 배율 1, `scenario`는 지정한 배율이다. 두 사례는 같은 dispatch·ESS 초기 조건으로 각각 독립 계산한다. 각 사례의 `residual_before_ess_mw`가 ESS 없는 비교값이다. HVDC 정지 전후는 서로 다른 입력을 각각 실행해 비교한다.

구간마다 `energy_mwh`와 `soc_percent`는 **구간 종료 시점** 값이며 `observed_at`은 구간 시작이다. 결과 끝에 두 사례의 최종 에너지도 반환한다. ESS가 없으면 충/방전·에너지·SOC 필드는 null, G/H가 없으면 잔차도 null이다.

`status`는 `complete`, `net_load_only`, `incomplete`다. 정상 JSON 결과는 incomplete를 포함해 exit 0, 입력/계산/IO 오류는 stderr와 exit 1이다. 소비자는 exit code뿐 아니라 status를 확인해야 한다.

예제의 순부하는 40→64MW, 공급 가정은 52MW다. 첫 구간 12MW 충전으로 5→5.9MWh, 다음 구간 12MW 방전으로 5.9→4.65MWh가 된다. 최종 SOC는 46.5%다. 송전 손실·ESS 대기 손실·종료 SOC 제약·경제성 최적화는 이 모델에 포함하지 않는다.

```bash
cargo test simulation::
cargo test --test simulation_cli
```
