# S1.3G GDELT aggregate offline producer

상태: `IMPLEMENTED_OFFLINE`
관련 Issue: #76
선행 계약: `20260731-s1-3g-naver-retirement-gdelt-aggregate-lock.md`

## KR

Decision Platform에 기사 metadata가 없는 GDELT aggregate fixture producer를 구현했다.
합성 `TimelineTone`과 `TimelineVolRaw` shape만 strict parser에 입력하고, timestamp set·window·
finite tone·nonnegative count/norm·중복·512-point·4 MiB 상한을 검증한다. 두 mode가 완전할 때만
`AVAILABLE`을 만들며 empty·partial·malformed·norm zero는 numeric zero 없이 `ABSTAIN`한다.

artifact는 canonical hash를 검증한 뒤 bounded root 아래 0600 append-only 파일로만 게시한다.
temp fsync와 no-replace hard link, directory `O_NOFOLLOW` traversal을 사용하며 raw response,
header, request URL/query, 기사 제목·본문·URL·domain·article ID는 저장하지 않는다.

CLI 기본 모드는 synthetic offline fixture이고 physical provider call은 0이다. future online
입력은 exact packet hash, HEAD, fixed origin/path, 두 mode, query definition hash, window,
physical cap 1, retry 0, raw persistence false, attribution, purpose, expiry를 모두 검증하지만
실제 HTTP transport는 활성화하지 않았다. 별도 fresh 승인이 있어도 후속 구현 전에는
`PROVIDER_DISABLED`다.

## EN

Decision Platform now has a GDELT aggregate fixture producer that stores no article metadata.
Only synthetic TimelineTone- and TimelineVolRaw-shaped inputs reach the strict parser. The
producer validates timestamp membership, complete mode alignment, finite tone, bounded counts,
duplicates, a 512-point cap, and a 4 MiB cap. Empty, partial, malformed, and zero-norm inputs
become `ABSTAIN` without fabricated numeric zeros.

Canonical observations are hash-checked and published as private append-only files beneath a
bounded root. Publication uses a durable temporary file, no-replace linking, and no-follow
directory traversal. Raw responses, headers, request URLs or queries, and article metadata are
never persisted.

The CLI defaults to synthetic offline fixtures, so physical provider calls remain zero. A future
online packet validator binds the packet hash, HEAD, fixed target, modes, query definition,
window, cap, zero retry, no-raw rule, attribution, purpose, and expiry, but no HTTP transport is
activated by this implementation.

## 불변식 / Invariants

```text
GDELT_RUNTIME_MODE=OFFLINE_FIXTURE
GDELT_PROVIDER_PHYSICAL_CALLS=0
GDELT_RAW_PROVIDER_ARTIFACTS=0
GDELT_ARTICLE_METADATA_STORAGE=0
GDELT_DECISION_AUTHORITY=NONE
GDELT_RISK_DECISION_HASH_INCLUDED=FALSE
GDELT_S5_FEATURE_ELIGIBLE=FALSE
GDELT_ONLINE_TRANSPORT=NOT_ACTIVATED
```
