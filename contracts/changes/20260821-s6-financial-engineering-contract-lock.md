# S6 금융공학·교차시장 계약 잠금

## 결정

- S6.1은 2-state causal HMM offline report, S6.2는 exact exponential GBM Monte Carlo,
  S6.3은 AR(1)에서 exact OU mapping을 사용하는 평균회귀 진단으로 고정한다.
- S6.4는 trusted `option_contract_terms.v1`과 서버 계산 `ACT/365F` tau를 사용하는 교육 valuation이다.
- S6.5는 legacy risk snapshot/report table과 분리된 append-only FE snapshot·manifest를 사용한다.
- S6.6 event study와 LightGBM replay는 `researchOnly=true`, decision/runtime Signal authority 0이다.
- S6.7 v2는 evidence mode와 storage mode를 분리하고 immutable 95/97.5/99 threshold artifact 없이는
  unavailable이다. P1 runtime 최고 권한은 신규 BUY `ALLOW -> WARN`이다.

## 호환성과 금지 경계

기존 v1 schema/catalog/example과 completion record bytes는 변경하지 않는다. HMM active wire,
LightGBM production/release/activation, ENFORCED HOLD/BLOCK, provider/live/account/order 호출,
외부 workspace 구현은 이 변경의 권한이 아니다.

## 생성과 검증

`contracts/generate_s6_contracts.py`가 10개 report/snapshot/research schema와
`option_contract_terms.v1`, positive/negative fixture, `s2-2-system-rule-catalog.v3`, S6 lock catalog의
단일 생성 권위다. `--check`, contract unittest, global validator, OpenAPI parity로 검증한다.
