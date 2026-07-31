# S4.4 fixture RAG guard·consent·encrypted history

상태: `S4_4_FIXTURE_GUARD_HISTORY_IMPLEMENTED`

## KR

이 변경은 S4.3의 owner/access 제한 retrieval 다음 단계로 S4.4의 공개 API, owner-scoped
저장 경계와 fixture-only 안전장치를 고정한다. 기본 answerer는 `FIXTURE_ONLY`이며
Gemini·OpenAI·Voyage, market provider, account, order 물리 호출은 모두 0이다.

### 계약과 런타임 경계

1. `POST /api/v1/rag/ask`는 exact 네 body field와
   `[A-Za-z0-9._~-]{16,128}` `X-Idempotency-Key`만 받는다.
2. local deterministic guard는 민감정보, 계좌·보유·주문·체결, 개인화 매수/매도 조언,
   prompt injection과 bounded obfuscation을 provider 경계 전에 차단한다. timeout,
   parser error, unknown label은 fail-closed다.
3. raw idempotency key는 request boundary에서 purpose-separated HMAC으로 바뀌며,
   same/different/in-progress/stale/deleted replay를 typed state로 구분한다.
4. `POST /api/v1/consents`는 `EXTERNAL_AI_RAG_V1`의 append-only `GRANT | REVOKE`
   event만 추가한다. actor와 시각은 JWT와 server clock이 소유한다.
5. 질문과 답변은 row별 random 32-byte DEK와 field별 independent nonce를 사용한
   AES-256-GCM으로 암호화한다. versioned secret-file KEK는 owner의 absolute 0700
   directory와 0600 regular single-link file에서만 읽는다.
6. history list는 metadata-only이고 detail만 owner·미삭제·미만료 조건에서 한 row를
   복호화한다. citation은 저장된 exact count와 `cit_1..cit_N` 연속 집합, exact
   `chunkRevisionId`, active generation, verified public source와 current membership을
   모두 다시 확인한 뒤에만 replay/detail plaintext를 연다. delete는 존재 여부와 무관하게
   204다.
7. feedback은 boolean `helpful`만 owner answer에 멱등 저장한다. hourly purge는 bounded
   batch, 삭제 수·실패 수·남은 lag의 비식별 metric만 기록한다.
8. provider client proof는 network-disabled fixture transport만 허용한다. 고정 HTTPS
   origin/path, TLS verification, redirect·ambient proxy·retry 금지, timeout과 byte/depth/list
   cap, caller transport/model/header override 거부를 검증한다. malformed provider bytes와
   credential/header/body는 exception cause와 request `repr`에 남기지 않는다.
9. external attempt marker는 owner별 consent lock과 append-only sequence로 GRANT/REVOKE
   순서를 고정하고, active·verified·PUBLIC·PROJECT이면서
   `externalProcessingAllowed=true`인 exact chunk context만 받는다. 현재 frozen 30-card
   corpus와 fixture answerer에서는 이 조건으로 실제 outbound를 만들지 않는다.
10. answer/history/source/consent/feedback의 최종 공통 envelope 직렬화 크기는 32KiB를
    넘으면 plaintext를 반환하지 않고 fail-closed한다.

### Machine-readable artifacts

| 파일 | 역할 |
|---|---|
| `schemas/s4-rag-answer.schema.json` | public answer data closed schema |
| `schemas/s4-rag-history-page.schema.json` | metadata-only history page |
| `schemas/s4-rag-history-detail.schema.json` | owner history detail |
| `schemas/s4-rag-feedback-request.schema.json` | boolean feedback body |
| `schemas/s4-rag-consent-request.schema.json` | exact consent event body |
| `openapi/openapi.json` | 실제 Spring 출력으로 정규화한 API SSOT |
| `../workspaces/decision-platform/spring-api/src/main/resources/db/migration/V20__s4_4_rag_guard_history.sql` | owner-scoped claims, consent, encrypted history, citation, feedback와 usage ledger |

새 public request/response selector, live provider credential/activation, streaming, history search,
export/share/restore/admin-decrypt API는 추가하지 않는다. Return Engine과 Experience Dashboard
workspace는 변경하지 않는다.

재현 명령은 provider/live/account/order 호출을 만들지 않는다.

```bash
uv run --frozen python contracts/generate_s4_rag_contracts.py --check
uv run --frozen python -m unittest discover -s contracts/tests -v
uv run --frozen python contracts/validate.py
uv run --frozen python contracts/run_openapi_gate.py \
  --env-file workspaces/decision-platform/spring-api/build/openapi-fixture/openapi.env
```

## EN

This change locks the S4.4 public APIs, owner-scoped persistence, and fixture-only
guard boundary after S4.3 authorized retrieval. The default answerer is
`FIXTURE_ONLY`; Gemini, OpenAI, Voyage, market-provider, account, and order physical
calls remain zero.

The ask route accepts only the four approved body fields and a bounded idempotency
header. Raw keys are immediately converted into purpose-separated HMAC identities.
The database state machine distinguishes same-request replay, conflict, in-progress,
stale, failed-before-provider, and unknown-after-provider outcomes. Consent is an
append-only `EXTERNAL_AI_RAG_V1` event whose actor and timestamp are server-owned.

Each history row uses a random 32-byte DEK and independent AES-256-GCM nonces for
question and answer. The DEK is wrapped by a versioned KEK loaded only from an
owner-controlled 0700 directory and 0600 regular single-link file. History lists are
metadata-only; detail reads decrypt one owned, unexpired row and revalidate current
citation access, exact stored citation count, contiguous citation identities, and
exact active chunk membership before plaintext replay. Feedback is a single boolean,
deletion hides existence, and an hourly bounded purge exposes only sanitized
aggregate metrics.

The generated closed schemas and normalized OpenAPI reject provider/model/internal
fields, history previews, free-form feedback, and caller-controlled consent actors.
The provider-shaped client remains network-disabled and proves a fixed HTTPS
origin/path, TLS verification, no redirects, no ambient proxy, no retries, bounded
timeouts and payloads, and rejection of caller transport/model/header overrides.
Malformed provider bytes and credential-bearing request internals are excluded from
exception causes and default request representations. The external-attempt marker
serializes consent ordering and accepts only exact active, verified, public project
chunks whose cards permit external processing. Final public envelopes fail closed
above 32 KiB.
Live generation, streaming, history search/export/share/restore, and admin decryption
remain outside this change.
