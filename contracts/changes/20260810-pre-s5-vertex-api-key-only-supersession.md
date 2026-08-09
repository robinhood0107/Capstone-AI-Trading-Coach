# Pre-S5 Vertex API-key-only active supersession

상태: `IMPLEMENTED_DRAFT / TARGET_NOT_ACTIVE`

## KR: 변경 이유와 범위

사용자 운영 경로는 GCP project, ADC 또는 service-account 파일이 아닌 API key 하나만 사용한다.
따라서 active Pre-S5 Vertex target을 `VERTEX_API_KEY`의 Vertex Express API-key-only route로
supersede한다. fixed origin은 `https://aiplatform.googleapis.com`이고 fixed path는
`/v1/publishers/google/models/gemini-3.5-flash:generateContent`다. key query parameter는 direct TLS
request target에서만 한 번 구성하며 packet, DB, log, URI abstraction, raw artifact에는 저장하지 않는다.

이 change는 historical OA112/OA140 record, existing v1 OpenAPI/proto/source-card/exact-30 bytes와
2026-08-03 contract-lock record를 수정하지 않는다. Decision Platform만 구현을 소유하며 다른
workspace의 코드 또는 외부 산출물 의존성, Decision/Signal/Risk/order authority는 추가하지 않는다.

### Active invariants

1. runtime은 `VERTEX_API_KEY`만 읽는다. `GOOGLE_APPLICATION_CREDENTIALS`, GCP project ID, ADC,
   service-account, ambient credential과 Gemini Developer API는 RAG v2 route에서 허용하지 않는다.
2. activation packet은 `pre-s5-vertex-activation/v2`, `VERTEX_EXPRESS_API_KEY`, fixed Vertex Express
   origin/path, logical/physical cap 1, token physical cap 0, `generateContent` physical cap 1, retry 0,
   raw artifact 0을 정확히 요구한다.
3. API-key security, data-governance state, abuse-monitoring state, model availability evidence와
   `EXTERNAL_AI_RAG_V2` effective consent, current immutable scope/evidence가 모두 없으면 provider
   socket은 0이다.
4. DB reservation/outcome은 API-key value, prompt, response를 저장하지 않는다. OAuth token-attempt
   authority는 pre-live empty ledger migration에서 제거하고 one generation attempt만 append한다.
5. OpenAI, Gemini Developer API, alternate generator, tools/functions, Search/Maps grounding, file upload,
   session resumption, context cache, reranker, verifier, retry와 query-level fallback은 계속 0이다.

이 tracked change 자체는 API key를 읽거나 Vertex physical call을 만들지 않는다. 실제 activation은
fresh clean HEAD, CI/security digest, exact local packet, valid key와 current provider governance evidence가
동시에 있을 때만 별도 one-shot boundary에서 가능하다.

## EN: Rationale and scope

The active Pre-S5 Vertex target is superseded by a Vertex Express API-key-only route because the chosen
operational path uses one API key rather than a GCP project, ADC, or a service-account file. The fixed
origin is `https://aiplatform.googleapis.com` and the fixed path is
`/v1/publishers/google/models/gemini-3.5-flash:generateContent`. The key query parameter is assembled only
inside the direct TLS request target and is never persisted in packets, the database, logs, URI abstractions,
or raw artifacts.

This change preserves historical artifacts and the previous contract-lock record byte-for-byte. It changes
only the active Decision Platform route: `VERTEX_API_KEY` is the sole allowed credential source; project ID,
ADC, service accounts, ambient credentials, credential files, and Gemini Developer API are forbidden for
RAG v2. The packet is v2 and permits one `generateContent` physical call with zero token calls and zero
retries. API-key security, governance, abuse-monitoring, model-availability, consent, and current immutable
scope/evidence are all required before any socket can open.

The migration preserves only sanitized ledger identities, removes OAuth token-attempt authority while the
pre-live ledger is empty, and keeps a single append-only generation attempt. This tracked change performs no
provider call and creates no external workspace dependency or decision/signal/risk/order authority.
