# 제주 송전 경로와 서부 지형 구현 계획

> **For agentic workers:** Use the existing source/asset/frontend boundaries below. Preserve other workers' files; no commit, branch switch, remote publication or source DB modification is required.

**Goal:** 기존 풍력 3D에 실제 송전·HVDC·변전소와 일부 지형/추정 설비 근접 장면을 추가한다.

**Architecture:** 기존 GIS 스냅샷·Copernicus DSM·GLB 생성 유틸을 재사용한다. 별도 정적 grid manifest/GLB를 처음 전환할 때 한 번 읽고 같은 Three.js 장면/선택 UI에서 표시한다. Rust 관측 API는 그대로 사용한다.

**Tech Stack:** Rust API, Python numpy/rasterio/trimesh, Three.js0.180.0, Kit106.5.

## Global Constraints

- 물리 단위1m, UTM52N 원점/EGM2008/X동Y위Z남은 기존 twin과 동일하다.
- 원천54선로에서 minor_line5개 제외. 타입/전압/geometry 동일한 중복3쌍을 ID목록으로 묶고46경로를 표시한다. 동명 변전소13개는 원천 ID별 보존한다.
- 선로 from/to, MW, 부하율·전류·배전망을 추정하여 실제값으로 표시하지 않는다. 철탑 위치/치수, sag, 변전소 부품은 추정이라고 표시한다.
- 지형은 탐라–한림 서부 일부, 전체 섬은 원천 해안 윤곽을 사용한다. HVDC 전체 경로를 자르지 않는다.
- 사용자 후속 승인: 서로 다른 등록 좌표의 PV3곳에 추정 패널/지지대/인버터를 추가한다. 원천 capacity_kw와 자료 기준일을 표시하며 실제 패널 수·발전량·접속선을 만들어내지 않는다. 동일 좌표의 여러 허가 기록은 좌표 정확도 한계로 기록한다.

## 작업

- [x] `renderers/twin/build_grid.py` / `grid-spec.json`: 원천 SHA 확인, 경로 정규화, 좌표 투영, 부분 DSM/토지피복, 한림 대표 선로3596 철탑/도체·변전소 GLB. `--self-test`로46경로13변전소·HVDC대응IDs·원천노드보존·좌표·normal·출력hash를 검사한다.
- [x] `renderers/twin/grid.js`와 기존app/index/style: lazy load, 풍력/전력망 전환, 전체/한림근접, 선로·변전소 선택 및 원천정보, 레이어토글. 실패 시 풍력 화면과 지역관측을 유지한다.
- [x] Nginx/Compose static route 추가. 실제 브라우저375/768/1280에서 풍력회귀·전력망/지형·ID선택·계층토글·로딩실패·contextloss 검사, 화면을 직접 열람한다.
- [x] `renderers/omniverse/twin.py`의 manifest기반 facilityroot/선택적인 rotor 검증을 재사용하여 grid GLB RTX 근접/지형 캡처. geometry 누락/단위/ID/원천metadata를 확인하고 별도 출력디렉터리에 보관한다.
- [x] 코드·실제 화면을 독립 검토하고 문서에 가정/원천/재현명령/검증범위를 기록한다. 전체 디지털트윈/배전망 완성으로 체크하지 않는다.

## 검증 결과

- `var/rendering/grid/verification.json`:46경로/13변전소/3PV/59철탑, 원천·좌표·단위·노멀·지형 관통 샘플 PASS.
- `var/verification/grid/browser-qa.json`:375/768/1280px 조작·실패/회복·풍력 회귀 PASS. `extra-qa.json`:직접 grid URL과 세 PV 모두 실제 canvas 클릭/등록 용량 PASS.
- `var/verification/grid/http-qa.json`:정적7파일 hash 일치, 비공개5경로404, API health200. Nginx/Compose 검사 PASS.
- `var/rendering/omniverse/grid-01/{evidence,verification,root-validation}.json`:GPU1 RTX3시점,17시설 root/300mesh·단위·원천 ID/위치·USD 재열기 PASS.
- `.omo/evidence/grid-assets-review.md`:APPROVE/CLEAR. 지형 아래로 숨는 개요선은 명시적 GIS 오버레이로 수정했으며 물리 GLB 도체는 변경하지 않았다.
- `.omo/evidence/grid-code-review.md`:APPROVE/WATCH(기존 app 모듈 크기 유지보수 메모). `.omo/evidence/grid-visual-review.md`:29이미지 및 갱신 overlay6장 직접 검토 PASS.

전체 디지털트윈/배전망/실측 형상·전기적 연결/개별 PV 관측/FPS 인증은 완료 범위가 아니다.
