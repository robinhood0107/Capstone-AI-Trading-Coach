---
schemaVersion: '2'
cardVariant: SCHOLARLY_PRIMARY_CARD
sourceId: src_project_delta_hedge_residual_cost_001
cardId: card_delta_hedge_residual_cost_001
title: Discrete delta hedge의 residual risk와 거래비용
institution: wiley
topic: delta_hedge_residual_cost
sourceType: PROJECT_SOURCE_CARD
tier: PROJECT
accessLevel: PUBLIC
claim: discrete hedge와 transaction cost는 residual risk를 만든다.
evidenceClass: PRIMARY_RESEARCH
status: VERIFIED
verifiedAt: '2026-07-31T00:00:00Z'
accessNote: primary DOI metadata 또는 공식 institution page의 bounded locator와 claim-supporting metadata만 읽기 전용으로 확인했다.
licenseNote: approvalId=AUTH_EXTERNAL_PROCESSING_30_PROJECT_CARDS_20260731. project-authored sanitized claim과 public locator/attribution만 external processing 대상으로 검토했다. upstream PDF, HTML, screenshot, API response, provider payload, raw/reference evidence는 복제하거나 전송하지 않는다. 사용자 동의와 third-party restriction review를 각각 확인했다.
attribution: Hayne E. Leland, The Journal of Finance
canonicalUrl: https://doi.org/10.1111/j.1540-6261.1985.tb02383.x
canonicalUrlSha256: 278ce6027abe3e7e7059f9a5513484d955eddcafc9a1240cb526a8a56dcbc05b
evidenceContentSha256: 1ba0d109e8b34454e4cf108b98307ef99e24108f2e2006b3635d20591de086ed
upstreamSourceIds: []
retentionOwner: python-rag-corpus-privacy
retentionDays: 365
contentClass: PROJECT_AUTHORED_SANITIZED_CARD
externalProcessingAllowed: true
externalProcessingGate: LICENSE_AND_CONSENT_VERIFIED
adoptedSession: S4.7B
contradicts: []
modelSensitive: true
modelAssumptions:
- key: DELTA_HEDGE_RESIDUAL_RISK
  statement: discrete rebalance와 transaction cost 뒤의 residual risk를 0으로 두지 않는다.
limitations:
- 특정 시장의 현재 거래비용이나 최적 hedge 주기를 이 카드가 산출하지 않는다.
allowedUses:
- sanitized offline retrieval citation과 경계 설명
forbiddenInferences:
- 모델 delta가 residual risk와 비용을 제거한다고 추론하지 않는다.
representativeQuestions:
- discrete delta hedge 뒤에도 위험과 비용이 남는 이유는 무엇인가요?
bibliographicLocator:
  authorityType: DOI_REGISTRY
  locatorType: DOI
  value: 10.1111/j.1540-6261.1985.tb02383.x
bibliographicMetadata:
  authors:
  - Hayne E. Leland
  editionOrVersion: Volume 40, Number 5, pages 1283-1301
  title: Option Pricing and Replication with Transactions Costs
  venue: The Journal of Finance
  year: 1985
---
# Source Card: Discrete delta hedge의 residual risk와 거래비용

## 핵심 claim
discrete hedge와 transaction cost는 residual risk를 만든다.

## 적용 범위와 전제
Leland의 transaction-cost replication 연구가 다루는 discrete hedge 경계에 적용한다.

## 프로젝트 적용
discrete delta hedge 뒤에도 위험과 비용이 남는 이유는 무엇인가요?
hedge 결과에는 rebalance frequency, cost model과 residual PnL을 분리 기록한다.

## 한계와 반례
- 특정 시장의 현재 거래비용이나 최적 hedge 주기를 이 카드가 산출하지 않는다.

## 허용 사용
- sanitized offline retrieval citation과 경계 설명

## 금지 추론
- 모델 delta가 residual risk와 비용을 제거한다고 추론하지 않는다.

## 근거 위치
front matter의 primary/official canonicalUrl, locator SHA-256과 bounded evidenceContentSha256을 확인한다.
