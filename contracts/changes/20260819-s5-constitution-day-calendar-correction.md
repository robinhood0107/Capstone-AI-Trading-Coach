# S5 second CTCA0903R calendar correction and single-session divergence evidence

Issue: #135

Builds on `contracts/changes/20260819-s5-superseded-allowance-and-calendar-authority.md`.
Neither that record nor the 2026-08-17 recovery ledger is edited.

## What the live run found

The corrected bootstrap resumed and sealed 128 further KRX projections, then stopped on
`stk_bydd_trd` for `2026-07-17`. The holiday authority added in the previous change was
then used on the uncollected 2026 candidate range, and it answered decisively.

```text
CANDIDATE_SESSIONS_QUERIED=26
CTCA0903R_CLOSED=2026-07-17
CTCA0903R_OPEN=25
KIS_HOLIDAY_PHYSICAL_CALLS=27
KRX_PHYSICAL_CALLS_DURING_CONFIRMATION=0
```

`2026-07-17` is Constitution Day, restored as a public holiday for 2026 and therefore
absent from pinned `exchange-calendars==4.13.2`. This is the same class as `2026-06-03`:
a session the pinned base exposes as open while the field authority reports it closed.

The read-only attestation reported the divergence before any further budget was spent:

```text
CALENDAR_AUTHORITY=CALENDAR_AUTHORITY_CONFLICT
UNEXPECTED_CLOSED=2026-07-17
CONFIRMED_CLOSED=2026-06-03
CONTRADICTED=0
```

## Decision

### The correction set gains the confirmed session

```text
S5_CALENDAR_POLICY=xkrx-4.13.2-kis-corrections-v1
CORRECTION_SESSIONS=2026-06-03,2026-07-17
CALENDAR_CORRECTION_SET_SHA256=20e17fc6bf8b21b2ad8acfc18e8f541535f300471e77f08464cfbfa70c6cdeb9
EVIDENCE_CLASS=CTCA0903R_CONFIRMED_CALENDAR_CORRECTION
```

Every correction now carries the evidence class of an actual confirmed observation rather
than a contract-only assertion, and the catalog requires ascending unique session dates.
Because the window is a trailing fixed-size session set, removing one session moves the
raw and eligible start boundaries one session earlier while the dimensions stay exact.

### Single-session failure is divergence evidence, not only an empty projection

The previous change classified an empty daily projection on a claimed session as
`CALENDAR_DIVERGENCE_SUSPECTED`. The provider answered this session with an error instead,
so the run reported a generic dataset failure and the cause took one further KRX call to
locate. The block now records two evidence classes.

```text
EMPTY_DAILY_PROJECTION          resumePacketAuthored=0  unresolvedBlockStopsResume=1
SINGLE_SESSION_QUERY_FAILURE    resumePacketAuthored=1  unresolvedBlockStopsResume=0
```

An empty projection is unambiguous, so it still blocks a later resume. A single-session
failure after healthy neighbours cannot be distinguished from a transient provider fault,
so it records the candidate as diagnostic evidence and leaves the contract-allowed resume
path intact. The operator confirms the candidate with the cheap, separately budgeted
holiday authority before spending another collection call.

Block bytes now depend only on the packet and the candidate set. Cumulative call
accounting stays solely in the append-only journal, so a legitimate resume that observes
the same candidate does not conflict with the sealed evidence.

### Calendar boundaries are no longer duplicated as stale literals

The corrected-calendar contract fixture pinned session boundaries that were already stale
against the live calendar. The generator runs in an environment without the calendar
package, so the fixture keeps literals, but a regression in the environment that has both
now requires those literals to equal the calendar-derived window. Feature bundle test
provenance derives its window instead of hard-coding it.

## Budget accounting

```text
KRX_PHYSICAL_CALLS_BEFORE=4211
SUCCESSFUL_LOGICAL_QUERIES=4208
FAILED_2026_07_17_CALL=1
SUPERSEDED_AFTER_CORRECTION=3
```

The failed call's query leaves the corrected logical set, so it is superseded consumed
rather than wasted, and the evidence-bound allowance grows from two to three. The allowance
still equals the proven superseded count exactly, stays within the approved bound of eight,
and does not enlarge the logical query set.

## Preservation boundary

Owner-private roots, packets, receipts, adoption journals, lineage, and divergence blocks
remain in the content-addressed dual artifact vault and are never committed to Git or Git
LFS. Provider raw bodies, headers, and request URLs are not persisted by any path here.

Signal v1/v2, OpenAPI, V73, RiskDecision, order, cross-market and every other composite
component contract are unchanged.
