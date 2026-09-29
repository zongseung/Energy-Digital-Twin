# 제주 전력 디지털 트윈

현재 서버에는 기존 iSCSI/DB를 읽어 비동기 HTTP·WebSocket으로 전달하는 [Rust 데이터 브릿지](bridge/README.md)를 구현한다. 실제 앱 백엔드·Redis·시뮬레이션·사진 기반 3D 환경은 A6000 두 장이 있는 GPU 서버에서 작성·실행한다.

- [최종 설계](jeju_power_grid_digital_twin_design.md)
- [서버별 구현 계획과 진행 상태](jeju_power_grid_implementation_plan.md)
- [브릿지 실행·API·GPU 비동기 수신 방법](bridge/README.md)
- [iSCSI 제주 지리 데이터 수집·재시작 방법](bridge/README.md#제주-지리-데이터-수집)
