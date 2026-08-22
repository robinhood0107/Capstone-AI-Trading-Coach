# S8 Dashboard runtime implementation amendment

## Current authority

- The four authenticated owner-scoped Dashboard routes use the existing standard success envelope:
  `success`, `requestId`, `data`, `warnings`, and nullable `error`.
- Persisted Decision identifiers remain `dec_*`; persisted RAG v2 answer identifiers remain `rag_*`.
  No compatibility alias is introduced.
- Model and backtest views read only an append-only sanitized projection. The checked-in synthetic fixture is
  produced by `decision-platform`, uses the `demo_*` namespace, and always sets
  `performanceClaimAllowed=false`.
- Decision/Risk and RAG views read existing owner-scoped persisted records. Foreign-owner identifiers return
  the same not-found response as absent identifiers.
- RAG source views omit canonical URLs, locators, raw chunks, local paths, and encrypted question/answer bytes.
- LightGBM remains `ABSTAIN` and exposes no prediction or invented timestamp.
- Artifact ingest status is bounded to 100 rows and requires a current active ADMIN role/securityVersion check.

## Async materialization

Synthetic projections are staged with an exact canonical-text SHA-256 check. An `ARTIFACT_INGEST` worker commit
publishes the staged projection in the same PostgreSQL transaction as its materialization receipt. DB and Kafka
adapters are both tested against the same projection bytes and must persist the same hashes.

## Explicit exclusions

- No `GET /api/v1/risk/cross-market` route or cross-market scheduler/runtime is restored.
- No Return Engine or Experience Dashboard workspace implementation is added.
- The synthetic bundle is not Team B evidence and does not complete real S8.1 or P1.
