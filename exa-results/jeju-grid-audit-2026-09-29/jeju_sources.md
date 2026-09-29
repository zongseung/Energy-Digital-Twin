# 제주 전력망 원문 검증 및 추가 데이터 확보 조사

- 조사 기준일: 2026-09-29 (Asia/Seoul).
- 범위: 기획서 전체 검토, 제주 HVDC 및 출력제어·재생예측 문헌, 공식 데이터 확보 경로. 실제 마운트/DB 감사와 구현은 별도 조사다.
- Exa Search skill 및 searching.md / patterns-papers.md / source-quality.md 적용.
- **sources_reviewed: 89** — Exa 검색 13회의 numResults 합계. 원문 89편을 정독했다는 뜻이 아니다.
- 실제 검색 반환 89건 → URL 기준 중복 제거 79개 후보. 제목·검색 하이라이트를 선별 검토했다.
- Exa 원문 조회: 총 27 URL 요청 → 중복 제거 26 URL. 그중 25 URL에서 본문 또는 공식 메타데이터가 추출되었고 IEEE PDF 1 URL은 제목만 추출되었다.
- 채택 원문 URL 23개: 핵심 논문 5개, HVDC 공식자료 2개, 공식 데이터·기상자료 16개. 2021 ToU 논문과 중복 학회 요약은 보조 검토 후 핵심 근거에서 제외했다.
- 뉴스, Exa 논문 색인, 교육데이터 미러, 상업 서지 사이트는 원문 발견 경로로만 사용했다.
- API 인증 호출, 원시 CSV/XLSX 다운로드, 외부기관 연락은 수행하지 않았다. 공개 페이지·명세가 확인된 것과 실제 다운로드 성공은 구분한다.

## 1. 기획서 HVDC 주장 검증

기획서의 제3연계선 **완도–동제주, 200 MW, ±150 kV, 약 98 km, 2024년 11월 상업운전**은 한전 원문으로 확인된다. 한전 2025년 1월 현장 기사는 상업운전 개시일을 **2024-11-29**로 명시한다. 2023년 논문의 '2023년 말 준공 예정'은 당시 계획이며 실적 날짜로 쓰면 안 된다.

| 설비 | 경로 | 방식 및 정격 근거 | 운영 특성과 모델 반영 | 검증 수준 / 한계 |
| --- | --- | --- | --- | --- |
| HVDC #1 | 해남–제주 | LCC, ±180 kV, 150 MW × 2: [Son & Jang, 표 1](https://www.mdpi.com/1996-1073/16/15/5699) | 역송 가능하나 방향 전환 절차 필요. VSC처럼 매 시점 자유롭게 부호를 바꾸는 모델은 부적절 | 경로·LCC는 [한전 공식](https://www.kepco.co.kr/KEPCO_FILE/html/2026_04/sight.html), 역송 실적은 KPX로 확인. 정격은 2023 논문 값이며 2026 운전한계는 별도 필요 |
| HVDC #2 | 진도–서제주 | LCC, ±250 kV, 200 MW × 2: [Son & Jang, 표 1](https://www.mdpi.com/1996-1073/16/15/5699) | 정격과 운영 가용능력 분리. 역송 기술 가능성을 현재 정상운전 허용으로 해석하지 않기 | 2023 UC 논문은 #1 150 MW/#2 250 MW 운영가정을 사용. 정격 300/400 MW와 혼합 금지 |
| HVDC #3 | 완도–동제주 | VSC, 200 MW, ±150 kV, 약 98 km: [한전 2026.04](https://www.kepco.co.kr/KEPCO_FILE/html/2026_04/sight.html) | 빠른 양방향 전력전송, 유효·무효전력 독립 제어. 수급 모델에서 제어가능 DC 링크, AC 검증에서는 Q 제약도 필요 | 2024-11-29 상업운전: [한전 2025.01](https://home.kepco.co.kr/kepco/front/html/WZ/2025_01/site.html). 정격이 모든 시간 가용하거나 내부 혼잡이 사라짐을 뜻하지 않음 |

[KIEE 2023 UC 원문](http://www.tkiee.org/kiee/XmlViewer/f423006)은 당시 모델에서 LCC 정역송 변환에 최소 6시간 차단, #1 최소 정송 30 MW/#2 40 MW를 사용한다. 이는 연구 시나리오의 근거이며 현재 운전규칙을 확인한 값이 아니다. 현재 모델에는 링크별 정격·pole 가용상태·방향·전환 조건·시점별 가용한계·램프·예비력 제약을 별도로 확보해야 한다.

[전력거래소 2024년 제주 운영실적](https://www.kpx.or.kr/boardDownload.es?bid=0159&list_no=74566&seq=1)은 제1연계선 역송 후 극성전환 사례 및 연간 HVDC 역송 21.3 GWh/83회를 보고한다. 따라서 'LCC는 역송 불가능'이라는 단순화도 틀린다. 한전 기사의 공급능력 360→600 MW는 해당 공급능력 지표이며 링크 정격 합계 또는 시간별 최적화 한계로 대체하지 않는다.

## 2. 제주 특화 핵심 논문 5편

| 제목 / 저자 | 연도 | 원문 URL | 검증 내용 및 기획서 적용 | 필요한 데이터 | 한계 / 검토 수준 |
| --- | --- | --- | --- | --- | --- |
| The Operation Strategy of the MIDC Systems for Optimizing Renewable Energy Integration of Jeju Power System — Hyeokjin Son, Gilsoo Jang | 2023 | [Energies 16(15), 5699; DOI 10.3390/en16155699](https://www.mdpi.com/1996-1073/16/15/5699) | DC 조류 PTDF+LP로 HVDC 운전점·출력제어 최적화, AC 조류 사후 비교. 부록 A1 열발전 10기 Pmin/Pmax 및 bus, A2 39회선 이름·연결 및 MVA rating, A3 재생 bus 시나리오 공개 | bus별 부하/발전, 선로 X·정격·연결, HVDC 범위와 주파수 안정한계, 초기 AC 조류 | 원문·부록 검토. Data Availability는 기밀 비공개. 전체 R/X/B·GIS·변압기·실제 bus 부하 없음. 재현 가능한 전체 계통 case나 2026 topology가 아님 |
| HVDC 운영 전략에 따른 제주 전력 계통의 출력제어 및 운영비용 분석 — 최지웅 외 | 2023 | [KIEE 72(6), 701–708; DOI 10.5370/KIEE.2023.72.6.701](http://www.tkiee.org/kiee/XmlViewer/f423006) | DC OPF 기반 UC로 #1 역송/#3 추가 비교. 증감발·기동정지·최소 운전/정지·HVDC 최소 정송 제약이 출력제어에 영향 | 발전비용·Pmin/Pmax·램프·최소 up/down, 수요·재생 가용출력, HVDC 범위·방향, 선로 X·한계 | 원문 검토. 2022-10-24를 2036 설비·부하로 확대한 시나리오. DC 모델은 Q·전압·저항손실 무시. 현재 실측 validation이 아님 |
| 하루전 출력제어 예측을 위한 최적화 모델 개발: 제주 전력시장을 중심으로 — 이정범 외 | 2025 | [KIEE 74(3), 417–424; DOI 10.5370/KIEE.2025.74.3.417](http://www.tkiee.org/kiee/XmlViewer/f435229) | NWP→풍력·태양광 이용률/수요→PCA/MLR→XGBoost. 학습 2021–2022, 검증 2023, 평가 2024.01–06. 풍력 제어량 RMSE 11 MWh, R² 0.41 보고 | 당시 발행 GFS/LDAPS, 시간별 제주 수요·원별 거래량·풍력 제어량·설비용량, 휴일 | 원문 검토. 태양광 제어량 없어 풍력만 평가. must-run·비시장 발전은 학습 반영 가정. 2024.06 시장제도와 2024.11 HVDC3 이후 재학습/기간 외 평가 필요 |
| Demonstration of Output Control Using a Renewable Flexible Interconnection Operation System in the Jeju Power System — 김현진 외 | 2025 | [KIEE 원문, 74(11), 1857–1861](http://www.tkiee.org/kiee/XmlViewer/f447225) | 변전소-A #3 주변압기, 22.9 kV 5개 선로에서 2024-05-02~03 실증. D-1 15분 예측→순부하/전압안정 수용한계→D-day SCADA/ADMS 지령. 약 7.2 MW 감축 | 발전소별 15분 출력·예보·허브높이/출력곡선, 배전 부하, 변압기·선로·전압·수용한계, 지령/실행 기록 | 원문 검토. 익명 변전소-A로 실제 GIS/ID 복원 불가. 실제 주변압기 60 MW를 실증에서 20 MW로 가정. 2일 실증을 연간/제주 전체에 일반화 불가 |
| A Probabilistic Estimation of Transmission Congestion for Mitigating Wind Power Curtailments — Minju Lee, Jin Hur | 2023 | [EWHA 기관 저장소; DOI 10.1109/ACCESS.2023.3337210](https://dspace.ewha.ac.kr/handle/2015.oak/267716) | R-LSTM과 KDE/Metropolis–Hastings 수요·풍력 시나리오로 계절·시간별 혼잡 확률. 제주 풍력단지 A와 수요 실측 사용 | 단지별 시간 출력, 수요, 예측오차/분포, network·열한계 | 기관 초록·저자·연도·DOI 검토. [IEEE PDF](https://ieeexplore.ieee.org/iel7/6287639/10005208/10328979.pdf)는 본문 추출 실패. 구현식·실험분할·정량결과 미검증, 개념 근거로만 채택 |

수급·선로 제약 계산과 경험적 출력제어 예측은 역할이 다르다. 실제 단지 출력은 제어 후 출력이므로 재생 **가용출력**과 같지 않다. 제어 이력 없이 실제 출력만 학습하면 날씨로 생산 가능한 전력을 과소추정할 수 있다.

## 3. 공식 추가 데이터 확보 경로

연도는 확인한 문서·목록 기준이다. 등록일·수정일·차기 등록예정일은 실제 시계열 마지막 시각이 아니다. 목록 표시 기준을 원시 파일에서 재확인해야 한다.

| 제목 / 기관 | 확인 기준 | 공식 URL | 검증 내용 / 확보 경로 | 필요한 데이터 | 한계 |
| --- | --- | --- | --- | --- | --- |
| 2024년 연간 제주 전력계통 운영실적 / KPX | 2024 실적, 2025.02 문서 | [공식 PDF](https://www.kpx.or.kr/boardDownload.es?bid=0159&list_no=74566&seq=1) | 송·변전 집계, 신규 표선–아시아태양광 T/L 175 MW/14.7 km 등, 발전/HVDC/전압/주파수/제어 실적 | 최신 단선도, 회선 R/X/B·rating·스위치, 증감 시점 | PDF에 개별 GIS·bus SCADA 없음. 집계 검증용 |
| 송전설비현황 / KEPCO | 목록 2021-12-31 | [15101529](https://www.data.go.kr/data/15101529/fileData.do) | 선로/회선/전선 길이·지지물·애자류, 가공/지중/수중·전압별 통계. 기관 XLSX | 제주 회선 GIS·from/to bus·병렬·도체/케이블·R/X/B·열한계 | 공식 설명에 다운로드 회원가입·로그인 요구. 통계는 topology/shapefile이 아님 |
| 변전설비현황 / KEPCO | 목록 2021-12-31 | [15101530](https://www.data.go.kr/data/15101530/fileData.do) | 지역본부별 변전소 수·변압기용량·차단기/콘덴서/리액터 수 | 개별 변전소 ID·좌표·busbar, 변압기 연결·임피던스·탭·상태 | 집계표로 개별 파라미터 복원 불가 |
| 지역별 공급가능 변전소 / KEPCO | 목록 2024-05-13 | [15128065](https://www.data.go.kr/data/15128065/fileData.do) | 시도·시군구·읍면동 공급변전소 CSV/자동 API | 실명 변전소·정확 위치·공급구역·부하 연결 | 공식 설명에 위치 노출 방지용 공급변전소 비식별화 명시. 행정구역만으로 bus 확정 불가 |
| 풍력발전현황 / 제주도 | 목록 2024-12-31 | [15047557](https://www.data.go.kr/data/15047557/fileData.do) | 발전소명·설비용량·설치장소 주소·원동력·기준일; CSV/자동 API | 공식 ID·개별 터빈 좌표·허브높이/출력곡선·bus·계량 | 주소 geocode는 검증된 설비 좌표가 아님. 설명은 kW, 컬럼은 MW로 불일치: CSV 단위 확인 필수 |
| 태양광발전소현황 / 제주도 | 목록 2024-12-31 | [3082724](https://www.data.go.kr/data/3082724/fileData.do) | 상호·설비용량(kW)·지번주소·사업개시일·기준일 CSV | ID·좌표/부지·tilt/azimuth·인버터/DC 정격·bus·계량/제어 | 원문 컬럼에 위경도·bus·시계열 없음. 상호는 안정 ID가 아님 |
| 제주 기상관측 및 태양광 발전 / 동서발전 | 목록 2025-12-31, 수정 2026-08-10 | [15126430](https://www.data.go.kr/data/15126430/fileData.do) | 1시간 ASOS+KPX 시장참여 PV. 설비 MW(월 주기), 발전 MWh, 일사 MJ/㎡·일조 hr | 당시 발행 예보, 개별 계량, non-market 구분 | 미출력은 공란. 지역 집계·관측자료이며 발전소별 출력/예보 archive가 아님 |
| 제주계통운영정보_GW(송전단포함) / KPX | 공개 API 메타 | [15158505](https://www.data.go.kr/data/15158505/openapi.do) | 기존 PUBC→GW 전환, 송전단수요 컬럼 추가. JSON/XML | GW 상세 응답·기간 조건, 링크별 HVDC·전압·Q | 기능별 명세가 충분히 추출되지 않음. 키/활용신청 후 응답 확인 필요. 구형 필드 복사 금지 |
| 오늘제주계통운영정보 / KPX | 가이드 2024-11-29 표기 | [15058920 공식 영문 명세](https://www.data.go.kr/en/data/15058920/openapi.do) | 5분 chejusukub5mToday/getChejuSukub5mToday. baseDatetime·suppAbility·currPwrTot·renewPwrTot/Solar/Wind(MW) | archive 범위·발전/송전단·PPA/BTM, GW 전환/폐기 확인 | 구형 명세. 오늘 조회를 전체 과거 archive로 오인하지 않음. MW와 CSV MWh 의미 구분 |
| 전국 PV/풍력 제어횟수 및 제주 풍력 제어량 / KPX | 페이지 2026-06-30 기준 | [15132422](https://www.data.go.kr/data/15132422/fileData.do) | 기존 시간별 제주/육지 목록 통합. 제주 건/MWh, 공란 날짜는 미발생 | 발전소/선로별 지령·실행·제어 전 가용출력, PV 제어 MWh, 입찰 감소 | PV 제어량 별도 산정 안 함. 풍력은 차단 당시 출력×시간 참고값. 2026.2분기 제주 신규실적 없음/과거 자료 참고 명시 |
| 제주지역 PPA 발전량 / KEPCO | 2022.12–2024.06, 57행 메타 | [15100272](https://www.data.go.kr/data/15100272/fileData.do) | 본부·기간·종류·구입/발전량(kWh), PV/풍력/바이오 CSV | 시간별 PPA/BTM 계량, 발전소 ID·bus | 1회성 집계. 시간/발전소별 시계열 대체 불가, 시장참여 제외분 보완용 |
| 시군구별 전력판매량 / KEPCO | 월별 설명 확인 | [15069677](https://www.data.go.kr/data/15069677/fileData.do) | 연도·시도·시군구·계약종/업종별 월 판매량 | bus 시간 부하·공식 공급구역·시간/계절 패턴 | 판매량은 bus 부하 실측 아님. 공간배분에 쓰면 추정 표시 |
| 전력판매량 공식 게시판 / KEPCO | 2026.05 자료·2026-07-20 게시 확인 | [공식 게시판](https://www.kepco.co.kr/home/customer/library/electricity-statistics/sales-volume/boardList.do) | 월 첨부 목록. 조회에서 가장 앞 목록은 2026년 5월 | 첨부 컬럼·단위·기간, 이후 게시 여부 | 화면에 보인 범위만 확인. 기관 전체 최신자료가 반드시 2026.05인 것은 아님 |
| ASOS 자료 / KMA | 관측 포털 설명 | [ASOS 포털](https://data.kma.go.kr/data/grnd/selectAsosRltmList.do?pgmNo=36) | 분/시간/일/월/연; 기온·바람·습도·일사. 시간 1회 1년 조회, 장기 파일셋, QC 0 정상/1 오류/9 결측 | 제주 지점·좌표·관측높이·이동·요소 가용성, 공간보간 | 지점마다 일사/풍속 가용성 다름. 전일자료 당일 10시 이후. 부족하면 AWS 별도 확인 |
| ASOS 조회서비스 / KMA | API 활용조건 확인 | [15059218](https://www.data.go.kr/data/15059218/openapi.do) | REST JSON/XML·ServiceKey, 심의승인, 개발 10,000, 출처표시 제1유형 | 목적별 관측/기간 상세기능·응답 검증 | 추출된 상세기능은 getSnowCover. 장기 전 요소 시간자료와 같은 endpoint로 쓰지 않음 |
| LDAPS 자료 / KMA | 운영중단 안내 확인 | [LDAPS 설명](https://data.kma.go.kr/data/rmt/rmtList.do?code=340&pgmNo=65) | 과거 UM 1.5 km/70층·GRIB2·최대 100 GB 신청. **2026년 3월 UM 자료 제공 중단** 명시 | 현재 대체모델 공급/권한, 당시 발행/유효시각 archive, 변경이력 | 옛 논문 LDAPS를 2026에도 같은 방법으로 수집 가능하다고 가정 금지. 대체모델·소급기간은 미검증 |

제주 전체 실제 송전 GIS와 전기 파라미터의 일반 공개 다운로드는 이번 공식 검색에서 확인하지 못했다. 이는 '어디에도 없다'는 주장이 아니다. 보유 데이터 provenance를 먼저 감사하고 학술 부록으로 부분 연결을 교차검증한다. 부족한 현재 단선도·R/X/B·변압기/탭·bus 부하·SCADA는 KEPCO/KPX 공공데이터 제공신청 또는 연구협력의 요청 항목이다. 비식별화·논문 비공개 명시를 감안하면 제공범위/조건은 기관별 심사 대상이다. 이 조사에서 신청·연락이나 법적 제공권한/의무 판단을 수행하지 않았다.

**별도 루트 조사에서 추가 확인한 업데이트 후보:** [KPX 제주지역 연료원별 시간대별 발전량 15100214](https://www.data.go.kr/data/15100214/fileData.do)은 현재 파일명 기준 2025-12-31, 등록 2026-06-16, 43,800행으로 표시되지만 설명은 여전히 2024년이다. 로컬 발전원별 CSV는 2024년만 확인되었으므로 신규 파일 다운로드 후 실제 연도·연료원 수·시간축·포함범위를 검사할 가치가 있다. 이는 루트의 원문/파일 감사 결과를 전달받은 항목이며 이 하위 조사의 Exa 89건·채택 23개 집계에 추가하지 않았다. 파일명과 설명의 불일치만으로 2025 데이터가 실제 포함되었다고 확정하지 않는다.

## 4. 데이터 정의에서 먼저 결정할 사항

1. **시장참여/PPA/BTM 경계:** 원별 발전량과 수요·공급을 섞기 전에 포함범위를 일치시킨다. KPX 보고서는 시장 PV/PPA/BTM을 구분하며 총 공급 집계에는 HVDC 유입도 포함한다. HVDC 유입을 도내 발전과 동일 처리하지 않는다.
2. **발전단/송전단:** GW는 송전단수요를 추가했다. 거래량과 발전량은 소내소비/손실 경계가 다르므로 단순합으로 검증하지 않는다.
3. **MW/MWh·시간경계:** 5분 MW와 1시간 MWh의 단위·구간을 보존한다. 평균 전력은 에너지/실제 구간 길이. 정각 시작/끝, 1~24시, KST/UTC는 원문 가이드와 실제 timestamp로 검증한다.
4. **제어 라벨 단절:** [KPX 2024 실적](https://www.kpx.or.kr/boardDownload.es?bid=0159&list_no=74566&seq=1)은 2024-06-01 이후 입찰제 참여 발전기 출력감소를 출력제어 통계에 미반영한다고 명시한다. 통계 감소를 물리적 과잉/혼잡 감소로 해석하면 안 된다. 2024-11-29 HVDC3도 체제변화일이다.
5. **예보 시점 누출:** D-1 예측은 당시 발행된 NWP를 쓴다. 당일 관측/후일 수정 예보를 하루전 입력으로 쓰지 않는다. UM 종료·대체모델 변경도 체제변화로 기록한다.

## 5. 추가 확보 우선순위와 구현 수준

| 순서 | 필요한 자료 | 가능한 검증 | 미확보 시 범위 |
| --- | --- | --- | --- |
| 1 | 최근 단선도, from/to bus·R/X/B·정격, 변압기 연결·임피던스·tap | AC/DC 조류·내부 혼잡·전압 | 보유 GIS/학술 연결 기반 연구 시나리오. 추정값을 실측으로 표시하지 않음 |
| 2 | 링크별 HVDC P/Q·방향·가용한계·pole/outage·제약, must-run | 수급 최적화·정비·방향전환·단일고장 | #1/#2 방향 고정 등 명시적 가정, #3 양방향 비교 |
| 3 | plant→bus, bus 부하, 실제/가용출력·제어지령 | 공간 동적 Twin, 실측 flow·제어·손실 검증 | 지역합계를 가중치로 배분한 추정 시나리오 |
| 4 | 당시 발행 예보 archive·제어 라벨·제도변경 | D-1 예측, 체제별 backtest/불확실성 | 관측 설명·기간 확인된 오프라인 예측 |

논문 부록 39회선은 연결·이름·rating 교차검증에 유용하지만 선로 X가 없으면 PTDF/DC 조류를 정할 수 없다. 주소·집계 발전량·제주 총수요만으로 실제 Level 4를 인증할 수 없다는 점은 기획서의 전기 파라미터 확보 조건과 일치한다.

## 6. 제외·미완료 검증

- 뉴스 HVDC 소개는 공식 한전 원문으로 대체.
- Exa publication/RePEc/Grafiati/교육데이터 미러는 발견 경로로만 사용.
- 2026 synthetic 4-bus/IEEE 30-bus 제주 DLR 논문은 실제 제주 topology 검증자료가 아니어서 제외.
- [2021 ToU 논문](https://www.mdpi.com/2079-9292/10/2/135)은 원문 초록/개요 확인 후 이번 5편에서는 최신 계통·운영·예측 근거 우선.
- IEEE PDF 본문 추출 실패로 기관 초록 범위를 넘어 구현식·정량결과 확정 안 함.
- 발전소 단위, API 구형/신형 차이, 기준일/메타 갱신일 차이는 실제 파일/응답으로 확인 필요.

## 7. Exa 검색 로그

검색 objective: 기관·논문·공공데이터 원문 우선, 뉴스/블로그/광고 제외, 실제 필드·운영제약 검증. 검색 실패·재시도 없음.

| 번호 | query | numResults |
| --- | --- | --- |
| 1 | 한국전력 공식 제주 제3 HVDC 연계선 완도 동제주 200MW ±150kV 2024년 11월 상업운전 전압형 설명 | 8 |
| 2 | category:research paper Jeju island power system renewable wind solar curtailment HVDC transmission constraints operation | 10 |
| 3 | category:research paper Jeju island wind solar renewable power forecasting curtailment probabilistic forecast data | 10 |
| 4 | 제주 전력망 송전선 변전소 위치 전기 파라미터 발전소 전력수요 기상 출력제어 공공데이터 한국전력 전력거래소 | 10 |
| 5 | 전력거래소 공식 제주 HVDC 제1 제2 연계선 설비용량 300 400 최소운전 2024 전력계통 운영실적 | 8 |
| 6 | category:research paper The Operation Strategy of the MIDC Systems for Optimizing Renewable Energy Integration of Jeju Power System Son Jang 2023 | 5 |
| 7 | 기상청 기상자료개방포털 ASOS AWS 일사 풍속 제주 수치예보 API 공식 이용 | 5 |
| 8 | 한국전력공사 송전선로 현황 변전소 위치 좌표 공간정보 전력설비 데이터 공공데이터포털 | 8 |
| 9 | 제주특별자치도 풍력발전현황 태양광발전소현황 설치장소 좌표 공공데이터포털 | 5 |
| 10 | 기상청 API허브 수치예보 LDAPS RDAPS 자료 다운로드 예보 발표시각 공식 | 5 |
| 11 | 한국전력거래소 제주계통운영정보 GW 송전단포함 HVDC 실시간 풍력 태양광 응답 필드 API 15158505 | 5 |
| 12 | 한국전력공사 시군구별 전력판매량 정보 공공데이터 월별 API | 5 |
| 13 | Demonstration of Output Control Using a Renewable Flexible Interconnection Operation System in the Jeju Power System 10.5370 2025 | 5 |

합계 **sources_reviewed = 89**. 검색 반환 89건, 중복 제거 후보 79개. 채택은 검색 순위가 아니라 원문 검증으로 결정했다.
