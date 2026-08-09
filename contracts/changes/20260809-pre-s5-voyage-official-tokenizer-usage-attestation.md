# Pre-S5 Voyage official-tokenizer usage attestation

## KR

### 변경 이유

기존 Pre-S5 Voyage full/query transport는 packet의 token cap과 provider 응답의
`usage.total_tokens`를 검증했지만, outbound 전 예상 token count가 BGE chunk metadata와
UTF-8 byte cap에 의존할 여지가 있었다. `voyage-context-4`의 model-specific official tokenizer
artifact hash를 packet과 usage receipt에 결속해 이 차이를 닫는다.

### 변경 범위

- local approved root의 고정 `artifacts/voyage-context-4/tokenizer.json`만 0700/0600,
  regular file, owner, link-count, `O_NOFOLLOW`, SHA-256 boundary로 읽는다. artifact의 자동
  download, provider `count_tokens` call, byte/character approximation은 추가하지 않는다.
- full generation과 query embedding packet은 `tokenizerSha256`을 필수로 포함한다. packet hash와
  local artifact hash가 다르면 DB lease와 provider socket 모두 0이다.
- V51은 V38/V46 historical function bytes와 rows를 바꾸지 않고, 새 versioned reservation/commit
  capability에 `official_tokenizer_sha256`와 `expected_input_tokens`만 append한다. provider
  `usage.total_tokens`와 실제 cost는 계속 별도 actual receipt다.
- raw tokenizer bytes, canonical text, question, vector, provider body, credential, owner ID는 새
  table/receipt/log에 저장하지 않는다. writer는 direct table privilege 없이 SECURITY DEFINER
  capability만 사용한다.

### 비목표와 gate

이 변경은 Voyage artifact acquisition, OA112 download, Voyage socket, query fallback, batch/files API,
Vertex/market/KIS provider call을 활성화하지 않는다. local official artifact rights/hash evidence,
organization privacy/payment evidence, credential, exact current-HEAD approval packet이 함께 있을 때만
one-shot physical call이 가능하다.

## EN

### Rationale

The existing Pre-S5 Voyage transports checked a packet token cap and provider-reported
`usage.total_tokens`, but their pre-send estimate could still rely on BGE chunk metadata or a UTF-8
byte bound. This amendment binds the model-specific `voyage-context-4` official tokenizer artifact hash
to the packet and sanitized usage receipt.

### Scope

- Only the fixed local `artifacts/voyage-context-4/tokenizer.json` may be read through a 0700/0600,
  regular-file, owner, link-count, `O_NOFOLLOW`, SHA-256 boundary. There is no automatic artifact
  download, provider `count_tokens` call, or byte/character approximation.
- Full-generation and query packets require `tokenizerSha256`; a packet/artifact mismatch reaches neither
  the DB lease nor a provider socket.
- V51 preserves V38/V46 historical function bytes and rows, while versioned new capabilities append only
  `official_tokenizer_sha256` and `expected_input_tokens`. Provider `usage.total_tokens` and actual cost
  remain separate actual receipts.
- No tokenizer bytes, canonical text, question, vector, provider body, credential, or owner identifier is
  persisted in the new tables, receipts, or logs; the writer has capability functions only.

### Non-goals and gate

This amendment does not activate Voyage artifact acquisition, OA112 download, a Voyage socket, query
fallback, Files/Batch APIs, or any Vertex/market/KIS provider call. A physical one-shot still requires
local artifact rights/hash evidence, organization privacy/payment evidence, credentials, and an exact
current-HEAD approval packet.
