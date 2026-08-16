# S5.6B Model Release, Signal Batch, and Daily Refresh Lock

Issue: #135

## Status and authority

S5.6B connects the approved S5.6A reconstructed source/feature bundle to the existing
exact four-grid LightGBM trainer. Repository-local implementation and offline tests are
authorized. This record does not prove a real dataset, a passing real model, or an active
production pointer; those states require separately executed provider and qualification receipts.

```text
S5_6B_CODE=IMPLEMENTED_MERGE_CANDIDATE
PROVIDER_CALLS_DURING_IMPLEMENTATION=0
REAL_MODEL_AVAILABLE=FALSE_UNTIL_ACTUAL_QUALIFICATION_PASS
PRODUCTION_POINTER=0_UNTIL_MANUAL_CAS
AUTOMATIC_RETRAIN=0
AUTOMATIC_MODEL_ACTIVATION=0
RISK_DECISION_ORDER_WIRING=NO_GO
```

## Immutable release and batch

The production release contains exactly `release.json`, LightGBM `model.txt`, numeric
`calibrator.json`, `report.json`, `gain-importance.json`, `contribution-report.json`, and
`qualification.json`. It binds feature/source, training dataset, code HEAD/tree, dependency
lock, calendar and policy hashes. IDs derive from canonical content. Pickle, joblib,
arbitrary object deserialization and remote-code model formats are not accepted.
Before the untouched final test is opened, a one-way input/candidate reservation is
written. A passing qualification is then sealed as one bounded, content-addressed binary
envelope before release files are published. If final release publication fails, the
same seal resumes publication without fitting again or reopening the final test. A
reservation without a complete seal, including failure of the first durable seal write,
is fail-closed as `UNIDENTIFIABLE_OUTPUT`; it never reopens the final test or guesses
missing bytes. This is distinct from release-file publication failure after a seal exists.

The daily Signal bundle contains exactly `batch.json` and `signals.parquet`. It is sorted
by symbol and contains exactly 31 unique `AVAILABLE` rows for `top30-plus-132030-v1`.
Normal `HOLD` remains AVAILABLE. `modelScore`, raw margins/probabilities, identifying
contribution rows and `predictedReturn` are absent. One missing, duplicate, ABSTAIN,
non-finite or release-mismatched row rejects the whole batch.

## XKRX clock and daily refresh

All feature, label, daily packet, batch `asOf`, stale and activation clocks derive from
pinned `exchange-calendars 4.13.2` XKRX sessions. Calendar-date `+1 day` and fixed weekday
assumptions are forbidden. The regression anchor is:

```text
sessionDate=2026-08-14
2026-08-17=SUBSTITUTE_HOLIDAY
nextXkrxSession=2026-08-18
asOf=2026-08-18T08:10:00+09:00
asOfUtc=2026-08-17T23:10:00Z
```

One daily packet advances exactly one XKRX session. The normal author refuses a missed
multi-session jump. An operator may author a separate resume packet only for the exact
next session, one session at a time. A completed session uses four KRX daily/index GETs;
monthly rollover adds three identity/ETF GETs. The hard maximum remains KRX 7, KIS daily
31 plus token endpoint at most 1, and ECOS 2: at most 41 physical calls, retry 0 and cost
0. OAuth cache hits are not reported as physical token calls.

Every daily provider handoff writes an intent before the call and seals only the
allowlisted projection after success. If one query fails, the canonical failure-resume
packet binds the original daily packet, the complete journal digest and that exact query;
successful queries are read from content-addressed projections and are never called again.
The separately authorized resume may call the failed query once only when that retry plus
every still-required query remains inside the original provider-specific caps and the same
41-call total cap. It never increases the approved budget; a full 41-call monthly packet has
no retry slack. If all exact logical queries succeeded and only local inference, staging, or
publication failed, resolved retry receipts are allowed and `LOCAL_FINALIZATION` authorizes
zero provider calls. Exact DB publication replay returns the existing generation without
appending another publication receipt.

S5.6B provides manual daily refresh/inference only. S6.5 may schedule it once after 08:10
KST and may publish a complete latest batch through scheduler CAS. It cannot retrain,
activate, rollback or select another model automatically.

## Database capabilities

Forward-only Flyway V73 adds immutable universe/model/batch records, append-only model
transitions and batch publication receipts, and singleton active model/batch pointers.
LightGBM rows cannot use the older V72 direct row pointer. Writer capability stages a
batch; initial model+batch activation and rollback use one admin expected-current CAS;
daily publication uses scheduler CAS bound to the active model and current XKRX batch
clock. The database recomputes canonical release and member projection digests before the
first stage, rejects a second batch for the same model/session, and serializes even the
empty-pointer activation race with a permanent transaction advisory lock. A published
batch cannot be republished; only an exact manifest-bound recovery is a no-op replay.
Manual rollback requires the previous `ACCEPTED` release and a newly generated fresh
31-row batch for the current XKRX session. It never re-exposes that release's old batch.

`decision_app`, `decision_signal_writer`, `decision_signal_scheduler`, and
`decision_signal_admin` receive no direct table DML. FORCE RLS remains enabled and only
fixed-search-path, exact-session-user SECURITY DEFINER functions expose each role's
minimum capability. Drift appends `SUSPENDED/ARTIFACT_DRIFT` and the public LightGBM
component becomes ABSTAIN without exposing the old signal.

## Public and downstream boundary

The existing `GET /api/v2/signals/{symbol}` wire schema is unchanged. A fresh active
LightGBM release/batch may make only that component AVAILABLE; missing evidence,
membership mismatch, stale batch or drift remains typed ABSTAIN. HMM, LSTM and Rule are
not fabricated, so the composite may remain `REQUIRED_COMPONENT_UNAVAILABLE`.
Cross-market first joins at S6.6 and RiskDecision or order integration remains forbidden.
