# KIS_MOCK owner-bound 암호문 reader

V202는 저장된 암호문을 `owner_user_id + KIS_MOCK + opaque account_id`가 모두 일치할
때만 반환하는 내부 함수를 추가한다. `decision_app`의 검증된 actor capability
`READ_MOCK_CREDENTIAL_ENVELOPE`가 없거나 GUC만 위조한 호출은 거부한다. 계좌
교체 뒤 이전 opaque ID는 읽을 수 없다. 다른 사용자의 account ID를 요청해도
암호문을 얻지 못한다.

Spring의 `MockCredentialSettingsService.resolveEnvelope`은 현재 행의 상태와
revision·봉인된 byte 배열만 반환한다. 호출자는 `AutoCloseable` envelope를 닫아
byte 배열을 지운다. 복호화는 기존 `BrokerageCredentialCrypto.open`을 사용한다.
공개 GET은 계속 끝 4자리 요약만 반환한다. STORED 행을 읽을 수 있다는 사실은
연결·인증·주문 가능 상태를 뜻하지 않는다. 다음 단계에서 기존 gRPC/KIS_MOCK
transport에 결속하고, 주문에서는 CERTIFIED 상태를 별도로 강제한다.
