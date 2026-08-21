# contracts

워크스페이스 간 유일한 진실 소스. 여기가 고정되기 전까지 각자 폴더 구현을 시작하지 않는다.

## S5.7A model-neutral Market Data contract

`catalogs/s5-7a-market-data-lock.v1.json`은 LightGBM publication과 분리된 내부 Python data plane의
contract-only authority다. generated artifact는 다음 세 개뿐이다.

- `market-data-seed.v1`: 7,218개 preserved source chunk의 provider-free 중립 adoption manifest
- `market-data-daily-shard.v1`: 한 XKRX session의 월중 고정 exact-31 + 두 지수 + ECOS 최대 2 series
- `market-data-health.v1`: freshness/calendar/partial/fail-closed 상태

운영 reader 최대 253 close, 연구 reader 최대 1,260 session, provider-on-read 0을 고정한다. 이 계약은
DB, runtime, public REST/OpenAPI, Dashboard, scheduler 또는 provider authority를 만들지 않는다.

```bash
uv run --frozen python contracts/generate_s5_7a_market_data_contracts.py --check
uv run --frozen python contracts/generate_p1_verification_contracts.py --check
uv run --frozen python -m unittest contracts.tests.test_s5_7a_market_data_contracts -v
uv run --frozen python -m unittest contracts.tests.test_p1_verification_contracts -v
uv run --frozen python contracts/validate.py
```

| 폴더 | 내용 |
|---|---|
| `schemas/` | principle/signal/backtest_result/risk_decision 등 JSON Schema |
| `proto/` | gRPC `.proto` 정의 (생성 코드는 커밋하지 않음) |
| `openapi/` | springdoc이 생성한 OpenAPI와 diff하는 기준 파일 |
| `examples/` | schema를 통과해야 하는 예시 payload |
| `changes/` | 계약 변경 이유·영향 범위 기록 |

## S2.1 Principle contract v1

`catalogs/s2-1-principle-contract.v1.json`은 S2.1 preset/rule/DTO/operation의
machine-readable 단일 진실이다. object key는 재귀 정렬하고 array 순서는 보존한 UTF-8/LF/2-space
JSON이며 마지막 LF를 포함한 전체 bytes의 SHA-256을 OpenAPI root
`x-s2-1-contract-sha256`에 넣는다. contract ID는
`s2-1-principle-contract/v1`이다. generated schema/fixture를 직접 편집하지 않고
`generate_principle_contracts.py`로 재생성·비교한다.

Implementation PR의 canonical OpenAPI는 실제 Spring controller에서 생성한 S2.1 6개 operation과
auth/health/error runtime path를 포함한다. Principle path/component는 수동 합성하지 않으며
controller 변경과 generated canonical OpenAPI를 같은 변경으로 갱신한다.

### Artifact map

| operation | request | success `data` / page examples | error examples |
|---|---|---|---|
| `GET /api/v1/principle-presets` | 없음 | `schemas/principle-preset-list.schema.json`, `examples/principle-presets.valid.json` | unauthorized/forbidden |
| `POST /api/v1/principles` | `schemas/principle-create-request.schema.json`, `examples/principle-create*.valid.json` | `schemas/principle.schema.json`, `examples/principle.valid.json` | validation, unauthorized, forbidden, payload-too-large |
| `GET /api/v1/principles` | `cursor,size,sort`만 | `schemas/principle-list-response.schema.json`, `examples/principle-list{,-next-page,-empty}.valid.json` | validation/cursor, unauthorized, forbidden |
| `GET /api/v1/principles/{principleId}` | 없음 | `schemas/principle.schema.json`, `examples/principle.valid.json` | validation, unauthorized, forbidden, not-found |
| `PUT /api/v1/principles/{principleId}` | `schemas/principle-update-request.schema.json`, `examples/principle-update{,-no-op}.valid.json` | `schemas/principle.schema.json`, `examples/principle.valid.json` | validation, unauthorized, forbidden, not-found, conflict, version-exhausted, payload-too-large |
| `GET /api/v1/principles/{principleId}/versions` | `cursor,size,sort`만 | `schemas/principle-history-response.schema.json`, `examples/principle-history{,-next-page,-empty}.valid.json` | validation/cursor, unauthorized, forbidden, not-found |

오류의 complete five-field envelope는
`examples/principle-error-{validation,cursor,unauthorized,forbidden,not-found,conflict,version-exhausted,payload-too-large}.valid.json`에
있다. rule tuple/range/scale/severity와 unknown/duplicate/empty/oversized negative fixture는
`examples/invalid/principle*.invalid.json`에 있다. operation/error allowlist와 모든 exact limit은
catalog를 기준으로 한다.

### Consumer matrix / 소비자 영향

| consumer | KR | EN |
|---|---|---|
| Experience Dashboard | exact 3개 preset의 KR/EN 이름·설명·disclaimer와 8 rules를 표시한다. create rules 생략은 deep copy, PUT은 full replacement다. create timeout을 blind retry하지 않고 owner list 후보를 사용자가 확인하게 한다. | Render the exact three localized presets, disclaimer, and eight rules. Omitted create rules deep-copy the preset; PUT is full replacement. Do not blindly retry an indeterminate create. |
| Return Engine | exact 8개 rule tuple과 canonical 순서를 사용한다. ratio는 fraction, loss/MDD는 signed ratio, money/count는 integer이며 `principleId + version` full snapshot을 참조한다. | Consume the exact eight rule tuples in canonical order. Ratios are fractions, loss/MDD are signed ratios, money/count are integers, and `principleId + version` identifies a full snapshot. |
| Decision Platform | DB-verified JWT `sub`만 owner로 사용하고 missing/cross-owner를 동일 404로 처리한다. update는 owner-scoped SQL CAS, history/audit는 append-only다. Principle은 finance idempotency 대상이 아니다. | Trust only the DB-verified JWT subject as owner, collapse missing/cross-owner to one 404, use owner-scoped SQL CAS, keep history/audit append-only, and exclude Principle from finance idempotency. |

### Reproducible checks

아래 명령은 provider key를 읽거나 provider 호출을 만들지 않는다. OpenAPI fixture는 Gradle이
`build/openapi-fixture/openapi.env`에 mode `0600`으로 생성하고 strict parser가 한 descriptor로
검증한다. dotenv text를 shell에서 `source`하지 않는다.

```bash
uv run --frozen python contracts/generate_principle_contracts.py --check
uv run --frozen python -m unittest discover -s contracts/tests -v
uv run --frozen python contracts/validate.py
workspaces/decision-platform/spring-api/gradlew \
  -p workspaces/decision-platform/spring-api --no-daemon prepareOpenApiFixtureEnv
uv run --frozen python contracts/run_openapi_gate.py \
  --env-file workspaces/decision-platform/spring-api/build/openapi-fixture/openapi.env
```

## S2.2 Rule evaluation offline contract v1

`catalogs/s2-2-system-rule-catalog.v1.json`은 offline RuleEvaluator와
MetricSnapshot assembler가 공유하는 14-rule 단일 진실이다. 앞의 public 8개는 S2.1 catalog의
rule ID·metric·operator·range·scale·순서를 읽기 전용으로 투영하고, 뒤의 system-managed 6개는
S2.2가 소유한다. 실행 분류는 threshold 12개, readiness 1개, v1 not-applicable 1개다.
`violations`에는 threshold rule만 들어갈 수 있으며 readiness 실패는 `issues`, v1 N/A는
`abstentions`로 표현한다.

새 S2.1 version snapshot과 public read 표현은 `PrincipleRule.evidenceRequirement`를 항상
명시한다. 기존 immutable 저장 row는 rewrite하지 않고 catalog의 versioned
`legacyEvidenceInference`에 따라 field가 없을 때 활성 rule은 `REQUIRED`, 비활성 rule은 해당
rule default로 보충하며 unknown tuple은 거부한다. hard 6개 public rule은 `REQUIRED`만 허용하고
news/disclosure는 `OPTIONAL|REQUIRED`를 허용한다. 동일한 disclosure source 오류라도
optional이면 같은 `(ruleId, code)`의 `WARN` + warning/abstention이고 required이면 `HOLD` +
issue다. optional evidence 하나만으로 HOLD/BLOCK을 만들 수 없다.

`schemas/risk_decision.schema.json`은 business result를 `violations`, `issues`, `warnings`,
`abstentions`로 분리하고 precedence를 `BLOCK > HOLD > WARN > ALLOW`로 고정한다. HOLD/BLOCK도
향후 runtime에서 HTTP 200 business result이며 transport/auth/validation error와 섞지 않는다.
`riskItems`는 실제 사용한 evidence만 담으므로 `value`는 non-null number다. `riskSummary`와
`signalSummary`는 선택 필드라서 unavailable component를 빈 성공처럼 강제하지 않는다.

portfolio selector는 `KIS_MOCK|INTERNAL_PAPER`만 허용한다. 선택 권한은 server-side
owner-scoped context에 있고 `INTERNAL_PAPER`만 저장 source `PAPER`에 매핑한다. 자동 fallback은
없다. selector 자체가 잘못되면 HTTP 400 `VALIDATION_ERROR`, 선택한 context가 없거나 사용할 수
없으면 HTTP 200 `HOLD`다. result는 immutable `principleVersionId + principleVersion`을 pin한다.

hash contract `HASH-CANONICALIZATION-S22-V2`와 `s2.2-metric-snapshot-v2`는 semantic input
hash와 snapshot artifact hash를 분리한다. object key는 사전식, 배열은 명시된 stable key,
숫자는 exponent 없는 plain decimal,
`-0`은 `0`, trailing zero는 제거한다. exact input/canonical bytes/SHA-256 vector는
`examples/s2-2-hash-vector.valid.json`에 있다. semantic input은 full order intent, 모든
MetricKey state/value/freshness/source identity, requested/observed optional evidence, disclosure
completeness/mapping/source refs와 provenance를 포함한다. artifact hash는 evaluation/retrieval
identity까지 포함한 versioned full snapshot의 exact UTF-8 bytes를 그대로 사용하며 별도 축약
hash map을 만들지 않는다.

현물 v1 full order intent는
`symbol,side,orderType,quantity,estimatedPrice,estimatedAmount,timeframe,strategyId`다. MARKET과
LIMIT 모두 `estimatedPrice`를 사용하며 `price`/`limitPrice`는 unknown property다.
`estimatedAmount`는 `quantity * estimatedPrice`의 exact overflow-checked 원화 정수 결과다. P2
`derivativeOrderIntent.limitPrice`와 provider wire 가격은 별도 namespace다.

S2.2 generated artifact는 `generate_s2_2_contracts.py`의 explicit `OUTPUTS`만 소유하고 S2.1
generator output과 겹치지 않는다. canonical catalog SHA-256은
`a4714ee9ce3031199b9067919b15931fb42e106857da5f8d8ad7a95bafa8ad7b`다. Spring classpath에는
catalog bytes를 변환 없이 복사하고 Gradle `check`가 byte equality를 검증한다. S2.2에서는
S2.2 커밋 자체는 Decision controller, persistence, OpenAPI path를 추가하지 않았다. S2.3
implementation mode부터 normalizer는 승인된 Decision path 3개와 `S23*` component 5개를 exact
allowlist로 요구한다. 외부 provider adapter는 추가하지 않고 S2.3 stored-source reader만
연결한다.

```bash
uv run --frozen python contracts/generate_principle_contracts.py --check
uv run --frozen python contracts/generate_s2_2_contracts.py --check
uv run --frozen python -m unittest discover -s contracts/tests -v
uv run --frozen python contracts/validate.py
```

## S2.3 Decision runtime과 stored-source 경계

S2.3 runtime은 `POST /api/v1/decisions/evaluate-order`, owner-scoped detail/audit와 V9
decision/trace/artifact/audit/outbox/idempotency 원자 저장을 제공한다. provider HTTP fallback
없이 저장된 sanitized source만 읽는다. V9의
`market_quote_observations`와 `instrument_catalog_observations`는 S1.1 producer, KIS_MOCK
`portfolio_balance_observations`/`portfolio_position_observations`는 S3 producer가,
`deterministic_risk_observations`/`daily_order_count_observations`는 deterministic producer가,
`corporation_registry_observations`는 S1.6 producer가 별도 최소권한으로 INSERT한다.
이번 S2.3 prerequisite는 fixture/mock transport/Testcontainers로 offline producer와 projection을
검증하며 provider 호출 권한이 아니다. INTERNAL_PAPER는 기존 ledger의 owner-scoped projection을
사용한다. `decision_app`은 SELECT만 가지며 production seed는 없다. source 구조 자체가 빠지면
`S23_RUNTIME_SOURCE_BLOCKED`, 구조가 준비된 뒤 row가 비거나 stale/incomplete/future이면 typed
unavailable과 persisted 200 HOLD다. 자세한 소유권·hash 전환은
[`20260724-s2-3-decision-contract-lock.md`](changes/20260724-s2-3-decision-contract-lock.md)를
따른다.

S1.1 instrument catalog는 append-only table과
`latest_instrument_catalog_observations` projection에
`symbol,isEtfEtn,isGoldEtfEtn,nullable productRiskScore,catalogVersion,observedAt,receivedAt,sourceRef,artifactHash`를
저장한다. `decision_market_writer`만 exact INSERT를 가지며 S2.3 reader는 symbol당 최대 한 행만
읽는다. 미래 시각·row 부재·nullable risk score를 `false`나 0으로 꾸미지 않는다.

source orchestration은 queue 없는 최대 8개 worker, source별 500ms, 전체 evaluation 900ms
shared deadline을 사용한다. 남은 전체 예산이 없으면 새 physical source call을 만들지 않고 실행
중 timeout은 cancel한다. JDBC source read는 connection acquisition과 statement도 500ms 안에
끝나야 하며, KIS_MOCK balance/position/margin은 같은 immutable revision만 조립한다. Python
gRPC reader는 pool 8, acquisition 450ms, connect 1초와 event/source-ref 각 100개 상한을
적용한다. 초과 구조를 truncate하지 않고 technical failure로 거부한다.

offline writer replay는 완전히 같은 sanitized row만 no-op으로 허용한다. primary key 또는 대체
unique identity가 같고 의미 필드가 다르면 PostgreSQL `23505`로 전체 transaction을 rollback한다.
Decision child row는 `decision_id + evaluation_id` composite FK로 같은 graph에 묶고 audit target과
payload `decisionId`가 달라질 수 없다. owner detail/audit과 idempotency replay는 base table
SELECT 없이 fixed-search-path bounded function만 사용한다.

canonical S2.3 catalog SHA-256은
`d035607af50a0f7cb9cd7170e9a6a188e6af32d5bbbdb76e5e4f7b3edc68cd18`이며 tracked OpenAPI의
`x-s2-3-contract-sha256`과 CI에서 일치해야 한다.

재현 가능한 offline fixture append와 local smoke 절차는
[`workspaces/decision-platform/README.md`](../workspaces/decision-platform/README.md#s23-offline-golden-path)를
따른다. 이 절차는 provider/live/account/order/broker 호출을 만들지 않는다.

## S2.4 Risk API와 Kill Switch

S2.4는 `GET /api/v1/risk/portfolio`, `GET /api/v1/risk/kill-switch`,
`POST /api/v1/risk/kill-switch` 세 route를 추가한다. portfolio 조회는 S2.3의 owner-scoped
stored observation과 기존 `MetricSnapshotAssembler`를 재사용하며 legacy `risk_snapshots`를
읽지 않는다. `priceFresh`는 `latest_market_quote_observations`의 실제 시각에서만 계산하고,
producer나 row가 없는 필드는 nullable 값과 sanitized `MISSING_SOURCE` warning으로 남긴다.

Kill Switch 권위는 V10의 `risk_kill_switch(kill_switch_id='GLOBAL')` 단일 행이다. 상태
변경 transaction은 singleton lock, generation CAS, append-only transition, 유효 Decision
집합 무효화, bounded audit와 outbox를 원자적으로 기록한다. USER와 ADMIN은 정지할 수 있지만
재가동은 현재 DB의 status/role/security version을 transaction 안에서 다시 확인한 ADMIN만
가능하다. Decision 저장도 singleton을 shared lock으로 재확인해 활성화와 저장 사이의
TOCTOU를 닫는다.

| 계약 | 경계 |
|---|---|
| `schemas/s2-4-risk-portfolio.schema.json` | nullable producer 값, 정수 원화와 비율 범위, exact freshness |
| `schemas/s2-4-kill-switch-state.schema.json` | active/reason 조합과 actor-free public projection |
| `schemas/s2-4-kill-switch-request.schema.json` | exact `active`/optional `reason` 요청 |
| `openapi/openapi.json` | 세 route와 fail-closed 오류 envelope의 generated SSOT |

재현 명령은 모두 fixture/Testcontainers 경계에서 실행되며 provider/live/account/order/broker
호출을 만들지 않는다.

```bash
uv run --frozen python -m unittest discover -s contracts/tests -v
uv run --frozen python contracts/validate.py
workspaces/decision-platform/spring-api/gradlew \
  -p workspaces/decision-platform/spring-api --no-daemon ktlintCheck build
workspaces/decision-platform/spring-api/gradlew \
  -p workspaces/decision-platform/spring-api --no-daemon prepareOpenApiFixtureEnv
uv run --frozen python contracts/run_openapi_gate.py \
  --env-file workspaces/decision-platform/spring-api/build/openapi-fixture/openapi.env
```

상세 결정과 소비자 영향은
[`20260725-s2-4-risk-kill-switch-contract.md`](changes/20260725-s2-4-risk-kill-switch-contract.md)를
따른다.

## S3.1 Brokerage Mock 주문

S3.1은 `POST /api/v1/brokerage/mock/orders`, `GET /api/v1/brokerage/orders/{orderId}`,
`POST /api/v1/brokerage/orders/{orderId}/cancel`,
`GET /api/v1/brokerage/mock/accounts/{accountId}/balances`,
`GET /api/v1/brokerage/mock/accounts/{accountId}/buyable`을 추가한다. 제출 route는 S2.3에서
저장된 owner-scoped Decision과 S2.4 Kill Switch DB authority를 같은 write path에서 재검증한 뒤
KIS Mock ledger에 durable reservation을 원자 저장한다. online gate가 닫힌 기본 경로는
`SUBMITTED`를 유지하고, 명시적으로 열린 KIS_MOCK 경로는 provider를 한 번 호출해 `ACCEPTED`
outcome을 기록하거나 모호한 결과에 `PENDING_RECONCILIATION` 기록을 시도한다. 후자도 실패하면
최초 `SUBMITTED` reservation이 recovery anchor로 남는다. cancel도 DB cancel-request를 먼저
기록하고 online gate가 열린 경우에만 provider 전량 취소를 한 번 호출한다. 요청 body는
`decisionId`, exact 8-field `orderIntent`, `userAcknowledgement.warningsAccepted`만 허용하며
`accountId`, provider, actor, raw receipt 필드는 모두 unknown field로 거부한다.

| 계약 | 경계 |
|---|---|
| `schemas/s3-1-mock-order-request.schema.json` | body-supplied account/provider 금지, exact 현물 order intent |
| `schemas/s3-1-mock-order-response.schema.json` | `SUBMITTED | PENDING_RECONCILIATION | ACCEPTED`와 opaque `accountId` |
| `schemas/s3-1-mock-order-detail.schema.json` | owner-scoped sanitized order detail |
| `schemas/s3-1-mock-balance.schema.json` | stored `KIS_MOCK` balance projection, raw account 없음 |
| `schemas/s3-1-mock-buyable.schema.json` | stored balance 기반 buyable 계산 projection |
| `proto/brokerage.proto` | Spring/Python gRPC boundary, 기본 disabled, raw credential/account 금지 |
| `db/migration/V11__s3_1_brokerage_mock_orders.sql` | one Decision = one order, HMAC idempotency, FORCE RLS, append-only order events, sanitized outbox/audit |
| `db/migration/V15__s3_online_kis_mock_provider_outcomes.sql` | mock-only provider outcome, pending 상태, bounded atomic writer |
| `openapi/openapi.json` | S3.1 route와 `S31*` schema component의 generated SSOT |

raw `X-Idempotency-Key`와 raw 계좌번호는 ledger에 저장하지 않고 purpose-version HMAC만 남긴다.
동일 idempotency key와 동일 payload는 저장된 canonical result를 replay하며, 같은 Decision을
다른 key로 재사용하면 `CONFLICT`다. cancel은 전역 write idempotency scope에서 동일 key replay와
다른 key 재취소 conflict를 구분한다. 기본 balance/buyable은 S2.3 stored-source projection을
읽는다. online gate가 열린 경우에도 그 owner/account anchor를 먼저 확인한 뒤 mock provider를
조회하며 raw 계좌번호를 공개하지 않는다. 만료 Decision은 `DECISION_EXPIRED`, 활성 Kill Switch
또는 무효화 Decision은 `RISK_BLOCKED`, verified tick-table context가 없는 LIMIT 주문은
`BROKERAGE_UNAVAILABLE`로 닫는다.

S3-online transport는 공식 KIS Mock origin과 주문 `VTTC0011U | VTTC0012U`, 전량 취소
`VTTC0013U`, 잔고 `VTTC8434R`, 매수가능 `VTTC8908R`, 체결
`VTTC0081R | VTSC9215R`의 exact path/TR만 허용한다. S1.1 mock 1초 no-burst와
deployment-global tokenP limiter를 재사용하고 주문성 retry·redirect는 0이다. Python gRPC는
numeric loopback과 finite cap을 요구하며 기본값은
`KIS_MOCK_BROKERAGE_ONLINE_ENABLED=false`다. raw provider reference는 Spring/DB로 넘기지
않고 owner/order에 결속한 bounded-TTL Fernet ciphertext로만 Redis에 둔다. brokerage cap은
token/cache, shared limiter, provider accounting, socket handoff보다 먼저 소비되어야 하며,
재시작 후에도 Redis의 encrypted `PENDING` ciphertext를 CAS 기준으로 `COMMITTED` 전환할 수
있어야 한다.

online 1회 검증의 `schemaVersion=1`은 PR #55 history verification에만 고정한다. 새
`schemaVersion=2` packet은 최종 HEAD/dynamic PR/head branch/required CI/fresh sealed security
report·manifest·coverage·findings/Redis baseline/nonce를 함께 결속한 60분 이하 owner-private
directory의 새 `0600` file과 별도 current-user approval ID/SHA가 있을 때만
`balance -> buyable -> LIMIT BUY 1주 -> 전량 취소 -> 최근 체결조회`를 cap
`tokenP=1`, `brokerage=5`, retry/artifact 0으로 실행한다. 이 packet은 gRPC server 상시
활성화, background polling, S3.3 fill append, KIS_LIVE 계좌·주문 권한을 승인하지 않는다.
마지막 체결조회는 source-shape/readability 진단이며, 낮은 지정가 주문을 즉시 취소해 해당 주문
row가 아직 없거나 provider가 sparse matched row만 반환하더라도 public fill이나 대사 snapshot을
합성하지 않는다. strict reconciliation reader만 완전한 provider row를 snapshot으로 해석한다.
구현·fixture·OpenAPI·일반 테스트의 provider physical call은 0건이다.

재현 명령:

```bash
uv run --frozen python -m unittest discover -s contracts/tests -v
uv run --frozen python contracts/validate.py
workspaces/decision-platform/spring-api/gradlew \
  -p workspaces/decision-platform/spring-api --no-daemon ktlintCheck build
workspaces/decision-platform/spring-api/gradlew \
  -p workspaces/decision-platform/spring-api --no-daemon prepareOpenApiFixtureEnv
uv run --frozen python contracts/run_openapi_gate.py \
  --env-file workspaces/decision-platform/spring-api/build/openapi-fixture/openapi.env
cd workspaces/decision-platform/python-services
uv run --frozen ruff check .
uv run --frozen mypy app
TMPDIR="$(mktemp -d /tmp/s31-pytest-XXXXXX)" uv run --frozen pytest -q
```

상세 결정과 소비자 영향은
[`20260726-s3-1-brokerage-mock-contract.md`](changes/20260726-s3-1-brokerage-mock-contract.md)를
따른다.

## S3.2 INTERNAL_PAPER 체결 원장

S3.2는 `POST /api/v1/brokerage/paper/orders`,
`GET /api/v1/brokerage/paper/accounts/{accountId}/balances`,
`GET /api/v1/brokerage/paper/accounts/{accountId}/buyable`을 추가하고 공통 주문 조회·취소 route가
저장된 `brokerageMode`를 그대로 반환하도록 확장한다. paper application path는 KIS Mock gRPC port를
참조하지 않으며 provider 장애 시 자동 fallback으로 호출되지 않는다.

| 계약 | 경계 |
|---|---|
| `catalogs/s3-2-internal-paper-contract.v1.json` | route, mode, id pattern, 가격·증거 exact 계약의 SSOT |
| `schemas/s3-2-paper-order-request.schema.json` | S3.1과 같은 exact body, client mode/account 금지 |
| `schemas/s3-2-paper-order-response.schema.json` | FILLED fill object 또는 ACCEPTED null의 상호강제 |
| `schemas/s3-2-order-detail.schema.json` | 공통 조회·취소의 mode↔orderId prefix; pending은 KIS_MOCK read만 허용 |
| `schemas/s3-2-paper-balance.schema.json` | append-only ledger 파생 잔고와 `LAST_FILL_PRICE_V1` 고지 |
| `schemas/s3-2-paper-buyable.schema.json` | owner-scoped cash/price 정수 몫 projection |
| `db/migration/V13__s3_2_internal_paper_ledger.sql` | mode identity, append-only fill ledger, FORCE RLS, exact evidence, bounded definer 함수 |
| `openapi/openapi.json` | paper 3 route와 공통 route mode 확장의 generated SSOT |

가격은 최신 COMPLETE stored last quote, 다음으로 같은 관측의 previous close만 사용한다. MARKET은
정수 KRW 5bps 불리한 방향 반올림, LIMIT은 slippage 0과 전량 체결 또는 ACCEPTED만 허용한다.
`paper_order_events`의 exact before/after chain이 order-derived 현금·포지션 mutation의 진실
소스이며 account/position row는 같은 transaction에서 갱신되는 projection이다. rebuild는 비교
전용이고 application role에 노출하지 않는다.

동시 제출은 purpose-version HMAC scope만 담는 30초 Redis claim으로 먼저 직렬화하고, 완료 결과와
재시도 응답의 진실은 PostgreSQL durable idempotency row에 둔다. Redis key/value에는 raw key,
owner, account, payload를 넣지 않으며 장애 시 paper write 전에 fail-closed한다. 구현 OpenAPI
normalizer는 S3.2 contract ID/SHA-256, exact 5개 path/method와 exact 9개 `S32*` component를
allowlist로 검사한다.

paper account 생성·충전·삭제 route, 부분 체결, 미체결 worker, 수수료·세금·시장충격 모델,
mark-to-market job은 없다. 테스트 account는 admin seed만 사용하고 시연 seed는 S8.3에 남긴다.
KIS/provider/live-account/live-order/fill-query physical call은 0건이다.

재현 명령:

```bash
uv run --frozen python contracts/generate_s3_2_contracts.py --check
uv run --frozen python -m unittest discover -s contracts/tests -v
uv run --frozen python contracts/validate.py
workspaces/decision-platform/spring-api/gradlew \
  -p workspaces/decision-platform/spring-api --no-daemon ktlintCheck build
workspaces/decision-platform/spring-api/gradlew \
  -p workspaces/decision-platform/spring-api --no-daemon prepareOpenApiFixtureEnv
uv run --frozen python contracts/run_openapi_gate.py \
  --env-file workspaces/decision-platform/spring-api/build/openapi-fixture/openapi.env
```

상세 결정과 소비자 영향은
[`20260727-s3-2-internal-paper-ledger-contract.md`](changes/20260727-s3-2-internal-paper-ledger-contract.md)를
따른다.

## S3.3 체결 이벤트와 대사

S3.3은 stored sanitized fill observation을 append-only 주문 이벤트와 수량 projection으로
반영하고, 주문/체결 대사를 자동 보정 없이 기록한다. 공개 surface는 ADMIN 전용
`POST /api/v1/brokerage/orders/{orderId}/reconcile`과 owner-scoped
`GET /api/v1/brokerage/{mock|paper}/accounts/{accountId}/fills` 두 route다.
클라이언트가 체결을 보고하는 route와 provider polling 경로는 없다.

| 계약 | 경계 |
|---|---|
| `catalogs/s3-3-fill-contract.v1.json` | route, mode, ID, 200/50/31일 bounds, cursor, status SSOT |
| `schemas/s3-3-fill-observation.schema.json` | 최대 4 MiB/10,000개의 sanitized offline fixture |
| `schemas/s3-3-reconcile-response.schema.json` | ADMIN reconcile data와 3상태 대사 projection |
| `schemas/s3-3-fill-page.schema.json` | exact 9-field owner fill, 최대 50개와 HMAC cursor |
| `db/migration/V14__s3_3_fill_events_reconciliation.sql` | additive 수량 컬럼, source, FORCE RLS, bounded definer 함수 |
| `openapi/openapi.json` | exact 3 route와 exact 5개 `S33*` component의 generated SSOT |

catalog ID는 `s3-3-fill-contract/v1`, SHA-256은
`937508069d35ee087e7c8cdd171f52f876396097f27b14f82528a839efae9da7`다.
관측은 `(order_id, provider_exec_ref_hash)`로 중복 방어하며 provider exec 원문을 저장하지 않는다.
Kotlin 순수 전이는 Applied/Duplicate/Invalid를 구분하고 SQL 원자 write가 같은 판정을 재검증한다.
단일 수량 보존식은
`filled_quantity + leaves_quantity + unfilled_terminated_quantity = quantity`다.
MISMATCH는 warning/audit로만 남기며 관측이나 주문을 자동 수정하지 않는다.

reconcile은 현재 ADMIN role/status/security version을 transaction 안에서 재검증하고 advisory lock
아래 한 번에 COMPLETE 관측 최대 200개를 처리한다. fills 조회는 KST inclusive 범위 최대 31일,
page size 50, `(filledAt DESC, orderId DESC, execRefHash DESC)` 정렬과 owner/mode/account/기간을
결속한 900초 HMAC cursor를 사용한다. `decision_fill_writer`만 관측 INSERT 권한을 가지며
`decision_app`의 직접 source 접근과 application role의 UPDATE/DELETE/TRUNCATE는 없다.

S3-online은 공통 주문 상태에 provider outcome 복구용 `PENDING_RECONCILIATION`을 additive하게
추가하되 mode/status schema는 이 상태를 KIS_MOCK에만 허용한다. 이는
`MATCHED | MISMATCH` 대사 판정과 별개다. 체결조회 strict parser는 provider order row가 정확히
하나 있을 때만 대사 snapshot을 만들고, exact 5단계 approval probe의 마지막 read는 bounded
source-shape만 확인한다. 둘 다 background polling, scheduler,
`decision_fill_writer` append 또는 자동 DB fill 반영은 없다.
exact KIS_MOCK probe가 주문가능 시간을 검증할 때 `orderDivision`은 승인 packet에
명시되어야 하며, buyable 조회·주문·취소 reference에 같은 값이 적용된다. packet의
`exchangeDivision`은 KIS_MOCK 현금 신규주문에서 `KRX`만 허용하며, `NXT`는 provider handoff
전에 fail-closed 된다. 생략 시 기존처럼 `KRX`가 기본이다.

재현 명령:

```bash
uv run --frozen python contracts/generate_s3_3_contracts.py --check
uv run --frozen python -m unittest discover -s contracts/tests -v
uv run --frozen python contracts/validate.py
workspaces/decision-platform/spring-api/gradlew \
  -p workspaces/decision-platform/spring-api --no-daemon ktlintCheck build
workspaces/decision-platform/spring-api/gradlew \
  -p workspaces/decision-platform/spring-api --no-daemon prepareOpenApiFixtureEnv
uv run --frozen python contracts/run_openapi_gate.py \
  --env-file workspaces/decision-platform/spring-api/build/openapi-fixture/openapi.env
```

전체 P0-11~P0-20 결정과 비범위는
[`20260727-s3-3-fill-events-reconciliation-contract.md`](changes/20260727-s3-3-fill-events-reconciliation-contract.md)를
따른다. 일반 구현·계약·OpenAPI·테스트의 provider call은 0건이고, 별도 exact-approved KIS_MOCK
`FULL` one-shot probe만 위의 고정 5회 상한 안에서 최종 검증으로 실행할 수 있다. 반복 실패
recovery는 새 exact-approved `BALANCE_DIAGNOSTIC` packet으로 balance 1회,
`tokenP=1`/`brokerage=1`, retry/artifact 0만 허용하며 성공해도 새 `FULL` 승인이 필요하다.
주문 접수 뒤 cancel 실패만 source packet SHA/nonce anchor가 보존된 encrypted `COMMITTED`
reference를 쓰는 `CANCEL_RECOVERY`의 `cancelFull -> executionRead` cap `2`로 정리할 수 있다.
execution read만 실패한 recovery는 cancel을 재전송하지 않고 read 1회 cap `1`만 허용한다.
balance source page의 continuation cursor는 `positions_complete=false`인 connectivity evidence로만
허용하고 authoritative position universe나 risk input으로 게시하지 않는다.
두 profile의 packet은 `approvalId`와
canonical SHA-256에 결속한 Redis `SET NX PX` single-use claim을 runtime 생성 전에 획득해야 하며,
성공·첫 실패·runtime 생성 실패 모두 재사용 권한을 남기지 않는다. KIS_MOCK provider response는
JSON parse/sanitize 전에 1 MiB cap을 적용하고 실패 출력은 allowlisted reason/HTTP status/
provider code만 허용한다. KIS_LIVE 실계좌 주문은 계속 OFF다.

v1/v2 분리, sealed scan receipt 검증, deterministic author와 cancel-only recovery의 상세는
[`20260801-s3-online-kis-mock-approval-packet-v2.md`](changes/20260801-s3-online-kis-mock-approval-packet-v2.md)를
따른다.

## S4 RAG profile·policy catalog

S4/P1 RAG는 LangChain agent가 아니라 결정적 2-step RAG 경계로 구현하며, embedding profile과
policy는 static contract catalog 하나에서만 읽는다. 활성 profile은
`bge_m3_local_1024_v1`, `voyage_context_4_1024_v1` 두 개뿐이고
`voyage_context_3_1024_v1`은 active·comparison·fallback 어디에도 등록하지 않는다.

| 계약 | 경계 |
|---|---|
| `catalogs/s4-rag-contract.v1.json` | profile, policy, dimension 1024, provider/model identity, chunk strategy SSOT |
| `schemas/s4-rag-contract.schema.json` | catalog exact-shape schema |
| `schemas/s4-rag-ask-request.schema.json` | public ask body; profile/policy/provider/topK/sourceTier 입력 금지 |
| `schemas/s4-rag-answer.schema.json` | provider/model/internal score를 노출하지 않는 S4.4 answer data |
| `schemas/s4-rag-history-page.schema.json` | 질문·답변 preview가 없는 metadata-only owner history page |
| `schemas/s4-rag-history-detail.schema.json` | owner 단건 복호화와 bounded public citation detail |
| `schemas/s4-rag-feedback-request.schema.json` | boolean `helpful` 하나만 허용하는 feedback body |
| `schemas/s4-rag-consent-request.schema.json` | append-only `EXTERNAL_AI_RAG_V1` GRANT/REVOKE event |
| `schemas/s4-rag-admin-policy-selection.schema.json` | 관리자 policy pointer 선택; profile ID를 policy ID로 쓰면 실패 |

catalog ID는 `s4-rag-contract/v1`이고 canonical SHA-256은
`9b9881f9b25b6486f20999f27c0dd7043048fc26491e33cf2af892817dabbe0a`다.
generator/schema/positive·negative fixture와 Spring/Python consumer가
`CONCISE/DETAILED`, NFC 후 1~1,000 Unicode scalar+8KiB,
pinned `ONNX_DATA_ONLY`, `embeddingInputStrategy`를 같은 bytes로 검증한다.
S4.0 catalog, S4.1 registry/API, S4.2A/S4.2B generation, S4.3 authorized retrieval에 이어
S4.4는 V20 owner claim·consent·encrypted history 경계와 위 closed request/response schema를
구현한다. 기본 answerer는 `FIXTURE_ONLY`이고 external provider physical call은 0이다.
S4.5는 `s4-5-evaluation-60.v1.json`의 공개·합성 exact 60과 deterministic report를
S4.7C corpus에 결속한다. `s4-2c-voyage-approval.schema.json`과
`s4-4g-gemini-approval.schema.json`은 내부 provider 제어 packet이며 public RAG API 계약이
아니다. 전자는 zero-paid one-shot plan 목적만, 후자는 preflight/evaluation/production
activation 목적을 분리해 허용한다. fresh packet이 없으므로 두 outbound executor는 닫혀 있다.
`bge_then_voyage_on_sla_v1`은 요청별 runtime fallback이 아니라 BGE warm p95 SLA 실패,
Voyage 평가 통과, 관리자 승인 뒤 default pointer를 한 번 원자 전환하는 정책이다.
public `POST /api/v1/rag/ask` body는 `question`, `answerMode`, `relatedSymbols`, `topics`만
허용하며 `X-Idempotency-Key`는 별도 header 계약으로 처리한다. DB는 source/revision,
generation, materialization/evaluation, active pointer, usage ledger 같은 동적 상태만 소유한다.

재현 명령:

```bash
uv run --frozen python contracts/generate_s4_rag_contracts.py --check
uv run --frozen python contracts/generate_s4_5_provider_contracts.py --check
uv run --frozen python capstone-rag/generate_s4_5_evaluation.py --check
uv run --frozen python -m unittest discover -s contracts/tests -v
uv run --frozen python contracts/validate.py
```

상세 결정과 소비자 영향은
[`20260729-s4-rag-contract-catalog.md`](changes/20260729-s4-rag-contract-catalog.md)를 따른다.

## Pre-S5 active RAG·global-news contract addendum

> 현재 상태: `RAG_AND_GLOBAL_NEWS_CONTRACT_LOCKED / OA112_ACTIVE_CONTRACT_LOCKED /
> S4_7D_OA112_PHYSICAL_ACTIVATION=NOT_MATERIALIZED / TARGET_NOT_ACTIVE`.
> 이 addendum은 existing v1 OpenAPI/proto/source-card, exact-30, historical OA112 metadata와
> `news_sentiment_summary.v2`를 byte-stable하게 보존한다. raw corpus download와 provider physical call은
> canonical packet 및 fresh execution evidence 없이는 계속 0이다. local Document IR materializer,
> immutable bundle/owner-import writer, Optional 3 및 Finnhub/SEC/Fed local one-shot executor는
> `IMPLEMENTED_DRAFT` 경계에만 있으며 activation 또는 provider entitlement를 뜻하지 않는다.

`generate_pre_s5_rag_news_contracts.py --check`는 다음 active policy를 deterministic하게 검증한다.

| 산출물 | 현재 lock |
|---|---|
| `catalogs/pre-s5-rag-news-contract.v1.json` | logical OA112 14 track × 8, reserve 최대 28, 자동 승격 0, Optional 3는 local one-shot packet-gated only |
| `schemas/rag-oa112-logical-selection-v1.schema.json` | raw URL/hash를 복제하지 않는 `CONTRACT_LOCKED_NOT_MATERIALIZED` selection |
| `schemas/rag-oa112-reserve-registry-v1.schema.json` | research-only reserve와 active-generation reference 0 |
| `schemas/rag-source-card-v4.schema.json` | future OA activation 전 machine fetch/local processing/external embedding/external generation 모두 true 요구 |
| `schemas/s4-rag-v2-*-v1.schema.json` | external consent/effective status, 5분 single-use import ticket, owner deletion activation/hard-delete, profile policy |
| `openapi/rag-v2-pre-s5-addendum.openapi.json` | existing ask/status/history bytes를 bind하고 consent/effective-consent/import ticket의 세 route만 더한 addendum; deletion activation/profile policy도 schema로 잠금, route activation은 없음 |
| `schemas/foreign-news-*.schema.json` | Finnhub personal-local, SEC/Fed official, GDELT offline-reference explanation-only aggregate 및 Finnhub/SEC/Fed one-shot packet/receipt만 허용 |
| `openapi/foreign-news-sentiment.v1.openapi.json` | `/api/v2/market-evidence/{symbol}/foreign-news-sentiment` contract-only endpoint |
| `schemas/s4-8-optional3-*.schema.json` | v1 zero-call templates를 보존하고, v2는 Finnhub Recommendation/Earnings, Twelve Data, Massive의 local one-shot packet/receipt만 허용; per-packet physical 1, retry/raw 0 |

foreign-news response는 `decisionAuthority=NONE`, `allowedUses=[EXPLANATION_ONLY]`,
`s5FeatureEligible=false`, `riskDecisionHashIncluded=false`, `rawProviderDataStored=false`,
`articleMetadataStored=false`를 정확히 고정한다. SEC/Fed의 `officialReleaseLocator`는 article metadata가
아닌 sanitized provenance locator다. GDELT는 existing Decision Platform offline aggregate의
agreement/conflict reference일 뿐 HTTP transport/executor/outbound implementation은 없다.
Finnhub/SEC/Fed probe는 selected local model, canonical short-expiry packet, clean HEAD/tree와
CI/security evidence를 모두 먼저 검증하며 operation당 physical call 1, retry 0, raw/header/query
cap 1이다. DNS/connection preflight 실패 receipt는 `NOT_EXECUTED/0`이며 actual provider handoff 뒤의
outcome만 physical call 1이다. raw/header/query persistence는 0이다. packet claim 전 owner writer
privilege preflight를 수행하고 성공한 transient
aggregate만 owner-local append-only record로 materialize한다.

Voyage는 `voyage-context-4` 1024차원, official tokenizer 기준 110K token 이하 exact
manifest-bound resumable document batch, EXACT30/OA112 query batch 각 1회만 허용한다. 성공 batch와
source checkpoint는 재호출하지 않는다. local tokenizer가 없으면 Voyage AI Hugging Face commit
`8ca946072a18e398cd61f2ad0243b56d0350b1db`의 exact `tokenizer.json`을 5분·physical cap 1
bootstrap packet으로 먼저 취득하고 observed SHA-256을 고정한다. 이 hash가 있어야 Window A batch를
authoring할 수 있다. Window A document/evaluation packet은 최대 2시간 TTL이고 일반 runtime query는
기존 5분 TTL을 유지한다. 성공 query vector도 V54에 usage와 원자 stage한다. public CPU BGE 재실행은
`TERMINALLY_SUPERSEDED_NO_FURTHER_BGE_RUN`이다. 모든 batch·평가 통과 뒤에만 CAS할 수 있다.
Vertex는 fixed 0600 service-account JSON으로 OAuth token을 1회 교환하고 `VERTEX_MODEL_ID`(기본
`gemini-3.5-flash`)를 packet에 고정한 global publisher model·top-5·질문당 generation 1회만 허용한다.
ambient ADC, API key와 Gemini Developer API는 0이다.
OpenAI, Gemini Developer API, reranker, verifier, files/batch API와 query-level fallback은 모두 0이다.

## Historical S4.7D OA140·owner-private RAG v2 contract context

> 현재 상태: `S4_7D_CONTRACT=LOCKED / SAFE_PARSER_OCR_RUNTIME=OFFLINE_ONLY /
> S4_7D_RUNTIME=STUB_FAIL_CLOSED / OA112_HISTORICAL / OA140_TARGET`.
> schema·fixture·별도 OpenAPI·proto와 OA112 metadata manifest가 병합됐지만 OA 원문
> download/parse/embed/evaluation, owner import, derived index writer, bundle activation과 actual
> retrieval은 없다. `/api/v2/rag/**`는 full bundle 미준비 시 typed `CORPUS_NOT_READY`, 현재
> skeleton이 그 뒤에 도달해도 `GENERATION_UNAVAILABLE`로 fail-closed한다.

S4.7D는 P1 exact-30과 v1 API를 byte-stable하게 유지하면서, 투자 코치용 공개 OA corpus와
요청 owner의 개인 문서를 후속 immutable generation으로 결합하는 계약이다. 공개 요청은
corpus/profile/topK를 선택하지 못하고 서버가 `exact30 + oa + ownerPrivate` bundle을 자동으로
pin한다. 검색은 PostgreSQL/pgvector/pg_trgm과 application RRF `k=60`을 유지하며 RAG의
`decisionAuthority`는 항상 `NONE`이다.

| 산출물 | 계약 경계 |
|---|---|
| `catalogs/s4-rag-v2-contract.v1.json` | exact 14개 curriculum track, 9개 format family, OCR 연구 후보 3개, server-selected bundle과 active processing mode |
| `schemas/rag-source-card-v3.schema.json` | `PROJECT_SOURCE_CARD | OPEN_ACCESS_DOCUMENT | OWNER_LOCAL_DOCUMENT` closed union과 provenance·권리 flag |
| `schemas/rag-document-ir-v1.schema.json` | heading/paragraph/list/table/formula/caption, page/slide/sheet/section locator, reading order와 OCR evidence |
| `schemas/rag-oa-manifest-v1.schema.json` | DRAFT/RELEASED manifest, track별 8~10개와 전체 112~140개 release gate |
| `schemas/s4-rag-v2-*.schema.json` | ask/status/history/error와 `PUBLIC_WEB | LOCAL_DOCUMENT` citation union |
| `openapi/rag-v2.openapi.json` | v1 OpenAPI를 변경하지 않는 v2 gated surface; materializer/retrieval은 아직 없음 |
| `proto/rag_v2.proto` | server-selected bundle과 tagged citation을 보존하는 unary `RagService.Ask` v2 |
| `../capstone-rag/manifests/s4-7d-oa140-release.v1.json` | `OA112_HISTORICAL`: 14개 track × 8개 = 112개 fixed HTTPS metadata/source hash; active corpus 아님 |
| `../capstone-rag/manifests/s4-7d-oa140-curriculum-map.v1.md` | 경제 기초 → 계량·시장 → 금융공학·퀀트 → 통합 검증 학습 경로와 대표 질문 |
| `../capstone-rag/manifests/s4-7d-oa140-distribution.v1.json` | GitHub Release/Hugging Face metadata-only artifact set과 publication blocker |

active processing mode는 `LOCAL_EPHEMERAL_PARSE` 하나다. 파일별 approval ID·nonce·TTL은
계약에 없다. 과거 파일별 ephemeral approval enum은 날짜가 고정된 계약·감사 기록 재현에만
남고 새 runtime 입력으로 허용하지 않는다. local parse 허용과 외부 LLM 전송은 분리한다.
source evidence와 owner corpus-level opt-in 중 하나라도 불충분하면 요청 전체를
retrieval-only로 처리한다.

OCR 연구 후보 세 개의 고정 금융 fixture 평가 결과 production backend는 `PADDLE_VL` 하나다.
`PADDLE_STRUCTURED`와 `UNLIMITED_GGUF`는 품질 gate 실패로 research benchmark에만 남는다.
CPU와 Intel Arc 130V `GPU.0` 실측, 모델·runtime pin과 raw text 없는 선택 영수증은
`../capstone-rag/ocr/benchmark/receipts/benchmark-summary.v1.json`에 있다. NVIDIA lane은
현재 장비가 없어 구현·계약 smoke만 유지하고 `NOT_RUN_NO_NVIDIA`로 기록한다.

OA `RELEASED` manifest의 historical release gate는 track마다 8~10개, 전체 112~140개의
canonical work/revision이다. `OA112_HISTORICAL`은 112개 source의 fixed HTTPS URL과 raw
SHA-256 metadata만 남긴다. historical `OA140_TARGET`은 active policy가 아니며, current logical
selection과 future physical activation은 위 Pre-S5 addendum을 따른다. 원문·추출 text·embedding은
재배포하지 않는다.
`rag-content setup`은 현재 manifest를 검증하고 `BUILDING`으로 들어가지만, 모든 source
download/parse/embed/eval과 active pointer pin이 끝나기 전에는 `FULL_READY`가 아니다.

재현 명령:

```bash
uv run --frozen python contracts/generate_s4_7d_rag_v2_contracts.py --check
uv run --project workspaces/decision-platform/python-services --frozen \
  python contracts/generate_rag_v2_proto.py --check
uv run --frozen python -m unittest \
  contracts.tests.test_s4_7d_rag_v2_contracts \
  contracts.tests.test_generate_rag_v2_proto
uv run --frozen python contracts/validate.py
```

상세 결정과 소비자 영향은
[`20260802-s4-7d-oa140-owner-private-rag-v2.md`](changes/20260802-s4-7d-oa140-owner-private-rag-v2.md)를
따른다.

## S4.8 교차시장·애널리스트 계약

> 계획 타당성: `PLAN_FEASIBILITY=GO_WITH_EXTERNAL_HARD_GATES`.
> 현재 상태: `S4_8A=CONTRACT_LOCKED / S4_8_CORE6_V2=CONTRACT_LOCKED /
> S4_8_CORE6_LOCAL_PROBE_RUNTIME=IMPLEMENTED_DRAFT / S4_8B_C=IMPLEMENTED_MERGE_CANDIDATE /
> S4_8=VERIFIED_OFFLINE_STORED / S6.6=RETIRED_STRICT_PIT_UNAVAILABLE /
> S6.7=RETIRED_NO_VALID_THRESHOLD`.
> 월 데이터 비용 목표는 `0원`이고 offline fixture·지연/EOD가 먼저다. 기관용 제품과
> 실시간 SOX/VIX feed는 post-P1 선택지이며 P1 DoD가 아니다. 새 agent framework·별도
> cloud·Kafka는 hard dependency가 아니다.
>
> S4.8A의 machine-readable schema·fixture·catalog와 generator/hash parity는 계약으로
> 고정됐다. S4.8B/C offline-only 구현은 provider 없는 Python fixture/scorer/projection,
> V23 append-only evidence 저장과 Spring latest snapshot read port를 제공한다. S6.6/S6.7
> 실행 capability는 `s6-contract-lock.v2`와 `s6-capability-disposition.v1`에서 퇴역했다.
> V78은 historical-only이고 V79가 writer/reader functions와 runtime grants를 제거한다.

순서 0 `S4.READ`는 관련 공개·private 명세 EOF receipt와 충돌 목록만 남기는 read-only
preflight다. 첫 변경 PR은 아래 일곱 계약과 fixture/generator/parity만 포함하는
**contract-only PR**이어야 한다. 이 PR이 검증·병합되기 전 adapter·DB·API·RiskEngine
runtime PR은 시작하지 않는다.

고정된 versioned SSOT는 다음 일곱 개다.

1. `market_source_entitlement.v1`
2. `cross_market_exposure_catalog.v1`
3. `cross_market_observation.v1`
4. `analyst_revision_evidence.v1`
5. `market_cause_evidence.v1`
6. `cross_market_risk_snapshot.v1`
7. `cross_market_policy_evaluation.v1`

S4.8A가 계약·entitlement와 fixture를 잠그고, S4.8B가 provider 호출 없는 offline/EOD
materialization·append-only projection·순수 scorer kernel을 소유한다. S4.8C는
`decisionAuthority=NONE`인 cause/analyst 설명만 만든다. S6.6 event-study/replay/threshold와
S6.7 snapshot materialization/RiskEngine 연결은 historical contract로만 보존하며 current
runtime owner가 없다. 재도입은 strict PIT evidence와 새 versioned contract-change를 요구한다.

S4.8 Core 6 v2는 `KIS`, `OPENDART`, `SEC_EDGAR`, `KRX`, `KOFIA`, `ECOS`의 entitlement,
probe approval, sanitized receipt를 별도 계약으로 고정한다. KIS current-price·SEC EDGAR
submissions/companyfacts·KRX KOSPI/KOSDAQ daily의 local one-shot executor는 canonical non-fixture
packet, clean HEAD/tree·CI/security evidence, fixed request plan, retry 0이 모두 일치할 때만 handoff를
만든다. KIS cached-token miss는 OAuth token issue를 열지 않는다. `SUCCESS` receipt는 eligible direct
source의 정확히 한 번의 `DATA_REQUEST`·`HTTP_2XX`·non-null projection hash를 함께 증명하고 complete
required-operation set만 V50 runtime에 read-only로 materialize한다. OpenDART/ECOS는 기존 authorized
projection만 재사용하고 KOFIA는 `BLOCKED_NO_CREDENTIAL_OR_APPROVAL`이다. GDELT producer, Naver
retirement와 v1/V23 경계는 변경하지 않는다. Optional 3는 Core 6 exact set에 포함하지 않으며 v1
template을 보존한 채 별도 v2 local one-shot approval/receipt만 사용한다. packet과 fresh execution
evidence가 없으면 physical call은 0이다.

조사 inventory 42개는 사용 가능한 API 수가 아니다. 39개 machine 연동 후보 계열과 3개
manual-link 원천의 합이며 현재 S4.8 활성/live provider adapter는 0이다. KIS 18개
endpoint도 disabled fixture-only 행으로만 materialize한다. exact 42개 행과 exact 18개 allowlist는
Git으로 추적하지 않는 로컬 전용 자료수급 레지스트리가
운영 authority이며 공개 계약에는 집계와 불변식만 두고 전체 inventory를 복제하지 않는다.
공개 fixture의 exact KIS 18 identity는 endpoint 이름을 노출하지 않는 opaque SHA-256이며
모두 `CANDIDATE_DISABLED`다. 별도 GDELT aggregate entitlement도
`CANDIDATE_DISABLED`, `decisionAuthority=NONE`이다. provider raw body, PDF·뉴스 원문,
credential·계좌 데이터는 저장하지 않고, RAG source registry와 기존 30-card corpus/hash는
변경하지 않는다.

증권사 PDF 기본값은 `MANUAL_LINK_ONLY`다. 사용자가 보유한 파일은
`LOCAL_EPHEMERAL_PARSE`로 read-only 처리할 수 있지만 자동 다운로드·권리 우회 권한은
생기지 않는다. 추출 projection은 `투자포인트`, `실적전망`, `Valuation`, `목표주가`,
`위험요인`, `Disclaimer` 여섯 절과 사용자가 직접 확인한 bounded tag만 허용한다.
`derivedDataAllowed=false`이면 parser/LLM 파생 결과를 저장·전달하지 않고 임시 입력과 함께
폐기한다.

계획된 timing 계약은 signed integer milliseconds를 유지한다.

- `sourceAvailableAt = max(required component source.availableAt)`이며 optional
  analyst/news 시각은 포함하지 않는다.
- `detectionLatency = snapshotAvailableAt - sourceAvailableAt`
- `preOpenLeadTime = XKRXOpen - snapshotAvailableAt`
- `preOpenLeadTime < 0`이면 `LATE`, `= 0`이면 `AT_OPEN`, `> 0`이면 `EARLY`다.
  값을 0으로 clamp하지 않으며 적용할 XKRX open이 없을 때만 `NOT_APPLICABLE`로 둔다.

기존 Decision request/response, RAG ask/history, Signal v1/v2 payload에 추가하는 교차시장
필드는 0이다. historical `CrossMarketDecisionInput(snapshot, exposure)` wrapper와 별도 planned
조회 DTO는 current runtime에 없고 일곱 신규 계약도 기존 payload를 확장하지 않는다.

현재 cross-market Decision/runtime/writer/reader authority는 `NONE`이다. 과거 S6.7
`ALLOW → WARN`과 `ENFORCED` fixture는 historical-only이며 주문 판단에 참여하지 않는다.
계약 산출물과 재현 명령은
[`20260731-s4-8a-cross-market-contract-lock.md`](changes/20260731-s4-8a-cross-market-contract-lock.md)에
기록한다. 후속 offline runtime·권한·coverage는
[`20260801-s4-8b-s4-8c-offline-runtime.md`](changes/20260801-s4-8b-s4-8c-offline-runtime.md)를
따르며 provider/live/account/order 호출은 포함하지 않는다.
Core 6 v2 contract lock과 local runtime boundary는
[`20260802-s4-8-core6-v2-contract-lock.md`](changes/20260802-s4-8-core6-v2-contract-lock.md) 및
[`20260810-s4-8-core6-local-probe-runtime.md`](changes/20260810-s4-8-core6-local-probe-runtime.md)를
따른다.

## S5.0 Signal v2 contract lock

Signal v1과 current OpenAPI bytes는 그대로 두고 component별 `AVAILABLE | ABSTAIN` closed
union인 `schemas/signal-v2.schema.json`을 추가한다. `AVAILABLE + HOLD`는 정상 neutral
prediction이고, stale·producer failure·artifact drift·missing evidence는 signal/confidence/asOf/
HMM state를 만들지 않는 `ABSTAIN`이다. required component가 하나라도 ABSTAIN이면 composite도
ABSTAIN이다.

`catalogs/s5-0-signal-v2-contract.v1.json`은 cross-market/analyst/news/cause/RAG/LLM field와
RiskDecision/order wiring을 금지하고 current Signal v1/OpenAPI hash를 고정한다. Python generator와
Spring contract-only parser가 같은 positive/negative fixture를 소비한다. 이 historical lock의
generated bytes는 유지하며 runtime 전환은 아래 별도 amendment를 따른다.

```bash
uv run --frozen python contracts/generate_s5_0_signal_v2_contracts.py --check
uv run --frozen python -m unittest contracts.tests.test_generate_s5_0_signal_v2_contracts -v
uv run --frozen python contracts/validate.py
```

상세 변경과 parity/coverage는
[`20260801-s5-0-signal-v2-contract-lock.md`](changes/20260801-s5-0-signal-v2-contract-lock.md)를
따른다.

### S5 Signal runtime transition

`catalogs/s5-signal-runtime-transition.v1.json`은 historical OpenAPI에서 정확히
`/api/v2/signals/{symbol}` GET, 승인된 runtime component와 `Signal v2` tag만 제거한 projection의
SHA-256이 기존 digest와 같아야 한다. `schemas/signal-v2-runtime-v1.schema.json`은 all-ABSTAIN에서
root `asOf`와 `modelReportId`를 생략하며, 하나 이상 AVAILABLE이면 최신 component `asOf`를 요구한다.
Signal v1/v2 unknown-field 개별 corpus와 internal `lightgbm-signal-artifact-v1`도 closed schema다.

S5.5 runtime은 deterministic `FAKE_CONTRACT` generator, bounded no-follow ingest, V72 exact
replay/conflict DML과 production-only read function을 구현한다. fake/legacy row는 production pointer가
될 수 없고 현재 real dataset/model/pointer는 없으므로 조회는 all-ABSTAIN이다. RiskDecision/order
wiring과 provider call은 0이다.

```bash
uv run --frozen python contracts/generate_s5_signal_runtime_contracts.py --check
uv run --frozen python -m unittest contracts.tests.test_generate_s5_signal_runtime_contracts -v
uv run --frozen python contracts/validate.py
```

결정과 hard stop은
[`20260815-s5-signal-runtime-transition.md`](changes/20260815-s5-signal-runtime-transition.md)를 따른다.
S5.1의 calendar-derived monthly schedule, external manifest digest trust anchor, closed PIT
provenance와 실제 LightGBM cross-market 0-call/hash 회귀는
[`20260816-s5-1-pit-artifact-hardening.md`](changes/20260816-s5-1-pit-artifact-hardening.md)를 따른다.

## S1.5 KIS 데이터 품질 리포트

S1.5는 public API가 아니라 Decision Platform 내부 CLI `kis-data-quality-report`가 생산하는
sanitized aggregate artifact다. reporter는 selected universe/dataset/collection manifest와 canonical
KIS 일봉만 읽고 provider/network outbound를 만들지 않는다. 실제 KIS 데이터 수집·백필은 이 CLI와
결합하지 않으며 별도의 현재 사용자 exact approval 없이는 실행하지 않는다.

| 계약 | Producer | Consumer | 보존 |
|---|---|---|---|
| `schemas/kis_data_quality_report.schema.json` | `decision-platform:python-data-quality` | 중간·최종보고서 evidence와 S6.5 strict nightly | 시연/평가 종료 후 28일까지 |
| `schemas/kis_data_quality_bundle_manifest.schema.json` | S1.5 secure bundle publisher | latest pointer verifier와 report consumer | report bundle과 동일 |

bundle은 ignored KIS data root의
`quality/YYYY/MM/DD/<reportId>/{report.json,report.md,manifest.json}`에 있고,
`quality/latest-manifest.json`은 완성 bundle 뒤에만 교체한다. report JSON에는 자기 hash나 Markdown
hash를 넣지 않으며 bundle manifest가 두 파일의 exact name/size/SHA-256을 소유한다. 동일
fingerprint 재실행은 existing mode/hash/content를 검증한 no-op이고 손상된 같은 reportId를 덮어쓰지
않는다.

`schemaVersion=1`, `metricPolicyVersion=s1-5-quality-report-v1`이다. 일반 실행은 collection accounting을
생략할 수 있지만 API metric은 `NOT_AVAILABLE`, evidence는 `PARTIAL`이 된다. 보고서 acceptance에는
`--fail-on-quality --require-complete-evidence`를 함께 쓰며 exit precedence는 `2 > 3 > 1 > 0`이다.
event date가 미확정이면 `HOLD_UNTIL_EVENT_DATE_CONFIGURED`, 인용한 reportId는 최종 제출 완료까지
pin한다. S1.5는 canonical Parquet이나 bundle을 자동 삭제하지 않는다.

## S1.3G Naver 퇴역과 GDELT aggregate active 계약

2026-07-31부터 active 뉴스 권한은
`changes/20260731-s1-3g-naver-retirement-gdelt-aggregate-lock.md`와
`docs/adr/ADR-038-naver-retirement-gdelt-aggregate.md`를 따른다.
2026-08-01 offline 구현은
`changes/20260801-s1-3g-gdelt-offline-producer.md`에 고정한다.

```text
NAVER_ACTIVE_PROVIDER_RUNTIME_STORAGE=RETIRED
GDELT_PROVIDER_PHYSICAL_CALLS=0
GDELT_ARTICLE_METADATA_STORAGE=0
GDELT_DECISION_AUTHORITY=NONE
GDELT_S5_FEATURE_ELIGIBLE=FALSE
SECURITY_SCAN_TIMING=FINAL_CONSOLIDATED_CAMPAIGN
```

| 계약 | Producer | Future consumer | 권한 |
|---|---|---|---|
| `schemas/gdelt_news_tone_observation.v1.schema.json` | Decision Platform fixture-first aggregate producer | 저장된 sanitized observation reader | 설명 evidence only, `decisionAuthority=NONE` |
| `schemas/news_sentiment_summary.v2.schema.json` | Decision Platform news sentiment aggregator | 별도 합의 뒤 Return Engine | `EXPLANATION_ONLY`, S5 feature 비활성 |

두 schema는 `AVAILABLE | ABSTAIN` closed union이며 불완전한 데이터를 수치 0으로 바꾸지 않는다.
GDELT 출처·프로젝트 URL·공식 About/Terms URL이 항상 있어야 하고 기사 본문·제목·URL·domain·
article ID·raw query와 raw provider payload 저장을 거부한다. 실제 provider 호출은 별도 승인형
후속 작업이며 현재 기본값과 이번 계약 wave의 physical call은 0이다. Naver boundary source card와
exact-30 RAG corpus는 provider 결과가 아니므로 유지한다.

구현된 `gdelt-aggregate-collect`의 기본 모드는 bundled synthetic fixture다. strict parser는 두
mode의 timestamp set을 교차 검증하고 4 MiB·512 point·finite/count/norm/window 상한을 적용한다.
완전한 입력만 `AVAILABLE`이며 empty/partial/malformed/norm-zero는 numeric field 없는
`ABSTAIN`이다. canonical artifact publication은 0600·append-only·fsync·no-follow 경계이고,
future online packet은 exact hash/HEAD/query/window/cap/retry 0을 검증해도 HTTP transport가 아직
`NOT_ACTIVATED`이므로 provider 호출을 만들 수 없다.

## S1.3 ECOS/Naver 내부 source snapshot — HISTORICAL_SUPERSEDED

아래 내용은 2026-07-16 당시의 감사 가능한 이력이며 신규 실행 권한이 아니다. Naver active
provider/runtime/storage 권한은 S1.3G에서 퇴역했다. S1.3은 public REST/gRPC를 추가하지 않았다.
당시 Decision Platform이 아래 sanitized JSON을 생성하고,
Return Engine은 이후 합의된 `contracts/`·`artifacts/` handoff 경계에서 manifest를 검증해 소비한다.
다른 workspace의 구현 파일이나 Decision Platform의 임의 로컬 경로를 직접 읽는 방식은 계약이 아니다.

> 현재 상태(2026-08-01): Naver collector·credential/CLI·snapshot schema/example/test와
> shared manifest/retention의 Naver branch를 제거했다. 승인된 local leaf는 exact
> application-visible 삭제를 완료했고 영수증은 ignored local 영역에만 있다. 아래 Naver
> 이름·수치·hash는 당시 감사 기록일 뿐 현재 파일이나 실행 명령을 가리키지 않는다.

> 구현 상태(2026-07-16): Naver lower-only batch·strict smoke, JSON Schema, offline 회귀와
> 승인된 online smoke를 완료하고 PR #16 merge commit
> `6f439155d9f5ec626fc185f29f2e0bd64ca54780`으로 `main`에 병합했다. Approval A1/A2/A3는
> 실패 evidence로 분리한다. A4
> `approval-a4-692635240394-20260715T055519Z`는 physical `4`·Redis `+4`로 성공했고,
> `semantic-3bb3810728cf` 승인 뒤 registry를 활성화했다. B1
> `approval-b1-23618d21265d-20260715T072151Z`는 ECOS physical `2`·Redis `+2`와 Naver
> physical `1`·Redis `+1`로 성공했다. B1 evidence SHA-256은
> `ecb62e114352439994fa799096a916757ba7fba081f08f1d1b78ec35397d85fb`다.

| 계약 | Producer | Consumer | 보존 |
|---|---|---|---:|
| `schemas/ecos_macro_snapshot.schema.json` | Decision Platform `ecos-macro-collect` | Return Engine macro feature pipeline | 365일 |
| historical `naver_news_metadata_snapshot` | active schema와 producer 제거 완료 | 없음 | active retention 없음 |
| `schemas/source_snapshot_manifest.schema.json` | ECOS secure publisher | handoff consumer·retention command | ECOS 365일 |

artifact는 ignored root의
`{source}/YYYY/MM/DD/{uuid-v4}/snapshot.json`과 `manifest.json` 두 파일로 구성한다.
consumer는 `manifest.json`만 완성 marker로 열거하고 schema, 상대경로, SHA-256을 확인한 뒤
snapshot을 읽는다. manifest가 없는 snapshot orphan은 무시한다. provider raw body/header/message,
credential/query가 포함된 provider request URL, auth/header, credential·credential hash, 기사 본문과
로컬 절대경로는 두 파일 모두에 금지한다. schema가 검증하는 정규화된 기사 metadata URL과 고정
provenance URL은 허용한다.
삭제 owner는 `decision-platform:source-snapshot-retention` 하나이며 command는 기본 dry-run,
명시적 `--apply`에서만 manifest를 먼저 지운다.

아래 Naver canonical snapshot 설명은 historical audit다. 해당 schema·producer·설정은
active tree에서 제거됐다. 당시에는 운영용과 smoke용 포맷을 나누지 않고 동일한 `schemaVersion: 1`에서
`queries` 길이 `1..4`를 허용한다. producer 설정 `NAVER_BATCH_SIZE`는 기본 4이고 `1..4` 범위에서만
하향하며 canonical smoke는 1이다. consumer는 정확히 네 query를 가정하지 않고 snapshot의
`queries` 배열 길이와 manifest `queryCount`를 교차 검증한다. 두 값이 다르거나 0 또는 5 이상이면
artifact를 거부한다. manifest `physicalAttemptCount`도 query당 최대 2회, 즉
`physicalAttemptCount <= 2 * queryCount`여야 한다.

Approval A preflight의 안전 진단은 contract schema를 늘리지 않고 ignored operator-evidence
v1의 `sanitizedPreflight.diagnostic`에만 저장한다. A1/A2/A3는 실패 evidence로 분리하고,
성공 채택 집합은 A4와 원자적으로 성공한 B1만으로 구성한다. B1은 ECOS `D-29..D` 2회를
완전히 성공한 뒤 Naver rank-1 `display=10` 1회를 성공했다. accepted set은 A4+B1의
ECOS `6`+Naver `1`=`7`이며 실패 run을 합산하거나 프로젝트 lifetime 호출 수로 표현하지 않는다.
A3/A4 복구와 B1 검증은 기존 3개 source snapshot schema, public API, DB/Flyway, dependency,
다른 workspace를 변경하지 않았다.

여기서 구현·병합 완료는 Decision Platform producer/storage 경계를 뜻한다. Return Engine이
이 snapshot을 실제 교환 artifact로 소비하는 cross-workspace handoff는 별도 계약 합의 전까지
활성 상태로 간주하지 않는다. S1.3K KRX universe 자동화는 이 계약 파일을 변경하지 않은 별도
내부 트랙이다.
## S5.7B normalized adoption runtime

S5.7A의 세 계약 bytes는 유지한다. S5.7B는 `market-data-seed.v1`을 실제 source-only exporter와 V75
append-only storage/reader로 구현했으며 새 public 계약은 추가하지 않았다. 구현은 raw chunk copy,
hardlink, source path persistence, provider call, LightGBM Signal/Risk/order authority를 만들지 않는다.

실측 source/normalized identity는 contract-change
`changes/20260821-s5-7b-market-data-normalized-adoption.md`에 기록한다. 역사 union에는 영숫자 KRX
단축코드가 하나 있으므로 archive/DB 연구 행은 원문을 보존하고, daily shard의 현재 exact-31 계약은
그대로 숫자 31개를 유지한다.

DB stage는 explicit expected manifest SHA를 필수로 받고, operational/research view는 correction의 최신
generation을 먼저 고른 뒤 각각 253/1,260 session 상한을 적용한다. 이 구현 보강은 S5.7A JSON Schema
bytes나 public OpenAPI를 바꾸지 않는다.
