# KR: S2.1 actor trust-root 선행 계약

## 변경 이유

S2.1 Principle owner 검증은 JWT actor ID를 DB foreign key와 같은 namespace로 신뢰해야 한다. 기존 demo 인증은 username subject, 별도 userId claim, 평문 credential property, memory account를 사용해 DB `users.user_id`와 하나의 trust root를 만들지 못했다. Principle 계약·런타임 변경 전에 이 인증 선행 조건을 독립 PR로 고정한다.

## 변경 범위

- DB `users`를 identity/hash/role/status/`security_version`과 attested credential evidence의 단일 진실 소스로 사용한다.
- V7 parameterized Java migration이 exact `usr_demo_user`/`usr_demo_admin` row를 fail-closed로 seed하며 BCrypt hash·reuse tag·bundle MAC을 SQL text에 넣지 않는다.
- JWT를 HS256으로 고정하고 exact issuer/single audience, internal-user-ID subject, `iat`/`exp`/role/`securityVersion`을 검증한다.
- 매 authenticated request에서 DB status/role/version을 재검증해 즉시 회수를 보장한다.
- login password는 선택 row와 peer row에서 각각 BCrypt 검증해 하나의 평문이 두 역할에 모두 일치하면 fail-closed하고, unknown username도 두 번의 dummy 검증을 거친다. limiter scope는 JWT와 다른 key의 purpose/version HMAC으로 만든다.
- purpose-separated HMAC reuse tag와 role/version/hash를 결속한 bundle MAC을 공통 bootstrap/rotation verifier로 검증한다. BCrypt 문자열 불일치는 평문 분리 증거로 취급하지 않는다.
- operator-only credential rotation과 pre/post deployment cutover smoke task를 추가한다. rotation은 loopback DB에서 두 demo row를 잠그고 저장 bundle을 검증하며 현재/peer reuse tag를 거부한 뒤 전체 credential evidence에 CAS·bounded timeout을 적용한다. raw password/token/hash/tag/MAC/key는 추적 파일, argv, 로그, audit, evidence에 넣지 않는다.
- `flyway` role은 PostgreSQL 일반/오류 parameter logging 길이를 모두 `0`으로 고정한다. rotation은 credential bind 전에 두 effective setting을 검증하고 안전하지 않으면 mutation과 audit 없이 fail-closed한다. SQL 문장 로깅 자체는 허용한다.
- login OpenAPI를 public operation으로 표시하고 response `userId`가 JWT `sub`와 같은 internal owner ID임을 문서화한다.

## 호환성·운영 영향

이 변경은 인증 의미의 intentional breaking cutover다. 기존 token은 새 `sub`/issuer/audience/version 계약을 만족하지 않아 배포 후 401이며, 사용자는 다시 로그인해야 한다. public login은 client가 stale Bearer를 자동 첨부해도 credential 재인증을 허용한다. 배포 전 secret store에 두 role-bound credential bundle, 별도 32-byte credential-separation key, JWT secret/issuer/audience, 별도 login-scope HMAC key를 원자적으로 준비한다. V7은 additive/forward-only이며 rollback 시 column/row를 삭제하지 않는다. credential rotation은 V7 재실행이 아니라 별도 privileged transaction과 sanitized audit를 사용하고, 성공한 bundle을 해당 persistent bootstrap secret에도 승격해야 clean recovery가 같은 credential evidence를 재현한다. bare BCrypt hash만 받는 호환 경로는 두지 않는다.

Dashboard는 `data.user.userId`/JWT `sub`를 owner ID로 사용하고 username을 owner key로 쓰지 않는다. Return Engine payload/schema와 다른 workspace 구현에는 변경이 없다. Principle API/schema/idempotency, S2.2/S2.3, provider 호출, 주문은 범위 밖이다.

# EN: S2.1 actor trust-root prerequisite contract

## Reason

S2.1 Principle ownership must trust a JWT actor ID in the same namespace as the database foreign key. The previous demo authentication used a username subject, a separate userId claim, plaintext credential properties, and memory-only accounts. This prerequisite isolates and closes that trust-root gap before any Principle contract or runtime change.

## Scope

- Database `users` is the sole source of truth for identity, password hash, role, status, `security_version`, and attested credential evidence.
- A parameterized V7 Java migration fail-closes while seeding the exact `usr_demo_user` and `usr_demo_admin` rows without placing BCrypt hashes, reuse tags, or bundle MACs in SQL text.
- JWT validation pins HS256 and requires the exact issuer, exact single audience, internal-user-ID subject, `iat`, `exp`, role, and `securityVersion`.
- Every authenticated request revalidates current database status, role, and version for immediate revocation.
- Login performs one BCrypt verification for the selected row and one for its peer, failing closed when one plaintext matches both roles. Unknown usernames still traverse two dummy verifications. Login limiter scopes use a purpose/version HMAC key distinct from the JWT key.
- A shared bootstrap/rotation verifier checks a purpose-separated HMAC reuse tag and a bundle MAC binding role, version, hash, and identity. Serialized BCrypt inequality is not treated as plaintext-separation evidence.
- Operator-only credential rotation and pre/post deployment cutover smoke tasks keep raw passwords, tokens, hashes, tags, MACs, and keys out of tracked files, argv, logs, audit payloads, and evidence. Rotation locks both loopback-hosted demo rows, verifies stored bundles, rejects current/peer reuse tags, and applies CAS with bounded timeouts to all credential evidence.
- The `flyway` role pins both normal and error PostgreSQL parameter-log lengths to `0`. Rotation verifies both effective settings before any credential bind and fails closed without mutation or audit when either is unsafe; SQL statement logging itself remains permitted.
- OpenAPI marks login as public and identifies response `userId` as the same internal owner ID used by JWT `sub`.

## Compatibility and operations

This is an intentional breaking authentication cutover. Previously issued tokens do not satisfy the new subject/issuer/audience/version contract and return 401 after deployment, so users must log in again. Public login still accepts credentials when a client automatically attaches that stale Bearer token. Operators must atomically prepare both role-bound credential bundles, a dedicated 32-byte credential-separation key, the JWT secret/issuer/audience, and a separate login-scope HMAC key in the secret store before deployment. V7 is additive and forward-only; rollback leaves its columns and rows intact. Credential rotation uses the dedicated privileged transaction and sanitized audit rather than rerunning V7, and the successfully rotated bundle must be promoted to the matching persistent bootstrap secret for reproducible clean recovery. No bare-BCrypt compatibility path remains.

The Dashboard must use `data.user.userId` and JWT `sub` as the owner ID, never the username. Return Engine payloads/schemas and other workspace implementations are unchanged. Principle APIs, schemas, and idempotency changes; S2.2/S2.3; provider calls; and orders are explicitly out of scope.
