# 신창 도로·식생 3D와 지형 영상 고해상도화 설계

**근거:** 사용자 요청 "도로·식생 정보로 3D", 승인한 설계(대화 2026-09-30), 원천 `road.geojsonl`(OSM 파생, 신창 AOI 157개, 폭 없음)과 `landcover.geojsonl`(환경부 토지피복 중분류, 2023 항공정사영상 기준).

## 목표

신창 site AOI `[126.155, 33.325, 126.19, 33.36]`에서 도로가 폭과 포장을 가진 리본으로 지형 위에 보이고, 침엽수림·활엽/혼효림·과수원·시설재배지가 나무와 비닐하우스로 보인다. 같은 구역의 지형 영상은 약 1 m/px(VWorld z17)로 바뀐다. 결과는 기존 단일 `var/rendering/local/scene.glb`이며 웹과 Omniverse가 공유한다. 실제(경로·구역)와 추정(폭·포장·개별 나무)을 레코드와 화면에서 구분한다.

## 공통

- 좌표 프레임과 기존 시설·건물·랜드마크는 바꾸지 않는다. 높이는 공유 함수 `surface_height(scene)`가 돌려주는 `height_at(x, z)`로 **표시 지형 삼각형**에서 읽는다(`build_local.py`, `00fe44b`).
- VWorld는 2D API(WMTS, 데이터 API)만 쓰고 키는 기존 `read_key`로 읽으며 산출물·로그에 남기지 않는다. 새 의존성은 없다.
- 노드 접두: 도로 `street_`, 식생 `vegetation_`. 지형 분할 메시는 기존 `terrain_landcover_` 접두를 유지한다(높이 함수·레이어 분류 호환).

## 도로 (`renderers/twin/roads.py`)

- **입력:** `var/rendering/site/scene.json`의 `roads[]`(157, 장면 좌표 `paths`, OSM `highway`·`lanes`). 선택적으로 VWorld 데이터 API의 도로명주소 도로구간 레이어에서 **도로폭** 속성을 수집해 공간 대응(선 거리·방향)으로 붙인다. 레이어나 폭 속성이 없으면 보고하고 생략한다.
- **폭:** VWorld 폭(`vworld_width`) → `lanes×3.25 m`(`lanes_estimated`) → 등급 추정(secondary 7.0, tertiary 6.0, *_link 5.0, residential 4.0 m; `class_estimated`).
- **형상:** 중심선을 폭/2로 flat-cap buffer, 등급별 union, 삼각분할 후 최대 변 5 m로 세분해 `height_at + 0.3 m`에 얹는다. AOI 밖·바다 위(높이 ≤ 0.2 m이고 원천이 해안선 밖)는 자른다.
- **재질:** 아스팔트(secondary·tertiary, 중앙 황색 복선·백색 가장자리선 메시), 주거 도로는 회색 콘크리트. 선은 도로면 +0.05 m.
- **반환:** `add_roads(scene, frame, height_at) -> dict` `{count, records:[{id, name, highway, width_m, width_status, length_m}], triangles, sources, limits}`.

## 식생 (`renderers/twin/vegetation.py`)

- **입력:** `landcover.geojsonl`에서 AOI와 겹치는 `l2_code` 310·320·330(숲), 240(과수원), 230(시설재배지). 폴리곤을 AOI로 자르고 장면 좌표로 투영한다.
- **배치:**
  - 침엽수(320): 원뿔 수관+줄기 저폴리, 높이 8–15 m. 활엽/혼효(310·330): 둥근 수관.
  - 숲 밀도: 약 1그루/60 m², 푸아송 디스크(최소 간격 5 m).
  - 과수원(240): 폴리곤 긴 축 방향으로 5 m 간격 행에 수고 2.5–3 m 감귤형 수관.
  - 시설재배지(230): 최소 회전 사각형 긴 축 방향의 폭 7 m 반원 비닐하우스 터널, 간격 1.5 m, 반투명 흰 재질.
  - 고정 seed로 재현 가능하게 한다. 각 물체는 `height_at`에 둔다.
- **예산:** 식생 전체 ≤ 300k 삼각형, 등급별 한 메시로 병합. 초과하면 밀도를 낮추고 기록한다.
- **반환:** `add_vegetation(scene, frame, height_at) -> dict` `{counts:{class:objects}, polygons, triangles, density, seed, sources, limits}`.

## 지형 영상 z17 (`prepare_imagery.py`, `build_local.py`)

- `prepare_imagery.py --terrain-detail`: site AOI를 덮는 VWorld Satellite z17 타일을 모자이크한다. JPEG 한 장 ≤4096², 약 1 m/px. 메타데이터 형식은 기존과 같다(`bounds` EPSG:3857, 타일 hash, 실패 타일).
- `build_local.py apply_imagery`: AOI 안 terrain 삼각형(중심 기준)을 같은 이름 접두의 별도 메시로 나눠 z17 재질·UV를 입히고, 나머지는 기존 영상을 유지한다. 바다(`ocean_surface`)는 바꾸지 않는다. 경계에서 정점 높이는 동일하다.

## 검증

- 각 모듈 `--self-test`(네트워크 없음, 평면 `height_at`): 형상 유한, 도로 폭·상태 기록, 리본이 `height_at+0.3` 위, 식생 개수·예산·seed 재현, AOI 밖 없음.
- `build_local.py --self-test`와 빌드, 브라우저 375/1280(도로·식생 레이어 토글, 시설 21개, 가로 넘침·오류 0, 마을·해안 스크린샷 육안 확인), GPU1 Kit 캡처.

## 범위 밖

밭담(개별 필지 경계 자료 없음), 도로 표지·신호, 실측 도로 폭·포장 재질, 개별 수목 조사.
