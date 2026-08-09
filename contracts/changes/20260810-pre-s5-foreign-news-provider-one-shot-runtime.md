# Pre-S5 foreign-news one-shot provider runtime addendum

상태: `IMPLEMENTED_DRAFT / PROVIDER_OUTBOUND=0`

## KR: 범위와 불변식

이 addendum은 기존 `foreign-news-sentiment-v1` response, V49 owner-scoped append/read boundary,
GDELT offline aggregate producer, v1 OpenAPI bytes를 보존한다. Decision Platform Python service에
Finnhub personal-local, SEC official, Federal Reserve official의 local-only one-shot probe를 추가한다.

- 허용 operation은 `FINNHUB_MARKET_NEWS`, `FINNHUB_COMPANY_NEWS`,
  `SEC_OFFICIAL_RELEASES`, `FED_OFFICIAL_RELEASES` 네 개뿐이다. GDELT HTTP transport나 executor는
  추가하지 않는다.
- canonical 0600 short-expiry packet, clean HEAD/tree, CI/security digest, fixed operation,
  `logicalCallCap=1`, `physicalCallCap=1`, `retryCount=0`가 모두 일치하기 전에는 socket을 열지 않는다.
- local SENTiVENT blind test를 정확히 한 번 통과한 선택 모델이 없으면 packet claim이나 provider socket
  전에 `FOREIGN_NEWS_MODEL_NOT_VERIFIED`로 종료한다. 후보 추가, threshold 완화, runner-up test-shopping은
  이 변경에 없다.
- Finnhub는 fixed `finnhub.io`의 Market/Company News path만, SEC/Fed는 fixed official HTML origin만
  사용한다. DNS rebind/peer mismatch, redirect, non-identity encoding, MIME/size violation, DOCTYPE/ENTITY,
  attachment와 first-failure 후 추가 call은 fail-closed다.
- raw body/header/query, credential, headline/summary, article metadata는 receipt/DB/API에 저장하거나
  Vertex에 전달하지 않는다. official lane의 bounded transient hash/operation locator는 in-memory
  materializer input에만 허용된다.
- actual provider call은 이 tracked change에서 수행하지 않는다. final clean tree의 exact local packet,
  model proof, credential/entitlement evidence가 있을 때만 one call을 허용한다.

## EN: Scope and invariants

This addendum preserves the existing `foreign-news-sentiment-v1` response, V49 owner-scoped append/read
boundary, GDELT offline aggregate producer, and v1 OpenAPI bytes. It adds local-only one-shot probes for
Finnhub personal-local, SEC official, and Federal Reserve official lanes in the Decision Platform Python
service.

- Only four fixed operations are available: Market News, Company News, SEC releases, and Federal Reserve
  releases. No GDELT HTTP transport or executor is introduced.
- A socket opens only after a canonical 0600 short-expiry packet, clean HEAD/tree, CI/security evidence,
  fixed operation, one logical/physical call cap, and zero retry agree. A DNS/connection pre-handoff failure
  seals `NOT_EXECUTED/0`; only a provider handoff consumes physical call count `1`.
- A model must have passed the single selected SENTiVENT blind test before a packet claim or provider socket.
  This change does not add candidates, weaken thresholds, or test another candidate.
- Responses are transiently parsed under fixed-origin, DNS-pinning, peer-check, no-redirect, MIME, size, and
  markup hardening rules. Raw provider data, article metadata, credentials, queries, headers, and Vertex
  forwarding remain absent.
- This tracked change makes zero physical provider calls. A later exact local packet plus current model,
  credential, and entitlement evidence is still required for one call.

## 재현 / Reproduction

```bash
uv run --project workspaces/decision-platform/python-services --frozen pytest -q \
  workspaces/decision-platform/python-services/tests/cross_market/test_foreign_news_provider_probe.py \
  workspaces/decision-platform/python-services/tests/cross_market/test_foreign_news_provider_probe_cli.py \
  workspaces/decision-platform/python-services/tests/cross_market/test_foreign_news_local_evaluation.py
uv run --project workspaces/decision-platform/python-services --frozen python \
  contracts/generate_pre_s5_rag_news_contracts.py --check
```
