---
schemaVersion: '2'
cardVariant: SCHOLARLY_PRIMARY_CARD
sourceId: src_project_threshold_cvar_not_exact_es_001
cardId: card_threshold_cvar_not_exact_es_001
title: Threshold CVaR 평균과 exact finite-sample ES의 경계
institution: wiley
topic: threshold_cvar_not_exact_es
sourceType: PROJECT_SOURCE_CARD
tier: PROJECT
accessLevel: PUBLIC
claim: threshold 이하 단순평균은 fractional boundary를 다루는 exact finite-sample ES와 항상 같지 않다.
evidenceClass: PRIMARY_RESEARCH
status: VERIFIED
verifiedAt: '2026-07-31T00:00:00Z'
accessNote: primary DOI metadata 또는 공식 institution page의 bounded locator와 claim-supporting metadata만 읽기 전용으로 확인했다.
licenseNote: approvalId=AUTH_EXTERNAL_PROCESSING_30_PROJECT_CARDS_20260731. project-authored sanitized claim과 public locator/attribution만 external processing 대상으로 검토했다. upstream PDF, HTML, screenshot, API response, provider payload, raw/reference evidence는 복제하거나 전송하지 않는다. 사용자 동의와 third-party restriction review를 각각 확인했다.
attribution: Carlo Acerbi and Dirk Tasche, Economic Notes
canonicalUrl: https://doi.org/10.1111/1468-0300.00091
canonicalUrlSha256: a09ef36dc0360f4f0e776881bc3e9aeae998b7ba96d587c1bf233f5d7e8d9560
evidenceContentSha256: 5dbbd813739e2eed61026b750751812d11ea03a0f1cdc81067d387f6a2a70550
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
- key: THRESHOLD_CVAR_NOT_EXACT_ES
  statement: finite-sample ES는 quantile boundary의 fractional mass convention을 명시한다.
limitations:
- 연속분포의 큰 표본 근사와 finite-sample exact 계산은 별도로 평가한다.
allowedUses:
- sanitized offline retrieval citation과 경계 설명
forbiddenInferences:
- threshold 이하 단순평균을 모든 표본의 exact ES로 단정하지 않는다.
representativeQuestions:
- VaR threshold 아래 표본 평균을 exact ES라고 불러도 되나요?
bibliographicLocator:
  authorityType: DOI_REGISTRY
  locatorType: DOI
  value: 10.1111/1468-0300.00091
bibliographicMetadata:
  authors:
  - Carlo Acerbi
  - Dirk Tasche
  editionOrVersion: Volume 31, Number 2, pages 379-388
  title: 'Expected Shortfall: A Natural Coherent Alternative to Value at Risk'
  venue: Economic Notes
  year: 2002
---
# Source Card: Threshold CVaR 평균과 exact finite-sample ES의 경계

## 핵심 claim
threshold 이하 단순평균은 fractional boundary를 다루는 exact finite-sample ES와 항상 같지 않다.

## 적용 범위와 전제
finite sample에서 quantile boundary mass를 다루는 ES 정의 구분에 적용한다.

## 프로젝트 적용
VaR threshold 아래 표본 평균을 exact ES라고 불러도 되나요?
estimator에 tail count, quantile convention과 fractional boundary policy를 기록한다.

## 한계와 반례
- 연속분포의 큰 표본 근사와 finite-sample exact 계산은 별도로 평가한다.

## 허용 사용
- sanitized offline retrieval citation과 경계 설명

## 금지 추론
- threshold 이하 단순평균을 모든 표본의 exact ES로 단정하지 않는다.

## 근거 위치
front matter의 primary/official canonicalUrl, locator SHA-256과 bounded evidenceContentSha256을 확인한다.
