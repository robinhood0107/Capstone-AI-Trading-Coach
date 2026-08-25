# P1 security and offline container release

## Status

`IMPLEMENTED_PENDING_RELEASE_VERIFICATION`

## Scope

This change closes the P1 offline-demo security findings without changing the public HTTP payloads.

- V86 replaces direct Decision graph and Kill Switch writes with capability-bound atomic database functions.
- Authentication verifier reads move from `decision_app` to the dedicated `decision_auth` role and datasource.
- Expired RUNNING jobs can be reclaimed with a new fencing token; stale tokens cannot complete or fail work.
- Poison-event duplicate/conflict classification precedes quota consumption.
- P1 approval packet v2 signs a closed provider target and uses a root-owned fixed trust policy with authoritative one-use claims.
- P1 report v1 remains historical byte-stable evidence; current v2 readers require packet binding and enforce the generated closed schema before semantic and hash validation.
- B86 is a generated clean-install baseline; V1 through V86 remain immutable upgrade history.
- `P1_OFFLINE_DEMO` adds digest-bound `linux/amd64` DB and Kafka distributions using the same application images.

## Compatibility

The following public contracts remain byte-stable:

- Decision `orderIntent` exact eight fields
- Signal v1/v2 payloads
- RAG ask/history payloads
- four Dashboard ViewModel paths and payloads

No cross-market endpoint, scheduler, materializer, overlay, or order authority is added. LightGBM remains
`ABSTAIN/MISSING_EVIDENCE`. Provider, live account, and live order calls are outside this change.

## Migration behavior

- Existing database: V1 through V85 remain in history and V86 is applied.
- Fresh database: B86 installs the final schema and lower versioned migrations are ignored.
- Both paths run the idempotent `role-bootstrap` before Flyway so `decision_auth` is not fresh-init-script-only.
- Credential/verifier and runtime rows are not embedded in B86; a one-shot identity bootstrap installs demo identity.
- Historical migrations and completed contract evidence are not rewritten.

## Required verification

- generated contract and OpenAPI drift gates
- V1 to V86, V85 to V86, and B86 fresh integration tests
- historical/baseline schema, ACL, RLS, function, trigger, and static-seed parity
- Decision/Kill Switch/auth/async/poison/report negative security tests
- Python and Spring full suites
- DB and Kafka container lifecycle using identical Spring/Python image digests
- Critical/High image vulnerability gate, secret scan, SBOM, provenance, signature, and offline import verification
- external signed archive checksum verification before extraction and exact AGPL-3.0-only LICENSE inclusion

Until all source and image gates are bound to the exact merge SHA, the release state remains
`IMPLEMENTED_PENDING_RELEASE_VERIFICATION`. Missing real Return Engine artifacts and Team A integration remain
external blockers and must not be represented by synthetic fixtures.
