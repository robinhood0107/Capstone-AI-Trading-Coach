# Pre-S5 Voyage public-base owner sentinel

상태: `CONTRACT_LOCKED / IMPLEMENTATION_AUTHORIZED`

## KR

Pre-S5 RAG v2의 Voyage full bundle은 `EXACT30`, `OA112`, `OWNER_PRIVATE`의 고정된 세
component slot을 유지한다. 전역 public base를 `voyage_context_4_1024_v1`로 생성할 때는
`OWNER_PRIVATE` slot에 다음 empty sentinel만 허용한다.

```text
ownerScopeSha256=null
orderedGroupCount=0
publicBaseOnly=true
sourceScope=OWNER_PRIVATE
```

이는 private corpus의 부재를 명시하는 membership marker이며 owner-private retrieval, consent
우회, raw/private chunk 전송, owner generation activation을 의미하지 않는다. 실제 owner-private
component는 기존대로 owner-bound consent, SHA-256 owner projection, non-empty ordered document
groups, owner RLS와 별도 immutable generation을 요구한다.

이 변경은 public base activation이 특정 owner의 private document를 Voyage provider input에 억지로
결합하지 않도록 한다. v1/OpenAPI/proto/exact-30/historical OA112/OA140 bytes, GDELT offline-only
boundary, Decision/Signal/Risk/order authority는 변경하지 않는다.

## EN

The Voyage full bundle retains its fixed `EXACT30`, `OA112`, and `OWNER_PRIVATE` component slots.
For a global public-base `voyage_context_4_1024_v1` generation, the `OWNER_PRIVATE` slot may contain
only the explicit empty sentinel above. It denotes the absence of private material; it is not a
private retrieval capability, consent bypass, or permission to transmit private canonical text,
chunks, or embeddings.

An actual owner-private component still requires owner-bound consent, a SHA-256 owner projection,
non-empty ordered groups, owner RLS, and its own immutable generation. This prevents a global
public-base activation from coupling an arbitrary owner's private document to a provider request.
