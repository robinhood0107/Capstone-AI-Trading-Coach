# S5 supersede generalized to every provider that consumes calls

Issue: #135

Builds on `contracts/changes/20260819-s5-recovery-generation-chaining.md`. No prior record is
edited.

## What the live run exposed

KRX collection completed at exactly the approved bound: 4,441 logical queries, 4,444 physical
calls, 4,441 sealed chunks, zero failures. Collection then moved to KIS and stopped.

```text
KIS  oauth2/tokenP    SUCCEEDED             1
KIS  FHKST03010100    SUCCEEDED             6
KIS  FHKST03010100    FAILED                3
```

Two parser guards rejected real provider data. Both were measured against the actual response
and corrected — the adjustment rate's sign carries the adjustment direction, and `mod_yn`
reports whether the returned prices are adjusted rather than whether that date carried a
corporate action. But by then one logical query had spent both of its permitted physical
attempts, so the journal would never open it again. The packet was structurally unable to
finish even though the data was retrievable.

The existing supersede mechanism could not carry the work forward. It admitted KRX only:

```text
assess_bootstrap_calendar_recovery  →  "calendar recovery requires a KRX-only failed prefix"
build_recovery_journal_bytes        →  "only KRX calls may be calendar-superseded"
```

That restriction was sound for its original reason. When a calendar correction changes the
session set, only KRX queries change, and the superseded queries disappear from the corrected
generation entirely. A defect in local finalization is a different reason with the same need.

## Decision

### Supersede admits every provider that can consume approved calls

```text
SUPERSEDED_PROVIDERS=KRX,KIS
ADOPTED_PROVIDERS_CARRYING_CHUNKS=KRX,KIS
KIS_TOKEN_SUCCESS_ADOPTABLE=0
```

Successful chunks are adopted by content and query identity. Anything whose result is not
carried forward becomes `SUPERSEDED_CONSUMED`: failures, and successes whose value is not
preserved. The access token is the second case — the token itself is never persisted, so
adopting its receipt would leave the new run holding a spent token budget and unable to make a
single KIS call. It is superseded, and the new generation buys one more.

### The per-query retry budget is scoped to the current generation

```text
PHYSICAL_ATTEMPTS_PER_LOGICAL_QUERY=2
PER_QUERY_RETRY_BUDGET_SCOPE=CURRENT_GENERATION
SUPERSEDED_CONSUMED_CALLS_COUNT_TOWARD_CUMULATIVE_BUDGET=1
```

This is the invariant that made the carry-forward useful rather than decorative. A superseded
attempt is a ledger entry from a previous generation; the cumulative budget counts it, so no
approved call is forgotten, but it does not consume this generation's retry eligibility. Had it
done so, an adopted query could never be called again and the adoption would accomplish
nothing. A success stays uncallable across every generation, because its result is already
held and a re-call would only burn approved calls.

### Allowance is evidence-bound per provider

```text
APPROVED_KRX_MAX_GET=4441   krxSupersededAllowance=3
APPROVED_KIS_MAX_GET=2970   kisSupersededAllowance=3
KIS_TOKEN_MAX=1             kisTokenSupersededAllowance=1
APPROVED_TOTAL_MAX_PHYSICAL_CALLS=7436  →  7443 with the three allowances
MAX_SUPERSEDED_ALLOWANCE_PER_PROVIDER=8
```

Each allowance equals the number of proven superseded consumed calls for that provider and
nothing more. Logical query counts do not move. `author_bootstrap_budget` takes no allowance at
all, so a fresh approved root is structurally unable to inherit spent calls.

The KIS allowance fields appear in packet bytes only when non-zero. A packet that never
superseded a KIS call is byte-identical to what it was before these fields existed, which is
what lets already-sealed generations stay verifiable. Their absence is the declaration that the
provider superseded nothing, and validation rejects a packet whose declared allowance and
sealed bytes disagree in either direction.

### KIS logical queries are identified, not enumerated

```text
KIS_LOGICAL_QUERY_SET_DERIVED_FROM_COLLECTED_KRX_EVIDENCE=1
```

KRX queries are derived from the packet, so recovery reconstructs the corrected set and
compares it. KIS queries depend on the horizon union derived from collected KRX data and on
paging cursors that only the data determines, so no static enumeration exists. Recovery
therefore validates each adopted KIS chunk against its own sealed receipt — source, operation,
request digest, snapshot digest, row count, byte bound, and Parquet schema — and carries the
query identity forward unchanged. Substituting another response is still impossible, because
the identity is bound in both the journal intent and the chunk receipt.

### The chain head is derived from consumed evidence

```text
PRIOR_PACKET_IS_CHAIN_HEAD=1
CHAIN_HEAD_DERIVED_FROM_CONSUMED_QUERY_MULTISET=1
SUPERSEDE_MUST_CHANGE_PACKET_IDENTITY=1
```

The previous rule identified the prior generation by requiring a correction set other than the
current one. That rule only worked because every supersede so far had changed the calendar. A
supersede for an approved-limit change leaves both packets declaring the same generation, and
the rule then found no candidate at all.

The head is now the consumed run that no other recovery has superseded, and when abandoned
sibling runs make that ambiguous, the one whose consumed-query multiset contains every
sibling's. Multiset, not attempt identity: ordinals are renumbered in each generation, so
`(ordinal, query)` pairs cannot express containment across a chain. Chaining from a
less-consumed sibling would drop already-spent calls out of the cumulative total, which is
exactly what the bound exists to prevent. Recovery also refuses to author a corrected packet
identical to its prior, so the chain can never point at itself.

## Ledger at this change

```text
KRX      logical=4441  physical=4444  chunks=4441  failures=0
KIS      token=1  daily=6 succeeded  3 superseded  4 consumed carried forward
ADOPTED  4441 KRX chunks + 6 KIS chunks, provider calls during adoption = 0
```

## Enforcement

Regressions cover: a superseded attempt not consuming the new generation's retry budget while
still closing at two attempts within it; an adopted success remaining uncallable across
generations; a token success being unadoptable and superseded instead; fresh lineage rejecting
every allowance; KIS allowance fields absent from packet bytes when zero and present when not;
allowance above the approved bound; and a recovery with no superseding effect being refused.

Contract keys are locked in `s5-production-materialization-lock.v1.json` and
`s5-bootstrap-calendar-recovery-lock.v1.json`.
