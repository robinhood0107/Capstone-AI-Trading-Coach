# S3-online KIS_MOCK approval packet v2

## KR

### 변경 이유

기존 `schemaVersion=1` packet은 PR #55 historical verification을 위해 `pullRequest=55`를
하드 고정한다. PR #77 또는 후속 PR의 exact KIS_MOCK one-shot probe에 그 packet을 재사용하면
현재 branch/remote/CI/security 증거를 올바르게 결속할 수 없다.

### 계약

- v1은 수정하지 않는다. `schemaVersion=1`과 PR #55 제한은 historical verification 전용으로
  유지한다.
- 새 실행은 `schemaVersion=2`만 사용한다. v2는 dynamic `pullRequest>=1`, `baseRef=main`,
  current `branchRef`, local/remote/CI/security의 동일 `headSha`, 64-hex nonce, TTL ≤ 60분,
  Redis PTTL baseline, exact KRX `BUY LIMIT` 1주 order, retry/artifact/pre-approval call `0`을
  결속한다.
- v2 security evidence는 report뿐 아니라 sealed scan `scan-manifest.json`, `coverage.json`,
  `findings.json` 각각의 digest를 포함한다. executor는 manifest target revision=final HEAD,
  coverage=`complete`, findings=`[]`, manifest artifact digest의 일치를 provider handoff 전에
  재검증한다.
- `kis-mock-brokerage-approval-author`는 current clean worktree, `origin/<branch>`, GitHub PR
  number/base/head/required checks, Redis limiter PTTL을 직접 읽는다. author와 executor 모두 PR이
  여전히 `OPEN`, non-draft, same head/base이며 required checks가 모두 `SUCCESS`인지 확인한다.
  operator가 SHA나 check conclusion을 입력할 수 없다. output은 기존 owner-only mode `0700` directory의 absolute path에
  `dirfd + O_NOFOLLOW + O_EXCL`, regular owner-only mode `0600`, fsync로 새 파일만 만든다.
  stdout은 approval ID와 packet SHA-256만 허용한다.
- `FULL`은 정확히 `balance → buyable → submitLimitBuy → cancelFull → executionRead`,
  `tokenP=1`, `brokerage=5`다. `BALANCE_DIAGNOSTIC`은 `balance` 1회 cap `1`만 허용한다.
- `CANCEL_RECOVERY`는 source approval ID/SHA/nonce, order identity, non-secret reference anchor와
  executor가 Fernet-encrypted Redis outcome receipt에 봉인한 **actual failedStep**이 모두 일치할 때만
  허용한다. 하나의 source failure는 Redis NX로 하나의 recovery packet에만 claim된다. source `cancelFull` 실패는
  `cancelFull → executionRead`, cap `2`이고 source `executionRead` 실패는 cancel 재전송 없이
  `executionRead`, cap `1`이다. 이 profile은 `submitLimitBuy`나 다른 신규 주문 surface를
  표현할 수 없다. missing, PENDING, historical unanchored, foreign reference는 provider call 0으로
  fail-closed한다.
- TTL은 packet parse 시점뿐 아니라 각 operation, token issue와 shared-limiter 대기 전후, socket
  handoff 직전에도 재확인한다. 만료되면 해당 provider reservation 전에 종료한다.

### 비범위

KIS_LIVE 주문·정정·취소, generic provider activation, gRPC 상시 enablement, background polling,
S3.3 fill append는 이번 변경으로 승인되지 않는다. actual KIS_MOCK provider call은 final HEAD,
green CI, complete zero-finding scan과 current-user exact approval 후에만 별도 실행한다.

### 검증

```bash
cd workspaces/decision-platform/python-services
uv run --frozen pytest -q \
  tests/brokerage/test_kis_mock_approval_v2.py \
  tests/brokerage/test_kis_mock_approval_probe.py \
  tests/brokerage/test_kis_mock_order_gateway.py
uv run --frozen ruff check app/brokerage tests/brokerage
uv run --frozen mypy app
```

## EN

### Why

The existing `schemaVersion=1` packet hard-codes `pullRequest=55` for historical
verification. It cannot safely bind a one-shot KIS_MOCK probe for PR #77 or a later
PR to its current branch, remote SHA, CI, and security evidence.

### Contract

- Preserve v1 unchanged: `schemaVersion=1` remains fixed to PR #55 for historical
  verification only.
- New executions use `schemaVersion=2` only. v2 binds a dynamic PR, `main` base,
  current branch, one local/remote/CI/security HEAD, 64-hex nonce, TTL no greater
  than 60 minutes, Redis PTTL baseline, exact one-share KRX BUY LIMIT order, and
  zero retries, artifacts, and pre-approval provider calls.
- Security evidence includes digests for the report plus sealed
  `scan-manifest.json`, `coverage.json`, and `findings.json`. The executor verifies
  final target revision, complete coverage, zero findings, and manifest artifact
  digests before any provider handoff.
- The author reads the clean worktree, `origin/<branch>`, GitHub PR/base/head/checks,
  and Redis PTTLs directly. Both author and executor require the PR to remain `OPEN`,
  non-draft, on the same head/base, with all required checks `SUCCESS`. It only creates a new owner-private `0600` regular file
  below an existing `0700` directory using dirfd, `O_NOFOLLOW`, `O_EXCL`, and fsync;
  stdout contains only the approval ID and packet SHA-256.
- `CANCEL_RECOVERY` can only reuse the encrypted `COMMITTED` reference after an
  encrypted executor outcome receipt proves the exact source packet, order identity,
  reference anchor, and actual failed step. A source failure can be claimed by one
  recovery packet only; it cannot express a new submit. A failed cancel permits
  cancel plus execution-read; a failed execution-read permits one execution-read
  without repeating a completed cancel.
- TTL is checked before every operation and before/after limiter waits and socket
  handoff, so expiry stops the next physical reservation.

### Out of scope

This change does not authorize KIS_LIVE orders, provider activation, permanent gRPC
enablement, background polling, or fill append. A real KIS_MOCK call still requires
the final exact HEAD, green CI, complete zero-finding security scan, and a new
current-user exact approval.
