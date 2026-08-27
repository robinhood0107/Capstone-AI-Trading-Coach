# 팀에 요청하기 전 통합 담당자 확인표

## 현재 결론

Owner가 Team A/B 요청 전에 끝낼 수 있는 Phase A 항목은 완료됐습니다. Team에게 backend, input 수집,
adapter, validator 또는 supply-chain 구현을 중복 배정하지 않습니다.

```text
OWNER_HANDOFF_READY=TRUE
OWNER_POST_TEAM_CODE_REQUIRED=0
TEAM_A_REQUEST_READY=TRUE
TEAM_B_REQUEST_READY=TRUE
TEAM_A_REAL_UI=PENDING_EXTERNAL_TEAM
TEAM_B_REAL_ARTIFACT=PENDING_EXTERNAL_TEAM
```

## 공통 Owner 완료

- [x] Automation/Journal API와 root OpenAPI exact-56
- [x] Team A exact-33 catalog, generated client, Seed/reset, live Spring acceptance
- [x] Team B exact-31 input pack, fixed ABI/cost/split, exact-10/manifest v2 schema
- [x] synthetic golden, hostile-input-safe validator/importer, projection, inference runtime
- [x] provider-free Vertex veto, daily collector, automation closed-loop fixture
- [x] 기본 5·models 7 Compose와 restart/one-shot/volume 검증
- [x] ordinary security, restricted OCI intake, SBOM/provenance/signature contract
- [x] PR #173 merge, exact merge SHA post-merge CI 6개, 별도 fresh-main clone
- [x] Team A/B/Owner handoff와 짧은 복사용 메시지
- [x] provider/account/order/GDELT/KIS Live physical call 0

## Team A에게 보낼 범위

- [x] `workspaces/experience-dashboard/`만 수정
- [x] 기존 source와 디자인을 보존하고 다섯 production UI 흐름만 완성
- [x] backend exact-33 표, fixture, image digest는 Owner가 재현
- [x] Team A 제출물은 PR/commit/lock SHA와 UI tests로 축소
- [x] backend/OpenAPI/migration/Compose/provider 변경 금지

## Team B에게 보낼 범위

- [x] Owner exact-31 input 경로와 manifest SHA를 별도 전달
- [x] price-only fixed LSTM, rule baseline, exact-10/manifest v2만 구현
- [x] existing preview/PTH는 삭제하지 않고 historical preview로 보존
- [x] local network-none `./capstone artifact validate` 제공
- [x] adapter/API/OCI signing tool은 Owner가 담당
- [x] Team B 제출물은 PR/commit/lock/input-output hash와 determinism/metric evidence로 축소

## Team 결과 수신 뒤

Owner는 새 adapter를 작성하지 않고 workspace diff와 계약을 검증합니다. 통과한 Team A source로 production
image/exact-33/UI를 재현하고, Team B source와 exact-10으로 restricted OCI/SBOM/provenance/signature를
재현한 뒤 기존 importer와 API projection만 사용합니다.

실패하면 Owner 계약을 완화하거나 우회 코드를 넣지 않고 해당 Team PR의 수정 범위만 돌려줍니다. 자세한
순서는 [두 팀 결과를 받은 뒤 통합 확인표](P1_TEAM_A_B_수신_후_통합_체크리스트.md)를 따릅니다.

## 정상적인 외부 대기

- Team A 실제 production UI
- Team B 실제 model artifact
- Vertex live
- KIS Mock certification과 실제 activation runtime
- 24-hour health와 연속 3 XKRX session soak
- v3 hard gate 16개와 별도 Release 승인

recurring automation은 계속 비활성입니다. 위 외부 항목은 Team 요청 전 Owner 미완료로 다시 세지 않으며,
실제 증거가 오기 전 PASS로 승격하지도 않습니다.
