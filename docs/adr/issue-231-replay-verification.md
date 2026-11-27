# Issue 231 controlled replay verification

Refs #225 and #231. Started from a clean `origin/develop` at `d938e793607e2ef7d7840ff4cf80d19d12de50db` with PRs #241, #244 and #246 merged. The team submission was read at PR #7 merge `2dbc66503d4f85ac40736445676f4907ea78c8f8`. The Git blobs for `scenario_materializer.py` and V154 match the personal baseline. Team submission test results belong to that checkout. [Team submission PR](https://github.com/pnucse-capstone2026/capstone-2026-team-33/pull/7)

## Observed synthetic paired results

`p1-issue-231-synthetic-replay.v1` ran on 13 pinned XKRX dates, with 72 eligible opportunities across 12 entry dates and six fictitious assets. The last date permits exit only. All arms reuse the existing Strict replay engine and the exact quotes, costs and exit schedule in the [frozen protocol](issue-231-replay-protocol.md). Rows in the two delta columns show added / removed IDs versus the reference. Each filled trade emits one simulated BUY and one simulated SELL leg.

| Candidate input | Order cap (KRW) | Candidates | Missed blocks / violations | Excess blocks / normal | Candidate Δ + / − | Legs | Leg Δ + / − | Cost (KRW) | Net P&L (KRW) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Rule BUY (reference) | 300,000 | 60 | 0 / 24 | 0 / 36 | 0 / 0 | 72 | 0 / 0 | 32,340.00 | −32,340.00 |
| Rule BUY | 360,000 | 60 | 12 / 24 | 0 / 36 | 0 / 0 | 96 | 24 / 0 | 46,002.60 | −45,402.60 |
| Rule BUY | 240,000 | 60 | 0 / 24 | 24 / 36 | 0 / 0 | 24 | 0 / 48 | 8,417.64 | −7,577.64 |
| LSTM BUY stand-in | 300,000 | 36 | 0 / 12 | 0 / 24 | 12 / 36 | 48 | 24 / 48 | 17,662.68 | −16,582.68 |
| Rule BUY with news filter | 300,000 | 36 | 0 / 12 | 0 / 24 | 0 / 24 | 48 | 0 / 24 | 23,922.36 | −24,762.36 |
| Rule BUY with LSTM SELL veto | 300,000 | 36 | 0 / 12 | 0 / 24 | 0 / 24 | 48 | 0 / 24 | 21,007.56 | −20,647.56 |

In the fixed-candidate comparison, the looser cap permits `SYN04` on 12 dates. The tighter cap drops `SYN02` and `SYN03` on 12 dates each. The reference permits `SYN03` at exactly KRW 300,000 on 2026-08-18. In the fixed-policy comparison, LSTM adds `SYN06` and drops `SYN02`, `SYN03` and `SYN05` before the gate. The news and veto arms each remove 24 candidate rows. News removes `SYN01` orders, while veto removes `SYN02` orders. Both also remove `SYN05` from the incoming violations, which explains the smaller policy denominator. All six arms reconcile planned candidates to closed trades plus blocks.

These six recurring cases cover the order-cap boundary and selected candidate-filter outcomes. The fixture deliberately keeps the other risk gates non-binding. Repeated sessions contain the same labels and signals per symbol. The resulting error fractions describe the fixture and are unsuitable as population estimates. The replay uses a common timed exit and the owner projection's replay engine. A separate live RiskEngine test with effective-dated policies, independent labels and a held-out history would be needed for broader claims.

## Historical records

| Evidence | Window and outcome | Reproduction status in #231 |
| --- | --- | --- |
| Original owner projection | 2026-08-18 through 2026-09-03, 13 XKRX sessions, exact-31. Baseline / Guide / Strict recorded −0.5476% / +0.0271% / −0.2274% after costs, and 14 / 8 / 6 trades. The recorded Strict count was two blocks. | Read the published result and the V127 / V154 bounds. Full matched snapshots and independent labels for this period were unavailable in the reviewed offline material. |
| Long-run v1 | 22 walk-forward folds for 2005–2026, 130,752 predictions, `BELOW_BASELINE`. Data selection used effective eligibility within a fixed modern universe and retains survivorship bias. | Read the historical report and protocol. |
| Long-run v3 | Protocol registered 2026-09-09 KST. Of 17 total trials, the v3 result lists zero accepted candidates and `KEEP_CURRENT_50_50`. | Read the archived protocol and result. No training run in #231. |

[Original owner projection](../금융공학_공식_및_자동매매_로직_설명서.md#102-세-가지-시나리오-실측) · [Walk-forward v1](../../workspaces/decision-platform/research/p1-return-profit-verification/reports/profit-verification.md) · [Long-run v3 result](../../workspaces/decision-platform/research/p1-return-profit-verification/reports/lstm-multihorizon.v3.json)

## Local verification

Ubuntu on WSL2, CPython 3.12.13, exchange-calendars 4.13.2. Local test execution was serial with numerical-library threads set to one. The isolated network namespace and frozen offline dependencies were used for the replay and regression. The 1 GiB limit is a process virtual-address-space limit. The first replay completed in 6.51 s with a peak RSS of 227,832 KiB. No long-window training or repeated large-data evaluation ran.

Run from `workspaces/decision-platform/python-services` with frozen dependencies cached.

```bash
unshare -Urn bash -c '
  ulimit -v 1048576
  export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONHASHSEED=0
  uv run --offline --frozen python -m tests.p1_owner.test_issue_231_strategy_replay > "$1/replay.json" &&
  uv run --offline --frozen python -m tests.p1_owner.test_issue_231_strategy_replay > "$1/replay-repeat.json" &&
  cmp "$1/replay.json" "$1/replay-repeat.json" &&
  sha256sum "$1/replay.json" &&
  uv run --offline --frozen pytest -q tests/p1_owner/test_issue_231_strategy_replay.py \
    tests/test_scenario_materializer.py tests/data/calendar/test_xkrx.py \
    tests/p1_owner/test_daily_inference.py tests/p1_owner/test_after_hours_replay.py
' bash "$(mktemp -d)"
uv run --offline --frozen ruff check .
uv run --offline --frozen ruff format --check tests/p1_owner/test_issue_231_strategy_replay.py
uv run --offline --frozen mypy app
uv lock --check --offline
```

- The base regression for the materializer and XKRX adapter passed 15 tests before edits. The new test's first run failed when the canonical JSON serializer rejected tuples. The input digest now hashes JSON-native case dictionaries, and the report determinism check compares serialized output. The final focused run passed 38 tests, including the calendar, materializer, daily inference and after-hours replay tests.
- The two replay runs matched byte for byte. Fixture input digest `6a67980662174c03feb6d0de9a11a2e9858bd7811e87d3e510a04e49e03e09f8`. JSON output digest `30e88b92b620b299f38898bfb80851d93ac52b4ff8e2fa630fa8d4d7e82db631`.
- Ruff check and formatting passed. Mypy passed on 409 app files. The frozen lock check passed. The product-impact classifier and release-failure isolation tests passed. Git ignore rules and both relevant TOML files were inspected locally.
- The generic gstack test discovery reported `ENV` for four Python selections because it looked for bare `pytest` on PATH. The four selected DEMO boundary tests passed. The Decision Platform results above come from explicit workspace `uv` invocations.
- Local Compose, Dashboard typecheck/lint/Playwright, JVM Gradle, broker and model integration were outside this offline experiment. Actual order submissions, live data requests and paid model calls in the experiment were zero. `CODEX_SECURITY_DEEP_SCAN=NOT_RUN_USER_SCOPED_OUT`.
