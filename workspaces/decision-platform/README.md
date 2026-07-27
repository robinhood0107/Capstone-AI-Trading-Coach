# decision-platform

담당: 박종진 (`robinhood0107`)

투자 원칙(Principle) → 평가(Decision/RiskEngine) → 모의 주문(Brokerage) → RAG 설명까지를 담당하는 워크스페이스. Kotlin(Spring) API 서버와 Python(gRPC/FastAPI) 서비스 두 축으로 구성된다.

## 구조

```
spring-api/            # Gradle Kotlin 프로젝트 — Controller/Application/Domain/Infrastructure
python-services/        # uv 프로젝트 — LightGBM/RAG/금융공학/데이터클라이언트/브로커리지 어댑터
```

## 세팅

공개 레포에는 최종 명세/API 계약과 구현 코드를 두고, 상세 개인 참고 노트는 루트의 ignored `private-reference/` 폴더에서만 관리한다. 요약:

1. `cp ../../.env.example ../../.env` 후 PostgreSQL/collector/disclosure-reader/source-writer/Redis password, role별 offline DSN, Spring↔Python gRPC shared secret, JWT issuer/audience, 목적별 JWT/login/credential HMAC key, single-quoted attested demo credential bundle과 필요한 provider secret을 채운다. plaintext demo password는 `.env`에 저장하지 않는다.
2. `docker compose --env-file ../../.env -f ../../infra/docker-compose.infra.yml up -d`로 loopback-only PostgreSQL/Redis를 기동한다.
3. `spring-api/`는 커밋된 Gradle wrapper로 `./gradlew ktlintCheck build`를 실행한다.
4. `python-services/`는 `uv sync --frozen` 후 `uv run pytest`, `uv run ruff check .`, `uv run mypy app`으로 검증한다.

기존 PostgreSQL volume을 유지하는 경우 루트 README의 one-time application role bootstrap 절차를 먼저 따른다. Redis는 password+AOF+`noeviction`이며 OpenDART quota 원장으로는 사용하지 않는다.

KIS outbound는 이 workspace가 단일 owner다. S1.1 client는 실전 18/s hard cap·기본 120ms 간격, 모의 1/s·1,000ms 간격을 같은 opaque credential/appkey scope의 Redis 원자 limiter로 공유한다. `/oauth2/tokenP` physical send는 mock/live 합산 deployment-global 1/s를 보수 적용하고 token cache/singleflight만 mode별로 분리한다. Return Engine과 후속 S1.6/S3 adapter는 별도 limiter를 만들지 않고 이 경계를 재사용한다.

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
stored balance/buyable 조회 경계다. runtime route는 `POST /api/v1/brokerage/mock/orders`,
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
KIS/broker/live account/order provider 물리 호출은 모두 0이다.

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

## S3.3 체결 관측·대사

S3.3은 KIS_MOCK offline fill observation과 S3.2 INTERNAL_PAPER 결정적 fill을 같은 주문 수량
보존식으로 대사한다. ADMIN reconcile은 저장된 COMPLETE 관측만 최대 200개씩 처리하고,
owner fills 조회는 KST 31일·50개 page·HMAC cursor로 제한한다. provider polling, scheduler,
실계좌와 실주문 호출은 없다.

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

## S1.3 ECOS/Naver snapshot

S1.3 ECOS 거시지표와 Naver 뉴스 metadata collector는 PR #16 merge commit
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
