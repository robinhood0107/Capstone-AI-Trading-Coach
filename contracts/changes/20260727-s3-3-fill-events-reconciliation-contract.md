# S3.3 체결 이벤트·대사 계약 변경

## KR

이 변경은 저장된 sanitized 체결 관측을 주문 상태와 append-only 이벤트로 반영하고,
주문/체결 보존식을 대사하는 S3.3 경계를 잠근다. 공개 API는 ADMIN 전용 reconcile 1개와
owner-scoped fills 조회 2개뿐이다. 클라이언트가 체결을 주장하는 route, provider polling,
실계좌 조회·주문, 스케줄러는 포함하지 않는다.

canonical catalog는 `s3-3-fill-contract/v1`이며 SHA-256은
`d76cd087592e4a9f0a87a9d0213836cbcdd20acd2723815ac762def2b9ef61b4`다.
OpenAPI는 같은 ID와 digest를 `x-s3-3-contract-id`,
`x-s3-3-contract-sha256`으로 노출한다.

### 잠긴 결정

1. **P0-11 — 체결 authenticity**
   - KIS_MOCK 체결 이벤트는 `decision_fill_writer`가 append한
     `order_fill_observations`의 COMPLETE row를
     `apply_stored_order_fills`가 소비할 때만 생긴다.
   - INTERNAL_PAPER는 S3.2 제출 transaction의 결정적 full fill을 재사용하며 사후 체결을
     다시 만들지 않는다.
   - `report-fill` 또는 `fill-observation` public route는 0개다. USER와
     `decision_app`은 관측 row를 직접 쓰거나 체결 이벤트를 만들 수 없다.

2. **P0-12 — append-only 관측 source**
   - V14는 `order_fill_observations`를 만들고
     `(order_id, provider_exec_ref_hash)`를 UNIQUE로 고정한다.
     provider exec 원문 대신 lowercase SHA-256/HMAC 64-hex만 저장한다.
   - exec type은 `PARTIAL_FILL | FILL | CANCELLED | REJECTED`,
     completeness는 `COMPLETE | PARTIAL`, schema version은 `1`이다.
   - `decision_fill_writer`에는 INSERT만 부여한다. `decision_app` 직접
     SELECT/INSERT와 모든 application role의 UPDATE/DELETE/TRUNCATE는 금지한다.
     FORCE RLS, PUBLIC revoke, schema/flyway 권한 0을 유지한다.
   - offline fixture는 최대 4 MiB/10,000개, text 128자이며 unknown key, raw provider
     reference, 범위 밖 숫자와 잘못된 시각을 거부한다.

3. **P0-13 — 단일 수량 보존식**
   - `orders`에 `filled_quantity`, `leaves_quantity`,
     `unfilled_terminated_quantity`, `average_fill_price_krw`를 additive하게 추가한다.
   - 모든 상태에서 단 하나의 산술 불변식
     `filled + leaves + unfilled_terminated = quantity`를 DB CHECK로 강제한다.
   - 종료 상태 leaves=0, FILLED 수량=주문 수량, filled=0과 평균가 NULL의 동치도
     별도 presence/state CHECK로 강제한다. 수수료·세금·정정·부분취소·다중 leg는 추가하지 않는다.

4. **P0-14 — 순수 전이와 원자 적용**
   - Kotlin `OrderFillTransition`은 `Applied | Duplicate | Invalid`만 반환한다.
     같은 누적수량은 no-op Duplicate, 역행/초과/종료 후 전이는 Invalid다.
     `CANCEL_REQUESTED -> FILLED` race만 허용한다.
   - Invalid는 버리지 않고 `INVALID_TRANSITION` 이벤트와 sanitized audit로 남기며 주문
     projection은 바꾸지 않는다. Duplicate는 이벤트를 만들지 않는다.
   - ADMIN 현재 권한 재검증, advisory transaction lock, order row lock, 최대 200개 관측,
     event append, order projection, reconciliation, audit, outbox를 한 transaction에서 처리한다.
   - 평균가는 `sum(fillQuantity * fillPriceKrw) / sum(fillQuantity)` 정수 내림으로 재계산한다.
     Kotlin 예상 결과와 SQL 결과가 다르면 transaction을 503으로 fail-closed한다.

5. **P0-15 — 대사 상태와 자동 수정 금지**
   - 주문 status enum은 늘리지 않는다. 별도 `reconciliation_status`는
     `NOT_APPLICABLE | MATCHED | MISMATCH`, `reconciled_at`은
     NOT_APPLICABLE일 때만 NULL이다.
   - 관측 수량, 단일 보존식, 재계산 평균가, provider final 평균가가 모두 일치할 때만
     MATCHED다. 하나라도 다르면 MISMATCH warning/audit를 남긴다.
   - 대사는 관측이나 주문값을 임의 보정하지 않는다.

6. **P0-16 — ADMIN reconcile**
   - `POST /api/v1/brokerage/orders/{orderId}/reconcile`은 ADMIN 전용이며
     `X-Idempotency-Key`가 필수다. body는 empty 또는 `{}`만 허용한다.
   - 필터와 method authorization 뒤 transaction 안에서 DB의 현재 role/status/
     security version을 다시 확인한다. 응답은 account/owner/raw provider 값을 제외한
     sanitized projection이다.
   - 한 번에 최대 200개를 처리하고 남은 COMPLETE 관측이 있으면 `hasMore=true`다.
     ShedLock, `@Scheduled`, background polling은 없다.

7. **P0-17 — owner-scoped fills**
   - 다음 두 route를 구현한다.
     - `GET /api/v1/brokerage/mock/accounts/{accountId}/fills`
     - `GET /api/v1/brokerage/paper/accounts/{accountId}/fills`
   - `from`/`to`는 KST inclusive 날짜이고 최대 31일이다. page size는 50이며 정렬은
     `(filledAt DESC, orderId DESC, execRefHash DESC)`다.
   - HMAC cursor는 owner, mode, opaque account, 기간, 마지막 정렬키를 결속하고
     900초 후 만료된다. raw offset, raw owner/account는 cursor에 넣지 않는다.
   - 타인과 미존재 account는 같은 404다. 응답은 exact 9개 fill field와 optional
     `nextCursor`만 포함하며 raw exec/account/provider 값은 노출하지 않는다.

8. **P0-18 — 중복 방어**
   - mock submit, paper submit, cancel, fill apply는 서로 다른 purpose/version HMAC
     scope를 사용한다. Redis key에는 opaque 64-hex identity만 둔다.
   - same key/same payload는 저장 응답을 replay하고, different payload는 409다.
     owner-token compare-and-save/delete, 다른 owner 완료/삭제 거부, 사용자 admission cap,
     bounded replay, DB unique, Decision TTL/one-use를 회귀 테스트로 고정한다.
   - fill observation UNIQUE와 event sequence/receipt가 at-least-once 재입력의 두 번째
     방어가 된다.

9. **P0-19 — offline 기본값과 비범위**
   - 구현·테스트·OpenAPI 생성의 provider physical call은 0건이다.
     fixture writer는 `decision_fill_writer` DSN과 sanitized local JSON만 사용한다.
   - `VTTC0081R`/`VTSC9215R`, WebSocket, `/oauth2/Approval`, live account/order,
     실전 주문·정정·취소, 자동 polling은 별도 exact approval 없이는 실행하거나 추가하지 않는다.
   - V1~V13, 다른 workspace, `active_paper_portfolio_projection`은 변경하지 않는다.

### 계약 산출물과 소비자 영향

| 산출물 | 경계 |
|---|---|
| `catalogs/s3-3-fill-contract.v1.json` | route, mode, ID, bounds, status, cursor의 SSOT |
| `schemas/s3-3-fill-observation.schema.json` | offline sanitized writer fixture |
| `schemas/s3-3-reconcile-response.schema.json` | ADMIN reconcile sanitized data |
| `schemas/s3-3-fill-page.schema.json` | owner-scoped 최대 50개 fill page |
| `openapi/openapi.json` | exact 3 route와 exact 5개 `S33*` component |
| `V14__s3_3_fill_events_reconciliation.sql` | additive columns, source, RLS, definer functions, evidence |

Return Engine과 Experience Dashboard workspace에는 구현 파일을 추가하지 않는다. Dashboard는
향후 이 owner-scoped projection을 계약 소비할 수 있지만 현재 cross-workspace handoff나 push
채널을 활성화하지 않는다.

## EN

This change locks S3.3 as an offline-first, stored-source reconciliation boundary.
Only an ADMIN reconcile endpoint and two owner-scoped fill-list endpoints are public.
Clients cannot report fills, no provider polling is enabled, and no live-account or
live-order authority is introduced.

1. **P0-11:** KIS_MOCK fill events can only be derived from COMPLETE observations
   inserted by the least-privilege fill writer. INTERNAL_PAPER reuses its deterministic
   S3.2 fill. There is no public fill-claim endpoint.
2. **P0-12:** observations are append-only, FORCE-RLS protected, deduplicated by
   `(order_id, provider_exec_ref_hash)`, and contain only a 64-hex execution reference.
   The offline fixture is strictly bounded and rejects raw or unknown provider fields.
3. **P0-13:** every order satisfies exactly one arithmetic conservation equation:
   `filled + leaves + unfilled_terminated = quantity`.
4. **P0-14:** a pure transition returns Applied, Duplicate, or Invalid. Invalid transitions
   become sanitized evidence without mutating the order; duplicates are no-ops. ADMIN
   revalidation, advisory locking, at most 200 observations, events, projection, audit, and
   outbox commit atomically. Kotlin/SQL divergence fails closed.
5. **P0-15:** reconciliation is a separate
   `NOT_APPLICABLE | MATCHED | MISMATCH` field and never repairs observations or orders.
6. **P0-16:** reconcile is ADMIN-only, requires an idempotency key, accepts only an empty
   object, revalidates current DB authority, and returns `hasMore` for bounded continuation.
   No scheduler or ShedLock table is added.
7. **P0-17:** mock and paper fill queries are owner-scoped, KST date-bounded to 31 days,
   limited to 50 records, and use a 900-second HMAC cursor over the exact descending sort.
8. **P0-18:** all four brokerage writes use purpose/version-separated HMAC identities,
   bounded replay, owner-token fencing, admission caps, database uniqueness, and Decision
   TTL/one-use protections.
9. **P0-19:** provider physical calls remain zero. KIS execution polling, WebSocket,
   live-account access, and live trading require a separate exact approval.

The canonical catalog digest is
`d76cd087592e4a9f0a87a9d0213836cbcdd20acd2723815ac762def2b9ef61b4`.
The implementation normalizer requires exactly three S3.3 path/method pairs and exactly
five `S33*` components. No other workspace or historical migration is changed.

## Verification

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
