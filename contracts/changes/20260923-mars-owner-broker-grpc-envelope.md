# 사용자별 KIS_MOCK gRPC 암호문 envelope

기존 `brokerage.proto`의 네 요청에 `BoundMockCredentialEnvelope`를 추가한다.
Spring은 검증된 `BrokerageActor.userId`와 opaque `accountId`로 V202 내부 reader를
호출한 뒤, 암호문·nonce·KEK 버전·revision·상태만 loopback gRPC 요청에 복사한다.
계좌번호나 App Key·Secret의 원문은 proto에 넣지 않는다. 요청 상태는 제출 시
`CERTIFIED`, 취소 시 `CERTIFIED|DISCONNECTING`, 잔고 조회 시
`STORED|CONNECTED|CERTIFIED|DISCONNECTING`, 주문가능 조회 시
`CONNECTED|CERTIFIED`로 제한한다.

이 PR은 Java/Kotlin의 전송과 생성물을 맞춘다. Python 기존 servicer가 암호문을
검증·복호화해 해당 계좌의 KIS_MOCK client만 생성하는 다음 변경 전까지 full 공개
주문 경로는 열지 않는다. 기존 LOCAL 개인 실행의 고정 계좌 경로는 전환 중 유지하며,
full 제품에서 전역 KIS 환경변수로 fallback하지 않도록 다음 변경에서 닫는다.
