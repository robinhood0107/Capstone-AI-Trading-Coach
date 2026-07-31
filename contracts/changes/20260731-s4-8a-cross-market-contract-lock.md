# S4.8A 교차시장 7종 계약과 entitlement 고정

상태: `APPROVED_CONTRACT_LOCK`
관련 Issue: #74
승인 표식: `AUTH_S4_8A_CROSS_MARKET_CONTRACT=APPROVED`

## KR: 변경 이유

S4.8B/C runtime보다 먼저 source 권리, point-in-time 시간축, 결측 표현, 판단 권한을
machine-readable 계약으로 고정한다. 이 변경은 교차시장 provider를 활성화하지 않으며,
KIS 후보 18개와 GDELT aggregate 행을 모두 `CANDIDATE_DISABLED`로 유지한다.

실제 KIS endpoint 이름과 exact 42개 조사 행의 운영 authority는 Git으로 추적하지 않는
로컬 전용 자료수급 레지스트리다. 공개 계약은 KIS 후보 18개의 opaque SHA-256 identity와
카테고리 수(애널리스트 3, 해외 선행 4, 국내 증폭 11)만 고정한다. GDELT aggregate는 KIS
18개와 분리된 선택적 설명 source이고 `decisionAuthority=NONE`이다.

## EN: Rationale

Machine-readable contracts now lock source rights, point-in-time chronology, missing-data
semantics, and decision authority before any S4.8B/C runtime implementation. This change
does not activate a cross-market provider. All 18 KIS candidates and the separate GDELT
aggregate row remain `CANDIDATE_DISABLED`.

The operational authority for actual KIS endpoint names and the exact 42 researched rows
remains in a local, untracked acquisition registry. Public contracts lock only 18 opaque
SHA-256 KIS identities and their category cardinalities (three analyst, four overseas lead,
and eleven domestic amplification). GDELT aggregate is a separate optional explanation
source with `decisionAuthority=NONE`.

## 계약 / Contracts

다음 일곱 versioned SSOT를 generator, positive fixture, fail-closed negative fixture와 함께
고정한다.

1. `market_source_entitlement.v1`
2. `cross_market_exposure_catalog.v1`
3. `cross_market_observation.v1`
4. `analyst_revision_evidence.v1`
5. `market_cause_evidence.v1`
6. `cross_market_risk_snapshot.v1`
7. `cross_market_policy_evaluation.v1`

`s4-8a-cross-market-get.v1`은 인증된 queryless
`GET /api/v1/risk/cross-market`의 latest-owner-only projection과 provider fan-out 0을
고정한다. 이 파일은 runtime/OpenAPI 구현 완료 증거가 아니다.

`s2-2-system-rule-catalog.v1`과 기존 Decision/RAG/Signal payload bytes는 그대로 유지한다.
별도 `s2-2-system-rule-catalog.v2`는 15번째 system rule
`cross_market_new_buy_guard`를 추가하고, `s2.2-metric-snapshot-v3`와
`HASH-CANONICALIZATION-S22-V3` golden vector가 판단 입력과 설명-only 제외 필드를
구분한다.

## 거부 계약 / Negative contracts

- 미래 `availableAt`과 만료 entitlement
- raw 저장, embedding, 외부 LLM, derived data 권한이 없는 materialization
- unknown endpoint identity
- 결측을 0으로 꾸미거나 incomplete 값을 `AVAILABLE`로 표시하는 observation
- P1 `ALLOW -> WARN`보다 높은 RiskDecision 권한
- GDELT article URL/text와 unknown field 주입

## 권한과 영향 / Authority and impact

```text
S4_8A_CONTRACT=LOCKED
S4_8B_C_RUNTIME=NOT_IMPLEMENTED
KIS_CANDIDATE_ENDPOINTS=18_DISABLED
GDELT_AGGREGATE_ENTITLEMENT=CANDIDATE_DISABLED
PROVIDER_PHYSICAL_CALLS=0
LIVE_ACCOUNT_CALLS=0
LIVE_ORDER_CALLS=0
MIGRATIONS=0
RETURN_ENGINE_FILES_CHANGED=0
EXPERIENCE_DASHBOARD_FILES_CHANGED=0
SECURITY_SCAN_TIMING=FINAL_CONSOLIDATED_CAMPAIGN
```

P1에서 추가 가능한 판단 변화는 적용 대상 신규 BUY의 `ALLOW -> WARN`뿐이다.
애널리스트·뉴스·원인 evidence와 RAG/LLM 출력은 RiskDecision 및 판단 hash를 바꾸지 않는다.
runtime, Flyway, provider activation은 후속 구현 단계이며, 이 계약 PR 자체에서는 모두 0이다.

## 재현 / Reproduction

```bash
uv run --frozen python contracts/generate_s4_8a_cross_market_contracts.py --check
uv run --frozen python -m unittest contracts.tests.test_s4_8a_cross_market_contracts -v
uv run --frozen python -m unittest discover -s contracts/tests -v
uv run --frozen python contracts/validate.py
```
