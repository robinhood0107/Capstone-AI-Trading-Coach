# S5 bootstrap authoritative-calendar recovery lock

Issue: #135

## Decision

S5 production clocks use pinned `exchange-calendars==4.13.2` XKRX as the deterministic
base and the existing S1.6 field authority order for exceptional open/closed status:
valid KIS `CTCA0903R.opnd_yn`, then a healthy non-conflicted prior canonical row, then
the pinned base. The first hash-bound correction set closes `2026-06-03`, which the
pinned base incorrectly exposes as a session.

This recovery did not call the KIS holiday endpoint. The date is a contract-locked
calendar correction under the already approved S1.6 authority; it is not represented as
a newly collected provider observation or provider vintage.

Historical bootstrap packet v1 remains byte-stable and is readable only by the recovery
validator. Production execution accepts current packet v2. A recovered v2 packet carries
`lineageMode=CALENDAR_RECOVERY` and a recovery binding SHA-256; missing receipt, adoption
journal, or lineage yields `PACKET_OR_ROOT_INVALID` before any provider client is created.
For a fresh approved root, packet authoring publishes exactly one immutable
`fresh-bootstrap-authority.v1.json` absent-to-SHA CAS pointer while holding
`.bootstrap-root.lock`. Authoring and execution reuse that exact packet; sibling fresh
packets or runs cannot reopen a second provider budget.

Validated successful projection chunks are copied into the corrected run by content
digest. Their retrieval time and payload digest remain unchanged; only the four receipts
whose next-session clock crossed the removed session are rebound to the corrected policy.
All non-adopted historical attempts remain in the new ledger as consumed calls and cannot
be retried as current queries. Deleting a block sidecar cannot reset the cumulative cap.

## Content-free execution receipt

```text
S5_CALENDAR_POLICY=xkrx-4.13.2-kis-corrections-v1
CORRECTION_SESSION=2026-06-03:CLOSED
CALENDAR_CORRECTION_SET_SHA256=30530e6f4ff06ac3ab71a748c910c87cd9233c70382f348e00c387684cdde169
PRIOR_PACKET_SHA256=365a656f88d1f83a403dbd04b329a70fbf8e325b09fc1fb130ef98edd99857e5
PRIOR_PROGRESS_SHA256=2027f684115f1aeb024ee7b8e26a1eebaf95c02c0fcb0ebc7517acb95e5f6fe0
RECOVERY_BINDING_SHA256=a7fe2efc615d3ac621f25237a5c5d7c7eb76283c61c1f5e2250ac09b202f1fd3
CORRECTED_PACKET_SHA256=73bcec055416670cdb4a91c3d64b4d0ae119fa5e7338c320c673e4342e40905f
RECOVERY_RECEIPT_SHA256=7282ddec14a7b25dacd33165b3875cb765db8cfea414273f6e109445553cc805
ADOPTED_PROGRESS_SHA256=ee33575a23827df04001507e8908e075427d8a76ac7a577b80332eb724f33e95
CONSUMED_KRX_PHYSICAL_CALLS=4082
REUSABLE_SUCCESSFUL_CHUNKS=4080
SUPERSEDED_CONSUMED_CALLS=2
TEMPORAL_RECEIPTS_TO_REBIND=4
MISSING_REQUIRED_KRX_QUERIES=361
PROJECTED_KRX_PHYSICAL_CALLS=4443
APPROVED_KRX_MAX_GET=4441
KRX_SHORTFALL=2
PROVIDER_CALLS_DURING_RECOVERY_AND_ADOPTION=0
RECOVERY_STATUS=CAPACITY_EXHAUSTED
```

The original execution stopped after the required KRX request for `2026-06-03` failed,
and its single contract-authorized resume failed on the same request. No KIS token, KIS
daily, KIS holiday, ECOS, account, balance, or order call was made. Completing the
corrected KRX logical set would require cumulative physical call 4,443, two above the
approved cap 4,441. Therefore the corrected packet is durably blocked before client
construction and no threshold, calendar, or call accounting is relaxed.

```text
REAL_DATASET=DATASET_UNAVAILABLE
REAL_MODEL_AVAILABLE=FALSE
ACTIVE_MODEL_RELEASE=0
ACTIVE_SIGNAL_BATCH=0
PRODUCTION_POINTER=0
LIGHTGBM_COMPONENT=ABSTAIN
KIS_TOKEN_CALLS=0
KIS_DAILY_CALLS=0
KIS_HOLIDAY_CALLS=0
ECOS_CALLS=0
ACCOUNT_BALANCE_ORDER_CALLS=0
RISK_DECISION_ORDER_WIRING=NO_GO
S5_FAILED_RUN_BACKUP=RESTORE_VERIFIED
S5_VAULT_ARCHIVE_SHA256=105620728b22450b0703f0fdfef8df2d45a3c1902238bccf7251f0bcd006a6d5
S5_VAULT_RESTORE_RECEIPT_SHA256=e9defe9b8eb50de6ab6a9140230563e0500d0a99a29994f781327387864314ef
```

## Preservation boundary

The original and corrected owner-private source roots, packets, recovery receipt,
adoption journal, and lineage are preserved in the S5 content-addressed dual artifact
vault. The 273,361,625-byte archive contains 8,170 owner-private payload files; extraction,
inventory parity, closed receipt/chunk validation, and provider-free executor replay were
verified before the two vault copies were accepted. They are never committed to Git or
Git LFS. A future contract may increase the physical cap, but it must resume from the
adopted 4,080 chunks and the two consumed-call receipts; it may not delete the lineage and
recollect the historical prefix.

Signal v1/v2, OpenAPI, V73, RiskDecision, order, cross-market, HMM, LSTM, and Rule
contracts are unchanged by this recovery.
