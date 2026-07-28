# S3.2 INTERNAL_PAPER 체결 원장 계약 변경

## KR

이 변경은 S2.3 Decision과 S2.4 Kill Switch를 소비하는 별도
`INTERNAL_PAPER` 주문 경로를 고정한다. 공개 범위는
`POST /api/v1/brokerage/paper/orders`,
`GET /api/v1/brokerage/paper/accounts/{accountId}/balances`,
`GET /api/v1/brokerage/paper/accounts/{accountId}/buyable`과 기존 공통 주문 조회·취소
route의 mode 확장이다. KIS gRPC, provider, live account, live order, 체결 조회를 호출하거나
실패 시 paper로 자동 전환하는 경로는 만들지 않는다.

### 잠긴 결정

1. **물리 분리와 자동 전환 금지**
   - paper controller/use case/repository/DB function은 KIS_MOCK gRPC port를 주입받지 않는다.
   - KIS_MOCK 오류, circuit open, Redis 오류, Kill Switch는 paper write를 만들지 않는다.
   - ArchUnit으로 `application.brokerage.paper` → `infrastructure.grpc` 의존을 금지한다.

2. **mode와 identity를 DB에서 상호 강제**
   - `orders.brokerage_mode`는 `KIS_MOCK | INTERNAL_PAPER`만 허용한다.
   - `ord_mock_[0-9a-f]{32}`는 KIS_MOCK, `ord_paper_[0-9a-f]{32}`는
     INTERNAL_PAPER와만 결합할 수 있다.
   - 전역 `orders.decision_id` UNIQUE를 유지해 한 Decision을 두 mode에서 소비할 수 없다.

3. **API와 owner scope**
   - 제출 body exact set은 `decisionId`, exact 8-field `orderIntent`,
     `userAcknowledgement`다. body-supplied mode/account/provider/actor는 거부한다.
   - account는 Decision snapshot의 `portfolio.ownerScopeHash`와
     `paper_accounts.owner_scope_hash`가 일치하는 단 하나의 ACTIVE KRW account로 해석한다.
   - nullable `margin_requirement_krw`는 security-barrier
     `paper_margin_owner_projection`으로만 owner-scoped read를 연다. 값이 없으면 0을 합성하지
     않고 S2.2/S2.3 readiness는 HOLD를 유지한다. 기존
     `active_paper_portfolio_projection`은 변경하지 않는다.
   - 타인 account/order/decision과 미존재 값은 동일한 404 envelope로 닫는다.
   - paper account 생성·충전·삭제 API는 없다. 테스트는 flyway/admin seed, 시연은 S8.3
     `demo_seed`만 사용한다.

4. **저장 가격 source와 체결 산술**
   - V13은 `market_quote_observations.price_krw`를 nullable로 바꾸고
     `previous_close_krw` nullable을 추가하되 둘 중 하나는 양수여야 한다.
   - 기존 row는 `price_krw`를 그대로 유지한다. S2.3 current-price adapter는 null
     `price_krw`를 사용하지 않고 fail-closed한다.
   - offline fixture writer는 `priceKrw | previousCloseKrw` 중 하나 이상과 exact payload를
     저장한다. provider/live 수집 코드는 이번 변경에 포함하지 않는다.
   - 최신 COMPLETE 저장 관측에서 `price_krw`, 다음으로 `previous_close_krw`를 고른다.
     row가 없거나 stale/partial이거나 두 값이 없으면 가격을 합성하지 않고
     `BROKERAGE_UNAVAILABLE` 또는 `DATA_STALE`로 닫는다.
   - MARKET만 기본 5bps를 적용한다. BUY는
     `ceil(base*(10000+bps)/10000)`, SELL은
     `floor(base*(10000-bps)/10000)`이며 정수 exact 연산만 쓴다.
   - LIMIT은 slippage 0이다. BUY `base <= limit`, SELL `base >= limit`에서만 전량
     체결하고, 아니면 `ACCEPTED`, `fill: null`, warning
     `PAPER_LIMIT_NOT_FILLED`, ledger mutation 0건으로 끝낸다.
   - 부분 체결·retry worker·mark-to-market·수수료·세금 모델은 없다.
     `feeModel=NONE_V1`, `valuationBasis=LAST_FILL_PRICE_V1`을 상시 표기한다.

5. **append-only 원장과 재구성**
   - `paper_order_events`의 v1 event type은 `PAPER_ORDER_FILLED` 하나다.
     `(account_id,event_seq)`와 `order_id`는 각각 UNIQUE이며 event sequence는 1부터
     빈틈없이 증가한다.
   - ledger payload exact set은
     `orderId,symbol,side,fillQuantity,fillPriceKrw,fillAmountKrw,priceBasis,
     slippageBps,feeModel,observedAt,beforeCashKrw,afterCashKrw,beforeQuantity,
     afterQuantity,beforeAveragePriceKrw,afterAveragePriceKrw,beforeMarketValueKrw,
     afterMarketValueKrw`이다.
   - 첫 fill의 before state가 admin/demo seed의 opening state를 고정하고, 이후 event의
     before state는 직전 event에서 계산한 after state와 정확히 이어져야 한다.
     계정에 fill이 없으면 order-derived mutation도 없으므로 rebuild 결과는 `NO_EVENTS`다.
   - `paper_accounts`/`paper_positions`는 같은 transaction에서 갱신하는 projection이다.
     `rebuild_paper_state`는 event chain으로 cash/quantity/average price/market value를
     계산해 projection과 비교할 뿐 수정하지 않는다.
   - security review에 따라 `decision_app`에는 rebuild EXECUTE를 주지 않는다. migration
     owner와 Testcontainers 검증 role만 사용할 수 있다.
   - `decision_app`은 paper 3테이블의 직접 SELECT/INSERT/UPDATE/DELETE/TRUNCATE 권한이 없다.
     event table은 어떤 application role에도 UPDATE/DELETE/TRUNCATE를 주지 않는다.

6. **상태와 증거**
   - 즉시 fill은 `PAPER_ORDER_FILLED/FILLED`, LIMIT 미체결은
     `PAPER_ORDER_ACCEPTED/ACCEPTED` order event를 남긴다.
   - ACCEPTED paper cancel은 같은 transaction에서
     `PAPER_ORDER_CANCEL_REQUESTED/CANCEL_REQUESTED`와
     `PAPER_ORDER_CANCELLED/CANCELLED`를 순서대로 append한다. FILLED cancel은 409다.
   - P0의 filled audit exact set과 `feeModel/slippageBps` 기록 요구가 충돌하므로 더
     완전한 합집합으로 잠근다:
     `orderId,decisionId,evaluationId,brokerageMode,status,idempotencyScopeHash,
     fillPriceKrw,fillQuantity,priceBasis,slippageBps,feeModel`.
   - 상태 변화 누락을 막기 위해 `PAPER_ORDER_ACCEPTED`, `PAPER_ORDER_CANCELLED` audit와
     `brokerage.paper-order-accepted.v1`,
     `brokerage.paper-order-cancelled.v1` outbox 분기도 exact key set으로 추가한다.
     `PAPER_ORDER_FILLED`와 `brokerage.paper-order-filled.v1`은 위 filled set을 공유한다.
   - 계좌번호, raw idempotency key, raw provider payload/header, token, 자유서술 provider
     message는 response/event/audit/outbox/log/metric에 넣지 않는다.
   - metric은 `brokerage.paper.fill`, `brokerage.paper.rejected`,
     `brokerage.paper.price_basis`만 추가한다. tag는 닫힌 `reason`/`basis` enum만 허용한다.
     stable fill log는 reference ID, `INTERNAL_PAPER`, price basis만 기록하고 계좌·종목·수량·금액·
     raw key를 기록하지 않는다.

7. **원자성·동시성·권한**
   - lock 순서는 user share → idempotency/decision advisory lock → Kill Switch share →
     paper account update → paper position update → insert/update다.
   - `create_paper_order` 한 transaction이 order, order events, optional fill event,
     account, position, audit, outbox를 함께 commit/rollback한다.
   - V13 precondition은 non-KIS order와 paper account/position/event가 모두 0인지 확인한다.
     하나라도 있으면 명시적 `S3.2 V13 precondition failed`로 중단한다.
   - 모든 definer 함수는 `SET search_path=pg_catalog`, fully-qualified relation,
     capability prepared bind, FORCE RLS, PUBLIC revoke를 사용한다.
   - `decision_app` runtime grant는 decision read, idempotency replay, balance read,
     atomic create/cancel에 필요한 bounded 함수만 허용한다.

8. **멱등성**
   - 기존 HMAC key를 재사용하되 purpose를
     `BROKERAGE_PAPER_ORDER_SUBMIT`으로 분리한다. 새 secret은 추가하지 않는다.
   - PostgreSQL의 durable replay를 최종 진실로 유지하면서, 같은 scope의 동시 진입만 막는
     Redis claim을 30초 TTL로 둔다. key에는 64자리 purpose HMAC scope만, value에는 임의 token과
     request hash만 저장하고 owner/raw key/payload를 넣지 않는다. release는 token+hash가 같은
     holder만 삭제하는 compare-and-delete다.
   - Redis 장애는 `BROKERAGE_UNAVAILABLE`로 fail-closed하며 DB write를 만들지 않는다. claim
     만료는 완료 결과 보존이 아니므로 이후 요청도 PostgreSQL durable replay를 먼저 확인한다.
   - same key/same payload replay, same key/different payload conflict, in-progress conflict,
     다른 owner 동일 raw key 무충돌을 유지한다.
   - mock과 paper의 같은 raw key는 purpose가 달라 충돌하지 않지만, 같은 Decision은 전역
     UNIQUE 때문에 한 mode에서만 성공한다.

9. **고정된 비범위**
   - KIS `VTTC0081R`, `VTSC9215R`, token, balance, order/provider 호출
   - live-order enablement, partial fill, matching queue, scheduled reconciliation
   - public paper account/funding API, fee/tax/impact model, Kafka publish/consumer
   - `active_paper_portfolio_projection` 또는 V1~V12 수정

10. **검증**
    - catalog, five generated JSON Schemas, positive/negative fixtures, generator `--check`,
      OpenAPI path/resource equality를 CI에서 확인한다. implementation normalizer는 S3.2
      contract ID/SHA-256, exact 5개 path/method, exact 9개 `S32*` component를 allowlist로
      검사한다.
    - pure policy, Testcontainers migration/privilege/rebuild/concurrency, API E2E,
      non-exposure, no-fallback, OpenAPI drift, full Kotlin/Python/hygiene gates가 모두
      통과해야 한다.
    - provider physical call count는 0이다.

### S3-online 공통 조회 schema 보강

S3-online은 공통 주문 상세 schema가 KIS_MOCK의
`PENDING_RECONCILIATION`을 읽을 수 있도록 additive하게 확장한다. INTERNAL_PAPER 생성·전이
함수는 이 상태를 만들 수 없고, paper controller/use case/repository의 no-gRPC·no-provider
경계도 바뀌지 않는다. 갱신된 `s3-2-internal-paper-contract/v1` catalog SHA-256은
`d2eea9d27ea066884fa0986c89b3e4932c9293484569dbe45a99005b606f94fe`다.

## EN

This change locks a physically separate `INTERNAL_PAPER` order path consuming the
S2.3 Decision and S2.4 Kill Switch authorities. It adds paper submit, balance, and
buyable routes and extends the shared order query/cancel routes by stored mode.
It never calls KIS gRPC, provider, live-account, live-order, or fill-query
endpoints and never falls back from a failed KIS_MOCK request to paper.

- The request exact set is `decisionId`, the exact eight-field `orderIntent`, and
  `userAcknowledgement`; client-supplied mode/account/provider/actor fields are rejected.
- The account is selected by exact equality between the Decision snapshot owner scope and
  one ACTIVE KRW paper account owner scope. Missing and cross-owner resources share the same
  404 response. A nullable margin requirement is exposed only through a security-barrier
  owner projection; absence remains unavailable rather than becoming a synthetic zero.
- V13 extends stored market observations with nullable `previous_close_krw` and allows a
  nullable last price only when at least one stored price exists. Existing S2.3 reads ignore
  a null current price and remain fail-closed. Only the offline fixture writer is extended.
- MARKET orders use deterministic integer 5bps adverse slippage. LIMIT orders use zero
  slippage and are either fully filled in the submit transaction or stored as ACCEPTED with
  no ledger mutation. There are no partial fills, retry workers, inferred fees, or live calls.
- `paper_order_events` is append-only and records one exact full-fill event per order. Its
  before/after state chain captures the seeded opening state at the first fill and is
  sufficient to recompute every order-derived cash and position mutation. The rebuild
  function compares state but never repairs it and is not executable by `decision_app`.
- Orders, lifecycle events, optional fill event, projections, audit, and outbox commit in
  one database transaction under a fixed lock order and least-privilege SECURITY DEFINER
  boundary.
- Filled evidence uses the union required by the original exact-set and disclosure rules:
  `orderId,decisionId,evaluationId,brokerageMode,status,idempotencyScopeHash,
  fillPriceKrw,fillQuantity,priceBasis,slippageBps,feeModel`.
  Exact ACCEPTED and CANCELLED audit/outbox branches are also present so state changes never
  become unaudited.
- Existing HMAC key material is reused with a distinct
  `BROKERAGE_PAPER_ORDER_SUBMIT` purpose. A 30-second Redis claim stores only the purpose
  scope hash, random holder token, and request hash to suppress concurrent entry; PostgreSQL
  remains the durable replay authority. Claim release is holder-checked and Redis failure
  fails closed before a ledger write. Raw keys, account numbers, provider payloads, headers,
  tokens, and free-form provider messages are never persisted or emitted.
- Metrics use only closed rejection-reason and price-basis tags. Stable logs contain bounded
  reference IDs, mode, and price basis, never account, symbol, quantity, amount, or raw key.
- The implementation OpenAPI normalizer requires the S3.2 contract ID/digest, exactly five
  approved path/method pairs, and exactly nine `S32*` schemas.
- Provider calls, live-order enablement, partial fills, public paper account APIs, fee/tax
  models, Kafka publication, historical migration edits, and changes to
  `active_paper_portfolio_projection` are out of scope.

The additive S3-online update lets the shared order-detail schema read
`PENDING_RECONCILIATION` for KIS_MOCK orders only. INTERNAL_PAPER cannot produce that
state, and its no-gRPC/no-provider architecture is unchanged. The updated canonical
catalog digest is
`d2eea9d27ea066884fa0986c89b3e4932c9293484569dbe45a99005b606f94fe`.

## Verification

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
