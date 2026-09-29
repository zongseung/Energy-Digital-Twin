# 제주 전력망 디지털 트윈: 수학 모델과 문헌 근거

작성일: 2026-09-29. 대상 기획서: `jeju_power_grid_digital_twin_plan.md` v0.1 전체. 본 문서는 수학·계통 문헌조사이며 마운트/DB 데이터의 독립 감사나 구현 결과가 아니다. 전달받은 데이터 범위는 제주 전체 수요 2021–2026, 발전원별 집계 2024, 월별 수급 2021–2026이다. 연도 표기가 그 연도 전체 관측을 뜻하지 않으며, 발전원 집계의 값이 발전량·출력·설비용량 중 무엇인지는 데이터 감사 결과에 따라 확정해야 한다.

**결론:** 지금 검증할 수 있는 것은 제주 전체 수급의 시간 변화와 집계 일관성이다. 실제 연결 관계와 전기 파라미터가 확인되기 전에는 선로 전력, 전압, 실제 혼잡, 실제 출력제한량을 산출할 수 없다. 공간 배분과 가상 망을 사용한 계산은 가정에 따른 시나리오이며 실제 제주 계통 상태의 복원이 아니다. 이는 아래 방정식과 관측가능성 조건에서 도출한 프로젝트 판단이다.

## 1. 단계와 데이터 진입 조건

| 단계 | 최소 모델과 산출물 | 필요한 데이터 | 현재 판단 및 검증 |
|---|---|---|---|
| M0 집계 수급 | 단일 지역 에너지 수지, 총수요 재생, 발전원 비중 | 동일 경계·동일 시간 간격의 수요/발전/연계 거래, 단위 정의 | 전달 범위에서 착수 가능. 단위·기간·월 합계 교차 검증. 2024 발전 집계를 다른 연도 실측처럼 사용하지 않음 |
| M1 공간 배분 | 합계 보존 부하/발전 배분, 가중치 민감도 | 설비 위치·용량, 행정구역 사용량 등 외부 제약과 bus 매핑 | 입력이 확보되면 시나리오 가능. 합계 보존은 검증할 수 있지만 개별 bus의 참값은 별도 계측 없이는 검증 불가 |
| M2 DC PF | 송전망 유효전력 흐름·위상각 | 실제 bus/branch 연결, 운전 상태, x, tap/위상변이, bus별 유효전력 | 상세 topology/x 미확인으로 현재 제주 실망 계산 불가. 표준 사례와 AC 비교로 수치 검증 |
| M3 다기간 DC OPF | 발전/HVDC/ESS 운전과 출력제한 시나리오 | M2 + 선로 한계, 발전 가용출력·최소출력·비용, ESS MW/MWh/효율/SOC, HVDC 운전 한계 | 입력 확보가 조건. 최적화 잔차·에너지 수지·AC 사후검증. 단일 지역 최적화는 가능해도 내부 선로 혼잡을 설명하지 못함 |
| M4 AC/배전 PF | 전압·무효전력·손실·전류·설비 loading | M2 + r/x/b, 변압기/tap, P/Q 부하, 발전기 전압·Q 한계, shunt | 현재 필수 입력 부족. 균형 송전 AC부터; 배전 불평형은 상별 데이터 확보 시 |
| M5 상태 추정/망 식별 | 실측으로 상태·스위치 상태 추정 | 계측 위치·오차, V/P/Q/I·PMU, 알려진 망 또는 후보 망, 충분한 독립 관측 | 집계 수급만으로 불가. 관측가능성·식별가능성부터 평가 |
| M6 동특성/EMT | 주파수 응답·과도 전압·인버터/보호 동작 | M4 + 관성, AVR/governor, 인버터·HVDC 제어, 보호 설정, 고속 이벤트 계측 | 현재 범위 밖. 준정적 시계열 계산 성공으로 동적 안정성을 주장할 수 없음 |

M2/M4 방정식은 [MATPOWER 원논문](https://matpower.org/docs/MATPOWER-paper.pdf), M3 저장장치는 [PyPSA 공식 저장장치 문서](https://docs.pypsa.org/stable/user-guide/optimization/storage/), M5/M6의 경계는 [PNNL 상태 추정 연구](https://arxiv.org/abs/1904.08036)와 [Maui EMT 검증 원문](https://docs.nlr.gov/docs/fy21osti/76808.pdf)에 근거한다. 단계 분류 자체는 이 기획서를 위한 제안이다.

## 2. 집계 수급: 먼저 맞춰야 하는 물리량

동일한 제주 경계의 시간 구간 t에 대해, 전력으로 쓸 때는 모두 구간 평균 MW로 통일한다.

\[
G_t+H_t^{in}+P_t^{dis}+U_t
=D_t+H_t^{out}+P_t^{ch}+P_t^{loss}.
\]

G는 계통으로 실제 공급된 발전, H는 제주 경계에서 계측한 HVDC 수입/수출, D는 공급해야 할 부하, U는 미공급 부하의 시나리오 변수다. U를 실측 수요가 이미 공급된 전력인 데이터에 중복 추가하지 않는다. 손실은 해당 경계에 포함되는 손실만 넣는다. 전력수요·판매량·발전소 송전단 발전량은 측정 경계가 다를 수 있으므로 원자료 정의를 먼저 맞춘다. 발전의 정의에 출력제한 전 가용출력이 들어 있다면 G=A−C로 별도 정의한다. 이미 실현된 발전에 C를 다시 차감하지 않는다.

\[
E_{month}=\sum_{t\in month}P_t\Delta t_t,
\qquad [P]=MW,\ [\Delta t]=h,\ [E]=MWh.
\]

월간 MWh를 시간별 MW로 복원할 수 없다. 구간평균 MW에는 사각형 적분을 쓰고, 순간 샘플을 적분한다면 그 보간 가정을 밝힌다. 불완전한 월, 결측 시간을 포함한 월은 완전 월과 분리한다. 경계·집계 정의가 맞기 전의 잔차를 손실, HVDC 역송, 출력제한 또는 미공급 전력으로 단정하지 않는다. 이 수지는 [PyPSA의 bus별 전력 수지](https://docs.pypsa.org/stable/user-guide/optimization/energy-balance/)를 제주 단일 지역으로 축약한 제안이다.

**현 데이터의 검증:** 날짜/시간대 및 계량 구간 정렬 → MW/MWh 통일 → 시간 자료의 월 적분과 월별 자료 비교 → 남은 잔차를 정의 차이/누락/추정 오차로 분리. 실제 HVDC 흐름이 없다면 수급 잔차를 특정 연계선별 흐름으로 나눌 근거가 없다.

## 3. 공간 배분: 제약을 지키되 참값이라고 부르지 않는다

전체 부하 D를 여러 bus에 배분하는 최소식은 다음과 같다.

\[
d_{i,t}=w_iD_t,\quad w_i\ge0,\quad\sum_iw_i=1.
\]

행정구역 전력사용량·산업용 수요·시간 패턴 등 제약이 추가되면 다음 QP로 일반화할 수 있다. 이것은 특정 논문의 정확 복원 공식을 옮긴 것이 아니라 본 과제의 합계 보존 모델 제안이다.

\[
\min_{d_t}\sum_i\frac{(d_{i,t}-\tilde d_{i,t})^2}{\sigma_i^2}
\quad\text{s.t.}\quad
\mathbf1^Td_t=D_t,\ d_t\ge0,\ Ad_t=b_t,\ d_t\le\bar d_t.
\]

\(\tilde d\)는 외부자료 기반 기준 배분(MW), \(\sigma\)는 그 불확실성(MW), A/b는 실제 확보한 지역 집계 제약, \(\bar d\)는 근거 있는 유효전력 상한이다. 변전소 MVA를 부하 MW로 곧바로 쓰지 않는다. 전력요인·운전 여유·공급구역 연결이 필요하다. 지역 경계와 feeder 경계가 다르면 교차 매핑을 확보한다. 풍력/태양광 발전도 발전원 합계·각 설비용량·지역 가용출력 상한을 함께 지켜 배분한다.

**식별 불가능성:** N개 부하에 합계 하나만 있으면 일반적으로 N−1개 자유도가 남는다. 최적화 목적함수가 한 해를 선택해도 그 해가 실제 부하라는 증거는 없다. 설비용량 비례 발전 배분은 모든 설비의 같은 capacity factor를 가정하므로 날씨·정비·국소 출력제한 차이를 복원하지 못한다. 측정 합계 보존, 각 설비 상한, 서로 다른 가중치에서 결과 변화, 독립 feeder 계측과의 오차를 검증한다. 가중치 대안을 바꿨을 때 혼잡 위치가 바뀌면 위험도 역시 조건부 결과로 표시한다.

## 4. AC PF와 DC PF: 이름이 비슷해도 HVDC와 다르다

AC의 최소 전력 방정식은 다음과 같다.

\[
S_i^{inj}=P_i+jQ_i
=V_i\overline{\sum_jY_{ij}V_j}.
\]

균형 3상 송전의 단상 등가/per-unit 모델로 시작한다. 내부 계산의 S/V/Y는 일관된 pu, \(P,Q\) 출력은 MW/Mvar, 위상각은 rad다. \(Z_{base}=V_{LL,base}^2/S_{3\phi,base}\)에서 kV²/MVA는 Ω이며, 선로 Ω와 pu를 섞지 않는다. line의 단위길이 r/x에 실제 길이·병렬회선·운전 회선 수를 반영하고, 용량성 charging과 transformer tap/위상변이를 별도로 처리한다. 연결된 각 island에는 위상각 기준 및 전력 불일치/손실을 담당하는 slack 규칙이 필요하다. PQ/PV bus를 구분하고 Q 한계 초과 시 voltage setpoint 유지 가능성을 확인한다. PF가 수렴했다는 사실은 설비 한계를 만족했다는 뜻이 아니다. [MATPOWER 모델·PF·OPF 공식](https://matpower.org/docs/MATPOWER-paper.pdf).

DC PF는 **AC 송전망에 대한 선형 근사**다. 손실/저항/charging을 무시하고, 전압크기를 1 pu 근방으로 고정하며, 위상차가 작다고 가정한다. 무효전력·전압크기를 산출하지 않는다. 다음은 tap=1, phase shift=0인 최소식이다.

\[
f_{\ell,t}=S_{base}\frac{\theta_{i,t}-\theta_{j,t}}{x_\ell^{pu}},
\quad Af_t=p_t,\quad
\theta_{ref,t}=0.
\]

A의 선로 열은 from bus +1, to bus −1이다. \(f,p\)는 MW, \(S_{base}\)는 MVA 기준의 유효전력 스케일, \(x\)는 pu, \(\theta\)는 rad다. tap/위상변이가 있으면 \(f=S_{base}(\theta_i-\theta_j-\phi_\ell)/(x_\ell\tau_\ell)\)처럼 해당 모델에 맞게 넣는다. 섬 분리 후에는 각 island별 수지와 기준각을 검사한다. 단절 섬에 공급이 없는데 slack만으로 부하가 공급된 것처럼 처리하면 안 된다. [MATPOWER DC 유도](https://matpower.org/docs/MATPOWER-paper.pdf).

**HVDC는 별도의 물리 설비**이며 위의 AC 위상차식으로 계산하는 선로가 아니다. 정상상태 MVP에서는 AC bus들 사이의 제어 가능한 전력 거래로 표현한다. 전송용량, 방향별 허용 범위, 손실, ramp 및 운영제약을 넣는다. 수입/수출을 각각 비음수 변수로 두면 sending-end h에 대해 receiving-end는 \(\eta h\)다. 효율 \(\eta<1\)인 단일 변수를 단순히 음수로 뒤집는 모델은 역방향 손실을 잘못 만들 수 있다. 제주 경계에서 실측한 H에 변환 손실을 또 더하지 않는다. 전압형/전류형 변환소의 Q 제어·Q 소비를 AC 분석할 때는 별도 모델/파라미터가 필요하다. [PyPSA 공식 Link 수지](https://docs.pypsa.org/stable/user-guide/optimization/energy-balance/).

DC 근사의 검증은 같은 입력의 AC PF와 비교하는 것이다. 평상시뿐 아니라 높은 재생에너지·높은 loading·선로 정지 시나리오에서 MW 오차와 한계 초과 판정의 차이를 기록한다. 저압/배전의 높은 R/X, 전압 변화, 불평형은 DC 근사가 불리하므로 이 영역에 그대로 확장하지 않는다. [PNNL 배전 모델 설명](https://arxiv.org/abs/1904.08036).

## 5. DC OPF + ESS + 출력제한의 최소 다기간 모델

\[
\min\sum_t\Delta t_t\left(
\sum_gc_g g_{g,t}+\sum_rc_r^{curt}c_{r,t}
+c^{shed}\sum_iu_{i,t}
+c^{cycle}\sum_s(p_{s,t}^{ch}+p_{s,t}^{dis})
\right).
\]

비용은 원/MWh, 운전·출력제한·미공급·충방전 전력은 MW로 두면 목적함수는 원이다. 제안한 penalty는 연구 시나리오 설정이며 실제 시장가격이나 제주 운전규칙의 관측값이 아니다. 발전 가용량 \(a\)에 대해 \(0\le c\le a\), 공급은 \(a-c\)다. bus 순주입을 다음처럼 정의하고 앞 절 DC식과 선로 한계를 붙인다.

\[
p_{i,t}=\sum_{g\in i}g_{g,t}+\sum_{r\in i}(a_{r,t}-c_{r,t})
+\sum_{s\in i}(p_{s,t}^{dis}-p_{s,t}^{ch})
+q_{i,t}^{HVDC}+u_{i,t}-d_{i,t},
\]
\[
Af_t=p_t,\quad |f_{\ell,t}|\le\bar f_\ell,\quad
\underline g_g\le g_{g,t}\le\bar g_{g,t}.
\]

\(q^{HVDC}\)는 terminal별 손실을 적용한 순주입이며 수입 terminal 양수, 수출 terminal 음수다. 발전 ramp 한계가 MW/h면 \(|g_t-g_{t-1}|\le R_g\Delta t_t\)를 붙인다. \(\bar f\)를 MW로 사용하는 DC 모델에서는 MVA nameplate를 그대로 같은 숫자의 MW로 넣었다는 가정이 있는지 밝히고 AC 사후검증한다. 발전기 최소출력·최소가동시간·기동 비용이 중요한 연구 질문이면 unit commitment가 필요하며 단순 LP 경제급전으로 그 결과를 주장하지 않는다. [MATPOWER DC OPF](https://matpower.org/docs/MATPOWER-paper.pdf), [PyPSA 전력 수지](https://docs.pypsa.org/stable/user-guide/optimization/energy-balance/).

ESS의 최소 시간 연결식은 다음이다.

\[
e_{s,t}=\eta_{stand,s}^{\Delta t_t}e_{s,t-1}
+\eta_{ch,s}p_{s,t}^{ch}\Delta t_t
-\frac{p_{s,t}^{dis}\Delta t_t}{\eta_{dis,s}},
\]
\[
\underline e_s\le e_{s,t}\le\bar e_s,\quad
0\le p_{s,t}^{ch}\le\bar p_s^{ch},\quad
0\le p_{s,t}^{dis}\le\bar p_s^{dis}.
\]

e는 MWh, p는 MW, 효율은 무차원이다. \(e_0\)를 명시하고, 종단 SOC 고정/하한 또는 cyclic 조건을 연구 목적에 맞게 정한다. 종단 에너지를 공짜로 소진하면 ESS 가치가 과대평가될 수 있다. [PyPSA 저장장치 공식](https://docs.pypsa.org/stable/user-guide/optimization/storage/).

충·방전 독립 변수만 둔 LP는 동시 충방전이 가능한 relaxation이다. 실제 운전 schedule을 주장하려면 \(p^{ch}p^{dis}=0\)을 보장하거나, binary z와 \(p^{ch}\le\bar p^{ch}z,\ p^{dis}\le\bar p^{dis}(1-z)\)로 제한한다. HVDC 수입/수출의 동시 거래도 운전 목적과 모델 경계에 맞게 검사한다. 작은 cycle penalty만으로 모든 가격·penalty 조건에서 동시충방전이 배제된다고 보장하지 않는다.

**검증과 불가능한 추론:** nodal 수지·KVL·선로 한계·SOC 재귀·초기/말기 조건·solver feasibility 잔차를 수치로 확인한다. curtailment MWh는 \(\sum c_t\Delta t_t\), 미공급 MWh도 별도로 보고한다. 실제 출력제한량을 식별하려면 출력제한 전 가용출력과 실현출력/제어 기록이 필요하다. 이미 출력제한된 발전량만으로 그 반사실 가용출력을 알 수 없다. topology가 없는 단일 지역 모델의 C는 지역 수급/연계 한계에 대한 시나리오이지 특정 선로 병목에 기인한 실제 C가 아니다.

## 6. 배전 DistFlow: 전체 저압망을 시작부터 만들 이유는 없다

radial·균형 등가·shunt 생략 모델에서 from i→j의 sending-end 흐름은 다음 형태로 쓸 수 있다. 전력 p/q는 부하 소비 양수다.

\[
P_{ij}=p_j+\sum_{k:j\to k}P_{jk}+r_{ij}\ell_{ij},\quad
Q_{ij}=q_j+\sum_{k:j\to k}Q_{jk}+x_{ij}\ell_{ij},
\]
\[
v_j=v_i-2(r_{ij}P_{ij}+x_{ij}Q_{ij})
+(r_{ij}^2+x_{ij}^2)\ell_{ij},\quad
v_i\ell_{ij}=P_{ij}^2+Q_{ij}^2.
\]

\(v=|V|^2\), \(\ell=|I|^2\)이며 일관된 pu 단위로 계산한다. LinDistFlow는 손실항과 이차항을 생략하므로 전압/손실 오차를 AC 모델로 검증해야 한다. SOCP에서 마지막 equality를 inequality로 완화할 때는 equality 잔차를 확인한다. convex solver의 성공만으로 물리적으로 정확한 OPF라 할 수 없다. Farivar–Low의 정확성 정리도 부하 상한 등 조건에 제한이 있으므로 제주에 무조건 적용하지 않는다. [Branch Flow 원문, Baran–Wu 모델의 계승·완화 조건](https://arxiv.org/abs/1204.4865).

제주 주요 송전망 MVP에 mesh를 radial로 바꾸어 이 모델을 억지 적용할 필요가 없다. 배전 hosting capacity·말단 전압·volt-var/volt-watt가 연구 질문이 될 때 feeder별 상세 모델을 추가한다. 불평형 분석에는 상별 연결·임피던스 행렬·P/Q와 중성선 정보가 필요하다. Hawaii 연구는 두 대표 feeder의 상세 저압망을 보강한 뒤 QSTS로 인버터 전압지원과 연간 PV 출력제한을 분석했다. 해당 상세 입력 없이 제주 전체 집계에 같은 정밀도를 기대할 수 없다. [NREL/Hawaiian Electric QSTS 보고서](https://docs.nlr.gov/docs/fy17osti/68681.pdf).

## 7. WLS 상태 추정과 topology 복원: 먼저 관측가능성

\[
z=h(x;\mathcal G,\beta)+\epsilon,\quad
\hat x=\arg\min_x(z-h(x))^TR^{-1}(z-h(x)).
\]

z는 V/P/Q/I/위상 등의 계측, \(\mathcal G\)는 운전 topology, \(\beta\)는 impedance/tap 등 파라미터, R은 계측 오차 covariance다. 혼합 물리량마다 단위에 맞는 분산을 쓰고 상관을 무시하면 잘못된 가중치가 생길 수 있다. 연결된 균형 AC 망에서 한 reference angle을 고정해 \(x=(\theta_{nonref},|V|)\)를 쓰면 자유 상태가 2N−1개다. 국소 관측가능성에는 Jacobian H가 그 상태 공간에서 full column rank여야 한다. 계측 개수가 많아도 같은 정보가 반복되면 rank는 확보되지 않는다.

부하 예측을 pseudo-measurement로 넣으면 사전 가정으로 빈 정보를 채울 수는 있다. 그것이 실제 센서가 추가되었다는 뜻은 아니다. 특히 원래 제주 합계에서 배분한 N개 부하를 독립적인 N개 계측처럼 작은 분산으로 넣으면 확신을 과장한다. PNNL 연구는 pseudo-measurement 분산을 실제보다 작게 지정할 때 더 신뢰할 수 있는 센서와 충돌하여 추정 오차가 커질 수 있음을 보였다. [PNNL WLS 원문](https://arxiv.org/abs/1904.08036).

**topology 복원은 별도 inverse problem**이다. GIS endpoint snapping은 후보 연결을 만들 뿐 switch 상태, 서로 다른 전압층, busbar 구성, 지중선 연결의 증거가 아니다. 공간상 교차가 전기적 접속인 것도 아니다. 최소한 알려진 bus/후보 branch/전압층과 수동 검증 규칙이 필요하다.

Park–Deka–Chertkov 알고리즘은 radial 선형화 모델에서 모든 leaf의 동기화된 전압·유효/무효 주입 샘플을 사용한다. 숨은 중간 노드 차수 ≥3, bus 간 주입의 비상관성, P/Q covariance의 비퇴화 조건 등이 명시되어 있다. 제주 총수요와 연간 발전원 합계는 해당 측정 조건을 만족하지 않는다. 특히 같은 날씨를 공유하는 PV/풍력은 비상관성 가정을 자동으로 충족한다고 볼 수 없다. [원문과 assumptions](https://arxiv.org/pdf/1710.10727).

Cavraro 등의 limited monitoring 연구 역시 알려진 선로 인프라·r/x·통계 및 일부 bus의 시간별 V/P/Q가 주어지고, 특정 meter 배치 조건이 있어야 스위치 topology의 유일 복원을 논한다. 논문의 'few meters'는 계측 없는 집계 통계로 새 망을 창작할 수 있다는 뜻이 아니다. [NREL topology identifiability 원문](https://www.osti.gov/servlets/purl/1544999).

**검증:** reference 처리 후 H의 rank와 conditioning, meter 제거에 따른 관측가능성, noise/결측/상관 perturbation, 독립 계측 잔차를 확인한다. topology는 동일 관측을 설명하는 후보가 여러 개 존재하는지 먼저 검사하고 알려진 switch 사건·holdout 계측으로 검증한다. 정규화나 prior로 유일해진 해와 데이터만으로 식별되는 해를 구분한다.

## 8. 시간 재생과 동적 안정성의 범위

\[
0=g(x_t,u_t)\quad\text{(quasi-static snapshot PF)},
\qquad
\dot y=f(y,x,u),\ 0=g(y,x,u)\quad\text{(동특성 DAE)}.
\]

PF/OPF를 시간 순서로 반복하고 ESS·tap 상태를 연결하면 준정적 시계열 twin이다. 섬 전체의 시간별 수요 변화로 발전/수급을 재생할 수 있지만 전압이 계산되지 않았다면 선로에 움직이는 입자가 실제 flow를 뜻하도록 표시하지 않는다. 기획서 Level 3의 '동적'은 시간에 따라 화면 상태가 변한다는 의미로 정의하고, electromechanical/EMT 동특성 해석과 용어를 구분하는 편이 안전하다. 이는 본 문서의 권장 명명이다.

상태 y에는 rotor speed/angle, governor/exciter, 인버터 제어 상태 등이 들어간다. EMT는 3상 순간파형과 훨씬 빠른 제어/전자기 반응을 다룬다. Maui 연구는 utility PSSE 모델과의 정상상태 비교 후 단상 고장·발전기 trip 사건의 실측 전압/전류/주파수 응답으로 PSCAD 모델을 검증했다. 높은 IBR·약계통에서는 positive-sequence 동특성 모델도 일부 제어 상호작용을 놓칠 수 있었다. [Maui PSCAD 개발·검증 원문](https://docs.nlr.gov/docs/fy21osti/76808.pdf).

따라서 시간별 PF의 수렴, DC OPF의 N−1 선로 한계 만족, 총수급 일치만으로 주파수 최저점/RoCoF, transient 안정성, fault ride-through, protection 동작을 주장할 수 없다. 해당 목표가 실제 요구사항이 되고 장비 제어·고장 계측이 확보될 때 M6를 독립 과제로 추가한다.

## 9. 기획서에서 수정해야 할 계산 약속

| 기획서의 표현/전제 | 모델 관점의 조정 |
|---|---|
| 발전량 시계열이 있으면 전력 흐름 animation | topology/impedance/bus별 주입이 없으면 발전량 변화만 관측 재생. 선로 흐름은 계산 근거를 별도 표시 |
| R/X 없으면 전압·길이·선종으로 대표값 추정 | 선종/병렬회선/운전상태까지 확인하고 출처·범위를 저장. 대표값은 sensitivity 시나리오이지 실제 파라미터의 식별 결과가 아님 |
| nearest substation snapping으로 topology | 전압층·busbar·switch·교차 접속 검증을 진입 조건으로 추가 |
| 발전량/부하 예측 → 미래 위험 | 망 모델·가용출력·제약이 있어야 조건부 계통 위험. 시계열 예측 정확도와 망 모델 불확실성을 별도 검증 |
| 정상 운전 Generation = Demand + Export | Import, ESS 충방전, 손실 및 계량 경계를 포함한 수지로 대체 |
| MVP에 기본 PF 포함 | 데이터 확보 조건부 항목으로 조정. 지금은 M0, 설비자료 확보 시 M1, 이후 실제망 M2/M4 |

## 10. 실제 채택 문헌과 검증 기록

아래 9개 독립 자료만 기술 결론의 근거로 채택했다. 모든 URL은 Exa fetch로 제목/내용 일치를 확인했다. DOI는 원문/랜딩 페이지에서 실제 노출된 경우만 기록했으며, 확인하지 못한 journal DOI를 추측하지 않았다. NREL 보고서의 현재 fetch 주소가 `docs.nlr.gov`인 것은 검색에서 반환된 공식 호스트 그대로다.

| ID | 문헌 및 직접 링크 | 채택 내용 | 품질/한계 |
|---|---|---|---|
| S1 | Zimmerman, Murillo-Sánchez, Thomas, **MATPOWER: Steady-State Operations, Planning and Analysis Tools for Power Systems Research and Education**, [공식 원논문 PDF](https://matpower.org/docs/MATPOWER-paper.pdf), DOI [10.1109/TPWRS.2010.2051168](https://doi.org/10.1109/TPWRS.2010.2051168) | AC/DC PF 모델·DC 가정·slack·OPF | 개발자 원논문. PDF에 DOI 확인. 설명되는 옛 버전의 라이선스/성능 수치를 현 버전 주장에 사용하지 않음 |
| S2 | PyPSA, [Energy Balances](https://docs.pypsa.org/stable/user-guide/optimization/energy-balance/) | nodal balance·Link 방향·efficiency | 공식 구현 문서. 수학식/terminal 부호 확인 |
| S3 | PyPSA, [Storage](https://docs.pypsa.org/stable/user-guide/optimization/storage/) | MW/MWh·SOC consistency·효율·initial/cyclic | 공식 구현 문서. 이 문서의 SOC는 MWh이며 본 보고서 e와 같은 차원 |
| S4 | Park, Deka, Chertkov, **Exact Topology and Parameter Estimation in Distribution Grids with Minimal Observability**, [랜딩](https://arxiv.org/abs/1710.10727), [원문](https://arxiv.org/pdf/1710.10727), arXiv DOI [10.48550/arXiv.1710.10727](https://doi.org/10.48550/arXiv.1710.10727) | leaf 측정 기반 topology/impedance 복원과 Assumptions 1–3 | 저자 preprint. radial/선형화·비상관·숨은 노드 차수 조건이 있어 제주 집계에 직접 전용 불가 |
| S5 | Ramachandran 등, **Distribution System State Estimation in the Presence of High Solar Penetration**, [원문](https://arxiv.org/abs/1904.08036) | WLS·pseudo-measurement 분산·sensor coverage | PNNL 저자 원연구. 센서 및 network model이 존재하는 사례 |
| S6 | Farivar, Low, **Branch Flow Model: Relaxations and Convexification (Parts I, II)**, [랜딩](https://arxiv.org/abs/1204.4865), [원문](https://arxiv.org/pdf/1204.4865) | branch-flow/DistFlow·SOCP·조건부 exactness | 저자 원문. Baran–Wu의 배전 모델과 연결을 원문에서 확인. 정리 조건을 생략하지 않음 |
| S7 | Cavraro, Bernstein, Kekatos, Zhang, **Real-Time Identifiability of Power Distribution Network Topologies with Limited Monitoring**, [DOE OSTI 원문](https://www.osti.gov/servlets/purl/1544999) | meter 배치·known infrastructure와 topology identifiability | NREL 연구자 원문. 알려진 선로 r/x와 통계·시계열 측정이 필요 |
| S8 | Giraldez 등, **Simulation of Hawaiian Electric Companies Feeder Operations with Advanced Inverters and Analysis of Annual Photovoltaic Energy Curtailment**, NREL/TP-5D00-68681, revised Sept. 2017, [보고서](https://docs.nlr.gov/docs/fy17osti/68681.pdf) | QSTS·상세 feeder·전압지원과 연간 curtailment | NREL와 Hawaiian Electric 공동 실무 연구. O‘ahu feeder 결과를 제주 수치로 전용하지 않음 |
| S9 | Kenyon 등, **Validation of Maui PSCAD Model: Motivation, Methodology, and Lessons Learned: Preprint**, [공식 보고서 PDF](https://docs.nlr.gov/docs/fy21osti/76808.pdf) | PF/positive-sequence/EMT 차이와 field-event validation | NREL/대학/utility 원연구. 상세 모델·고속 측정이 필요함을 실제 절차로 제시 |

## 11. 검색 로그

Exa Search 스킬과 `references/searching.md`, `patterns-papers.md`, `source-quality.md`를 읽고 수행했다. `sources_reviewed`는 실제 독립 채택 문헌 수가 아니라 스킬이 정한 **모든 search 호출의 numResults 합계**다.

| 호출 | query | numResults |
|---|---|---:|
| Q1 | MATPOWER official manual AC power flow DC modelling assumptions optimal power flow | 8 |
| Q2 | PyPSA official optimization storage energy balance curtailment HVDC controllable link constraints | 8 |
| Q3 | category:research paper power distribution topology identification voltage measurements observability identifiability smart meter data | 8 |
| Q4 | category:research paper distribution system state estimation weighted least squares observability pseudomeasurements DistFlow Baran Wu | 8 |
| Q5 | NREL Hawaii high renewable integration quasi static time series electromagnetic transient inverter island grid modeling | 8 |

각 호출의 objective는 원논문·MATPOWER/PyPSA 공식 문서·NREL 보고서를 우선하고 tutorial/commercial roundup을 제외하며 가정·필수 측정·static/transient 구분과 검증된 URL/DOI를 추출하도록 설정했다. 5개 각도 × 8개 = **sources_reviewed: 40**. 재시도 없음. **실제 채택 독립 자료: 9개**. 동일 논문의 abstract/PDF는 한 자료로 합쳤고, MATPOWER manual 버전별 중복, Exa library의 2차 레코드, 원문 확인이 부족한 후보는 채택하지 않았다.

fetch는 S1–S9의 9개 URL 배치 후 S4/S6 PDF 2개를 추가 확인했다(11 URL fetch, 독립 자료 수는 9). search의 40을 fetch 수와 합산하지 않았다. S6의 알려진 arXiv 랜딩은 직접 fetch로 실제 제목·저자·내용을 확인한 뒤 채택했다. 이 문헌조사는 exhaustive systematic review가 아니라 이 기획서의 모델 진입 조건을 판단하는 범위 제한 조사다.
