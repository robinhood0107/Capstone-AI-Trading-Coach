# 개인 중지·수익률 예측 API 추가

기존 76개 행은 역사적 문서로 보존한다. 현재 root는 78개 operation이며 아래 두 행을 추가한다.
이전 표의 전역 `GET/POST /api/v1/risk/kill-switch`는 현재 관리자 전용이다.
일반 사용자 화면과 Team A acceptance v5는 개인 API를 사용한다. acceptance는 45개 operation을 유지한다.

| 번호 | Method | Path | 분류 | 용도 |
|---:|---|---|---|---|
| 77 | GET | `/api/v2/risk/kill-switch` | `Team A 필수` | 인증된 본인의 중지·전역 중지·최종 주문 차단 상태와 변경 시각 조회 |
| 78 | POST | `/api/v2/risk/kill-switch` | `Team A 필수` | 본인의 주문 중지와 해제. 해제는 종료된 실행과 판단을 복구하지 않음 |

기존 Signal v3는 Ridge 1·5·20거래일 예측과 출처를 추가한다.
1일 결합은 고정 50:50이며 비교 검증 전 상태를 표시한다.
