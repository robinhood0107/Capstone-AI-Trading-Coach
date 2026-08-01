# KR: S4.7D OA140·개인 문서 RAG v2 계약 / EN: S4.7D OA140 and owner-private RAG v2 contract

## KR

### 결정

기존 P1 exact-30, source-card v1/v2, RAG v1 OpenAPI/proto와 평가 산출물을 수정하지 않고
S4.7D 확장을 별도 v3/v2 계약으로 추가한다. 이번 변경의 상태는
`S4_7D_CONTRACT=LOCKED`이며 parser/OCR, DB migration, active API, OA release corpus는 후속
PR이다.

catalog, separate OpenAPI, proto와 descriptor의 exact digest는 generated digest artifact와
deterministic generator가 소유한다. 변경 기록에 같은 값을 수동 복제하지 않는다.

### Source와 Document IR

source v3는 `PROJECT_SOURCE_CARD`, `OPEN_ACCESS_DOCUMENT`,
`OWNER_LOCAL_DOCUMENT`의 closed union이다. source/revision ID, bibliographic metadata,
curriculum track, HTTPS locator 또는 opaque document ID, MIME, raw/normalized hash,
license/access evidence, 처리·재배포·외부 LLM 권한, parser/OCR backend와 model hash를
구조화한다. owner 문서는 path와 URL을 저장하거나 반환하지 않는다.

Document IR은 heading, paragraph, list, table, formula, caption을 공통 block으로 나타내고
page/slide/sheet/section locator, contiguous reading order, OCR confidence와 backend evidence를
보존한다. 원본 경로, credential, provider body는 IR에 들어갈 수 없다.

### OA curriculum과 release gate

OA manifest는 투자 코치에 필요한 경제·금융·퀀트 14개 track을 exact order로 고정한다.
`RELEASED`는 track별 8~10개, 전체 112~140개와 다음 역할의 동시 coverage를 요구한다.

1. 공개 교재 또는 강의
2. 원 연구
3. 현대 review, replication 또는 correction

이번 contract-only PR의 manifest는 `DRAFT`이고 source 수는 0이다. 검증되지 않은 URL,
license 또는 원문 hash를 계약 완료처럼 채우지 않는다.

### RAG v2와 권한 경계

`POST /api/v2/rag/ask`는 v1 질문 의미를 유지하지만 corpus/profile/topK 입력을 허용하지
않는다. 서버가 exact-30, OA, 요청 owner-private generation을 자동 pin한다. full bundle이
준비되지 않으면 typed `CORPUS_NOT_READY`이고 v1 exact-30은 계속 사용할 수 있다.

v2 citation은 `PUBLIC_WEB`과 `LOCAL_DOCUMENT`의 tagged union이다. 전자는 HTTPS canonical
URL과 locator를, 후자는 opaque document ID, sanitized display name과 locator만 반환한다.
local absolute path는 API, history, status, log에 노출하지 않는다.

RAG는 근거·설명만 제공하고 Signal, RiskDecision, 주문 판단/hash/feature에 연결하지 않는다.
외부 LLM은 source 권리와 owner corpus-level opt-in을 모두 통과한 chunk만 받을 수 있다.
하나라도 불충분하면 전체 요청을 retrieval-only로 처리한다.

### Processing mode 전환

새 active 계약은 `LOCAL_EPHEMERAL_PARSE`만 사용하고 파일별 approval ID, nonce, TTL을
요구하지 않는다. `LICENSED_EPHEMERAL_LOCAL`은 과거 schema와 날짜가 고정된 변경 기록에만
historical-only 값으로 보존한다. 이 전환은 DRM, 로그인, paywall 우회나 무단 crawling을
허용하지 않으며 local parse 권한을 외부 LLM 전송 권한으로 확대하지 않는다.

### 후속 구현 순서

1. 9-format safe parser, OCR benchmark, 단일 production backend와 BAT
2. next-free migration, immutable bundle, owner RLS, deletion, v2 runtime/history
3. 검증된 112~140 source manifest, 콘텐츠 설정, 동일 digest 배포 metadata/tools

## EN

### Decision

Add S4.7D as separate v3/v2 contracts without modifying the P1 exact-30 corpus,
source-card v1/v2, RAG v1 OpenAPI/proto, or evaluation artifacts. This change is
`S4_7D_CONTRACT=LOCKED`; parser/OCR, persistence, active endpoints, and the OA release corpus
remain follow-up work.

### Source and Document IR

Source v3 is a closed union of `PROJECT_SOURCE_CARD`, `OPEN_ACCESS_DOCUMENT`, and
`OWNER_LOCAL_DOCUMENT`. It records versioned provenance, rights flags, content hashes, and
parser/OCR identity while forbidding owner paths and URLs from the owner-local variant.

Document IR represents headings, paragraphs, lists, tables, formulas, and captions with
page/slide/sheet/section locators, contiguous reading order, and OCR evidence. It cannot carry an
original path, credential, or provider body.

### OA curriculum and release gate

The OA manifest fixes fourteen investment-coach economics, finance, and quantitative tracks.
A `RELEASED` manifest requires eight to ten sources per track, 112 to 140 in total, and teaching,
original-research, and modern review/replication/correction coverage in every track. This contract
PR intentionally publishes an empty `DRAFT`, not unverified URLs, licenses, or content hashes.

### RAG v2 and authority boundary

`POST /api/v2/rag/ask` retains v1 question semantics but accepts no corpus, profile, or top-k
selector. The server pins exact-30, OA, and the requesting owner's private generation. An
incomplete full bundle returns typed `CORPUS_NOT_READY`; v1 exact-30 remains available.

V2 citations are a `PUBLIC_WEB | LOCAL_DOCUMENT` tagged union. Local citations expose only an
opaque document ID, sanitized display name, and locator. RAG remains explanatory evidence with no
Signal, RiskDecision, order, hash, or feature authority. External LLM processing requires both
source rights and owner corpus-level opt-in; otherwise the whole request is retrieval-only.

### Processing-mode transition

The new active contract uses only `LOCAL_EPHEMERAL_PARSE` and has no per-file approval ID, nonce,
or TTL. `LICENSED_EPHEMERAL_LOCAL` remains only as a historical value in old schemas and dated
records. This does not authorize DRM, login, or paywall bypass, unauthorized crawling, or external
LLM transmission.

### Follow-up order

1. Safe parsers for nine format families, OCR benchmark, one production backend, and BAT launchers
2. The next-free migration, immutable bundles, owner RLS, deletion, and v2 runtime/history
3. A validated 112-to-140-source manifest, content setup, and same-digest distribution metadata/tools

## 검증 / Verification

```bash
uv run --frozen python contracts/generate_s4_7d_rag_v2_contracts.py --check
uv run --project workspaces/decision-platform/python-services --frozen \
  python contracts/generate_rag_v2_proto.py --check
uv run --frozen python -m unittest discover -s contracts/tests -v
uv run --frozen python contracts/validate.py
```
