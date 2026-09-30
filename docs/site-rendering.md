# 신창 실제 공간 자료 기반 3D

사용자가 제시한 실제 설비와 대응하는 3D 디지털 트윈의 참고 이미지에 따라, 이 지도는 **공간 자료 검증 도구**로 분류한다. 설비 외형·부품·현장 배치를 재현한 주 장면이나 디지털 트윈 완성본으로 사용하지 않는다.

기본 화면은 VWorld 공식 WebGL 3D SDK의 실제 영상·지형을 사용한다. 수집한 건물 경계와 양수 높이 623개를 단색 입체로 보완하며, 시설 목록과 제주 수급 집계는 Rust API에서 읽는다. 이전 기호 지도는 `/mock/`의 기술 검증 기록으로 보존한다.

## 실행

```bash
uv run renderers/site/prepare.py --self-test
uv run renderers/site/prepare.py
python3 renderers/site/configure.py
docker compose --profile preview up -d preview
```

`prepare.py`는 `.worktrees/data`의 검증된 원천을 사용한다. `configure.py`는 프로젝트 `.env`의 `vworld_key`로 브라우저용 공식 SDK URL을 구성한다. 생성 HTML과 지리 데이터는 Git에서 제외한 `var/rendering/site/`에 둔다. 브라우저용 VWorld 키는 SDK 호출에 포함되지만 SSH·DB·모델 토큰은 화면에 전달하지 않는다. `.env` 전체를 정적 디렉터리에 복사하지 않는다.

사용자 PC에서 아래 터널을 유지하고 **http://127.0.0.1:18080/** 을 연다.

```bash
ssh -p 10000 -N -o ExitOnForwardFailure=yes -L 127.0.0.1:18080:127.0.0.1:8080 user@192.9.59.208
```

서버 자체에서는 `http://127.0.0.1:8080/`이다. 지도 영상·지형은 브라우저가 VWorld에서 받으므로 인터넷 연결과 해당 키의 정상 인증이 필요하다. SDK가 내부 HTTP 리소스도 사용하므로 HTTPS 배포 호환성은 별도 확인 대상이다.

## 자료의 범위

- 건물: VWorld `LT_C_BLDGINFO`의 실제 경계·양수 높이 623개. 높이 미확보 2,038개는 입체 모델로 만들지 않는다. 제공 높이는 현장 실측 검증값이 아니며, 단색 모델에 실제 외벽·지붕 모양이나 색을 부여하지 않는다. 원천 건물 간 일부 겹침은 남아 있다.
- 배경: 공식 SDK의 영상·지형. 영상 기준일과 해당 구역의 텍스처 포함 건물 모델 공급 여부는 미확인이다. 건물 보완 레이어를 끄고 원래 지도를 비교할 수 있다.
- 시설: 원천 Point의 ID·좌표·속성을 유지한다. 개별 터빈과 단지 대표 위치를 구분한다. 시설 선택은 해당 좌표로 이동하며, 사진 속 터빈을 정확히 식별했다는 뜻이 아니다.
- 수급: 제주 전체 최신 관측값·관측 시각·지연 상태를 표시한다. 합성 곡선이나 시설별 발전량 배분은 없다. 현재 화면은 최신 조회이며 과거 시간축과 WebSocket 화면 갱신은 연결하지 않았다.

로컬 `scene.json`은 별도의 엔진 입력이다. UTM52N 수평 좌표와 EGM2008 높이를 미터 단위, 높이 과장 없이 보존한다. Copernicus 30m DSM은 건물·식생을 포함하므로 건물 기초 실측 고도로 취급하지 않는다. 이 높이를 VWorld의 타원체 높이에 직접 넣지 않는다. 원천 해시·필수 Copernicus 고지·한계는 파일 metadata에 있다.

**참조 사진과 일치하는 터빈·돌담·해안 지물의 세부 외형 제작과 정합은 아직 미완료다.** 이 화면을 정밀 현장 복원 완료로 표시하지 않는다. VWorld 지도 타일을 로컬 GLB/USD로 추출하지 않았으며, 기존 Omniverse PNG는 계속 별도의 Mock 결과다.

공식 근거: [VWorld SDK 예제](https://github.com/V-world/V-world_API_sample), [Cesium 건물 높이·polygon API](https://cesium.com/learn/cesiumjs/ref-doc/PolygonGraphics.html). 실제 자료 감사는 `var/verification/sinchang-source-audit.md`에 있다.
