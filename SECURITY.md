# Security Policy

## Supported versions

Security fixes target the current `main` branch and the latest public release. Older versions may remain available
for reproducibility, but backports are not guaranteed.

## Reporting a vulnerability

Use GitHub private vulnerability reporting for confidential reports. Do not open a public issue containing an
exploit, secret, raw provider response, account identifier, credential, token, order payload, or personal data.
When private reporting is unavailable, open a content-free issue asking a maintainer to establish a private channel.

Include the affected commit or release, reachable entry point, expected security boundary, minimal reproduction,
and impact. Redact logs and attach only the smallest sanitized evidence needed to reproduce the problem.

## Security review scope

Security review includes the full tracked source tree and, in particular, these trust boundaries:

- PostgreSQL roles, `SECURITY DEFINER` functions, actor capabilities, grants, and one-shot claims
- Kafka principals, topic ACLs, signed envelopes, poison receipts, offset handling, and async workers
- provider approval packets, account/credential binding, replay prevention, quotas, and terminal receipts
- authentication, MCP sessions, bounded HTTP readers, document/PDF parsers, and resource budgets
- build inputs, dependency and action pinning, state filesystem handling, release archives, SBOMs, provenance,
  anti-rollback state, and offline bundles

A security scan is complete only when all tracked in-scope sources are covered and every validated finding is fixed
and retested. Findings are not closed by reducing scan scope, suppressing the report, or treating them as an
accepted backlog.

## Secret and data handling

Never place secrets, raw provider data, account information, access tokens, private keys, or unredacted order data
in GitHub issues, pull requests, CI logs, test fixtures, or diagnostic bundles. Rotate any exposed credential and
report the exposure privately even if the value was later deleted.
