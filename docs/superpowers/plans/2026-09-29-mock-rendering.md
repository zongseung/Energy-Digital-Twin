# 최신 원천 동기화와 두 Mock 렌더러

사용자가 최신 pull과 Mock 실행, 브라우저 3D/Omniverse 두 경로를 요청했다. 인프라 작업은 `feat/gpu-infra`, 원천 pull은 별도 `feat/async-data-bridge`, 데이터는 `data` 작업 트리에 유지한다.

- [x] 원천 `5d15ecb` → `03e02ef` fast-forward. HTTP/WS 계약 변경 없음; 독립 target 브릿지 검사 7개 통과, 실제 DB 검사는 제외.
- [x] 새 main/northern 12종과 사진 provenance 동기화·검증.
- [x] 실제 GIS로 공통 `scene.json`을 생성하고 두 renderer가 같은 파일을 읽는다. DEM 높이는 수직 기준/단위 미확인인 표시용 연출이며 원본은 수정하지 않는다.
- [x] 브라우저: Three.js 하나 + native HTML/CSS. 회전/확대/reset, facility ID 선택·검색, 레이어, 5분 합성 하루 timeline. 수급은 항상 Mock; 시설별 실제 출력으로 배분하지 않는다.
- [x] Nginx loopback 8080에 미리보기와 명시적 산출물만 제공, 기존 Rust API/WS를 프록시한다. 비밀 설정과 수집 원본 전체는 공개하지 않는다.
- [x] Omniverse: 기존 535 드라이버에서 고정 Kit 106.5와 GPU 1 실행을 검증하고 공통 장면의 USD·PNG를 생성한다. WebRTC 운영 스트리밍과 현장 정합은 이번 Mock 성공 기준에 포함하지 않는다.
- [x] 실제 브라우저 375/768/1280 screenshot·선택/카메라/필터/시간축/context-loss 검사, 독립 시각 검토 및 Kit 실제 출력 증거를 남겼다. `.omo/evidence/mock-visual-gate-review.md`, `mock-rendering-code-review.md` 모두 PASS/APPROVE. 전체 접근성 인증·실제 터치 장치·FPS 측정은 이번 증거로 주장하지 않는다.

설계 원칙: 기존 Rust/Redis 제품 경로는 유지하며 합성 프로파일을 원천 관측 API에 넣지 않는다. 새로운 사진36장은 사용 조건 미검증이므로 생성/텍스처 입력에서 제외한다. 렌더링 성공은 물리 고도·설비 외형·실제 전력 흐름 검증을 뜻하지 않는다.
