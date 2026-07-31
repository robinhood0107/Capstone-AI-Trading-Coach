---
schemaVersion: '2'
cardVariant: SCHOLARLY_PRIMARY_CARD
sourceId: src_project_expected_payoff_measure_discount_001
cardId: card_expected_payoff_measure_discount_001
title: Expected payoff의 measure와 discounting 경계
institution: jstor
topic: expected_payoff_measure_discount
sourceType: PROJECT_SOURCE_CARD
tier: PROJECT
accessLevel: PUBLIC
claim: expected payoff에는 measure·conditioning·horizon·numeraire/discounting이 필요하다.
evidenceClass: PRIMARY_RESEARCH
status: VERIFIED
verifiedAt: '2026-07-31T00:00:00Z'
accessNote: primary DOI metadata 또는 공식 institution page의 bounded locator와 claim-supporting metadata만 읽기 전용으로 확인했다.
licenseNote: 서지 metadata, official locator와 project-authored bounded claim만 사용하며 원문 PDF, HTML, screenshot 또는 provider payload는 corpus에 복제하지 않는다.
attribution: Robert C. Merton, The Bell Journal of Economics and Management Science
canonicalUrl: https://doi.org/10.2307/3003143
canonicalUrlSha256: cfe9968e500096d1b0cdbba448e060868aa4fb0e5e0bdf1a768bb0a4df5fe479
evidenceContentSha256: 7287e32dbb0551e71627eac8944948f67e57aba7b0d239d986a89d5e6d3b1c09
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
- key: EXPECTED_PAYOFF_REQUIRES_MEASURE_AND_DISCOUNTING
  statement: expected payoff에는 measure, conditioning, horizon과 discounting convention을 결합한다.
limitations:
- 특정 asset의 physical expected return을 이 카드가 추정하지 않는다.
allowedUses:
- sanitized offline retrieval citation과 경계 설명
forbiddenInferences:
- measure나 discounting이 없는 expectation을 valuation으로 확정하지 않는다.
representativeQuestions:
- expected payoff만 적고 measure와 할인 기준을 생략해도 되나요?
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
# Source Card: Expected payoff의 measure와 discounting 경계

## 핵심 claim
expected payoff에는 measure·conditioning·horizon·numeraire/discounting이 필요하다.

## 적용 범위와 전제
rational option pricing에서 expectation과 discounting을 해석하는 범위에 적용한다.

## 프로젝트 적용
expected payoff만 적고 measure와 할인 기준을 생략해도 되나요?
모든 expected payoff output에 measure, information set, horizon과 numeraire를 기록한다.

## 한계와 반례
- 특정 asset의 physical expected return을 이 카드가 추정하지 않는다.

## 허용 사용
- sanitized offline retrieval citation과 경계 설명

## 금지 추론
- measure나 discounting이 없는 expectation을 valuation으로 확정하지 않는다.

## 근거 위치
front matter의 primary/official canonicalUrl, locator SHA-256과 bounded evidenceContentSha256을 확인한다.
