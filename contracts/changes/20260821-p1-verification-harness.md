# P1.V0 verification harness contract change

## KR

Issue #153에서 S0~S5의 현재 상태를 provider 호출 없이 검증하는 내부 계약을 추가했다.

- 추가: `p1-verification-catalog.v1`, `p1-verification-packet.v1`, `p1-verification-report.v1`
- 상태 분리: implementation, execution, aggregate outcome
- authority: provider smoke packet만 TTL 최대 60분, data physical cap 6, optional KIS token cap 1,
  retransmission/account/balance/order/product DB write 0
- P1.V0 runtime: `S0_S5_CURRENT` provider 0만 구현; `PROVIDER_READ_SMOKE`는 미구현
- S5.7 교정: normal/month-boundary `38/41`은 replay operation 수이고 physical provider call은 0
- API 영향: Public REST/OpenAPI/Signal v2 변경 0

V76은 daily packet predecessor를 accepted DB head와 preflight/trigger 양쪽에서 검증한다. base table
SELECT 권한은 writer에 추가하지 않고 bounded security-definer 함수만 허용한다.

## EN

Issue #153 adds internal contracts for provider-free verification of the current S0-S5 state boundaries.

- Added `p1-verification-catalog.v1`, `p1-verification-packet.v1`, and
  `p1-verification-report.v1`.
- Separates implementation state, execution state, and aggregate outcome.
- A future provider-smoke packet is limited to a 60-minute TTL, six data attempts, an optional single KIS
  token attempt, and zero retransmission, account, balance, order, or product-database writes.
- P1.V0 implements only the provider-free `S0_S5_CURRENT` profile. `PROVIDER_READ_SMOKE` remains
  unimplemented.
- Corrects S5.7 `38/41` to offline replay operation counts with zero physical provider calls.
- Changes no public REST, OpenAPI, or Signal v2 payload.

V76 verifies the daily predecessor against the accepted database head during both preflight and INSERT. The
writer receives only a bounded security-definer capability and no base-table SELECT grant.
