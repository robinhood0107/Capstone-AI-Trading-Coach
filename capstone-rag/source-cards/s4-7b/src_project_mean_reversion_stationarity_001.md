---
schemaVersion: '2'
cardVariant: SCHOLARLY_PRIMARY_CARD
sourceId: src_project_mean_reversion_stationarity_001
cardId: card_mean_reversion_stationarity_001
title: Mean reversion과 stationarity evidence 경계
institution: taylor_francis
topic: mean_reversion_stationarity
sourceType: PROJECT_SOURCE_CARD
tier: PROJECT
accessLevel: PUBLIC
claim: OU 형태를 썼다는 사실만으로 관측 가격의 mean reversion이 입증되지 않으며 unit-root/stability 검증이 필요하다.
evidenceClass: PRIMARY_RESEARCH
status: VERIFIED
verifiedAt: '2026-07-31T00:00:00Z'
accessNote: primary DOI metadata 또는 공식 institution page의 bounded locator와 claim-supporting metadata만 읽기 전용으로 확인했다.
licenseNote: 서지 metadata, official locator와 project-authored bounded claim만 사용하며 원문 PDF, HTML, screenshot 또는 provider payload는 corpus에 복제하지 않는다.
attribution: David A. Dickey and Wayne A. Fuller, JASA
canonicalUrl: https://doi.org/10.1080/01621459.1979.10482531
canonicalUrlSha256: db5bd28a0da67deee4afd47494f2af05f310c0c4870620d0a1aa6fec9ecf2d80
evidenceContentSha256: ee39cb00e6066e5799e6f494cdb6f8411727da9283424161d9b361954e9cca47
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
- 단일 test 통과가 모든 regime의 structural stability를 보장하지 않는다.
allowedUses:
- sanitized offline retrieval citation과 경계 설명
forbiddenInferences:
- 모델 식의 형태만으로 관측 가격의 stationarity를 확정하지 않는다.
representativeQuestions:
- OU 식을 적었다는 이유만으로 market price가 mean reverting이라고 말할 수 있나요?
bibliographicLocator:
  authorityType: DOI_REGISTRY
  locatorType: DOI
  value: 10.1080/01621459.1979.10482531
bibliographicMetadata:
  authors:
  - David A. Dickey
  - Wayne A. Fuller
  editionOrVersion: Volume 74, Number 366a, pages 427-431
  title: Distribution of the Estimators for Autoregressive Time Series with a Unit Root
  venue: Journal of the American Statistical Association
  year: 1979
---
# Source Card: Mean reversion과 stationarity evidence 경계

## 핵심 claim
OU 형태를 썼다는 사실만으로 관측 가격의 mean reversion이 입증되지 않으며 unit-root/stability 검증이 필요하다.

## 적용 범위와 전제
observed series의 unit-root and stability evidence 필요성을 설명하는 범위에 적용한다.

## 프로젝트 적용
OU 식을 적었다는 이유만으로 market price가 mean reverting이라고 말할 수 있나요?
calibration 전에 transform, sample window, unit-root와 stability diagnostics를 기록한다.

## 한계와 반례
- 단일 test 통과가 모든 regime의 structural stability를 보장하지 않는다.

## 허용 사용
- sanitized offline retrieval citation과 경계 설명

## 금지 추론
- 모델 식의 형태만으로 관측 가격의 stationarity를 확정하지 않는다.

## 근거 위치
front matter의 primary/official canonicalUrl, locator SHA-256과 bounded evidenceContentSha256을 확인한다.
