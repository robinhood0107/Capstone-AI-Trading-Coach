---
schemaVersion: '2'
cardVariant: SCHOLARLY_PRIMARY_CARD
sourceId: src_project_monte_carlo_not_stress_probability_001
cardId: card_monte_carlo_not_stress_probability_001
title: Monte Carlo 확률과 deterministic stress의 구분
institution: elsevier
topic: monte_carlo_not_stress_probability
sourceType: PROJECT_SOURCE_CARD
tier: PROJECT
accessLevel: PUBLIC
claim: stochastic simulation probability와 deterministic severe scenario loss를 같은 확률로 합치지 않는다.
evidenceClass: PRIMARY_RESEARCH
status: VERIFIED
verifiedAt: '2026-07-31T00:00:00Z'
accessNote: primary DOI metadata 또는 공식 institution page의 bounded locator와 claim-supporting metadata만 읽기 전용으로 확인했다.
licenseNote: 서지 metadata, official locator와 project-authored bounded claim만 사용하며 원문 PDF, HTML, screenshot 또는 provider payload는 corpus에 복제하지 않는다.
attribution: Phelim P. Boyle, Journal of Financial Economics
canonicalUrl: https://doi.org/10.1016/0304-405X(77)90005-8
canonicalUrlSha256: 573f945496f684f02597b5b7bd3cb0ba083e67c36b7e5eccae6f3b4ca3839e99
evidenceContentSha256: da6ff83eed316d99d5a83d9aacd065ffb42d16dfdef8f6be1230b865316aa1bc
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
- key: STOCHASTIC_PROBABILITY_NOT_STRESS_PROBABILITY
  statement: stochastic sampling probability와 designed stress severity를 별도 evidence로 유지한다.
limitations:
- scenario plausibility나 실제 발생빈도를 이 카드가 정량화하지 않는다.
allowedUses:
- sanitized offline retrieval citation과 경계 설명
forbiddenInferences:
- deterministic stress loss에 sampling probability를 임의 부여하지 않는다.
representativeQuestions:
- stress scenario 손실을 Monte Carlo 발생확률과 같은 숫자로 합쳐도 되나요?
bibliographicLocator:
  authorityType: DOI_REGISTRY
  locatorType: DOI
  value: 10.1016/0304-405X(77)90005-8
bibliographicMetadata:
  authors:
  - Phelim P. Boyle
  editionOrVersion: Volume 4, Number 3, pages 323-338
  title: 'Options: A Monte Carlo approach'
  venue: Journal of Financial Economics
  year: 1977
---
# Source Card: Monte Carlo 확률과 deterministic stress의 구분

## 핵심 claim
stochastic simulation probability와 deterministic severe scenario loss를 같은 확률로 합치지 않는다.

## 적용 범위와 전제
specified stochastic law의 simulation estimate와 designed severe scenario를 구분한다.

## 프로젝트 적용
stress scenario 손실을 Monte Carlo 발생확률과 같은 숫자로 합쳐도 되나요?
결과에 stochastic probability estimate와 scenario severity label을 별도 field로 둔다.

## 한계와 반례
- scenario plausibility나 실제 발생빈도를 이 카드가 정량화하지 않는다.

## 허용 사용
- sanitized offline retrieval citation과 경계 설명

## 금지 추론
- deterministic stress loss에 sampling probability를 임의 부여하지 않는다.

## 근거 위치
front matter의 primary/official canonicalUrl, locator SHA-256과 bounded evidenceContentSha256을 확인한다.
