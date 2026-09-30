# 신창 현장 3D 디자인 계약

기존 [Mock 디자인 계약](../mock/DESIGN.md)의 색·글꼴·간격·버튼·목록·선택 상세 토큰을 그대로 재사용한다. 사용자 정정에 따라 지도는 공식 VWorld WebGL3 영상·지형 장면을 사용한다. 임의 시설 도형, 자체 지형 mesh, 합성 곡선은 만들지 않는다. 건물·터빈 모델 공급은 별도 확인 대상이며 사진 기반 시설 보완 완료를 주장하지 않는다.

지도에는 신창 해안과 마을을 함께 담는 target 기반 기울어진 시점, 구역/해안 보기 버튼만 추가한다. 지도 SDK의 기본 탐색 기능과 출처 로고는 유지한다. 원천 Point 목록은 실제 API AOI [126.155,33.325,126.19,33.36]에 한정하고 선택은 카메라 이동과 상세를 연결한다. 풍력단지 레코드와 개별 터빈 레코드를 구분하며 목록 선택이 실제 3D 모델 식별을 의미하지 않는다.

추가 토큰: `--sidebar-w:320px`, `--map-min-h:400px`. 데스크톱은 header / minmax(0,1fr) workspace / 최신 집계. workspace는 목록·상세를 합친 320px aside와 나머지 지도이며 aside만 세로 스크롤한다. 767px 이하 또는 높이600px 이하에서는 문서가 스크롤하며 지도400px, 정보패널을 자연 흐름으로 배치한다. 5개 MW 지표는 한 dl에 두고 좁은 화면에서 줄바꿈한다. 가로 넘침은 허용하지 않는다.

재사용 primitive: native button의 default/hover/active/focus/disabled; 시설 행의 aria-pressed 선택 상태; search label+input의 empty 상태; dl 상세; details 출처; role=status 로딩/성공 및 role=alert 실패. 데이터 실패는 해당 영역에 표시하며 다른 영역을 지우지 않는다. 숫자 누락과 실패는 —, 최신 집계의 observed_at·source·quality_flags를 제공한다. reduced-motion 시 카메라는 즉시 이동한다. 데이터는 textContent로 렌더해 원천 문자열이 HTML로 실행되지 않도록 한다.

접근성 목표: 기존 대비·44px 조작·visible focus·색 외 선택문구·좌표/목록 대체 정보 유지. 외부 SDK 자체 접근성과 HTTPS 혼합콘텐츠, 영상 기준일, 건물 공급, 참조 사진 대조는 아직 검증하지 않았으며 승인 부채나 완료로 간주하지 않는다. 실제 브라우저 최종 검증은 루트 작업자가 수행한다.

추가 승인 범위: 높이 자료가 있는 원천 건물 footprint를 SDK GeoJSON Entity polygon으로 표시하는 선택 레이어. SDK 지형 위 원천 양수 높이의 단색 LOD1이며 임의 높이·외벽 실사 텍스처를 만들지 않는다. checkbox 기본on/off와 독립 레이어 실패 상태를 둔다. 높이 미상 원천은 표시하지 않는다.
