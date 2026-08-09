# Pre-S5 foreign-news sanitized runtime

상태: `IMPLEMENTED_DRAFT / PROVIDER_OUTBOUND=0`

## KR: 변경 이유와 범위

Pre-S5 foreign-news 계약의 explanation-only response를 owner-scoped local runtime으로 구현한다.
Flyway V49는 `decision_market_writer`에게 하나의 SECURITY DEFINER append capability만, `decision_app`에게
actor-bound latest-read capability만 준다. base table direct read/write 권한은 없고 RLS를 강제한다.

- `GET /api/v2/market-evidence/{symbol}/foreign-news-sentiment`은 root OpenAPI bytes를 바꾸지 않는 hidden
  direct-payload route다. row가 없으면 fabricated provider state가 아니라 네 lane 모두 `NOT_ACTIVATED`인
  `ABSTAIN`을 반환한다.
- persisted/API payload는 exact `foreign-news-sentiment-v1` shape만 허용한다. `contractId`, authority flags,
  four ordered lane states, symbol, as-of 외 headline, summary, body, article metadata, raw provider data,
  content hash, provenance locator, credential, query, header는 저장하거나 반환하지 않는다.
- 기존 Decision-owned GDELT aggregate는 strict v2 allowlist를 통과한 경우에만
  `GDELT_OFFLINE_REFERENCE` state로 축소한다. GDELT HTTP transport, executor, outbound implementation은
  추가하지 않는다.
- Finnhub personal-local, SEC official, Federal Reserve official transport/parser와 model-weight execution은
  exact local entitlement/evidence/packet이 있을 때의 후속 gated path다. 지금은 provider physical call,
  retry, raw artifact, Vertex forwarding, Decision/Signal/Risk/order/hash authority가 모두 0이다.
- model selection은 정확히 ProsusAI FinBERT, `yiyanghkust/finbert-tone`, Loughran--McDonald baseline만
  validation에서 순서대로 비교한다. selected winner만 test set을 정확히 한 번 평가하고 실패하면
  `ABSTAIN`이며 runner-up test-shopping은 없다.

## EN: Rationale and scope

This implements the Pre-S5 foreign-news explanation-only response as an owner-scoped local runtime.
Flyway V49 grants the market writer one SECURITY DEFINER append capability and the application one
actor-bound latest-read capability; direct base-table access is revoked and RLS is forced.

The hidden direct-payload route returns a four-lane `ABSTAIN` when no sanitized record exists. Both persisted
and API payloads must exactly match `foreign-news-sentiment-v1`; article content, raw provider material,
provenance/content details, credentials, queries, and headers are excluded. Existing GDELT is reduced only from
its strict offline v2 aggregate and no GDELT HTTP path is introduced. Finnhub, SEC, Fed, and model execution
remain exact-evidence/packet-gated with zero provider calls, retries, raw artifacts, Vertex forwarding, or
decision authority in this change.

## 불변식 / Invariants

```text
FOREIGN_NEWS_RUNTIME=IMPLEMENTED_DRAFT
FOREIGN_NEWS_ROUTE_DIRECT_PAYLOAD_ONLY=1
FOREIGN_NEWS_PROVIDER_PHYSICAL_CALLS=0
FOREIGN_NEWS_RETRY_COUNT=0
FOREIGN_NEWS_RAW_PROVIDER_DATA_STORED=0
FOREIGN_NEWS_ARTICLE_METADATA_STORED=0
FOREIGN_NEWS_GDELT_HTTP_TRANSPORT_ADDED=0
FOREIGN_NEWS_DECISION_SIGNAL_RISK_ORDER_AUTHORITY=0
RETURN_ENGINE_FILES_CHANGED=0
EXPERIENCE_DASHBOARD_FILES_CHANGED=0
```

## 재현 / Reproduction

```bash
cd workspaces/decision-platform/python-services
uv run --frozen pytest -q tests/cross_market/test_foreign_news.py tests/cross_market/test_foreign_news_repository.py
uv run --frozen ruff check app/cross_market/foreign_news.py app/cross_market/foreign_news_repository.py
uv run --frozen mypy app/cross_market/foreign_news.py app/cross_market/foreign_news_repository.py

cd ../spring-api
./gradlew ktlintCheck test --tests com.capstone.decision.ForeignNewsSentimentMigrationContractTest \
  --tests com.capstone.decision.application.market.ForeignNewsSentimentServiceTest \
  --tests com.capstone.decision.RagV2ApiIntegrationTest
```
