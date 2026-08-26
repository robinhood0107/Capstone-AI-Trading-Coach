# P1 Owner input pack and synthetic golden bundle

## KR

contract-locked `p1-return-engine-input-pack.v1`과
`p1-return-engine-artifact-manifest.v2`를 실제 provider-free Owner 도구로 구현한다.

- 검증된 `market-data-seed.v1` archive의 manifest와 네 Parquet hash를 먼저 검증한다.
- latest exact-31과 고정 `132030`, XKRX session, RAW_CLOSE OHLCV, ECOS 최대 2 series,
  explicit corporate-action exclusion manifest, global split과 fixed 35bps policy를 새 input pack에 복사한다.
- input pack은 새 owner-private `0700` directory와 `0600` file만 사용하고 manifest를 마지막에 게시한다.
- synthetic golden은 fixed ABI 31-symbol safetensors와 exact 10개 결과 파일을 생성한다.
- golden manifest는 `SYNTHETIC_GOLDEN`, `realTeamB=false`, `performanceClaimAllowed=false`,
  `orderAuthority=NONE`을 강제한다.
- 동일 manifest 재실행은 검증된 no-op이고 다른 manifest, symlink, tamper는 거부한다.

provider, account, balance, order, GDELT, Vertex, KIS Live physical call은 0이다. Owner-private output과
source archive path는 Git에 추적하지 않는다.

## EN

Implements the locked input-pack and Return manifest v2 contracts as a provider-free Owner tool. It verifies
the sealed market-data archive, publishes a manifest-last exact-31 input pack, and creates a wire-compatible
synthetic ten-file golden bundle. Replays are verified no-ops; different manifests, symlinks, and tampering
fail closed. Synthetic output never claims real Team B, performance, or order authority.
