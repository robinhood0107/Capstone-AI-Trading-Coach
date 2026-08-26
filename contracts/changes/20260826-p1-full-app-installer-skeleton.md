# P1 full-app installer skeleton

## Scope

Add one Linux/WSL and one Windows PowerShell entrypoint with the same operational verbs. The
existing Core-only `p1ctl` remains the degraded, provider-free regression lane. A new Compose
overlay implements the first real full-app dependency: Flyway migration, deterministic public RAG
Seed import, then identity bootstrap.

## Fail-closed status

The default full lane validates the committed Seed parts and blocks when the BGE-M3 model asset,
PaddleOCR-VL model asset, or Team B real artifact is absent. Missing Team A Dashboard packaging is
reported as the contract's non-blocking partial capability. No placeholder service or synthetic
artifact is treated as production. `--degraded` explicitly starts only the historical Core lane and
prints `CAPSTONE_RELEASE_AUTHORITY=NONE`.

Atomic G7 restore is not implemented by this change. The public `restore` verb therefore returns
`CAPSTONE_RESTORE=BLOCKED_G7_ATOMIC_RESTORE_NOT_IMPLEMENTED`; it does not alias the existing
isolated `restore-test` into a destructive production restore.

## Secret and provider boundary

Seed import receives only a purpose-specific Flyway DSN through a dedicated Docker secret file.
The Seed directory is read-only. This change performs no provider, account, balance, or order call,
and does not enable Vertex, Voyage query, KIS, ECOS, SearXNG, brokerage, or live-order settings.
