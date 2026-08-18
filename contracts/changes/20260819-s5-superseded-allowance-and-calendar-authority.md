# S5 superseded allowance and CTCA0903R calendar authority

Issue: #135

Supersedes nothing. This change builds on the immutable ledger in
`contracts/changes/20260817-s5-bootstrap-calendar-recovery-lock.md`, which recorded that
completing the corrected KRX logical set required cumulative physical call 4,443 against
approved cap 4,441. That record is not edited.

## Decision

### Evidence-bound superseded allowance

A bootstrap budget may exceed the approved KRX physical cap only by the number of
physical attempts that a calendar recovery has proven to be consumed and superseded.

```text
FRESH_KRX_MAX_GET=4441            FRESH_TOTAL_MAX_PHYSICAL_CALLS=6446
FRESH_SUPERSEDED_ALLOWANCE=0      MAX_SUPERSEDED_ALLOWANCE=8
ALLOWANCE_DERIVATION=PROVEN_SUPERSEDED_CONSUMED_CALLS
ALLOWANCE_LINEAGE=CALENDAR_RECOVERY
```

The fresh authoring path does not accept an allowance argument, so a new approved root
cannot open a larger budget. Only `lineageMode=CALENDAR_RECOVERY` carries one, and the
value must equal `supersededConsumedCalls` proven by the recovery receipt. The allowance
is bound in four surfaces that are each recomputed during validation: packet bytes, the
recovery binding preimage, the recovery receipt, and the adoption journal. Execution
authority additionally requires the allowance to equal the count of `SUPERSEDED_CONSUMED`
entries in the adopted ledger before any provider client is constructed.

The allowance never increases the logical query set. The corrected logical count must
equal the fresh derived dimension count, and the corrected packet limit must equal that
count plus the allowance. Because the durable journal permits at most two physical
attempts per logical query, the number of superseded calls is structurally bounded and
cannot be inflated by repeated failures.

### Calendar divergence is a distinct terminal result

An empty but structurally valid KRX daily projection for a session the calendar claims
open is no longer reported as a generic dataset failure. It terminates the run as
`CALENDAR_DIVERGENCE_SUSPECTED`, publishes a content-free candidate sidecar, and does not
author a resume packet, because resuming would spend approved calls on the same query.
An unresolved block stops a later resume before the provider client is created.

```text
DIVERGENCE_BLOCK_FILE=calendar-divergence-candidates.json
DIVERGENCE_BLOCK_VERSION=s5-calendar-divergence-block-v1
DIVERGENCE_DETECTION=EMPTY_DAILY_PROJECTION_ON_CLAIMED_SESSION
DIVERGENCE_RESUME_PACKET_AUTHORED=0
PROVIDER_CALLS_DURING_BLOCK=0
```

### CTCA0903R authority is now produced, not only declared

The S1.6 field authority order was already contract-locked, and the adapter, strict
parser, merger, and canonical repository already existed, but no production path
connected the KIS client to them. This change adds that connection so a divergence
candidate can be confirmed by the actual provider rather than by a constant alone.

```text
HOLIDAY_TRANSACTION_ID=CTCA0903R
HOLIDAY_SOURCE_ID=kis-holiday-ctca0903r
HOLIDAY_MODE=LIVE_ONLY
HOLIDAY_MAX_PHYSICAL_CALLS=32
HOLIDAY_SCOPE=DIVERGENCE_CANDIDATE_SESSIONS_ONLY
HOLIDAY_SEPARATE_FROM_BOOTSTRAP_BUDGET=1
HOLIDAY_REQUIRED_ROLE=decision_collector
```

The reconciliation job runs before packet authoring, writes only `trading_sessions`, and
is not part of the bootstrap source bundle, so the approved KRX, KIS daily, KIS token, and
ECOS caps are unchanged. A collector-role privilege preflight runs before the provider
socket so a missing grant cannot waste a holiday call. The job is bounded to candidate
sessions; it does not sweep the window, and it does not relax the conservative
per-endpoint call policy.

The S5 correction set stays a static constant so packet bytes, the fresh-authority CAS
pointer, and the recovery lineage remain deterministic. A separate read-only attestation
compares the constant against stored tier-1 observations:

```text
API_CONFIRMED                   every in-window correction observed closed, no other closed session
CALENDAR_AUTHORITY_CONFLICT     an observation contradicts the constant
CALENDAR_AUTHORITY_UNVERIFIED   an in-window correction has no observation
```

Absence of evidence is reported as unverified, never as confirmation.

### Quota backend credential preflight

Bootstrap execution attests quota backend authentication before constructing any provider
client, so a credential mismatch cannot be discovered after one-shot calls are consumed.

```text
CREDENTIAL_PREFLIGHT_RESULT=CREDENTIALS_UNAVAILABLE
CREDENTIAL_PREFLIGHT_REASON=QUOTA_BACKEND_AUTH
PROVIDER_CALLS_ON_CREDENTIAL_FAILURE=0
```

Credential values are never printed or persisted.

## Recovery projection under this contract

```text
PRIOR_PACKET_SHA256=365a656f88d1f83a403dbd04b329a70fbf8e325b09fc1fb130ef98edd99857e5
CONSUMED_KRX_PHYSICAL_CALLS=4082
REUSABLE_SUCCESSFUL_CHUNKS=4080
SUPERSEDED_CONSUMED_CALLS=2
TEMPORAL_RECEIPTS_TO_REBIND=4
MISSING_REQUIRED_KRX_QUERIES=361
PROJECTED_KRX_PHYSICAL_CALLS=4443
KRX_SUPERSEDED_ALLOWANCE=2
APPROVED_KRX_MAX_GET=4443
TOTAL_MAX_PHYSICAL_CALLS=6448
KRX_SHORTFALL=0
RECOVERY_STATUS=READY_TO_SUPERSEDE
PROVIDER_CALLS_DURING_RECOVERY_AND_ADOPTION=0
```

Because the allowance is bound into packet bytes, the corrected packet SHA-256 recorded in
the prior change is superseded by a newly authored corrected packet at the same dataset
cutoff. The recovery cutoff is derived from the prior packet window, not from wall clock,
so the adopted chunk set does not shift.

Completion must resume from the adopted 4,080 chunks and the two consumed-call receipts.
Deleting the lineage and recollecting the historical prefix remains prohibited, as does
deleting a block sidecar to reset cumulative accounting.

## Preservation boundary

Owner-private source roots, packets, recovery receipts, adoption journals, lineage, and
divergence blocks remain in the S5 content-addressed dual artifact vault and are never
committed to Git or Git LFS. Provider raw bodies, headers, and request URLs are not
persisted by any path added here.

Signal v1/v2, OpenAPI, V73, RiskDecision, order, cross-market, HMM, LSTM, and Rule
contracts are unchanged.
