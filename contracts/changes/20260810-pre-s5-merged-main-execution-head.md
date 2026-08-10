# Pre-S5 merged-main execution HEAD approval

## KR

### 변경 이유

Pre-S5 완결 순서는 provider 실행 전에 모든 tracked 구현을 병합하고 `origin/main`의
post-merge CI를 확인한 SHA를 `EXECUTION_HEAD`로 동결한다. 기존 KIS_MOCK v2 author는 열린
PR HEAD만 허용하므로, 이 순서에서는 packet을 안전하게 발급할 수 없었다.

### 계약

- `schemaVersion=1`과 기존 v2 packet bytes는 변경하지 않는다. v2에서 생략 가능한
  `repository.evidenceMode`의 기본값은 `OPEN_PR`이며 기존 동작을 그대로 유지한다.
- `evidenceMode=MERGED_MAIN`은 `branchRef=main`, local `HEAD=origin/main`, 지정 PR의
  `state=MERGED`, `baseRef=main`, `mergeCommit.oid=HEAD`가 모두 일치할 때만 허용한다.
- author와 executor는 GitHub check-runs API에서 exact merge SHA의 required job 다섯 개가
  모두 성공했는지 각각 확인한다. 같은 이름의 PR-head check나 다른 SHA의 check는 인정하지 않는다.
- security report/manifest/coverage/findings, Redis baseline, TTL, nonce, exact order, physical cap,
  retry 0, artifact 0, current-user approval latch와 첫 실패 stop rule은 기존 v2 계약을 유지한다.
- `EXECUTION_HEAD` 뒤 상태 문서만 추가한 `RELEASE_HEAD`는 provider evidence의 SHA를 대신할 수 없다.

### 비범위

이 변경은 KIS live 주문, KIS_MOCK 물리 실행, 다른 provider 실행, packet 자동 승인 또는 public API
변경을 허용하지 않는다. 실제 실행은 merged-main packet SHA가 포함된 content-free batch manifest에
대한 현재 사용자의 exact 승인 뒤에만 가능하다.

## EN

### Why

The Pre-S5 landing order freezes `EXECUTION_HEAD` only after all tracked implementation is merged and
post-merge CI is green on `origin/main`. The existing KIS_MOCK v2 author accepted only an open PR head,
so it could not safely issue a packet in that sequence.

### Contract

- Preserve schema v1 and existing v2 packet bytes. The optional v2
  `repository.evidenceMode` defaults to `OPEN_PR`, retaining the existing behavior.
- `MERGED_MAIN` requires `branchRef=main`, local `HEAD=origin/main`, and a selected merged PR whose
  main-base merge commit is the exact HEAD.
- Both author and executor independently require all five required GitHub check-runs to succeed on the
  exact merge SHA. A same-named PR-head check or check from another SHA does not count.
- Existing sealed security evidence, Redis baseline, TTL, nonce, exact order, physical caps, zero retry,
  zero artifacts, current-user latch, and first-failure stop rules remain unchanged.
- A later documentation-only `RELEASE_HEAD` cannot replace the provider execution SHA.

### Out of scope

This change does not authorize a KIS live order, a physical KIS_MOCK run, another provider call,
automatic approval, or a public API change. Physical execution still requires the current user's exact
approval of the content-free batch manifest containing the merged-main packet digest.
