# Pre-S5 Vertex service-account OAuth supersession

상태: `ACTIVE_SUPERSEDING_CHANGE`

## KR

사용자 실행 환경에는 Vertex AI 권한을 가진 Google service-account JSON이 준비돼 있고, 사용자는
2026-08-12에 이 credential을 Pre-S5 Vertex 호출에 사용하도록 명시했다. 따라서
`20260810-pre-s5-vertex-api-key-only-supersession.md`는 당시 결정을 보존하는
`HISTORICAL_SUPERSEDED` 기록으로 남기고 active runtime을 service-account OAuth로 전환한다.

- credential은 Git이 추적하지 않는 local root의 `secrets/pre-s5-vertex-service-account.json` 한 파일만 읽는다.
- 파일과 상위 디렉터리는 owner, regular-file, link-count, 0600/0700, bounded JSON, RSA PKCS#8 검사를 통과해야 한다.
- ambient ADC, `GOOGLE_APPLICATION_CREDENTIALS`, Vertex API key와 Gemini Developer API key는 읽지 않는다.
- `https://oauth2.googleapis.com/token`에 cloud-platform scope JWT를 보내는 token physical call은 최대 1회다.
- generation은 `aiplatform.googleapis.com`의 project-scoped `global` publisher endpoint에서 최대 1회다.
- `VERTEX_MODEL_ID`는 기본 `gemini-3.5-flash`이며 허용된 publisher model ID 형식만 받는다.
- resolved project ID, model ID, endpoint는 v3 activation packet과 fresh manifest에 정확히 결속한다.
- token/generation retry, fallback, tools, raw request/response/credential persistence는 모두 0이다.
- public OpenAPI/proto와 기존 RAG ask/history payload는 변경하지 않는다.

V52를 수정하지 않고 next-free V57이 empty pre-live ledger를 확인한 뒤 token/generation attempt를 각각
1회로 복구한다. `generateContent` physical call이 정확히 1회라는 최종 marker는 유지하며 OAuth token
교환은 별도의 authentication physical call 1회로 기록한다.

## EN

The operator explicitly selected the available Google service-account JSON for the Pre-S5 Vertex call on
2026-08-12. The 2026-08-10 API-key-only decision remains byte-stable as a historical superseded record, while
the active runtime now uses explicit service-account OAuth.

The runtime reads one ignored fixed credential file, validates its filesystem and bounded JSON/RSA boundary,
exchanges at most one cloud-platform OAuth token, and performs at most one project-scoped global
`generateContent` call. `VERTEX_MODEL_ID` defaults to `gemini-3.5-flash`, but the resolved project, model, and
endpoint must match the v3 activation packet and fresh approval manifest exactly. Ambient ADC, API keys,
Developer API, retries, fallbacks, tools, and raw credential/request/response persistence remain disabled.
V57 is forward-only and restores the one-token/one-generation append-only ledger without modifying V52.
