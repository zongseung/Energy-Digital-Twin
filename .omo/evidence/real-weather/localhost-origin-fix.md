# localhost:8080 기상 표시 연결 수정

2026-09-30. 사용자가 실제 접속한 주소 `http://localhost:8080/`로 재현했다. 이전127.0.0.1 기준 검증으로는 이 결함을 발견하지 못했다.

## 수정 전 관찰

- 실행 컨테이너 `ALLOWED_ORIGINS`: `http://127.0.0.1:8080,http://127.0.0.1:18080,http://localhost:18080`. `.env`에는 별도 override가 없고 Compose와 예제 설정 모두 localhost8080을 누락했다.
- 같은 `/api/v1/jeju/wind/ws` 업그레이드를 `Origin: http://localhost:8080`으로 요청하면 HTTP403, `Origin: http://127.0.0.1:8080`이면 HTTP101. 명령은 `curl --silent --max-time 2 --output /dev/null --write-out '%{http_code}' -H 'Connection: Upgrade' -H 'Upgrade: websocket' -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' -H 'Origin: <origin>' http://127.0.0.1:8080/api/v1/jeju/wind/ws`였다.101은 열린 소켓으로 인한 curl timeout(exit28)이며403은 즉시 exit0였다.
- `node tests/preview-ws-origins.mjs`: exit1, `http://localhost:8080 must receive live updates on /api/v1/jeju/wind/ws`, `403 !== 101`.
- `node .omo/evidence/real-weather/missing-display-qa.mjs http://localhost:8080/`: exit1. 실제1280px Chromium 화면의 값은 `기온 — · 습도 — · 이전 60분 강수 —`, 상태 `유효 기상 관측 없음 · 결측은 —`, wind_frames=[]이었다. API HTTP200에 기상값이 있으나 브라우저의 WS가 차단되어 전달되지 않는 증상을 재현했다.

## 수정

Compose 기본값과 `.env.example`에 정확한 `http://localhost:8080`을 추가한다. 두 WebSocket 경로는 공통 설정을 사용하므로 기상과 지역 수급 연결이 함께 복구된다. 임의 Origin을 허용하거나 검사 코드를 완화하지 않는다. Rust/JS 제품 코드는 변경하지 않는다. API 컨테이너를 재생성해 새 설정을 적용한다.

## 수정 후

- `docker compose up -d --no-deps api`: exit0, API만 재생성했다. Compose 기본값 및 `.env.example`를 지정한 설정 모두 네 개의 정확한 로컬 Origin을 포함한다.
- `node tests/preview-ws-origins.mjs`: exit0/PASS. 두 실제 WS 경로에서 localhost/127.0.0.1의8080/18080 네 주소는101, 외부 `https://untrusted.example`은403을 반환했다. 수정 전 같은 검사는localhost8080의403으로 실패했다.
- `node .omo/evidence/real-weather/missing-display-qa.mjs http://localhost:8080/`: exit0/VISIBLE. 같은 사용자 URL에서43개 관측소 WS 프레임이 들어오고 실제 기온·습도·강수 문자열이 표시됐다. 수정 전에는 wind_frames=[] 및 모두—이었다.
- `cargo test --locked`: exit0; unit46+shutdown1+CLI3=50 passed, 4 dedicated-Redis tests ignored, 0 failed. `node --test tests/wind-estimate.mjs tests/playback.mjs`: exit0/2passed. JS syntax와 기존 actual showWind 검사는 exit0였다.

## 관측소 표에 기상 표시 추가

사용자는 `관측소 / 거리 · 바람 · 관측` 표를 보고 있었으나 기상값은 기존 표 밖의12px 한 줄에만 있었다. 원래3열 표 그대로인 것은 HTML과 실제 브라우저에서 확인했다. 표에 대한 새 브라우저 assertion은 `Station table must show measured temperature`로 실패했다.

기존 native 표에 기온(°C)·습도(%)·이전60분 강수(mm)를 추가하고, 일사량 열에는—를 표시했다. ‘일사량: 현재 AWS 원천 미제공 · 별도 자료 연동 필요’를 함께 표시한다. 일사량은 연결된 측정값이 아니다. 일사량을0으로 만들거나 습도/강수에서 추정하지 않았다. 기상만 유효한 행은 관측시각을 유지하며 지연 상태를 표시한다. 상단 기상값은 기존14px token으로 키웠고, 표의 가로 스크롤은 기존 container에 한정했다. HTML이 새 열을 로드할 때 JS/CSS도 같은 변경을 읽도록 버전 query를 갱신했다.

`node .omo/evidence/real-weather/missing-display-qa.mjs http://localhost:8080/ 1280` 및 `... 375`는 모두 exit0/VISIBLE이었다. 실제1280/375 viewport에서7열·43행과 기온/습도/강수 단위·일사량—·원천 미제공 안내를 확인했고 페이지 가로 넘침은 false였다. 표의 scrollWidth600px, panel폭237/305px로 내부 가로 스크롤을 확인했다. 결과: [desktop](missing-display-qa-1280.json), [mobile](missing-display-qa-375.json), [desktop capture](missing-display-panel-1280.png), [mobile capture](missing-display-panel-375.png). 환경 차이와 UI 누락을 각각 수정했으며 사용자 주소에서 검증했다.

자체 검토: 원천 경계·null/0·시각/freshness 처리는 기존 로직을 재사용하고 표의 측정값도 weatherValid를 요구한다. 정확한 Origin 비교는 유지한다. 다른 사용자의 3D/지형/식생 변경은 보존했고 새 의존성·DB 수정·계측/일사 추정을 추가하지 않았다.
