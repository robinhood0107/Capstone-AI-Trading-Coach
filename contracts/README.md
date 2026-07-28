# contracts

워크스페이스 간 유일한 진실 소스. 여기가 고정되기 전까지 각자 폴더 구현을 시작하지 않는다.

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

online 1회 검증은 최종 HEAD/PR #55 CI/fresh security report/Redis baseline을 결속한 60분 이하
0600 packet과 별도 current-user approval ID/SHA가 있을 때만
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
balance source page의 continuation cursor는 `positions_complete=false`인 connectivity evidence로만
허용하고 authoritative position universe나 risk input으로 게시하지 않는다.
두 profile의 packet은 `approvalId`와
canonical SHA-256에 결속한 Redis `SET NX PX` single-use claim을 runtime 생성 전에 획득해야 하며,
성공·첫 실패·runtime 생성 실패 모두 재사용 권한을 남기지 않는다. KIS_MOCK provider response는
JSON parse/sanitize 전에 1 MiB cap을 적용하고 실패 출력은 allowlisted reason/HTTP status/
provider code만 허용한다. KIS_LIVE 실계좌 주문은 계속 OFF다.

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

## S1.3 ECOS/Naver 내부 source snapshot

S1.3은 public REST/gRPC를 추가하지 않는다. Decision Platform이 아래 sanitized JSON을 생성하고,
Return Engine은 이후 합의된 `contracts/`·`artifacts/` handoff 경계에서 manifest를 검증해 소비한다.
다른 workspace의 구현 파일이나 Decision Platform의 임의 로컬 경로를 직접 읽는 방식은 계약이 아니다.

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
| `schemas/naver_news_metadata_snapshot.schema.json` | Decision Platform `naver-news-metadata-collect` | Return Engine sentiment pipeline | 최대 30일, Naver 이용조건 gate 필요 |
| `schemas/source_snapshot_manifest.schema.json` | 두 collector의 secure publisher | handoff consumer·retention command | source snapshot과 동일 |

artifact는 ignored root의
`{source}/YYYY/MM/DD/{uuid-v4}/snapshot.json`과 `manifest.json` 두 파일로 구성한다.
consumer는 `manifest.json`만 완성 marker로 열거하고 schema, 상대경로, SHA-256을 확인한 뒤
snapshot을 읽는다. manifest가 없는 snapshot orphan은 무시한다. provider raw body/header/message,
credential/query가 포함된 provider request URL, auth/header, credential·credential hash, 기사 본문과
로컬 절대경로는 두 파일 모두에 금지한다. schema가 검증하는 정규화된 기사 metadata URL과 고정
provenance URL은 허용한다.
삭제 owner는 `decision-platform:source-snapshot-retention` 하나이며 command는 기본 dry-run,
명시적 `--apply`에서만 manifest를 먼저 지운다.

Naver canonical snapshot은 운영용과 smoke용 포맷을 나누지 않고 동일한 `schemaVersion: 1`에서
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
