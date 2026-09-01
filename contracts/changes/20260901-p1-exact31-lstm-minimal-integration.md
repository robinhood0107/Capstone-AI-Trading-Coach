# P1 exact-31 LSTM minimal integration

## Decision

Preserve historical Return manifest/signal v2 bytes and add current confidence-free manifest/signal v3.
The current path is `Git Model Seed -> existing inference -> existing automation loop` with V116 separating
the immutable base model seed from per-session Rule+LSTM batches.

## Current invariants

- exact-10 plus `p1-return-engine-manifest.v3.json` under `deploy/p1/seed/team-b/`
- exact-31, fixed `132030`, RAW_CLOSE, minimum 756 XKRX sessions
- default candidate order `expectedReturn DESC, symbol ASC`
- Strong LLM authority is rank, veto, abstain only
- RiskEngine is the only quantity authority
- identical seed or daily identity replays as no-op; different bytes rollback
- inference retry is the same request at most once
- provider/account/order calls during implementation are zero

## Compatibility

V1/V2 schema, contract-change, generated client and evidence bytes are not rewritten. Signal v3 is one
additive root OpenAPI operation, moving current root 75 to 76 while Team A current acceptance remains exact 45
through v4.

## External gates

Real 756-session KIS collection needs a fresh explicit physical-call approval. Team B exact-10, market-hours KIS
Mock certification and three XKRX session soak remain separate blockers. Kubernetes stays post-P1 TODO only.
