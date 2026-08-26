# P1 KIS Mock execution approval 분리

## 이유

기존 외부 실행 approval의 contract identifier가 다른 provider approval과 충돌했고, KIS 모의투자
packet의 lowerCamel 내부 단계가 서명된 operation과 동일하지 않았다. 이 상태에서는 provider client를
만들기 전에 approval 검증이 항상 거부됐다.

## 변경

- KIS 모의투자 외곽 계약을 `p1-kis-mock-execution-approval.v1`로 식별한다.
- 내부 v3 단계는 다음 서명 operation에 정확히 매핑한다.
  - `KIS_MOCK_PRICE_READ`
  - `KIS_MOCK_PRE_BALANCE`
  - `KIS_MOCK_BUYABLE`
  - `KIS_MOCK_SUBMIT_LIMIT_BUY`
  - `KIS_MOCK_CANCEL_FULL`
  - `KIS_MOCK_EXECUTION_READ`
  - `KIS_MOCK_POST_BALANCE`
  - `KIS_MOCK_OPEN_ORDER_RECONCILIATION`
- Ed25519 서명, 5분 TTL, exact scope와 PostgreSQL single-use claim은 그대로 유지한다.
- KIS Live origin과 Live 주문 TR ID는 추가하지 않는다.

## 영향

historical provider packet bytes는 수정하지 않는다. 이 변경 뒤 새로 작성되는 KIS 모의투자 v3
certification packet만 새 contract identifier와 operation을 사용한다. 거래시간·PR/CI·일반 보안검사
gate가 닫혀 있으면 physical call은 0이다.
