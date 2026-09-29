# 제주 전력망 디지털 트윈: 마운트 데이터 감사·수학 모델·Rust 문헌 검토

- 조사일: 2026-09-29, Asia/Seoul. 운영 DB는 조회 중에도 갱신되므로 아래 행 수와 최신 시각은 각 감사 쿼리의 시점 기준이다.
- 검토 대상: [기획서 v0.1](jeju_power_grid_digital_twin_plan.md), `/mnt/iscsi`, `/mnt/iscsi-renewable`.
- 방법: 마운트/컨테이너 볼륨 대조, CSV 전수 파일 목록 및 읽기 가능한 파일의 내용 검사, PostgreSQL 스키마·실제 제주 레코드·품질 조회, 기존 계산 코드 읽기, Exa 원문 조사.
- DB 조회는 `BEGIN READ ONLY`와 statement timeout을 사용했다. 원본 데이터·운영 서비스·기존 코드를 수정하지 않았다.
- Exa 검색 후보 합계 **179건**: 제주 89 + 수학 40 + Rust 50. 중복을 포함한 검색 요청 결과 수이며 179개 독립 논문을 정독한 수가 아니다. 원문 검토·채택·접근 실패·검색어는 분야별 보고서에 따로 기록했다.

## 1. 판단

**공간·시계열 기반 연구용 트윈을 시작할 데이터는 이미 상당 부분 있다. 가장 큰 결손은 실제 전기적 연결관계와 파라미터, 버스별 부하, 링크별 HVDC 계측이다.**

OSM 기반 선로·변전소·발전설비 GIS, 제주 시간별 수요, 5분 수급/풍력/태양광, 일부 개별 발전소 계량, 제주 ASOS가 있다. 한전 주소–변전소–변압기–배전선로 접속정보도 확보되어 있다. 이 자료들을 먼저 연결·검증하는 편이 새 GIS/수집 파이프라인을 만드는 것보다 유리하다.

기획서 기준 **Level 2의 공간 자료와 Level 3의 지역 집계 시계열은 확보**, 설비별 동적 상태와 연결관계는 부분 확보다. **실제 계통을 검증한 Level 4는 아직 충족하지 않는다.** 기존 DC 조류 결과가 존재하지만 추정 파라미터·분담·연결을 사용하는 시연 결과다.

Rust를 실행부·API·수치 모델에 사용하는 방향은 타당하다. 실제 제주 계통 정확도를 결정하는 첫 조건은 언어보다 입력 데이터와 검증이다. 순수 Rust, Rust + native solver 중 무엇을 선택해도 같은 물리 모델·기준 사례 검증을 통과해야 한다.

## 2. 두 마운트에서 확인한 것

| 마운트 | 확인한 용도 | 사용 현황 | 실제 연결된 DB |
|---|---|---|---|
| `/mnt/iscsi` (`/dev/sdb`) | 수요 PostgreSQL, SQL 백업 | 약 98 GiB 중 2.5 GiB, 3% | `demand-postgres` → `/mnt/iscsi/postgres/demand-postgres` |
| `/mnt/iscsi-renewable` (`/dev/sdc`) | 제주 CSV, 재생발전/기상 DB, 공간 DB | 약 98 GiB 중 62 GiB, 67% | `pv-data-postgres` → `postgres/pv-data-postgres`; `energy-hub-db` → `postgres/energy-hub-data` |

조사 환경에서 실제 ext4 마운트는 읽기 전용으로 노출되었다. PostgreSQL 저장 파일을 직접 해석하거나 다른 서버로 열지 않고 실행 중인 DB를 읽었다. `lost+found`는 데이터 감사 대상에서 제외했다.

`iscsi-renewable/postgres/pv-db-data`, `pv-main-data` 디렉터리도 존재하지만 실행 중인 감사 대상 DB의 볼륨으로 연결되지 않았다. 디렉터리 존재만으로 내용·신선도·중복 여부를 확정하지 않았다. `pv-prefect-postgres`는 두 마운트가 아닌 Docker named volume을 사용하므로 보유 계통 데이터로 세지 않았다.

`/mnt/iscsi/backup/demand_2026-02-03.sql.gz`에는 `demand_5min`, `demand_weather_1h`, `heat_demand`, `heat_demand_location`의 CREATE/COPY가 확인된다. 현재 제주 5분 자료는 실행 중인 `demand-postgres.public.jeju_supply_demand`에 있으며 이 오래된 백업만으로 현재 DB를 대표하면 안 된다.

## 3. 기획서 필수 데이터와 실제 보유 자료

공간 검색은 기본적으로 제주 bbox `(126,33,127,33.7)`와 `sido`를 사용했다. 선로는 육지–제주 해저케이블 확인을 위해 bbox 북쪽 경계를 34까지 넓혀 교차 검증했다. 아래 개수는 GIS 객체/레코드 수이며 전기적 회선·발전단지·bus 개수와 동일하지 않다.

| 데이터 | 실제 위치 / 주요 스키마 | 제주 확인 결과 | 사용할 범위 / 남은 조건 |
|---|---|---|---|
| 송·배전선 GIS | Hub `public.power_line`: `id,name,geom,power_type,voltage,sido` | 제주 `sido` 51레코드. 제주 교차 케이블까지 포함하면 54레코드. EPSG:4326 LineString, geometry invalid 0; 전압 누락 5개 | 공간 표시 가능. `from_bus/to_bus`, R/X/B, 정격, 회선/개폐 상태 없음. 지리 선분을 실제 회선으로 바로 쓰지 않기 |
| 변전소·변환소 GIS | Hub `public.substation`: `id,name,geom,voltage,sub_type,osm_id,...` | 13레코드, 전압 누락 0, 이름 없는 180 kV 변환소 1개. 서제주변환소는 154/250 kV 별도 레코드 | 전압은 문자열 V 단위(`154000`)이므로 kV 변환 필요. 이름 기준 병합은 전압별 bus를 잃을 수 있음. 용량·busbar·변압기 임피던스 없음 |
| OSM 발전설비 | Hub `public.power_plant`: `id,name,geom,plant_source,plant_output,osm_id,...` | wind 165, solar 704, gas 1, oil 2, diesel 8 등 | 풍력 165개를 165개 풍력단지로 해석 금지. 일부는 터빈/발전기 단위. wind 40개, diesel 8개는 출력 문자열 없음. `plant_output` 단위 파싱·공식 설비 대조 필요 |
| 태양광 인허가/설비 | Hub `public.pv_facility`: `geom,capacity_kw,status,permit_date,data_date,source_file,...` | 제주 bbox 1,791레코드, 모두 좌표·양수 용량·정상가동 표시. 등록 용량 합 593,212.25 kW | 위치·용량 배분 근거. 데이터 기준일·동일 사업 중복·현재 운전 상태 별도 검증. OSM solar 객체와 합산 금지 |
| 개별 발전소 master/계량 | PV `public.plants` + `generation(timestamp,plant_id,gen_kwh,source)` | 좌표/지역 기준 제주 후보 2개: 남제주소내 PV `plant_id=3`, Hangyoung wind `51` | 안정 ID를 사용할 수 있으나 전 제주 설비를 대표하지 않음. 둘 다 `capacity_mw` NULL. plant→bus 매핑 없음 |
| 발전원별 시간 거래량 | `/mnt/iscsi-renewable/jeju_data/gen/jeju_gen_2024.csv`; Hub `jeju_generation_mix(ts,fuel_type,gen_mwh)` | 46,680행, 2024.01.01–12.31, 6개 발전원. 5개는 8,784시간, oil은 2,760시간 | 시장참여 송전단 **전력거래량 MWh**. 발전소별 MW나 제주 전체 실제 발전과 다름. PPA/자가용 제외 범위를 보완 |
| 시간별 전체 수요 | `jeju_data/demand/jeju_demand_2021..2026.csv`; Hub `jeju_demand_hourly(ts,demand_mw,source)` | 합 48,119행, 2021.01.01–2026.06.30 23:00 | 지역 수요 재생/예측 가능. bus별 부하 아님. 2026 하반기는 5분 자료에서 품질을 확인해 집계 가능 |
| 5분 전체 수급·재생 출력 | `jeju_data/sukub/*.csv`; Demand `jeju_supply_demand(ts,supply_mw,demand_mw,renewable_total_mw,solar_mw,wind_mw)` | 월별 CSV 69개, 2021.01–2026.09. DB 602,673행, 당시 최신 2026.09.29 17:50. Hub 최신 복제/조회 18:05 확인 | 최신 동적 지역 상태의 우선 원천. `supply_mw`는 **공급능력**, 실제 발전출력이 아님. HVDC별 P, thermal 실제 출력 없음 |
| 한전 접속정보 | Hub `research.kepco_grid`: 주소, `subst_nm/subst_cd`, `mtr_no`, `dl_nm/dl_cd`, 용량 필드 | 제주 주소 레코드 99,860건, 변전소 코드 15개, `(subst_cd,mtr_no)` 54개, `(subst_cd,mtr_no,dl_cd)` 146개 | 설비/공급구역 연결의 유용한 근거. 99,860개 부하 또는 54개 실제 변압기로 확정하지 않음. ID 조합·비활성/예약 정보·용량 단위를 원천 명세로 검증 |
| 제주 ASOS | PV `weather_asos(timestamp,station_name,temperature,humidity,solar_radiation)` | 제주·고산·성산·서귀포 각 51,072행. 2021–2025와 2026.09.28까지 시간 슬롯 확보; 2019는 744행, 2020 없음 | 기온/습도·일사 기반 예측. 이 테이블에는 풍속/풍향 없음. 일사 MJ/m² 누적→평균 W/m² 변환 및 구간 라벨 검증 필요 |
| 풍속/풍향 일자료 | Hub `pf.aws_obs_daily(...,ws,wd,imputed)` | 전국 일자료 테이블은 존재. 제주 ASOS ID 184/185/188/189 및 4개 제주 지점명 조회 결과 0행 | 현 보유 제주 시간별 풍속 자료로 세지 않음. 다른 제주 AWS 지점 존재 여부와 실제 지역 커버리지는 후속 확인 |
| 가격 | PV `smp_realtime_jeju(timestamp,price,is_confirmed)` 및 `smp_hourly` | 제주 실시간 SMP 82,656행, 2024.03.01–2026.09.27 23:45, confirmed 표시 | 최적화 비용/시장 상태 설명에 사용. 가격차 부호만으로 실제 HVDC 방향을 관측했다고 간주 불가 |
| 기존 계산 결과 | Hub `jeju_grid_summary`, `jeju_grid_line_state` | 2026.06.13 00:00–01:55의 24 snapshot; 선로상태 240행/10 ID, 추정연결 표시 48행 | 시연 구조 참고용. 최신 실측, 전 제주 실망 또는 검증 완료 결과로 세지 않음 |

전국 수요/발전원별 5분 자료와 전국 기상·재생발전도 두 DB에 있다. 육지 수급·가격을 HVDC 시나리오의 외생조건으로 활용할 수 있지만 제주 자료와 지역 경계를 분리해야 한다. Hub/PV의 `research` 뷰·FDW는 같은 원천을 다른 DB에서 노출하기도 하므로 추가 데이터로 중복 집계하지 않는다.

## 4. 확인된 품질 문제와 해석상의 주의

### CSV 감사

- CSV 총 76개 중 74개 내용을 읽었다. 2026.08/09 수급 CSV는 `nobody`, mode 0600으로 파일 읽기가 제한되었고, 승인된 `sudo -n`도 비밀번호 요구로 실패했다. 권한은 변경하지 않았다. 해당 월을 포함하는 DB의 실제 행은 읽었지만 두 CSV의 파일별 전수 품질은 미검증이다.
- 수요 파일: 2021 8,760행; 2022 8,738행(22시간 누락); 2023 8,734행(26시간 누락); 2024 8,784행; 2025 8,759행(1시간 누락); 2026 4,344행(6월까지). 중복 key 0. 기존 행 안의 공란은 없지만 시간 슬롯 누락은 총 49시간이다.
- 읽은 수급 67파일: 585,498행, 동일 timestamp 중복 1행(2026.07), 파일의 관측 시작–끝 사이 5분 슬롯 누락 합 1,434개. 월 경계 바깥 슬롯은 이 누락 집계에 포함하지 않는다. 원천 수집/적재가 중복 제거를 수행하므로 CSV 행수와 DB 행수는 동일할 필요가 없다.
- 2024 `oil`은 04.24 23:00 이후 6,024시간 레코드가 없다. 빈 행 없이 다른 발전원이 계속되므로 원천의 연료 분류/제공범위 변화부터 확인한다. 확인 전에 누락을 모두 발전 0으로 채우지 않는다.
- 2026 수요 CSV는 `datagokr` 2,614행 + `sukub_5min` 1,730행이다. 시간 평균 방식·수요 정의의 차이를 source별로 교차 확인한다.

### DB 감사

- 수급 DB에서 demand 최소 0, 최대 1,920 MW; wind 최대 1,058.38 MW, 1,000 MW 초과 1행; `renewable_total_mw < solar_mw + wind_mw` 51행이 확인됐다. 정상 운전/동일 정의로 바로 사용할 값이 아니라 원천 응답·수집 시각·필드 의미를 확인할 후보다. 무조건 clip 또는 0 대체하면 연구 결과가 바뀐다.
- 남제주소내 태양광: 90,050행, 2015.10.23 01:00–2026.09.27 23:00, 음수 `gen_kwh` 24행, 최대 85,525 kWh. 실제 원자료의 계량 범위·단위·시간 라벨과 설비용량 확인이 필요하다. 좌표는 제주인데 `region=mainland`여서 region만 필터하면 빠진다.
- Hangyoung: 106,608행, 2013.01.01 01:00–2025.03.01 00:00, 음수 0. `research.plants`는 풍력 시간 라벨을 ±1시간 불확실·미검증으로 표시한다. 해당 ID를 공식 발전소명/부지/운영자와 연결하기 전에는 특정 제주 단지의 확정된 실측이라 부르지 않는다.
- ASOS 4지점 온도 NULL은 고산 9, 성산 28, 제주 3, 서귀포 4행. 일사 NULL은 각각 23,039 / 51,071 / 23,088 / 38,963행. 특히 성산은 일사 한 행만 비NULL이다. 야간 공란·미관측 장비·진짜 결측을 구분하고 다른 지점 일사를 쓸 때 공간근사라고 표시한다.
- ASOS min/max만 보면 2019–2026 전체 자료처럼 보이지만 연도별 집계에는 2020이 없다. 현재 수요/발전과 맞추는 공통 창은 먼저 2024년 및 2021년 이후 구간으로 잡는다.
- 제주 `sido` 51 선로와 bbox 교차 54 선로의 차이는 HVDC 3개 geometry가 제주/전남 행에 중복 저장됐기 때문이다. `(3631,4156)`, `(3632,4157)`, `(3633,4158)`은 각각 `ST_Equals=true`. 물리적 병렬회선과 데이터 중복을 구분해 모델링한다.

원별 거래량의 원문은 시장참여 발전기만 포함하고 PPA/자가용을 제외하며 송전단 기준이라고 밝힌다. 이를 5분 총 재생 출력이나 제주 전체 수요와 그대로 빼서 손실/HVDC를 추정하면 경계 차이가 잔차에 섞인다. 원문 페이지는 현재 `20251231`, 43,800행, 2026.06.16 등록으로 표시되지만 설명은 2024년으로 남아 있다. **2025 파일 업데이트 후보는 있으나 실제 다운로드/내용 검증은 아직 하지 않았다.** [KPX 원별 거래량 공식 페이지](https://www.data.go.kr/data/15100214/fileData.do).

## 5. 기존 계통 모델에서 가져올 것과 다시 검증할 것

기존 구현은 [compute_jeju_grid.py](/mnt/nvme/Energy-hub/etl/compute_jeju_grid.py), [상태 API](/mnt/nvme/Energy-hub/src/backend/app/api/v1/twin.py), [상태 DDL](/mnt/nvme/Energy-hub/etl/schema/jeju_grid_ddl.sql)에 있다. GIS 조회·snapshot 선택·결과 형식은 재사용할 가치가 있다. 계산 가정은 Rust로 그대로 옮기기 전에 검증한다.

| 실제 코드 동작 | 결과에 미치는 영향 | Rust 작업 전 최소 확인 |
|---|---|---|
| 가장 가까운 변전소에 선로 endpoint 매칭; bus 이름별 중복 제거 | 거리 임계값·전압·busbar 검증 없음. 이름 같은 전압별 설비가 병합될 수 있음 | 이름 대신 stable ID와 전압별 bus; EPSG:5179 거리·허용오차·단선도 확인 |
| 같은 bus pair는 가장 짧은 선분 하나만 남김 | 긴 선로의 조각, 복수 회선, 다른 경로를 잃을 수 있음 | 원본 Line ID/OSM ID와 회선 식별을 보존한 연결 검증 |
| 고립 bus를 nearest bus에 가상 선으로 연결 | 그래프를 연결해도 실제 전기 연결을 확인한 것은 아님 | 알려지지 않은 연결은 후보 시나리오로 유지; 실제 outage/island 처리 |
| `X_PER_KM=0.4`, 선로용량 전부 300 MVA, 70% 초과를 congested로 표시 | 임피던스·정격과 혼잡 정의가 실제 설비 자료에 근거하지 않음 | 추정 범위별 민감도; 실제 열한계/운전한계 확보. 70%는 내부 경고 기준 |
| `x_pu=(0.4×km)/(154²/100)`를 `Line.x`에 전달 | 설치된 PyPSA와 공식 문서의 `Line.x` 입력은 Ω다. pu를 Ω 필드에 전달하는 단위 불일치 | 입력 Ω와 내부 pu를 명확히 구분하고 한 번만 변환. 현재 설정에서는 의도한 Ω보다 약 237.16배 작은 값을 전달 |
| 비재생 `max(demand-renew,0)`의 절반을 화력, 나머지는 HVDC slack | 화력/실제 HVDC를 식별하지 못하며 link별 한계·역송 제약도 없음 | 도내 실제 발전 및 링크별 P/availability 확보; 부재 시 명시적 분담 시나리오 |
| EV충전기 개수로 제주 전체 부하 배분 | 가정·산업·관광 수요 공간 분포를 계측하지 못함 | 행정구역 판매량/한전 공급구역/변전소 부하로 가중치 비교 |
| `estimated`는 가상 연결선만 표시; 계산 후 `converged=True` 저장 | 나머지 선로도 X/용량/주입이 추정. bool만으로 수치·운전 적합성을 검증하지 못함 | geometry 출처, topology 신뢰도, 파라미터 출처, 주입 출처를 구분하고 잔차·finite·island 검사 |

모든 X가 같은 배율로 바뀌고 주입이 고정된 DC 망에서는 상대 선로 MW가 같게 나올 수 있다. 따라서 단위 오류가 기존 화면에서 반드시 큰 MW 차이를 만든다고 단정하지 않는다. 각도·AC 확장·다른 파라미터 혼합에는 영향을 주며 정합성 검사가 필요하다. `Line.x`는 입력 Ω, `x_pu`는 종속 출력이라는 정의를 설치본 CSV와 [PyPSA 공식 Line 문서](https://docs.pypsa.org/latest/user-guide/components/lines/) 양쪽에서 확인했다.

상태 API의 고정 HVDC 목록은 2개이고 첫 연계선을 해남↔서제주라고 표기한다. 확인한 GIS는 180 kV 해남–제주, 250 kV 진도–서제주, 150 kV 완도–동제주 경로를 이미 포함한다. **데이터에는 #3이 있는데 기존 코드의 링크 목록·단일 slack 모델이 이를 분리하지 않는다.** 이름 없는 180 kV 변환소 및 150 kV terminal은 공식 설비와 별도 매핑해야 한다. SMP 가격차로 추정하는 방향도 실제 계측 대체물이 아니다.

## 6. 추가 데이터의 우선순위

| 순위 | 추가/검증할 항목 | 현재 자료가 채우지 못하는 이유 | 확보 경로 / 성공 기준 |
|---|---|---|---|
| P0 | 공식 plant/substation/bus/line ID 매핑, 현재 단선도, 전압별 bus, 실제 회선·병렬·switch 상태 | GIS 근접성·이름은 전기 연결 증거가 아님 | 기존 OSM+한전 접속정보와 2023 논문 부록 연결 대조 → 최신 운영 단선도/기관 자료. 승인된 연결표와 미확인 연결 목록 |
| P0 | 선로 R/X/B·정격전류/MVA, 변압기 정격·임피던스·tap·연결, 발전기 P/Q 한계 | 기존 값은 대표 가정; 변압기 master 없음 | KEPCO/KPX 연구용 제공신청·기술자료, 실험망은 MATPOWER. 출처·단위·운전시점 명시 |
| P0 | 버스/변전소별 시간 P/Q 부하, plant→bus, 개별 실제·가용 발전 | 전체 합계만으로 공간 상태가 유일하게 정해지지 않음 | 우선 KEPCO 월별 지역/업종 판매량을 배분 프록시로, 검증은 feeder/substation 계측 |
| P0 | HVDC #1/#2/#3 링크별 P/Q, 방향·가용한계·pole/outage·램프·손실·방향전환 규칙 | 순수지·SMP·정격만으로 실제 운전을 알 수 없음 | KPX/KEPCO 공식 운영실적과 실측 제공. 정격과 운영 가능범위를 분리 |
| P1 | 제주 시간별 풍속/풍향·기압·QC, 허브높이·출력곡선, 당시 발행 NWP | 현재 제주 ASOS table은 T/RH/일사만 있음; 관측은 하루전 예보가 아님 | [KMA ASOS](https://data.kma.go.kr/data/grnd/selectAsosRltmList.do?pgmNo=36), AWS/예보 archive. forecast issue_time/valid_time 모두 확보 |
| P1 | 출력제어 지령/실행량·가용출력, PPA/BTM 시간 계량, 최신 원별 거래량 | 실제 출력은 제어 후 값. 거래량은 비시장 발전 제외 | [KPX 제어 자료](https://www.data.go.kr/data/15132422/fileData.do), [KEPCO 제주 PPA](https://www.data.go.kr/data/15100272/fileData.do), [원별 거래량](https://www.data.go.kr/data/15100214/fileData.do). 제도별 라벨 정의 확인 |
| P1 | ESS bus, MW/MWh·효율·초기 SOC·운영 범위 | OSM battery 위치 한 개는 저장장치 모델 파라미터가 아님 | 실설비 제원/계량. 없으면 용량을 명시한 가상 ESS 시나리오 |
| P2 | DEM/terrain·imagery·3D asset와 배포/사용 조건 | 감사한 두 마운트에서 terrain 원본은 확인하지 않음 | Cesium/공식 지형자료. 이 항목이 PF 입력 검증보다 먼저 개발을 막을 필요는 없음 |

풍력/태양광 공식 master는 [제주 풍력 현황](https://www.data.go.kr/data/15047557/fileData.do), [제주 태양광 현황](https://www.data.go.kr/data/3082724/fileData.do)으로 기존 GIS/인허가와 대조한다. 주소 geocode를 측량 좌표처럼 취급하지 않는다. 공식 송전/변전 공개 통계는 주로 집계표여서 from/to bus·R/X를 제공하는 전체 계통 case와 다르다. 상세 경로·접근 제한은 [제주 데이터 원문 조사](exa-results/jeju-grid-audit-2026-09-29/jeju_sources.md)에 있다.

두 가지 시점 변화는 모델 학습·운영 시나리오에서 분리해야 한다. KPX는 2024.06.01 이후 입찰 참여 발전기의 출력감소가 출력제어 통계에 포함되지 않음을 명시하며, HVDC3은 2024.11.29 상업운전을 시작했다. 과거 출력제어 패턴을 현재에도 같은 라벨로 적용하면 오류가 생길 수 있다. [KPX 2024 운영실적](https://www.kpx.or.kr/boardDownload.es?bid=0159&list_no=74566&seq=1), [한전 HVDC3](https://home.kepco.co.kr/kepco/front/html/WZ/2025_01/site.html).

기상청 LDAPS 페이지는 2026.03 UM 자료 제공 중단을 안내한다. 과거 논문의 LDAPS 수집법을 그대로 현재에 적용하기보다 대체 모델·발행시각 archive를 확인한다. [KMA 안내](https://data.kma.go.kr/data/rmt/rmtList.do?code=340&pgmNo=65).

## 7. 수학적으로 모델링할 부분

아래 단계와 연구 주제는 확보 자료와 문헌을 바탕으로 한 프로젝트 제안이다. 상세 식·가정·필수 관측·검증은 [수학 문헌 보고서](exa-results/jeju-grid-audit-2026-09-29/math_literature.md)에 있다.

### 7.1 지역 수급과 시간구간 정합성 — 지금 시작

\[
\bar P_t=E_t/\Delta t_t,\qquad
G_t+H_t^{in}+P_t^{dis}=D_t+H_t^{out}+P_t^{ch}+P_t^{loss}.
\]

단위는 MW, MWh, h로 고정한다. `gen_mwh`의 1시간 거래량은 같은 구간 평균 MW와 숫자가 같을 수 있지만 의미와 시장 경계가 같다는 뜻은 아니다. 5분 `supply_mw`는 공급능력이므로 G에 넣지 않는다. HVDC 계측·비시장 발전이 없을 때 남는 잔차를 손실이나 역송 실측이라고 이름 붙이지 않는다. 원천 KST와 hour-ending/start 변환을 보존한 뒤 공통 시간창으로 비교한다.

### 7.2 합계와 용량을 지키는 공간 배분 — 현재 자료로 추정 시나리오

\[
\min_d\sum_i(d_i-\tilde d_i)^2/\sigma_i^2
\quad\text{s.t.}\quad \sum_i d_i=D,\quad 0\le d_i\le\bar d_i.
\]

\(\tilde d\)는 지역 판매량·한전 공급구역 등을 이용한 기준값, \(\sigma\)는 배분 불확실성이다. 추가 지역 집계가 있으면 선형 제약을 붙인다. 발전도 원별 합계와 각 설비 상한을 보존한다. 변전소 MVA를 부하 MW로 그대로 쓰지 않는다. 합계 한 개로 N개 bus 부하를 정하면 보통 N−1 자유도가 남으므로, 최적화 해가 유일해도 실제 부하가 복원됐다고 주장할 수 없다.

### 7.3 topology 검증 + DC PF — X와 연결을 확보한 뒤

\[
f_\ell=S_{base}(\theta_i-\theta_j)/x_\ell^{pu},\quad Af=p,\quad\theta_{ref}=0.
\]

이는 tap=1, phase shift=0의 AC 송전망 선형 근사다. 무효전력·전압크기·손실을 계산하지 않으며 물리적인 HVDC 회로 해석과 다르다. HVDC는 별도 제어가능 주입/거래와 링크별 한계로 둔다. EPSG:5179 거리와 전압을 이용한 endpoint 후보 생성 → 공식 연결 대조 → 전압별 bus/회선 구성 → island별 수지/기준각 확인이 필요하다. [MATPOWER 원논문](https://matpower.org/docs/MATPOWER-paper.pdf).

### 7.4 다기간 DC OPF + HVDC + ESS + 출력제어 — 운영 데이터 확보 후

\[
\min\sum_t\Delta t\left(\sum_g c_g p_{g,t}+c_{curt}C_t+c_{shed}U_t\right),
\qquad |f_{\ell,t}|\le\bar f_\ell,
\]
\[
e_{t+1}=e_t+\eta_{ch}p_t^{ch}\Delta t-p_t^{dis}\Delta t/\eta_{dis}.
\]

노드별 수지, 발전 최소/최대·램프, 재생 가용출력−제어량, 링크별 방향/손실·가용범위, SOC/MW/MWh 한계를 함께 둔다. 선형 비용은 LP, 기동정지·최소운전은 MILP, 양의 준정부호 이차 비용은 convex QP로 시작할 수 있다. 충방전 동시 허용 여부와 말기 SOC를 명시한다. 측정 출력은 제어 후 결과이므로 가용출력으로 쓰고 C를 다시 차감하지 않는다. [PyPSA 저장장치 식](https://docs.pypsa.org/stable/user-guide/optimization/storage/).

#1/#2 LCC에도 역송 사례가 있지만 #3 VSC와 방향전환 특성이 다르다. 모두 동일한 자유 양방향 ±정격 링크로 두지 않는다. 과거 논문의 최소정송/전환시간은 당시 가정으로만 사용하고 현재 규칙을 확보한다.

### 7.5 AC PF·N−1·상태 추정 — 더 높은 입력 조건

\[
S_i=V_i\overline{\sum_jY_{ij}V_j},\qquad
\hat x=\arg\min_x(z-h(x))^TR^{-1}(z-h(x)).
\]

AC PF에는 R/X/B, P/Q, transformer/tap, 발전기 전압/Q 한계가 필요하다. N−1은 단순 선로 제거 외에도 islanding, HVDC pole/outage, 예비력·재급전 조건을 확인한다. WLS 상태 추정에는 실제 센서 위치·오차와 충분한 관측이 있어야 하며 제주 합계 수급만으로 bus 전압/topology를 추정할 수 없다. 배전 불평형/DistFlow는 feeder·상별 데이터가 생길 때 추가한다. [관측가능성/상태 추정 원연구](https://arxiv.org/abs/1904.08036).

### 7.6 연구 주제로 유망한 세 가지

| 제안 주제 | 실제 확보 데이터와 연결 | 비교/평가 | 추가 조건 |
|---|---|---|---|
| 공개 GIS + 한전 공급구역의 topology/공간배분 불확실성이 혼잡 판단에 미치는 영향 | 선로 GIS, 15개 subst 코드, 99,860 주소 접속정보, 전체 수급 | 거리 임계값·bus 매핑·부하 가중치·X 범위를 바꿔 선로 flow/혼잡 판정의 안정성 평가 | 실제 단선도 일부와 독립 계측이 있어야 정확도 검증. 없이도 가정 민감도 연구는 가능 |
| HVDC3 전후 및 출력제어 라벨 변화에 따른 예측·운영 시나리오 | 2021–2026 수급, 2024 원별 거래량, ASOS/SMP | 2024.06 시장변화와 2024.11 HVDC3을 분리한 시간순 검증; LP/UC 운전제약 비교 | 당시 발행 예보, 실제 제어/가용출력 및 최신 거래량. 단순 전후 평균으로 HVDC3의 인과효과 단정 금지 |
| 예측 오차를 반영한 HVDC/ESS 재급전과 조건부 혼잡확률 | 전체 수요·재생 예측과 검증된 network | point forecast 대비 복수 오차 시나리오의 infeasibility·제어 MWh·비용·위험률 비교 | network/X/한계 확보. 우선 deterministic dispatch 통과 후 추가 |

이들은 문헌에 유사 접근이 있어 신규성은 후속 비교가 필요하다. 단순히 Rust로 구현했다는 사실만으로 수학적 신규성은 생기지 않는다. 장시간/5분 snapshot을 연속 계산하는 준정적 트윈과 주파수/보호/인버터 EMT 트윈은 요구 데이터가 다르다. 현재 우선 범위는 준정적 시계열이다.

## 8. 문헌에서 직접 가져올 근거

| 문헌 | 이 프로젝트에 주는 근거 | 그대로 가져오면 안 되는 것 |
|---|---|---|
| Son & Jang, 2023, [MIDC 운전 최적화](https://www.mdpi.com/1996-1073/16/15/5699) | 제주 HVDC/PTDF/LP 후 AC 검증; 부록의 39회선 연결·MVA와 발전기 bus/Pmin/Pmax는 GIS 교차검증에 유용 | R/X/B와 전체 case는 비공개. 39회선을 2026 실망 전체로 간주 불가 |
| 최지웅 외, 2023, [HVDC 전략·출력제어·운영비용](http://www.tkiee.org/kiee/XmlViewer/f423006) | DC OPF+UC, 최소출력/램프/기동정지·방향전환 조건 | 미래 설비 시나리오와 당시 HVDC 운전가정을 현재 실측처럼 사용 불가 |
| 이정범 외, 2025, [하루전 제주 출력제어 예측](http://www.tkiee.org/kiee/XmlViewer/f435229) | NWP→수요/재생→출력제어 예측, 시간순 평가 구성 | 원문의 2024 상반기 평가를 HVDC3 이후 성능으로 일반화 불가 |
| 김현진 외, 2025, [제주 유연연계 출력제어 실증](http://www.tkiee.org/kiee/XmlViewer/f447225) | 실제 D-1 예측/배전 수용한계/SCADA 지령 연결 | 익명 bus/변전소와 2일 실증을 전체 제주 계통 검증으로 간주 불가 |
| Lee & Hur, 2023, [확률적 혼잡 연구 기관 원문](https://dspace.ewha.ac.kr/handle/2015.oak/267716) | 수요·풍력 불확실성에서 혼잡확률로 연결하는 연구 방향 | 기관 초록까지 검토. IEEE PDF 본문 추출 실패로 세부식·정량 재현 미검증 |
| Zimmerman 외, [MATPOWER 원논문](https://matpower.org/docs/MATPOWER-paper.pdf) | AC/DC/OPF formulation, 단위·slack·기준 사례 | solver 수렴이 실제 제주 모델의 정확성을 보증하지 않음 |
| Park/Deka/Chertkov, [제한 관측 topology/파라미터 추정](https://arxiv.org/abs/1710.10727) | 식별가능성과 관측 조건을 먼저 따져야 함 | radial/통계 가정을 가진 결과를 mesh 송전망·합계 수급에 직접 적용 불가 |
| Giraldez 외, [Hawaii QSTS·inverter·제어량](https://docs.nlr.gov/docs/fy17osti/68681.pdf) | feeder별 시계열·전압 검증의 실무 방법 | Hawaii 수치를 제주로 전용 불가 |
| Kenyon 외, [Maui PSCAD 검증](https://docs.nlr.gov/docs/fy21osti/76808.pdf) | field event를 사용하는 EMT 검증과 정상상태 모델의 구분 | 고속 측정·제어모델 없이 EMT/주파수 안정성을 주장 불가 |

## 9. Rust 구현 방향

**첫 구조는 기존 PostgreSQL/PostGIS + Rust의 조회/검증/계산/API + Unreal/Cesium의 표시로 충분하다.** 운영 중인 GIS·수집기·DB를 새로 만들 필요가 없다. 기존 Python 계산은 가정과 단위를 확인한 뒤 비교 기준/데이터 추출 참고용으로 쓰며 최종 실행부를 Rust로 가져갈 수 있다.

| 역할 | 최소 후보 | 선정 조건 |
|---|---|---|
| DB/GeoJSON | `sqlx` + 기존 PostGIS | 공간변환/거리/`ST_AsGeoJSON`을 DB에서 수행. 불필요한 GIS binding을 먼저 추가하지 않기 |
| API/snapshot | `axum`, `tokio`, `serde` | 처음은 HTTP snapshot 재생. CPU solver는 제한된 blocking 작업으로 분리. async가 실시간 마감시간을 보장하지 않음 |
| DC/AC 희소 계산 | 기존 Rust PF 후보 우선; 부족하면 `faer` | 동일 MATPOWER 입력에 잔차/결과/오류 처리 검증. 일반 AC Jacobian에는 비대칭 LU 등 맞는 분해 선택 |
| LP/MILP | `good_lp` + HiGHS | good_lp는 선형 모델러. UC가 필요할 때 정수 변수. native HiGHS build 조건 확인 |
| convex QP | `highs` 직접 Hessian API 또는 `Clarabel.rs` | good_lp 경유 QP 미지원. Clarabel은 일반 비선형 AC-OPF/정수 UC 대체가 아님 |
| 비선형 AC-OPF | 추후 native Ipopt 등 | 실제 AC-OPF 요구가 생기면 미분·scaling·국소해·binding 검증 후 도입 |
| Unreal/Cesium 연동 | Rust HTTP/JSON 결과 → 기존 C++/Blueprint/플러그인 | 엔진/플러그인 전체를 Rust로 재작성할 필요 없음. renderer와 계산 주기 분리 |

Rust PF 후보로 `rustpower`, `powers`, `gridoxide`, `OxiGrid`, `GAT`가 검색된다. 존재를 확인한 후보와 채택 검증을 마친 엔진은 다르다. `gridoxide`의 기본 빌드에는 LGPL component가 있고, 각 후보의 Q-limit/switch/transformer/OPF 범위와 release·license는 선택 feature까지 확인해야 한다. 세부 공식 링크와 한계는 [Rust 조사](exa-results/jeju-grid-audit-2026-09-29/rust_feasibility.md)에 있다. [good_lp 지원 범위](https://github.com/rust-or/good_lp), [highs Hessian API](https://docs.rs/highs/latest/highs/struct.Model.html), [Clarabel](https://github.com/oxfordcontrol/Clarabel.rs).

### 먼저 통과시킬 검증

1. 데이터: 원천 시간대/구간·단위·지역 경계·ID와 품질 flags를 고정. 실측, 배분 추정, 시뮬레이션, 시나리오 가정을 결과에서 구분.
2. 수치: 같은 MATPOWER case14/30/118 파일·baseMVA·tap·status·Q-limit 옵션으로 DC/AC PF 비교. 수지 잔차, finite, island, 실패상태를 검사. 특정 제주 가정 결과와의 일치만으로 물리 정확도를 검증하지 않기.
3. 제주: 선로·bus 매핑을 검증하고 추정 파라미터 범위를 바꿔 결과 안정성을 기록. 실제 V/P/Q/flow가 확보되면 별도 시간창에서 오차 확인.
4. 최적화: 목적값뿐 아니라 nodal balance/선로/발전/ESS/HVDC 제약 위반을 확인하고 DC 최적해를 AC로 사후검증.

언어 변경으로 데이터 정합성 문제가 사라지지는 않는다. 반대로 검증된 solver를 Rust에서 호출하는 구조는 사용자 의도인 Rust 실행부를 유지하면서 수치 알고리즘 재구현을 줄일 수 있다. 이번 작업은 조사이며 Rust 코드 작성·패키지 설치·컴파일/벤치마크는 수행하지 않았다.

## 10. 추천 착수 순서와 산출물

1. **기존 자산 연결:** OSM/인허가/공식 발전소 master·한전 접속정보의 ID·단위·지역·시점 매핑표. HVDC geometry 중복과 이름/전압별 bus 구분.
2. **지역 시계열 재생:** 깨끗한 2024 공통 창과 최신 5분 수급을 Rust 조회/API로 재생. 여기까지는 실제 선로 loading·전압 계산을 약속하지 않는다.
3. **검증용 Rust 수치 코어:** 기존 PF 후보의 MATPOWER 비교 → 필요한 최소 DC 모델. 실패/섬 분리/단위 오류를 결과 status로 처리.
4. **제주 연구망:** 검증한 연결 + 출처 있는 파라미터 또는 명시한 대표값 범위. 공간배분·HVDC별 가정 민감도. 실제 데이터가 채워진 항목부터 Level 4로 확장.
5. **최적화/3D:** 실제 질문이 있는 HVDC/ESS/출력제어 시나리오부터. 참조 이미지로 만든 편집 가능한 3D 자산을 실제 GIS 좌표에 배치하고 Rust 계산 상태를 연결한다. 영상 출력은 선택 사항이다. 이미지 기반 환경 제작은 아래 11절을 따른다.

관련 산출물:

- [CSV 파일별 스키마·기간·중복·결측·범위](exa-results/jeju-grid-audit-2026-09-29/csv_inventory.json)
- [DB 감사 쿼리·집계 증거](exa-results/jeju-grid-audit-2026-09-29/database_audit.txt)
- [제주 공식 자료·논문 5편·추가 확보 경로](exa-results/jeju-grid-audit-2026-09-29/jeju_sources.md)
- [수학 모델 상세식·필요 관측·문헌 9개](exa-results/jeju-grid-audit-2026-09-29/math_literature.md)
- [Rust 라이브러리·native solver·검증·라이선스 검토](exa-results/jeju-grid-audit-2026-09-29/rust_feasibility.md)

남은 접근 한계는 2026.08/09 두 CSV의 파일별 내용, 연결되지 않은 옛 PostgreSQL 디렉터리, 상세 운영 SCADA·전기 파라미터다. 공식 페이지/API 명세 발견은 원시 파일 다운로드 성공이나 기관 제공 확정을 뜻하지 않는다. 보유 데이터 수준·예상 구현 단계는 이번 감사에 따른 판단이며 실제 운영제어 모델 인증은 아니다.

## 11. 참조 이미지로 편집 가능한 3D 환경 제작

목표는 참조 이미지의 외형·분위기를 구현한 3D 공간에서 이동·확대·설비 선택·시간 변경을 수행하고 실제 GIS·수급·계통 모델을 연결하는 것이다. 영상 생성은 필수 단계가 아니다.

Higgsfield 공식 **3D Jutsu** 안내는 prompt/reference 기반 편집 가능한 geometry·배치·조명·카메라, 대화형 viewport, GLB import/export를 명시한다. **Blender 플러그인**도 Scene Builder의 편집 가능한 geometry 및 참조 이미지 기반 mesh 생성을 안내한다. 따라서 Higgsfield의 역할을 이미지·영상 연출에만 한정할 필요가 없다. [3D Jutsu 공식 안내](https://higgsfield.ai/blog/higgsfield-3d-jutsu), [Blender 플러그인 공식 안내](https://higgsfield.ai/plugins/blender).

최소 제작 경로는 `참조 이미지 → Higgsfield 3D Jutsu/Blender 자산·장면 제작 → GLB → Rust 환경에서 GIS 배치·상태 연동`이다. Rust 렌더링 후보인 Bevy는 glTF/GLB scene loading을 지원한다. 이는 문서 기반 연동 경로이며 실제 생성 GLB의 material/extension·크기·좌표·object 이름/ID 호환은 작은 샘플로 확인해야 한다. [Bevy 공식 glTF 문서](https://docs.rs/bevy/latest/bevy/gltf/index.html).

설비 외형·재질·장면 구성에는 참조 이미지를 사용하고, 실제 제주 지형·좌표·선로 연결은 GIS와 검증된 topology로 배치한다. 공개 3D Jutsu 설명의 용도는 scene blocking/previz이므로 단일 이미지에서 실제 전력망을 측량 정확도로 복원하는 기능을 확인한 것은 아니다. 발전소·bus·line ID를 장면 object와 연결해 Rust 상태에 따라 출력·색·표시를 갱신한다.

공식 문서로 기능을 확인했으며 사용자 계정의 기능 접근·실제 export·3D API/Bridge 자동화는 아직 실행 검증하지 않았다. 이미지가 준비되면 우선 변전소 또는 풍력설비 하나를 생성·내보내기·불러오기 하는 경로부터 확인한다. 후속 Exa 검색 2회 × 5후보(이미지/영상 API와 3D 환경)를 추가했으며, 앞의 179건은 최초 데이터·수학·Rust 문헌조사 집계로 유지한다.

## 12. 기획서 기준 구현 가능성 재확인: 이미지 기반 대화형 3D 환경

### 12.1 판정과 기획서 요구사항 대조

**구현 가능한 구성이다. 현재 자료로 착수할 수 있는 범위는 실제 GIS에 지역 집계 시계열을 연결하는 Level 3 MVP이며, 설비별 실측 상태를 모두 재현하는 Level 3와 실제 제주 계통을 검증한 Level 4는 추가 입력이 필요하다.** 이번 판정은 기획서 전체 요구사항, 앞의 마운트/DB 감사, 공식 3D 문서와 현재 개발 환경 점검에 근거한다. 완성 앱이나 Higgsfield→Rust 통합 실행을 검증했다는 뜻은 아니다.

사용자가 원하는 결과는 이미지의 외형을 참고하여 만든 공간 안에서 이동·확대·설비 선택·시간 변경이 가능한 환경이다. 기획서의 6·8·9·10절은 Python 계산, Unreal/Cesium 표시, Higgsfield 영상 제작을 전제로 작성되어 있어 Rust 실행부와 이미지 기반 3D 제작 흐름으로 조정해야 한다. 영상은 선택 출력이다.

| 기획서 요구 | 확인한 근거 | 판정 / 남은 조건 |
|---|---|---|
| 실제 위치에 발전설비·변전소·송전선 배치 | 제주 선로 GIS 51레코드, 변전소/변환소 13레코드, PV 인허가 1,791레코드, HVDC 3경로 | 구현 가능. GIS 객체와 실제 시설의 ID·전압·중복을 먼저 정리. GIS 선분의 수는 검증된 전기 회선 수가 아님 |
| 실제 제주 지형과 환경 | 감사한 두 마운트에서 terrain 원본 미확인 | DEM/terrain 및 선택적으로 위성·정사영상 추가. 평면 배경은 지형 요구사항을 충족한 결과로 간주하지 않음 |
| 참조 이미지와 유사한 편집 가능 3D 설비·공간 | 3D Jutsu의 reference 기반 장면/GLB export, Blender 플러그인의 image 기반 mesh 공식 안내 | 문서상 제작 경로 있음. 참조 이미지와 사용자 계정에서 생성·export할 수 있는지, 생성물 품질을 샘플로 확인 |
| 카메라 이동·설비 선택·시간 변경 | Rust Bevy의 3D/glTF 지원, 기존 DB의 위치·시간 데이터 | 구현 가능한 개발 항목. 모델만 가져오면 상호작용이 자동으로 생기는 것은 아니며 Rust 앱에서 연결 |
| 시간별 발전·부하 표시 | 2021–2026 제주 합계 5분 수급, 시간별 수요, 일부 개별 발전 계량 | 제주 전체 수요/풍력/태양광 재생 가능. 발전소·bus별 표시에는 실제 계량 또는 명시적인 배분 추정 필요 |
| HVDC 방향·출력 변화 | #1/#2/#3 공간 경로와 공개 정격 | 경로 표시와 가정 시나리오는 가능. 실제 시점별 링크 MW·방향·가용한계는 추가 확보 |
| 조류·선로 loading·혼잡·고장 시나리오 | 기존 추정 DC 모델, 공개 문헌의 연결/정격 일부 | 연구 시나리오는 가능. 실제 연결·X·정격·주입을 확인하고 수지 잔차를 검증. 기존 결과를 바로 실측 상태로 사용하지 않음 |
| AC 전압·무효전력·변압기 부하 | 기획서의 AC 식은 있으나 실제 입력 부족 | R/X/B, bus P/Q, 변압기/tap, 발전기 전압/Q 한계와 독립 계측 확보 후 검증 |
| 미래 위험 예측 | 수요·재생·기상 과거 자료 일부 | 연구 착수 가능. 풍속/풍향·당시 발행 예보·가용출력/제어량 보강, 시간순 검증 및 검증된 계통 모델 필요 |

### 12.2 Rust와 3D 제작의 현실적인 경로

Rust 3D MVP의 후보 구조는 다음과 같다. 기존 수집기와 PostgreSQL/PostGIS는 재사용한다.

```text
참조 이미지 → Higgsfield 3D Jutsu → GLB 설비/장면
                                      ↓
기존 PostGIS·시계열 → Rust 조회/계산 → Bevy 공간 배치·선택·시간 재생
DEM/terrain ────────────────────────→ 실제 제주 지형
```

Bevy의 GLB 지원은 공식 문서로 확인했지만 모든 생성 GLB의 호환성을 보장하지는 않는다. export 파일의 재질·필수 확장·node 분리·단위·축·pivot을 확인해야 한다. 예를 들어 조사 시점 공식 표는 Draco와 meshopt 압축 확장을 미지원으로 표시한다. 모델의 외형과 별개로 object에 발전소/변전소 ID를 연결하고, 시계열을 바꿀 때 그 ID의 표시값·색을 갱신한다. [Bevy glTF 공식 문서](https://docs.rs/bevy/latest/bevy/gltf/index.html).

전체 제주와 육지 HVDC까지 다루면 좌표 변환, 큰 좌표의 정밀도, 지형 타일/LOD·로딩도 구현 범위에 들어간다. Bevy GLB 로더 하나로 기획서의 Cesium 기능을 대체했다고 볼 수 없다. 첫 Bevy MVP는 제주 영역의 지형 mesh와 주요 설비에 범위를 제한하고, 세부 모델은 선택한 시설에서 사용한다. 같은 종류의 풍력/PV 모델을 재사용하면 모든 시설을 별도로 생성할 필요가 없다.

기획서의 광역 지형 스트리밍을 우선한다면 **Rust 계산/API + Unreal/Cesium 표시**도 유효하다. Cesium은 WGS84/ECEF↔Unreal 좌표 변환과 georeference를 제공하며, 이 경우 표시 계층에는 C++/Blueprint가 남는다. Rust로 전체 앱을 작성할 때는 Bevy 쪽 지리 기능과 성능을 별도로 검증해야 한다. [Cesium georeference 공식 API](https://cesium.com/learn/cesium-unreal/ref-doc/classACesiumGeoreference.html).

Higgsfield Blender 플러그인 공식 안내는 Windows/macOS를 명시한다. 안내 페이지는 Blender 4.2–5.1, help center는 5.1 이상으로 서로 달라 설치 전 대상 버전을 확인해야 한다. Linux 지원은 확인하지 못했으므로 현재 Linux 서버에서 플러그인을 사용할 수 있다고 전제하지 않는다. 브라우저 기반 3D Jutsu에서 생성·GLB export한 파일을 가져오는 경로부터 시험할 수 있다. 사용자 계정의 접근·credits·export 권한과 자동화 연결은 미검증이다. [Blender 플러그인 요구사항](https://higgsfield.ai/blog/higgsfield-blender-plugin), [help center](https://higgsfield.ai/creator-hub/help-center/integrations/external-integrations-higgsfield), [설치가 필요 없는 3D Jutsu](https://higgsfield.ai/blog/higgsfield-3d-jutsu).

### 12.3 실제로 더 필요한 것

1. **참조 이미지와 표현 정확도:** 원하는 전체 환경 이미지와 풍력·변전소 등 대표 시설 사진. 실제 시설 형상을 정확히 재현하려면 여러 방향 사진, 도면/치수 또는 검증된 기존 모델을 보강한다. 이미지 한 장의 보이지 않는 부분은 추정 형상이다.
2. **3D 자산을 얻는 경로:** Higgsfield 기능을 사용할 계정/credits와 실제 export 샘플. 첫 자산은 풍력 또는 변전소 하나로 충분하다. 특정 생성 서비스가 실패해도 표준 GLB나 기본 형상으로 데이터 연동 개발을 진행할 수 있다.
3. **지형과 영상 자료:** 제주 DEM/terrain, 사용할 배경 영상의 제공·사용 조건과 해상도. 후보 Copernicus GLO-30은 전 지구 약 30m DSM으로 건물/식생까지 포함하므로 시설 상세 측량 자료를 대신하지 않는다. 2026.08.25 공지는 **30m View Service**에 CCM 등록/접근 조건을 명시하고 기본 서비스를 90m로 변경했다. 자료 다운로드 권한과 View Service 권한을 혼동하지 않고 실제 제주 타일의 접근·고도 기준을 확인한다. [Copernicus DEM 설명](https://dataspace.copernicus.eu/explore-data/data-collections/copernicus-contributing-missions/collections-description/COP-DEM), [현재 View Service 접근 조건](https://dataspace.copernicus.eu/news/2026-8-25-copernicus-dem-30m-view-service-update).
4. **자산–GIS–시계열 대응표:** 설비 ID, 좌표/전압, 3D object, 측정값 또는 추정값의 출처를 연결. 현재 개별 발전 계량은 일부뿐이고 bus별 부하는 부족하므로 합계 표시와 배분 표시를 구분한다.
5. **3D 실행 환경:** GPU/그래픽 드라이버를 사용할 수 있는 PC 또는 브라우저 클라이언트. 서버에서 Rust API/계산을 실행하고 다른 PC에서 표시하는 구성이 가능하다. 현재 서버 자체의 GPU 렌더링은 확인되지 않았다.
6. **실제 계통 해석용 추가 입력:** 검증된 bus/회선/변압기 연결, R/X/B·정격·tap, bus P/Q 및 발전 제어한계, HVDC별 운전·계측. 예측까지 확대할 때는 풍속/풍향과 예보·출력제어 원천을 추가한다. 상세 확보 우선순위는 6절을 따른다.

### 12.4 현재 작업 환경에서 직접 확인한 결과

2026-09-29에 `rustc --version`, `cargo --version`, `free -h`, `lscpu`, `nvidia-smi` 및 그래픽 장치/라이브러리 경로와 프로젝트 파일 목록을 읽었다.

| 항목 | 관측 결과 | 의미 |
|---|---|---|
| Rust 도구 | rustc/cargo 1.96.0 | 도구는 설치됨. 선택할 Bevy/solver 버전의 MSRV와 빌드는 별도 확인 |
| CPU/메모리 | x86_64, Intel Xeon E5-2697A v4, 논리 CPU 64개, RAM 약 62GiB | 개발·계산용 기반 존재. 렌더 FPS나 solver 처리시간을 벤치마크한 것은 아님 |
| 로컬 GUI | DISPLAY/ WAYLAND_DISPLAY 없음, 세션 tty | 현재 세션에서 대화형 창 실행 확인 안 됨. headless 서버와 표시 PC 분리 가능 |
| GPU 접근 | NVIDIA PCI 장치 있으나 이 환경의 driver symlink 없음. `nvidia-smi`가 드라이버 통신 실패. `/dev/dri`·`/proc/driver/nvidia` 없음 | 현재 환경에서 GPU를 사용할 수 있다고 가정하지 않음. 호스트 드라이버/장치 노출 점검 또는 별도 표시 PC 필요 |
| 3D 제작/빌드 도구 | Blender, CMake, clang 미설치 | 브라우저 자산 제작은 별도 경로. CMake는 HiGHS 등 native solver 선택 시 추가 조건이며 clang이 모든 Rust 빌드에 필수인 것은 아님 |
| 현재 프로젝트 | Cargo.toml·Rust 소스·참조 이미지·GLB/glTF 자산 없음. 점검한 Cargo source cache에 Bevy/API/solver 후보도 없음 | 구현체는 아직 없음. 의존성 설치·컴파일·GLB 표시 통합시험을 수행하지 않았음 |

### 12.5 첫 통합 검증의 통과 조건

**참조 이미지 1개 → 실제 생성 GLB 1개 → Rust 표시 → GIS 좌표 배치 → 설비 선택 → 시간별 데이터 갱신**을 먼저 확인한다. 파일이 읽히는지뿐 아니라 형상/재질, 크기·축, 시설 ID 선택, 두 시점 이상의 데이터 변화와 관측/추정 구분이 맞는지 확인한다. 여기까지 통과하면 제주 지형과 여러 시설로 확대하고, 계통 모델은 MATPOWER 기준 사례를 통과한 뒤 연결한다.

현재는 참조 이미지와 실제 export 파일이 없고 계정에 접속하지 않았으므로 이 통합시험은 미수행이다. 문서·보유 데이터·개발 환경의 점검으로 **구현 경로와 부족 조건을 확인한 단계**다. 원 기획서와 서비스·원천 데이터는 수정하지 않았다.

## 13. 후속 확인: 실제 지역 사진과 기존 외부 DEM

12절은 지역 사진 수집 전의 환경 점검 결과다. 이후 사용자 설명에 따라 **제주 현장 실사 사진을 수집해 주변 환경과 시설을 제작하는 방향**으로 설계를 보완했다. GIS/DEM은 위치·바닥 높이를 맞추는 보조 자료다.

- [프로젝트 루트의 현재 기획서](jeju_power_grid_digital_twin_design.md) 14절에 현장 사진 수집·역할·3D 제작/복원·완료 기준을 기록했다. v0.1에는 새 기획서 링크를 추가했고 원 본문은 보존했다.
- 공식 Visit Jeju 페이지의 신창 해안 실사 2장을 저장·decode·육안 확인했다. [사진 파일·출처 목록](reference_photos/jeju/photo_catalog.json)에 크기·SHA256·지역·공식 URL·카메라/촬영일 미확인·참고용 상태를 기록했다. 현재 두 장은 정밀 SfM 촬영 세트가 아니다. 사진별 제작 이용 조건 확인과 다중 시점 원본 보강이 필요하다.
- 두 iSCSI 마운트 밖에서 `/mnt/nvme/Energy-hub/research/data/raw/dem_korea.tif`를 발견·실제로 읽었다. 파일은 218,364,148 bytes, EPSG:4326, 28,808×21,606, bounds `[124,33,132,39]`, int16, NoData −32768다.
- 제주 bbox `[126.1,33.2,126.9,33.6]`의 512×1024 샘플은 고도 −117~1,931m, 양수 고도 300,471개였다. 제주 고도 포함을 확인했으며 해안·수직 기준·측량 정확도 검증을 뜻하지 않는다.
- `/mnt/nvme/Energy-hub/src/terrain-tiles`에서 PNG 16,713개와 제주 z10 타일 3개 존재를 확인했다. 이는 현장 사진이나 RGB 배경 영상이 아니며 높이 인코딩 자료다. 처음 두 마운트에서 terrain 원본을 찾지 못했다는 감사 결과와 조사 범위가 다르다.

Higgsfield 생성/export, 다중 시점 복원, Bevy 앱 실행은 아직 미수행이다. 이 후속 작업에서 운영 서비스·원천 데이터·GPU 드라이버를 변경하지 않았다.

## 14. 후속 확인: 국토부 API로 기존 지역 공간 활용

사용자의 추가 제안에 따라 VWorld의 공공 3D 공간을 먼저 확인하고, 부족한 지역 지물·시설만 현장 사진으로 보완하는 대안을 [현재 기획서](jeju_power_grid_digital_twin_design.md) 3절·14.8절에 반영했다. 사진 전체를 모아 재제작하는 것을 필수 선행 단계로 두지 않는다.

- [VWorld 공식 안내](https://www.vworld.kr/dev/v4dv_opnws3dmap3guide_s001.do)는 WebGL 3D API 3.0이 JavaScript SDK이며 KTX 2.0 기반 3D Tiles를 적용한다고 설명한다. 프로젝트 인증키·도메인 조건을 확인해야 한다.
- [운영기관의 서비스 안내](http://www.spacen.or.kr/vworld_mgm/business_info.do)는 3D 건물·시설물을 안내한다. [공식 GLB 예제](https://github.com/V-world/V-world_API_sample/blob/master/%5BWebGL%5D%20glb%20%EC%9B%80%EC%A7%81%EC%9D%B4%EA%B8%B0.html)는 자체 모델을 지도에 추가한다. 지도 건물의 GLB export를 검증한 것은 아니다.
- Rust API·계통 계산과 VWorld 웹 표시를 연결하는 경로가 있다. 표시부까지 Rust인 Bevy 경로에서는 자료 저장/변환 권한과 모델·타일 형식을 별도 확인한다. VWorld 자체 현장 사진 API는 이번 조사에서 확인하지 못했다. 실제 도로 파노라마 조회·표시는 별도 [Kakao 공식 로드뷰 API](https://apis.map.kakao.com/web/sample/basicRoadview/)의 기능이다.
- 기존 Energy-hub에는 VWorld 주소 지오코딩 스크립트가 있다. 새 프로젝트의 3D 인증·SDK 실행·제주 관심 구역 상세 품질은 미검증이다. 원본 3D 자료의 다운로드·변환도 실행하지 않았다.

외부 자료 상세 페이지 일부는 조회 오류가 있어 원본 제공 형식·권한의 근거로 채택하지 않았다. 오래된 3D 데이터 다운로드 예제나 전국 서비스 안내만으로 현재 API 가용성·제주 전역 실사 품질을 확정하지 않는다.

## 15. 최신 요구 반영: 이미지 기반 Higgsfield 프런트와 Rust 실행 구성

사용자 수정에 따라 기본안은 **실제 제주 사진을 반영한 Higgsfield 3D 환경·프런트 + Docker의 Rust 백엔드 + Redis 캐시 + WebSocket**이다. VWorld는 위치·높이·배치의 보조 자료로 활용한다. 앞 절의 VWorld 주 화면 구성은 비교 조사였으며 현재 기본안을 대체하지 않는다. [현재 기획서](jeju_power_grid_digital_twin_design.md) 7절·14절·16절에 제작/실행 역할과 완료 기준을 반영했다.

프로젝트 `.env`에서 비어 있지 않은 `higs_key`, `vworld_key`를 확인했고 전자는 공식 `ID:Secret` 형태와 일치한다. DB/Redis 연결 설정은 아직 없다. 키 값은 출력·변경하지 않았다. 실제 API 인증·Higgsfield 계정/제작 기능 접근은 미검증이다.

Higgsfield는 [Jutsu의 editable scene·GLB](https://higgsfield.ai/blog/higgsfield-3d-jutsu), [Supercomputer의 대화형 웹 제작](https://higgsfield.ai/creator-hub/help-center/tools/how-do-i-use-supercomputer), [Apps의 React 코드·Git 접근](https://higgsfield.ai/creator-hub/help-center/tools/what-are-higgsfield-apps), [Games의 대화형 3D](https://higgsfield.ai/blog/higgsfield-mcp-gpt6-astra-games-2)를 안내한다. 조회 중심 디지털 트윈에 맞는 제작 흐름과 실제 코드/자산의 Rust 연결을 확인해야 한다. 미디어 REST 키만으로 모든 제작 도구가 연결됐다고 판단하지 않는다.

기존 Redis가 256MiB·`allkeys-lru`로 실행 중임을 읽기 전용 확인했다. Rust 프로젝트/Compose 파일은 아직 없으며 현재 서버의 NVIDIA 드라이버 통신은 실패한다. API·캐시·WS와 첫 계통 계산은 CPU로 시작하고 3D 렌더링은 접속 PC에서 수행하는 구성이다. 로컬 dense MVS·딥러닝 학습·서버 렌더링은 GPU 요구를 따로 검증한다.

이 검토에서 확인한 것은 구성의 구현 경로와 남은 입력/통합시험 조건이다. Higgsfield 생성·Docker 빌드·Rust DB/Redis 연결·WebSocket 갱신·실제 렌더링의 성공을 확인한 단계는 아니다.
