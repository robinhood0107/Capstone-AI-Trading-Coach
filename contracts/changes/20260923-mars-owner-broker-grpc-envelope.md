# 사용자별 KIS_MOCK gRPC 암호문 envelope

기존 `brokerage.proto`의 네 요청에 `BoundMockCredentialEnvelope`를 추가한다.
Spring은 검증된 `BrokerageActor.userId`와 opaque `accountId`로 V202 내부 reader를
호출한 뒤, 암호문·nonce·KEK 버전·revision·상태만 loopback gRPC 요청에 복사한다.
계좌번호나 App Key·Secret의 원문은 proto에 넣지 않는다. 요청 상태는 제출 시
`CERTIFIED`, 취소 시 `CERTIFIED|DISCONNECTING`, 잔고 조회 시
`STORED|CONNECTED|CERTIFIED|DISCONNECTING`, 주문가능 조회 시
`CONNECTED|CERTIFIED`로 제한한다.

Python 기존 servicer는 FULL에서 envelope가 없으면 요청을 거부하고,
`MARS_BROKERAGE_KEK_DIRECTORY`의 별도 KEK로 owner/account AAD를 검증한다.
요청별 KIS_MOCK client는 복호화한 App Key·Secret·계좌번호 한 쌍만 받는다.
FULL에서는 전역 `KIS_MOCK_BOUND_ACCOUNT_ID`를 설정하면 기동을 거부하며,
전역 KIS App Key·Secret·계좌번호로 fallback하지 않는다. 기존 LOCAL 개인 실행의
고정 계좌 경로는 독립 제품 모드로 유지한다. 연결 확인·장중 인증·자동운용 대사가
아직 없으므로 full 공개 주문 경로는 계속 닫는다.
