# MARS DEMO Portainer stack

`mars-demo.stack.yml` and `mars-demo.env.example` are the DEMO product contract.
The image embeds a versioned historical scenario plus separately validated
report and 31-symbol signal fixtures. Page reads do not query a FULL service or a live price provider.
New visitor controls and journals use only the DEMO state volume; Agent questions
use the server-side Vertex route and its DEMO quota ledger.
Keep the environment file and `demo-secrets/` directory outside the repository.
The service account and session-signing key are host-mounted Docker secrets; do
not put either value in Portainer's plain environment editor or the image. The
service-account identity may be shared with FULL when explicitly authorized,
but its JSON file and mount path remain in the DEMO secret root.

Set `MARS_DEMO_IMAGE_DIGEST` from the verified `mars-demo-images.json` release
asset. `MARS_DEMO_VERSION` is a human-readable independent DEMO version; the
digest remains the runtime identity.

The container listens on host loopback port 3001 by default so a local reverse
proxy can terminate TLS. Cookies use `Secure` in production. Route that host
port through the public DEMO hostname only; preserve the public `Host` and
overwrite `X-Forwarded-Proto` with `https` at the trusted proxy. The DEMO network `mars-demo_web`
contains no FULL services; outbound access is needed only for Google OAuth and
Vertex. Apply host firewall policy so the public service cannot reach FULL API
or broker endpoints.

The only persistent volume is `mars-demo_state`, which holds hashed visitor
overlay keys and daily Agent reservation/usage counters in SQLite. A DEMO image
redeploy preserves today's counters. `MARS_DEMO_AGENT_*` settings are read at
startup. If an operator lowers a daily limit below today's use, remaining calls
are denied until the KST date changes. The screen displays the calculated
maximum text-token cost exposure; no USD hard cap is enforced.

The retired `MARS_DEMO_AI_DAILY_HARD_CAP_USD` setting is not read by this image.
Remove it from the old DEMO environment during the later cutover; do not copy a
FULL budget variable or FULL secret file into this stack.

## Secret files

Create `demo-secrets/` under the DEMO-only root with restrictive directory
permissions. Provide `session-signing-key` (at least 32 random bytes) and
`vertex-service-account.json` (a service account with only the Vertex
permissions required by the selected model). Keep them owned/readable by
the container's UID 1000; do not print their contents in shell output.

## Promote and roll back manually

Keep the previous DEMO digest, env revision, and stack file together as the
rollback record. Stop the currently active product stack before starting this
one; FULL and DEMO are not supported concurrently on the NAS. Promotion updates
only `mars-demo` image/env/secret/volume references. For rollback, stop the
candidate and restore the prior DEMO digest and DEMO env/secret revision. Do not
touch `mars-full_postgres-data`, `mars-full_brokerage-kek`, FULL tags, or FULL
secrets. Do not use `docker compose down -v`.

Candidate creation, CI verification, registry publish, manual NAS promotion,
and live-operation confirmation are separate states. The old Docker Hub DEMO
tags stay in place through candidate verification, later NAS cutover, consumer
reference updates, and the rollback window. Only then may a later authorized
cleanup remove old tags; keep `pjjpjj111/mars-demo` itself.
