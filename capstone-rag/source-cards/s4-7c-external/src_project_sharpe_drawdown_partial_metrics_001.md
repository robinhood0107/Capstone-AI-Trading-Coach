---
schemaVersion: '2'
cardVariant: SCHOLARLY_PRIMARY_CARD
sourceId: src_project_sharpe_drawdown_partial_metrics_001
cardId: card_sharpe_drawdown_partial_metrics_001
title: Sharpe와 maximum drawdown의 부분 위험 관점
institution: wiley
topic: sharpe_drawdown_partial_metrics
sourceType: PROJECT_SOURCE_CARD
tier: PROJECT
accessLevel: PUBLIC
claim: Sharpe와 maximum drawdown은 서로 다른 위험 단면이며 한 지표가 전체 위험을 대표하지 않는다.
evidenceClass: PRIMARY_RESEARCH
status: VERIFIED
verifiedAt: '2026-07-31T00:00:00Z'
accessNote: primary DOI metadata 또는 공식 institution page의 bounded locator와 claim-supporting metadata만 읽기 전용으로 확인했다.
licenseNote: approvalId=AUTH_EXTERNAL_PROCESSING_30_PROJECT_CARDS_20260731. project-authored sanitized claim과 public locator/attribution만 external processing 대상으로 검토했다. upstream PDF, HTML, screenshot, API response, provider payload, raw/reference evidence는 복제하거나 전송하지 않는다. 사용자 동의와 third-party restriction review를 각각 확인했다.
attribution: Hans Geboers, Benoît Depaire and Jan Annaert, Journal of Economic Surveys
canonicalUrl: https://doi.org/10.1111/joes.12520
canonicalUrlSha256: 4bbe976266efb9625e10a5d30892e60e1f656817d95c52c13ca370682cba5834
evidenceContentSha256: e383c351a0e42b4a22ff8e0b3df0faf4b7a2a048bc15a4acd4938943d21f4816
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
- 어떤 단일 metric 조합도 미래 손실을 완전히 설명하지 않는다.
allowedUses:
- sanitized offline retrieval citation과 경계 설명
forbiddenInferences:
- Sharpe 또는 maximum drawdown 하나를 전체 risk의 충분통계로 선언하지 않는다.
representativeQuestions:
- Sharpe가 높으면 drawdown risk도 충분히 설명됐다고 볼 수 있나요?
bibliographicLocator:
  authorityType: DOI_REGISTRY
  locatorType: DOI
  value: 10.1111/joes.12520
bibliographicMetadata:
  authors:
  - Hans Geboers
  - Benoît Depaire
  - Jan Annaert
  editionOrVersion: Volume 37, Number 3, pages 865-889
  title: A review on drawdown risk measures and their implications for risk management
  venue: Journal of Economic Surveys
  year: 2022
---
# Source Card: Sharpe와 maximum drawdown의 부분 위험 관점

## 핵심 claim
Sharpe와 maximum drawdown은 서로 다른 위험 단면이며 한 지표가 전체 위험을 대표하지 않는다.

## 적용 범위와 전제
return-dispersion ratio와 path-dependent drawdown measure의 차이에 적용한다.

## 프로젝트 적용
Sharpe가 높으면 drawdown risk도 충분히 설명됐다고 볼 수 있나요?
strategy report에 return, dispersion, drawdown과 tail metric을 별도로 표시한다.

## 한계와 반례
- 어떤 단일 metric 조합도 미래 손실을 완전히 설명하지 않는다.

## 허용 사용
- sanitized offline retrieval citation과 경계 설명

## 금지 추론
- Sharpe 또는 maximum drawdown 하나를 전체 risk의 충분통계로 선언하지 않는다.

## 근거 위치
front matter의 primary/official canonicalUrl, locator SHA-256과 bounded evidenceContentSha256을 확인한다.
