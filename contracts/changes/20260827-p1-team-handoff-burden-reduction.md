# P1 Team A/B handoff burden reduction

## 결정

PR #173 이후 Owner Phase A가 main에서 완료됐으므로 두 Team 요청서의 과거 미완료 표현을 제거한다.
exact-33, exact-31, exact-10, manifest v2와 workspace 경계는 완화하지 않고, 이미 Owner가 구현한 backend,
fixture, validator, importer, projection, image/OCI supply-chain 작업을 Team에게 중복 배정하지 않는다.

## Team A 경계

Team A는 `workspaces/experience-dashboard/`의 기존 source를 보존하며 다섯 production UI 흐름만 완성한다.
Owner가 exact-33 backend matrix, generated client, Seed/reset, final image build와 재검증을 담당한다. Team A가
수동 33행 표나 backend/OpenAPI/Compose 변경을 만들 필요는 없다.

## Team B 경계

`OWNER_INPUT_MISSING`, output manifest v1, optional news와 adapter 미구현 설명은 현재 사실이 아니므로
폐기한다. Team B는 `workspaces/return-engine/`에서 Owner exact-31 input에 대한 fixed price-only LSTM,
rule baseline, exact-10/manifest v2와 two-run determinism만 담당한다. Owner가 local network-none validation,
import/projection/inference와 OCI/SBOM/provenance/signature 재현을 담당한다.

## 검증 표면

`./capstone artifact validate <bundle> --manifest-sha256 <sha256>`를 Owner local acceptance 표면으로 추가한다.
이 명령은 read-only, cap-drop, no-new-privileges, network-none container에서 importer의 validate-only 경로만
실행하며 DB, provider, account와 order 호출을 만들지 않는다. Linux와 Windows entrypoint는 같은
`artifact`와 `team-a` 명령을 전달한다.

## 불변식

```text
ROOT_OPENAPI_OPERATION_COUNT=56
TEAM_A_ACCEPTANCE_OPERATION_COUNT=33
OWNER_POST_TEAM_CODE_REQUIRED=0
TEAM_A_REQUEST_READY=TRUE
TEAM_B_REQUEST_READY=TRUE
PROVIDER_PHYSICAL_CALLS=0
GDELT_OUTBOUND_CALLS=0
KIS_LIVE_CALLS=0
RECURRING_AUTOMATION=DISABLED
P1_1_0_0_RELEASED=FALSE
```

이 변경은 Team 실제 결과, Vertex live, KIS Mock certification/activation, three-session soak 또는 Release
gate를 PASS로 승격하지 않는다.
