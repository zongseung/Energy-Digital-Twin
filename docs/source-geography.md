# 데이터 서버 지리 자료 확인

## 최신 코드·기획 동기화: 3cc3a3b

2026-09-30 별도 원천 작업 트리에서 `git pull --ff-only origin feat/async-data-bridge`를 실행하여 `03e02ef` → `3cc3a3b`의5커밋·9파일을 반영했다. 현재 GPU 작업 브랜치 `feat/photo-scene`과 그 미커밋 구현은 유지했다. Rust 브릿지 실행부·HTTP/WS 계약은 이번 범위에서 변경되지 않았다.

추가 코드는 [전력 자료 수집기](../.worktrees/async-data-bridge/bridge/scripts/collect_power_data.py)와 [ASOS·건축HUB 수집기](../.worktrees/async-data-bridge/bridge/scripts/collect_registered_api.py), 두 회귀 검사 파일이다. 원천 작업 트리에서 `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s bridge/scripts -p 'test_*.py'`로14개 검사가 통과했다. 실제 수집기나 원천 DB 조회는 이번 동기화에서 실행하지 않았다.

공통 자료 검토17–21절, 설계의 시설 클릭→로드뷰 설명, 로컬 구현 계획의Task0B를 동기화했다. GPU에서 이미 완료한 구현 체크는 보존했다. 원천 문서는 전력9파일58,603,088바이트, 2025 ASOS4지점35,040행 및 건축물대장217,844행의 수집·검증을 보고한다. ASOS·대장의190JSONL파일391,938,526바이트와 등록 높이 주소 일치 후보48,648건은 실제 GIS 보완 완료나 실측3D를 뜻하지 않는다.

이번 작업은 **Git 코드·문서 동기화**다. 위 신규 원천 파일은 iSCSI에 그대로 두며 기존 로컬 지리 snapshot `source-03e02ef`를 변경하지 않았다. 2025 ASOS 과거 자료를 현재43지점 실시간 AWS 관측으로 대체하지 않는다.

## 이전 데이터 동기화: 03e02ef

2026-09-29 두 번째 요청에 따라 별도 원천 작업 트리를 `5d15ecb`에서 `03e02ef`까지 fast-forward pull했다. HTTP/WS 계약은 동일하여 GPU Rust 클라이언트 변경은 필요하지 않았다. 독립 Cargo target의 브릿지 검사 7개 통과, 직접 DB 검사는 제외했다.

새 자료는 `data` 작업 공간의 `var/data/geography/source-03e02ef/`에 있다. 기본 12종 **763,403,223 B / 877,327 features**, `supplements/northern_islands/`의 북쪽 12종 **7,210,137 B / 4,964 features**가 해시·크기·sidecar·좌표·ID·DEM 검사를 통과했다. 기존 11개 데이터 파일 481MB는 hardlink로 재사용하고 이전 snapshot을 보존했다. 새 `building_info`의 양수 높이 필드는 기본 117,008개·북쪽 573개다. 기존 `buildings`의 층수와 혼동하거나 모든 건물에 높이가 있다고 해석하지 않는다.

새 사진 36장은 원격 파일의 해시·sidecar를 확인하고 로컬에는 manifest와 metadata만 받았다. 원천 표시가 `per_image_terms_not_verified`, `local_reference_only`, `reconstruction_eligible=false`이므로 Mock 텍스처·생성 모델 입력에 사용하지 않았다. DEM 원본과 수직 기준·사용 조건 미확인 상태는 유지된다.

검증 보고서는 같은 작업 공간의 `var/data/geography/validation-source-03e02ef.json`, `validation-northern-islands-03e02ef.json`, `source-photo-audit-03e02ef.json`, `snapshot-transfer-03e02ef.json`에 있다. Mock 장면은 이 중 본섬의 지형·경계·전력 GIS를 사용하며 [두 렌더러 실행 결과](mock-rendering.md)에 기록한다.

## 최초 동기화: 5d15ecb

2026-09-29 `feat/async-data-bridge`를 별도 작업 트리 `.worktrees/async-data-bridge`에서 fast-forward pull했다. 새 커밋은 `5d15ecbc0b7a1899a6d160e2ad7203c56d2378ec`이며 루트 인프라 작업은 `feat/gpu-infra`에 보존했다. 브릿지 HTTP/WS 구현과 GIS SQL은 이전 `9a94596`과 동일하다. 추가된 수집 CLI의 단위 검사 4개는 로컬에서 통과했다.

[원천 브랜치의 수집 방법](../.worktrees/async-data-bridge/bridge/README.md)에 따르면 자료는 데이터 서버 `/mnt/iscsi/energy-digital-twin/geography/jeju`에 저장되며 Git에는 포함되지 않는다. 수집 파일은 브릿지 HTTP/WS에도 자동 공개되지 않는다. 따라서 pull만으로 자료가 GPU 서버에 복사되지는 않는다.

사용자가 지정한 데이터 서버 `dlwhdtmd@192.9.65.58:22`에 접속해 실제 manifest와 파일을 확인했다. 사용자 `.env`의 `pwd`는 SSH 인증에만 사용했고 앱 설정·이미지·로그에 넣지 않았다. 원격 마운트는 `/mnt/iscsi`와 `/mnt/iscsi-renewable`이며 이번 수집 결과는 전자의 위 경로에 있다.

## GPU 호스트 전송·검증

완료된 11종 데이터와 각 metadata sidecar, manifest를 두 개의 병렬 rsync로 `data` 브랜치 작업 공간의 `.worktrees/data/var/data/geography/source-5d15ecb/`에 전송했다. 중간 수집 checkpoint와 원천 DB 파일은 복사하지 않았다.

- 데이터 **481,226,655 B**, 10개 GeoJSONL **413,803 features**. 11종 모두 SHA-256·크기·sidecar가 manifest와 일치했다.
- 전력 GIS는 선로 54·변전소 13·발전 레코드 890·태양광 1,791, 합계 2,748개다. 발전 레코드는 plant/generator가 섞여 있어 고유 발전소 수를 뜻하지 않는다.
- 건물 footprint 260,169개, 도로 18,007개, 토지피복 132,225개를 확인했다. 레이어 내 ID 유일성·유한 좌표·WGS84 범위 검사를 통과했다.
- DEM은 3601×2521, EPSG:4326, int16, NoData −32768, 저장값 −146~1936이다. 수직 기준·단위·사용 조건은 미확인으로 유지한다. 별도 Copernicus DSM의 EGM2008 기준을 적용하지 않는다.

건물 층수는 실측 높이가 아니며 GIS는 전기적 topology가 아니다. 선택 bbox 밖의 전체 HVDC 경로와 행정구역 geometry를 보존했다. 상세 결과·검증 재실행 방법은 [data 브랜치 문서](../.worktrees/data/data/geography/README.md), 실제 보고서는 `.worktrees/data/var/data/geography/validation-source-5d15ecb.json`에 있다. 전송 로그는 `var/verification/geography-transfer-{0,1}.log`다.

## 실제 브릿지 조회

GPU에서 데이터 서버로 SSH local forward를 열어 `127.0.0.1:18091`을 원격 `127.0.0.1:8091`에 연결했다. 앱 `127.0.0.1:8090`에서 health 200, GIS 2,748개 전체 feature 필드, 두 시점의 관측값, WS 초기 관측을 원천 HTTP와 대조했다. 원천 DB에 GPU가 직접 접속하지 않았다.

실제 터널 단절 시 마지막 관측 보존·HTTP 503, 재연결 시 WS snapshot·health 200 복구도 확인했다 (`var/verification/live-tunnel-recovery.log`). 터널은 현재 개발 세션에서 실행하며 재부팅 자동 기동은 남아 있다.

2026-09-28 KST 하루에는 5분 관측 288개 중 23:55 한 개가 없었다. 하루 계산은 `incomplete`와 비어 있는 points를 반환했으며, 00:00~23:55 미만의 연속 287구간은 `net_load_only`로 계산했다. 실제 비재생 발전·HVDC dispatch가 없으므로 실제 ESS 운전 검증으로 해석하지 않는다. 증거는 `var/verification/live-integration.log`, 재실행 코드는 같은 디렉터리의 `live-integration.py`다.
