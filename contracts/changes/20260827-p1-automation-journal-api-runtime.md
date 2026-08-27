# P1 Automation/Journal API와 root OpenAPI exact-56 전환

## KR

Owner-First Phase A의 마지막 public API 경계를 V89와 Spring runtime으로 구현한다.

- Automation 4개와 Journal 4개 route의 method/path/operationId를 additive 계약과 동일하게 구현한다.
- root OpenAPI는 exact 48의 path/component 의미를 보존하고 exact 8개 operation만 더해 56개로 전환한다.
- `verify_p1_automation_journal_openapi_transition.py`는 8개 route와 18개 additive component를 제거한
  canonical projection이 전환 전 exact-48 SHA-256과 같은지 검증한다.
- V89는 journals forward migration, Automation control/gate/run/position/event, hash-only idempotency,
  append-only event, owner RLS/ACL과 account/release/certification gate를 잠근다.
- write body는 bounded strict JSON이고 raw idempotency key, provider/account/order 원문을 저장하거나
  로그하지 않는다. missing/cross-owner/link mismatch는 같은 404, stale CAS와 key conflict는 409다.
- KIS_MOCK arm은 certification, clean release binding, REAL_TEAM_B pointer, Kill Switch, complete account
  baseline과 unexplained drift를 server-side transaction에서 확인한다. INTERNAL_PAPER는 explicit 선택만
  허용하고 KIS 실패 fallback은 없다.
- disarm은 신규 주문만 막고 pending reconciliation, position, event, Journal과 volume을 삭제하지 않는다.

이 변경은 provider, account, balance, order, Vertex, GDELT 또는 KIS Live physical call을 수행하지 않는다.
실제 Team A UI, REAL_TEAM_B artifact, KIS Mock certification, soak와 Release authority는 계속 별도 gate다.

## EN

This change implements the final Owner-First Phase A public API boundary in V89 and Spring.

- The four Automation and four Journal method/path/operation IDs match the locked additive contract.
- Root OpenAPI moves from exact 48 to exact 56 by adding only those eight operations. Removing the exact
  additive paths and components reconstructs the canonical pre-transition SHA-256.
- V89 adds the forward Journal shape, Automation control and append-only runtime tables, hash-only replay
  records, FORCE RLS/ACL, and server-owned activation checks.
- Strict bounded JSON rejects duplicate, unknown, deep, oversized, and non-canonical input. Raw idempotency
  keys and raw provider/account/order payloads are never persisted or logged.
- KIS_MOCK arm validates certification, release and REAL_TEAM_B binding, Kill Switch, account baseline, and
  unexplained drift in the server transaction. INTERNAL_PAPER remains an explicit choice, never a fallback.
- Disarm stops new orders while preserving outstanding reconciliation and durable state.

No provider, account, balance, order, Vertex, GDELT, or KIS Live physical call is made by this change. Team A
UI, real Team B artifacts, physical certification, soak, and release remain separate gates.
