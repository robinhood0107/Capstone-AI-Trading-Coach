# Pre-S5 OA112·global-news contract lock

상태: `CONTRACT_ONLY / RAG_AND_GLOBAL_NEWS_CONTRACT_LOCKED`

## KR: 변경 이유와 범위

이 변경은 historical S4.7D OA112/OA140 metadata와 기존 v1 contract bytes를 바꾸지 않고,
Pre-S5의 logical OA112, RAG v2 consent/import, foreign-news explanation lane, Optional 3의
실행 전 경계를 additive contract로 잠근다. 현재 corpus materialization, provider transport,
adapter, raw data persistence, owner import writer는 모두 열리지 않는다.

Decision Platform만 이 범위의 future runtime owner다. 다른 workspace의 diff와 새 외부 산출물
의존성은 만들지 않으며, 부재한 입력은 기존 `NOT_AVAILABLE` 또는 `ABSTAIN` 경계를 따른다.

### 잠긴 결정

1. `OA112_ACTIVE_CONTRACT_LOCKED`는 정확히 14 track × 8 = 112의 logical selection이다.
   `S4_7D_OA112_PHYSICAL_ACTIVATION=NOT_MATERIALIZED`이므로 source URL, raw corpus, raw hash,
   extracted text, chunk, embedding, cache를 새로 수집하거나 추적하지 않는다.
2. reserve research는 최대 28개이며 `automaticPromotion=false`다. source별
   `machineFetchAllowed`, `localProcessingAllowed`, `externalEmbeddingAllowed`,
   `externalGenerationAllowed`와 actual rights evidence가 모두 true가 되기 전에는 active
   generation으로 전환할 수 없다.
3. RAG v2 addendum은 existing ask/status/history bytes를 digest로 bind하고 consent,
   effective-consent, 300초 single-use owner-bound import ticket, deletion activation, embedding
   profile policy만 추가한다. ticket/API/BAT command line에는 raw path, JWT, owner ID, DB credential을
   담을 수 없다.
4. Voyage target은 `voyage-context-4` 1024차원이고 runtime environment variable은
   `VOYAGE_API_KEY` 하나뿐이다. Files/Batch API, retry, partial/mixed profile, query-level fallback은
   금지한다. unavailable 상태의 대안은 exact-30+OA112+owner bundle 전체를 BGE-M3로
   rebuild·evaluate한 뒤 CAS로 전환하는 경우뿐이다.
5. Vertex target은 ADC/service-account `gemini-3.5-flash` 단일 generator다. top-5 evidence,
   question당 `generateContent` 1회, fallback 0을 고정한다. Developer API, OpenAI, other LLM,
   reranker, verifier, tools/functions, Search/Maps, file upload, session resumption, context cache,
   retry는 허용하지 않는다.
6. foreign-news lane은 Finnhub personal-local, SEC official, Federal Reserve official, existing
   GDELT offline-reference의 정확히 네 개다. GDELT HTTP transport/executor/outbound와 새 adapter는
   0이다. Finnhub shared/hosted key mode는 없으며, raw provider data와 article metadata를 저장하지
   않는다.
7. foreign-news response는 아래 불변식을 정확히 유지한다.

```text
decisionAuthority=NONE
allowedUses=[EXPLANATION_ONLY]
s5FeatureEligible=false
riskDecisionHashIncluded=false
rawProviderDataStored=false
articleMetadataStored=false
```

8. Optional 3 source family는 `FINNHUB_OPTIONAL3`, `TWELVE_DATA`, `MASSIVE`만 허용한다.
   checked-in entitlement와 approval/receipt는 template 또는 disabled/blocked 상태이고 physical
   call, retry, raw persistence는 0이다. 이 집합은 Core 6 exact set을 넓히지 않는다.
9. historical OA112/OA140 manifest, existing v1 OpenAPI/proto/source-card, exact-30,
   `news_sentiment_summary.v2`는 byte-stable하게 보존한다.

### 새 contract와 검증

| 영역 | 추가 artifact |
|---|---|
| RAG/global-news SSOT | `catalogs/pre-s5-rag-news-contract.v1.json` 및 digest manifest |
| OA selection | logical selection, reserve registry, source-card v4 permission schemas |
| RAG v2 | consent/effective-consent/import/status/policy schemas와 additive OpenAPI |
| foreign news | entitlement, sentiment, model-selection schemas와 additive OpenAPI |
| Optional 3 | entitlement, approval, receipt schemas |
| verification | deterministic generator, semantic validation, positive/negative fixtures, CI generated check |

재현 명령은 provider/account/order physical call 또는 raw corpus download를 만들지 않는다.

```bash
uv run --frozen python contracts/generate_pre_s5_rag_news_contracts.py --check
uv run --frozen python contracts/validate.py
uv run --frozen python -m unittest contracts.tests.test_pre_s5_rag_news_contracts -v
uv run --frozen python contracts/verify_pre_s5_doc_truth_freeze.py --check
```

## EN: Rationale and scope

This additive change locks the Pre-S5 logical OA112, RAG v2 consent/import, foreign-news
explanation lane, and Optional 3 pre-execution boundary without modifying historical S4.7D
OA112/OA140 metadata or existing v1 contract bytes. Corpus materialization, provider transport,
adapters, raw persistence, and the owner import writer remain inactive.

Decision Platform is the sole future runtime owner for this scope. No other workspace is changed,
no external deliverable dependency is created, and unavailable inputs retain the existing
`NOT_AVAILABLE` or `ABSTAIN` boundary.

The contract fixes a logical 14 × 8 OA112 selection with at most 28 non-promoting reserve records;
all four source permissions plus actual rights evidence are required before any physical activation.
The RAG addendum binds legacy ask/status/history bytes and adds only consent, effective-consent,
a 300-second single-use owner-bound import ticket, deletion activation, and profile policy.

Voyage is fixed to `voyage-context-4` at 1024 dimensions with one runtime key name and no Files,
Batch, retry, mixed profile, or query fallback. Its only unavailable-state alternative is a full
bundle BGE-M3 rebuild/evaluation/CAS transition. Vertex is fixed to ADC/service-account
`gemini-3.5-flash`, top-five evidence, one `generateContent` call per question, and no alternate
generator or auxiliary model/service call.

Foreign news has exactly four lanes: Finnhub personal-local, SEC official, Federal Reserve
official, and the existing GDELT offline reference. It remains explanation-only with no decision,
signal, risk, order, hash, or S5 feature authority; GDELT transport/executor/outbound and new
adapter capacity remain zero. Optional 3 contains only Finnhub Recommendation/Earnings, Twelve
Data, and Massive contract templates and does not expand the exact Core 6 set.

All historical OA112/OA140 manifest bytes, existing v1 OpenAPI/proto/source-card bytes, exact-30,
and `news_sentiment_summary.v2` remain byte-stable. The listed reproducible commands perform
contract checks only and make no provider, account, order, raw-download, or data-persistence call.
