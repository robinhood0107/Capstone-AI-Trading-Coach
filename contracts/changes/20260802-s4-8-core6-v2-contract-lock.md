# S4.8 Core 6 v2 entitlement·probe contract lock

상태: `CONTRACT_ONLY`

관련 Issue: #91

## KR: 변경 이유와 범위

기존 S4.8A v1은 KIS opaque 후보 18개와 GDELT aggregate 한 행을 정확히 19개의
`CANDIDATE_DISABLED` fixture로 고정한다. 이를 확장하거나 V23 runtime을 활성화하지 않고,
S5 진입 전에 검증할 Core 6 provider의 향후 entitlement·single-use probe·sanitized receipt
경계를 별도 v2 계약으로 잠근다.

Core 6은 `KIS`, `OPENDART`, `SEC_EDGAR`, `KRX`, `KOFIA`, `ECOS`의 정확히 여섯 source family다.
KIS/SEC EDGAR/KRX/KOFIA만 향후 direct read probe 후보이고, OpenDART와 ECOS는 이미 승인된
sanitized projection만 재사용한다. KOFIA는 활용 승인과 credential evidence가 없으므로
`BLOCKED_NO_CREDENTIAL_OR_APPROVAL`로 남긴다.

- 모든 checked-in entitlement는 `CANDIDATE_DISABLED` 또는 `BLOCKED`다.
- provider/machine/account/order physical call, raw store, embedding, external LLM, RiskEngine,
  Signal, Decision, order authority는 모두 `0` 또는 `NONE`이다.
- checked-in approval fixture는 `fixtureOnly=true`, `TEMPLATE`, logical/physical cap `0`, retry `0`,
  artifact `0`이며 실행할 수 없다.
- generated check는 public Core 6 schema/example namespace의 선언되지 않은 추가 파일을 거부한다.
  따라서 tracked public fixture tree에는 execution capacity를 가진 `APPROVED` packet을 둘 수 없고,
  실제 packet은 public fixture directory 밖 local-only regular `0600` artifact여야 한다.
- 실행 capacity는 `APPROVED`인 non-fixture packet 하나에만 exact `1/1`로 존재한다. `CONSUMED`와
  `EXPIRED`는 execution flag와 두 cap을 모두 `0`으로 revoke하며, KOFIA·OpenDART·ECOS는 이 v2
  entitlement 아래 executable packet이 될 수 없다.
- receipt fixture는 `NOT_EXECUTED`, empty step ledger, physical call `0`이다.
- `SUCCESS` receipt는 eligible direct source의 한 번의 `DATA_REQUEST`, logical/physical `1/1`,
  `HTTP_2XX`, non-null projection hash를 모두 요구한다. `NOT_EXECUTED`가 아닌 projection-only
  source receipt와 zero-call success는 fail-closed한다.
- `FAILED` receipt도 attempted HTTP/transport 결과를 주장하면 logical/physical `1/1`과 하나의
  `FAIL_CLOSED` step physical call `1`을 함께 기록해야 한다. zero-call failure receipt는
  fail-closed한다.
- raw body/header/query/sensitive material field 또는 arbitrary query injection은 closed schema가
  거부한다. 실제 packet은 local-only regular 0600 artifact로 별도 runner가 만들어야 하며 이
  contract fixture를 실행 입력으로 사용할 수 없다.

이 변경은 provider adapter, migration, V23 table/function, REST/OpenAPI endpoint, snapshot writer,
RiskEngine wiring, S5 feature, Return/Experience workspace를 변경하지 않는다. 기존 GDELT offline
aggregate producer는 Decision Platform 소유로 변경 없이 유지하며, 이 변경은 GDELT executor 또는
outbound implementation을 추가하지 않는다. Naver runtime을 재활성화하지 않으며 Finnhub/Twelve
Data/Massive는 별도 Optional 3 계약/adapter 단계 전까지 포함하지 않는다.

## EN: Rationale and scope

The existing S4.8A v1 contract locks exactly 19 `CANDIDATE_DISABLED` fixtures: 18 opaque KIS
candidates and one GDELT aggregate. This change does not extend that contract or activate the V23
runtime. Instead, it locks separate v2 contracts for the future entitlement, single-use probe, and
sanitized receipt boundary of the six Core providers required before S5.

The Core 6 are exactly `KIS`, `OPENDART`, `SEC_EDGAR`, `KRX`, `KOFIA`, and `ECOS`.
Only KIS, SEC EDGAR, KRX, and KOFIA are future direct-read probe candidates. OpenDART and ECOS
reuse an already authorized sanitized projection. KOFIA remains
`BLOCKED_NO_CREDENTIAL_OR_APPROVAL` until its access approval and credential evidence exist.

- Every checked-in entitlement stays `CANDIDATE_DISABLED` or `BLOCKED`.
- Provider/machine/account/order physical calls, raw storage, embeddings, external LLM handling,
  and RiskEngine, Signal, Decision, and order authority remain `0` or `NONE`.
- The checked-in approval fixture is `fixtureOnly=true`, `TEMPLATE`, with zero logical/physical caps,
  retry `0`, artifact `0`, and cannot execute.
- The generated check rejects undeclared extra files in the public Core 6 schema/example namespace. The
  tracked public fixture tree therefore cannot contain an `APPROVED` packet with execution capacity; a
  real packet must be a local-only regular `0600` artifact outside the public fixture directory.
- Execution capacity exists only on one non-fixture `APPROVED` packet with exact `1/1` caps. `CONSUMED`
  and `EXPIRED` revoke both caps and the execution flag; KOFIA, OpenDART, and ECOS cannot become
  executable packets under this v2 entitlement.
- The receipt fixture is `NOT_EXECUTED`, has an empty step ledger, and reports zero physical calls.
- A `SUCCESS` receipt requires one `DATA_REQUEST` from an eligible direct source, logical/physical
  `1/1`, `HTTP_2XX`, and a non-null projection hash. A projection-only provider receipt or zero-call
  success fails closed.
- A `FAILED` receipt that claims an attempted HTTP/transport result must also record logical/physical
  `1/1` and one `FAIL_CLOSED` step with one physical call. A zero-call failure receipt fails closed.
- Closed schemas reject raw body/header/query/sensitive-material fields and arbitrary query injection.
  A real packet must be authored by a later local-only 0600 runner and must never use this fixture as
  executable input.

This change adds no provider adapter, migration, V23 table/function, REST/OpenAPI endpoint, snapshot
writer, RiskEngine wiring, S5 feature, or Return/Experience workspace change. The existing GDELT
offline aggregate producer remains Decision Platform-owned and unchanged; this change adds no GDELT
executor or outbound implementation. Naver remains retired. Finnhub, Twelve Data, and Massive stay
outside this Core 6 contract until a separate Optional 3 contract/adapter stage.

## 새 계약 / New contracts

1. `market_source_entitlement.v2`
2. `cross_market_provider_probe_approval.v1`
3. `cross_market_provider_probe_receipt.v1`

`market_source_entitlement.v2` stores only opaque origin/endpoint-set identities, evidence digests,
rights, retention caps, and authority boundaries. It has no provider URL, credential, request query,
symbol, account, provider payload, or raw response field. The approval binds a future exact head,
CI/security evidence digests, entitlement/request-plan identities, nonce, and TTL no longer than 60
minutes. The receipt binds the approval/head/request identities and contains only sanitized call
counts, stable outcome class, and an ordered fail-closed step ledger.

## 불변식 / Invariants

```text
S4_8_CORE6_V2=CONTRACT_ONLY
S4_8_CORE6_PROVIDER_PHYSICAL_CALLS=0
S4_8_CORE6_ACCOUNT_ORDER_CALLS=0
S4_8_CORE6_RAW_PROVIDER_PERSISTENCE=0
S4_8_CORE6_RISK_SIGNAL_ORDER_AUTHORITY=0
S4_8_CORE6_KOFIA=BLOCKED_NO_CREDENTIAL_OR_APPROVAL
S4_8_CORE6_TRACKED_PUBLIC_FIXTURE_EXECUTABLE_PACKET_ARTIFACTS=0
S4_8_V1_AND_V23_BYTES_UNCHANGED=1
GDELT_EXISTING_OFFLINE_PRODUCER_UNCHANGED=1
GDELT_EXECUTOR_ADDED=0
GDELT_OUTBOUND_IMPLEMENTATION=0
NAVER_RUNTIME_REACTIVATION=0
```

## 재현 / Reproduction

```bash
uv run --frozen python contracts/generate_s4_8a_cross_market_contracts.py --check
uv run --frozen python contracts/generate_s4_8_core6_v2_contracts.py --check
uv run --frozen python -m unittest contracts.tests.test_s4_8_core6_contracts -v
uv run --frozen python -m unittest discover -s contracts/tests -v
uv run --frozen python contracts/validate.py
```
