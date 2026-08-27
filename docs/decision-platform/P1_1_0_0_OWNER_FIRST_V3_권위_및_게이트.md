# P1 1.0.0 Owner-First full-app v3 권위와 게이트

## 현재 판정

```text
P1_OWNER_PHASE_A_CONTRACTS=LOCKED_MERGE_CANDIDATE
OWNER_AUTOMATION_JOURNAL_API=IMPLEMENTED_MERGE_CANDIDATE
ROOT_OPENAPI_OPERATIONS=56_IMPLEMENTED_MERGE_CANDIDATE
TARGET_ROOT_OPENAPI_OPERATIONS=56
TEAM_A_REQUIRED_OPERATIONS=33
TEAM_B_REAL_ARTIFACT=PENDING_EXTERNAL_TEAM
TEAM_A_REAL_UI=PENDING_EXTERNAL_TEAM
LIGHTGBM=RESEARCH_ONLY_NO_SIGNAL_OR_ORDER_AUTHORITY
KIS_LIVE_ORDER=IMMUTABLE_DISABLED
GDELT_OUTBOUND_CALLS=0
P1_FINAL=NOT_READY
P1_1_0_0_RELEASED=FALSE
```

현재 release catalog는 `contracts/catalogs/p1-full-app-release-contract.v3.json`, manifest schema는
`deploy/p1/full-app-release-manifest.v3.schema.json`이다. v1과 full-app v2 계약·schema·workflow는
역사적 회귀로 bytes를 보존한다.

## Owner-First 계약

Team A/B에게 요청하기 전에 Owner는 다음을 완성한다.

- exact-31 input pack과 fixed price-only LSTM ABI
- exact 10개 결과 파일과 manifest v2의 hostile-input-safe importer
- real Team B와 synthetic golden의 truth marker 분리
- production inference process, Vertex 신규 BUY veto, data-only daily collector
- append-only automation closed loop와 Automation/Journal API
- Team A exact-33 acceptance environment
- 기본 5개/모델 포함 7개 Compose, supply-chain과 handoff

Team B는 뉴스/GDELT/Vertex/Spring/account/order를 호출하거나 feature로 사용하지 않는다. Vertex는
final 신규 BUY 후보 하나에만 `VETO_BUY | NO_VETO | ABSTAIN`을 반환하고 SELL에는 호출하지 않는다.
LightGBM은 연구·재현 전용이며 production Signal/RiskDecision/order authority가 없다.

## v3 hard gate

다음 16개가 모두 `PASS`일 때만 `FINAL` manifest, `1.0.0` tag와 GitHub Release를 만들 수 있다.

1. `P1_CORE`
2. `PUBLIC_RAG_SEED`
3. `OWNER_RAG_BACKEND`
4. `BGE_OCR_CPU_INTEL`
5. `MARKET_DATA_DAILY`
6. `TEAM_B_REAL_ARTIFACT_V2`
7. `TEAM_A_REAL_UI_33`
8. `VERTEX_NEWS_VETO`
9. `JOURNAL`
10. `AUTOMATION_CLOSED_LOOP`
11. `LIGHTGBM_RESEARCH_DISCLOSURE`
12. `SECURITY_RELEASE`
13. `SUPPLY_CHAIN_RELEASE`
14. `OCI_REPRODUCIBILITY`
15. `COMPOSE_E2E`
16. `THREE_XKRX_SESSION_SOAK`

contract-only PASS는 runtime, external Team artifact, physical activation, soak 또는 release 증거가 아니다.
실제 provider/account/order 실행은 별도 exact approval과 credential 입력 뒤에만 가능하다.
