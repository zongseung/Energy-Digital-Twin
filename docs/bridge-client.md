# GPU 앱의 브릿지 연결

브릿지 기준은 `feat/async-data-bridge@03e02ef`이며 HTTP/WS 계약은 이전 `9a94596`과 같다. 최신 변경은 health 지역변수와 timeline SQL 상수 추출이며 원천 검사 7개를 별도 target에서 통과했다. 인프라 작업 브랜치는 `feat/gpu-infra`다. 브릿지 서버의 DB 검증과 GPU 앱의 실제 원천 통합 검증을 구분한다.

## 연결

데이터 서버의 기존 브릿지는 `127.0.0.1:8091`에 실행된다. GPU에서 데이터 서버 SSH에 접근할 수 있으므로 GPU 쪽에서 다음 local forward를 유지한다. host key와 기존 SSH 인증을 사용한다.

```bash
ssh -p 22 -NT -o StrictHostKeyChecking=yes \
  -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
  -L 127.0.0.1:18091:127.0.0.1:8091 dlwhdtmd@192.9.65.58
```

기획의 reverse forward와 같은 앱 loopback 주소를 제공한다. 현재는 개발 세션의 SSH master로 실행하며 OS 재부팅 후 자동 기동은 구성하지 않았다. 비밀번호는 SSH 접속에만 사용하고 앱에 주입하지 않는다.

GPU 서버에서:

```bash
curl --fail http://127.0.0.1:18091/api/v1/health
BRIDGE_BASE_URL=http://127.0.0.1:18091 docker compose --env-file .env.example up --build -d api
curl --fail http://127.0.0.1:8090/api/v1/health
curl --fail http://127.0.0.1:8090/api/v1/jeju/state
curl --fail http://127.0.0.1:8090/api/v1/jeju/assets
```

브릿지 health는 `status=ready`, `hub_ready=true`, `demand_ready=true`를 모두 요구한다. 앱의 정상 health는 계속 `status=ok`다. 브릿지 health timeout은 3초이며 더 느린 응답도 준비 실패로 처리한다.

## 조회와 스트림

| 앱 경로 | 계약 |
|---|---|
| `GET /api/v1/jeju/state` | 실제 최신 관측, 지연/미래 시각 flag 재평가 |
| `GET /api/v1/jeju/state?at=<RFC3339>` | offset을 UTC로 정규화; 정확한 시각 불일치는 502, 없음 404 |
| `GET /api/v1/jeju/timeline?start=...&end=...` | `[start,end)`, 최대 7일·2016개, 실제 존재하는 정렬된 시각 |
| `GET /api/v1/jeju/assets` | 원천 GIS JSON·ID·속성·전체 geometry 보존 |
| `WS /api/v1/jeju/ws` | 초기 전체 상태, 이후 정정·장애·복구. 과거 시각 재생은 HTTP |

입력 오류는 422, 통신/원천 실패는 503, 잘못된 원천 schema/응답은 502다. DB 오류 본문은 사용자에게 전달하지 않는다. HTTP body 상한은 state 64KiB, timeline 128KiB, GIS 16MiB이며 요청 timeout은 15초다. 수급의 음수는 원천 품질 표시와 함께 보존하고 0과 NULL을 바꾸지 않는다. 공급 가능 용량을 발전량으로 해석하지 않는다.

Redis namespace는 `edt:dev:v1:`이며 브릿지 주소 식별자와 schema를 포함한다. GIS TTL은 3600초, 최신은 30초, 특정 시점/시간 목록은 300초다. 관측 key에는 앱 실행 식별자와 WS 수신 세대를 추가한다. 정정·원천 상태 변화·재접속 시 이전 세대는 재사용하지 않고 TTL로 정리한다. Redis 작업은 각 1초로 제한하며 실패하면 브릿지를 조회한다. GIS의 `X-Cache`는 `hit/miss/bypass`다.

캐시를 사용하기 전에 브릿지 health를 직접 확인한다. WS가 연결돼 초기 snapshot을 받은 동안만 관측 캐시를 사용하고, 단절/원천 오류 상태에서는 HTTP 원천 조회로 전환한다. 과거 시점만 수정되어 최신 WS 상태가 바뀌지 않는 정정은 최대 300초 TTL 뒤 반영된다. 더 엄격한 과거 정정 전파에는 브릿지의 이력 버전 계약이 필요하다.

앱 전체에서 브릿지 WS 하나를 공유하고 watch에 최신 상태 하나만 보관한다. 서버 재시작으로 원천 `state_version`이 작아져도 새 snapshot을 적용한다. 터널 단절은 `type=status`, `bridge_disconnected`로 표시하고 마지막 관측의 값/시각을 유지한다. `sent_at`은 앱 전달 시각이다. `sent_at`만 다른 같은 내용(`type`·`schema_version`·`observed_at`·`state_version`·`source`·`quality_flags`·`data`)의 upstream 프레임은 사용자에게 다시 보내지 않고 캐시 세대도 올리지 않는다. 버전 번호가 아니라 내용을 비교하므로 재시작으로 `state_version`이 1로 돌아가도 내용이 다르면 전달하고, 지연 flag가 바뀌어도 새 내용으로 본다. 재접속 간격은 1초에서 최대 30초이며 upstream 응답 대기는 초기 15초/이후 75초다.

사용자 WS는 최대 32개, 입력 16KiB, 송신 5초 제한과 30초 ping을 사용한다. 느린 소비자는 중간 상태를 생략하고 최신 상태를 받거나 연결이 종료된다. `ALLOWED_ORIGINS`는 쉼표로 구분한 정확한 origin 목록이며, 비어 있으면 Origin 헤더가 있는 브라우저 연결을 거부한다. 네이티브 클라이언트는 Origin 없이 연결한다. ping/pong은 새 관측이 아니다.

## 검증 범위

```bash
cargo fmt --all -- --check
cargo clippy --all-targets --locked -- -D warnings
TEST_REDIS_URL=redis://127.0.0.1:6380/0 cargo test --locked -- --include-ignored
```

시험 서버의 합성 응답으로 HTTP 두 시점·정정·NULL/0·오류·WS 단절/재시작/종료를 확인하며 실제 앱 Redis로 hit/miss·TTL·원천 장애를 검사한다. WS 검사는 세 가지를 더 본다. `ws_does_not_resend_identical_upstream_state`는 `sent_at`만 다른 재발행이 전송되지 않고 버전이 1로 돌아간 새 내용은 전달되는지, `ws_initial_snapshot_never_hides_immediate_correction`은 연결 직후 정정을 30회 반복해도 최종 버전이 유실되지 않는지, `ws_slow_subscriber_is_dropped_without_blocking_others`는 수신 버퍼 4KiB로 읽지 않는 클라이언트가 약 5.4초 뒤 정리되어 슬롯이 돌아오고 동시에 연결한 클라이언트는 최신 버전을 계속 받는지 확인한다. 서버에는 클라이언트별 큐가 없고 watch의 최신값 하나만 보낸다. 느린 소비자 검사는 5회 반복해 모두 통과했다(`var/verification/ws-tests/`). 운영 DB를 변경하거나 중지하지 않는다.

2026-09-29 실제 SSH 터널로 GIS 2,748개 전체 feature 필드와 두 시점의 관측값을 브릿지 HTTP에 대조하고 앱 WS 초기 수신도 확인했다. 앱 health는 200, bridge/Redis 모두 ok다. 실제 하루 이력의 결측 처리와 연속 구간 순부하 계산도 통과했다 (`var/verification/live-integration.log`). 지리 자료·사진·DEM은 독립 `data` 브랜치 작업 공간에 보관하며 [전송 결과](source-geography.md)에 기록한다.

이 세션의 SSH master만 종료한 실제 단절 검사도 통과했다. WS는 `status`와 `bridge_disconnected`를 전달하며 마지막 관측 시각·값을 보존했다. health/state는 503, Redis는 ok를 유지했다. SSH 복구 후 같은 사용자 WS가 새 snapshot을 받고 health는 200으로 돌아왔다. 기록·재실행 코드는 `var/verification/live-tunnel-recovery.{log,json,py}`다. 원천 DB·수집기는 중지하지 않았다.

## 중복 조회 비용

앱 health와 캐시 GET/SET은 하나의 lazy Redis `ConnectionManager`를 공유하며 단절 시 재연결한다. GIS의 겹친 요청은 진행 중인 조회 결과/실패를 공유한다. 각 호출자는 먼저 원천 health를 확인하며, 완료된 조회 결과를 메모리에 추가 캐시하지 않는다. 마지막 대기자가 취소되면 진행 중 조회도 해제된다. 상태/시간 목록/시뮬레이션은 GIS 대기와 독립적이다.

최적화 전후 실측과 한계는 [캐시 검토](cache-review.md)에 기록한다. Redis hit 자체의 GET과 원천 health 확인 비용은 남는다. 과거 시각 정정 계약 없이 시뮬레이션 관측을 재사용하면 재현성·최신성 의미가 달라지므로 시뮬레이션은 계속 직접 조회한다.
