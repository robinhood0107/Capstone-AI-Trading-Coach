# Full 자동운용의 owner-bound KIS credential transport

`mars-full` 자동운용 runtime은 Spring loopback bridge에 claim의 `ownerUserId`와
`accountId`를 보내며, bridge는 활성 사용자와 현재 owner-bound encrypted envelope를
확인한다. wire에는 ciphertext만 포함한다. Python은 `CERTIFIED` 상태, 같은 account ID,
owner/account AAD, 전용 0700/0600 brokerage KEK를 확인한 뒤에만 해당 owner의 KIS_MOCK
REST token scope와 read-only quote, execution/balance client를 만든다. 매매 키와 계좌번호는
환경 파일에 fallback하지 않는다.

LOCAL은 현재 private operator-key 경로를 계속 사용한다. FULL Compose는 owner gRPC에 필요한
별도 Redis order-reference encryption key만 허용하고 App Key·App Secret·계좌번호 환경 변수를
거부한다. 사용자별 scheduler claim·동시 실행·인증서 발급/체결 격리의 완료 선언은 별도다.
