# S4.8 Core 6 local probe runtime addendum

상태: `IMPLEMENTED_DRAFT`

관련 Issue: #91

## KR: 범위와 불변식

이 addendum은 2026-08-02 Core 6 v2 contract lock의 public schema, generated fixture,
v1/V23 bytes를 바꾸지 않는다. Decision Platform Python service에 local-only packet-gated
executor와 content-free receipt bridge를 추가한다.

- 대상 direct operation은 정확히 다섯 개다: KIS current-price 1개, SEC EDGAR
  submissions/companyfacts 2개, KRX KOSPI/KOSDAQ daily 2개.
- canonical 0600 short-expiry packet, exact clean HEAD/tree, CI/security digest, fixed operation,
  `logicalCallCap=1`, `physicalCallCap=1`, `retryCount=0`가 모두 일치하기 전에는 socket을 열지 않는다.
- KIS는 cache-only access token preflight를 사용한다. cached token miss는 OAuth token endpoint를
  열지 않고 `NOT_EXECUTED`로 끝난다.
- SEC EDGAR는 fixed `data.sec.gov` origin, required contact User-Agent, DNS pinning/TLS peer check,
  redirect/encoding/MIME/size failure-closed boundary를 사용한다. KRX는 existing fixed-origin private
  client의 single-call/no-retry quota boundary를 재사용한다.
- claim은 O_EXCL single-use이고 receipt에는 raw body/header/query, credential, account data를 저장하지
  않는다. runtime은 selected success receipt를 read-only로 load하며 KIS 1, SEC 2, KRX 2 complete set이
  모두 있을 때만 해당 lane을 `AVAILABLE`로 materialize한다. materialization의 provider call은 0이다.
- OpenDART/ECOS는 authorized projection-only이고 KOFIA는
  `BLOCKED_NO_CREDENTIAL_OR_APPROVAL`를 유지한다. GDELT outbound, Naver runtime, Return/Experience
  workspace, Decision/Signal/Risk/order authority는 추가하지 않는다.

이 tracked change는 provider physical call을 수행하지 않는다. 실제 call은 최종 clean tree의 exact
approval packet과 현재 external credential/entitlement evidence가 있는 local control root에서만 가능하다.

## EN: Scope and invariants

This addendum preserves the 2026-08-02 Core 6 v2 public schemas, generated fixtures, and v1/V23
bytes. It adds a local-only packet-gated executor and content-free receipt bridge to the Decision
Platform Python service.

- The direct operation set is exactly five: one KIS current-price, two SEC EDGAR operations, and two
  KRX daily-market operations.
- No socket opens until a canonical 0600 short-expiry packet, exact clean HEAD/tree, CI/security
  evidence, fixed operation, one logical/physical call cap, and zero retry all agree.
- KIS requires a cached access token and never opens OAuth token issuance on a cache miss.
- Successful receipts retain no raw provider material. The runtime only reads selected receipts and
  requires the complete 1/2/2 KIS/SEC/KRX operation sets before materializing a lane as `AVAILABLE`.
  Runtime materialization itself makes zero provider calls.
- OpenDART/ECOS remain projection-only; KOFIA remains blocked. GDELT outbound, Naver runtime,
  Return/Experience placeholder workspaces, and Decision/Signal/Risk/order authority remain unchanged.

## 재현 / Reproduction

```bash
uv run --project workspaces/decision-platform/python-services --frozen \
  pytest -q tests/cross_market/test_core6_probe.py \
  tests/cross_market/test_core6_probe_backends.py \
  tests/cross_market/test_core6_probe_cli.py \
  tests/cross_market/test_s48_runtime.py \
  tests/cross_market/test_s48_runtime_cli.py
uv run --frozen python contracts/generate_s4_8_core6_v2_contracts.py --check
```
