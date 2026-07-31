# KR: S4.7B source-card v2 union 계약 / EN: S4.7B source-card v2 union contract

## KR

### 결정

S4.7B project source card를 작성하기 전에 `rag-source-card-v2`를 별도 계약으로 추가한다.
기존 v1 schema와 fixture는 수정하지 않는다.

- v1 schema SHA-256:
  `89f25e66d8165ceb813045e17c689e1000bb86f710f8d8c0acb22ccc6d0c846c`
- v1 positive fixture SHA-256:
  `6a77525100c67a9bfcc1a966f1550cfe9bd19f73179544d716a5b8e963fea0c4`
- 승인:
  `AUTH_SOURCE_CARD_V2_CONTRACT=APPROVED`

### 계약

v2는 `cardVariant`로 구분되는 closed union이다.

1. `OFFICIAL_UPSTREAM_CARD`
   - exact upstream reference allowlist에서 1~5개를 사용한다.
   - 하나 이상의 upstream ID가 card `institution`과 일치해야 한다.
   - 공식 API·service·product evidence class만 허용한다.
2. `SCHOLARLY_PRIMARY_CARD`
   - `upstreamSourceIds`는 빈 배열이다.
   - DOI, ISBN 또는 공식 publisher·author archive·institution URL을 사용한다.
   - title, authors, year, venue, edition/version을 구조화해 보존한다.
   - `PRIMARY_RESEARCH`, `OFFICIAL_REPORT`, `OFFICIAL_STANDARD`만 허용한다.

두 variant 모두 canonical URL/hash, bounded evidence hash, access/license/attribution,
retention, allowed use, forbidden inference, limitation, contradiction, NFC/UTF-8,
bounded size와 안전한 HTTPS locator를 요구한다. model-sensitive claim은
`{key, statement}` 구조의 stable assumption을 1~12개 요구한다.

### 외부 처리 경계

`externalProcessingAllowed`는 다음 exact gate를 따른다.

- `RAW_OR_REFERENCE_EVIDENCE`는 항상 `false`이고 gate는 `NOT_GRANTED`다.
- `PROJECT_AUTHORED_SANITIZED_CARD`도 기본은 `false`와 `NOT_GRANTED`다.
- sanitized card를 `true`로 승격하려면 새 immutable revision에서
  `LICENSE_AND_CONSENT_VERIFIED`를 명시해야 한다.

이번 계약과 migration fixture는 모두 외부 처리 `false`다. 이 결정은 provider 호출,
외부 전송, live account/order/market 접근을 승인하지 않는다.

### Migration

`rag-source-card-v2.official-migration.valid.json`은 v1 공식 positive fixture의 claim,
lineage, URL/hash, access/license/retention, attribution, 금지 추론을 의미 보존한다.
추가되는 값은 v2 discriminator, sanitized content class, `NOT_GRANTED` gate,
`modelSensitive=false`뿐이다. 실제 card migration은 새 v2 파일로 수행하며 v1 artifact를
in-place 변경하지 않는다.

### 범위

이 변경은 schema, deterministic generator, positive/negative fixture, Python/Spring
validator 및 canonical schema byte parity만 포함한다. 실제 source-card 본문, corpus
manifest, DB/API/retrieval/generation runtime은 후속 변경이다. 수동 security scan은
승인된 전체 S4 구현이 끝난 최종 통합 HEAD에서 한 번 수행한다.

Python validator의 runtime dependency인 `jsonschema[format]`은 `4.26.0`으로 고정한다.
production `pyproject.toml`과 `uv.lock`만 변경한 carrier commit
`13b7b21a904fc37ce0947d5da2de7d04794e497a`를 S1.4X reference lock과 correctness
workflow allowlist에 연결하며, PR은 이 commit의 조상성을 보존하는 merge commit
방식으로만 병합한다.

## EN

### Decision

Add `rag-source-card-v2` as a separate contract before authoring the S4.7B
project source cards. The existing v1 schema and fixture remain unchanged.

- v1 schema SHA-256:
  `89f25e66d8165ceb813045e17c689e1000bb86f710f8d8c0acb22ccc6d0c846c`
- v1 positive fixture SHA-256:
  `6a77525100c67a9bfcc1a966f1550cfe9bd19f73179544d716a5b8e963fea0c4`
- approval:
  `AUTH_SOURCE_CARD_V2_CONTRACT=APPROVED`

### Contract

V2 is a closed union discriminated by `cardVariant`.

1. `OFFICIAL_UPSTREAM_CARD`
   - Uses one to five entries from the exact upstream-reference allowlist.
   - At least one upstream ID must match the card institution.
   - Only official API, service, or product evidence classes are allowed.
2. `SCHOLARLY_PRIMARY_CARD`
   - Uses an empty `upstreamSourceIds` array.
   - Requires a DOI, ISBN, or official publisher, author-archive, or institution URL.
   - Preserves structured title, authors, year, venue, and edition/version metadata.
   - Allows only `PRIMARY_RESEARCH`, `OFFICIAL_REPORT`, or `OFFICIAL_STANDARD`.

Both variants require canonical URL/hash, a bounded evidence hash, access,
license, attribution, retention, allowed-use, forbidden-inference, limitation,
contradiction, NFC/UTF-8, bounded-size, and safe-HTTPS-locator controls.
Model-sensitive claims require one to twelve stable `{key, statement}`
assumptions.

### External-processing boundary

`externalProcessingAllowed` follows this exact gate:

- `RAW_OR_REFERENCE_EVIDENCE` is always `false` with `NOT_GRANTED`.
- `PROJECT_AUTHORED_SANITIZED_CARD` also defaults to `false` with `NOT_GRANTED`.
- Promoting a sanitized card to `true` requires a new immutable revision with
  `LICENSE_AND_CONSENT_VERIFIED`.

This contract and its migration fixture keep external processing disabled. The
decision grants no provider call, outbound transfer, live account/order, or
market access.

### Migration

`rag-source-card-v2.official-migration.valid.json` preserves the v1 official
positive fixture's claim, lineage, URL/hash, access/license/retention,
attribution, and forbidden inferences. It adds only the v2 discriminator,
sanitized content class, `NOT_GRANTED` gate, and `modelSensitive=false`.
Actual migration creates new v2 files and never rewrites v1 artifacts in place.

### Scope

This change contains only the schema, deterministic generator,
positive/negative fixtures, Python/Spring validators, and canonical schema-byte
parity. Source-card bodies, the corpus manifest, and DB/API/retrieval/generation
runtime remain follow-up work. The manual security scan runs once on the final
integrated HEAD after all approved S4 implementation is complete.

The Python validator pins its `jsonschema[format]` runtime dependency to
`4.26.0`. The carrier commit
`13b7b21a904fc37ce0947d5da2de7d04794e497a`, whose own diff contains only the
production `pyproject.toml` and `uv.lock`, is bound into the S1.4X reference
lock and correctness-workflow allowlist. The PR must use a merge commit so this
carrier remains an ancestor.
