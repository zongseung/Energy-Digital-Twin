# Omniverse 풍력 설비 추정 장면

기존 mock 지형 장면과 분리한 GLB → USD → GPU 1 RTX 경로다. 실제 GIS 시설 위치를 유지하고 터빈·로터·지지구조는 사용자가 승인한 추정 형상이다. 실측 형상, 현장 사진 대조 완료, 개별 발전량 관측을 주장하지 않는다.

Kit `106.5.0.162521`, driver `535.183.01` 그대로 사용한다. `twin.kit`은 기존 RTX·USD·viewport 버전을 재사용하며 converter `2.8.9`를 exact pin한다. 공식 registry package target은 Kit106.5.0/Python3.10/Linux x86_64다. [NVIDIA 공식 Asset Converter 문서](https://docs.omniverse.nvidia.com/extensions/latest/ext_asset-converter.html)의 native `create_converter_task` 경로를 사용한다. GLB·embedded texture 지원과 비동기 완료 결과를 확인하고 `use_meter_as_world_unit=True`, PreviewSurface material을 사용한다.

```bash
OMNI_KIT_ACCEPT_EULA=yes timeout 420 var/kit-runtime/bin/python -u \
  renderers/omniverse/twin.py \
  --asset var/rendering/twin/scene.glb \
  --manifest var/rendering/twin/manifest.json \
  --output var/rendering/omniverse/twin-04 --gpu 1 \
  > var/rendering/omniverse/twin-04.log 2>&1
```

출력 경로가 이미 존재하면 덮어쓰지 않고 거부한다. `converted.usda` native import와 `estimated-turbines.usda` 최종 장면, `close.png` / `array.png` 두 시점, `evidence.json`을 함께 보관한다. USD가 참조하는 converter 생성 자료도 같은 출력 디렉터리에 유지한다.

검사는 GLB node/mesh/material 수, USD finite vertex·transform·shader 값, face index 범위, metre 단위·Y-up, 시설 root ID·원천 위치·독립 rotor node, 저장 후 재열기 geometry/material 보존을 포함한다. 추정 manifest 전체를 `/World`에 기록하고 지역 API snapshot은 `/World/Observations`에만 붙인다. 개별 터빈 실제 출력은 null로 명시한다. API 실패 시 관측 상태는 unavailable이며 임의 값으로 채우지 않는다.

선행 import smoke는 `var/rendering/omniverse/twin-import-smoke-01/evidence.json`에 있다. 이전 TRELLIS 시험 자산이며 제주 설비 모델이 아니다. 실제 native import가 exit0, meshes1/points116816/faces96390/material1/metres1/Y-up를 기록했고 GPU1 Active 로그를 확인했다. 최종 본 터빈 결과는 `var/rendering/omniverse/twin-03/`이다. 최신 input GLB SHA256 `58c8d36e6d75d793ece59ab71700beda472626aeb6e4b980f7562bfba41abb70`를 직접 확인했다. GLB224nodes/20sharedmeshes/193mesh instances가 USD193meshes/116771points/224264faces/9materials로 변환됐다. 시설10개 ID·좌표·독립로터가 보존되고, native import와 최종 USD 재열기 결과가 일치한다.

실제 GPU1 RTX 결과: `close.png`, `array.png` 각1600×1000을 Pillow로 decode/verify하고 직접 열람했다. 근접 시점은 블레이드 끝을 포함한 첫 터빈 전체 형상과 나셀·타워·노란 지지구조를 보인다. 근접 카메라는 manifest inspect offset을1.35배로 넓혀 tip 잘림을 해결했고, vertical FOV48을 USD aperture/focal에 적용했다. 전체 시점은 manifest array를 사용한다. `evidence.json`과 `verification.json`, `../twin-03.log`에 hash·geometry·GPU1 Active·실행 증거가 있다. `estimated-turbines.usda`에 실제 API 관측16:10Z snapshot(수요636/공급1279/풍력1.25166/태양광0/재생합계11.667 MW)을 지역만 기록했다.

첫 `twin-01`은 converter의 반복 props 외부 참조 경로 오류로 터빈 geometry가 누락되어 `rejection.json`으로 거부했다. 공식 context `single_mesh=True`는 반복 props를 한 USD 내부에 기록하며 `merge_all_meshes=False`로 시설·부품 hierarchy를 유지한다. GLB mesh instance 수와 USD mesh 수 비교가 같은 누락을 검출한다. `twin-02`는 이전 자산과 clipped close view로 보존했으며 최종 결과로 사용하지 않는다. 기존 Mock 파일은 변경하지 않았다.

남은 한계: 기종 및 사진 속 개별 터빈 대응은 tentative이며 허브높이·지지구조·yaw 등 manifest 추정값을 유지한다. 실제사진 외벽 텍스처, 제조사 CAD/BIM, 상세 현장조사, 개별 telemetry, 실시간 Kit streaming은 이 결과에 포함되지 않는다. 본 결과는 실제 GIS 위치의 추정 설비 형상을 실제 Omniverse에서 렌더한 것이다.

## 송전·변전소·태양광 장면

같은 native importer는 `manifest.facilities[].node`를 기준으로 시설 root를 찾는다. S/L/P 등 이름 접두사에 의존하지 않으며, rotor 검사는 해당 시설에 `rotor_node`가 있을 때만 수행한다. 기존 터빈 기본 실행은 close/array 이름을 유지한다. `--views`는 manifest의 inspect/overview/array/network/hvdc/pv 중 서로 다른 최대5개를 선택하며 누락된 카메라는 시작 전에 거부한다.

```bash
OMNI_KIT_ACCEPT_EULA=yes timeout 420 var/kit-runtime/bin/python -u \
  renderers/omniverse/twin.py \
  --asset var/rendering/grid/scene.glb \
  --manifest var/rendering/grid/manifest.json \
  --views inspect,overview,pv --gpu 1 \
  --output var/rendering/omniverse/grid-01 \
  > var/rendering/omniverse/grid-01.log 2>&1
```

명시한 view는 `inspect.png`(대표 송전 구조), `overview.png`(부분 지형), `pv.png`(표본 태양광)와 `estimated-scene.usda`를 만든다. 실제 GIS 위치에 추정 구조를 놓으며 변전소 설비 배치·대표 철탑/전선·태양광 장비 치수와 배치는 실측 완료 자료가 아니다. 부분 지형의 실제 피복 기반 색은 합성 팔레트이며 실사 영상 텍스처가 아니다. manifest.routes에 담긴 전 제주 GIS/HVDC 선로를 USD 내 실제 geometry나 전체 계통 설비로 렌더했다고 주장하지 않는다. 원천 경로 데이터와 GLB에 포함된 실제 물리 형상의 범위를 구분한다.

최종 grid 결과는 `var/rendering/omniverse/grid-01/`이다. 입력 `scene.glb` SHA256 `9dbbc83168238d1ac59416082cd03493d577d2a5550184440ea1f177bb258173`의 377노드/44공유mesh/300mesh instance가 native USD300mesh/515835점/827926면/32재질로 변환됐고 저장 후 재열기 수치가 같다. metre1·Y-up, finite vertex/transform/shader, 유효 face index, root 원천 좌표·시설 ID를 검사했다. 별도 Kit 재열기에서 변전소13개는 각각 mesh6개, PV3개는 각각 mesh5개, 대표선로L3596은 mesh184개를 실제 USD root 아래 확인했다. input GLB와 manifest SHA는 실행 종료까지 불변이었다.

GPU1 Active 로그와 각 1600×1000 PNG의 Pillow decode·SHA 검사를 `grid-01/verification.json`에 기록했다. 근접 사진은 대표 철탑과 전선, overview는 **부분 DSM**과 실제 피복 분류 기반 합성 색, PV는 32장 **표본 표현** 패널과 다리를 보여준다. PV의 실제 패널 수와 설치 필지는 확인되지 않았고, 한 표본 위치는 13개의 등록 레코드가 동일 좌표를 공유한다. 이 점을 설치 형상/부지 측량의 근거로 사용하지 않는다. USD에는 실제 API 관측 시각16:30Z의 제주 지역값과 `source_delayed` 플래그만 넣었다. 개별 설비의 실측 출력은 null이며 송전 경로의 실제 전력 흐름을 시각화하지 않았다.

## 현재 통합 영상·고도 장면

사용자 수정에 따라 현재 기본 UI는 모드 구분 없이 `var/rendering/local/scene.glb` 하나를 사용한다. 실제 VWorld Satellite375타일 JPEG를 기존 Copernicus DSM 꼭짓점의 EPSG3857 UV로 입힌다. source terrain의 실제 고도는 약0–760m, 수직배율1이며 풍력·PV·변전소·대표 송전 구조가 함께 있다. `terrain` 카메라는 원천 DSM 약511.37m 봉우리 주변의 실제 경사·능선을 보여준다. 전체46경로 중 구역 내7경로의 GIS 오버레이는 브라우저 표시이며 USD의 실제 도체와 구분한다.

```bash
OMNI_KIT_ACCEPT_EULA=yes timeout 420 var/kit-runtime/bin/python -u \
  renderers/omniverse/twin.py \
  --asset var/rendering/local/scene.glb \
  --manifest var/rendering/local/manifest.json \
  --views array,overview,pv,terrain --gpu 1 \
  --output var/rendering/omniverse/local-03
```

검증된 최종 실행은 `local-02`다. 입력 GLB SHA256 `2e2ee1be025368f8945be770f209813b9180dd16a62977c0225263e7e950ecf7`, manifest SHA256 `bc99f8bcd7feaceee2d43b962a3f3ca4729cda2fe24e07d896a89969185b9877`. GPU1 RTX A6000에서32.75초, 네 PNG1600×1000을 생성했다. 런타임35개1초간격샘플에서 최대 GPU사용률100%, 메모리3401MiB를 관측했다. 이 수치는 샘플 최대이며 연속 최고값/브라우저 FPS 측정이 아니다.

`local-02/{evidence,verification}.json`은15시설 root·418mesh·922,830면·metre1/Y-up·원천 위치/ID·독립 rotor·USD 재열기 및 PNG decode/hash 검증을 기록한다. GPU 샘플은 `var/verification/local/gpu1-render-samples.csv`이다. `/omniverse/local-{array,overview,pv,terrain}.png`와 `/omniverse/local.usda`를 exact Nginx경로/read-only mount로 제공한다. 웹 자체는 접속 장치 WebGL이며 이 RTX 캡처는 정지 이미지다. 모델 추론의 GPU0 사용과 현재 설비형상 채택 여부는 [통합 장면 문서](estimated-twin.md)에 구분해 기록했다.

## 라이브·과거 재생·시설 선택

옵션을 주지 않은 기본 실행은 이전과 같다. evidence 키 순서·캡처 이름과 `/World/Observations` 속성 집합(`snapshotStatus`·`stateVersion` 없음)이 `local-02`와 같음을 scratch 출력으로 확인했다. 장면·자산은 한 번만 만들고 아래 옵션은 그 뒤에 `/World/Observations` 속성만 바꾼다.

- `--replay T1,T2`: 과거 시각을 URL 인코딩해 `/state?at=`로 순서대로 조회하고 `snapshotStatus=history_replay`로 적용한다. `stateVersion`은 지운다. 적용 직후 USD 속성을 다시 읽어 API JSON과 다르면 실패한다. `replay-NN.png`를 남기며 현재 계측으로 표시하지 않는다.
- `--live SECONDS`: Kit 이벤트 루프 안에서 `omni.kit.pip_archive`에 번들된 `websockets` 12.0으로 `/api/v1/jeju/ws`를 구독한다. `EDT_WS_URL`로 주소를 바꿀 수 있다. snapshot을 받으면 값·`stateVersion`·`snapshotStatus=live`를 쓰고, 새 `(state_version, observed_at)`에서만 `live-NNN.png`를 캡처한다. status envelope는 `unavailable`로 표시하고 값을 유지한다. 접속이 끊기면 `disconnected`로 표시하되 마지막 `observedAt`·값·`stateVersion`은 유지하고, 5초 뒤 다시 접속한다. Kit 제한 시간은 `240 + SECONDS`초다.
- `--select FACILITY_ID`: `facilityId`가 정확히 일치하는 시설 root prim이 하나일 때만 Kit selection에 넣는다. evidence `selected_facility`에는 개별 출력 `null`과 `unavailable_null`을 기록한다.

```bash
OMNI_KIT_ACCEPT_EULA=yes timeout 600 var/kit-runtime/bin/python -u \
  renderers/omniverse/twin.py \
  --asset var/rendering/local/scene.glb \
  --manifest var/rendering/local/manifest.json \
  --views array,overview,pv,terrain --gpu 1 \
  --live 420 --select hub:power_plant:5722 \
  --output var/rendering/omniverse/live-02
```

evidence `applied[]`는 적용한 순서대로 `{mode, status, state_version, observed_at, usd(다시 읽은 속성), capture}`를 남기고, `final_observation`은 종료 시점의 속성이다. 세 실행 모두 현재 `var/rendering/local/` 입력(GLB SHA256 `5c243005…`, manifest `8a3f2ec9…`)을 썼다. Kit 로그에서 GPU1 Active를 확인했고, 1초 간격 `nvidia-smi` 샘플은 각 출력의 `gpu-samples.csv`에 있다.

- `live-01` 과거 재생 결과. `--replay 2026-09-28T00:05:00Z,2026-09-29T12:30:00+09:00`로 실행했고, 두 시각 모두 같은 폴더의 `/timeline` 응답에 있다. 00:05Z는 수요763/공급1380/풍력41.9512/태양광37.0984/재생합계91.1461 MW였다. `+09:00` 요청은 03:30Z(862/1623/24.8968/372.072/408.522)로 돌아왔다. Kit 안의 read-back 비교를 통과했고, 실행 뒤 API를 다시 조회한 값도 evidence의 USD 값과 같았다. 41.1초, GPU1 최대 100%·3401MiB였다.
- `live-02` 라이브 결과. `--live 420 --select hub:power_plant:5722`로 실행했고 선택 prim은 `/World/T5722`다. snapshot 세 개를 적용했다. v145는 01:40Z(`source_delayed`)였다. v146은 값·플래그가 같고 `sent_at`만 4초 늦은 재발행이며(T4에서 다루는 중복 전송), 새 버전이라 캡처됐다. v147은 01:50Z(수요742/공급1563 MW)였다. 결과 PNG는 3장이고 457.3초 걸렸다. GPU1 샘플 444개 중 최대 100%·3787MiB, 평균 78%였고 GPU0은 최대 1%였다.
- `live-03` 단절 결과. 브릿지·API는 건드리지 않고 scratch TCP 프록시(`127.0.0.1:59998`→8090, `flaky_proxy.py`, `proxy.log`)를 `EDT_WS_URL`로 거쳤다. 프록시는 100초 뒤 연결을 끊고 20초간 포트를 닫았다. v148(01:50Z)을 적용·캡처한 뒤 `ConnectionClosedError` 1회와 `ConnectionRefusedError` 3회를 `disconnected`로 기록했다. 모든 기록에서 USD `observedAt` 01:50Z·`stateVersion` 148·값이 유지됐고 캡처는 없었다. 다시 접속한 뒤 같은 v148을 받아 `live`로 돌아왔으며 새 캡처는 만들지 않았다. 132.9초 걸렸다.

한계는 다음과 같다. headless Kit에는 omni.ui HUD가 없어 PNG에 글자가 없다. 관측값은 렌더 형상에 반영되지 않으므로 live PNG는 같은 장면이고 RTX 노이즈 때문에 hash만 다르다. 값과 PNG의 짝은 evidence가 증명한다. 화면 스트리밍이 아니라 새 버전마다 찍는 정지 PNG다. replay/live 값은 Kit 메모리 stage와 evidence read-back에만 있고, `estimated-scene.usda`에는 시작 시 스냅샷이 남는다. `live`는 WS 수신 상태를 뜻하며 관측 시각은 수신보다 약 15분 늦다(`source_delayed`). 5분 주기라 420초 동안 새 관측은 01:40Z→01:50Z 한 번이었다. 단절은 프록시로 흉내 낸 것이며 실제 브릿지·API 장애는 재현하지 않았다. `nvidia-smi` 샘플은 장치 전체 값이라 동시에 돌던 다른 GPU 작업과 구분하지 못한다. 라이브 중에는 viewport가 계속 렌더해 GPU1을 점유한다.
