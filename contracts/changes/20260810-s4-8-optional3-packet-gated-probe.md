# S4.8 Optional 3 packet-gated local probe

상태: `IMPLEMENTED_DRAFT / DEFAULT_PROVIDER_OUTBOUND=0`

## KR: 변경 이유와 범위

기존 `s4-8-optional3-*-v1`은 contract-only zero-call template으로 byte-stable하게 보존한다.
이 addendum은 `s4-8-optional3-probe-approval-v2`와
`s4-8-optional3-probe-receipt-v2`를 **additive**로 추가해, Decision Platform local operator가
exact current HEAD/tree, CI/security digest, short-expiry canonical packet에 결속된 경우만
Optional 3 provider 한 endpoint에 한 번 probe할 수 있게 한다.

- 허용 source family와 operation은 정확히 다음 넷이다.
  - `FINNHUB_OPTIONAL3`: `FINNHUB_RECOMMENDATION`, `FINNHUB_EARNINGS`
  - `TWELVE_DATA`: `TWELVE_DATA_TIME_SERIES`
  - `MASSIVE`: `MASSIVE_PREVIOUS_DAY_AGGREGATE`
- packet은 operation/provider family, fixed hostname/endpoint set, symbol/date plan digest,
  `logicalCallCap=1`, `physicalCallCap=1`, `retryCount=0`, `trackedRawArtifactCount=0`, operator,
  expiry, nonce, exact CI/security/tree proof를 함께 bind한다.
- executor는 local 0700 control root의 canonical 0600 regular packet만 읽고 O_EXCL claim을 request보다
  먼저 남긴다. 같은 packet의 재사용, redirect, compression, DNS rebinding, non-global peer,
  response size/shape drift는 fail-closed한다.
- API key는 provider-standard process environment variable에서만 transient하게 읽고 argv, packet,
  receipt, log, DB에 넣지 않는다. raw body/header/query/credential은 저장하지 않으며 receipt에는
  content-free parser success hash와 physical count만 남긴다.
- default process는 outbound 0이다. packet 또는 fresh local execution evidence가 없거나 drift하면
  socket을 열지 않는다. Core 6, Finnhub foreign-news, SEC/Fed, GDELT, Decision/Signal/Risk/order/hash와
  S5 feature 권한은 이 변경으로 열리지 않는다.

이것은 live data projection persistence나 S4.8 RiskEngine wiring이 아니다. provider response는
bounded transient parse로 only protocol shape를 확인하고, V50의 provider-zero typed runtime을
재해석하거나 수정하지 않는다.

## EN: Rationale and scope

The existing `s4-8-optional3-*-v1` artifacts remain byte-stable zero-call templates. This additive v2
contract permits one local, packet-gated probe only when canonical short-lived approval, exact clean
HEAD/tree, and CI/security evidence all match. The exact allowed operations are Finnhub recommendation
and earnings, Twelve Data time series, and Massive previous-day aggregate.

Each packet binds the fixed provider family/operation, endpoint set, symbol/date request-plan digest,
one logical and physical call, zero retries and raw artifacts, operator, expiry, nonce, and current
execution proof. The executor accepts only a 0700-root/0600-regular local control file, consumes it via
O_EXCL before the request, and fails closed on reuse, redirects, compression, DNS rebinding, non-global
peers, or response drift. Credentials are transient process-environment values only; receipts retain no
body, headers, query, credentials, or DB payload.

No packet means zero outbound calls. This does not activate Core 6, foreign-news, SEC/Fed, GDELT,
Decision/Signal/Risk/order/hash authority, S5 features, persistent live projections, or RiskEngine wiring.
