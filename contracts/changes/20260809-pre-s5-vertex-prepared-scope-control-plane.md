# Pre-S5 Vertex prepared-scope control plane

상태: `CONTRACT_LOCKED / IMPLEMENTATION_AUTHORIZED / PROVIDER_OUTBOUND=0`

## KR

기존 Vertex activation packet은 `scopeClaimId`, request ID, question HMAC, consent/policy digest를 exact하게
결박해야 하지만 기존 `/api/v2/rag/ask`는 request 직전에 새 retrieval scope를 만들었다. 따라서 packet을
물리 호출 전에 안전하게 작성할 수 없었다.

`POST /api/v2/rag/vertex-preparations`는 authenticated owner의 packet-compatible `req_` request ID와 기존
RAG parsed ask command를 받아 current immutable bundle의 two-minute opaque scope를 준비한다. 반환값은
`scopeClaimId`, question HMAC, answer mode, embedding profile, consent/policy digest, expiry뿐이다. owner ID,
raw question, raw evidence, credential, provider response는 persistent receipt 또는 HTTP response에 넣지 않는다.

뒤의 `/api/v2/rag/ask`는 exact same request ID/parsed command와
`X-Rag-V2-Vertex-Scope-Claim`을 요구한다. Spring과 DB SECURITY DEFINER projection은 owner/request/topic,
active public/owner pointer, profile, expiry를 재검증하고, usage ledger는 same-body HMAC을 activation packet과
다시 대조한다. enabled Vertex에 scope header가 없거나 disabled instance에 preparation scope를 보낸 경우에는
gRPC/provider socket 전에 fail-closed한다.

이 변경은 public Voyage base의 empty `OWNER_PRIVATE` sentinel과 양립한다. v1/OpenAPI/proto/exact-30,
historical OA112/OA140, GDELT offline-only boundary, Decision/Signal/Risk/order authority는 변경하지 않는다.
Vertex ADC/service-account credential, billing/IAM, cache/abuse/ZDR evidence와 exact physical approval packet이
없으므로 provider outbound call은 계속 0이다.

## EN

The existing Vertex activation packet must bind an exact scope claim, request ID, question HMAC, and consent/policy
digests, while `/api/v2/rag/ask` previously minted a new retrieval scope immediately before execution. That made a
safe pre-call packet impossible to construct.

`POST /api/v2/rag/vertex-preparations` accepts an authenticated owner's packet-compatible `req_` request ID and the
existing parsed RAG ask command, then prepares a current immutable-bundle two-minute opaque scope. It returns only the scope
claim, question HMAC, answer mode, embedding profile, consent/policy digests, and expiry. Owner identity, raw
question, raw evidence, credentials, and provider responses are excluded from persisted receipts and HTTP responses.

The later `/api/v2/rag/ask` requires the exact same request ID/parsed command and
`X-Rag-V2-Vertex-Scope-Claim`. Spring and the DB SECURITY DEFINER projection re-check owner/request/topic,
active pointers, profile, and expiry; the usage ledger re-checks the same-body HMAC against the activation packet.
Missing or unsuitable prepared scope fails closed before gRPC or provider sockets. Provider outbound remains zero
until credentials, privacy evidence, and an exact physical approval packet are present.
