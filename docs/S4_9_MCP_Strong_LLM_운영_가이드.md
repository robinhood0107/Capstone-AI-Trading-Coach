# S4.9 MCP + Strong LLM 운영 가이드

상태: `V71_LIVE_VERIFIED_MERGE_CANDIDATE` (2026-08-15)
계약: `contracts/catalogs/s4-9-mcp-strong-llm-contract.v2.json` (v1 역사 보존)

## 1. 구성

- Capstone Spring: `/mcp`, OAuth 2.1, Google budget, network/provenance, Strong LLM validator
- Python `strong-llm-grpc`: LangGraph bounded state와 LangChain Gemini message/schema adapter
- Vertex AI: fixed service-account OAuth + `gemini-3.5-flash` + optional Google Search grounding
- SearXNG: internal search backend, host loopback 또는 Compose service로만 접근
- `mcp-searxng`: 내부 호환성 sidecar이며 외부에 직접 publish하지 않는다
- 외부 ChatGPT/Claude: 자신의 모델/API 비용과 research loop를 소유하고 Capstone OAuth MCP만 호출

Gemini Deep Research, Google Maps grounding, Naver, browser automation, crawler와 대상 사이트의
login/click/JavaScript는 활성화하지 않는다. 아래 OAuth 사용자 로그인은 Capstone 자체 인증 절차로서
이 금지 대상과 다르다.

## 2. 기동 전 local-only 파일

다음 파일은 absolute normalized path, owner-only `0600`, regular file, link count 1이어야 한다.

- MCP ES256 private JWK: `S4_9_MCP_SIGNING_JWK_PATH`
- static/CIMD-verified client allowlist JSON: `S4_9_MCP_CLIENT_ALLOWLIST_PATH`
- Vertex service account: `${CAPSTONE_RAG_LOCAL_ROOT}/secrets/pre-s5-vertex-service-account.json`

allowlist client는 Authorization Code + PKCE S256, redirect URI 1~8개와 아래 scope 일부만 가진다.

```text
mcp:rag.public
mcp:rag.owner
mcp:web.read
mcp:answer.validate
mcp:history.write
```

public Dynamic Client Registration은 없다. production issuer/resource는 HTTPS이고 loopback HTTP는 local에서만
허용한다. 프로젝트는 OpenAI/Anthropic API key를 받거나 저장하지 않는다.

## 3. 검색과 tool budget

```text
RAG_WEB_CONCISE_MAX_SEARCHES=1
RAG_WEB_CONCISE_MAX_READS=3
RAG_WEB_DETAILED_MAX_SEARCHES=2
RAG_WEB_DETAILED_MAX_READS=6
RAG_WEB_ABSOLUTE_MAX_SEARCHES=3
RAG_WEB_ABSOLUTE_MAX_READS=8
RAG_WEB_MAX_PARALLEL_READS=3
RAG_LLM_MAX_TOOL_ROUNDS=3
RAG_WEB_EXTERNAL_MAX_SEARCHES=30
RAG_WEB_EXTERNAL_MAX_READS=120
RAG_WEB_EXTERNAL_USER_PARALLEL_READS=4
RAG_WEB_EXTERNAL_GLOBAL_PARALLEL_READS=8
RAG_WEB_EXTERNAL_MAX_CONTEXTS_PER_CALLER=30
RAG_WEB_EXTERNAL_MAX_TOTAL_CONTEXTS=1024
```

데모 1~2명은 mode budget을 absolute `3/8`까지 올릴 수 있다. mode 값은 absolute cap 이하만 허용한다.
사용자가 늘면 search/read와 parallel 값을 먼저 낮춘다. Capstone Strong LLM은 최대 3 tool round이고,
외부 research client는 owner/client별 15분 `30 search / 120 read` cap을 별도로 공유한다.

## 4. SearXNG

### 4.0 검색 우선순위와 Google budget

기본은 `RAG_WEB_VERTEX_GOOGLE_SEARCH_ENABLED=true`이며 Gemini가 질문별 검색 필요성을 결정한다. Pacific
month local observed+reserved+unknown 합계가 4,000에 도달하면 Google tool은 provider 전에 제거된다.
prompt당 8은 과금 상한이 아니라 timeout/unknown billing을 위한 보수 예약치이며 정상 응답은
`webSearchQueries.size`로 정산한다. Google grounding redirect URL은 자동으로 읽지 않는다.

```text
RAG_WEB_VERTEX_GOOGLE_SEARCH_ENABLED=true
RAG_WEB_VERTEX_GOOGLE_SEARCH_OVERAGE_ALLOWED=false
RAG_WEB_VERTEX_GOOGLE_SEARCH_MONTHLY_SOFT_CAP=4000
RAG_WEB_VERTEX_GOOGLE_SEARCH_RESERVE_PER_PROMPT=8
RAG_WEB_GOOGLE_BILLING_PERIOD_ZONE=America/Los_Angeles
```

Google tool이 제거된 요청만 SearXNG DuckDuckGo best-effort 경로를 사용한다. CAPTCHA/rate limit/0-result는
typed search failure 또는 insufficient로 끝내고 FlareSolverr·browser solver를 추가하지 않는다.

```bash
docker compose --profile s4-9-web -f infra/docker-compose.infra.yml up -d searxng mcp-searxng
```

필수 secret은 `SEARXNG_SECRET`, `MCP_SEARXNG_AUTH_TOKEN`이다. 이미지와 digest는 compose에 고정한다.
일반 fallback engine은 DuckDuckGo만 사용한다. Spring은 JSON search endpoint만 호출하고 SearXNG result,
질문에 실제 포함된 public HTTPS URL, 읽은 문서에서 발견한 link를 opaque `resultId`로 reader에 전달한다.

reader는 HTTPS 443, public IP, redirect 최대 3, DNS answer 재검증, pinned-IP TLS hostname 검증,
HTML/text/PDF, body 2 MB, normalized text 60,000자, PDF 20쪽만 허용한다. cookie/credential/proxy/retry는
없다. login page, unsupported MIME, private/link-local/documentation IP, prompt injection pattern은 거부한다.

## 5. ChatGPT·Claude 연결 흐름

1. 운영자가 client metadata를 0600 allowlist에 등록한다.
2. client가 protected-resource/authorization-server metadata를 discovery한다.
3. Authorization Code + PKCE S256과 exact `/mcp` resource로 사용자 로그인을 수행한다.
4. 필요한 최소 scope만 consent한다.
5. `capstone_rag_search`로 context를 만들고 필요할 때 search/read를 호출한다.
6. 외부 LLM draft는 `capstone_answer_validate`를 통과해야만 Capstone 검증 답변이다.
7. 사용자가 명시한 경우에만 one-use receipt로 `capstone_answer_save`를 호출한다.

owner ID는 tool argument가 아니라 access token subject에서만 온다. refresh token은 7일 family rotation이며
revocation 시 해당 family를 폐기한다. access token은 15분이고 account status/securityVersion을 매 요청 확인한다.
`mcp:rag.owner`가 없는 client는 owner pointer 자체를 읽지 않는 public-only 15분 DB claim만 받는다.

### 5.1 어떤 로그인인가

- 앱 사용자는 기존 `POST /api/v1/auth/login`으로 Capstone API JWT를 받는다.
- 외부 MCP client 연결은 같은 Capstone 계정으로 `/oauth2/authorize`에서 로그인하고 요청 scope를
  승인한다. Google 계정 로그인이나 Vertex service-account 인증이 아니다.
- Vertex OAuth는 서버가 local 0600 service-account JSON으로 처리하며 UI와 사용자 비밀번호를 사용하지 않는다.
- local live smoke는 기존 `demo-user`의 DB identity를 사용하되 무작위 임시 비밀번호를 메모리에서만
  설정하고 종료 시 원래 BCrypt hash를 복원했다. 평문 비밀번호를 파일·로그·argv에 저장하지 않았다.

Spring의 기본 local authorize/login/consent 화면으로 backend E2E는 동작한다. 제품용 Experience UI는
현재 이 Decision Platform workspace의 구현물이 아니다. 기존 제품 UI에 통합할 때는 client 표시명,
요청 scope, owner snippet 외부 전달 여부, 승인/거부를 보여 주고 Spring OAuth endpoint를 호출해야 한다.

## 6. 장애와 데이터 경계

- SearXNG/read 실패: tool result를 답처럼 저장하지 않고 남은 근거로 final 또는 insufficient를 선택한다.
- Vertex transport/commit 불확실: `UNKNOWN_BILLING`, 자동 retry 0.
- owner consent 불일치/REVOKE/delete: owner snippet 외부 전달 0, 기존 receipt 무효.
- raw web body, raw model response, owner text, OAuth token, credential은 DB/log/history에 저장하지 않는다.
- web evidence DB에는 URL/title/section/retrievedAt/content hash만 저장한다.
- history는 validate 후 사용자의 explicit save에만 AES-GCM으로 30일 저장한다.
- RAG/MCP/LLM은 downstream 모델·판단·주문·judgement hash에 영향 0이다.

## 7. 검증

- focused: Strong LLM direct/search/read/final, validator, OAuth resource/PKCE/rotation/revocation, SSRF/DNS/redirect
- DB: V1→V71 clean, V70→V71 upgrade, public-only owner isolation, RLS/ACL, one-use receipt/save
- contract: MCP tools/list fixture, OpenAPI/proto/exact-30 byte parity
- load: 2/10/50 admission fixture
- release: 전체 local gate 뒤 Codex Security campaign 정확히 1회

### 7.1 2026-08-15 live-smoke receipt

- OAuth PKCE actual code/token exchange와 token-family revoke: 성공
- MCP `initialize`, `tools/list=5`, public RAG search: 성공
- SearXNG search 1회, exact HTTPS URL read 1회: 성공
- answer validation: `VALID_WITH_WARNINGS` (단일 web 출처), answer save/history: `0/0`
- Strong LLM: `ANSWERED / MODEL_KNOWLEDGE_ONLY`, provider/model `VERTEX_AI/gemini-3.5-flash`, usage `COMMITTED`
- raw token/web body/model request/model response 저장: 0
- public corpus `142/7,871/63/2`, owner residual 0: 불변

추가 LangGraph live evidence:

- Gemini가 Vertex Google Search를 자율 선택하고 query count를 `COMMITTED`: 성공
- provider grounding source/support가 없는 Google 응답을 `RETRIEVAL_ONLY`로 변환: 성공
- Google soft cap 차단 후 SearXNG 검색·Investor.gov bounded read·EVIDENCE generation: 성공
- V71 `searxng_<24hex>`/source-type/support-edge history forward repair: focused DB integration 통과
- DuckDuckGo CAPTCHA/ALL_ENGINES: solver·browser 우회 없이 typed failure
- V71 tool-free 교육 질문: `ANSWERED / MODEL_KNOWLEDGE_ONLY`, encrypted history·usage `COMMITTED`

고정 질문은 local evidence가 0이라 citation 없는 일반 교육 답변 경로를 사용했다. 따라서 Strong LLM
결과 자체를 citation coverage 성공으로 표시하지 않는다. citation/quote validator의 실제 성공은 별도
MCP web-evidence draft에서 확인했다.
