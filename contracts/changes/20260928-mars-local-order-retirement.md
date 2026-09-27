# Owner-confirmed local retirement of an unreconciled FULL order

## Reason

The `demo-user` FULL ledger contained one legacy `005930 BUY 1` KIS_MOCK row with no
provider order reference, fill evidence, or connected-account binding. Its unresolved
status blocked account-history readiness. The owner confirmed that the local row should
be retired. That confirmation is not evidence of a KIS-side cancellation or zero fill.

## Contract

- Preserve the order row, its original `MOCK_ORDER_SUBMITTED` and
  `MOCK_ORDER_CANCEL_REQUESTED` events, and the V218 quarantine record.
- Close only its local fill projection as `CANCELLED` with zero filled, zero leaves, and
  one unfilled-terminated share. Record the owner-confirmed local resolution separately
  with `broker_cancel_confirmed=false`.
- The owner order-read API returns `LOCAL_RETIRED` for this recorded local resolution.
  This is a read projection, not a KIS order outcome. Normal provider-confirmed
  `CANCELLED` keeps its existing meaning.
- ADMIN reconciliation returns `409 ORDER_RECONCILIATION_NOT_APPLICABLE`; it does not
  update the order, fill, or balance ledgers. The status and error text say that KIS
  cancel/fill results remain unknown.
- This change retires only the identified demo-user legacy row. It does not arm
  automation, submit or cancel a provider order, change other users' orders, or prove
  service readiness. demo-user remains `DISARMED`.

## Validation boundary

An isolated PostgreSQL clone restored from the protected pre-v1.0.0 V216 dump applied
V217, V218, then V219. The target row reached the local terminal projection, the
original events remained unchanged, and V218 owner-history integrity reported zero
unlinked positions, orders, and runs. This validation did not call KIS. The published
release gate keeps `serviceReady=false` until user-specific natural KIS_MOCK order,
fill/balance reconciliation, and target capacity evidence are complete.

See [the API reference](../../docs/API_명세서.md) and
[the FULL state and database verification matrix](../../docs/FULL_상태전이_DB_검증표.md).
