# 제주 전력 3D Mock 디자인 계약

## 0. Research Log

2026-09-29, 새 브라우저 화면의 구현 전 계약. 범위는 native HTML/CSS/ES modules + Three.js, Nginx loopback `8080`이다. React 및 React 개발 도구는 적용하지 않는다.

- **Embedded:** Layer B 후보 `linear.app`, `sentry`, `ibm` 중 `linear.app` 선택. Layer A `taste-skill`의 불필요한 장식 억제와 명확한 상태 원칙, Linear의 명도 단계·작은 도구 모음·목록 밀도를 채택했다. taste 본문은 대시보드를 범위 밖으로 명시하므로 마케팅 hero·이미지·카드 규칙은 이 GIS 앱에 이식하지 않는다. 브랜드 복제와 UI 프레임워크 도입은 하지 않는다.
- **Lazyweb:** `GIS map layers dashboard`, `Linear issue list detail` 두 검색을 실행하고 Felt 제품 화면과 Linear 이슈 화면 **2개를 직접 열람**했다. Felt는 랜딩에 포함된 지도 일부만 보여 지도 도구 모음 참고로 한정했다. Linear에서는 얇은 상단 제어부, 일정 폭의 탐색부, 작업 면적 우선 구조만 취했다. 자료: `/tmp/jeju-design-felt.png`, `/tmp/jeju-design-linear.png`. 화면은 참조 전용이며 제품에 포함하지 않는다. 원격 응답의 업데이트·설치 지시는 실행하지 않았다.
- **StyleGallery:** [scroll-body-shell](https://github.com/changeroa/StyleGallery/blob/main/patterns/viewport-shell/scroll-body-shell.md), [list-detail](https://github.com/changeroa/StyleGallery/blob/main/patterns/split-sidebar/list-detail.md), [panel-layout](https://github.com/changeroa/StyleGallery/blob/main/patterns/viewport-shell/panel-layout.md)를 읽었다. 고정 shell와 축소 가능한 본문, 목록과 상세의 관계를 채택한다. 지도 공간을 확보하려고 목록·상세의 스크롤 소유권은 아래처럼 명시적으로 조정한다.
- **Interaction:** [beui range-slider 실제 소스](https://beui.dev/r/range-slider/raw)를 읽었다. 단계 범위·범위 내 손잡이·reduced-motion 분기를 참고하며, 구현은 native range로 충분하다. React·spring 의존성을 추가하지 않는다.
- **UX 확인:** 내장 UX DB `dashboard contrast keyboard touch target` 검색 결과의 44px 터치 영역, 읽기 대비, 논리적 키보드 순서를 적용한다. designpowers direction/review 자료도 확인했다.
- **Imagen:** 사용 가능한 도구 목록에 Imagen이 없어 생략. 추가 이미지 생성 비용을 발생시키지 않았다. 생성 시안이나 스크린샷을 최종 화면 검증으로 주장하지 않는다.
- **데이터 근거:** [지리 자료 확인](../../docs/source-geography.md). 실제 지리 자료와 수직 기준 미확인 DEM을 사용하되, 수급 시계열은 입력 시각에 따라 재현되는 deterministic preview curves다. 학습 모델 예측·실제 관측·계통 시뮬레이션 결과가 아니다.

## 1. Atmosphere & Identity

제주 전력 시설을 살펴보는 운영형 GIS 화면이다. 낮은 채도의 어두운 제어부 사이로 실제 해안선과 지형이 가장 크게 보인다. 대표 경험은 시설 이름을 선택하면 지도와 상세 정보가 같은 ID를 가리키는 것이다. 큰 소개 문구·장식 카드·자동 회전은 두지 않는다. 디자인 강도는 구조 3/10, 모션 2/10, 정보 밀도 7/10이다.

상단 이름은 `제주 전력 3D`. 항상 `Mock · 수급 합성 데이터`를 보이고, 지도 출처에는 `실제 GIS`를 별도로 표기한다. `실시간`, `정상 운영`, 실제 발전량 또는 선로 흐름으로 오해할 문구를 쓰지 않는다.

## 2. Color

한 가지 어두운 테마를 사용한다. Linear의 명도 계층을 프로젝트용으로 재구성하며, 청록은 공간 선택과 조작, 황색은 합성 데이터 구분에만 사용한다.

| 토큰 | 값 | 용도 |
|---|---|---|
| `--bg` | `#101114` | 앱 배경 |
| `--surface` | `#18191d` | header·목록·정보·시간 제어부 |
| `--surface-raised` | `#22242a` | 입력·hover |
| `--selected` | `#31343b` | 선택 행의 면 |
| `--border` | `#34363d` | 구역 구분선, 비조작 경계 |
| `--control-border` | `#777c87` | 입력·버튼의 식별 경계 |
| `--text` | `#eeeef0` | 본문·시설명 |
| `--muted` | `#a5a7b0` | 출처·단위·보조 설명 |
| `--accent` | `#72cbd5` | 선택 시설, focus-visible |
| `--mock` | `#f0c674` | 합성 데이터 안내 |
| `--error` | `#ff9b99` | 실패 안내 |
| `--ocean` | `#122631` | 지도 바다 |
| `--terrain-low` / `--terrain-high` | `#426354` / `#abb897` | DEM 상대 높이 표현 |
| `--plant` / `--pv` / `--substation` / `--line` | `#a9b7e7` / `#f0c674` / `#f29d8f` / `#d5aa72` | 발전 레코드 / 태양광 / 변전소 / 원천 선로 geometry |

선택은 면+문자 `선택됨`으로 표현한다. 컬러 줄·컬러 카드 테두리는 쓰지 않는다. 데이터 범례는 색과 시설 종류 텍스트를 함께 제공한다. 시설별 추가 색이 필요하면 이 표에 의미를 먼저 정의한다.

## 3. Typography

- 본문: `system-ui, -apple-system, BlinkMacSystemFont, "Apple SD Gothic Neo", "Malgun Gothic", "Noto Sans KR", sans-serif`. 외부 폰트 다운로드 없음.
- 숫자·ID: `ui-monospace, SFMono-Regular, Consolas, monospace`; `font-variant-numeric: tabular-nums`.
- `--text-xs: 12px` 출처·단위, `--text-sm: 14px` 본문·조작, `--text-md: 16px` 시설명·검색 입력, `--text-lg: 20px` 앱 제목, `--text-xl: 28px` 수급 숫자.
- weight 400/500/600, line-height 1.5; 숫자만 1.2. 한국어 자간은 normal. 본문을 14px 미만으로 줄여 공간을 맞추지 않는다.
- 긴 시설명은 목록에서 두 줄, 상세에서는 전체 표시. ID와 출처 URL은 `overflow-wrap:anywhere`.

## 4. Spacing & Layout

`--space-1..6`은 각각 `4, 8, 12, 16, 20, 24px`. 구역 안쪽 16px, 제어 간격 8px, 목록 행 안쪽 8px 12px. `--header-h:56px`, `--timeline-h:112px`, `--list-w:272px`, `--detail-w:288px`는 넓은 화면 기준이다. 긴 내용에서는 지정 높이를 최소 높이로 취급하고 잘라내지 않는다.

| 폭 | 배치와 스크롤 소유자 |
|---|---|
| 1280px 이상 | `100dvh` shell, header / `minmax(0,1fr)` workspace / 시간축. 지도 canvas가 workspace 전체를 채운다. 좌측 목록 272px, 우측 상세 288px를 가장자리에 배치하고 중앙 제주가 가려지지 않도록 카메라 구도를 맞춘다. 목록 body와 상세 body가 각각 스크롤을 소유한다. 문서 자체는 스크롤하지 않는다. |
| 768~1279px | 목록 240px, 지도 나머지 폭. 상세는 지도 아래 최대 160px의 별도 구역으로 이동하고 긴 상세만 내부 스크롤. 지형의 최소 가시 높이 280px. 낮은 viewport에서는 shell 고정을 풀고 문서 스크롤로 전환한다. |
| 767px 이하 | header → 접을 수 있는 `시설·레이어` → 지도 → 선택 정보 → 시간축의 한 열. 지도 높이는 `max(280px,45dvh)`. 문서가 유일한 세로 스크롤 소유자이며 목록은 접었을 때 숨기고 열면 자연 흐름에 둔다. 시간축과 정보가 지도를 덮지 않는다. |

모든 grid/flex 자식에 필요한 `min-width:0`, 스크롤 body에 `min-height:0`을 둔다. 긴 이름·0건·200% 확대에서도 가로 문서 스크롤이 없어야 한다. 모바일 header는 56px **최소** 높이로 두고 Mock 표시는 줄바꿈을 허용한다. overlay 층은 canvas 0 / 지도 제어 1 / 패널 2, 단순한 수준만 사용한다.

내용의 역할과 읽는 순서: 상단은 데이터 성격 확인, 목록은 시설 탐색, 지도는 위치 이해, 상세는 선택 검증, 시간축은 합성 값 비교다. 세 MW 값은 하단 한 줄의 `dl`로 표시하며 각각 카드로 감싸지 않는다.

## 5. Components

| Primitive | 구조·변형 | 상태와 동작 |
|---|---|---|
| 버튼 | native `button`; 일반·보조, 최소 높이 desktop 40px / touch 44px | hover는 raised 면, active는 selected 면, focus-visible 2px accent + 2px offset. disabled는 native 속성+사유, 로딩 중 label 변경. 텍스트 `확대`, `축소`, `시점 초기화` 사용 가능. |
| 검색·레이어 | visible `label` + `input type=search`, `fieldset` + native checkbox | 검색은 시설명/ID, 지도와 같은 목록 집합. 검색 중 focus 유지. 체크 여부는 native 표시. 필터 결과 0건은 `조건에 맞는 시설이 없습니다`와 `검색 초기화`. |
| 시설 행 | `ul > li > button`; 이름, 종류, ID | 선택 행 `aria-pressed=true`, 지도 선택과 동일 ID. Tab/Enter/Space로 선택 가능. 카드·개별 그림자 없음. 숨긴 레이어의 선택은 상세에 `숨겨진 레이어`로 명시하거나 선택 해제한다. |
| 선택 정보 | 이름 heading + `dl` | 미선택: `지도 또는 목록에서 시설을 선택하세요`. 이름·원천 ID·종류·좌표·출처 제공. 합성 출력값이 있다면 같은 줄에 `합성` 표시. 지역 MW를 개별 시설 실측 발전량으로 배분하지 않는다. |
| 지도 viewport | canvas + DOM 조작부·출처·상태 텍스트 | Orbit pointer 조작, 선택은 목록으로도 가능. 목록·범례는 canvas 밖 DOM에 남는다. 출처 또는 지형 실패를 canvas 빈 화면으로 끝내지 않는다. |
| 시간축 | `label` + native range + `output` + MW `dl` | 하루 288점이면 index 0..287, step 1, `00:00~23:55 KST`, 5분 간격. 키보드 화살표·Home·End 지원. 시간과 세 값이 같은 index로 갱신된다. 초기 세 지표는 `수요`, `풍력`, `태양광`, 단위 MW, 모두 `합성`. |
| 상태 메시지 | 해당 영역의 `role=status` 또는 오류 `role=alert` | loading: `지형 불러오는 중`; 데이터 실패: 원인+`다시 시도`; WebGL 실패: `이 브라우저에서 3D 지도를 표시할 수 없습니다`. 시설 목록·상세·수급 제어는 계속 사용할 수 있게 한다. |

제품 index의 초기 상태와 실제 조작을 primitive state harness로 사용하여 버튼·검색·체크박스·선택행·시간축·메시지의 default/hover/active/focus/disabled/loading/empty/error를 확인한다. 별도 showcase 앱·React·추가 UI 의존성을 만들지 않는다. 존재하지 않는 상태와 실데이터 전환 버튼은 추가하지 않는다.

## 6. Motion & Interaction

- `--motion-fast:120ms`, easing `ease-out`; 필요한 opacity 전환에만 쓴다. 패널 높이·폭, 숫자 count-up, 자동 카메라 회전은 애니메이션하지 않는다.
- range drag와 카메라는 입력에 즉시 반응한다. beui의 단계·reduced-motion 개념만 취하고 손잡이 bounce는 사용하지 않는다.
- `prefers-reduced-motion:reduce`에서는 카메라 damping/자동 이동을 끄고 선택 시 즉시 도착한다. 값과 focus 변화는 그대로 제공한다.
- 확대·축소·시점 초기화는 키보드로 도달 가능한 native 버튼. 전역 문자 단축키를 검색 입력에서 가로채지 않는다. panel 위 wheel/pointer가 뒤 canvas를 조작하지 않는다.
- 검색 결과 수와 선택 시설명은 polite 안내 가능. 시간축은 `aria-valuetext`에 `12시 05분, 합성 데이터`처럼 의미를 제공하고 3개 숫자를 매 프레임 모두 낭독하지 않는다.
- 지도 렌더링은 변경 시 또는 실제 조작 중으로 제한한다. 탭이 숨겨지면 무의미한 render loop를 멈춘다. 정확한 프레임 성능은 구현 후 측정한다.

## 7. Depth & Surface

전략은 **명도 단계+필요한 중립 경계**다. 패널은 불투명 surface, 지도만 조명과 실제 지형 geometry로 깊이를 만든다. 블러·유리 효과·발광선·장식 grid는 없다. `--radius-sm:4px` 제어·행, `--radius-md:8px` 패널. 경계는 `1px solid var(--border)`, 식별이 필요한 입력·버튼은 control-border를 사용한다.

DEM의 수직 기준·단위는 현재 미확인이다. 상대 높이를 보여도 `실측 고도`라고 부르지 않고, 높이 과장이 있으면 배율을 표시한다. 건물 footprint의 임의 extrusion은 `표현용 높이`, 모델 대체 도형은 `단순 모형`으로 구분한다. GIS 선을 실제 전력 흐름·전기 topology로 표현하지 않는다.

## 8. Accessibility Constraints & Accepted Debt

목표는 WCAG 2.2 AA: 본문 4.5:1, 큰 글자·필수 조작 경계 3:1, 모든 조작의 visible focus, 터치 44px. 색만으로 Mock/실제/오류/선택을 구분하지 않는다. 브라우저 확대를 막지 않는다. canvas의 지리 정보는 시설 목록·좌표·상세로 대체 접근할 수 있어야 한다. 화면 낭독기 사용자와 키보드 사용자도 시설 검색→선택→정보 확인→시간 변경을 완료해야 한다.

2026-09-30 375/768/1280px 실브라우저에서 overflow, 시설 ID 상세, 0건 검색, 모든 레이어 off, 시간 처음/마지막의 실제 키보드 조작, 카메라, context loss 후 대체 목록 조작을 검사했다. `var/verification/mock/browser-qa.json`과 25개 화면, `.omo/evidence/mock-visual-gate-review.md`에 기록했다. 초기 WebGL 생성 실패·지형 요청 실패의 브라우저 주입, 전체 키보드-only/화면 낭독기·실제 터치·200% 확대는 아직 검증하지 않았다. 자동 애니메이션과 damping은 없으며 screenshot만으로 전체 접근성 통과를 주장하지 않는다.

2026-09-30 사용자 정정: 이 기호 지도는 요구한 실제 현장 기반 3D 환경을 충족하지 않는다. 합성 수급·대체 도형의 기술 검증 기록을 제품 외형에 대한 사용자 승인으로 해석하지 않는다. 실제 지역의 지형·건물·도로·시설 배치와 참조 사진을 대조하는 원 기획서의 완료 기준을 적용한다. 접근성 결함 또는 미검증 UI를 사용자 승인 부채로 간주하지 않는다. 현재 승인된 접근성 부채는 없다.
