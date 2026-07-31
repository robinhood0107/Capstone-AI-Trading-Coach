---
schemaVersion: '2'
cardVariant: SCHOLARLY_PRIMARY_CARD
sourceId: src_project_bsm_risk_neutral_001
cardId: card_bsm_risk_neutral_001
title: BSM risk-neutral 가격과 physical 확률의 경계
institution: university_of_chicago_press
topic: bsm_risk_neutral
sourceType: PROJECT_SOURCE_CARD
tier: PROJECT
accessLevel: PUBLIC
claim: BSM은 무차익·복제 가격식이며 physical 상승확률 예측기가 아니다.
evidenceClass: PRIMARY_RESEARCH
status: VERIFIED
verifiedAt: '2026-07-31T00:00:00Z'
accessNote: primary DOI metadata 또는 공식 institution page의 bounded locator와 claim-supporting metadata만 읽기 전용으로 확인했다.
licenseNote: 서지 metadata, official locator와 project-authored bounded claim만 사용하며 원문 PDF, HTML, screenshot 또는 provider payload는 corpus에 복제하지 않는다.
attribution: Fischer Black and Myron Scholes, Journal of Political Economy
canonicalUrl: https://doi.org/10.1086/260062
canonicalUrlSha256: 08c7e61fc0aa1f29292774d24814428748b23026825a04896266eeeafca52ae8
evidenceContentSha256: 51e893ac2c988d5950629b9388dba9afc414344ba6130266d0aeef5554078e68
upstreamSourceIds: []
retentionOwner: python-rag-corpus-privacy
retentionDays: 365
contentClass: PROJECT_AUTHORED_SANITIZED_CARD
externalProcessingAllowed: false
externalProcessingGate: NOT_GRANTED
adoptedSession: S4.7B
contradicts: []
modelSensitive: true
modelAssumptions:
- key: RISK_NEUTRAL_NOT_PHYSICAL_PROBABILITY
  statement: 복제와 무차익 가격결정의 measure를 실제 상승확률로 치환하지 않는다.
limitations:
- 모델 가정 밖 실제 시장 확률이나 미래 가격을 보장하지 않는다.
allowedUses:
- sanitized offline retrieval citation과 경계 설명
forbiddenInferences:
- risk-neutral measure를 physical 상승확률로 해석하지 않는다.
representativeQuestions:
- BSM 가격을 실제 주가 상승확률로 읽으면 왜 안 되나요?
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
# Source Card: BSM risk-neutral 가격과 physical 확률의 경계

## 핵심 claim
BSM은 무차익·복제 가격식이며 physical 상승확률 예측기가 아니다.

## 적용 범위와 전제
Black과 Scholes의 무차익·복제 가격결정 metadata와 risk-neutral 해석 경계에만 적용한다.

## 프로젝트 적용
BSM 가격을 실제 주가 상승확률로 읽으면 왜 안 되나요?
옵션 valuation 결과와 physical forecast를 별도 output과 evidence로 다룬다.

## 한계와 반례
- 모델 가정 밖 실제 시장 확률이나 미래 가격을 보장하지 않는다.

## 허용 사용
- sanitized offline retrieval citation과 경계 설명

## 금지 추론
- risk-neutral measure를 physical 상승확률로 해석하지 않는다.

## 근거 위치
front matter의 primary/official canonicalUrl, locator SHA-256과 bounded evidenceContentSha256을 확인한다.
