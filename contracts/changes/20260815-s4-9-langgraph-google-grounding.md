# S4.9 LangGraph + Vertex Google grounding forward contract

## KR

이 변경은 기존 S4.9 MCP v1과 V1~V69 데이터를 보존하면서 Strong LLM의 active 내부 실행기를
Python LangGraph로 교체한다. Kotlin은 인증·동의·Top-5·월별 비용 예약·SSRF·citation·숫자·조언·DB
경계를 계속 소유한다. Python은 provider message, Gemini thought signature, 최대 3회 도구 상태와
Pydantic structured output만 소유하며 `ProviderCallPermit` 전에는 provider socket을 열지 않는다.

Vertex Google Search grounding은 Gemini가 질문별로 사용할지 결정한다. provider grounding metadata의
source/support segment를 검증된 citation provenance로 사용하고 Google redirect URI를 서버가 자동으로
GET하지 않는다. 로컬 관측 사용량 4,000 query에서 Google tool을 제거하고 SearXNG DuckDuckGo
best-effort 경로로 전환한다. raw query/page/owner text/model request·response는 저장하지 않는다.

MCP 다섯 tool은 유지한다. 검색 결과는 `resultId/sourceType`을 추가하고 read는 등록된 `resultId`를
우선한다. 호환 `url`은 현재 provenance graph node와 일치할 때만 허용한다. RAG/MCP/LLM은 Signal,
LSTM, RiskDecision, 주문 또는 판단 hash 권한을 얻지 않는다.

## EN

This change preserves the S4.9 MCP v1 contract and all V1-V69 data while replacing the active internal Strong
LLM runtime with a bounded Python LangGraph. Kotlin continues to own authentication, consent, Top-5 retrieval,
monthly cost reservations, SSRF controls, citation/number/advice validation, and database persistence. Python
owns provider messages, Gemini thought signatures, at most three tool rounds, and Pydantic structured output.
No provider socket may open before a host-issued `ProviderCallPermit`.

Gemini autonomously decides whether to use Vertex Google Search grounding. Verified provider grounding sources
and support segments are citation provenance; the server never automatically fetches Google redirect URIs. At
4,000 locally observed queries the Google tool is removed and the runtime switches to best-effort SearXNG
DuckDuckGo. Raw queries, pages, owner text, and model requests/responses are not stored.

The five MCP tools remain. Search results add `resultId/sourceType`; reads prefer a registered `resultId`, while
the compatibility URL is allowed only when it resolves to a current provenance node. RAG/MCP/LLM gain no
authority over Signal, LSTM, RiskDecision, orders, or judgement hashes.

Refs #20 #74 #82 #89
