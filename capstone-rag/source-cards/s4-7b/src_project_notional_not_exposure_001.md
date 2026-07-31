---
schemaVersion: '2'
cardVariant: SCHOLARLY_PRIMARY_CARD
sourceId: src_project_notional_not_exposure_001
cardId: card_notional_not_exposure_001
title: Derivative notional과 exposure의 구분
institution: bis
topic: notional_not_exposure
sourceType: PROJECT_SOURCE_CARD
tier: PROJECT
accessLevel: PUBLIC
claim: notional은 market value·credit exposure·amount at risk와 동일하지 않다.
evidenceClass: OFFICIAL_REPORT
status: VERIFIED
verifiedAt: '2026-07-31T00:00:00Z'
accessNote: primary DOI metadata 또는 공식 institution page의 bounded locator와 claim-supporting metadata만 읽기 전용으로 확인했다.
licenseNote: 서지 metadata, official locator와 project-authored bounded claim만 사용하며 원문 PDF, HTML, screenshot 또는 provider payload는 corpus에 복제하지 않는다.
attribution: Bank for International Settlements, BIS Data Portal
canonicalUrl: https://data.bis.org/topics/OTC_DER
canonicalUrlSha256: 30fb899bf3b36cb890e5ab58229b3d6166bd4f2f6ff3871f0c6325724f157561
evidenceContentSha256: 90208f7f4c9e3300f4bf51e8022bce21ee5cb13991091112d40ea3d68146117b
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
- key: NOTIONAL_NOT_EXPOSURE
  statement: notional, market value, credit exposure와 amount at risk를 서로 다른 measure로 유지한다.
limitations:
- 계약별 netting, collateral과 future exposure 계산은 별도 모델이 필요하다.
allowedUses:
- sanitized offline retrieval citation과 경계 설명
forbiddenInferences:
- notional 전액을 현재 손실액이나 credit exposure로 단정하지 않는다.
representativeQuestions:
- 파생상품 notional을 실제 위험 노출액으로 그대로 써도 되나요?
bibliographicLocator:
  authorityType: OFFICIAL_INSTITUTION
  locatorType: OFFICIAL_URL
  value: https://data.bis.org/topics/OTC_DER
bibliographicMetadata:
  authors:
  - Bank for International Settlements
  editionOrVersion: Overview and methodology verified 2026-07-31
  title: OTC derivatives statistics
  venue: BIS Data Portal
  year: 2026
---
# Source Card: Derivative notional과 exposure의 구분

## 핵심 claim
notional은 market value·credit exposure·amount at risk와 동일하지 않다.

## 적용 범위와 전제
BIS OTC statistics가 notional, market value와 credit exposure를 별도 measure로 제시하는 범위에 적용한다.

## 프로젝트 적용
파생상품 notional을 실제 위험 노출액으로 그대로 써도 되나요?
risk report에서 notional과 valuation 또는 exposure measure를 별도 column으로 보존한다.

## 한계와 반례
- 계약별 netting, collateral과 future exposure 계산은 별도 모델이 필요하다.

## 허용 사용
- sanitized offline retrieval citation과 경계 설명

## 금지 추론
- notional 전액을 현재 손실액이나 credit exposure로 단정하지 않는다.

## 근거 위치
front matter의 primary/official canonicalUrl, locator SHA-256과 bounded evidenceContentSha256을 확인한다.
