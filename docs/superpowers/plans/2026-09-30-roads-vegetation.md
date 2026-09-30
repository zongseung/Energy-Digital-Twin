# Sinchang Roads, Vegetation and Detail Terrain Imagery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:dispatching-parallel-agents (user-selected). R, V, T run in parallel in `/home/user/Energy-Digital-Twin`; I is the main session. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 신창 AOI에 폭·포장을 가진 도로 리본, 토지피복 기반 나무·과수원·비닐하우스, 약 1 m/px 지형 영상을 같은 `scene.glb`에 추가한다.

**Architecture:** 도로·식생은 독립 모듈(`roads.py`, `vegetation.py`)이 `add_*(scene, frame, height_at) -> dict`를 제공하고, 통합에서 `build_local.py`가 `surface_height(scene)`로 만든 `height_at`을 넘겨 호출한다. 지형 영상은 `prepare_imagery.py` 새 모드와 `apply_imagery`의 AOI 분할로 처리한다.

**Tech Stack:** Python 3.11 `uv run --script`(numpy 2.2.6, trimesh 4.7.4, shapely 2.1.2, rasterio 1.4.3, Pillow 11.3.0), VWorld WMTS/데이터 API(기존 키), Three.js 뷰어, Omniverse Kit 106.5.

**Spec:** `docs/superpowers/specs/2026-09-30-roads-vegetation-design.md`

## Global Constraints

- 좌표 프레임: EPSG:32652/EPSG:3855, x=동·y=위·z=남, 1 m. 프레임 원점은 `var/rendering/local/manifest.json` `coordinateFrame`이고, 투영은 `x = E−E0`, `z = N0−N`이다.
- 높이는 `height_at(x: ndarray, z: ndarray) -> ndarray`(표시 지형, `build_local.surface_height`)에서만 읽는다. self-test에서는 평면 함수를 쓴다.
- VWorld 2D API만 쓴다. 키는 `configure.read_key`(prepare_imagery와 같은 방식)로 읽고, 출력·로그·manifest에 남기지 않는다. 새 의존성은 금지한다.
- 모듈 스크립트는 `build_local.py`와 같은 PEP 723 헤더(`uv run --script`)를 쓴다.
- 노드 접두: `street_`, `vegetation_`. 지형 분할 메시는 `terrain_landcover_` 접두를 유지한다.
- **소유 파일만** 수정한다. 커밋은 `git add <paths> && git commit -m … -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -- <paths>` 형식으로 한다. push는 하지 않는다. `.git/index.lock`이 있으면 기다렸다가 재시도한다.
- ponytail: 가장 짧은 올바른 코드, 기존 함수 재사용, 클래스·추상화 금지, 의도적 단순화에는 `# ponytail:` 주석.

| 작업 | 소유 파일 |
|---|---|
| R | new `renderers/twin/roads.py`, `var/rendering/roads/` |
| V | new `renderers/twin/vegetation.py`, `var/rendering/vegetation/` |
| T | `renderers/twin/prepare_imagery.py`, `renderers/twin/build_local.py`, `var/rendering/imagery-detail/`, `var/rendering/local/` |
| I (메인) | `build_local.py` 호출·manifest·CREDITS, `grid.js`·`index.html`·`app.js` 레이어, 문서 |

## Review Focus

1. 지형 60 m 면 위 리본 → 도로가 지형 아래로 파묻히거나 크게 뜨면 안 된다(세분 5 m, +0.3 m).
2. 교차로 겹침 → 같은 등급 union으로 z-fighting이 없어야 한다. 등급이 다른 교차는 높은 등급을 +0.02 m.
3. 폴리곤이 AOI 경계에 걸침 → AOI 밖에 나무나 도로가 생기면 안 된다.
4. 식생 과다 → 300k 삼각형을 넘으면 밀도를 낮추고 기록해야 한다. 60 FPS가 깨지면 안 된다.
5. z17 타일 일부 실패 → 해당 영역은 기존 영상으로 두고 실패를 기록해야 한다. 흰 구멍이 생기면 안 된다.

---

### Task R: 도로 (`roads.py`)

- [x] **Step 1:** `--self-test` 작성(평면 `height_at = 10 + 0.01x`, 합성 도로 3개: secondary+lanes 2, tertiary, residential)하고 **먼저 실패**를 확인한다. 검사 항목:
  - 폭 규칙과 `width_status`
  - 리본 모든 정점이 `height_at + 0.3 ± 0.01`
  - 최대 변 ≤ 5 m
  - 중앙선이 secondary/tertiary에만 있음
  - 노드 접두 `street_`
  - 삼각형 유한·법선 위쪽
- [x] **Step 2:** `--collect`: VWorld 데이터 API(`https://api.vworld.kr/req/data`)에서 도로명주소 도로구간 레이어를 조회한다. 레이어 ID와 폭 속성명은 VWorld 공식 개발자 문서에서 확인하고, 추측 URL을 반복하지 않는다. 대상은 AOI이고 결과는 `var/rendering/roads/vworld.json`에 저장한다. 없으면 이유를 기록하고 넘어간다.
- [x] **Step 3:** 구현한다.
  - `add_roads(scene, frame, height_at)`는 `var/rendering/site/scene.json` `roads[].paths`(장면 좌표)를 읽는다.
  - VWorld 폭은 중심선 간 평균거리 ≤ 3 m이고 방향차 ≤ 20°인 구간에만 붙인다.
  - 형상과 재질은 spec대로 만든다.
  - 반환 dict는 spec 형식이다.
- [x] **Step 4:** 실제 입력으로 `var/rendering/roads/preview.glb`를 만들고 통계를 확인한다. 여기서 쓰는 `height_at`은 `build_local.surface_height(trimesh.load('var/rendering/local/scene.glb', force='scene'))`로 만든다(importable).
- [x] **Step 5:** 커밋 `feat: draped Sinchang road ribbons`.

### Task V: 식생 (`vegetation.py`)

- [x] **Step 1:** `--self-test`(평면 `height_at`, 합성 폴리곤: 침엽수림 1 ha, 과수원 0.5 ha, 시설재배지 0.2 ha 1개씩)를 작성하고 **먼저 실패**를 확인한다. 검사 항목:
  - 밀도·간격 (숲 ≥ 5 m, 과수원 행 5 m)
  - 모든 물체 기저가 `height_at`
  - 폴리곤·AOI 밖 없음
  - 같은 seed는 같은 결과
  - 예산 초과 시 밀도 축소
  - 노드 접두 `vegetation_`
- [x] **Step 2:** 구현한다. `.worktrees/data/var/data/geography/source-03e02ef/landcover.geojsonl`을 한 줄씩 읽는다(290 MB). `'"제주"'` 문자열로 먼저 거르고 bbox로 선별한 뒤, `rasterio.warp.transform`으로 투영한다. 저폴리 원형은 다음과 같다.
  - 침엽수: 8각 원뿔 2단 + 줄기
  - 활엽수: 12면체 수관 + 줄기
  - 감귤: 낮은 구
  - 비닐하우스: 12분할 반원 터널
  - 물체마다 크기와 회전에 난수를 준다. 등급별로 메시 하나로 병합한다. 반환은 spec 형식이다.
- [x] **Step 3:** 실제 입력으로 `var/rendering/vegetation/preview.glb`를 만든다(`height_at`은 Task R Step 4와 같은 방법). 삼각형 수·개수·소요 시간을 확인한다.
- [x] **Step 4:** 커밋 `feat: landcover-based Sinchang vegetation`.

### Task T: z17 지형 영상

- [x] **Step 1:** `prepare_imagery.py --self-test`에 합성 z17 모자이크 검사를 추가하고 실패를 확인한다. 검사 항목: bounds, 크기 ≤4096, 실패 타일 기록.
- [x] **Step 2:** `--terrain-detail [--output var/rendering/imagery-detail]`를 추가한다.
  - 기존 `tile`/`coverage`/`fetch`/`validate` 흐름을 재사용한다.
  - 수집 범위는 site AOI의 z17 타일이다.
  - 산출물은 `texture.jpg`와 `manifest.json`이다(기존 imagery 메타데이터 형식 + `failed_tiles`).
- [x] **Step 3:** `build_local.py`를 고친다.
  - `build(..., detail_imagery_path=None)`: 기본값으로 `var/rendering/imagery-detail`이 있으면 사용한다.
  - `apply_imagery`는 기존 영상을 입힌 뒤, AOI(detail bounds) 안에 중심이 있는 `terrain_landcover_*` 삼각형을 `terrain_landcover_<원래>_detail` 메시로 분리하고 z17 재질·UV를 입힌다.
  - 실패 타일 영역의 삼각형은 분리하지 않는다.
  - `--self-test`에 다음 검사를 추가한다: 분리 전후 삼각형 수·정점 높이 보존, detail UV ∈ [0,1], `surface_height` 결과 불변(표본 1,000점).
- [x] **Step 4:** `python3 renderers/twin/prepare_imagery.py --terrain-detail`, `uv run renderers/twin/build_local.py --self-test`, `uv run renderers/twin/build_local.py`를 실행한다. 브라우저에서 `마을·영상`과 `풍력·해안` 시점의 1280 스크린샷을 찍어 **직접 보고** 선명도를 판단한다(GPU Chromium 플래그는 `var/verification/buildings/buildings-qa.mjs` 참고). 결과는 `var/verification/terrain-detail/`에 둔다.
- [x] **Step 5:** 커밋 `feat: z17 terrain imagery for Sinchang` (prepare_imagery.py, build_local.py).

### Task I: 통합 (메인)

- [x] T 커밋 뒤 `build_local.py`에서 `height_at = surface_height(scene)`(지형·영상 적용 후)를 만들고 `add_roads`·`add_vegetation`을 호출한다. manifest `roads`·`vegetation`과 CREDITS(OSM ODbL, 환경부 토지피복도)를 추가한다.
- [x] `grid.js`: `street_` → `roads`, `vegetation_` → `vegetation` 레이어(기존 terrain 정규식보다 먼저). `index.html`: 두 레이어 토글과 라벨(`도로 · 경로 실제 / 폭·포장 추정`, `식생 · 구역 실제(토지피복 2023) / 개별 나무 추정`). `app.js` `layers` 목록에 추가한다.
- [x] 빌드, 브라우저 375/1280 QA, FPS 재측정(`var/verification/fps/fps.mjs vulkan 60`), Kit 캡처(`buildings,lighthouse,pavilion,overview`), 문서·커밋·push.

## 실행 결과 (2026-09-30)

R `157d5aa`→`58224ba`(사용자 승인으로 OSM 대신 국토지리정보원 중심선 `LT_L_N3A0020000` 3,208구간·실제 폭; 도로명주소 `LT_L_SPRD`에는 폭 속성 없음), V `3818b2a`, T `e40e8a2`. 통합: 연안 바다 삼각형도 z17로 분할(육지만 선명해 생기던 벽 모양 흐림 제거), 레이어·CREDITS·anisotropy. FPS 2회 p5 30.1/59.5.
