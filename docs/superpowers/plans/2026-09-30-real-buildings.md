# Sinchang Photo-Based Buildings and Landmarks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:dispatching-parallel-agents (user-selected). B1 and B4 run as parallel agents in the shared working tree `/home/user/Energy-Digital-Twin`; B2 waits for aerial photos. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 신창 구역 건물을 높이 상자가 아닌 지붕 형태·처마·난간·창호와 실제 z19 지붕 영상이 있는 건물로 바꾸고, 등대와 싱계물공원 정자를 사진 참고 모델로 같은 GLB에 넣는다.

**Architecture:** B1은 `prepare_imagery.py`에 z19 지붕 atlas 수집 모드를 추가하고, `build_local.py`의 `add_buildings`를 규칙 기반 지붕·외벽 생성기로 교체한다. B4는 독립 모듈 `landmarks.py`의 `add_landmarks(scene, frame) -> dict`를 만든다. 통합(I)에서 한 줄로 연결한다. 결과는 기존 `var/rendering/local/scene.glb`와 manifest다.

**Tech Stack:** Python 3.11 `uv run`, numpy·trimesh·shapely·PIL(기존), VWorld Satellite WMTS(기존 키), Three.js 뷰어(`grid.js`/`app.js`), Omniverse Kit 106.5.

**Spec:** `docs/superpowers/specs/2026-09-30-real-buildings-design.md`

## Global Constraints

- 좌표 프레임 EPSG:32652/EPSG:3855, x=동·y=위·z=남, 1 m. 원천 윤곽·ID·시설·지형·영상 UV는 바꾸지 않는다.
- VWorld는 2D Satellite WMTS만 쓴다. 3D 타일은 쓰지 않는다. `vworld_key`는 기존 `configure.read_key` 경로로만 읽고 출력·로그·manifest에 남기지 않는다.
- Commons 사진은 형태·비례 참고 전용이다. 텍스처로 쓰지 않는다.
- 새 의존성 금지. 원본 타일·atlas·GLB는 `var/` 아래에 둔다.
- 각 에이전트는 **소유 파일만** 수정한다. 커밋은 `git add <paths> && git commit -m … -- <paths>` 형식으로 한다. 메시지 끝에 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`를 붙인다. push는 하지 않는다. `.git/index.lock`이 있으면 기다렸다가 재시도한다.
- ponytail: 가장 짧은 올바른 구현, 기존 함수 재사용, 추상화 금지, 의도적 단순화에는 `# ponytail:` 주석.
- 라이브 GLB 교체는 기존 CLI 방식(임시 출력 검증 후 파일별 교체, manifest 마지막)을 유지한다.

| 작업 | 소유 파일 |
|---|---|
| B1 | `renderers/twin/prepare_imagery.py`, `renderers/twin/build_local.py`, `renderers/twin/grid.js`, `renderers/twin/index.html`, `renderers/twin/DESIGN.md`, `docs/estimated-twin.md`, `var/rendering/roofs/`, `var/rendering/local/` |
| B4 | new `renderers/twin/landmarks.py`, `var/rendering/landmarks/` |
| I (메인) | `build_local.py`에 `add_landmarks` 호출 한 줄, `grid.js` 레이어 분류, README/구현 계획 |

## Review Focus

1. 윤곽이 사각형이 아닌 건물(ㄱ자·중정) → 박공으로 억지로 덮지 말고 평지붕+난간으로 가야 하며, 지붕이 윤곽 밖으로 과하게 튀어나오면 안 된다(처마 0.4 m 이내).
2. 속성이 전부 빈 1,761동 → 1층 3.5 m 추정으로 그리되 manifest와 화면에 `default_estimated`로 구분돼야 한다.
3. z19 타일 일부 실패·빈 타일 → 해당 지붕은 중립 지붕색으로 두고 `roof_texture: unavailable`로 기록해야 한다. 빌드 전체가 실패하거나 다른 건물 영상을 쓰면 안 된다.
4. 원천 높이 0 또는 비유한값 → 제공 높이로 쓰지 말고 다음 규칙으로 내려가야 한다.
5. 랜드마크 좌표 근거 없음 → 장면에 넣지 말고 실패로 보고해야 한다.

---

### Task B1: 절차적 건물 + z19 지붕 영상

**Files:** Modify `renderers/twin/prepare_imagery.py`(새 `--roofs` 모드), `renderers/twin/build_local.py`(`building_meshes`, `add_buildings`), `renderers/twin/grid.js`(buildings 기본 표시), `renderers/twin/index.html`(레이어 라벨·설명), `renderers/twin/DESIGN.md`, `docs/estimated-twin.md`.

**Interfaces:**
- Produces: `python3 renderers/twin/prepare_imagery.py --roofs [--output var/rendering/roofs]` → `atlas.jpg`(≤4096²), `atlas.json` `{zoom:19, crs:'EPSG:3857', tiles:[…sha], entries:{building_id:{atlas_px:[x0,y0,x1,y1], mercator_bbox:[w,s,e,n]}}, failed_tiles:[…], attribution}`.
- `add_buildings(scene, frame) -> dict`는 기존 반환 형태를 유지하고 다음을 바꾸거나 추가한다: `display_label`, `default_visible: true`, records에 `height_status ∈ {provider, floors_estimated, default_estimated}`, `roof_rule ∈ {gable_house, gable_shed, flat_parapet}`, `roof_texture ∈ {vworld_z19, unavailable}`, `floors_used`.
- 노드 이름 규칙 `building_<id>`와 `_walls`/`_roof` 접미사는 유지한다(`grid.js`가 `building_` 접두로 레이어를 분류). 박공 경사면과 난간도 `_roof`에 넣는다.

- [x] **Step 1: 실패 검사 먼저.** `build_local.py --self-test`에 다음 assert를 추가하고 현재 코드에서 실패하는지 확인한다.
  - 2,661개 윤곽이 모두 레코드가 되고 ID가 보존된다.
  - `height_status` 세 종류의 합이 2,661이다.
  - 박공 건물의 지붕 최고점이 벽 높이보다 높고 처마는 윤곽 밖 0.4 m 이내다.
  - `flat_parapet` 난간 높이는 0.6 m다.
  - 지붕 UV는 [0,1] 안에 있다.
  - 벽 UV 간격이 미터 단위다(층 높이 3 m = 창 한 줄).
- [x] **Step 2: 지붕 atlas 수집.** `prepare_imagery.py`에 `--roofs`를 추가한다.
  - 기존 `tile()`·`coverage()`·`fetch`·`validate` 흐름을 그대로 쓴다.
  - 받을 타일은 `var/rendering/site/scene.json` 윤곽들의 EPSG:4326 bbox(1 m 여유)와 겹치는 z19 타일만이다. 약 0.25 m/px다.
  - 건물별 잘라내기를 shelf-packing으로 atlas에 배치하고, 칸 사이에 2 px 여백을 둔다.
  - 실패 타일은 `failed_tiles`에 기록하고 그 건물은 entry를 만들지 않는다.
  - 키는 기존 `read_key`로만 읽는다. self-test는 네트워크 없이 합성 타일로 packing과 bbox를 검사한다.
- [x] **Step 3: 높이·지붕 규칙.** `add_buildings`에서 윤곽 속성으로 결정한다.

```python
def building_rule(props, parts):
    height = float(props.get('height') or 0)
    floors = int(props.get('grnd_flr') or 0)
    status, walls = ('provider', height) if math.isfinite(height) and height > 0 else \
        ('floors_estimated', floors * 3.0) if floors > 0 else ('default_estimated', 3.5)
    union = shapely.union_all(parts)
    rect = union.minimum_rotated_rectangle
    rectangular = len(parts) == 1 and not parts[0].interiors and union.area / rect.area >= 0.85
    use, strct = props.get('usability', ''), props.get('strct_cd', '')
    low = strct in ('11', '12', '13', '32', '33') or (not strct and max(floors, 1) == 1)
    if rectangular and low and use in ('01000', '', None):
        return status, walls, 'gable_house'   # 20°
    if rectangular and low and use in ('18000', '21000'):
        return status, walls, 'gable_shed'    # 10°
    return status, walls, 'flat_parapet'
```

  `walls`는 처마선까지의 벽 높이다. `provider` 높이를 박공 건물 전체 높이로 볼지는 원천에 정의가 없으므로 벽 높이로 쓰고 `# ponytail:` 주석을 남긴다. ②에서 실측으로 교체한다.
- [x] **Step 4: 형상.** 벽은 기존처럼 원천 윤곽을 그대로 세운다(구멍 포함).
  - **박공:** `rect`의 긴 변 방향이 용마루다. 사각형을 처마 0.4 m만큼 키운 두 경사면을 만들고, 짧은 변 쪽에 박공 삼각 벽을 만든다. 용마루 높이는 `walls + (짧은변/2 + 0.4)·tan(경사)`다.
  - **평지붕:** 기존 constrained Delaunay 지붕을 `walls`에 두고, 외곽 링을 따라 두께 0.2 m·높이 0.6 m 난간을 세운다.
  - 모든 면은 유한하고 법선이 바깥을 향해야 한다.
- [x] **Step 5: 텍스처.**
  - **지붕:** atlas entry가 있으면 지붕 정점을 EPSG:4326→EPSG:3857로 변환해 `mercator_bbox` 기준 atlas UV를 계산한다. 기존 `apply_imagery`의 `project_crs` 방식을 재사용한다. atlas 한 장을 `roof_imagery` 재질 하나로 공유한다. entry가 없으면 중립 지붕색이다.
  - **외벽:** PIL로 512² 절차적 텍스처 세 장을 빌드 시 생성한다.
    - 미장+창: 3 m마다 창 줄, 1층 한쪽에 문
    - 금속 골판: 창고·동식물
    - 현무암: 구조 13
  - 외벽 UV는 `u = 벽을 따라 누적 길이 / 4 m`, `v = 높이 / 3 m`다. 박공 삼각 벽도 같은 규칙이다.
- [x] **Step 6: 뷰어.**
  - `grid.js`에서 buildings를 기본 표시로 바꾼다(`visible.buildings = true`, 초기 숨김 제거).
  - `index.html`의 `#layer-buildings`를 기본 체크하고, 라벨을 `건물 · 위치·윤곽·지붕영상 실제 / 형태·외벽·일부 높이 추정`으로 바꾼다. 설명 문단도 새 내용으로 바꾼다.
  - 선택·레이어 코드 흐름은 유지한다.
- [x] **Step 7: 빌드·검증.**
  - `python3 renderers/twin/prepare_imagery.py --self-test`
  - `python3 renderers/twin/prepare_imagery.py --roofs`(실제 수집)
  - `uv run renderers/twin/build_local.py --self-test`
  - `uv run renderers/twin/build_local.py`
  - 브라우저 375/1280(`var/verification/local/browser-qa.mjs` 복사, `http://127.0.0.1:8080/`)에서 확인할 것:
    - `마을·영상` 시점 스크린샷
    - 건물 레이어 on/off
    - 시설 21개 목록 유지
    - 가로 넘침과 페이지 오류 0
  - **스크린샷을 직접 열어 박공·난간·창 줄·지붕 영상이 보이는지 확인하고 보고한다.** 결과는 `var/verification/buildings/`에 둔다.
- [x] **Step 8: 문서·커밋.**
  - `DESIGN.md`와 `docs/estimated-twin.md`에 규칙, 수량(박공/평지붕, 높이 상태별 수, 지붕 영상 유무), 한계를 적는다.
  - `git add renderers/twin/prepare_imagery.py renderers/twin/build_local.py renderers/twin/grid.js renderers/twin/index.html renderers/twin/DESIGN.md docs/estimated-twin.md && git commit -m "feat: procedural Sinchang buildings with z19 roof imagery" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -- <same paths>`.

### Task B4: 사진 참고 랜드마크

**Files:** Create `renderers/twin/landmarks.py`. Evidence under `var/rendering/landmarks/`. 다른 저장소 파일은 수정하지 않는다.

**Interfaces:**
- Produces: `add_landmarks(scene: trimesh.Scene, frame: dict) -> dict` → `{count, records:[{id, name, node, lon_lat, position, dimensions_m, dimension_basis, location_evidence, source_photo, status:'photo_reference_estimated_geometry'}], sources, limits}`. 노드 이름은 `landmark_<id>`(루트)와 자식 메시들이다.
- Consumes: `frame` = local manifest `coordinateFrame` (`origin_easting_northing`, `horizontal_crs`, `vertical_crs`, axes x east/y up/z south). 위치 변환은 `x = E − E0`, `z = N0 − N`, `y` = 지반고다. 지반고는 기존 DSM `var/rendering/site/scene.json`의 `terrain.positions` 근방 샘플을 쓴다(`build_local.add_buildings`와 같은 방식).
- `python3 renderers/twin/landmarks.py --self-test`(네트워크 없음)와 `--locate`(z19 타일로 위치 근거 수집)를 제공한다.

- [x] **Step 1: 실패 검사.** `--self-test`: 빈 `trimesh.Scene`과 합성 frame으로 `add_landmarks`를 호출해 다음을 확인한다.
  - 각 랜드마크 루트 노드가 존재하고 메시가 유한하다.
  - 등대 높이가 `dimensions_m['height']`와 1 cm 이내로 같다.
  - `lon_lat → position → lon_lat` 왕복 오차가 1 m 이내다.
  - `location_evidence`가 없는 레코드는 ValueError를 낸다.
  - 구현 전에는 실패해야 한다.
- [x] **Step 2: 위치 근거.** `--locate`는 `prepare_imagery.py`의 `tile`·`coverage`·수집 흐름을 import해 쓴다(`read_key`, 키 비노출).
  - 대상: 신창 풍차해안도로 싱계물공원과 해안 흰 등대 후보 주변 z19 타일(수십 장 이내)
  - 저장: `var/rendering/landmarks/tiles/`
  - 위치 판정: 영상에서 등대(흰 원형 구조물)와 정자(기와 지붕)를 **직접 찾아** 픽셀 좌표를 적고, EPSG:3857→EPSG:4326 좌표로 바꾼다. 근거로 타일 z/x/y·픽셀·잘라낸 PNG 경로를 남긴다.
  - 필요하면 VWorld 검색 2D API(`https://api.vworld.kr/req/search`, 같은 키)로 `싱계물공원` POI 좌표를 교차 확인한다.
  - 영상에서 확정하지 못한 랜드마크는 제외하고 보고한다.
- [x] **Step 3: 치수.** Commons 사진을 연다: `.worktrees/data/var/data/photos/sinchang/commons_sinchang_09.jpg`(등대, 전망대 위 사람들), `commons_sinchang_01.jpg`(기와 정자·육각정, 주변 사람들).
  - 사람 1.7 m를 기준으로 픽셀 비례를 재서 치수를 정한다: 등대 몸통 지름·높이·전망대 높이·등롱 높이, 정자 기둥 간격·높이·지붕 높이.
  - 근거(사진, 픽셀 측정값, 가정)를 `dimension_basis`에 적는다. 오차는 약 ±10%로 표기한다.
- [x] **Step 4: 모델.** trimesh 코드 모델을 만든다. 재질 색은 사진을 참고한 PBR이고 사진 텍스처는 쓰지 않는다.
  - **등대:** 원형 기단 데크와 흰 난간, 원통(약간 테이퍼) 몸통, 전망대 난간, 유리 등롱, 상부 안테나.
  - **정자:** 원기둥 4×N, 목재 마루, 기와 우진각 겹처마 지붕(처마 곡선은 구간별 평면 근사, `# ponytail:` 주석).
  - **육각정:** 6기둥과 육모 지붕.
  - 면 수는 랜드마크당 2만 이하로 한다.
- [x] **Step 5: 확인·커밋.** 두 검사를 통과한 뒤 다음을 한다.
  - 합성 frame 대신 실제 `var/rendering/local/manifest.json`의 `coordinateFrame`으로 GLB 한 장(`var/rendering/landmarks/preview.glb`)을 만든다.
  - trimesh로 앞·옆·위 3시점 PNG를 렌더해 사진과 비교하고 보고한다. 렌더가 안 되면 GLB 통계만 보고한다.
  - `git add renderers/twin/landmarks.py && git commit -m "feat: photo-referenced Sinchang landmarks" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -- renderers/twin/landmarks.py`.

### Task B2: 항공사진 사진측량 (사진 도착 후, 별도 진행)

- [ ] `var/aerial/ngii/`에 프레임이 생기면 `docker run --rm --gpus '"device=0"' -v …:/datasets opendronemap/odm:gpu --project-path /datasets <name> --dsm --dtm --orthophoto-resolution 25 --mesh-size 300000`로 실행한다.
- [ ] 장면 프레임에 정합하고 오차를 기록한 뒤, 건물별 높이·지붕형을 B1 레코드에 덮어쓴다(`height_status: photogrammetry_measured`). 세부는 사진의 메타데이터를 본 뒤 정한다.

### Task I: 통합 (메인 세션)

- [x] `build_local.py`에서 `add_buildings` 다음에 `add_landmarks`를 호출하고 manifest `landmarks`를 추가한다. `grid.js`는 `landmark_` 노드를 `buildings` 레이어로 분류한다.
- [x] `uv run renderers/twin/build_local.py --self-test && uv run renderers/twin/build_local.py`, 브라우저 재확인, GPU1 Kit `twin.py`로 새 GLB 캡처(`var/rendering/omniverse/buildings-01/`).
- [x] README·구현 계획 갱신, 커밋, push.

## 실행 결과 (2026-09-30)

B1 `66ebaf8`(2,661동: 박공 1,785·평지붕 876, 높이 provider 623·층수 887·기본 1,151, z19 타일 747장·지붕 영상 2,661동), B4 `f3f0587`(등대·정자·육각정). 통합에서 `add_landmarks` 연결, CREDITS 랜드마크 출처, `lighthouse`·`pavilion` 카메라, 뷰어 라벨 정정, Kit 캡처 `buildings-01`. B2는 항공사진 대기.
