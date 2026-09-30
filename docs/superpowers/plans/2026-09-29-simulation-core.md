# 브릿지 독립 수급·ESS 계산

기획서 17.3–17.4, 구현 계획 Task 4의 순수 계산을 먼저 구현한다. 사용자 요청에 따라 feat/gpu-infra에서 진행한다.

- [x] `src/simulation.rs`, `src/simulation/{types,validation,tests}.rs`: 5분·하루 제한, nullable 원천, 순부하/명시적 G/H/ESS 계산, 동일 초기 조건의 기준/변경 사례. 결측은 결과 전체 incomplete, 중복/범위 밖/비유한값은 오류. 테스트로 에너지 보존과 경계를 검사한다.
- [x] `src/simulation/cli.rs`, `src/main.rs`, `examples/scenario.json`: 256KiB 제한 JSON 파일을 받아 stdout JSON으로 출력하는 `simulate <file>` 명령. 정상·결측·잘못된 입력을 실제 binary로 검증한다.
- [x] 실행 설명 및 fmt/clippy/test/build/실제 CLI 검증 기록을 남긴다.

설계 결정: 실제 관측을 준비할 브릿지는 개발 중이므로 HTTP 관측/시뮬레이션 endpoint는 이번에 만들지 않는다. CLI는 명시적으로 전달받은 프로파일만 계산한다. 기준/변경은 같은 G/H·ESS 초기 조건을 사용하며 각 사례의 ESS 전후 잔차로 ESS 없는 경우와 비교한다. 정지 HVDC는 입력 MW=0을 요구한다. 송전/대기 손실·경제성 최적화는 포함하지 않는다.

## 검증 기록

- 함수 부재로 실패하는 계산 테스트를 먼저 실행한 뒤 구현했다. 최소 양수 효율의 시간 곱이 0으로 소실되는 경계도 실패를 재현한 후 입력 단계에서 거부했다.
- `cargo fmt --all -- --check`, `cargo clippy --all-targets --locked -- -D warnings`, `cargo build --locked`, `git diff --check` 통과.
- `TEST_REDIS_URL=redis://127.0.0.1:6380/0 cargo test --locked -- --include-ignored`: unit 24/24, CLI 3/3 통과.
- 실제 CLI의 도움말·합성 정상 입력·없는 입력 파일 확인. 정상 결과 stdout JSON, 실패는 exit 1 + stderr, 실패 stdout 비어 있음.
- 합성 ESS 사례: 5→5.9→4.65MWh; 수요+10%/풍력−20%: 최종 3.083333333MWh, 마지막 잔차20.8MW. 실제 제주 계측을 사용한 결과가 아니다.
- 결과: `var/verification/simulation-result.json`, `simulation-scaled-result.json`. Docker 이미지 빌드 및 예제 volume을 마운트한 CLI 실행 성공, `simulation-docker-result.json`에서 complete/최종 4.65MWh를 확인했다.
- [독립 코드 검토](../../../.omo/evidence/simulation-code-review.md): APPROVE, blocker 없음. 작업 트리 대상 검토이며 커밋 SHA 승인 아님.
- LSP는 데몬 timeout이 계속되어 미확인. 컴파일러와 clippy에서 오류·경고 없음.
- 브릿지/HTTP/실측 하루 자료 검증은 외부 데이터가 준비된 다음 단계다. 모델 다운로드·기존 API/Redis 실행은 유지했고 커밋·푸시는 하지 않았다.
