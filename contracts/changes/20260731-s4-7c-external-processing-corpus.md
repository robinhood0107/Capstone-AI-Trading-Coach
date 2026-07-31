# KR: S4.7C 외부처리 corpus revision / EN: S4.7C external-processing corpus revision

## KR

### 목적

기존 S4.7B exact-30 corpus를 수정하지 않고, 프로젝트가 직접 작성한 sanitized card만
외부처리 가능하다고 검증한 새 immutable revision과 generation을 추가한다. 이 변경은
upstream PDF/HTML/API 응답, 뉴스 원문·기사 metadata, private path 또는 provider payload의
전송 권한을 만들지 않는다.

### 고정 경계

- 기존 `s4_7b_internal_v1` manifest file SHA-256은
  `d772ab9a54c5477afeccfd41cd41e496645967dee59dd50c0bcc304ae3c95558`, corpus hash는
  `7f2b4d72dcbaccf57cbe49a980973b17b4a9bfd85bec4694fd66fd7fd2a9decd`로 유지한다.
- 새 `s4_7c_external_v1`은 동일한 `sourceId`/`cardId`/canonical body exact 30을 사용하고,
  corpus hash는 `bdc42bfb735b411156ec2f79626d6fd2cf56662c57d83e2cdb960fb74e7b0e04`다.
- 새 card 30개만 `PROJECT_AUTHORED_SANITIZED_CARD`, `PUBLIC`,
  `externalProcessingAllowed=true`, `LICENSE_AND_CONSENT_VERIFIED`를 사용한다.
- manifest는 card별 license/consent receipt 30개와 old/new body hash 관계를 포함한다.
- profile은 서버가 선택한다. public request/response, RAG ask/history, OpenAPI에는 profile
  selector나 새 field를 추가하지 않는다.
- provider에 투영 가능한 context는 새 active generation의 verified/current top 5 이하이며,
  question의 PII/advice/consent gate는 card 승인과 별도로 통과해야 한다.

### 저장·활성화

Flyway V21은 기존 revision을 보존한 채 `s4-7c-source-card-v2` revision만 append한다.
external-processing 허용은 exact registry와 승인 marker 조합으로 제한한다. 완전한 30-row
generation과 vector parity, retrieval gate를 통과한 경우에만 admin CAS로 pointer를 바꾸며,
stale CAS는 전체 transaction을 rollback한다.

실제 local BGE 검증 결과 old generation은 `DISABLED`, 새 generation
`rag_gen_789b3ba9589ad399373194c0e3c0e76f`는 `ACTIVE`, active row는 정확히 1개다.
old/new canonical body와 vector는 30/30 동등했고 expected top-5 hit rate는 모두 1.0이었다.
외부 provider, Voyage, Gemini, OpenAI physical call은 0이다.

## EN

### Purpose

Add a new immutable revision and generation for the exact project-authored sanitized cards that
passed the external-processing review, without modifying the existing S4.7B exact-30 corpus. This
change does not authorize transmitting upstream PDF/HTML/API responses, news content or article
metadata, private paths, or provider payloads.

### Locked boundaries

- The `s4_7b_internal_v1` manifest file and corpus hashes remain byte-identical.
- `s4_7c_external_v1` keeps the same exact 30 `sourceId`, `cardId`, and canonical bodies and uses
  corpus hash `bdc42bfb735b411156ec2f79626d6fd2cf56662c57d83e2cdb960fb74e7b0e04`.
- Only the 30 new cards are `PROJECT_AUTHORED_SANITIZED_CARD`, `PUBLIC`, externally processable,
  and `LICENSE_AND_CONSENT_VERIFIED`.
- The manifest contains 30 deterministic license/consent receipts and the old/new body-hash
  relationship.
- The server selects the profile. No selector or new field is added to public RAG contracts,
  OpenAPI, or history payloads.
- Provider context is bounded to at most five verified/current chunks in the new active generation;
  the question must pass its independent privacy, advice, and consent gate.

### Persistence and activation

Flyway V21 appends `s4-7c-source-card-v2` revisions and preserves prior rows. The exact registry and
approval marker constrain the external-processing boundary. Only a complete 30-row generation that
passes vector parity and retrieval gates may replace the pointer through the admin CAS; stale CAS
attempts roll back atomically.

The local BGE evidence records exactly one active generation, 30/30 body and vector equivalence, and
an expected top-5 hit rate of 1.0 for both profiles. External provider, Voyage, Gemini, and OpenAI
physical calls remained zero.

## 검증 / Verification

```bash
uv run --frozen python capstone-rag/generate_s4_7c_external_corpus.py --check
cd workspaces/decision-platform/python-services
uv run --frozen pytest -q tests/rag/test_external_processing_corpus.py
TMPDIR=/tmp uv run --frozen pytest -q tests/rag/test_external_generation_postgres.py
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 S4_7C_EXTERNAL_GENERATION_TESTS=1 \
  TMPDIR=/tmp uv run --frozen pytest -q tests/rag/test_s4_7c_external_generation_local.py -s
```
