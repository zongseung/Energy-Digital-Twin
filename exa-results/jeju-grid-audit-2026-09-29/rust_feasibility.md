# 제주 전력망 디지털 트윈: Rust 실현 가능성 조사

- 조사일: 2026-09-29, Asia/Seoul
- 입력 문서: `jeju_power_grid_digital_twin_plan.md` v0.1. 특히 §6–7, §12, §17, §20–21의 Python 해석 계층을 Rust 실행부로 바꾸는 범위.
- 방법: Exa Search 스킬과 `searching.md`, `patterns-code.md`, `source-quality.md` 적용. 5개 독립 관점 검색 뒤 공식 저장소·API·원문 검토.
- `sources_reviewed: 50` = Exa `numResults` 합계(10 × 5). 실제 검색 결과 50개, 정확한 URL 기준 중복 제거 뒤 50개. 이는 독립적으로 검증된 논문 50편이라는 뜻이 아니다.
- 구현·설치·벤치마크 실행·DB/마운트 조사는 하지 않았다. 라이브러리 속도와 정확도 수치는 자체 재현하지 않았다.

## 결론과 최소 변경

**실행부를 Rust로 두는 계획은 실현 가능하다.** 다만 pandapower/PyPSA가 제공하는 모든 설비 모델·해석·최적화를 한 번에 재구현하는 계획은 검증 범위를 크게 늘린다. 기존 PostGIS, Parquet, GeoJSON과 상태 API는 유지하고, Level 3 시계열 재생 → DC PF → 검증된 AC PF → DC dispatch/curtailment 순으로 확장하는 것이 가장 작은 변경이다. 이것은 조사에 근거한 설계 권고이며 성능 측정 결과는 아니다.

권고 연결: `PostGIS/Parquet → Rust(sqlx/serde) → Rust 계산 작업 → axum 상태 API → Unreal C++/Blueprint + Cesium → 기존 영상 출력`. 초기에는 단일 Rust 실행 파일 안에서 데이터/API와 제한된 계산 작업을 처리할 수 있다. 미리 마이크로서비스, 커스텀 solver 추상화, Unreal용 Rust 플러그인을 만들 필요는 없다.

Rust의 타입·메모리 안전성이 잘못된 topology, 단위, 초기조건, 미수렴, 수치 오차를 자동으로 해결하지는 않는다. Tokio 비동기 서버도 CPU 수치해석의 마감시간을 보장하지 않는다. 데이터 유효성 검사, solver 상태와 잔차 검사, 재현 가능한 기준 해석기 비교가 별도로 필요하다. [Tokio blocking 작업 문서](https://docs.rs/tokio/latest/tokio/task/fn.spawn_blocking.html), [MATPOWER AC PF 설명](https://matpower.app/manual/matpower/ACPowerFlow.html).

## 후보별 정확한 지원 범위

`Phase`는 원 기획서의 단계와 대응한다. P0 데이터 감사, P1 공간 모델, P2 전력망 모델, P3 시계열, P4 Unreal. 최적화는 P2 이후 별도 확장이다. 라이선스는 확인한 원문 기준이며 선택 feature와 전이 의존성까지 한꺼번에 같은 라이선스라고 가정하지 않는다.

| 라이브러리/도구 | 확인한 지원 | 제약·사용 판단 | 원문/라이선스 근거 | Phase |
|---|---|---|---|---|
| faer | Rust 희소 행렬, triangular solve, sparse Cholesky/LU/QR. Hermitian 행렬 지원 API가 있음 | 일반 AC Newton Jacobian은 보통 비대칭이므로 LU가 우선 후보. Cholesky는 양의 정부호 조건 확인 후 사용. 저수준 tuning 문서 일부 미완성. PF 모델 자체는 제공하지 않음 | [희소 solve](https://faer.veganb.tw/docs/sparse-linalg/linsolve/), [복소수 API](https://docs.rs/faer/latest/faer/), [MIT](https://docs.rs/crate/faer/latest/source/Cargo.toml). GitHub 저장소는 Codeberg로 이전한 mirror라고 명시 | P2 |
| nalgebra / nalgebra-sparse | nalgebra는 행렬·벡터 계산, nalgebra-sparse는 COO/CSR/CSC 자료구조와 기본 연산 | nalgebra-sparse 원문은 sparse system solver가 제한적/없으며 complex 지원도 제한적이라고 명시. nalgebra만으로 대규모 희소 AC PF 완성이라 주장할 수 없음 | [희소 범위/한계](https://docs.rs/nalgebra-sparse/latest/nalgebra_sparse/), [Apache-2.0](https://github.com/dimforge/nalgebra/blob/dev/Cargo.toml) | P2 소규모 검증/보조 |
| sprs / sprs-ldl | CSR/CSC, triplet 조립, 희소 곱셈, triangular solve; 별도 sprs-ldl은 LDLᵀ 분해 | sprs의 기본 자료구조를 임의 일반 Jacobian LU solver로 오인하지 말 것. sprs-ldl의 적용 행렬 조건과 수치 안정성 확인 필요. faer로 충분하면 중복 추가 불필요 | [sprs 범위](https://docs.rs/sprs/latest/sprs/), [sprs MIT OR Apache-2.0](https://docs.rs/crate/sprs/latest), [sprs-ldl LGPL-2.1](https://docs.rs/crate/sprs-ldl/latest/source/Cargo.toml) | P2 선택적 |
| good_lp + HiGHS feature | Rust LP/MILP 모델러와 HiGHS solver 호출. 연속·정수 변수, 선형 목적/제약 | **good_lp는 solver가 아니며 quadratic expression 미지원.** 선형 비용 DC dispatch, curtailment, 선형화된 UC 후보. 기본 CBC가 자동 선택되지 않도록 feature를 명시적으로 고정 | [지원·제약/feature](https://github.com/rust-or/good_lp), [good_lp MIT](https://raw.githubusercontent.com/rust-or/good_lp/main/LICENSE) | P2 이후 최적화 |
| highs 직접 API / native HiGHS | Rust safe binding. LP/MILP, `Model::try_pass_hessian`으로 QP 목적의 Hessian 업로드. native HiGHS는 LP/MIP/convex QP 지원 | convex QP에서 Hessian PSD 필요. HiGHS의 원문은 정수 변수 허용을 Q=0인 경우로 설명하므로 일반 MIQP 지원으로 확대하지 말 것. C++ build toolchain 필요. 일반 비선형 AC-OPF solver 아님 | [Rust QP API](https://docs.rs/highs/latest/highs/struct.Model.html), [binding MIT](https://github.com/rust-or/highs), [native 범위/라이선스](https://github.com/ERGO-Code/HiGHS) | P2 이후 QP/MILP |
| Clarabel.rs | Rust convex conic interior-point solver. LP, QP, SOCP, SDP, exponential/power cones | 직접 Clarabel API를 쓰면 QP 가능하지만 good_lp 경유는 선형 모델러 범위. 정수 변수 없음. 비볼록 AC 방정식의 일반 NLP solver 아님. SDP·선형대수 optional backend의 build/dependency 조건은 실제 feature 선택 후 별도 확인 | [공식 repo](https://github.com/oxfordcontrol/Clarabel.rs), [문제/라이선스 문서](https://docs.rs/clarabel/latest/clarabel/), Apache-2.0 | P2 이후 convex QP/relaxation |
| Ipopt native | 대규모 연속 비선형·비볼록 최적화, 국소해. 미분 가능한 목적·제약과 derivative 필요 | Rust에서 모델 조립 후 native solver를 호출하는 AC-OPF 확장 후보. 이번 조사에서는 사용할 Rust binding의 안정성·API를 선정하지 않았음. 전역 최적해 보장 아님. native linear algebra dependency 존재 | [공식 범위/EPL](https://github.com/coin-or/Ipopt) | AC-OPF 필요 시 |
| sqlx + PostgreSQL/PostGIS | Rust async SQL, PostgreSQL 접속/타입 매핑. 기존 공간 연산을 PostGIS에 유지 가능 | SQLx의 PostgreSQL 내장 geometry 타입을 PostGIS geometry/geography와 동일하다고 보지 말 것. MVP는 DB에서 `ST_AsGeoJSON(ST_Transform(geom,4326))`을 text/json으로 반환하면 별도 GIS binding이 필요 없음 | [sqlx](https://github.com/launchbadge/sqlx), [PG 타입](https://docs.rs/sqlx/latest/sqlx/postgres/types/index.html), [GeoJSON](https://postgis.net/docs/ST_AsGeoJSON.html), [MIT OR Apache-2.0](https://docs.rs/crate/sqlx/latest/source/Cargo.toml) | P0–P3 |
| axum + Tokio | HTTP routing, JSON, WebSocket 상태 전달; 비동기 DB/IO | CPU solver를 async worker 위에서 직접 길게 실행하지 말 것. 제한된 `spawn_blocking` 작업으로 시작. 시작된 blocking 작업은 `abort()`로 취소되지 않으므로 solver 자체 iteration/time limit과 동시 계산 상한 필요 | [axum MIT](https://github.com/tokio-rs/axum), [Tokio CPU/취소 제약](https://docs.rs/tokio/latest/tokio/task/fn.spawn_blocking.html), [Tokio MIT](https://docs.rs/crate/tokio/latest/source/Cargo.toml) | P1–P4 |
| parquet (Apache Arrow Rust) | 공식 native Rust Parquet reader/writer, Arrow batch API | 대량 시계열 재생·snapshot 교환에 사용. 기존 SQL 조회로 작은 MVP가 되면 DataFrame 엔진 추가는 후순위. Parquet 자체는 시간 정렬·timezone·단위·ID 의미를 보장하지 않음 | [공식 구현](https://docs.rs/parquet/latest/parquet/), [Apache-2.0](https://docs.rs/crate/parquet/latest/source/Cargo.toml) | P0/P3 |
| serde + serde_json | Rust struct의 직렬화/역직렬화. ID·timestamp·status가 포함된 상태 payload | 역직렬화 성공은 물리적 입력 검증 성공과 다름. finite 값, 단위, ID 참조, timestamp 정책을 별도로 검사 | [Serde](https://serde.rs/), [MIT OR Apache-2.0](https://docs.rs/crate/serde/latest/source/Cargo.toml) | 전체 |
| Cesium for Unreal + Unreal network API | 기존 Cesium 플러그인과 WGS84 좌표/Unreal 객체. Epic의 C++ `FWebSocketsModule` API 확인 | 공식 구현의 언어·접점은 C++/Blueprint. Rust backend에서 HTTP/JSON 상태를 제공하고 Unreal 측 기존 코드가 소비하는 구조가 최소. Rust만으로 Cesium Unreal native plugin을 대체한다는 범위는 추가 FFI/빌드/엔진 ABI 작업을 뜻함 | [Cesium repo Apache-2.0](https://github.com/CesiumGS/cesium-unreal), [공식 C++ 좌표 예제](https://cesium.com/learn/unreal/unreal-flight-tracker), [Epic WebSocket API](https://dev.epicgames.com/documentation/en-us/unreal-engine/API/Runtime/WebSockets/FWebSocketsModule) | P4 |

HiGHS 라이선스는 core와 선택한 배포 artifact를 구분해야 한다. 공식 README는 core/MIT package와 HiPO 포함 Apache package를 구분한다. faer, sqlx 등 `latest` 문서와 branch 원문이 서로 다른 시점의 내용을 반환할 수 있어 이번 문서는 최신 버전 번호를 확정하지 않는다. 구현 시 release/commit과 lockfile을 고정한 뒤 해당 API·LICENSE·전이 의존성으로 다시 확인해야 한다. [HiGHS 원문](https://github.com/ERGO-Code/HiGHS).

## Rust 전력망 생태계: 후보는 있으나 동등성은 직접 검증

검색으로 Rust PF/OPF 후보를 찾았으므로 “Rust에는 성숙한 전력망 라이브러리가 없다”는 부재 주장은 하지 않는다. 반대로 README의 광범위한 기능·production-grade·몇 배 빠르다는 문구만으로 pandapower/GridCal 수준의 설비 모델, 오류 처리, validation coverage를 확보했다고 결론내리지 않는다.

| 후보 | 공식 원문에서 확인한 범위 | 이번 조사에서 남은 검증/라이선스 | 도입 단계 |
|---|---|---|---|
| [rustpower](https://github.com/chengts95/rustpower) | Newton–Raphson steady-state PF, pandapower JSON, transformer/switch, Q-limit plugin; RSparse/KLU backend | switch 일부 experimental 명시. speedup은 저자 benchmark이므로 채택하지 않음. [Cargo.toml](https://raw.githubusercontent.com/chengts95/rustpower/master/Cargo.toml) MPL-2.0. KLU feature의 실제 SuiteSparse component/license 추가 검토 | P2 비교 실험 후보 |
| [powers / powers-pf](https://github.com/powe-rs/powers) | MATPOWER에서 Rust로 번역. Newton, fast-decoupled, Gauss–Seidel, DC PF | 공개 README는 OPF/CPF/PTDF의 추가 기능을 저자 요청 사항으로 구분. 공개 패키지와 API의 실제 확보 범위를 먼저 확인. repo 설명 BSD-3-Clause | P2 비교 실험 후보 |
| [gridoxide](https://github.com/m-mirz/gridoxide) | sparse NR AC PF, symmetric/asymmetric, WLS state estimation, Q limit, faer/block/KLU native/FFI/PARDISO 후보. [비교 harness](https://raw.githubusercontent.com/m-mirz/gridoxide/main/scripts/bench/README.md) 있음 | **기본 빌드도** Rust KLU 번역을 포함하여 README가 `Apache-2.0 AND BSD-3-Clause AND LGPL-2.1-or-later`를 명시. `pure Rust`는 copyleft가 없다는 뜻이 아님. benchmark 속도/일치 결과는 독립 재현 필요 | P2 기능/라이선스 검토 후보 |
| [OxiGrid](https://github.com/cool-japan/oxigrid) | README에 AC/DC PF, Q limits, state estimation, three-phase, DC/AC OPF, IEEE 사례, MATPOWER import 명시 | 넓은 기능 주장은 src/tests에서 기능별 재현 필요. README Apache-2.0 badge만 확인; LICENSE fetch 실패로 상세 라이선스 확인 미완료. 완제품 선정 근거로 쓰지 않음 | P2 탐색 후보 |
| [GAT](https://github.com/monistowl/gat) | README의 DC/AC PF, native CLP/CBC/Ipopt 구성, MATPOWER/PGLib benchmark 명령 | README 앞부분 AC-OPF/Ipopt 검증 주장과 backend 표의 “coming soon”이 함께 존재. 실제 release 기능 확인 필요. LICENSE fetch 실패, workspace Cargo만으로 라이선스 확정 불가 | 필요 시 후속 검증 후보 |

이들 후보 중 바로 하나를 최종 선정하는 대신 같은 입력·solver 조건으로 IEEE/MATPOWER 비교를 통과하는 후보를 먼저 재사용한다. 표준 모델을 구현할 라이브러리가 충분하면 자체 NR/OPF 코드를 만들 이유가 없다. 후보가 필요한 모델·오류 처리·라이선스 조건을 충족하지 못할 때 faer 기반 최소 DC/AC 계산을 검토한다.

## Pure Rust와 Rust + native solver의 선택

| 방향 | 얻는 것 | 추가로 부담할 것 | 권고 |
|---|---|---|---|
| Rust 데이터/API + pure Rust PF/Clarabel | C/C++ solver 툴체인 의존성을 줄일 수 있고 계산 코드 추적이 쉬움 | PF의 PV/PQ/slack, transformer tap/phase shift, Q-limit, islanding, convergence를 직접 또는 후보 구현으로 검증. Clarabel만으로 UC 정수 변수/일반 AC-OPF 해결 불가 | DC PF와 convex QP는 적합한 출발점. all-Rust가 필수인 경우 범위를 이 수준부터 검증 |
| Rust 데이터/API + HiGHS 등 native solver | 실행부와 모델 조립은 Rust 유지. LP/MILP/convex QP는 기존 수치 solver 재사용 | native build/reproducible deployment/FFI error mapping 및 dependency license 관리. native solver도 formulation이 틀리면 잘못된 답을 계산 | DC dispatch/curtailment와 UC가 필요해질 때 기본 권고 |
| Rust 실행부 + native NLP (예: Ipopt) | 비선형 AC-OPF formulation을 기존 optimizer에 전달 가능 | derivative, scaling, local optimum, feasibility, linear solver 연결, release/라이선스 확인 | AC PF 및 DC optimization 검증 후 실제 요구가 있을 때 추가 |

Rust orchestrator라고 해서 Python을 운영 런타임에 반드시 넣어야 하는 것은 아니다. MATPOWER/Octave 또는 pandapower를 **offline 기준 결과 생성**에만 쓰고 Rust 배포 실행부는 독립시킬 수 있다. 검증 도구의 언어와 최종 제품의 언어는 별도 결정이다.

## 최소 프로토타입과 통과 기준

아래 허용오차는 이 프로젝트에 제안하는 초기 검증 기준이다. 국제 표준의 의무값이나 solver의 보장값이 아니다. 작은 IEEE 사례로 통과시킨 뒤 어려운 topology/운전점에서 조건수와 residual을 검토하며 조정한다.

1. **Level 3부터:** 기존 `plants`, `lines`, bus mapping과 시계열을 Rust에서 조회해 시간별 상태를 API로 반환. UTC 저장/표시 timezone, MW/Mvar/MVA, 좌표계와 longitude/latitude 순서 고정. 출력에 `source`, `model_version`, `scenario_id`, `timestamp`, `status`를 둔다. 데이터가 없는 전기량을 계산 결과로 꾸미지 않는다.
2. **DC PF:** MATPOWER [case14](https://raw.githubusercontent.com/MATPOWER/matpower/master/data/case14.m), [case30](https://raw.githubusercontent.com/MATPOWER/matpower/master/data/case30.m), [case118](https://raw.githubusercontent.com/MATPOWER/matpower/master/data/case118.m)의 동일 입력에서 `rundcpf`와 비교. 30-bus 파일에는 여러 변형이 있으므로 “IEEE 30” 이름만 맞추지 말고 정확한 파일 hash와 baseMVA, status, tap, shift를 고정. AC 연결 성분마다 기준각/slack·전력수지 조건을 명시하고 reduced B를 구성. zero/near-zero X, de-energized line, parallel line, 분리 island를 검증.
3. **AC PF:** 후보 Rust 라이브러리를 먼저 비교. NR polar P/Q formulation, Ybus와 branch π 모델, generator/PV/PQ/slack 처리, shunt, transformer tap/phase shift, Q-limit enforcement 옵션을 MATPOWER와 맞춘다. [MATPOWER 설명](https://matpower.app/manual/matpower/ACPowerFlow.html)은 PF 기본 실행이 generator/branch/voltage limits를 모두 강제하지 않는다고 명시하므로 PF 수렴과 운전 적합성을 구분한다.
4. **정확도 검사 제안:** 반복 종료의 max P/Q mismatch `≤ 1e-8 p.u.`; 기준각을 일치시킨 후 bus `|ΔVm| ≤ 1e-6 p.u.`, `|Δθ| ≤ 1e-5 rad`; branch P/Q의 차이 `≤ 1e-5 × baseMVA` MW/Mvar를 출발값으로 삼는다. 부동소수점 bitwise 일치는 요구하지 않는다. residual, 모든 P/Q·loss balance, solver status, finite 결과를 함께 검사한다. 다른 Q-limit active set이면 먼저 모델 조건을 확인한다.
5. **오류 경로 검사:** 비정상 단위·없는 bus ID·nonfinite 값·부적절한 zero impedance·슬랙 없는 island·특이행렬·최대 iteration·미수렴은 성공 데이터로 반환하지 않는다. UI에는 stale last-known state와 실패 사유를 명시하고 새 계산의 성공으로 표시하지 않는다. 이전 성공 결과를 조용히 재사용해 timestamp만 바꾸는 방식은 금지한다.
6. **N−1/시계열:** topology와 Jacobian sparsity가 유지될 때만 symbolic factorization/warm start를 재사용한다. outage, island, PV→PQ 전환으로 구조가 바뀌면 유효성을 다시 확인. 한 시점의 수렴을 모든 시점의 수렴으로 일반화하지 않는다.
7. **DC 최적화:** 선형 비용은 good_lp+HiGHS, convex quadratic cost는 직접 highs Hessian API 또는 Clarabel. 동일 제약·cost curve로 `rundcopf`와 비교하고 objective gap뿐 아니라 bound/line/power-balance violation을 검사. binary UC와 convex QP는 다른 solver capability로 취급한다.
8. **Unreal 표시:** 초기에는 snapshot HTTP 조회나 미리 계산한 상태 재생으로 충분. 낮은 latency push가 실제로 필요해지면 WebSocket. solver 시간과 render frame rate를 결합하지 말고 timestamp가 붙은 결과를 갱신. 고빈도 SCADA·EMT·protection·converter control은 원 MVP 범위 밖이므로 별도 요구가 생길 때 검토.

MATPOWER 코드는 BSD-3-Clause이나 LICENSE가 일부 case data의 별도 조건 가능성을 명시하므로 test data를 묶어 배포할 때 원 파일의 provenance를 유지한다. [MATPOWER LICENSE](https://raw.githubusercontent.com/MATPOWER/matpower/master/LICENSE).

HVDC를 초기 정상상태 연구 모델에서 부호·용량·손실이 명시된 제어 가능한 P injection/transfer로 두는 것은 설계 선택이다. AC reactive support, converter voltage control, DC 회로, EMT 동특성을 구현했다는 뜻이 아니다. 입력 데이터가 실제 제어 특성을 뒷받침할 때 모델을 늘려야 한다.

## 검색 로그 및 원문 접근 한계

모든 검색은 Exa `web_search_exa(query, objective, numResults)`로 실행했다. 검색 성공, 인증/연결 실패 없음. 결과 중 Stack Overflow/마케팅 블로그/aggregate crate 소개는 최종 기술 주장 근거로 사용하지 않았다. `web_fetch_exa`의 원문 수집은 총 57 URL 요청(중복 재검토 포함)이었으며 50이라는 지표에는 더하지 않았다.

| # | 독립 각도 | query (실제 전달 문자열) | objective 요지 | numResults |
|---|---|---|---|---|
| 1 | 희소 선형대수 | Rust faer nalgebra sprs official documentation sparse LU Cholesky complex matrix linear system licenses | 공식 문서/repo 우선; sparse solve, complex와 license | 10 |
| 2 | 최적화 | Rust good_lp highs HiGHS Clarabel official documentation linear mixed integer quadratic conic optimization supported problems | wrapper/native 분리; LP/MILP/QP/conic와 AC nonlinear 한계 | 10 |
| 3 | 전력망 생태계 | Rust power flow optimal power flow library pandapower GridCal MATPOWER IEEE benchmark Rust repository | 실존 후보와 검증 사례; absence 주장 배제 | 10 |
| 4 | 데이터/API | Rust sqlx PostGIS axum tokio parquet serde official documentation PostgreSQL geography GeoJSON | 최소 역할; PostGIS geometry decoding과 DB 중심 대안 | 10 |
| 5 | Unreal/Cesium | Cesium Unreal native C++ plugin Unreal HTTP WebSockets Rust backend integration official documentation | 공식 native 접점과 network integration 제약 | 10 |

원문 fetch는 문서의 링크를 직접 확인하기 위한 후속 단계이며 API feature를 실제로 빌드한 검증은 아니다. 다음 접근 실패를 숨기지 않았다.

- `https://nalgebra.rs/docs/user_guide/sparse_matrices/`: CRAWL_NOT_FOUND → 공식 docs.rs nalgebra-sparse로 지원 한계 확인.
- `https://dev.epicgames.com/documentation/en-us/unreal-engine/API/Runtime/HTTP/FHttpModule`: Exa 결과가 Error 403 페이지. `.../CreateRequest` fetch도 실패. 공식 Cesium C++ 예제와 Epic FWebSocketsModule 원문은 성공했으며, HTTP 세부 API signature는 확정하지 않음.
- `https://raw.githubusercontent.com/powe-rs/powers/main/README.md`: CRAWL_NOT_FOUND → GitHub repo 본문의 기능/라이선스 설명 확인.
- OxiGrid raw `LICENSE`와 `LICENSE-APACHE`, GAT raw `LICENSE`: CRAWL_NOT_FOUND → 최종 라이선스 선정 유보.
- `https://clarabel.org/stable/installation_rust/`: CRAWL_NOT_FOUND → 공식 repo/API는 확인; SDP feature의 native build 조건을 이번 조사에서 확정하지 않음.

최종 선택 전 남은 가장 작은 작업은 후보 1–2개의 release/feature를 고정하고 동일 case14/30/118 입력·옵션의 비교 결과와 license manifest를 남기는 것이다. Rust 자체를 전면 포기할 근거는 없고, 초기부터 전체 전력계통 연구 스택을 직접 재구현할 근거도 없다.
