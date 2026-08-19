# S5 recovery chaining across calendar correction generations

Issue: #135

Builds on `contracts/changes/20260819-s5-constitution-day-calendar-correction.md`. No prior
record is edited.

## What the live run exposed

After the second correction was locked, re-running recovery produced a corrected run holding
only 4,080 chunks and 4,082 consumed calls, while the run that had actually executed held
4,208 chunks and 4,211 consumed calls.

```text
run(previous generation)  physical=4211  succeeded=4208  superseded=2  failed=1
run(new recovery)         physical=4082  succeeded=4080  superseded=2
```

Recovery could chain only from a historical packet v1, so a second correction silently
discarded everything collected under the first corrected generation. Executing that run would
have consumed 361 further calls and reported 4,443 internally while the true provider total
reached 4,572 — 129 above the approved cap. The gap was found before any of those calls were
made, and none were made.

## Decision

### Correction generations are preserved and hash-addressable

```text
GENERATION_0  correctionSetSha256=82fbb91d…  sessions=[]              usage=READ_ONLY_RECOVERY_VALIDATION
GENERATION_1  correctionSetSha256=30530e6f…  sessions=[2026-06-03]    usage=READ_ONLY_RECOVERY_VALIDATION
CURRENT       correctionSetSha256=20e17fc6…  sessions=[2026-06-03,2026-07-17]
```

Each generation resolves to a deterministic calendar. A packet declares the generation it was
authored under, and that declaration is the only accepted way back to a superseded calendar.
An unrecognised digest is rejected. Generation 0 is the unmodified pinned base, which keeps
the existing historical packet v1 behaviour unchanged. Superseded generations are never
deleted, because deleting one would strand the ledger it validates.

### Production execution stays on the current generation

`validate_bootstrap_packet` accepts a superseded generation only when a caller explicitly
opens read-only recovery validation. Every production path leaves that closed, so a packet
from an older generation cannot be executed.

### Recovery chains from the latest consumed run

```text
CHAINABLE_FROM_SUPERSEDED_RECOVERY_PACKET=1
PRIOR_PACKET_MUST_NOT_USE_CURRENT_CORRECTIONS=1
PRIOR_JOURNAL_READ_UNDER_ITS_OWN_GENERATION=1
```

A prior packet may now be either a historical v1 or a calendar-recovery packet from a
superseded generation. A packet already on the current generation is refused, since a
generation cannot supersede itself and the cumulative ledger would break. The prior journal is
read under the generation it was sealed in, so its receipt clocks validate against the calendar
that produced them, and only the receipts whose next-session clock crossed a newly removed
session are rebound.

## Chained projection

```text
PRIOR_PACKET=482a10a91179af47794c6c6fc6e37dfe6154aa0322cc0fdd6e4e3aa0a6abeb9a
PRIOR_GENERATION=[2026-06-03]
CONSUMED_KRX_PHYSICAL_CALLS=4211
REUSABLE_SUCCESSFUL_CHUNKS=4208
SUPERSEDED_CONSUMED_CALLS=3
TEMPORAL_RECEIPTS_TO_REBIND=4
MISSING_REQUIRED_KRX_QUERIES=233
PROJECTED_KRX_PHYSICAL_CALLS=4444
KRX_SUPERSEDED_ALLOWANCE=3
APPROVED_KRX_MAX_GET=4444
KRX_SHORTFALL=0
RECOVERY_STATUS=READY_TO_SUPERSEDE
PROVIDER_CALLS_DURING_ASSESSMENT=0
```

Nothing already collected is discarded and nothing already collected is fetched again. The
allowance still equals the proven superseded count exactly, and the approved cap continues to
bound the true cumulative provider total rather than a per-generation subtotal.

## Preservation boundary

Owner-private roots, packets, receipts, adoption journals, lineage, and divergence blocks
remain in the content-addressed dual artifact vault and are never committed to Git or Git LFS.
Provider raw bodies, headers, and request URLs are not persisted by any path here.

Signal v1/v2, OpenAPI, V73, RiskDecision, order, cross-market and every other composite
component contract are unchanged.
