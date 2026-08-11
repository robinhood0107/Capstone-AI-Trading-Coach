# Pre-S5 Voyage official tokenizer bootstrap acquisition

## KR

### 이유

Voyage document batch는 official `voyage-context-4` tokenizer의 exact token count와 artifact
SHA-256을 알아야 authoring할 수 있다. local artifact가 없는 상태에서는 tokenizer 취득과 아직
계산할 수 없는 document batch packet을 같은 manifest에 미리 결속할 수 없다.

### 변경

- Voyage AI의 Hugging Face repository와 immutable commit
  `8ca946072a18e398cd61f2ad0243b56d0350b1db`의 `tokenizer.json` 한 파일만 허용한다.
- bootstrap packet은 current HEAD/tree/CI/security digest, nonce, 5분 TTL, byte cap 8 MiB,
  logical/physical cap 1, retry 0에 결속한다.
- 성공 bytes는 ignored local corpus의 0700/0600 artifact에만 저장하고 bounded JSON/tokenizer parser를
  통과한 observed SHA-256을 content-free receipt로 남긴다. Git 추적 artifact는 0이다.
- bootstrap packet은 성공·실패와 무관하게 single-use다. 자동 다운로드와 수동 `curl` 우회는 없다.
- observed tokenizer hash가 없으면 source checkpoint, token count, document/evaluation Window A packet을
  authoring하지 않는다.

OpenAPI/proto/RAG ask/history bytes와 기존 migration은 변경하지 않는다. Voyage embedding,
Vertex, market provider, account/order call authority는 이 bootstrap으로 열리지 않는다.

## EN

### Rationale

Exact Voyage document batches require the official `voyage-context-4` tokenizer bytes and their
observed SHA-256. When the local artifact is absent, the acquisition and the not-yet-computable
document batch packet set cannot truthfully share one pre-authored manifest.

### Change

- Permit only `tokenizer.json` from Voyage AI's Hugging Face repository at immutable commit
  `8ca946072a18e398cd61f2ad0243b56d0350b1db`.
- Bind the bootstrap packet to current HEAD/tree/CI/security evidence, a nonce, a five-minute TTL,
  an 8 MiB byte cap, logical/physical cap one, and retry zero.
- Publish validated bytes only to the ignored 0700/0600 local corpus and retain a content-free
  observed-SHA receipt. Tracked artifacts remain zero.
- Consume the packet on every attempted outcome. Automatic download and ad-hoc curl bypasses remain
  forbidden.
- Do not author checkpoints or Window A document/evaluation packets until the tokenizer hash exists.

This does not change public OpenAPI/proto/RAG payload bytes or existing migrations, and it grants no
Voyage embedding, Vertex, market-provider, account, or order authority.
