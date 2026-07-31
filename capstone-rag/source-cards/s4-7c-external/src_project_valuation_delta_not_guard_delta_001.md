---
schemaVersion: '2'
cardVariant: SCHOLARLY_PRIMARY_CARD
sourceId: src_project_valuation_delta_not_guard_delta_001
cardId: card_valuation_delta_not_guard_delta_001
title: Valuation delta와 deterministic hard-guard delta의 권한 분리
institution: university_of_chicago_press
topic: valuation_delta_not_guard_delta
sourceType: PROJECT_SOURCE_CARD
tier: PROJECT
accessLevel: PUBLIC
claim: model valuation delta는 운영상 conservative hard-guard delta의 authority가 아니다.
evidenceClass: PRIMARY_RESEARCH
status: VERIFIED
verifiedAt: '2026-07-31T00:00:00Z'
accessNote: primary DOI metadata 또는 공식 institution page의 bounded locator와 claim-supporting metadata만 읽기 전용으로 확인했다.
licenseNote: approvalId=AUTH_EXTERNAL_PROCESSING_30_PROJECT_CARDS_20260731. project-authored sanitized claim과 public locator/attribution만 external processing 대상으로 검토했다. upstream PDF, HTML, screenshot, API response, provider payload, raw/reference evidence는 복제하거나 전송하지 않는다. 사용자 동의와 third-party restriction review를 각각 확인했다.
attribution: Fischer Black and Myron Scholes; project deterministic rule catalog
canonicalUrl: https://doi.org/10.1086/260062
canonicalUrlSha256: 08c7e61fc0aa1f29292774d24814428748b23026825a04896266eeeafca52ae8
evidenceContentSha256: 3f337462cf0d344085ac38e676740adbb62fb24dc111f0866135f2119a639652
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
- key: VALUATION_DELTA_NOT_HARD_RISK_DELTA
  statement: valuation sensitivity와 deterministic hard-guard authority를 서로 다른 versioned input으로 유지한다.
limitations:
- 이 카드는 project rule threshold의 현재 숫자를 복제하지 않는다.
allowedUses:
- sanitized offline retrieval citation과 경계 설명
forbiddenInferences:
- valuation delta로 deterministic hard-risk decision을 우회하지 않는다.
representativeQuestions:
- option valuation delta로 deterministic risk guard를 대체해도 되나요?
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
# Source Card: Valuation delta와 deterministic hard-guard delta의 권한 분리

## 핵심 claim
model valuation delta는 운영상 conservative hard-guard delta의 authority가 아니다.

## 적용 범위와 전제
option sensitivity 설명과 project rule-catalog authority 분리에 적용한다.

## 프로젝트 적용
option valuation delta로 deterministic risk guard를 대체해도 되나요?
valuation output은 advisory input으로만 전달하고 hard guard는 versioned rule catalog가 결정한다.

## 한계와 반례
- 이 카드는 project rule threshold의 현재 숫자를 복제하지 않는다.

## 허용 사용
- sanitized offline retrieval citation과 경계 설명

## 금지 추론
- valuation delta로 deterministic hard-risk decision을 우회하지 않는다.

## 근거 위치
front matter의 primary/official canonicalUrl, locator SHA-256과 bounded evidenceContentSha256을 확인한다.
