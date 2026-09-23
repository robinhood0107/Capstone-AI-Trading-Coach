# 사용자별 KIS_MOCK 연결 해제와 복구 보존

full 제품의 `DELETE /api/v1/brokerage/mock/credential`은 현재 인증된 owner의 키만
대상으로 한다. V205는 owner advisory lock 아래 현재 account/revision을 다시 확인하고
상태를 DISCONNECTING으로 바꿔 새 주문·잔고 probe를 막는다.

계좌의 주문이 `SUBMITTED`, `PENDING_RECONCILIATION`, `ACCEPTED`,
`PARTIALLY_FILLED`, `CANCEL_REQUESTED` 중 하나이거나 자동운용 execution이
`PLANNED`, `SUBMITTING`, `PENDING_RECONCILIATION`이면 암호문을 보존하고 200과
`{"state":"DISCONNECTING"}`을 반환한다. 미대사 상태가 해소된 뒤 사용자가 해제를
다시 확인하면 204와 함께 봉투를 제거한다. 연결 해제 직전 이미 시작한 RPC는 끝날 수
있지만 연결을 끊은 뒤 새 요청은 상태 검사에서 거부된다.

V199의 키 교체 함수는 과거 migration을 수정하지 않는다. V205 trigger가 기존 함수의
누락된 `PENDING_RECONCILIATION` 상태를 보완해 암호문 교체·삭제를 거부한다. 사용자는
키 교체 뒤 저장·연결·인증을 다시 거쳐야 한다. audit에는 owner·opaque account·상태와
revision만 있고 비밀값은 없다.
