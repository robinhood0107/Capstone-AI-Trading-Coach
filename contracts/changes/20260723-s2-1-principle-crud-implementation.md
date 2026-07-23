# KR: S2.1 Principle CRUD·preset·versioning runtime 구현

## 변경 이유

Issue #40은 병합된 `s2-1-principle-contract/v1` amendment를 실제 Decision Platform runtime에
연결한다. 계약을 다시 해석하지 않고 canonical catalog, V8 schema/seed/privilege, DB 검증 JWT
actor를 그대로 소비한다.

## 구현 범위

- `GET /api/v1/principle-presets`와 사용자별 create/list/detail/full-update/history의 6개
  operation을 추가한다.
- create는 active preset snapshot을 transaction 안에서 deep-copy하고 principle, version 1,
  allowlisted audit payload를 원자적으로 저장한다.
- update는 `principle_id + user_id + current_version` 조건의 단일 SQL CAS를 사용한다. 실제
  변경만 version을 올리고 full snapshot/audit를 append하며 canonical no-op은 어떤 row도
  추가하거나 갱신하지 않는다.
- 모든 lookup과 history query는 owner predicate를 SQL에 포함한다. missing과 cross-owner는
  같은 404이고 stale owned version만 현재 version을 포함한 409다.
- request parser는 duplicate/unknown field, title Unicode/control, rule tuple/range/scale/severity,
  query/path/cursor 오류를 allowlisted violation으로 수렴시키고 rejected raw value를 반사하지 않는다.
- cursor는 HMAC-SHA-256 signature와 purpose-separated subject binding을 사용하고
  route/resource/sort/size/15분 TTL을 검증한다. cursor key는 JWT, login scope, demo credential
  separation key와 startup에서 분리 검증한다.
- Principle route는 finance idempotency path에 추가하지 않는다. provider 또는 주문 경로는
  호출하거나 변경하지 않는다.
- 실제 Spring controller에서 생성한 S2.1 path/component를 canonical OpenAPI에 기록하고 drift
  gate를 implementation mode로 전환한다.

## 호환성·운영 영향

- Dashboard는 create timeout을 자동 재시도하지 않고 owner list 후보를 사용자가 확인하게 한다.
- Return Engine은 `principleId + version` immutable snapshot과 canonical rule 순서를 소비한다.
- V8은 이미 병합된 forward-only migration이다. 이 변경은 새 migration이나 기존 row backfill을
  추가하지 않는다.
- `STRICT` 값의 저장은 지원하지만 RiskEngine enforcement 구현을 의미하지 않는다.

# EN: S2.1 Principle CRUD, preset, and versioning runtime implementation

## Reason

Issue #40 connects the merged `s2-1-principle-contract/v1` amendment to the Decision Platform
runtime. It consumes the canonical catalog, V8 schema/seed/privileges, and database-verified JWT
actor without redefining those contracts.

## Scope

- Add the six preset, create, owner-list, detail, full-update, and history operations.
- Atomically create the Principle row, immutable version-one snapshot, and allowlisted audit payload
  while deep-copying an active preset inside the transaction.
- Use one `principle_id + user_id + current_version` SQL CAS for real updates. Append a full snapshot
  and audit only after a successful change; a canonical no-op changes no row or timestamp.
- Keep the owner predicate in every lookup and history query. Missing and cross-owner resources
  share one 404, while only an owned stale version exposes the current version in a 409.
- Fail closed on duplicate and unknown fields, Unicode/control title violations, rule
  tuple/range/scale/severity violations, and invalid path/query/cursor input without reflecting raw
  rejected values.
- Authenticate cursors with HMAC-SHA-256 and a purpose-separated subject binding, including
  route/resource/sort/size and a 15-minute TTL. Verify cursor-key separation at startup.
- Keep Principle outside finance idempotency and make no provider or order call.
- Track only the OpenAPI paths/components generated from the real Spring controller and run the
  drift gate in implementation mode.

## Compatibility and operations

The Dashboard must not blindly retry an indeterminate create. The Return Engine consumes immutable
`principleId + version` snapshots in canonical rule order. V8 remains the already-merged
forward-only migration; this change adds no migration or guessed backfill. Persisting `STRICT` does
not claim RiskEngine enforcement.
