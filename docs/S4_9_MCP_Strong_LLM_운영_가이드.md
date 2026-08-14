# S4.9 MCP + Strong LLM 운영 가이드

상태: `IMPLEMENTED_DRAFT`
계약: `contracts/catalogs/s4-9-mcp-strong-llm-contract.v1.json`

## 1. 구성

- Capstone Spring: `/mcp`, OAuth 2.1 authorization/resource server, Strong LLM validator
- Vertex AI: fixed service-account OAuth + `gemini-3.5-flash`
- SearXNG: internal search backend, host loopback 또는 Compose service로만 접근
- `mcp-searxng`: 내부 호환성 sidecar이며 외부에 직접 publish하지 않는다
- 외부 ChatGPT/Claude: 자신의 모델/API 비용과 research loop를 소유하고 Capstone OAuth MCP만 호출

Gemini Deep Research, Google Search grounding, Naver, browser automation, crawler, login/click/JavaScript는
활성화하지 않는다.

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

```bash
docker compose --profile s4-9-web -f infra/docker-compose.infra.yml up -d searxng mcp-searxng
```

필수 secret은 `SEARXNG_SECRET`, `MCP_SEARXNG_AUTH_TOKEN`이다. 이미지와 digest는 compose에 고정한다.
engine은 DuckDuckGo, Brave, Mojeek, Qwant, Wikipedia만 허용한다. Spring은 JSON search endpoint만 호출하고
검색 결과의 exact URL만 reader에 전달한다.

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

## 6. 장애와 데이터 경계

- SearXNG/read 실패: tool result를 답처럼 저장하지 않고 남은 근거로 final 또는 insufficient를 선택한다.
- Vertex transport/commit 불확실: `UNKNOWN_BILLING`, 자동 retry 0.
- owner consent 불일치/REVOKE/delete: owner snippet 외부 전달 0, 기존 receipt 무효.
- raw web body, raw model response, owner text, OAuth token, credential은 DB/log/history에 저장하지 않는다.
- web evidence DB에는 URL/title/section/retrievedAt/content hash만 저장한다.
- history는 validate 후 사용자의 explicit save에만 AES-GCM으로 30일 저장한다.
- RAG/MCP/LLM은 Signal, LSTM, RiskDecision, order, judgement hash에 영향 0이다.

## 7. 검증

- focused: Strong LLM direct/search/read/final, validator, OAuth resource/PKCE/rotation/revocation, SSRF/DNS/redirect
- DB: V1→V67 clean, V66→V67 upgrade, public-only owner isolation, RLS/ACL, one-use receipt/save
- contract: MCP tools/list fixture, OpenAPI/proto/exact-30 byte parity
- load: 2/10/50 admission fixture
- release: 전체 local gate 뒤 Codex Security campaign 정확히 1회
