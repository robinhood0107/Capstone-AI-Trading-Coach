# S1.3G Naver active runtime·schema·test 퇴역

상태: `IMPLEMENTED_RETIREMENT`
관련 Issue: #76
선행 계약: `20260731-s1-3g-naver-retirement-gdelt-aggregate-lock.md`

## KR

계약 잠금에 따라 Naver active provider/runtime/storage 경계를 물리 코드에서 제거했다.
Naver collector·credential settings·CLI entrypoint, provider-shaped fixture와 테스트,
Naver snapshot schema·example·pair validator, shared manifest의 Naver union과 retention branch는
더 이상 active tree에 존재하지 않는다. `source_snapshot_manifest`와 retention command는 ECOS
전용으로 축소했다.

프로젝트가 작성한 Naver discovery 정책 경계 source card, source-card v2 공식 경계 fixture,
2026-07-16 당시 contract-change와 공개 감사 기록은 보존한다. 이 보존 자료는 provider 호출,
credential, metadata 저장 또는 30일 retention 권한을 다시 만들지 않는다.

승인된 local leaf의 두 파일은 type/mode/size/hash/inode drift 검증 뒤 exact unlink했고 leaf만
제거했다. 경로·내용·삭제 영수증은 Git에 넣지 않는다. 이는 application-visible 삭제이며
물리 secure erasure 보장은 아니다. 그 밖의 상위 디렉터리는 승인 범위 밖이라 보존했다.

## EN

The active Naver provider/runtime/storage boundary has been removed according to the prior
contract lock. The collector, credential settings, CLI entry point, provider-shaped fixtures
and tests, Naver snapshot schema/examples/pair validator, shared-manifest Naver union, and
retention branch no longer exist in the active tree. The shared manifest and retention command
are now ECOS-only.

The project-authored Naver discovery policy-boundary source card, its source-card-v2 official
boundary fixture, the 2026-07-16 contract record, and public audit history remain preserved.
These historical materials do not restore provider calls, credentials, metadata persistence,
or a 30-day retention authority.

The two files in the separately approved local leaf were unlinked only after exact
type/mode/size/hash/inode drift checks, and only the empty leaf was removed. Its path, content,
and local deletion receipt are not tracked. This establishes application-visible deletion,
not physical secure erasure. Parent directories outside the exact approval remain preserved.

## 불변식 / Invariants

```text
NAVER_ACTIVE_PROVIDER_RUNTIME_STORAGE=RETIRED
NAVER_PROVIDER_PHYSICAL_CALLS=0
NAVER_ACTIVE_CREDENTIAL_SETTINGS=0
NAVER_ACTIVE_SNAPSHOT_SCHEMA=0
NAVER_ACTIVE_RETENTION_BRANCH=0
NAVER_BOUNDARY_SOURCE_CARD=PRESERVED
HISTORICAL_AUDIT=PRESERVED
```
