# KR: S2.1 Principle CRUD·preset·version 계약 amendment

## 변경 이유

Issue #38은 S2.1 runtime 구현 전에 wire, owner/CAS, preset, immutable history, DB privilege,
finance idempotency 제외, OpenAPI drift 방식을 하나의 검증 가능한 계약으로 고정한다. 기존 문서는
빈 rules를 허용하는 부분 수정 예시, 불명확한 status/preset, owner 사후 검사와 별도 `@Version`
가능성을 남겨 같은 구현을 보장하지 못했다. PR #37이 DB actor trust root를 이미 병합했으므로
새 인증 설계를 만들지 않고 그 internal user ID와 회수 의미를 회귀 gate로 사용한다.

## 변경 범위

- `contracts/catalogs/s2-1-principle-contract.v1.json`을 exact 3×8 preset, rule tuple/range/scale,
  DTO, limit, enum, operation/error allowlist의 machine-readable 단일 진실로 추가한다.
- catalog에서 Draft 2020-12 standalone schemas와 positive/negative/page/error fixture를
  결정론적으로 생성한다. duplicate JSON key, NaN/Infinity, 비정규 Decimal spelling을
  fail-closed한다.
- create는 `presetId,title` 필수, `mode,rules` 선택이다. rules 생략은 preset deep copy이고
  rules 제공은 1~8개 전체 replacement다. PUT은
  `expectedVersion,title,mode,status,rules` 전부를 요구하고 preset은 immutable이다.
- status는 `ACTIVE|ARCHIVED`만 사용하며 DELETE endpoint는 없다. canonical no-op은 200이지만
  version/time/history/audit를 바꾸지 않는다.
- current wire field는 `version`, stale conflict details만 `currentVersion`이다. owner lookup과
  actual update는 `principle_id + user_id + current_version` SQL CAS를 사용한다.
  missing/cross-owner는 같은 owner-scoped query shape와 동일 404다.
- owner list와 version history는 HMAC-authenticated 15분 cursor를 사용한다.
  `PRINCIPLE_CURSOR_HMAC_KEY`는 JWT/login-scope key와 분리한다.
- V8은 sparse Principle/version row가 있거나 reserved preset identity가 충돌하면 DDL 전에
  중단한다. 추정 backfill·overwrite·delete는 하지 않는다. exact 3×8 seed, immutable full
  snapshot, sanitized audit, RESTRICT FK, table/column least privilege를 설치한다.
- `/api/v1/principles/**`를 금융 idempotency replay 대상에서 제거한다. create timeout은 blind
  retry하지 않으며 owner list는 사람이 후보를 확인하는 best-effort 수단이지 commit 증명이 아니다.
- OpenAPI plugin 1.9.0으로 실제 Spring 문서를 생성한다. tracked root는 OAS 3.1.1과 OAS base
  dialect를 사용한다. normalizer는 generated `3.1.0`을 `3.1.1`로 바꾸는 root 한 field와
  deterministic formatting만 허용한다.
- canonical catalog bytes의 SHA-256은
  `27c9a86201ce6263d10e7d915947dc694bfbc7b684279cde43d63e1201e40d0f`이며 OpenAPI
  `x-s2-1-contract-sha256`과 일치한다.

이 amendment는 Principle controller/service/repository/evaluator를 추가하지 않는다. tracked
OpenAPI에는 현재 실제 runtime path만 남는다. S2.1 path와 component는 amendment 병합 뒤 별도
implementation PR이 controller와 함께 생성한다. `STRICT` 저장 계약은 RiskEngine enforcement가
이미 구현됐다는 의미가 아니다.

## 배포 전 migration gate

V8 적용 전에 운영자는 read-only connection으로 아래 결과가 모두 `0`인지 확인한다. 하나라도
0이 아니면 배포를 멈추고 별도 data migration/retention 결정을 승인받는다.

```sql
BEGIN READ ONLY;
SELECT
  (SELECT count(*) FROM principles) AS principle_rows,
  (SELECT count(*) FROM principle_versions) AS principle_version_rows,
  (SELECT count(*) FROM principle_presets) AS principle_preset_rows;
COMMIT;
```

그 다음 maintenance window에서 migration role로 V8을 적용하고 Flyway version 8, preset 3개,
각 preset 8 rules, runtime privilege를 검증한다. V8은 forward-only다. rollback은 table/column/seed를
삭제하지 않고 이전 application binary/config를 복원한다. 현재 amendment runtime은 Principle
table을 사용하지 않으므로 controller rollback이나 dual contract를 발명하지 않는다.

## 호환성·소비자 영향

- Dashboard/팀원 A: exact KR/EN preset name/description/disclaimer를 표시하고 PUT에서 form 전체
  state를 보낸다. archive/reactivate는 status PUT이며 create timeout을 자동 재시도하지 않는다.
- Return Engine/팀원 B: public rule은 exact 8개다. ratio는 fraction, loss/MDD는 signed ratio,
  money/count는 integer이며 immutable `principleId + version` snapshot을 소비한다.
- Backend/운영: DB 검증된 JWT `sub`만 owner로 사용한다. raw userId/title/cursor/rejected
  payload를 로그·metric label에 남기지 않는다. runtime preset mutation, hard delete,
  version/audit 과거 row mutation을 허용하지 않는다.
- 다른 workspace 구현 파일, root contract의 비관련 schema, provider 호출, 주문 경로는 변경하지
  않는다.

operation별 schema/fixture/error/page mapping과 copy-paste 검증 명령은 `contracts/README.md`의
S2.1 artifact map에 있다.

# EN: S2.1 Principle CRUD, preset, and version contract amendment

## Reason

Issue #38 locks one testable contract for the S2.1 wire format, owner-scoped CAS, presets, immutable
history, database privileges, finance-idempotency exclusion, and OpenAPI drift before runtime
implementation. The previous documentation allowed an empty-rules partial update, ambiguous
statuses and presets, and multiple owner/versioning interpretations. PR #37 has already merged the
database actor trust root, so this amendment consumes its internal user ID and revocation semantics
as regression gates instead of redesigning authentication.

## Scope

- `contracts/catalogs/s2-1-principle-contract.v1.json` is the machine-readable source of truth for
  the exact three-by-eight preset matrix, rule tuples/ranges/scales, DTOs, limits, enums, operations,
  and errors.
- Draft 2020-12 standalone schemas and positive, negative, page, and error fixtures are generated
  deterministically. Duplicate JSON keys, NaN/Infinity, and non-canonical decimal forms fail closed.
- Create requires `presetId,title`; `mode,rules` are optional. Omitted rules deep-copy the preset,
  while supplied rules are a complete one-to-eight-item replacement. PUT requires
  `expectedVersion,title,mode,status,rules`; preset provenance is immutable.
- Lifecycle is `ACTIVE|ARCHIVED` with no DELETE endpoint. A canonical no-op returns 200 without
  changing version, timestamps, history, or audit.
- Current representations expose `version`; only stale-conflict details expose `currentVersion`.
  Lookup and mutation use owner-scoped SQL CAS. Missing and cross-owner resources share one 404.
- Owner-list and history cursors are HMAC-authenticated for 15 minutes with a cursor key separated
  from JWT and login-scope keys.
- V8 stops before DDL when sparse Principle/version data or reserved preset identities exist. It
  performs no guessed backfill, overwrite, or deletion, and installs the exact seed, full immutable
  snapshots, sanitized audit, RESTRICT foreign keys, and least privilege.
- Principle routes are excluded from finance idempotency. Clients must not blindly retry an
  indeterminate create; the owner list is only a human-assisted candidate check.
- Spring generates the actual OpenAPI with plugin 1.9.0. The tracked document pins OAS 3.1.1 and the
  OAS base dialect. Normalization may only patch root `3.1.0` to `3.1.1` and format deterministically.
- The canonical catalog SHA-256 is
  `27c9a86201ce6263d10e7d915947dc694bfbc7b684279cde43d63e1201e40d0f`, equal to the OpenAPI
  `x-s2-1-contract-sha256` extension.

This amendment adds no Principle controller, service, repository, or evaluator. The tracked OpenAPI
contains only runtime paths that actually exist. A separate fresh-main implementation PR will add
S2.1 paths and components together with the controllers. Persisting `STRICT` does not claim that
RiskEngine enforcement is already implemented.

## Migration and compatibility

Before V8, operators must run the read-only query above and require all three counts to be zero.
Otherwise deployment stops for an explicitly approved data-migration or retention decision. After
V8, verify Flyway version 8, three presets with eight rules each, and exact runtime privileges. V8 is
forward-only; rollback restores the previous application binary/configuration without deleting its
tables, columns, or seed.

The Dashboard must render the exact localized catalog, send full replacement updates, and avoid
blind create retries. The Return Engine consumes the exact eight tuples and immutable
`principleId + version` snapshots with the documented numeric units. The backend trusts only the
database-verified JWT subject and must not log raw owner IDs, titles, cursors, or rejected payloads.
Other workspace implementations, unrelated contracts, provider calls, and order paths are out of
scope. The S2.1 artifact map and reproducible commands are in `contracts/README.md`.
