# MARS FULL password accounts and explicit provider linking

## Decision

FULL supports the existing `demo-user` username/password login, minimal email/password signup, and Google/Kakao authentication. DEMO remains anonymous and password-free. This change supersedes the FULL password-disabled runtime policy in `20260924-mars-public-password-runtime.md`; the old fixed credential bundle boundary and generated demo row remain unchanged.

The private stack started by `./capstone up` uses the same signup, account settings, and provider linking. Its login keeps the `username` field for `demo-user`/`demo-admin` and accepts a signed-up email in that field. Google/Kakao are optional there: the `social-login` profile starts only when the root `.env` has both providers and an `https://` `MARS_PUBLIC_ORIGIN`; otherwise `GET /api/v1/auth/options` returns no providers and provider link start returns 404.

## Identity and data ownership

- `demo-user` authenticates as the existing `usr_demo_user`. No account or owned row is automatically transferred.
- Password signup creates a separate USER and stores only a normalized email, BCrypt hash, owner ID, and timestamps in `password_login_identities`.
- First Google/Kakao login without an existing provider identity creates a separate USER.
- An authenticated user can attach one Google and one Kakao issuer+subject from Settings. Same-email values never link accounts. An identity already attached to another user is rejected.
- A provider cannot be removed when it would leave the account without a password or another linked provider.
- A USER-level provider identity cannot be linked to an ADMIN account, so linking never re-evaluates `demo-admin` down to USER.

## Security rules

1. Passwords are 15–64 characters and no more than 72 UTF-8 bytes; new hashes use BCrypt strength 12.
2. Passwords, provider subjects, and OAuth tokens are not returned or written to logs, audit payloads, or URLs. The audit records action and provider only.
3. Unknown and incorrect password logins return the same 401. Login and signup use the existing HMAC-scoped attempt limiter.
4. Password credentials have forced RLS, no direct runtime role grants, and SECURITY DEFINER access functions restricted to `decision_auth`.
5. OAuth verifies provider state/nonce and issuer+subject. Linking additionally requires a current Bearer-authenticated owner, exact same-origin Origin, and a short-lived HttpOnly/Secure/SameSite session intent.
6. Every authenticated request revalidates the actor's active status, role, and security version from the database. Signup is always USER; ADMIN is controlled by the exact Google subject hash. Kakao does not grant ADMIN.
7. Provider identity rows are unique by issuer+subject and by user+issuer. Email is not stored from OAuth and is not an account merge key.
8. The browser keeps JWTs in memory. OAuth callbacks exchange one-use server-side session state and never place the Bearer token in a URL.

Email verification, password change/reset mail, and provider identity transfer between two already-created accounts are not included. The operator must have the existing `demo-user` password before linking a provider to its data.

## Affected contracts

- FULL auth OpenAPI: `contracts/openapi/mars-full-auth.v1.openapi.json`
- Forward migration: `V212__password_accounts_and_provider_linking.sql`
- API and user flow: `docs/API_명세서.md`
