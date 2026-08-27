# P1 Compose, supply-chain, and handoff preparation

## 결정

Owner Phase A의 마지막 변경은 `deploy/p1/compose.yml`을 단일 Compose 권위로 유지하면서 기본 5개,
`--models` 7개, one-shot `run --rm`, loopback bind, no Docker socket, provider-free 기본값을 함께 검증한다.

## Team B OCI intake

Team B 실제 bundle은 restricted private GHCR repository의 immutable digest만 받는다. mutable tag와 foreign
repository는 거부한다. 동일 workflow identity가 keyless signed-blob으로 만든 receipt Sigstore bundle과
OCI signature, SLSA provenance, SPDX/CycloneDX SBOM을 검증한 뒤 ORAS digest pull을 수행한다. pulled root는
`manifest.json` + exact-10만 허용하며 서명된 receipt의 input/source/manifest/output hash와 다시 결속한다.

현재 실제 Team B digest나 receipt는 없으므로 remote verification을 실행하지 않는다. 준비된 contract와
verifier의 상태만 PASS이고 `TEAM_B_REAL_ARTIFACT=PENDING_EXTERNAL_TEAM`이다.

## Handoff

`docs/handoff/`의 시작, Team A, Team B, Owner 네 문서는 정확히 일곱 절만 사용한다. Team 문서에는 내부
단계명이나 local-only 경로를 노출하지 않고 workspace, 실행 명령, 완료 test, 제출물과 금지 범위만 둔다.

## 안전 경계

- 기본 provider/account/order/KIS Live/GDELT outbound call 0
- Docker socket mount 0, privileged container 0
- host port loopback only
- volume 삭제 0
- 실제 Team B artifact 없이 OCI pull/signature call 0
- tag/Release authority 0

## 판정

```text
OWNER_COMPOSE_5_7_PREP=PASS
OWNER_SUPPLY_CHAIN_PREP=PASS
OWNER_HANDOFF_DOCS=PASS
TEAM_B_REAL_ARTIFACT=PENDING_EXTERNAL_TEAM
```
