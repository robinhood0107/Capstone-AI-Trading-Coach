# P1.V1 isolated provider read smoke

## KR

- `PROVIDER_READ_SMOKE` 구현 상태를 `IMPLEMENTED`로 전이한다.
- 실행 계약은 KRX 2, KIS 2, ECOS 2의 exact read-only operation을 순서대로 각각 physical 1회만 허용한다.
- KIS token은 cache hit이면 0회, cache miss이면 packet cap 안에서 최대 1회이며 data call과 분리해 계상한다.
- 첫 terminal 실패 뒤 나머지는 `NOT_RUN`이고, retry·retransmission·account·balance·order·product DB write는 0이다.
- packet claim은 provider client 생성 전에 owner-private immutable ledger에 한 번만 기록한다.
- report는 content-free evidence SHA와 호출 수만 보존한다. raw body, header, token, credential, URL, 실제 관측값은 보존하지 않는다.
- 이 계약은 S5.7 live collector, S6 계산, 모델, 주문 연쇄 또는 runtime activation을 증명하지 않는다.

## EN

- Transitions `PROVIDER_READ_SMOKE` to `IMPLEMENTED`.
- The runtime permits only the ordered exact read-only set of two KRX, two KIS, and two ECOS operations, each with one physical attempt.
- KIS token issuance is accounted separately: zero on cache hit and at most one on cache miss when authorized by the packet.
- The first terminal failure leaves every later gate `NOT_RUN`; retries, retransmission, account, balance, order, and product database writes remain zero.
- The packet is claimed exactly once in an owner-private immutable ledger before provider client construction.
- Reports retain only content-free evidence hashes and call counts, never raw bodies, headers, tokens, credentials, URLs, or actual observed values.
- This contract does not prove or activate the S5.7 live collector, S6 calculations, models, order chains, or any production runtime.

Refs #155
