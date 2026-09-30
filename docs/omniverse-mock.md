# Omniverse 제주 Mock 미리보기

브라우저 미리보기와 같은 `var/rendering/mock/scene.json`을 실제 Omniverse Kit RTX가 읽는다. 해안선·시설 위치·선로는 원천 GIS, 시설 큐브는 지역 화면용 Mock 기호다. DEM 원시값을 3배 높여 표현하므로 실측 높이·수직 기준·정확한 시설 복원으로 해석하면 안 된다. 수급 실시간 데이터나 임의 발전량은 넣지 않았다. 화면 스트리밍/WebRTC는 이 PNG 배치와 별개다.

## 실행

저장소 루트에서 Python 3.10과 공개 NVIDIA wheel을 별도 환경에 설치한다. 호스트 드라이버나 기존 컨테이너는 변경하지 않는다.

```bash
uv venv --python /usr/bin/python3 var/kit-runtime
uv pip install --python var/kit-runtime/bin/python \
  'omniverse-kit==106.5.0.162521' --extra-index-url https://pypi.nvidia.com
OMNI_KIT_ACCEPT_EULA=yes timeout 360 var/kit-runtime/bin/python -u \
  renderers/omniverse/render.py \
  --scene var/rendering/mock/scene.json \
  --output var/rendering/omniverse/new-run --gpu 1 \
  > var/rendering/omniverse/render.log 2>&1
```

`OMNI_KIT_ACCEPT_EULA=yes`는 NVIDIA Omniverse 사용 약관 확인이다. 사용자 요청에 따라 이 설치/렌더 실행에서 사용했다. 첫 실행은 공식 확장 registry에서 RTX/viewport 확장을 다운로드한다. NGC 자격증명은 필요하지 않았다. Kit wheel은 정확한 버전에, 주요 확장은 `mock.kit`에 실제 확인한 버전에 고정했다. 전이 확장 전체를 독립 lock으로 배포하는 구성은 아직 없다. 확장 캐시는 사용자 Omniverse 경로에 남는다.

입력 payload는 `renderers/mock/prepare.py`가 생성한다. `coast`, `lines`, `facilities`는 JSON 최상위 배열이다. 새 출력 폴더를 사용하며 기존 USD/PNG/evidence가 있으면 덮어쓰지 않는다. 산출물은 `jeju-mock.usda`, `jeju-mock.png`, `evidence.json`이다. evidence는 입력 scene hash·원천 metadata·GPU index·PNG hash와 Mock 상태를 기록한다. 예외/timeout은 완료 evidence를 만들지 않는다.

## 버전 선택 근거

2026-09-29 검증. [공식 Kit Python 패키지 안내](https://docs.omniverse.nvidia.com/kit/docs/kit-manual/latest/guide/kit_python_package.html)와 [공개 NVIDIA index](https://pypi.nvidia.com/omniverse-kit/)에서 Python 3.10용 Kit 106.5 wheel을 확보했다. 최신 Kit를 그대로 설치하지 않고 현재 R535 드라이버 세대와 맞는 구버전을 시험했다. [공식 Kit App Template 변경 이력](https://github.com/NVIDIA-Omniverse/kit-app-template/blob/main/CHANGELOG.md)은 Kit 106.5 채택과 이후 109.0.1의 Linux >=550.54.15 요구사항 변경을 구분한다. [과거 Omniverse 요구사항](https://docs.isaacsim.omniverse.nvidia.com/4.1.0/common/technical-requirements.html)은 Linux 최소 535.129.03을 명시한다. 최종 호환성은 문서 유추에 그치지 않고 실제 설치/렌더 로그로 확인한다.

`rtx-probe.log`의 Vulkan 장치 표는 **535.183.01 / NVIDIA RTX A6000 / GPU 1 Active**를 확인했다. GPU 0은 렌더 active 장치가 아니다. 호스트 드라이버 검사를 우회하지 않았다. headless 환경의 GLFW 창 초기화 경고는 기록에 남으며 PNG 생성/육안 검사가 최종 성공 기준이다. [공식 viewport capture API](https://docs.omniverse.nvidia.com/kit/docs/omni.kit.viewport.utility/latest/omni.kit.viewport.utility/omni.kit.viewport.utility.capture_viewport_to_file.html)를 사용한다.

## 실제 검증 결과

최종 산출물 경로는 `var/rendering/omniverse/verified-v2/`다. 2026-09-30 재실행이 exit 0으로 완료됐고, 1600×1000 PNG와 `jeju-mock.usda`, `evidence.json`을 생성했다. USD를 다시 열어 공유 payload의 **20,044 terrain triangles / 2,694 facility markers**가 저장되었는지 실행 중 assert 검사를 통과했다. PNG signature/해상도와 PNG·입력 scene SHA-256도 별도로 확인했다. 최종 PNG를 육안 확인하여 한라산 주변 초기 틈이 사라진 것을 확인했다. 원천은 `03e02ef` snapshot이며 공유 payload의 source hash/quality flags를 evidence에 보존한다. warm-cache 캡처 시간은 약 3.0초로 startup/확장 다운로드 시간과 구분하며, 실시간 FPS나 WebRTC 성능 측정이 아니다.

최종 실행 기록은 `var/rendering/omniverse/render-verified-v2.log`다. `verified`는 입력 마스크 수정 전 결과로 보존했다. `capture-02`는 근접 뷰, `capture-04`는 이전 전체 뷰, `probe`와 `final`은 초기 런타임/캡처 타이밍 진단용이므로 최종 증거로 사용하지 않는다. 초기 실패는 Kit capture future가 PNG 디스크 기록 전에 완료된 경우였고, 현재 코드는 추가 프레임/실제 파일 완료를 기다린 뒤에만 evidence를 쓴다.

초기 한라산 주변 틈은 입력 육지 마스크 선택 오류였다. `prepare.py`가 Hub 2018 마스크를 사용하면서 북동 사면의 고지대 샘플을 제외했고, 최신 VWorld `vworld_admin_boundary.geojsonl` 마스크로 교체했다. 새 scene의 `highland_samples_outside_mask`는 0이며 변경된 scene hash로 다시 렌더링했다. 이 오류를 DEM 자체의 결측 제약으로 단정하지 않는다. 높이는 3배 표현, 지형 수직 datum·단위/라이선스는 미검증, 시설은 Mock 큐브라는 조건이 계속 적용된다. 새 VisitJeju 참고사진·TRELLIS 자산은 이 장면에 사용하지 않았다.
