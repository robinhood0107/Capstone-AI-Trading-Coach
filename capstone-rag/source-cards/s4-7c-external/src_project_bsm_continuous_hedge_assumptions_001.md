---
schemaVersion: '2'
cardVariant: SCHOLARLY_PRIMARY_CARD
sourceId: src_project_bsm_continuous_hedge_assumptions_001
cardId: card_bsm_continuous_hedge_assumptions_001
title: BSM continuous hedge 가정의 적용 한계
institution: university_of_chicago_press
topic: bsm_continuous_hedge_assumptions
sourceType: PROJECT_SOURCE_CARD
tier: PROJECT
accessLevel: PUBLIC
claim: continuous delta hedge 결론은 연속거래·무마찰 등 모델 가정 아래 성립한다.
evidenceClass: PRIMARY_RESEARCH
status: VERIFIED
verifiedAt: '2026-07-31T00:00:00Z'
accessNote: primary DOI metadata 또는 공식 institution page의 bounded locator와 claim-supporting metadata만 읽기 전용으로 확인했다.
licenseNote: approvalId=AUTH_EXTERNAL_PROCESSING_30_PROJECT_CARDS_20260731. project-authored sanitized claim과 public locator/attribution만 external processing 대상으로 검토했다. upstream PDF, HTML, screenshot, API response, provider payload, raw/reference evidence는 복제하거나 전송하지 않는다. 사용자 동의와 third-party restriction review를 각각 확인했다.
attribution: Fischer Black and Myron Scholes, Journal of Political Economy
canonicalUrl: https://doi.org/10.1086/260062
canonicalUrlSha256: 08c7e61fc0aa1f29292774d24814428748b23026825a04896266eeeafca52ae8
evidenceContentSha256: 636373e0e2fb4740a84b97573a52808cef10f037dc0aeba0ffca3ddc57a9be87
upstreamSourceIds: []
retentionOwner: python-rag-corpus-privacy
retentionDays: 365
contentClass: PROJECT_AUTHORED_SANITIZED_CARD
externalProcessingAllowed: true
externalProcessingGate: LICENSE_AND_CONSENT_VERIFIED
adoptedSession: S4.7B
contradicts: []
modelSensitive: false
modelAssumptions: []
limitations:
- 불연속 가격, 비용과 체결 제약이 있는 실제 hedge 오차를 정량 보장하지 않는다.
allowedUses:
- sanitized offline retrieval citation과 경계 설명
forbiddenInferences:
- continuous hedge 결론을 실제 시장의 무손실 보장으로 확대하지 않는다.
representativeQuestions:
- continuous delta hedge를 실제 시장의 무손실 보장으로 볼 수 있나요?
bibliographicLocator:
  authorityType: DOI_REGISTRY
  locatorType: DOI
  value: 10.1086/260062
bibliographicMetadata:
  authors:
  - Fischer Black
  - Myron Scholes
  editionOrVersion: Volume 81, Number 3, pages 637-654
  title: The Pricing of Options and Corporate Liabilities
  venue: Journal of Political Economy
  year: 1973
---
# Source Card: BSM continuous hedge 가정의 적용 한계

## 핵심 claim
continuous delta hedge 결론은 연속거래·무마찰 등 모델 가정 아래 성립한다.

## 적용 범위와 전제
Black–Scholes replication argument의 명시된 idealized assumptions에만 적용한다.

## 프로젝트 적용
continuous delta hedge를 실제 시장의 무손실 보장으로 볼 수 있나요?
실제 hedge 설명에는 rebalance interval, liquidity와 transaction cost를 함께 표시한다.

## 한계와 반례
- 불연속 가격, 비용과 체결 제약이 있는 실제 hedge 오차를 정량 보장하지 않는다.

## 허용 사용
- sanitized offline retrieval citation과 경계 설명

## 금지 추론
- continuous hedge 결론을 실제 시장의 무손실 보장으로 확대하지 않는다.

## 근거 위치
front matter의 primary/official canonicalUrl, locator SHA-256과 bounded evidenceContentSha256을 확인한다.
