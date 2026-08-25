# P1 public RAG Seed v1

## Status

`IMPLEMENTED_MERGE_CANDIDATE`

## Scope

`deploy/p1/seed/public-rag/` contains a data-only, deterministic gzip JSONL Seed built from the V73
active public pointer. It contains only the two active public component generations and their reachable
142 source revisions, 7,871 chunks, memberships and Voyage 1024 embeddings, OA112 cards, two component
manifests, two content-free evaluations, and the final public pointer.

The Seed excludes users, account state, conversations, owner documents, provider usage ledgers, credentials,
raw provider responses and original PDFs. The archive is split into ordinary Git files no larger than 32MiB;
the manifest binds each part and the reassembled archive with SHA-256.

## Restore boundary

The importer accepts a fresh V87 target only. It verifies every part before opening a database transaction,
locks the install path, stages rows with the public pointer still `NOT_MATERIALIZED`, validates exact counts,
profile, dimension, public ownership, active component state and evaluation status, then updates the pointer
last. The same active Seed is a no-op and any other non-empty immutable graph fails closed.

The migration owner temporarily suspends `FORCE ROW LEVEL SECURITY` only for the nine Seed tables inside the
single install transaction. It restores `FORCE` before commit; any error or process termination rolls the
temporary DDL and all staged rows back together. This capability is for the pre-runtime migration/Seed job and
is not exposed to Spring, Python query workers or providers.

## Non-authority

This contract and its focused tests do not prove the full Compose installer, owner upload/OCR, BGE model
installation, provider live receipt, Team B artifacts, security closure, image publication, tag or release.
