# 두 경로의 제주 Mock 렌더링

2026-09-30 사용자 정정: 아래 결과는 렌더링 실행 확인용이며, 요구한 **실제 현장 기반 3D 환경**의 완료 결과가 아니다. 실제 건물·도로·시설 외형과 사진 비교가 빠져 있다. 원 기획서 1절·14.7절의 현장 재현 기준으로 후속 구현을 판단한다.

동일한 `var/rendering/mock/scene.json`을 브라우저 Three.js와 GPU 1 Omniverse Kit가 읽는다. 실제 원천 GIS의 시설 위치·ID·선로를 보존하며 시설 형상은 기호다. 브라우저의 수요·풍력·태양광은 날짜에 종속되지 않는 재현 가능한 **합성 미리보기 곡선**이다. 실제 관측·예측·Rust 계통 계산을 뜻하지 않으며 원천 API/Redis에 합성 값을 넣지 않는다.

## 브라우저 실행

```bash
npm ci --prefix renderers/mock --ignore-scripts
uv run --no-project --python 3.11 --with rasterio==1.4.3 --with numpy==2.2.6 \
  python renderers/mock/prepare.py
docker compose --env-file .env.example --profile preview up -d --no-deps preview
```

`127.0.0.1:8080`은 **GPU 서버 내부 주소**다. 다른 PC에서 이 주소만 누르면 접속되지 않는다. 브라우저를 사용하는 **본인 PC의 터미널/PowerShell**에서 아래 명령을 실행한다.

```bash
ssh -p 10000 -N -o ExitOnForwardFailure=yes \
  -L 127.0.0.1:18080:127.0.0.1:8080 user@192.9.59.208
```

한 줄 실행: `ssh -p 10000 -N -o ExitOnForwardFailure=yes -L 127.0.0.1:18080:127.0.0.1:8080 user@192.9.59.208`.

기존 GPU SSH 계정으로 인증한 뒤 이 터미널을 열어 둔다. 이전 Mock은 <http://127.0.0.1:18080/mock/>이며 기본 `/`는 [실제 공간 자료 기반 화면](site-rendering.md)이다. 서버 Mock PNG는 <http://127.0.0.1:18080/omniverse/jeju-mock.png>다. 화면이 아무것도 출력되지 않고 SSH가 유지되는 것은 `-N` 포워딩의 정상 동작이다. 종료하려면 Ctrl+C를 누른다. GPU 서버 자체에서는 <http://127.0.0.1:8080/mock/>을 사용한다.

회전·확대·전체 보기, 시설명/ID 검색, 레이어 표시, 지도/목록 선택, 선택 위치 이동, 00:00~23:55의 5분 합성 시간축을 제공한다. WebGL이 실패해도 시설 목록과 시간축은 사용할 수 있다. 새 사진을 텍스처로 쓰거나 지역 합계를 시설별 출력으로 나누지 않는다.

Nginx 1.30.5 이미지 digest와 Three.js 0.180.0은 Compose/package lock에 고정했다. 브라우저는 외부 CDN에 접속하지 않는다. Nginx는 정적 화면·명시적 장면/PNG/USD와 기존 `127.0.0.1:8090`의 HTTP/WS만 제공한다. `.env`, Python 준비 코드 등은 HTTP 404다. 브라우저 Mock 자체는 원천 WS를 구독하지 않는다. 나중에 실관측 모드를 연결할 때는 Rust의 `ALLOWED_ORIGINS`에 실제 브라우저 origin을 지정해야 한다.

## 공통 장면과 GPU 출력

`prepare.py`는 사용한 7개 원천 파일의 SHA-256을 확인한 뒤 241×169 DEM 표본, 해안선, 시설 기호 2,694개와 선로 54개를 준비한다. 지형은 20,044개 삼각형이다. 수평 위치는 `[126.5,33.35]` 기준의 지역 근사 투영/km이며 정밀 측량 좌표계 변환을 대신하지 않는다. 높이는 DEM 원시값/1000×3으로 연출한 것으로 수직 단위·datum은 미검증이다. 원본의 음수·결측은 바꾸지 않으며 표시용 mesh에서만 해수면으로 표현한다.

첫 장면에서는 2018 Hub 행정경계의 틈 때문에 한라산 북동 사면 일부가 잘렸다. 같은 격자에서 원시 DEM 값 300 초과 표본 23개가 제외되던 것을 확인했다. VWorld 행정경계 마스크로 바꾸자 제외 표본은 0개가 되었고 두 렌더러에서 틈이 사라졌다. 임의 지형을 생성해서 메운 것이 아니다. VWorld도 행정경계이므로 측량 해안선과 같다고 주장하지 않는다.

북쪽 부속도서·건물·사진 자료의 수집 완료와 이번 본섬 미리보기 범위는 별개다. 추가 구역과 실측 높이/사진 자산은 이 장면에 아직 배치하지 않았다.

[Omniverse 실행 방법](omniverse-mock.md)을 따른다. 실제 GPU 1 RTX 출력은 `var/rendering/omniverse/verified-v2/jeju-mock.png`, `jeju-mock.usda`, `evidence.json`이다. 화면 우측 위 **서버 렌더** 링크로 동일 PNG를 열 수 있다. GPU 캡처는 배치 이미지이며 브라우저 상호작용은 접속 PC의 WebGL로 실행한다. GPU 1의 WebRTC 실시간 스트리밍은 아직 구현하지 않았다.

## 검증

```bash
npm test --prefix renderers/mock
python3 renderers/mock/prepare.py --self-test
docker compose --env-file .env.example --profile preview config --quiet
docker compose --env-file .env.example exec preview nginx -t
```

실제 브라우저 375/768/1280에서 검색·시설 선택·빈 결과·레이어 off·시간 양끝·카메라 조작과 WebGL context loss 후 DOM 대체 조작을 검사했다. 이미지·로그는 `var/verification/mock/`, 재실행 스크립트는 `browser-qa.mjs`다. 이 QA는 로컬 OMO browser skill의 설치된 omowright와 Chromium을 사용한다. 배포 FPS와 Lighthouse 성능 점수는 측정하지 않았다.

Nginx 정적 경로·자료 접근 범위·실제 Rust health/네이티브 WS 중계 결과는 `nginx-proxy.log`, GPU 출력의 PNG/입력 hash와 USD 재로딩 검사는 `var/rendering/omniverse/verified-v2/evidence.json`과 실행 로그에 있다. 25개 브라우저 화면과 GPU PNG를 확인한 [독립 시각 검토](../.omo/evidence/mock-visual-gate-review.md)와 [코드 검토](../.omo/evidence/mock-rendering-code-review.md)는 모두 PASS/APPROVE다. 실제 터치 장치·전체 키보드/화면 낭독기 접근성·200% 확대·FPS 인증으로 확대 해석하지 않는다.
