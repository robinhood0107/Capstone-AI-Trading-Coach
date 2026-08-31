# P1 Automation V3 runtime — evidence first, owner exit policy, after-hours replay

Date: 2026-08-31

## Decision

The additive Automation V3 contract locked earlier is now backed by V111 through
V113 runtime state. V1/V2 routes, schemas, generated clients, and historical
checkpoints remain present. New V3 runs use pre-selection candidate screening;
legacy checkpoints keep the historical `NEWS_CHECKING` path.

## Semantic change

- The old order was “select one candidate, then ask for a veto.” V3 seals the
  complete Return Engine BUY candidate set, removes deterministic ineligible
  instruments, screens every remaining candidate once, and only then selects.
- AI never creates a symbol, quantity, policy, risk decision, or order. The
  deterministic engine remains the sole sizing authority.
- `aiJudgementEnabled=false` invokes neither news screening nor judging.
- An enabled run snapshots the secret-free owner AI settings JSON and its
  canonical hash at arm time. Later settings edits affect only a later run.
- Unsupported scores and vetoes are neutralized to `0.5 / false`; prompt
  injection abstains only the affected candidate; a batch/provider failure means
  zero new buys for that run.

## Storage and privilege boundary

- V111 adds nullable legacy-compatible policy/position columns, Wilder ATR state,
  monotonic peak/trailing stop, nullable unlimited expiry, and V3 definer calls.
- V112 adds `NEWS_SCREENING`, bounded candidate screening/evidence ledgers, V3
  call accounting, durable pre-call SCREEN/JUDGE reservations, and a read-only
  isolated replay reader.
- V113 adds owner AI enablement/thinking fields and immutable arm/run settings
  snapshots.
- `decision_automation_runtime` still has no base-table market-data SELECT.
- Provider raw responses, source URLs, tokens, account data, and order data are
  not stored in the evidence tables.
- `p1-after-hours-observed-anchors.v1` binds the eight preserved 2026-08-31
  KIS Mock/exit/AI-rerank receipts by SHA-256; replay output exposes only their
  category/count/set hash.

## External activity

This change authorizes no physical call. Live grounding, KIS read-only bootstrap,
KIS Mock orders, and real-session soak remain separate gates.

```text
KIS_LIVE_ORDER_CALLS=0
GDELT_OUTBOUND_CALLS=0
AUTOMATIC_INTERNAL_PAPER_FALLBACK=0
AUTOMATIC_ARM_OR_POINTER_ACTIVATION=0
CODEX_SECURITY_DEEP_SCAN=NOT_RUN_USER_SCOPED_OUT
```
