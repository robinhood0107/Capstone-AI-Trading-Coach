# AI usage telemetry does not gate generation

The shared provider exposure ledger and the RAG Vertex daily usage counter are measurement surfaces only.
They record estimated exposure or today's generation reservation count, but neither a reached count nor a
meter read/write failure may prevent an otherwise authorized provider call. The corpus-status response
keeps its existing shape: `generationUsedToday` is best-effort, while `generationDailyCap` and
`generationRemaining` are always `null` for wire compatibility.

Per-request consent, owner scope, evidence binding, single-use call claims, token/byte bounds, provider
authentication, and trading-risk controls remain independent authorization or safety checks. This change
removes only cumulative usage quota enforcement; provider-side quota and availability responses still apply.
