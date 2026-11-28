# FULL capacity and recovery rehearsal

Refs #234, #225. The issue stays open for published FULL and live-provider evidence.

## Environment and boundaries

The checkout and `origin/develop` both started at `d938e793607e2ef7d7840ff4cf80d19d12de50db` with a clean tree. This included #241, #244 and #246. The PR base advanced to `272e417111448743c5fad1019f4f8ad48c4111dd` through #247. That range adds two documents and a Python test. The Dockerfiles, runtime Compose, DB initialization, application sources and lockfiles match the execution revision.

The capacity run used the local FULL application topology from [compose.yml](../../deploy/p1/compose.yml), with the LOCAL surface and its test login. The pre-open harness exercised the FULL scheduler separately. Both ran in WSL2 with Docker Desktop, Compose v5.4.0, and generated test state. Initial Windows C: free space was about 24.3 GiB and WSL available RAM was 7.4 GiB. C: showed about 20 GiB free during recovery.

| Image | SHA-256 image ID | Source |
| --- | --- | --- |
| API and Python runtime | `6825bc4a5b051528c540a461c8c5b5771692ddf013fdcc5198b1276825bbbddb` | Built at execution revision |
| Dashboard | `3c2eeafba03b34ce1ad08121b3d2ff584f2a866baeccc7bedaf94a4b35a973aa` | Built at execution revision |
| PostgreSQL | `e81dedd9a40fc2de7a939708ca52103ca81eb09b0513db02478778b01e3e18d3` | Local cached image, revision label absent |
| Redis | `32107a372f94fd6710a56af8647df76a0161d301159d23f042ece26ad99a52d3` | Local cached image, revision label absent |

Source project `capstone234-full` used a fresh private state directory and `capstone234-full_p1-postgres-current`. Recovery project `capstone234-restore` used a second private state directory and `capstone234-restore_p1-postgres-current`. The rehearsal checked resolved paths, state ownership/mode, Docker project labels and the actual Postgres mounts. It reused images and separate named volumes. The lab had no mounted operations state. Both stacks and the legacy restore projects kept their volumes.

The source requested loopback ports 24480 and 24481. Once the app network was marked internal, those publishes were unreachable from WSL. A lab-only, fixed-upstream HTTP relay exposed the web/API on 24482/24483 and the recovery API on 24484. The application networks stayed internal. All model and provider calls and the order runtime were disabled in the app stack. The pre-open harness used an in-process HTTP fixture.

## Web read load

The driver sent authenticated GET requests through the Next.js proxy for portfolio risk, v2 positions, v3 automation status, recent v3 runs, latest risk results and latest performance report. It warmed each route once, then sent 40 requests per route at each concurrency. The p95 uses nearest rank and includes both 200 and 404 responses. The two unpublished-result endpoints consistently returned 404. No tier returned 429 or 5xx.

| Concurrent requests | Responses (200 / 404) | Overall p95 | Portfolio p95 | Mean Hikari acquire | Max Hikari acquire reported after tier |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 4 | 160 / 80 | 148.8 ms | 174.5 ms | 0.017 ms | 0.916 ms |
| 16 | 160 / 80 | 520.6 ms | 791.6 ms | 0.180 ms | 22.627 ms |
| 32 | 160 / 80 | 792.8 ms | 894.3 ms | 79.303 ms | 257.992 ms |

The main Hikari pool had ten connections. Its pending gauge was zero at tier boundaries and its timeout counter did not increase. PostgreSQL sampling every 100 ms saw up to ten `decision_app` connections, three active and one lock waiter across all DB sessions. This gives a peak count at sample times with no duration or lock attribution. The 32-request tier showed a material increase in pool acquisition time.

## Separate pre-open replay

The harness ran the production FULL scheduler with four worker threads and a fixed KST clock. A fixture supplied owner lists, 20 ms inference preparation and owner outcomes. Each owner attempt made a real read-only call to `p1_read_automation_recovery_cursor_v1` as the runtime role in the synthetic DB. The cursor returned no runnable schedule for these fixture owners. The first owner received one HTTP 429 per replay from MockTransport and returned a retry 60 simulated seconds later. The previous day's report remained blocked across the next opening. The scheduler reached 2026-08-18 after 2026-08-14 with the pinned XKRX calendar. The separate regression covers late preparation, retry, unresolved reconciliation and the resumed report path.

| Fixture owners per session | Completed outcomes across 2 sessions | Preparation p95 (n=2) | Owner first dispatch to completion p95 | Connect p95 | DB query p95 |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 8 | 16 | 20.157 ms | 344.149 ms | 16.835 ms | 13.132 ms |
| 24 | 48 | 20.131 ms | 59.254 ms | 33.187 ms | 24.880 ms |

The small 8-owner replay's p95 includes its one retry. The 24-owner replay's p95 excludes that tail. Connection times measure fresh runtime-role connections. Samples at 50 ms intervals saw at most three of these sessions and zero lock waiters. The 429 path was also exercised in the existing HTTP client test, which checks that a 429 produces no retry storm. The workload did not exercise eligibility, order submission, a successful brokerage resync, model compute or live upstream capacity.

## Backup and recovery

[backup](../../deploy/p1/backup) uses the caller's project for a Postgres logical dump and writes a private dump and SHA-256 sidecar under the chosen state. It does not back up secrets or keys. The shared state writer limits each input to 4 MiB. Against the 133,979,159-byte synthetic database, the command exited 1 with `P1_RELEASE_GUARD=ValueError`. A direct `pg_dump -Fc` exported 57,941,016 bytes in 8.362 s.

[restore-test](../../deploy/p1/restore-test) exports the selected dump and sidecar to a private staging directory, verifies the digest, starts a randomly named offline PostgreSQL project, and restores into a new database. Its success contract requires Flyway 87, specific Flyway object ownership, the `2|1` demo trust-root counts and offline authority. The latest FULL dump restored, then the CLI correctly exited 1 with `P1_ERROR=restore_version`. The restore database reported Flyway 231 in the separate rehearsal. Before the fix, a checksum mismatch left two staged files. After installing the exit trap immediately after `mktemp -d`, the same mismatch gave exit 1 and zero staged files. Original artifacts and volumes were preserved. The legacy assertions stayed intact.

For the FULL exercise, the harness wrote synthetic RAG and brokerage envelopes using the application crypto classes. It captured the DB separately from an archive of the two generated KEKs, checked SHA-256 on both, and used `pg_restore --exit-on-error --single-transaction` with an empty destination database. Roles for restored ownership were created on destination initialization. After promotion of the recovered DB name within the test project, the API booted with the restored RAG KEK. The owner got HTTP 200 and the expected decrypted question. A peer-owned fixture inserted after the restore returned HTTP 404 to the same session. The restored brokerage envelope opened with its matching key and owner. Both wrong keys and a different brokerage owner were rejected.

| Recovery observation | Result |
| --- | --- |
| Writes immediately before and after the snapshot | Before marker present, after marker absent |
| Observed RPO from latest recovered marker to simulated cut | 17.166 s |
| From simulated cut to API health | 136.784 s |
| From simulated cut to verified decrypt and HTTP isolation | 269.742 s |
| Successful API start-to-health step | 30.463 s |

The two cut-to-recovery intervals include local diagnosis and the failed first check. The source stayed up during the simulated cut. The first SQL history read lacked an exact actor capability and was denied. Verification then used the recovered API and normal authentication. The first recovery wait command included a one-shot seed service and reported its exit code 0 as a wait failure. The retry verified that exit code before starting the persistent API.

The restored V231 routine returned SQLSTATE `40001` with one unresolved synthetic order and `42501` with zero open orders and no claim. The test rolled back and sync-event counts stayed zero. Separate Temurin 25.0.3 Testcontainers tests verified stored fill reconciliation and concurrent deduplication, while the Python fixtures verified next-session halted-account recovery and retry after a transient failure. A successful account resync against a restored, certified broker account remains unverified.

## Reproduction and checks

Use an unused project, private state and loopback ports. Check `docker compose config` for project-scoped volumes, internal data networks and resolved synthetic mounts before any write. For this rehearsal, the Compose file-secret bind failed on the default Docker Desktop route. The WSL Unix socket resolved a direct bind probe. The local lab rendered file secrets into equivalent read-only mounts with `create_host_path: false`, preserved the original target paths under `/run/secrets`, and reran only the lab projects. Do not copy these temporary artifacts into a deployed stack.

1. Build the runtime and dashboard from the execution revision. Generate disposable state with `P1_PROJECT_NAME=capstone234-full P1_STATE_DIR=<private-state> deploy/p1/p1ctl init`. Bring up local FULL Postgres/Redis, migrations/identity, authority/API and the dashboard in that order with provider flags off. Confirm health through the isolated relay before running the six-route GET mix above.
2. In a private directory, run `P1_PROJECT_NAME=capstone234-full P1_STATE_DIR=<private-state> deploy/p1/backup`. Export the dump separately with `docker exec capstone234-full-postgres-1 pg_dump -U postgres -d capstone_p1 -Fc > full.dump`. Place a test copy and its sidecar in the source state's `backups` directory to reproduce the legacy failure with `P1_BACKUP_FILE=<dump-name> deploy/p1/restore-test` under the same state and project environment.
3. Verify the dump and key archive, start fresh Postgres under `capstone234-restore` and check its actual volume label. Create an empty DB with `createdb -T template0`, then restore with `pg_restore --exit-on-error --single-transaction`. Compare Flyway, markers and ciphertext before opening with the test keys. Confirm positive and negative owner reads on the recovered API.
4. Run `bash -n deploy/p1/backup` and `bash -n deploy/p1/restore-test` and `python3 -m unittest discover -s contracts/tests -p test_p1_release_workflow.py`. The 23 contract checks passed. The Python workspace's focused `uv run --frozen pytest` passed 117 tests, its separate pre-open/429/reconciliation selection passed seven, and `uv run --frozen ruff check .` passed.
5. On Node v22.22.1, dashboard typecheck, lint and 21 unit tests passed. The live Compose Playwright test initially lacked the preview backtest fixture. After applying the existing provider-free `dashboard-preview-seed`, the retry passed. Three overview read-recovery Playwright tests passed. JVM crypto and lock migration tests passed on Temurin 25.0.4.1. The two brokerage integration tests passed on Temurin 25.0.3.

The private machine receipt retains `base-compare.log`, `web-load.log`, `web-db-samples.log`, `preopen-load.log`, `preopen-db-samples.log`, `restore-lab.log`, `legacy-restore-direct.log`, `recovered-validation.log`, `restored-reconciliation.log`, and `summary.log`. The bench drivers and the artifacts they read remain in the same private evidence directory. Deployment, a signed offline release lifecycle, the published FULL OIDC profile, live KIS limits and live account resync are outside this evidence.

`CODEX_SECURITY_DEEP_SCAN=NOT_RUN_USER_SCOPED_OUT`
