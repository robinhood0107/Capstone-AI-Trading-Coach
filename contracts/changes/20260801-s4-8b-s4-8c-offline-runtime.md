# S4.8B/C offline 교차시장 evidence runtime

상태: `IMPLEMENTED_OFFLINE_MERGE_CANDIDATE`
관련 Issue: #76

## KR: 변경 이유와 범위

S4.8A에서 먼저 잠근 일곱 계약을 소비해 provider 호출 없는 fixture/EOD 생산, append-only
저장, 결정적 scorer와 설명 전용 projection을 구현한다. 이 변경은 snapshot materialization,
RiskEngine 연결, cross-market REST endpoint, live provider, 계좌 또는 주문 권한을 활성화하지
않는다.

- Python fixture producer는 disabled entitlement 19개(KIS opaque 후보 18개와 별도 GDELT
  aggregate 1개), instrument별 current+완료 세션 252개, analyst revision 3개, cause evidence
  2개만 만든다.
- `CrossMarketScorer`는 완료 세션 exact 252개의 adverse empirical percentile과 component별
  동일 가중 중앙값만 계산한다. missing, incomplete, future, NaN/Infinity와 coverage 부족은
  fake zero가 아니라 unavailable이다.
- Flyway V23은 일곱 append-only table과 네 latest view를 만들고 entitlement/exposure/
  observation/analyst/cause 다섯 append function만 `decision_market_writer`에 허용한다. snapshot
  writer는 의도적으로 없으며 snapshot/link는 FORCE RLS owner read 경계만 갖는다.
- Spring `CrossMarketSnapshotReadPort`는 actor-scoped latest view에서만 읽고 missing,
  duplicate, future, source-unavailable을 typed result로 반환한다. controller와 RiskEngine wiring은
  없다.
- S4.8C는 사실·보도·해석·가설, contradiction/retraction/supersede를 보존한다. GDELT
  aggregate는 confirmed fact나 reported cause가 될 수 없고 analyst BUY 의견 수준의 방향
  가중치는 exact 0이다.
- PDF 기본 mode는 `MANUAL_LINK_ONLY`다. 별도 승인된 `LICENSED_EPHEMERAL_LOCAL`도
  owned/no-follow PDF를 bounded 검증한 뒤 원본을 먼저 삭제하고 로컬 parser만 실행한다.
  raw text·quote 저장과 외부 LLM 전송은 0이다.

## EN: Rationale and scope

Consume the seven S4.8A contracts through an offline fixture/EOD producer, append-only storage,
deterministic scoring, and explanation-only projections. This change does not activate snapshot
materialization, RiskEngine wiring, a cross-market REST endpoint, live providers, accounts, or
orders.

The producer emits only disabled sanitized fixtures. The scorer uses exactly 252 completed
sessions and never substitutes missing or invalid evidence with zero. V23 grants the narrow writer
only five SECURITY DEFINER append functions and intentionally exposes no snapshot writer. The
Spring port is an owner-scoped bounded reader, while S4.8C preserves contradictory and retracted
evidence without granting decision authority. PDF processing remains metadata-only by default and
bounded local-ephemeral under a separate approval.

## 저장·권한 불변식 / Persistence and authority invariants

```text
S4_8A_CONTRACT=LOCKED
S4_8B_FIXTURE_SCORER=IMPLEMENTED
S4_8C_EXPLANATION_BOUNDARY=IMPLEMENTED
S6_6_EVENT_STUDY=NOT_IMPLEMENTED
S6_7_SNAPSHOT_MATERIALIZATION=NOT_IMPLEMENTED
CROSS_MARKET_REST_ENDPOINT=NOT_IMPLEMENTED
RISK_DECISION_WIRING=0
SNAPSHOT_WRITER_GRANTS=0
PROVIDER_LIVE_ACCOUNT_ORDER_CALLS=0
RETURN_ENGINE_FILES_CHANGED=0
EXPERIENCE_DASHBOARD_FILES_CHANGED=0
SECURITY_CAMPAIGN=FINAL_GATE_ONLY
```

동일 logical identity와 동일 canonical payload는 replay이고, 다른 payload는 PostgreSQL
`23505`로 transaction 전체를 rollback한다. base table UPDATE/DELETE/TRUNCATE는 statement
trigger가 거부하며 writer role은 base table 직접 권한을 갖지 않는다.

## code-path·user-flow coverage

```text
[disabled exact-19 fixture]
    |-- count/hash stable --------------------> test_fixture_producer.py
    |-- outbound counter != 0 -- reject ------> test_fixture_producer.py
    `-- replay | conflict --------------------> test_fixture_producer.py
                    |
                    v
[decision_market_writer / one transaction]
    |-- exact role + five append functions ---> test_postgres_repository.py
    |-- same bytes -> REPLAY -----------------> FlywayMigrationIntegrationTest
    |-- changed bytes -> 23505 rollback ------> FlywayMigrationIntegrationTest
    `-- UPDATE/DELETE/RLS/grant bypass reject -> CrossMarketMigrationContractTest
                    |
                    v
[252-session pure scorer]
    |-- tie/midrank/boundary -----------------> test_scorer.py
    |-- reorder -> same canonical bytes ------> test_scorer.py
    `-- missing/future/nonfinite -> unavailable> test_scorer.py
                    |
                    v
[latest owner-scoped snapshot reader]
    |-- exactly one current row -> AVAILABLE -> JdbcCrossMarketSnapshotAdapterTest
    `-- 0 | 2 | future | unavailable --------> typed unavailable test matrix

[cause/analyst/GDELT projection]
    |-- contradiction/retraction/supersede ---> test_explanation_projection.py
    |-- PRECEDES/GDELT causal upgrade reject -> test_explanation_projection.py
    `-- broker count <3 / BUY weight=0 --------> test_explanation_projection.py

[PDF boundary]
    |-- MANUAL_LINK_ONLY ---------------------> metadata only, download 0
    |-- path/symlink/MIME/bounds fail --------> parser 0
    |-- delete fail --------------------------> parser/storage/outbound 0
    `-- derivedDataAllowed=false -------------> projection discarded
                                               test_pdf_boundary.py
```

## 재현 / Reproduction

```bash
cd workspaces/decision-platform/python-services
TMPDIR="$(mktemp -d)" uv run --frozen pytest -q tests/cross_market
uv run --frozen ruff check app/cross_market tests/cross_market
uv run --frozen mypy app

cd ../spring-api
./gradlew ktlintCheck test \
  --tests '*CrossMarket*' \
  --tests '*FlywayMigrationIntegrationTest*'
```
