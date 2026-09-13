# Automation operational recovery

The operator requested same-day recovery of the missed KIS Mock session and prevention
of recurring execution failures. This change does not add public HTTP operations or
change the normal engine transition whitelist, order budget, other risk rules, or Live authority.

- V138 restricts the v2/v3 runtime position readers to the current control account.
  Other accounts' positions remain stored and cannot enter the current account's checkpoint.
- The resident runtime rechecks wall time at most every 30 seconds while waiting. It
  uses actual current time after waking, including laptop suspend and clock changes.
- Before consuming a session, the runtime checks TLS connectivity to the fixed KIS
  Mock origin without credentials or an HTTP request. Failure leaves the schedule
  available and retries connectivity in 60 seconds while the market window is open.
- Tick errors log only state, exception class and attempt count, without exception
  text, credentials, query values or provider responses.
- V139 adds an explicitly invoked local operator function and `mock resume` command.
  One receipt per existing run allows recovery only during the same day's 09:30–15:20
  window, from `SKIPPED_DATA_UNAVAILABLE`, with no selected symbol, decision,
  reservation, logical submit or physical submit. Control, account, policy, certification,
  model, principle, Kill Switch, balance and unresolved-work gates remain required.
  The original run ID, history, processed ticks and monotonically increasing checkpoint
  version remain. The incident's two failed quote attempts are conservatively reserved
  in existing call counters; no call budget is enlarged. `SESSION_RESUMED` is append-only.
  A second resume, an order-bearing run, another day, or changed policy is rejected.

No provider success or order completion is implied by this contract or the tests.

The owner's subsequent instruction extends pre-order data recovery to exactly two
fallbacks per session, using the existing append-only recovery receipts. V142 enforces
the shared automatic/operator cap transactionally and schedules delays of two and five
minutes. Normal completion, a risk block, or any order-bearing reservation is not retried.

V140 preserves checkpoint call counts when optional v3 usage is absent. V141 aligns
checkpoint writers with effective exit-policy snapshots and the current account.
V145 readiness compares the expected cash/quantity lineage, rather than valuation
changes in the historical baseline.

The owner explicitly removed the per-order KRW ceiling for BUY and SELL. V143 disables
that rule in all three presets; existing owner principles are updated through the
normal versioned API. Other risk rules and capital/quantity/order budgets remain.
V144 permits one explicit same-day re-evaluation of an amount-only BLOCK whose intent
has never been submitted. It requires the new principle to differ only in that rule,
preserves the original decision, audits the prior checkpoint and reservation in the
existing audit log, and keeps the same intent and daily order reservation. A distinct
decision epoch prevents replaying the old BLOCK under the previous idempotency key.

V146/V147 project verified automation fills into the existing order/fill/reconciliation
records in the same transaction as completion and account lineage. Completion first
requires a verified post-fill balance; failure leaves reconciliation pending. Order
recovery binds the stored provider receipt hash and does not submit another order.
V148/V149 scope realized results to the current mock account and verified exit, including
explicitly adopted holdings without a bot-originated entry order.

The KIS cash projection uses D+2 settlement cash instead of booked deposit cash. A
post-completion observation refresh reuses the verified balance without resetting the
risk baseline. Dashboard balance reads use the complete owner-scoped stored observation
and its timestamp; they no longer call the unenriched broker balance gateway or trip
the shared order circuit breaker on each poll.

The dashboard renders fillQuantity/fillPriceKrw from the actual fill contract. Five-second
background refreshes retain the last successful view on transient errors, deduplicate
in-flight requests, and clear data when identity or authorization changes. Runtime
status and next execution are derived from the v3 API, and holding ownership is reconciled
with the current account's open bot positions. Missing numeric values render as unavailable,
not NaN or fabricated zero. Order checklist headings no longer assert a passing state
before evaluation; realized PnL explicitly identifies estimated transaction costs.

Strong LLM grounding support receipts retain only edges to sources actually returned by
the run. An explicit generation-unavailable response permits one frontend retry, while
ambiguous request failures are not resubmitted. Generation, persistence, history retrieval,
and rendering are verified separately.
