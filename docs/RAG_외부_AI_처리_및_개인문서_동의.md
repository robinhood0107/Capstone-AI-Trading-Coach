# RAG 외부 AI 처리 및 개인 문서 동의

상태: `EXTERNAL_AI_RAG_V2 / TARGET_NOT_ACTIVE / OA112_ACTIVE_CONTRACT_LOCKED`
적용 대상: S4.7D logical OA112·owner-private RAG v2의 후속 materializer 및 generator runtime

이 문서는 사용자가 자신의 문서를 RAG 설명 기능에 넣을 때의 처리 경계와 향후 동의 계약을
설명한다. 현재 checkout의 RAG v2 materializer, Voyage executor, Vertex Gemini executor는
활성화되지 않았으며 이 문서만으로 외부 호출이 시작되지 않는다.

## 1. 역할과 범위

RAG는 검색된 근거를 사용해 금융 개념, 가정, 한계와 citation을 설명하는 기능이다. RAG에는
Signal feature, RiskDecision, 주문 의도, 주문 수량, 주문 hash를 바꿀 권한이 없다.
`RAG_DECISION_SIGNAL_ORDER_AUTHORITY=0`은 이 경계를 고정한다.

지원 대상은 contract로 고정된 logical OA112 자료와 사용자가 적법하게 보유한 개인 문서다. 앱은
파일별 저작권 판단을 대신하지 않는다. 사용자는 문서를 처리하고 필요한 경우 외부 processor로
전송할 권한을 보유한다고 확인해야 한다. DRM·paywall·login 우회, 무단 crawling, credential·secret,
악성 파일은 동의와 무관하게 기술적으로 차단한다.

## 2. 처리 흐름과 저장 위치

후속 구현의 처리 순서는 다음과 같다.

```text
안전 파일 검사
→ native parse 또는 필요한 page만 OCR
→ Document IR·secret/PII/prompt-injection 분류
→ canonical chunk·hash
→ embedding generation
→ pgvector + pg_trgm staging 평가
→ atomic bundle activation
→ top-5 evidence만 생성기에 전달
```

개인 원본은 사용자가 보유한 위치에서 read-only로 읽으며 import pipeline이 복사하지 않는다.
OA 원문은 사용자가 선택한 local cache에만 보존할 수 있다. Git, GitHub Release, Hugging Face
metadata mirror에는 원문, 추출 text, canonical chunk, embedding, provider raw response를 넣지 않는다.
개인 문서의 derived Document IR, canonical chunk, embedding, locator와 hash는 owner RLS 아래의
local generation에만 저장하는 target이다. 로컬 절대경로는 API, history, log, receipt에 넣지 않는다.

현재는 importer가 설치되지 않아 `CORPUS_RUNTIME_NOT_INSTALLED`로 종료하며, physical OA112 또는
개인 문서 derived index가 구축되어 있지 않다. 개발 runtime에 존재하는 OCR benchmark 원본/model
cache는 OA corpus 또는 개인문서 index가 아니다.

## 3. 외부 processor와 전송

유효한 `EXTERNAL_AI_RAG_V2` 동의와 provider-specific 운영 증거가 모두 있을 때만 다음 target을
사용할 수 있다.

| 역할 | processor | 전송 대상 | 호출 제한 |
|---|---|---|---|
| 문서·질문 embedding | Voyage AI `voyage-context-4` | ordered canonical chunk group 또는 질문 1개 | generation complete-only, 질문당 최대 1회, retry 0 |
| 최종 설명 생성 | Google Cloud Vertex AI Gemini `gemini-3.5-flash` | source/owner scope를 재검증한 top-5 evidence와 질문 | 질문당 최대 1회, retry 0 |

Voyage는 답을 생성하지 않고 embedding 전용이다. Vertex AI Gemini만 final response를 생성하는
LLM target이다. OpenAI와 Gemini Developer API는 v2 runtime에서 호출하지 않는다. tool/function,
Google Search/Maps grounding, file upload, context cache, session resumption, URL fetch, code execution은
사용하지 않는다.

Voyage live activation에는 organization admin의 training opt-out과 payment-method/privacy evidence가
필요하다. `VOYAGE_API_KEY` 외 runtime 환경변수는 허용하지 않으며 Files/Batch API와 retry는 0이다.
Voyage가 불가해도 query별 fallback, partial profile 혼합 또는 BGE public 재실행을 하지 않고 activation을
중단한다. Vertex는 local root의 0600 service-account JSON만 읽어 OAuth token을 한 번 교환하며,
`VERTEX_MODEL_ID`(기본 `gemini-3.5-flash`)의 project/model/path를 exact packet에 고정한다. ambient ADC,
API key와 Gemini Developer API는 허용하지 않는다. service-account file security,
data-governance·abuse-monitoring evidence와 fresh selected-model availability 확인 전에는 활성화하지 않는다.

외부 전송은 source의 `externalEmbeddingAllowed`/`externalGenerationAllowed`와 owner의 effective
consent를 모두 통과한 evidence에 한정한다. 하나라도 불충분하면 해당 요청 전체를 local
`RETRIEVAL_ONLY`로 처리하며, owner 자료가 필요하지만 동의가 없으면
`EXTERNAL_AI_CONSENT_REQUIRED`를 반환하고 provider call은 0이다.

Vertex service-account project의 billing, IAM, data-governance/grounding/session/abuse-monitoring 설정과
data-retention evidence, Voyage의 paid organization privacy/retention evidence는 실제 activation의 별도 hard gate다. provider의
정책 문구나 이 동의 문서만으로 zero retention 또는 no-training을 보장한다고 주장하지 않는다.

## 4. 동의, 철회, 삭제

후속 v2 API는 append-only `GRANT | REVOKE` event로 policy version과 disclosure digest를 기록한다.
owner와 timestamp는 JWT/server clock에서 결정하며 raw document path, credential, provider body는 event에
포함하지 않는다. processor 집합이나 policy digest가 바뀌면 다시 동의해야 한다.

- `GRANT`: 외부 처리의 필요조건일 뿐 activation 또는 provider call의 충분조건이 아니다.
- `REVOKE`: 그 시점 이후 Voyage/Vertex 신규 호출을 즉시 0으로 만든다.
- 철회는 이미 저장된 local derived generation을 자동 삭제하지 않는다. 사용자는 별도 document
  deletion으로 자신의 Document IR·chunk·embedding을 hard-delete할 수 있다.
- 삭제 receipt에는 document 내용, 원본 경로, provider response를 남기지 않는다.

비용은 provider의 실제 billing과 project 설정에 따라 발생할 수 있다. 앱은 사용자에게 provider,
목적, 전송 종류, 비용 경계, 철회·삭제 방법을 표시한 뒤 동의를 받아야 한다. 동의가 없거나 packet,
credential, privacy evidence가 누락되면 기능은 fail-closed한다.

## 5. Pre-S5 contract addendum과 다음 gate

`PRE_S5_RAG_GLOBAL_NEWS_CONTRACT_LOCKED=1`이고
`S4_7D_OA112_PHYSICAL_ACTIVATION=NOT_MATERIALIZED`다. historical OA112 metadata manifest는 보존하며,
active selection은 14 track × 8 = 112의 logical policy만 뜻한다. reserve research는 최대 28개이며
자동 승격은 없다. active physical source는 `machineFetchAllowed`, `localProcessingAllowed`,
`externalEmbeddingAllowed`, `externalGenerationAllowed`가 모두 true로 재검증되기 전에는 활성화할 수
없다.

RAG v2 addendum은 existing ask/status/history bytes를 bind하고 `POST /api/v2/rag/consents`,
`GET /api/v2/rag/consent`, `POST /api/v2/rag/import-tickets` 세 route만 계약에 추가한다. effective consent,
owner deletion activation/hard-delete, embedding profile policy도 schema로 잠근다. ticket은 5분
single-use·owner-bound이고 raw path/JWT/owner ID/DB credential을 API 또는 BAT command line에
노출하지 않는다. 현재 상태는 계속 `TARGET_NOT_ACTIVE`다. 다음 구현은 OA112 secure
downloader/materializer, owner import/delete, single-profile bundle, authorized RRF retrieval, Voyage
generation, Vertex single generator 순서로 진행한다.
외부 호출은 exact HEAD의 CI·security evidence와 provider별 approval packet, 그리고 사용자의 해당
packet에 대한 최종 승인 뒤에만 실행한다.
