# S4.8 Core 6 + Optional 3 sanitized runtime projection

상태: `IMPLEMENTED_DRAFT / PROVIDER_OUTBOUND=0`

## KR: 변경 이유와 범위

PR #92의 Core 6 v2 entitlement/probe/receipt contract와 Optional 3 경계를 소비해, Decision Platform에
exact nine-lane typed local runtime을 추가한다. 이 runtime은 provider adapter가 아니라 현재 가능한
상태 또는 이미 승인된 projection hash만 append-only로 materialize한다.

- Core 6은 `KIS`, `OPENDART`, `SEC_EDGAR`, `KRX`, `KOFIA`, `ECOS`이고 Optional 3은
  `FINNHUB_OPTIONAL3`, `TWELVE_DATA`, `MASSIVE`다. 다른 source family와 GDELT lane은 없다.
- OpenDART/ECOS만 existing authorized sanitized projection hash를 재사용할 수 있으며 S4.8 own fan-out은
  없다. KOFIA는 `BLOCKED_NO_CREDENTIAL_OR_APPROVAL`, Optional 3은
  `BLOCKED_NO_CREDENTIAL_OR_ENTITLEMENT`, direct-read candidates are `ABSTAIN/APPROVAL_PACKET_REQUIRED`다.
- Flyway V50은 `decision_market_writer`의 one-function append와 `decision_app`의 fixed-source latest read만
  허용한다. direct table privileges, raw response/header/query/credential/account data, retries, provider
  physical calls, Risk/Signal/order authority는 모두 없다.
- `s48-runtime materialize`는 provider-free nine-lane receipt만 생성하며 `stage`는 explicit offline target과
  exact writer role을 요구한다. 이것은 entitlement, live execution, snapshot materialization, RiskEngine
  wiring, public endpoint 또는 S5 feature activation이 아니다.

## EN: Rationale and scope

This consumes the Core 6 v2 and Optional 3 boundaries to add an exact nine-lane typed local runtime in the
Decision Platform. It materializes only current typed state or already-authorized projection hashes; it is not a
provider adapter. V50 exposes one writer append function and a fixed-source application reader, with no direct
table access, raw request/response material, credentials, retries, physical provider calls, or decision authority.

## 불변식 / Invariants

```text
S4_8_RUNTIME_NINE_LANES=IMPLEMENTED_DRAFT
S4_8_PROVIDER_ADAPTERS_ACTIVE=0
S4_8_PROVIDER_PHYSICAL_CALLS=0
S4_8_RETRY_COUNT=0
S4_8_RAW_PROVIDER_DATA_STORED=0
S4_8_GDELT_ADAPTER_OR_OUTBOUND=0
S4_8_RISK_SIGNAL_ORDER_AUTHORITY=0
S6_6_EVENT_STUDY=NOT_IMPLEMENTED
S6_7_SNAPSHOT_MATERIALIZATION=NOT_IMPLEMENTED
```

## 재현 / Reproduction

```bash
cd workspaces/decision-platform/python-services
uv run --frozen pytest -q tests/cross_market/test_s48_runtime.py \
  tests/cross_market/test_s48_runtime_repository.py tests/cross_market/test_s48_runtime_cli.py
uv run --frozen s48-runtime materialize

cd ../spring-api
./gradlew ktlintCheck test --tests com.capstone.decision.S48RuntimeMigrationContractTest \
  --tests com.capstone.decision.RagV2ImmutableBundleMigrationIntegrationTest
```
