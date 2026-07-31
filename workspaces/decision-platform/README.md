# decision-platform

담당: 박종진 (`robinhood0107`)

투자 원칙(Principle) → 평가(Decision/RiskEngine) → 모의 주문(Brokerage) → RAG 설명까지를 담당하는 워크스페이스. Kotlin(Spring) API 서버와 Python(gRPC/FastAPI) 서비스 두 축으로 구성된다.

## 구조

```
spring-api/            # Gradle Kotlin 프로젝트 — Controller/Application/Domain/Infrastructure
python-services/        # uv 프로젝트 — LightGBM/RAG/금융공학/데이터클라이언트/브로커리지 어댑터
```

## 세팅

공개 레포에는 최종 명세/API 계약과 구현 코드만 두고, 로컬 전용 참고자료와 개인 파일 경로는 커밋하지 않는다. 요약:

1. `cp ../../.env.example ../../.env` 후 PostgreSQL/collector/disclosure-reader/source-writer/Redis password, role별 offline DSN, Spring↔Python gRPC shared secret, JWT issuer/audience, 목적별 JWT/login/credential HMAC key, single-quoted attested demo credential bundle과 필요한 provider secret을 채운다. plaintext demo password는 `.env`에 저장하지 않는다.
2. `docker compose --env-file ../../.env -f ../../infra/docker-compose.infra.yml up -d`로 loopback-only PostgreSQL/Redis를 기동한다.
3. `spring-api/`는 커밋된 Gradle wrapper로 `./gradlew ktlintCheck build`를 실행한다.
4. `python-services/`는 `uv sync --frozen` 후 `uv run pytest`, `uv run ruff check .`, `uv run mypy app`으로 검증한다.

기존 PostgreSQL volume을 유지하는 경우 루트 README의 one-time application role bootstrap 절차를 먼저 따른다. Redis는 password+AOF+`noeviction`이며 OpenDART quota 원장으로는 사용하지 않는다.

KIS outbound는 이 workspace가 단일 owner다. S1.1 client는 실전 18/s hard cap·기본 120ms 간격, 모의 1/s·1,000ms 간격을 같은 opaque credential/appkey scope의 Redis 원자 limiter로 공유한다. `/oauth2/tokenP` physical send는 mock/live 합산 deployment-global 1/s를 보수 적용하고 token cache/singleflight만 mode별로 분리한다. Return Engine과 후속 S1.6/S3 adapter는 별도 limiter를 만들지 않고 이 경계를 재사용한다.

## S4.8→S6.7 교차시장 위험 오버레이

> 계획 타당성: `PLAN_FEASIBILITY=GO`.
> 구현 상태: `IMPLEMENTATION=SPEC_ONLY / NOT_IMPLEMENTED / PLANNED`.
> 월 데이터 비용 목표는 `0원`이고 offline fixture·지연/EOD가 먼저다. 기관용 데이터와
> 실시간 SOX/VIX feed는 post-P1 선택지이고, 새 agent framework·별도 cloud·Kafka는 hard
> dependency가 아니다.
>
> 아래 구조·API·명령은 목표 계약이며,
> 일곱 schema, `s2-2-system-rule-catalog.v2`, contract-change, fixture/golden vector가
> contract gate를 통과하기 전에는 구현 또는 실행 가능 상태를 뜻하지 않는다. provider/live
> account/live order physical call은 0이다.

순서 0 `S4.READ`에서 관련 공개·private 명세를 EOF까지 읽고 receipt·충돌 목록만 남긴다.
그다음 S4.8A의 일곱 계약, fixture, generator, parity만 담은 contract-only PR을
검증·병합하며, 이 PR 전에는 adapter·DB·API·RiskEngine runtime PR을 시작하지 않는다.

교차시장 모듈은 RAG와 분리된 저장형 위험 evidence를 목표로 한다. Python은 entitlement가 허용된
offline fixture/EOD 관측, 애널리스트 revision projection, 원인 evidence ledger, 252개 완료
세션 empirical percentile, S6.6 event-study와 LightGBM BUY policy replay를 소유할 예정이다.
Spring은 provider를 호출하지 않고 owner-scoped latest snapshot과 같은 시점의 versioned
exposure를 한 내부 입력으로 결속해 S6.7 RiskEngine에 적용할 예정이다.

P1 기본 mode는 `WARN_ONLY`다. versioned exposure catalog에 명시된 종목의 신규 BUY만
`ALLOW`에서 최대 `WARN`으로 강화할 수 있다. `OFF`와 `SHADOW`는 판단을 바꾸지 않으며,
`ENFORCED`의 HOLD/BLOCK은 post-P1 별도 승인 전 비활성이다. SELL, 기존 보유분 자동매도,
주문 생성, 수량 축소는 범위 밖이다.

다음 경계를 지킨다.

- KIS 18개 endpoint adapter는 fixture-first이며 기본 physical call은 0이다.
  exact 42개 integration target 행과 exact 18개 allowlist의 authority는
  Git으로 추적하지 않는 로컬 전용 자료수급 레지스트리이며
  공개 문서에는 전체 inventory를 복제하지 않는다.
- 구조화 애널리스트 자료는 같은 증권사의 이전 값 대비 목표가·EPS·매출 revision만 설명에
  사용하고 `BUY` 의견 자체의 가중치는 0이다.
- 증권사 PDF는 기본 `MANUAL_LINK_ONLY`다. 자동 다운로드·영속 저장·외부 LLM 전송은 하지
  않는다. 별도 권리 확인을 거친 `LICENSED_EPHEMERAL_LOCAL`도 `투자포인트`, `실적전망`,
  `Valuation`, `목표주가`, `위험요인`, `Disclaimer` 여섯 절과 사용자가 확인한 bounded tag만
  projection할 수 있다. `derivedDataAllowed=false`이면 파생 결과도 저장·전달하지 않고
  임시 입력과 함께 폐기한다.
- provider body, PDF·뉴스 원문, credential·계좌 식별자는 DB와 test fixture에 저장하지 않는다.
- 기존 Decision request/response, RAG ask/history, Signal v1/v2 payload에 추가하는 교차시장
  필드는 0이다. 내부 `CrossMarketDecisionInput(snapshot, exposure)` wrapper와 별도 planned
  조회 DTO만 사용한다.
- 가격·신선도·설정·exposure처럼 판단에 사용한 field는 Decision semantic/artifact hash v3에
  포함하고, 애널리스트·뉴스·원인 text·RAG·LLM 설명과 snapshot identity 시각은 판단 hash에서
  제외한다. 별도 저장 snapshot artifact는 bounded provenance/evidence를 포함할 수 있고 자신의
  `artifactHash`를 canonical preimage에 넣지 않는다.
- synthetic fixture 결과는 실제 성과로 표시하지 않는다. 최소 3년 PIT 자료가 없으면
  event-study 결과는 `DATASET_UNAVAILABLE`이다. `evidenceMode=SYNTHETIC_FIXTURE`이면
  `validationStatus=UNVALIDATED`, `performanceClaimAllowed=false`를 강제한다.

S4.8B는 수동/offline EOD materialization, append-only 저장과 I/O 없는 결정적
`CrossMarketScorer` kernel을 만들 계획이다. S6.6은 scorer output으로 event-study/replay와
threshold 동결만 수행하고, S6.7이 snapshot을 materialize해 저장 reader/RiskEngine에 연결한다.
S7.3은 동일 저장 port의 scheduling만 추가하며 새 provider 호출이나 source ownership을 만들지
않는다.

관측 가능 시간 지표는 signed integer milliseconds로 분리한다.

- `sourceAvailableAt = max(required component source.availableAt)`이며 optional
  analyst/news 시각은 제외한다.
- `detectionLatency = snapshotAvailableAt - sourceAvailableAt`
- `preOpenLeadTime = XKRXOpen - snapshotAvailableAt`
- `preOpenLeadTime < 0`은 `LATE`, `= 0`은 `AT_OPEN`, `> 0`은 `EARLY`이며 0으로 clamp하지 않는다.
  적용할 XKRX open이 없을 때만 `NOT_APPLICABLE`이다.

계약·구현 파일이 생성된 뒤 사용할 planned focused offline 검증:

```bash
cd python-services
uv run --frozen pytest -q tests/cross_market

cd ../spring-api
./gradlew --no-daemon test --tests '*CrossMarket*'
```

S8/P1의 planned 공개 조회는 query parameter가 없는 인증형
`GET /api/v1/risk/cross-market`이다. 구현되면 저장된 네 점수, 정확히 네 component freshness,
config version, artifact hash, evidence/validation/performance-claim 상태와 원인·반론을 합친
최대 10개 sanitized evidence만 반환한다. versioned exposure는 내부 Decision 입력에만 결속하고
공개 DTO에는 노출하지 않는다.

S8.4 사용자 과제는 synthetic/paper 화면에서 `WARN_ONLY`, `LATE`, 근거와 반론을 구분하고
교차시장 evidence가 주문 생성·매도·BLOCK을 직접 만들지 않음을 확인하는 것이다. 사용자
피드백은 사용성·이해도·경고 후 수정/보류 여부만 수집하며 실거래를 요구하지 않는다.

## S2.3 stored-source 경계

Decision 평가 요청의 현물 v1 `orderIntent`는 MARKET/LIMIT 모두 `estimatedPrice`를 사용한다.
S2.3은 provider HTTP를 호출하지 않고 저장된 sanitized source만 읽는다. 현재가·호가와 instrument
metadata는 S1.1 producer의 `market_quote_observations`,
`instrument_catalog_observations`/`latest_instrument_catalog_observations`, KIS_MOCK 잔고는
S3 producer의
`portfolio_balance_observations`/`portfolio_position_observations`, INTERNAL_PAPER는 기존
owner-scoped ledger, deterministic risk/order-count와 corp registry는 각 전용 observation이
권위다. 이번 continuation은 각 소유 모듈의 offline producer/writer/projection을
fixture/mock transport/Testcontainers로 검증하되 provider/live/order call은 0이다.
instrument row는
`symbol,isEtfEtn,isGoldEtfEtn,nullable productRiskScore,catalogVersion,observedAt,receivedAt,sourceRef,artifactHash`를
저장하고 `decision_market_writer`만 exact INSERT한다. S2.3 reader는 symbol당 최대 한 행만 읽고
row 부재·미래 시각·nullable score를 false/0으로 만들지 않는다.
`decision_app`은 source projection SELECT와 append-only
Decision writer에 필요한 exact 권한만 가지며 production source seed와
KIS_MOCK→INTERNAL_PAPER 자동 fallback은 없다. source 구조 자체가 없으면
`S23_RUNTIME_SOURCE_BLOCKED`, 준비된 구조의 row가 없거나 stale/incomplete면 persisted 200
HOLD가 된다. test fixture는 test profile/Testcontainers에만 존재하며 mock/live 주문 실행은
S3 경계로 남는다.

source orchestration은 queue 없는 최대 8개 worker에서 source별 500ms와 전체 evaluation 900ms
shared deadline을 함께 강제한다. 전체 예산이 끝난 뒤 새 physical call을 만들지 않고 timeout
작업은 cancel하며 MDC trace 문맥은 worker 실행 뒤 복원한다. JDBC connection acquisition과
statement timeout도 500ms이고, KIS_MOCK balance/position/margin은 하나의 immutable revision에
pin한다. Python gRPC reader는 최대 8개 connection, acquisition 450ms, connect 1초와
event/source-ref 각 100개 상한을 사용한다. `GetDisclosureEvents` business RPC는 loopback
plaintext 위에서도 shared-secret metadata를 요구하고 `decision_disclosure_reader` DSN만 허용한다.
초과는 truncate된 성공이 아니라 technical failure다.

offline writer의 같은 fixture 재실행은 exact row가 같을 때만 no-op이다. 같은 primary/alternate
unique identity에 다른 의미 필드가 들어오면 PostgreSQL `23505`로 transaction 전체를 rollback한다.
Decision child graph는 `decision_id + evaluation_id` composite FK와 audit payload-target 일치
constraint로 cross-wire를 막는다.

## S2.4 Risk와 Kill Switch

S2.4는 owner-scoped `GET /api/v1/risk/portfolio`와 DB-authoritative
`GET|POST /api/v1/risk/kill-switch`를 제공한다. portfolio 조회는 S2.3의
`latest_portfolio_balance_observations`, `latest_deterministic_risk_observations`,
`latest_market_quote_observations`와 기존 `MetricSnapshotAssembler`를 재사용한다.
legacy `risk_snapshots`는 읽지 않고, source가 없거나 stale이면 nullable 값과 sanitized
warning을 반환한다.

Kill Switch는 V10의 `GLOBAL` singleton과 monotonic generation이 권위다. 활성화는 현재
사용 가능한 Decision을 append-only invalidation으로 막고 신규 평가·저장을 fail-closed한다.
같은 상태 요청은 멱등 no-op이며, 해제는 transaction 안에서 현재 DB의 ADMIN 권한과 security
version을 다시 확인한다. 공개 상태에는 `active`, `reasonClass`, `changedAt`만 포함한다.

아래 검증은 Testcontainers와 tracked fixture만 사용한다. KIS/broker/gRPC/외부 HTTP와
account/order 호출은 모두 0이다.

```bash
cd spring-api
./gradlew --no-daemon ktlintCheck build
./gradlew --no-daemon prepareOpenApiFixtureEnv

cd ../../..
uv run --frozen python contracts/validate.py
uv run --frozen python contracts/run_openapi_gate.py \
  --env-file workspaces/decision-platform/spring-api/build/openapi-fixture/openapi.env
```

## S3.1 Brokerage Mock 주문

S3.1은 S2.3 Decision과 S2.4 Kill Switch를 소비하는 `KIS_MOCK` 주문 제출/조회/취소와
balance/buyable 조회 경계다. runtime route는 `POST /api/v1/brokerage/mock/orders`,
`GET /api/v1/brokerage/orders/{orderId}`, `POST /api/v1/brokerage/orders/{orderId}/cancel`,
`GET /api/v1/brokerage/mock/accounts/{accountId}/balances`,
`GET /api/v1/brokerage/mock/accounts/{accountId}/buyable`을 추가한다. S3.3은 stored sanitized
체결 관측·대사와 owner fills 조회를 후속 구현했으며 live readiness는 여전히 별도 gate다.

요청 body는 `decisionId`, exact 8-field `orderIntent`, `userAcknowledgement.warningsAccepted`만
허용한다. account/provider/actor/raw receipt 필드는 거부하고, raw `X-Idempotency-Key`, raw
계좌번호, provider raw payload는 저장하지 않는다. V11 ledger는 one Decision = one order,
purpose-version HMAC idempotency, FORCE RLS owner projection, append-only cancel event와
sanitized outbox를 강제한다.
verified KRX tick-table context가 없는 LIMIT 주문은 `BROKERAGE_UNAVAILABLE`로 fail-closed한다.

아래 검증은 Testcontainers, contract fixture와 injected/fake Python transport만 사용한다.
검증 중 KIS/broker/live account/order provider 물리 호출은 모두 0이다.

```bash
cd spring-api
./gradlew --no-daemon ktlintCheck build
./gradlew --no-daemon prepareOpenApiFixtureEnv

cd ../../..
uv run --frozen python -m unittest discover -s contracts/tests -v
uv run --frozen python contracts/validate.py
uv run --frozen python contracts/run_openapi_gate.py \
  --env-file workspaces/decision-platform/spring-api/build/openapi-fixture/openapi.env

cd workspaces/decision-platform/python-services
uv run --frozen ruff check app/brokerage tests/brokerage
uv run --frozen mypy app
uv run --frozen pytest -q tests/brokerage
```

## S3-online KIS_MOCK 통합과 exact approval

S3-online은 S3.1 REST route를 기본 OFF인 loopback Brokerage gRPC에 연결한다. Spring은 DB에서
owner/account anchor와 Decision/Kill Switch/one-use를 먼저 검증하고 durable reservation을
만든 뒤에만 Python에 전달한다. Python은 공식 KIS Mock origin과 exact mock TR만 허용하고,
S1.1 Redis limiter와 deployment-global tokenP limiter를 재사용한다. 성공 접수는 `ACCEPTED`,
모호한 transport 또는 durable outcome 확인 실패는 `PENDING_RECONCILIATION` 기록을 시도하며
같은 주문을 자동 재전송하지 않는다. 이 보조 기록도 실패하면 최초 `SUBMITTED` reservation이
recovery anchor로 남는다.

기본 `.env`는 다음 불변식을 유지한다.

- `BROKERAGE_GRPC_ENABLED=false`
- `KIS_MOCK_BROKERAGE_ONLINE_ENABLED=false`
- `KIS_MOCK_BOUND_ACCOUNT_ID`는 승인된 단일 opaque mock account와 credential이 준비되기 전까지 비움
- `KIS_LIVE` 주문·정정·취소용 enable flag와 TR allowlist는 없음
- `KIS_BROKERAGE_TOKEN_P_PHYSICAL_CAP`과 `KIS_BROKERAGE_PHYSICAL_CAP`은 승인 없이는 비움
- `S3_KIS_MOCK_EXACT_APPROVAL_ID`와 `S3_KIS_MOCK_EXACT_APPROVAL_SHA256`은 승인 없이는 비움

일반 개발·테스트에서는 이 값을 바꾸지 않는다. online gRPC server는 numeric loopback,
shared secret, finite cap, Fernet reference key와 `KIS_MOCK_BOUND_ACCOUNT_ID`를 모두 검증하며
모든 RPC의 opaque `accountId`가 이 값과 일치할 때만 reader/gateway를 호출한다. reflection은 열지 않는다.
raw 계좌번호와 provider 주문번호는 Spring/DB/응답으로 보내지 않고 취소에 필요한 reference만
owner/order에 결속한 bounded-TTL ciphertext로 Redis에 저장한다. 주문 send 전에 encrypted
`PENDING` marker를 `SET NX`로 확정하고 접수 reference를 `COMMITTED`로 원자 전환한다.
전환 실패 시 in-memory reference로 전량취소를 retry 없이 최대 1회 보상한다.

실제 KIS_MOCK 최종 검증은 `probeType=FULL`인 다음 순서를 바꿀 수 없는 exact packet으로만
수행한다.

1. 잔고 source-shape 진단 `VTTC8434R`
2. 매수가능 `VTTC8908R`
3. 지정가 매수 1주 `VTTC0012U`
4. 전량 취소 `VTTC0013U`
5. 최근 체결조회 `VTTC0081R`

packet은 최종 local/remote HEAD, PR #55 required CI 성공, 같은 HEAD의 fresh clean security
report digest, Redis PTTL baseline, symbol/price/date/account/order opaque identity, 물리 cap
`tokenP=1`/`brokerage=5`, retry 0, artifact 0, 60분 이하 TTL을 결속한다. `/tmp` 아래 owner
regular file mode `0600`으로만 발급하고, 현재 사용자가 packet의 exact approval ID와 SHA-256을
별도 승인한 뒤 packet 안의 `executionCommand` 그대로 한 번 실행한다. approval latch가
없거나 다르거나 만료되면 provider handoff 전에 종료한다. packet 검증 뒤에는 runtime 생성 전에
tracked/untracked/staged 변경이 없는 clean worktree와 packet account가
`KIS_MOCK_BOUND_ACCOUNT_ID`에 정확히 결속됐는지도 확인한다. ignored `.env`와
Git ignore 규칙에 포함된 로컬 전용 파일은 clean 판정에서 제외한다. 이어서
`approvalId`와 packet SHA-256에서 파생한 opaque Redis key를 `SET NX PX`로 claim하며,
성공·첫 실패·runtime 생성 실패 모두 해당 packet을 재사용할 수 없다. Redis 장애나 기존 claim도
provider handoff 전에 fail-closed한다.

`FULL` 실패 원인을 stable 출력으로 좁힐 수 없으면 같은 packet이나 5단계를 다시 실행하지 않는다.
새 final HEAD/CI/security evidence에 결속한 `probeType=BALANCE_DIAGNOSTIC`,
`steps=["balance"]`, cap `tokenP=1`/`brokerage=1`, retry/artifact 0 packet과 새 exact 승인을
발급해 production transport·limiter·balance parser만 1회 실행한다. 이 profile은 Fernet
reference key, 주문 gateway, 취소, 체결조회를 만들지 않는다. 실패 JSON은 allowlisted
`reasonCode`, 선택적 `httpStatus`, `[A-Z0-9_-]{1,32}` `providerCode`만 출력하고 provider
body/header/URL/`msg1`/계좌/credential은 출력하지 않는다. diagnostic도 single-use이며,
성공해도 최종 5단계에는 또 다른 새 `FULL` packet과 현재 사용자의 새 exact 승인이 필요하다.
이 balance 단계는 cash/equity/position source shape만 검증하며 margin requirement나
gold ETF/ETN 분류를 합성하지 않는다. trusted margin/catalog enrichment가 없는 persistent
online balance projection은 provider 호출 전에 `BALANCE_RISK_FIELDS_UNAVAILABLE`로 닫힌다.

첫 실패는 남은 호출을 모두 중단한다. 주문 접수 뒤 취소가 실패해도 자동 retry하지 않으며,
모의투자 포털에서 확인·정리가 필요하면 실패 evidence를 고정한 뒤 새 authorization을 받는다.
probe 성공은 background polling, gRPC 상시 활성화, S3.3 fill observation append 또는
KIS_LIVE 실계좌 주문 권한이 아니다.
마지막 `executionRead`는 source-shape/readability 확인이다. provider가 즉시 취소 주문 row를
아직 반환하지 않거나 sparse matched row만 반환해도 probe는 fill이나 대사 snapshot을 꾸미지
않으며, strict reconciliation reader는 실제 row가 정확히 하나이고 필수 field/invariant가 맞을
때만 사용한다.

KIS_MOCK online response는 credential scrubber가 provider echo를 제거하기 전에 1 MiB cap을
적용한다. 더 큰 body, 과도한 JSON depth/list/text는 stable error로 축약하며 raw provider body,
header, 계좌번호, credential은 로그·DB·응답에 남기지 않는다.

## S3.3 체결 관측·대사

S3.3은 KIS_MOCK offline fill observation과 S3.2 INTERNAL_PAPER 결정적 fill을 같은 주문 수량
보존식으로 대사한다. ADMIN reconcile은 저장된 COMPLETE 관측만 최대 200개씩 처리하고,
owner fills 조회는 KST 31일·50개 page·HMAC cursor로 제한한다. S3-online 체결조회 parser는
위 exact probe의 source-shape read 또는 별도 strict reconciliation reader로만 존재하며
provider polling, scheduler, fill writer append, 실계좌와 실주문 호출은 없다.

`decision_fill_writer`는 `.env.example`의 별도 password로 bootstrap하며 sanitized observation
INSERT만 수행한다. 기존 PostgreSQL volume은 삭제하지 않고 루트 README의 one-time role
bootstrap을 재실행한 뒤 V14 migration을 적용한다. offline writer와 왕복 HTTP 절차는
`spring-api/http/s3-3-offline-roundtrip.http`, 계약과 exact bounds는 루트
`contracts/README.md`의 S3.3 절을 따른다.

## S2.3 offline golden path

아래 절차는 개발용 loopback PostgreSQL에 repository의 sanitized fixture만 append한다.
`DECISION_*_DATABASE_DSN`은 각 exact non-superuser role을 가리켜야 하며 production DB DSN을
사용하지 않는다. offline writer CLI는 `DECISION_SOURCE_WRITER_OFFLINE_TARGET=local|offline|test|testcontainers`
가 명시되고 DSN current role/privilege attestation을 통과해야 append한다. provider credential은
필요하지 않고 provider/live/account/order/broker call은 모두 0이다. Spring/Python process에는
루트 `.env`의 필요한 값을 IDE나 local secret manager의 env-file 기능으로 주입하되 파일 내용을
shell command로 출력하거나 추적 파일에 복사하지 않는다.

먼저 루트에서 infrastructure와 role을 준비한다. 기존 volume도 삭제하지 않는다.

```bash
docker compose --env-file .env -f infra/docker-compose.infra.yml up -d
docker compose --env-file .env -f infra/docker-compose.infra.yml exec -T postgres \
  bash /docker-entrypoint-initdb.d/02-application-roles.sh
```

첫 번째 terminal에서 Spring을 시작한다. startup Flyway가 V9까지 적용하고 실제
`decision_app` runtime graph를 사용한다.

```bash
cd workspaces/decision-platform/spring-api
./gradlew bootRun
```

두 번째 terminal에서 V9 적용을 확인한 뒤 fixture를 append하고 loopback gRPC를 시작한다.
각 command는 저장된 row 수와 source 이름만 출력하며 fixture 원문이나 DSN을 출력하지 않는다.

```bash
cd workspaces/decision-platform/python-services
uv sync --frozen
export DECISION_SOURCE_WRITER_OFFLINE_TARGET=testcontainers
uv run --frozen decision-market-quote-append tests/fixtures/decision/market_quote.v1.json
uv run --frozen decision-instrument-catalog-append tests/fixtures/decision/instrument_catalog.v1.json
uv run --frozen decision-kis-mock-portfolio-append tests/fixtures/decision/kis_mock_portfolio.v1.json
uv run --frozen decision-deterministic-metrics-append tests/fixtures/decision/deterministic_metrics.v1.json
uv run --frozen decision-corporation-registry-append tests/fixtures/decision/corporation_registry.v1.json
uv run --frozen decision-disclosure-grpc
```

세 번째 terminal 또는 IDE HTTP client에서 `spring-api/http/auth.http`를 순서대로 실행한다. 이
smoke는 `demo-user` 로그인 → 원칙 생성 → KIS_MOCK 평가 → owner detail/audit 조회를 수행한다.
fixture 시각이 현재 evaluation freshness window 밖이면 결과가 persisted 200 HOLD인 것이
정상이며, 이를 현재값이나 성공 주문으로 해석하지 않는다. 종료할 때 Spring/gRPC를 중지하고
필요하면 `docker compose --env-file .env -f infra/docker-compose.infra.yml down`으로 container만
내린다. volume 삭제와 production seed는 이 절차에 없다.

## S1.6 offline 구현 경계

PR #34는 `testcontainers[postgres]==4.14.2`, S1.4X intentional reference refresh와 Market
Calendar/Event Aggregator v1 내부 계약을 먼저 동결했다. 후속 offline 구현은 strict source
registry, XKRX/KIS holiday/KASI XML/KIS KSD/OpenDART adapter, canonical merger와 immutable
revision/audit 저장, PostgreSQL quota reservation, DS001 pagination, DS004 privacy projection,
disclosure state와 `decision_collector` 최소권한을 추가한다. 기존 V4 `market_calendar` seed는
V6에서 `trading_sessions`로 이관하고 같은 이름의 read-only compatibility view로 제공한다.

운영 bootstrap에는 `POSTGRES_COLLECTOR_PASSWORD`가 필요하다. OpenDART online에는 코드
기본값이 없는 `OPENDART_DAILY_CALL_LIMIT`, `OPENDART_DAILY_CALL_BUDGET`,
`OPENDART_MAX_CALLS_PER_RUN`, `OPENDART_MAX_SYMBOLS_PER_RUN`이 모두 필요하지만, 값 주입은
online 활성화나 provider 호출 승인이 아니다. KASI는 `networkReady=false`, provider 호출은 0이며
public REST/gRPC/Dashboard도 별도 contract-change 전까지 제공하지 않는다. 계약의 공개 단일 진실
소스는 최종 명세 11.1.2와 API 명세 12A다.

```bash
cd python-services
uv run --frozen pytest -q tests/data/calendar tests/data/opendart
```

## S1.3G active 뉴스 source 경계

Naver provider/runtime/storage 권한은 2026-07-31 계약에서 퇴역했다. tracked runtime 제거와
승인된 로컬 snapshot의 exact 삭제는 해당 contract-only PR 병합 뒤 후속 구현 wave에서 수행한다.
새 active 계약은 기사 metadata를 저장하지 않는 GDELT aggregate synthetic fixture와
`AVAILABLE | ABSTAIN` semantics만 허용한다. 실제 GDELT outbound, RiskDecision/hash/order 권한,
S5 feature 주입은 모두 0이다. Decision Platform이 producer 경계를 소유하고 Return Engine은
별도 합의된 public artifact만 미래에 소비할 수 있다.

## S1.3 ECOS/Naver snapshot — HISTORICAL_SUPERSEDED

아래는 신규 실행 지침이 아니라 과거 감사 기록이다. S1.3 ECOS 거시지표와 Naver 뉴스 metadata collector는 PR #16 merge commit
`6f439155d9f5ec626fc185f29f2e0bd64ca54780`으로 `main`에 병합됐다. A4 metadata preflight와
의미 승인, B1 atomic ECOS+Naver smoke가 완료됐으며 accepted set은 성공한 A4+B1의
ECOS `6`+Naver `1`=`7` physical attempts다. A1/A2/A3 실패 evidence나 프로젝트 lifetime
호출량과 합산하지 않는다. 기사 본문 fetch, public REST/gRPC, DB/Flyway 변경은 포함하지 않는다.

## KRX universe 자동화

S1.3K는 PR #17 merge commit `814aab377251d76672566d39c3edb379d132248e`으로 `main`에
병합됐고 해당 merge 시점의 Contracts CI, Kotlin Build, Repo Hygiene가 모두 성공했다. KRX OPEN
API의 `유가증권 일별매매정보`와 `코스닥 일별매매정보`만 사용해
내부 top-30 universe를 만든다. 운영 계정은 31개 서비스 entitlement를 모두 승인받았지만
runtime allowlist는 NOW 두 개로 고정한다.

아래 KRX1~10 기록은 완료 상태가 아니라 recovery history다. KRX1은 physical `0`·Redis `0→1`, KRX2는 첫 NOW
endpoint handoff 뒤 원인 미분류 `collection_failed`로 physical `1`·Redis `1→2`, KRX3는 같은
첫 handoff의 `authentication_failed(401_or_403)`로 physical `1`·Redis `2→3`을 기록했고 online 성공
산출물은 없다. KRX4는 HEAD `971ea39418ba`, 기준일 `2026-07-15`의 첫 handoff에서
`transport_unavailable`, physical `1`, Redis `3→4`로 중단했고 KOSDAQ·retry·online artifact는 `0`이다.
예약 뒤 `5.279초`라 당시 5초 read timeout 가능성이 가장 높지만 예외 타입이 소실되어 확정하지
않는다. 별도 sanitized RCA evidence SHA-256은
`30326a713ab1c638a2897412ccb50dc3fce44408e73ef47a1ad4db3d9b468033`이다. KRX5는 HEAD
`9d2dcdea937d`, 같은 기준일의 첫 endpoint에서 HTTP `200` 뒤 기존 validation 관측성 축약의
`invalid_response`, physical `1`, Redis `4→5`로 중단했고 KOSDAQ·retry·online artifact는 `0`이다.
failure/RCA SHA-256은 각각 `969711e95c12fdd4e51bc1a3fdbaa7983f36c5c46d622cbe406b3b7775d217b4`와
`d08eac2d2c443f39b1ff940ccea7fefe130775ce76c7d789375b45e64c16ca56`이다. 공식 성공 shape는
strict parser가 그대로 수용하므로 계약을 느슨하게 하지 않고 media/body/JSON/envelope/row typed
diagnostic만 보강했다. KRX6/7은 TTL 만료로 provider `0`이다. KRX8은 HEAD `4783432ad7de`,
첫 endpoint `read_timeout`, physical `1`, Redis `5→6`이며 KOSDAQ·retry·online artifact는 `0`이다.
failure/RCA SHA-256은 `a2547290e39fe63c1ceda9171beb4dd701c9db8938182e6e93d08e5aacf23dca`와
`53bc9d4e001b839fe2692be61271f5954fc9e9703b6f94501ac782b128029d62`이다. KRX9는 TTL 만료로
provider `0`이며 packet SHA-256은 `7aae38e0cc3b721557d93ca16fdd4576f890b2d257b743bab953c01a33304364`다.
KRX10은 HEAD `cd212c8e22ac`에서 KOSPI probe를 1회 실행해 HTTP `200`, physical `1`, Redis rolling
`3→4`까지 도달했지만 row `10`의 `ISU_CD`를 숫자 6자리로만 제한한 `row_symbol_invalid`에서
중단했다. KOSDAQ·final refresh·retry·파일 생성은 `0`이고 failure evidence SHA-256은
`3acefd3b5f772050b58ece93397db1123f4412355fe5bf3982385e8e639bd320`이다.
KRX1~5/KRX8/KRX10 실패, KRX6/7/9 만료 packet, 기존 S1.3 A4/B1 승인은 재사용하지 않는다.

공식 KRX source `ISU_CD`는 exact ASCII uppercase alphanumeric 6자 `[0-9A-Z]{6}`로 검증하고
영문 포함 행도 row count·중복검사·canonical hash에 포함한다. 기존 KIS/Naver manifest와
positive candidate/top-30은 exact 숫자 6자리 `[0-9]{6}`만 허용한다.

KRX11 `approval-krx11-81aed4c1fad6-20260716T122917Z`는 실행 HEAD `81aed4c1fad6`, 기준일
`2026-07-15`에서 `KOSPI probe 1 → KOSDAQ probe 1 → full refresh 2`를 순서대로 성공했다.
KOSPI는 row `944`·양수 후보 `887`·SHA
`4f8e4849ac655598d0bb1ce736d7c0ff4436168eeb232c7bfa2364ee830cfda6`·`11,943ms`,
KOSDAQ은 row `1,821`·후보 `1,690`·SHA
`cc2ae17c110196c2daeaa73c1592930d76a2821addab5068c2bd963d5b0350c7`·`14,019ms`를
기록했다. final refresh는 physical `2`로 두 시장을 다시 검증해 source SHA
`f23bbd75c55121c65351fa10f47a86871a8e0082a03cab3df8e816527e18c9d1`, manifest/report
SHA `ed979913de7415146cbb56df97bdf4eddeec3c21bc4792f4c03d802c7596674e`/
`625caa61ab8cb5382b5da7acc84741f38c1cab5dc2edb1ff2901108c27dc8671`, 30종목, rank 1
`005930/삼성전자`를 게시했다. Redis는 `4→8`, retry·추가 호출·cooldown은 `0`이다. success
evidence와 소비 완료 packet SHA는
`57d66380e2a86c928bf21a69d9e626fa697d487cf878378558aa26959e3f64c9`/
`58dc47bf96f644b634d76cec6bd08caedd06cc1c8e829419e6d9bf6f49492619`다.
완료 뒤 KRX `313`, S1.3 matrix `892`, 전체 Python `1086` 테스트와 Ruff, mypy `78` source
files, lock, contracts, JDK 25 Gradle build, Compose, repo hygiene, gitleaks를 모두 통과했다.

이후 online 실행도 probe timeout `2/120/2/1초 + logical 130초`, full refresh
`2/120/2/1초 + shared logical 260초`, retry `0` 계약을 유지한다. 각 프로세스 cap은 `1/1/2`이고
합계 `4`는 approval packet과 executor stop rule이 강제한다. final HEAD·기준일·명령 순서·발급
직전 Redis rolling baseline·TTL에 결속한 새 exact 승인을 받은 경우에만 다음 세 명령을 순차 실행한다.

```bash
cd python-services
uv run krx-openapi-service-probe --online --as-of YYYY-MM-DD --service stk_bydd_trd
uv run krx-openapi-service-probe --online --as-of YYYY-MM-DD --service ksq_bydd_trd
uv run krx-openapi-universe-refresh --online --as-of YYYY-MM-DD --data-dir data/kis
```

probe는 service·기준일·row/양수 후보 수·canonical SHA-256·elapsed ms·physical `1`만 출력하고
manifest/report를 쓰지 않는다. `--online`은 로컬 안전 gate일 뿐 사용자 실행 승인을 대체하지 않는다.
API 실패를 CSV나 이전
manifest 성공으로 바꾸지 않으며, 수동 CSV는 기존 `kis-universe-refresh`를 별도 명령으로 실행할
때만 사용한다. ASCII `YYYY-MM-DD`와 approved ignored `data/` root 내부 output만 허용한다.
성공·실패 출력에는 caller argv·로컬 경로 대신 안정 code, physical attempt 수와 검증된 allowlist
typed diagnostic scalar만 남고,
client cleanup이 성공한 뒤에만 서로 다른 report와 manifest target이 게시된다. 병합된 S1.3K
구현 범위에는 주기 scheduler가 포함되지 않는다.

## 외부 provider 반복 실패 복구

같은 단계가 반복 실패하거나 stable code만으로 원인을 좁힐 수 없으면 전체 명령을 다시 실행하지
않는다. 실패 packet/evidence를 소비 완료로 동결한 뒤 `focused regression → allowlisted typed
leaf → 최소 수정 → 관련 matrix → 전체 gate → fresh approval` 순서로 진행한다. production
transport/parser/quota를 그대로 재사용할 수 있을 때만 endpoint별 no-publish probe를 retry `0`,
physical cap `1`, artifact `0`으로 순차 실행하고 첫 실패 뒤 남은 호출을 만들지 않는다.
fresh packet은 현재 사용자의 exact 승인을 받은 뒤에만 소비하며 승인 전 provider 호출은 `0`이다.

probe 성공은 최종 artifact가 아니다. 최종 production 명령이 현재 응답 전체를 독립적으로 다시
검증하고 성공한 뒤에만 원자 게시한다. probe와 final hash 일치는 요구하지 않으며, 실패 evidence와
성공 acceptance set은 분리한다. direct `curl`, 브라우저 sample, 임시 credential script로
fixed-origin transport·quota·승인 gate를 우회하지 않는다.
