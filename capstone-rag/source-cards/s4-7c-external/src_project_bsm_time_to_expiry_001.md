---
schemaVersion: '2'
cardVariant: SCHOLARLY_PRIMARY_CARD
sourceId: src_project_bsm_time_to_expiry_001
cardId: card_bsm_time_to_expiry_001
title: BSM time-to-expiry와 보유기간의 경계
institution: jstor
topic: bsm_time_to_expiry
sourceType: PROJECT_SOURCE_CARD
tier: PROJECT
accessLevel: PUBLIC
claim: BSM의 T는 valuation 시점부터 계약의 actual last-trading/expiry instant까지다.
evidenceClass: PRIMARY_RESEARCH
status: VERIFIED
verifiedAt: '2026-07-31T00:00:00Z'
accessNote: primary DOI metadata 또는 공식 institution page의 bounded locator와 claim-supporting metadata만 읽기 전용으로 확인했다.
licenseNote: approvalId=AUTH_EXTERNAL_PROCESSING_30_PROJECT_CARDS_20260731. project-authored sanitized claim과 public locator/attribution만 external processing 대상으로 검토했다. upstream PDF, HTML, screenshot, API response, provider payload, raw/reference evidence는 복제하거나 전송하지 않는다. 사용자 동의와 third-party restriction review를 각각 확인했다.
attribution: Robert C. Merton, The Bell Journal of Economics and Management Science
canonicalUrl: https://doi.org/10.2307/3003143
canonicalUrlSha256: cfe9968e500096d1b0cdbba448e060868aa4fb0e5e0bdf1a768bb0a4df5fe479
evidenceContentSha256: 7e94c11488e1220ac115e4f056f97d10e50b731e16d39e5271fe77648060cbc2
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
- key: TIME_TO_EXPIRY_NOT_HOLDING_PERIOD
  statement: time-to-expiry는 valuation instant와 실제 계약 expiry instant의 차이다.
limitations:
- calendar convention과 product별 settlement 규칙은 별도 official evidence가 필요하다.
allowedUses:
- sanitized offline retrieval citation과 경계 설명
forbiddenInferences:
- T를 투자자의 임의 holding period로 대체하지 않는다.
representativeQuestions:
- BSM의 T를 임의 보유기간으로 바꾸면 왜 안 되나요?
bibliographicLocator:
  authorityType: DOI_REGISTRY
  locatorType: DOI
  value: 10.2307/3003143
bibliographicMetadata:
  authors:
  - Robert C. Merton
  editionOrVersion: Volume 4, Number 1, page 141
  title: Theory of Rational Option Pricing
  venue: The Bell Journal of Economics and Management Science
  year: 1973
---
# Source Card: BSM time-to-expiry와 보유기간의 경계

## 핵심 claim
BSM의 T는 valuation 시점부터 계약의 actual last-trading/expiry instant까지다.

## 적용 범위와 전제
계약의 actual expiry instant를 사용하는 option-pricing horizon에만 적용한다.

## 프로젝트 적용
BSM의 T를 임의 보유기간으로 바꾸면 왜 안 되나요?
valuation timestamp와 exchange contract의 last-trading 또는 expiry instant를 같은 timezone 기준으로 계산한다.

## 한계와 반례
- calendar convention과 product별 settlement 규칙은 별도 official evidence가 필요하다.

## 허용 사용
- sanitized offline retrieval citation과 경계 설명

## 금지 추론
- T를 투자자의 임의 holding period로 대체하지 않는다.

## 근거 위치
front matter의 primary/official canonicalUrl, locator SHA-256과 bounded evidenceContentSha256을 확인한다.
