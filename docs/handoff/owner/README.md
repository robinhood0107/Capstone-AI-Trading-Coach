# Owner integration handoff

## 1. 최종 프로그램 목표

Owner 준비물과 Team A/B 수신물을 변경 없이 검증·통합하고, 외부 hard gate와 별도 승인 전에는 release나
물리 provider 실행을 열지 않습니다.

## 2. Owner가 이미 준비한 것

exact-56 API, exact-33 Dashboard acceptance, Team B input/golden/importer/runtime, 기본 5·모델 7 Compose,
ordinary security, local network-none Team B validator와 supply-chain intake를 소유합니다.

## 3. 수정할 것

Team 결과 수신 뒤 새 adapter를 작성하지 않습니다. Team A source로 production image를 만들고 exact-33/UI를
재검증하며, Team B source와 exact-10으로 restricted OCI·SBOM·provenance·signature를 재현합니다. 계약 실패는
Owner 우회 코드가 아니라 해당 Team PR 수정 요청으로 돌려보냅니다.

## 4. 실행 명령

```bash
./capstone doctor
./capstone up
./capstone team-a acceptance
./capstone artifact validate <bundle-directory> --manifest-sha256 <manifest-sha256>
./capstone smoke
./capstone status
uv run --frozen python contracts/verify_p1_compose_supply_handoff.py
deploy/p1/verify-team-b-oci <immutable-reference> <receipt.json> <receipt.sigstore.json>
```

## 5. 완료 테스트

Compose 5/7, restart recovery, one-shot residual 0, named volume 보존, Team A exact-33, Team B exact-10,
ordinary security, clean clone과 exact merge SHA post-merge CI를 확인합니다.

## 6. 제출할 파일·commit·OCI digest

Team PR/commit/lock/image 또는 OCI digest, Owner merge SHA, CI URL, Compose/fresh-clone evidence, provider physical
count와 남은 hard gate를 최종 보고에 기록합니다.

## 7. 하지 말아야 할 것

Team 계약 실패를 Owner 코드로 우회하지 않고, 실제 artifact 없이 REAL badge를 만들지 않습니다. 별도 승인
전 Vertex/KIS mock physical 실행, KIS Live, GDELT outbound, tag와 GitHub Release를 생성하지 않습니다.
