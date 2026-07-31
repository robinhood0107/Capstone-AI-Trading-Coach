---
schemaVersion: '2'
cardVariant: SCHOLARLY_PRIMARY_CARD
sourceId: src_project_var_es_coherence_001
cardId: card_var_es_coherence_001
title: VaR와 Expected Shortfall coherence 경계
institution: wiley
topic: var_es_coherence
sourceType: PROJECT_SOURCE_CARD
tier: PROJECT
accessLevel: PUBLIC
claim: VaR와 ES는 서로 다른 tail-risk functional이며 coherence 성질도 구분한다.
evidenceClass: PRIMARY_RESEARCH
status: VERIFIED
verifiedAt: '2026-07-31T00:00:00Z'
accessNote: primary DOI metadata 또는 공식 institution page의 bounded locator와 claim-supporting metadata만 읽기 전용으로 확인했다.
licenseNote: 서지 metadata, official locator와 project-authored bounded claim만 사용하며 원문 PDF, HTML, screenshot 또는 provider payload는 corpus에 복제하지 않는다.
attribution: Philippe Artzner, Freddy Delbaen, Jean-Marc Eber and David Heath
canonicalUrl: https://doi.org/10.1111/1467-9965.00068
canonicalUrlSha256: afa54dd0912912c281d95548e7b78d5a7bb8bf119a9c2406adf38fbb073d4b1d
evidenceContentSha256: bc74718d9ed9482711605cd85db353a6dca421f671b1f872f3a62a7b1f7bcc8c
upstreamSourceIds: []
retentionOwner: python-rag-corpus-privacy
retentionDays: 365
contentClass: PROJECT_AUTHORED_SANITIZED_CARD
externalProcessingAllowed: false
externalProcessingGate: NOT_GRANTED
adoptedSession: S4.7B
contradicts: []
modelSensitive: false
modelAssumptions: []
limitations:
- 특정 portfolio 분포에서 두 measure의 수치 관계를 보장하지 않는다.
allowedUses:
- sanitized offline retrieval citation과 경계 설명
forbiddenInferences:
- VaR와 ES의 정의 또는 coherence 성질을 서로 바꾸지 않는다.
representativeQuestions:
- VaR와 ES를 같은 tail-risk 숫자로 취급하면 왜 안 되나요?
bibliographicLocator:
  authorityType: DOI_REGISTRY
  locatorType: DOI
  value: 10.1111/1467-9965.00068
bibliographicMetadata:
  authors:
  - Philippe Artzner
  - Freddy Delbaen
  - Jean-Marc Eber
  - David Heath
  editionOrVersion: Volume 9, Number 3, pages 203-228
  title: Coherent Measures of Risk
  venue: Mathematical Finance
  year: 1999
---
# Source Card: VaR와 Expected Shortfall coherence 경계

## 핵심 claim
VaR와 ES는 서로 다른 tail-risk functional이며 coherence 성질도 구분한다.

## 적용 범위와 전제
risk functional의 정의와 coherence axioms를 구분하는 설명에 적용한다.

## 프로젝트 적용
VaR와 ES를 같은 tail-risk 숫자로 취급하면 왜 안 되나요?
risk report에 measure name, confidence level, horizon과 estimator를 명시한다.

## 한계와 반례
- 특정 portfolio 분포에서 두 measure의 수치 관계를 보장하지 않는다.

## 허용 사용
- sanitized offline retrieval citation과 경계 설명

## 금지 추론
- VaR와 ES의 정의 또는 coherence 성질을 서로 바꾸지 않는다.

## 근거 위치
front matter의 primary/official canonicalUrl, locator SHA-256과 bounded evidenceContentSha256을 확인한다.
