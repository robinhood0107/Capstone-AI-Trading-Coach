# KR: S2.1 actor trust-root 선행 계약

## 변경 이유

S2.1 Principle owner 검증은 JWT actor ID를 DB foreign key와 같은 namespace로 신뢰해야 한다. 기존 demo 인증은 username subject, 별도 userId claim, 평문 credential property, memory account를 사용해 DB `users.user_id`와 하나의 trust root를 만들지 못했다. Principle 계약·런타임 변경 전에 이 인증 선행 조건을 독립 PR로 고정한다.

## 변경 범위

- DB `users`를 identity/hash/role/status/`security_version`의 단일 진실 소스로 사용한다.
- V7이 exact `usr_demo_user`/`usr_demo_admin` row와 BCrypt strength-12 hash placeholder를 fail-closed로 seed한다.
- JWT를 HS256으로 고정하고 exact issuer/single audience, internal-user-ID subject, `iat`/`exp`/role/`securityVersion`을 검증한다.
- 매 authenticated request에서 DB status/role/version을 재검증해 즉시 회수를 보장한다.
- login password는 DB BCrypt verifier와 동일 cost dummy path로 검증하고, limiter scope는 JWT와 다른 key의 purpose/version HMAC으로 만든다.
- operator-only credential rotation과 pre/post deployment cutover smoke task를 추가한다. raw password/token/hash는 추적 파일, argv, 로그, audit, evidence에 넣지 않는다.
- login OpenAPI를 public operation으로 표시하고 response `userId`가 JWT `sub`와 같은 internal owner ID임을 문서화한다.

## 호환성·운영 영향

이 변경은 인증 의미의 intentional breaking cutover다. 기존 token은 새 `sub`/issuer/audience/version 계약을 만족하지 않아 배포 후 401이며, 사용자는 다시 로그인해야 한다. 배포 전 secret store에 두 BCrypt hash, JWT secret/issuer/audience, 별도 login-scope HMAC key를 준비한다. V7은 additive/forward-only이며 rollback 시 column/row를 삭제하지 않는다. credential rotation은 V7 재실행이 아니라 별도 privileged transaction과 sanitized audit를 사용한다.

Dashboard는 `data.user.userId`/JWT `sub`를 owner ID로 사용하고 username을 owner key로 쓰지 않는다. Return Engine payload/schema와 다른 workspace 구현에는 변경이 없다. Principle API/schema/idempotency, S2.2/S2.3, provider 호출, 주문은 범위 밖이다.

# EN: S2.1 actor trust-root prerequisite contract

## Reason

S2.1 Principle ownership must trust a JWT actor ID in the same namespace as the database foreign key. The previous demo authentication used a username subject, a separate userId claim, plaintext credential properties, and memory-only accounts. This prerequisite isolates and closes that trust-root gap before any Principle contract or runtime change.

## Scope

- Database `users` is the sole source of truth for identity, password hash, role, status, and `security_version`.
- V7 fail-closes while seeding the exact `usr_demo_user` and `usr_demo_admin` rows from untracked BCrypt strength-12 placeholders.
- JWT validation pins HS256 and requires the exact issuer, exact single audience, internal-user-ID subject, `iat`, `exp`, role, and `securityVersion`.
- Every authenticated request revalidates current database status, role, and version for immediate revocation.
- Login uses the database BCrypt verifier and an equal-cost dummy path. Login limiter scopes use a purpose/version HMAC key distinct from the JWT key.
- Operator-only credential rotation and pre/post deployment cutover smoke tasks keep raw passwords, tokens, and hashes out of tracked files, argv, logs, audit payloads, and evidence.
- OpenAPI marks login as public and identifies response `userId` as the same internal owner ID used by JWT `sub`.

## Compatibility and operations

This is an intentional breaking authentication cutover. Previously issued tokens do not satisfy the new subject/issuer/audience/version contract and return 401 after deployment, so users must log in again. Operators must prepare both BCrypt hashes, the JWT secret/issuer/audience, and a separate login-scope HMAC key in the secret store before deployment. V7 is additive and forward-only; rollback leaves its column and rows intact. Credential rotation uses the dedicated privileged transaction and sanitized audit rather than rerunning V7.

The Dashboard must use `data.user.userId` and JWT `sub` as the owner ID, never the username. Return Engine payloads/schemas and other workspace implementations are unchanged. Principle APIs, schemas, and idempotency changes; S2.2/S2.3; provider calls; and orders are explicitly out of scope.
