# 탐라–한림 실제 영상·고도 통합 3D

현재 `/`는 **한 장면**이다. VWorld Satellite 실제 영상과 Copernicus DSM 위에 풍력10기, PV3곳, 한림변전소1곳, 대표 송전 구조를 같은 좌표계로 배치한다. 풍력/전력망 모드 탭은 없으며, `풍력·해안`, `고도·지형`, `한림 선로`, `태양광` 버튼은 카메라만 이동한다. 기존 `/?view=grid` 주소도 이 통합 장면으로 열린다.

사용자 PC에서 기존 SSH 터널을 유지하고 **http://127.0.0.1:18080/** 를 연다.

```bash
ssh -p 10000 -N -o ExitOnForwardFailure=yes \
  -L 127.0.0.1:18080:127.0.0.1:8080 user@192.9.59.208
```

## 실제 지형과 영상

- 범위: 경도126.145–126.40, 위도33.31–33.435 부근의 탐라–한림 일부. 범위 밖 시설과 경로는 화면에서 제외한다. 전체 원천46경로는 provenance에 남기며, 실제 표시되는7경로는 경계에서 잘라 분리된 조각을 연결하지 않는다.
- 높이: Copernicus GLO-30 DSM / EGM2008. 약30m 원천을 약60m 간격으로 표시하고 각 꼭짓점의 실제 높이를 유지한다. 고도 범위는 약0–760m, **수직 배율1:1**이다. 위성사진을 평면에만 붙인 장면이 아니다. DSM은 식생·건물을 포함한 표면 높이로 세부 지표면 측량이 아니다.
- 영상: VWorld Satellite WMTS zoom15,375타일을6400×3840 JPEG로 조합했다. UTM52N 지형 꼭짓점을 EPSG3857로 변환해 UV를 계산한다. 영상의 픽셀을 생성형 이미지나 토지피복 팔레트로 대체하지 않았다. 약33°N에서 표시 픽셀 간격은 약4m이며 원천 촬영 해상도/촬영일은 미확인이다. 해상 영상 사이의 색·촬영 경계는 원천에 존재한다.
- 좌표:1m, EPSG32652, 기존 원점126.17217/33.3430267, X동/Y위/Z남. grid의 DSM만 유지하고 이전 풍력 자산의 지형·해면은 중복 삽입하지 않는다.

[브이월드 공식 API 예제](https://github.com/V-world/V-world_API_sample)와 공급자 이용조건을 따른다. 로컬 타일 캐시는 이 미리보기 입력이며 공개 재배포 라이선스를 주장하지 않는다. 원천 타일 좌표/hash·범위·취득시각·촬영일 미확인은 `var/rendering/imagery/manifest.json`에, 모델/원천/영상 hash는 `/local/manifest.json`에 기록한다. `.env` 키와 DB/SSH 비밀값은 정적 출력에 포함하지 않는다.

## 건물·바다 표현 정정 (2026-09-30)

사용자 요청에 따라 단색 수면 변경을 취소하고 바다를 기존 VWorld 영상과 UV로 복원했다. 건물 623개는 실제 건물 경계·제공 높이를 세운 단순 높이 모형이며 실사 3D가 아니다. 기본 화면에서는 숨기고 명시적인 비교용 체크박스로만 켤 수 있다. 마을 시점 버튼도 이 모형을 자동으로 켜지 않는다.

현재 수집 디렉터리의 건물 자료는 GeoJSON 경계·높이, 항공영상, DSM이며, 지붕·외벽 텍스처가 포함된 수집 3D 건물 자산은 발견되지 않았다. 생성한 GLB는 원천 수집 3D 모델의 증거가 아니다. 외부 공급 범위는 아직 미확인이다. 파일 감사는 `var/verification/buildings-wind/collected-3d-audit.json`, 브라우저 1280/375px 복원 검사는 `correction-qa.json`에 기록했다.

현재 서버 GPU PNG와 USD는 건물 추가 전 `local-02` 자산의 검증 기록이다. 이번 수정 자산을 다시 GPU 렌더했다고 주장하지 않는다. 현재 GLB의 해면 영상 복원과 기하 검사는 `var/rendering/local/verification.json`에 있다.

## 제주 전체 관측 바람 연동

Rust 서버가 기상청 날씨누리의 제주 전체 지상관측 응답을 시작 시 한 번, 이후60초마다 한 번 HTTP GET으로 읽는다. 현재43개 지점이며 개수를 고정하지 않는다. 브라우저는 `/api/v1/jeju/wind/ws` WebSocket으로 접속 직후와 원천 조회 완료 시 최신 스냅샷을 받는다. 브라우저의 주기적 기상 HTTP 조회는 제거했다. `GET /api/v1/jeju/wind/stations`와 기존 `/api/v1/jeju/wind` 고산 응답도 같은 메모리 상태를 읽으며 별도로 수집하지 않는다. 관측 파일·DB·시계열 이력은 적재하지 않는다.

공식 AWS 페이지의 JS와 실제 제주 조회 네트워크를 확인한 결과 해당 경로는 HTTP GET이며 WebSocket은 발견되지 않았다. 원천에 확인되지 않은 WS 주소를 만들지 않았다. Rust→브라우저만 WS로 전달하며 기상청의 관측 공개 지연은 그대로 표시한다. 느린 브라우저를 위해 과거 메시지를 쌓지 않고 최신 상태만 유지한다. 숨긴 탭은 연결을 닫고 복귀/장애 시 재접속한다. 조사 기록: `.omo/evidence/weather-upstream-transport.md`.

각 풍력의 실제 GIS 좌표에서 최근 유효 관측소까지의 거리를 구해 가까운 지점을 사용한다. 현재 원천 위치 기준 풍력5722·5723은 고산,5724–5731은 낙천이 가장 가깝다. PV3곳과 한림변전소는 한림이 가깝다. 이전5.54km는 장면 중심점–고산 거리였으며 모든 터빈에 대한 거리가 아니다. 화면은 선택 시설 기준 관측소·거리·시각과 제주 전체 지점 목록을 표시한다.

자료는 관측소의10분 평균 풍속(m/s), 풍향16방위(중심각22.5°간격)다. 풍향은 불어오는 방향이며 UTM52 자오선 수렴각을 고려해 나셀 방향에 반영한다. **인근 지상 관측 기반 시연이며 실제 터빈 방향·허브높이 풍속·RPM·발전량이 아니다.** 회전 체크박스는 아래의 풍속 기반 추정 RPM으로 시연한다. 타워·기초·지형은 움직이지 않는다.

관측이15분, 수신이150초를 넘거나 수집이 실패하면 해당 자료는 지연으로 표시한다. 가까운 지점이 결측/지연이면 다음 유효 지점을 사용하고 이를 화면에 알린다. 가까운 지점이 정온이면 먼 곳의 바람으로 대체하지 않는다. 유효한 비정온 관측이 없는 터빈, 표시 레이어를 끈 터빈, 방향 시연을 끈 경우에는 원래 추정 방향으로 복구한다. 전체 수집 실패·WebGL연결 손실에서도 풍향 반영을 중지한다.

공식 출처는 [기상청 제주 관측 화면](https://www.weather.go.kr/w/observation/land/aws-obs.do)이며 관측소 데이터에 정식QC 통과를 주장하지 않는다. 웹사이트 내부 공개JSON 경로라 형식 변경 시 실패 상태로 처리한다. 조사 근거는 `exa-results/jeju-wind-observations-2026-09-30.md`, CPU캐시/파싱 검사는 `src/weather.rs`, 실제 화면/방향 검사는 `var/verification/buildings-wind/wind-browser-qa.mjs`에 있다.

## 풍속 기반 출력·회전 추정

풍력의 기본 관계는 `P = ½ρACp v³`, 회전자 속도 관계는 `RPM = 60λv/(2πR)`이다. 풍향만으로 출력이나 RPM을 정할 수 없다. 현재 풍향은 나셀을 바람 쪽으로 향하게 하는 시연에 사용하며, 실제 나셀 방향이 없어 yaw 오차에 의한 출력 손실을 계산하지 않는다. [DOE 풍력 안내](https://www.energy.gov/cmei/systems/windexchange/small-wind-guidebook), [NREL 터빈 모델](https://www1.eere.energy.gov/water/pdfs/39485.pdf).

제조사가 작성한 [구형 WinDS3000 기술표 사본](https://www.visiongroup21.eu/en/pdf/tech6c.pdf)은91.3m·3MW, 시동/정격/정지 풍속3/13/25m/s, 정격15.7RPM을 제시한다. 단지 운영자 자료의91.59m와 차이가 있고 개별 설치 사양·제어 설정은 확인되지 않았다. 현재134m급 WinDS3000의 제원을 이 장면에 섞지 않는다.

따라서 아래 값은 제조사 출력곡선을 복제한 것이 아닌 **설명용 시나리오**다. 관측소 풍속v를 허브풍속 보정 없이 입력한다.

- v<3 또는 v≥25: 시연 출력0kW, 회전0RPM.
- 3≤v<13: 출력 `3000×(v³−3³)/(13³−3³)` kW, 회전 `15.7×v/13` RPM.
- 13≤v<25: 출력3000kW, 회전15.7RPM.

회전은 정격점에 맞춘 일정 주속비를 가정한다. 선택 시설에 추정값을 표시하며 사용자가 회전 시연을 켜면 시설별 가까운 유효 관측소에 따라 로터를 움직인다. 결측·지연·연결 단절 시 추정값을 유효 운전값처럼 유지하지 않는다. **0RPM 표시는 실제 터빈 정지를 뜻하지 않으며** 관성 회전, 최소 운전 회전수, 기동 지연, 출력 제한, 피치 제어는 미반영이다. 관측소10분 평균의 세제곱은 순간풍속 세제곱의 평균과도 다르다. 개별 계측과 제주 전체 실측 집계는 그대로 구분한다. 근거: `.omo/evidence/wind-power-research.md`.

## 시설과 다운로드한 모델의 관계

통합 장면의 풍력·송전·PV 형상은 공개 제원과 사진 참고를 사용한 **코드 기반 추정 geometry**다. 풍력 원천 ID5722–5731, 로터 직경91.59m와3MW 공개 제원은 유지하며 허브80m·기초·부품·방향은 추정이다. 대표선로3596의59철탑·도체, 변전소 내부 배치, PV4×8패널도 추정이며 전기적 접속/전력 흐름을 만들지 않았다.

PV는 도송93955(197.02kW), 한림93709(27kW), 명월93187(99kW)의 등록 좌표다. 기준일2025-09-15 등록 용량이며 패널 수나 실측 발전량을 역산하지 않았다. 각 좌표에13/3/25개 원천 레코드가 겹치므로 실제 필지·설치 방식은 미확인이다.

2026-09-30에는 실제 Commons 신창09 원본 사진으로 다운로드한 TRELLIS.2·DINOv3·RMBG와 decoder를 **GPU0에서 실제 실행**했다. 156.99초에97,616면 GLB를 만들었지만 전경 등대가 주 대상이 되어 풍력 자산으로 채택하지 않았다. 결과는 `var/generated/jeju-photo-sinchang09-original-01/`, 모델 revision·GPU샘플·사진 출처/CC BY-SA4.0·품질 한계는 `.omo/evidence/local-photo-inference.md`에 있다. 모델 실행 성공과 현재 장면의 시설 복원 완료를 구분한다.

## GPU와 관측 연결

대화형 웹은 접속 기기의 WebGL을 사용한다. 서버 **GPU1 RTX A6000 / Omniverse Kit106.5**로 같은 통합 GLB를 별도 렌더하고 `/omniverse/local-array.png`, `/omniverse/local-overview.png`, `/omniverse/local-pv.png`, `/omniverse/local-terrain.png`를 제공한다. PNG는 실제 서버 GPU 결과이며 실시간 스트리밍은 아니다. `/omniverse/local.usda`가 같은 장면의 USD다.

Rust HTTP/WS는 실제 **제주 집계**만 표시한다. 개별 시설 발전량은 null이며 원천 지연·관측시각·장애 상태를 구분한다. 풍속 기반 추정 출력·회전 시연은 실제 운전값과 별도로 표시한다. 장면은 최초1회 로드하고 시점 변경 때 재로딩하지 않는다. 원천 브릿지 터널은 별도로 유지해야 한다.

## 재생성과 확인

```bash
# 최초 원천 자산이 없을 때
uv run renderers/twin/build.py
uv run renderers/twin/build_grid.py
# 실제 영상 cache와 통합 자산
python3 renderers/twin/prepare_imagery.py --self-test
python3 renderers/twin/prepare_imagery.py
uv run renderers/twin/build_local.py
uv run renderers/twin/build_local.py --self-test
docker compose --profile preview up -d preview
node var/verification/local/browser-qa.mjs
node var/verification/local/lifecycle-replay.mjs
```

생성물은 `var/rendering/local/`이며 입력 자산·영상의 SHA, 원천 위치와 독립 rotor 변환, DSM 꼭짓점 높이, EPSG3857 UV·내장 JPEG, 범위 clipping/재진입/경계 교차, 유한 geometry/normal을 검사한다. 브라우저 검사 범위는375/768/1280px의 동일 장면 시점·21개 선택 목록·레이어/단축버튼 복구·단일 GLB 로드·회전 시연·HTTP실패·context-loss다. 실제 장치 FPS/전체 접근성/실측 형상 인증을 뜻하지 않는다.

GPU 입력15시설 root(풍력10/PV3/변전소1/선로1)는 USD418메시/922,830면으로 변환되어 단위·ID·위치·독립 rotor와 재열기를 검증한다. [Kit 재실행 방법](omniverse-twin.md)을 참고한다. 이전 분리된 `twin-03`/`grid-01` 산출물은 과거 검증 기록으로 보존한다.

2026-09-30 추가 검증: WS 단일 연결71초/43지점 프레임3회/브라우저 기상 HTTP조회0회, PC·모바일 관측 표시 및 추정 회전10기/저풍속·정지풍속·지연·단절 정지/복구 검사 통과. 재실행: `node tests/wind-estimate.mjs`, `node var/verification/weather-ws/client-lifecycle.mjs`, `node var/verification/weather-ws/rotor-browser-qa.mjs`. 상세: `.omo/evidence/weather-ws-qa.md`, `.omo/evidence/weather-ws-review.md`, `.omo/evidence/wind-estimate-review.md`.
