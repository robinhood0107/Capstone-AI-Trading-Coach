# Pre-S5 Voyage resumable batch activation addendum

## KR

### 변경 이유

public EXACT30+OA112 CPU BGE 전체 materialization은 OA112 7,844 chunk에서 한 번에 약 10시간이
소요됐고, 부분 generation을 재개하지 못하는 실행 구조 때문에 같은 장기 추론이 반복됐다. 또한 기존
Voyage operator는 전체 corpus를 단일 요청으로 보내려 해 `voyage-context-4`의 request token cap을
만족할 수 없었다.

### 잠긴 변경

- CPU BGE public 재실행은 `TERMINALLY_SUPERSEDED_NO_FURTHER_BGE_RUN`이다. 기존 partial generation은
  삭제하거나 ACTIVE로 만들지 않고 V54 terminal marker와 함께 실패 이력으로 보존한다. operator CLI는
  모델 load 전에 모든 BGE 실행 명령을 거부한다.
- public bundle은 EXACT30 30개, OA112 112개, ordered group 0인 빈 `OWNER_PRIVATE` sentinel로 고정한다.
  owner 원문은 public Voyage input에 포함하지 않는다.
- source별 derived IR checkpoint는 ignored local root의 `derived-ir/` 아래 0600 regular file로만
  저장하며 raw SHA, parser version, tokenizer version, source revision에 결속한다. 최대 worker는 4이고
  완료 source 142개는 provider call 없이 재사용한다.
- official `voyage-context-4` tokenizer의 exact hash를 packet과 local artifact에 함께 고정한다. document
  request는 기존 chunk/locator/source order를 보존하면서 110,000 token 이하로 결정적으로 pack한다.
  provider 120,000 token cap 대비 10,000 token headroom을 둔다.
- 각 document batch는 별도 nonce, TTL, logical/physical cap 1, token/byte/cost cap, exact clean
  HEAD/tree/CI/security evidence에 결속된다. 성공 batch vector는 V54 append-only ledger에 즉시 stage하고
  재개 시 이미 committed된 batch를 호출하지 않는다. 각 global batch ID는 전체 plan seed와 official
  tokenizer hash까지 포함해 다른 plan과 충돌하지 않는다. 첫 실패 뒤 남은 provider call은 0이다.
- document batch의 request/response byte cap은 packet별로 고정한다. 1024차원 float JSON을 chunk당
  24 KiB와 envelope 256 KiB로 보수 산정해 운영 상한을 672 chunk, 최대 응답을 16 MiB로 제한하며
  packet byte cap은 해당 batch의 deterministic 예상 응답 이상이어야 한다. 기존 full-bundle/query
  cap은 넓히지 않는다.
- provider 성공 usage와 vector stage는 같은 DB transaction에서 commit한다. 같은 plan/batch의 서로
  다른 packet은 최초 claim 하나만 허용하며, provider handoff 뒤 stage가 확정되지 않은 attempt는
  `UNKNOWN_BILLING` 또는 terminal ambiguous로 남겨 자동 재호출하지 않는다.
- query 평가는 logical EXACT30 10개와 OA112 112개를 singleton contextual group으로 유지하되 component별
  physical call을 정확히 1회만 사용한다. Files API와 Batch API는 사용하지 않는다.
- 모든 document batch와 두 query batch가 완료되고 exact-30 top-5 hit 1.0, track Recall@5 0.8 이상,
  citation coverage 0.8 이상, direct-advice block 1.0, mixed-profile row 0을 만족한 경우에만 CAS한다.

### 변경하지 않는 경계

v1 OpenAPI/proto, RAG ask/status/history payload, exact-30 bytes, historical V24~V53, OA112 historical
artifact, Return/Experience placeholder는 변경하지 않는다. raw corpus/text/provider response/credential/
approval packet/vector receipt는 Git 또는 content-free receipt에 저장하지 않는다. Voyage tokenizer와
embedding의 실제 outbound는 privacy/payment evidence, credential, 새 `EXECUTION_HEAD`와 fresh exact
manifest 승인 전 0이다. Vertex, KIS, market provider, Decision/Signal/Risk/order authority도 열지 않는다.
Voyage packet binding은 이미 소비된 OA112 다운로드 증거를 재사용하지 않고 ignored local
`pre-s5-voyage-execution-evidence.v1.json`의 새 HEAD/tree/CI/security digest만 사용한다.

## EN

### Reason

CPU-only public BGE materialization took roughly ten hours for the 7,844 OA112 chunks and the former
execution shape could not resume a partial generation. The former Voyage operator also attempted to
submit the full corpus as one request, which could not satisfy the `voyage-context-4` request token cap.

### Locked change

- Further public CPU BGE execution is `TERMINALLY_SUPERSEDED_NO_FURTHER_BGE_RUN`. Existing partial
  generations remain immutable, inactive failure history and V54 records the terminal marker. The operator
  CLI rejects every BGE execution command before loading a model.
- The public bundle contains exact 30 EXACT30 sources, 112 OA112 sources, and the empty ordered-group-zero
  `OWNER_PRIVATE` sentinel. No owner content enters the public Voyage input.
- Per-source 0600 checkpoints under ignored `derived-ir/` bind the raw hash, parser version, tokenizer
  version, and source revision. At most four workers prepare sources and all 142 completed checkpoints
  are reusable with zero provider calls.
- The exact official `voyage-context-4` tokenizer hash is bound to the packet and local artifact. Existing
  chunk, locator, source, and order identities are packed deterministically below 110,000 tokens, leaving
  10,000 tokens of headroom below the 120,000-token provider cap.
- Every document batch has its own nonce, TTL, one logical and physical call, caps, and exact execution
  evidence. A successful batch is staged immediately in the append-only V54 ledger and is never called
  again during resume. Each global batch ID incorporates the whole-plan seed and official tokenizer hash,
  preventing cross-plan identity collisions. The first failed batch stops the remaining provider calls.
- Each document batch packet fixes its request/response byte cap. A conservative 24 KiB per 1024-dimensional
  float vector plus a 256 KiB envelope limits the operational batch to 672 chunks and 16 MiB, and the packet
  byte cap must cover that batch's deterministic response estimate. Existing full-bundle and query caps are
  not widened.
- Provider-success usage and vector staging commit in one database transaction. Only the first packet may
  claim a plan/batch member; an attempt that crossed provider handoff without a confirmed stage remains
  `UNKNOWN_BILLING` or terminal ambiguous and is never called automatically again.
- The ten logical EXACT30 and 112 logical OA112 queries remain singleton contextual groups but consume
  exactly one physical component-batch call each. Files API and Batch API remain disabled.
- CAS activation requires every document and query batch plus all retrieval and safety thresholds.

### Preserved boundary

This addendum does not change v1 OpenAPI/proto, RAG ask/status/history payloads, exact-30 bytes,
historical V24 through V53, historical OA112 artifacts, or Return/Experience placeholders. Raw corpus, text,
provider responses, credentials, approval packets, and vector receipts remain outside Git and
content-free evidence. Tokenizer and embedding outbound calls remain zero until privacy/payment evidence,
credentials, a new `EXECUTION_HEAD`, and a fresh exact manifest approval all exist. Vertex, KIS, market
providers, and Decision/Signal/Risk/order authority remain closed.
Voyage packet binding does not reuse consumed OA112 download evidence. It accepts only the new
HEAD/tree/CI/security digests in ignored local `pre-s5-voyage-execution-evidence.v1.json`.
