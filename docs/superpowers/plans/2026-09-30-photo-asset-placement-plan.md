# Georeferenced Photo Asset Placement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 사진 기반 3D 자산을 확인된 시설 ID·좌표·길이로 정합해 기존 탐라–한림 통합 지도에 실제 미터 축척으로 배치하고, 근거가 없으면 기존 장면을 유지한다.

**Architecture:** 기존 `inference/run.py`의 GLB 출력과 `renderers/twin/build_local.py`의 단일 장면 합성을 연결한다. 등록 manifest의 출처·시설 동일성·좌표·기준 길이·축·방향을 검사하고 통과한 정적 자산만 같은 GLB에 포함한다. 현재 사진은 채택 조건을 충족하지 않으므로 기본 빌드는 변하지 않는다.

**Tech Stack:** Python 3.11+, 현재 `numpy`·`trimesh`·`rasterio`·`uv`; 기존 Three.js/Omniverse GLB 소비 경로. Rust API 변경 없음.

**Spec:** `docs/superpowers/specs/2026-09-30-georeferenced-photo-scene-design.md`

## Global Constraints

- 기존 프레임 `EPSG:32652`/`EPSG:3855`, `x=동, y=위, z=남`, 1 unit = 1 m와 원천 시설 ID·좌표·DSM 높이·영상 UV를 유지한다.
- 사진·생성 GLB·최종 장면 GLB는 Git에 추가하지 않는다. 출처·hash·라이선스는 manifest에 기록한다.
- 36장의 VisitJeju 참고 사진과 신창09의 등대 중심 GLB는 자산 등록에서 제외한다. 등록 증거가 없으면 기존 추정 메시를 유지한다.
- 풍력 자산은 회전자 pivot 검증 전 대체하지 않는다. 첫 연결은 정적 시설 또는 현장 지물만 대상으로 한다.
- 웹과 Omniverse는 같은 `/local/scene.glb`를 소비한다. 별도 사진 패널이나 추가 브라우저 자산 경로를 만들지 않는다. 라이브 GLB는 임시 출력의 검증이 통과한 뒤 교체한다.

## Review Focus

- 존재하지만 다른 시설의 GLB: 시설 ID 대응 근거가 없으면 거부.
- 길이 0/음수/비유한 수치 또는 좌표·yaw 누락: 빌드 전에 거부.
- 사진·GLB hash 불일치 또는 라이선스 누락: 빌드 전에 거부.
- 미등록 자산 또는 빈 manifest: 현재 노드·좌표·회전자·지형이 동일.
- 정상 등록 자산: 기준 길이와 위치가 맞고 GLB 재열기·웹 선택·Omniverse 가져오기가 가능.

---

### Task 1: 등록 계약과 미터 좌표 변환

**Files:** Create `renderers/twin/photo_assets.py`; create `renderers/twin/test_photo_assets.py`.

**Interfaces:** `load_placements(path: Path, facilities: list[dict], frame: dict) -> dict[str, dict]`는 검증된 등록만 돌려주고 실패 시 `ValueError`를 낸다. 등록 JSON은 `schema_version: 1`, `assets: []`이며 항목은 `facility_id`, `generated_dir`, `photo_path`, `photo_sha256`, `asset_sha256`, `license`, `facility_match_evidence`, `asset_anchor_xyz`, `length_endpoints_xyz`(두 점), `length_m`, `up_axis: "Y"`, `yaw_degrees`, `status: "verified_for_placement"`를 가진다. 시설 좌표·기준 위치는 기존 facility manifest에서 읽고 별도 수동 좌표로 덮어쓰지 않는다. `place_asset(dest: trimesh.Scene, facility: dict, placement: dict, root: Path) -> list[str]`는 기준점과 길이로 GLB 메시를 변환·추가하고 새 노드 이름을 반환한다.

- [ ] **Step 1: 실패 검사 작성.** `test_photo_assets.py`에 임시 2m 박스 GLB와 등록 JSON을 만들고, 정상 길이 10m가 균일 배율 5·기존 시설 기준점 위치로 변환되는지 검사한다. 다른 ID·빈 증거/라이선스·음수/NaN 길이·누락 yaw·잘못된 hash·배치 상태 `generated_unvalidated`를 각각 거부하는 하나의 table-driven 검사와 빈 목록 무변경 검사를 작성한다.
- [ ] **Step 2: 실패 확인.** `uv run --script renderers/twin/test_photo_assets.py`가 구현 전 예상된 import 실패를 보여야 한다.
- [ ] **Step 3: 최소 구현.** 위 두 함수만 작성한다. `generated_dir/asset.glb`·`metadata.json`과 `photo_path`의 실제 hash를 대조한다. 모델 기준점 `p₀`, 길이 `L_asset=‖p₂−p₁‖`, 공개 길이 `L_m`에 대해 `s=L_m/L_asset`을 계산한다. 회전은 Y-up 확인 후 yaw만 적용하고 최종 기준점은 `facility["position"]`에 정확히 일치시킨다. `generated_dir`은 저장소 `var/generated` 내부만 허용한다.
- [ ] **Step 4: 통과 확인.** 같은 명령에서 전 검사 PASS, 변환된 위치·길이는 수치 오차 `1e-5 m` 이내여야 한다.
- [ ] **Step 5: 독립 커밋.** 새 모듈과 검사 파일만 stage하여 `feat: validate photo asset placement`로 커밋한다.

### Task 2: 통합 장면 교체와 소비자 검증

**Files:** Modify `renderers/twin/build_local.py`; modify `renderers/twin/grid.js`; modify `renderers/twin/DESIGN.md`; extend `renderers/twin/test_photo_assets.py`.

**Interfaces:** `build(..., placements_path: Path | None = None) -> dict`에서 등록 manifest가 없거나 `assets=[]`이면 기존 출력과 시설 노드 구조를 유지한다. 등록된 **정적** 시설은 같은 `facility["node"]` 이름으로 사진 자산을 넣고 원래 추정 노드를 제외한다. 생성 manifest의 해당 시설에는 `photo_asset`의 출처·검증 길이·`photo_derived_estimated_geometry` 상태를 기록한다. 풍력·선로 등록은 이번 작업에서 명시적으로 거부한다.

- [ ] **Step 1: 실패 검사 확장.** 합성 fixture로 정적 시설 하나의 기존 노드가 한 번만 존재하고 기준점·길이가 맞는지, 미등록 시설과 풍력 회전자·DSM 메시가 기존과 같은지, `facility_id`가 중복되면 거부되는지 검사한다. 생성 manifest에 사진 출처·검증 길이와 추정 상태가 남는지 확인한다.
- [ ] **Step 2: 실패 확인.** `uv run --script renderers/twin/test_photo_assets.py`에서 새 합성 검사가 실패해야 한다.
- [ ] **Step 3: 최소 구현.** `build_local.py`가 선택적으로 `var/rendering/photo-assets/manifest.json`을 읽어 Task 1 검증과 정적 노드 교체를 수행하게 한다. 기존 `verify()`는 미등록 시설의 원본 변환과 등록 시설의 길이/위치를 각각 검사한다. `grid.js`의 선택 상세에서 등록 자산만 사진 출처·확인 길이·나머지 추정 형상을 표시한다. 설계·실제 입력 부족 상태를 `DESIGN.md`에 반영한다.
- [ ] **Step 4: 통합 검증.** 새 검사, `uv run renderers/twin/build_local.py --self-test`, 브라우저 375/1280px 시설 선택, `/local/scene.glb` GLB 재열기, 기존 Omniverse 로더의 단일 장면 캡처를 확인한다. 사진 등록이 없는 현재 운영 장면은 15시설·10회전자와 1:1 지형 높이를 유지해야 한다.
- [ ] **Step 5: 독립 커밋.** 변경 파일만 stage하여 `feat: place validated photo assets in local scene`으로 커밋한다.

### Task 3: 실제 사진 후보의 채택 판정

**Files:** Create `docs/photo-asset-adoption.md` only if a candidate is found; use ignored `var/rendering/photo-assets/manifest.json` for an accepted asset.

**Interfaces:** 공개 사진 후보의 이용 조건, 사진 안의 대상과 GIS 시설 ID 대응 근거, 공인 길이, 방향·기준점 증거가 모두 있어야 Task 1 계약으로 등록한다. 없으면 `assets=[]`를 유지하고 누락 항목을 문서에 적는다.

- [ ] **Step 1: 기존 Commons 3장·VisitJeju 36장과 공개 추가 후보의 시설 동일성/기준 치수를 대조한다.** 신창09 등대 GLB를 풍력으로 등록하지 않는다.
- [ ] **Step 2: 조건을 충족하는 후보가 있으면 GPU0 TRELLIS.2 배치를 1회 실행하고, 생성 결과를 눈으로 확인해 등록한다.** 없으면 생산 장면에 새 사진 자산을 넣지 않고 필요한 촬영·길이·좌표 자료를 명시한다.
- [ ] **Step 3: 등록 후보가 있을 때만 Task 2의 통합 검사를 실제 사진 자산으로 반복하고 웹/Omniverse 캡처를 남긴다.** 결과에 `surveyed_asset=false`를 보존한다.
