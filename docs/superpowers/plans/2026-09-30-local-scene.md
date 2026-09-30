# 탐라–한림 통합 부분 장면

사용자 수정 요청: 풍력/PV/송전망을 분리하지 말고 같은 일부 지형 위에 결합한다. GPU 사용 여부는 실행 증거로 설명한다. 기존 추정 형상 허용과 현재 브랜치 작업 승인을 유지하며 추가 승인·새 브랜치·커밋은 필요하지 않다.

## 제약

- 기존 UTM52N/EGM2008/1m/X동Y위Z남·원천 ID·위치·추정값을 유지한다. 시설을 보기 좋게 이동하거나 허구의 연결선을 만들지 않는다.
- 기존 grid 부분 DSM 범위만 사용한다. 해당 범위 밖 시설·선로·해안선은 화면에서 제외한다. 원천 전체 경로는 provenance로 보존하되 표시 경로는 경계에 자른다.
- 풍력10기·등록 PV3곳·범위 내 변전소 및 대표 송전 구조를 하나의 GLB/scene에 둔다. 중복 지형/해면 없이 grid 지형만 사용한다.
- 브라우저는 한 장면을 처음부터 로드한다. 모드 탭 없이 시설/범위 시점 버튼과 선택·레이어를 제공한다. 지역 HTTP/WS와 오류/컨텍스트 손실 처리를 유지한다.
- GPU1 Kit RTX로 같은 통합 GLB를 실제 렌더한다. 브라우저 WebGL, 서버 오프라인 RTX, 실시간 서버 스트리밍을 명확히 구분한다.

## 작업

- [x] Task1: `renderers/twin/build_local.py`에서 기존 grid/twin GLB와 manifest를 결합해 `var/rendering/local/` 출력. 입력 hash·좌표계·원천 변환/rotor·단일 terrain·범위 필터·clipped display path·재개방 단위/normal/ID를 runnable self-test로 확인한다.
- [x] Task2: 기존 app/grid/index/style와 Nginx/Compose를 통합 장면에 연결. 기본 풍력+지형 시점, 구역 전체/한림 선로/PV 선택은 카메라만 바꾸며 다른 시설이 계속 함께 존재한다. PC/모바일/레이어/선택/실패/로터/context-loss QA.
- [x] Task3: 같은 통합 자산을 Kit/GPU1로 overview/array/PV 캡처하고 GPU 실제 사용·USD/PNG/source hash를 기록한다. 코드/시각 검토와 현재 문서 갱신.

## 사용자 후속 수정과 실행 증거

- 실제 영상 요구: 기존 팔레트 지형을 VWorld Satellite375타일6400×3840으로 대체했다. UTM52N→EPSG3857 UV로 같은 DSM에 입혔다. source hash/권리/비밀값 미포함을 검토했다.
- 실제 고도 요구: sourceDSM 높이를 변형 없이 유지하고 수직배율1, 약0–760m range와 source511m봉우리 camera를 검증했다. `고도·지형`은 같은 장면의 근접 시점이다.
- 모델 사용 질문: 이전 화면의 설비는 코드 추정형상이라는 점을 바로잡았다. 이번 실제 신창09사진으로GPU0/TRELLIS2+DINOv3+RMBG 추론 성공, 등대 중심 결과라 터빈 자산으로 채택하지 않았다. `.omo/evidence/local-photo-inference.md`.
- `var/rendering/local/verification.json`: source transforms/rotors/DSM/UV/범위 clipping PASS; 최종 GLB SHA2562e2ee1be025368f8945be770f209813b9180dd16a62977c0225263e7e950ecf7.
- `var/verification/local/browser-qa.json`:375/768/1280 동일 장면·지형근접·조작·21목록·실패·contextloss PASS. extra-qa.json은 실제PV3객체 canvas선택/등록용량 PASS. lifecycle-replay는20탭전환/WS재사용·stale callback·로딩중contextloss PASS.
- `var/rendering/omniverse/local-02/verification.json`:15시설/418mesh/922830면,4PNGdecode/hash·USD재열기·단위/ID/위치/rotor PASS. GPU1 32.75초,35샘플최대100%/3401MiB.
- `var/verification/local/http-qa.json`:현재10파일hash일치,5비공개경로404,APIhealth200; Nginx검사PASS. 과거local-01mount를local-02로교체하고terrain exactroute를추가했다.

최종 검토 `.omo/evidence/local-final-review.md`: 코드·실제 화면·배포 PNG4장 독립 hash조회 모두 PASS, 남은 scoped blocker 없음.
