# HTTP 수급·ESS 계산 연결

기준: `feat/async-data-bridge@9a94596` Task 4, 설계 17.3–17.4. 작업 브랜치는 `feat/gpu-infra`다. 기존 순수 계산기와 브릿지 검증 경로를 재사용한다.

- [x] `POST /api/v1/jeju/simulate`: 요청에는 run_id/start/end/scales/dispatch/ess만 받고 관측은 서버가 브릿지에서 조회한다. 최대 하루, 5분 정각, 256KiB를 검증한다. 잘못된 조건은 원천 조회 전에 422, 포화는 429다.
- [x] 정확한 시점의 관측을 bounded async 병렬 조회한다. 404는 누락 시각으로 보존해 incomplete를 반환하며 통신·잘못된 원천 응답은 기존 503/502다. 전체 조회에 deadline을 둔다. 실제 관측 입력/수집시각/내용 hash를 결과에 남기고 원천 DB의 원자적 snapshot이라고 주장하지 않는다.
- [x] 동시에 두 실행만 허용하며 permit을 조회·blocking 계산 종료까지 보유한다. 취소된 HTTP 요청 때문에 실행 중 계산의 permit이 조기 해제되지 않게 한다. CPU 계산/직렬화를 `spawn_blocking`에 둔다.
- [x] 합성 브릿지 TCP 계약으로 ESS 4.65MWh·배율 변화·정확한 시각·NULL/404·원천 장애·입력 제한·포화·관측 WS 불변을 검증한다. fmt/clippy/전체 검사 및 Docker 실제 API를 검사한다.

추가로 공식 PyTorch 2.6.0/CUDA 12.4 컨테이너의 GPU 0 연산을 검사하고, 성공하면 고정한 TRELLIS.2 배치 실행 환경을 구축한다. 드라이버 교체는 이 작업에 포함하지 않는다. 실제 데이터 하루 검증은 reverse SSH tunnel 미개통으로 별도 대기한다.

검증: `var/verification/simulation-http-tests.log`, `simulation-http-docker-build.log`, `simulation-http-smoke.log`. HTTP/CLI는 합성 원천 검증이며 실제 원천 하루 실행은 별도 대기다. 캐시 후속 변경 검사는 `cache-final-tests.log`에 분리한다.
