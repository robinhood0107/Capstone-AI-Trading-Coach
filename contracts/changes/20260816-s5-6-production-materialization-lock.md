# S5.6 Production Materialization Lock

Issue: #135

## Status and scope

This change authorizes S5.6A and S5.6B after the fixture-first S5 implementation. It
does not authorize RiskDecision or order wiring, account or balance reads, automatic
model replacement, or S6.6 feedback into S5 training.

Historical bootstrap data is classified as
`HISTORICAL_REPLAY_RECONSTRUCTED / RECONSTRUCTED_FIXED_LAG`. It is never described as
strict provider-vintage PIT. A production candidate is eligible only after the normal
walk-forward calibration gates and both fixed sensitivity gates pass.

```text
historicalMode=HISTORICAL_REPLAY_RECONSTRUCTED
temporalQuality=RECONSTRUCTED_FIXED_LAG
futureCollectionMode=AS_COLLECTED
strictProviderPITClaim=false
productionEligible=DUAL_SENSITIVITY_PASS_REQUIRED
```

## Temporal evidence

Production source evidence uses the closed `TemporalReceipt v2` contract. Provider
availability and revision fields are present only when supplied by the provider. A
content digest is a snapshot digest, never a provider revision. KIS and ECOS bootstrap
rows use a project fixed lag at 08:10 KST on the next completed XKRX session. KRX rows
use the documented provider-as-of schedule without claiming revision history.

Feature evidence for session `t` must be effective by the next XKRX session at 08:10
KST. A label using `t+6` becomes eligible only after the next XKRX session at 08:10.
Equal snapshots for one logical key collapse; unequal snapshots without a comparable
provider revision fail as `SOURCE_SNAPSHOT_CONFLICT`.

## One-shot acquisition authority

```text
KRX_SERVICES=stk_bydd_trd,ksq_bydd_trd,kospi_dd_trd,kosdaq_dd_trd,stk_isu_base_info,ksq_isu_base_info,etf_bydd_trd
KRX_MAX_PHYSICAL_GET=4441
KIS_TR_ID=FHKST03010100
KIS_ADJUSTED_PRICE=FID_ORG_ADJ_PRC:0
KIS_MAX_TOKEN_CALLS=1
KIS_MAX_PHYSICAL_GET=1980
ECOS_SERIES=722Y001/0101000/D,731Y001/0000001/D
ECOS_MAX_PHYSICAL_GET=24
TOTAL_MAX_PHYSICAL_CALLS=6446
RETRY=0
COST_MAX=0
ACCOUNT_BALANCE_ORDER_CALLS=0
```

The execution order is KRX, universe finalization, KIS, then ECOS. A mandatory failure
stops all remaining calls in that run. Failed calls consume the cumulative budget.
Validated content-addressed chunks are not called again; a bounded resume packet may
attempt only the failed chunk while the original cumulative cap remains. KIS retention
shortfall produces `KIS_HISTORY_UNAVAILABLE`; KRX OHLC is not an automatic replacement.

Only closed projections may be persisted beneath a server-configured approved root.
Directories are mode `0700`, files are mode `0600`, and manifests are published last.
The source manifest is bounded at 16 MiB because it must bind up to 6,446 individual
physical-call receipts; KRX/KIS/ECOS decoded row and byte caps are rechecked from both
Parquet footer metadata and actual batches. Each handoff writes a fsynced intent before
the call and a terminal receipt afterward. A completed query is never called again.
An unresolved failure may be retried once only through a canonical resume packet bound
to the original bootstrap packet, failed query digest, consumed count and remaining cap;
an intent without a terminal receipt is ambiguous and cannot be retried automatically.
Credentials, authorization headers, account identifiers, and raw provider responses are
never persisted, logged, or committed.

## Dataset and sensitivity gates

Historical monthly membership may contain 1 through 180 permanent identities. The
current inference policy `top30-plus-132030-v1` requires exactly 30 eligible stocks plus
ETF `132030`. Preferred shares, REITs, ETFs and ETNs use exact KRX categories. When KRX
has no SPAC field, the official name is Unicode-NFC normalized, whitespace is removed,
and only `스팩` or `기업인수목적` is treated as a SPAC marker. Unresolved candidates are
excluded. Stocks require a 12-character standard code; the fixed ETF identity is
`XKRX:ETF:132030`.

KIS projections preserve `flng_cls_code`, `prtt_rate`, `mod_yn`, and
`revl_issu_reas`. Unknown or contradictory adjustment evidence fails closed. In
event-free rows, KIS/KRX close-return and open-label-return absolute differences above
`0.0005` may affect at most `0.1%` of their respective denominators. The fixed selected
model and calibrator must still pass the normal ECE, Brier and log-loss gates on the
event-free evaluation block.

The macro sensitivity table delays both the policy-effective base-rate and USD/KRW time
by one additional XKRX session. Its row intersection must retain at least 98% of the
primary rows. With the fixed model and calibrator, class disagreement is at most 10%,
ECE and log-loss degradation are each at most 0.02, and Brier is at most 1.10 times the
primary Brier. These gates are evaluated on evaluation folds before candidate selection;
the untouched final test cannot change policy, features, or candidate selection.

## Versioning and downstream boundary

S5.0 Signal contracts, OpenAPI, generated v1 fixtures, feature bundle v1, and Flyway V72
remain byte-stable. S5.6 adds `s5-pit-source-bundle-v1`, `s5-feature-bundle-v2`, and
`s5-production-materialization-lock.v1`. Feature bundle v1 remains fixture-only for
production training. Cross-market, news, RAG, LLM and HMM remain outside S5 inputs and
hashes; their first permitted join is unchanged at S6.6.

S5.6B will add immutable model releases and exact 31-row signal batches. Production
activation is a manual expected-current CAS over a complete model release and current
batch. Any missing, stale, drifting or unidentifiable evidence yields ABSTAIN and no
production pointer.
