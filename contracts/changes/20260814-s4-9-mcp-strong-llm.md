# S4.9 MCP + Strong LLM contract lock

## KR

S4.9는 기존 RAG v2 공개 응답 bytes를 바꾸지 않고 내부 생성기를 provider-neutral Strong LLM으로
일반화한다. Vertex `gemini-3.5-flash`가 첫 adapter이며, Top-5 전체를 보고 관련 근거를 선택·종합한다.
생성 문장에는 exact evidence quote를 결박하고 서버가 citation·quote·숫자·직접 투자 조언을 검증한다.

Streamable HTTP `/mcp`는 계약에 고정된 다섯 read/validate/explicit-save tool만 노출한다. OAuth 2.1
Authorization Code + PKCE S256, `/mcp` resource audience, owner subject binding을 적용하며 공개 Dynamic
Client Registration은 열지 않는다. SearXNG 검색과 bounded HTTPS read는 보조 근거일 뿐이고
Google Search, Naver, browser automation, crawler, Deep Research는 활성화하지 않는다.

RAG/MCP/LLM 결과는 downstream 모델·판단·주문 및 판단 hash에 영향을 주지 않는다.
V1~V65 migration과 기존 DB row는 보존한다. V66은 S4.9 저장 경계를 추가하고, 적용 후 발견된
public-only OAuth scope의 owner overlay 선조회와 15분 context TTL 불일치는 V67 forward repair로만 고친다.

## EN

S4.9 generalizes the internal answer generator into a provider-neutral Strong LLM without changing the existing
RAG v2 public response bytes. Vertex `gemini-3.5-flash` is the first adapter. It receives all Top-5 evidence,
selects relevant sources, and may synthesize them. Every evidence-based sentence supplies exact evidence quotes;
the server independently validates citations, quotes, numbers, and direct-investment-advice boundaries.

The Streamable HTTP `/mcp` surface exposes only the five locked read, validate, and explicit-save tools. It uses
OAuth 2.1 Authorization Code with PKCE S256, a fixed `/mcp` resource audience, and authenticated-subject owner
binding. Public Dynamic Client Registration is disabled. SearXNG search and bounded HTTPS reads are evidence
helpers; Google Search, Naver, browser automation, crawling, and Deep Research remain disabled.

RAG, MCP, and LLM outputs have no authority over downstream models, decisions, orders, or judgement hashes.
Migrations V1 through V65 and all existing database rows remain preserved. V66 adds the S4.9 persistence
boundary, while V67 forward-repairs the public-only OAuth scope so it cannot read the owner overlay and aligns
the database claim with the 15-minute research context.

Refs #82 #74 #89
