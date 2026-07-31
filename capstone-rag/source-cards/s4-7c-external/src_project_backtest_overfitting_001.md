---
schemaVersion: '2'
cardVariant: SCHOLARLY_PRIMARY_CARD
sourceId: src_project_backtest_overfitting_001
cardId: card_backtest_overfitting_001
title: Backtest 반복 선택과 false discovery 경계
institution: wiley
topic: backtest_overfitting
sourceType: PROJECT_SOURCE_CARD
tier: PROJECT
accessLevel: PUBLIC
claim: 반복 선택·동일 history 재사용은 backtest false discovery를 키우므로 temporal/OOS와 multiple-testing evidence가 필요하다.
evidenceClass: PRIMARY_RESEARCH
status: VERIFIED
verifiedAt: '2026-07-31T00:00:00Z'
accessNote: primary DOI metadata 또는 공식 institution page의 bounded locator와 claim-supporting metadata만 읽기 전용으로 확인했다.
licenseNote: approvalId=AUTH_EXTERNAL_PROCESSING_30_PROJECT_CARDS_20260731. project-authored sanitized claim과 public locator/attribution만 external processing 대상으로 검토했다. upstream PDF, HTML, screenshot, API response, provider payload, raw/reference evidence는 복제하거나 전송하지 않는다. 사용자 동의와 third-party restriction review를 각각 확인했다.
attribution: Halbert White, Econometrica
canonicalUrl: https://doi.org/10.1111/1468-0262.00152
canonicalUrlSha256: 68ae583d236e402969241d905f4c34422b3e28cd1fd664f9ed1452a1eb5ac0ea
evidenceContentSha256: 8d49722742f4c2279e13d9d40d5dbbf4e24a10887449bc438a57d198b2eaaebe
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
- 특정 correction 하나가 모든 adaptive research bias를 제거하지 않는다.
allowedUses:
- sanitized offline retrieval citation과 경계 설명
forbiddenInferences:
- 반복 선택된 최고 in-sample 결과를 독립 OOS evidence로 표현하지 않는다.
representativeQuestions:
- 같은 history에서 반복 선택한 최고 backtest를 그대로 채택하면 왜 안 되나요?
bibliographicLocator:
  authorityType: DOI_REGISTRY
  locatorType: DOI
  value: 10.1111/1468-0262.00152
bibliographicMetadata:
  authors:
  - Halbert White
  editionOrVersion: Volume 68, Number 5, pages 1097-1126
  title: A Reality Check for Data Snooping
  venue: Econometrica
  year: 2000
---
# Source Card: Backtest 반복 선택과 false discovery 경계

## 핵심 claim
반복 선택·동일 history 재사용은 backtest false discovery를 키우므로 temporal/OOS와 multiple-testing evidence가 필요하다.

## 적용 범위와 전제
data snooping과 repeated model selection의 false-discovery risk에 적용한다.

## 프로젝트 적용
같은 history에서 반복 선택한 최고 backtest를 그대로 채택하면 왜 안 되나요?
temporal split, untouched OOS, candidate count와 selection history를 evidence로 남긴다.

## 한계와 반례
- 특정 correction 하나가 모든 adaptive research bias를 제거하지 않는다.

## 허용 사용
- sanitized offline retrieval citation과 경계 설명

## 금지 추론
- 반복 선택된 최고 in-sample 결과를 독립 OOS evidence로 표현하지 않는다.

## 근거 위치
front matter의 primary/official canonicalUrl, locator SHA-256과 bounded evidenceContentSha256을 확인한다.
