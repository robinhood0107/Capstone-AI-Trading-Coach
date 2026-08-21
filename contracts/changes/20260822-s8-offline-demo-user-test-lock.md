# S8 offline demo and user-test contract lock

## Scope

- Add a deterministic, idempotent `capstone-s8-demo` seed contract and local runner.
- Require the operator to select exact `INTERNAL_PAPER`; no automatic mode transition exists.
- Keep provider, live account, live order, external RAG, and performance-claim authority at zero.
- Add a closed user-test response contract using only opaque identifiers, boolean/enum values, bounded scores,
  and bounded tags. Free text and direct identifiers are not accepted.

## Cross-market retirement

The educational task exposes only `RETIRED_NOT_APPLICABLE`, no current runtime, and no order authority.
It does not restore the retired endpoint, scheduler, reader, materializer, catalog rule, decision hash, or
`WARN_ONLY` overlay.

## Human gate

The checked-in kit is preparation only. It contains no participant result and cannot be used to claim IRB
approval or exemption. Actual recruitment and collection remain blocked until the institution issues the
required determination.
