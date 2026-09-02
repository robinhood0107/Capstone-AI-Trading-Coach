# RAG v2 always answers, evidence is attached rather than required

## KR

시연 핵심 기능인 `POST /api/v2/rag/ask`가 설명 자체를 막고 있었다. 화면에 실제로 나온 세 상태는
모두 답이 없는 상태였다. `BLOCKED_ADVICE`는 검색·생성 이전에 질문을 닫았고, `GENERATION_UNAVAILABLE`은
strict validator가 다 만들어진 답을 통째로 버린 결과였고, `RETRIEVAL_ONLY`는 근거는 찾았으나 문장을
만들지 않은 상태였다. 사용자가 "금 ETF의 롤오버 위험"을 물어도 얻는 것은 설명이 아니라 거절이었다.

이 변경은 집행 지점을 옮긴다. **LLM 답변은 항상 전부 출력한다. RAG 근거와 LLM 내부 Google Search
grounding은 있으면 붙이는 것이고, 없다고 답변을 막는 조건이 아니다.** 조언이 아니라는 사실은 질문
차단이 아니라 생성 프롬프트의 '할 수 없는 것'과 동의 화면의 고지가 말한다.

### 공개 계약은 바뀌지 않는다

`contracts/schemas/s4-rag-v2-answer.schema.json`, `contracts/proto/rag_v2.proto`,
`contracts/openapi/rag-v2.openapi.json`, `contracts/openapi/p1-rag-v2-public.v1.openapi.json`,
root `contracts/openapi/openapi.json`, v1 계열 schema/proto의 bytes를 하나도 바꾸지 않는다.
`ANSWERED` + `citations: []` + `citationCoverage: 0`은 이미 schema가 허용하는 조합이었다.
`BLOCKED_ADVICE`는 wire 호환을 위해 enum에 남되 서버가 더 이상 생성하지 않는다. 68 → 61 → 56 → 48
projection 체인은 영향을 받지 않는다.

### 경계가 바뀐 자리

1. **앞단 guard** (`python-services/app/rag/guardrail.py`) — 개인화 조언은 차단이 아니라 관측이 된다.
   `PERSONALIZED_TRADING_ADVICE` flag만 남기고 통과시킨다. 분류기 장애도 fail-closed에서 fail-open으로
   바꾼다. PII·prompt injection 차단은 남되, `보유`·`주문`·`체결`·`잔고`·`계좌`·`토큰`·`비밀` 같은 일반
   금융 명사와 URL 포함 질문으로 닫던 대안을 뺐다. 남은 것은 실제 식별자 패턴과 "특정인의 자료를
   달라"는 요구 형태다.
2. **생성 프롬프트** (`app/strong_llm/prompt.py`, `strong-llm-prompt/v2` → `v3`) — 모델이 고를 수 있는
   basis에서 `INSUFFICIENT_EVIDENCE`를 없앴다. 근거가 없으면 `MODEL_KNOWLEDGE`로 설명한다.
   Google grounding 정책도 "근거 없으면 빈 답" 대신 "근거 없어도 인용 없이 설명"으로 바꿨다.
   개인화 매매 지시 금지 문구는 그대로 남는다.
3. **provider 정규화** (`app/strong_llm/vertex_provider.py`) — grounding을 결속할 수 없을 때 답을
   비우던 자리를 `MODEL_KNOWLEDGE` 강등으로 바꿨다. 문장 텍스트는 보존하고 인용만 뗀다. 인용이
   뒷받침하지 못하는 숫자가 있으면 그 문장만 인용 없는 문장으로 내린다. 구조화 출력 파싱이 깨지면
   모델 본문을 인용 없는 설명으로 되살리는 평문 폴백을 최후 안전망으로 둔다.
4. **Kotlin validator** (`application/rag/RagV2VertexGenerationRuntime.kt`) — `MODEL_KNOWLEDGE`의 숫자·시점
   표현 금지, 인용 없는 추론 문장의 숫자·시점 제약, 생성된 답의 직접 조언 표현 검사를 뺐다. 인용을
   가진 문장의 exact quote·숫자 결속 검증과 PII 검사는 그대로 남는다.
5. **Kotlin service 경계** (`application/rag/RagV2RuntimeService.kt`) — 인용률 하한(`EVIDENCE` 0.8,
   `EVIDENCE_WITH_REASONING` 0.2)을 뺐다. 연결률은 답을 통과시킬 문턱이 아니라 화면이 보여 주는 지표다.
6. **동의 고지** (`experience-dashboard/src/features/rag-source/viewModel.ts`) — `EXTERNAL_DISCLOSURE`에
   "이 답변은 투자 조언이 아니며 정확성을 보장하지 않는다"를 넣었다. 서버는 `disclosureDigest`의 hex
   형식만 검증하고 값을 대조하지 않으므로(`api/rag/RagRequestParser.kt`) 기존 동의는 유효하며 이후
   동의부터 새 digest가 기록된다.

### `directAdviceBlockRate`의 뜻이 바뀐다

`capstone-rag/eval/s4-5-evaluation-60.v1.json`의 exact-60 구성(injection 4, advice 2, PII/account 2,
unauthorized citation 2)은 그대로다. 바뀐 것은 advice 2건의 기대 결과이며 `allowedAnswerStatus`가
`BLOCK`에서 새 값 `FLAG`로 바뀐다 — 통과시키는 것이 정답이고, 그 부류임을 알아본 흔적이 flag로
남아야 한다.

`directAdviceBlockRate`는 이름을 유지한다. 이 값이 `rag_v2_public_bge_staging` 컬럼과 activation 계약
payload에 같은 글자로 굳어 있기 때문이다. 재는 대상은 "몇 개를 막았나"에서 "몇 개를 알아봤나"로
바뀌었고 게이트는 여전히 `EQUAL_1_00`이다. 게이트가 지키려던 성질(분류기가 이 부류를 놓치지 않는다)은
그대로이며, `contracts/changes/20260812-pre-s5-voyage-resumable-batch-activation.md`가 pointer CAS 조건으로
요구하는 `1.0`도 그대로 만족한다. Pre-S5 public corpus는 `FULL_READY`로 동결돼 있어 재실행하지 않는다.

### v1 표면의 파생 변화

v1 `/api/v1/rag/ask`는 같은 guard를 공유하므로 조언성 질문에서 더 이상 `BLOCKED_ADVICE`를 내지 않는다.
v1 fixture corpus에 맞는 근거가 없으면 `RETRIEVAL_FAILURE` + `RAG_INSUFFICIENT_EVIDENCE`가 된다.
v1 proto/OpenAPI/schema bytes는 바뀌지 않는다.

### RAG 권한

RAG·news·analyst의 Decision, Signal, RiskDecision, order, decision hash 권한은 그대로 `0`이다.
`AGENTS.md`의 marker block은 바뀌지 않는다.

## EN

The demo's core feature, `POST /api/v2/rag/ask`, was blocking the explanation itself. All three states seen on
screen were answerless: `BLOCKED_ADVICE` closed the question before retrieval or generation, `GENERATION_UNAVAILABLE`
was the strict validator discarding an answer it had already produced, and `RETRIEVAL_ONLY` found evidence but wrote
no sentences. Asking about gold-ETF rollover risk returned a refusal, not an explanation.

This change moves where the boundary is enforced. **The model's answer is always shown in full. RAG evidence and the
model's own Google Search grounding are attached when available; their absence never suppresses the answer.** The
"this is not advice" boundary is stated by the generation prompt's prohibitions and by the consent disclosure, not by
refusing the question.

**No public contract bytes change.** The v2 answer schema, `rag_v2.proto`, both v2 OpenAPI files, the root OpenAPI
projection, and the entire frozen v1 family are untouched. `ANSWERED` with an empty `citations` array and
`citationCoverage` `0` was already a valid combination. `BLOCKED_ADVICE` stays in the enum for wire compatibility but
is no longer produced. The 68 → 61 → 56 → 48 projection chain is unaffected.

Changed boundaries: the pre-provider guard now flags personalized advice instead of blocking it and fails open on
classifier failure, while keeping PII and prompt-injection blocks and dropping the alternatives that closed on
ordinary finance vocabulary or on any URL; the prompt (contract id `v3`) removes the empty-answer basis so the model
explains with `MODEL_KNOWLEDGE` when evidence is thin; provider normalization demotes unbindable citations to
`MODEL_KNOWLEDGE` instead of emptying the answer and adds a plaintext fallback for broken structured output; the
Kotlin validator drops the number and time-word prohibitions on uncited sentences and the direct-advice check on
generated answers while keeping exact-quote and numeric binding for cited sentences plus the PII check; the service
drops the citation-coverage floors so coverage is a reported metric rather than a gate; and the consent disclosure
now states that answers are explanatory, not investment advice, and are not guaranteed accurate.

`directAdviceBlockRate` keeps its name because it is frozen into a staging column and the activation payload, but it
now measures recognition rather than blocking; the `EQUAL_1_00` gate and the property it protects are unchanged, and
the exact-60 fixture composition is unchanged apart from the two advice items moving from `BLOCK` to the new `FLAG`
expectation. The v1 ask surface shares the guard and therefore returns `RETRIEVAL_FAILURE` rather than
`BLOCKED_ADVICE` for advice questions, with no v1 artifact bytes changed. RAG retains zero authority over decisions,
signals, risk decisions, orders, and decision hashes.
