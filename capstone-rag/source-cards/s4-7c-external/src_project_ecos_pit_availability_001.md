---
schemaVersion: '2'
cardVariant: OFFICIAL_UPSTREAM_CARD
sourceId: src_project_ecos_pit_availability_001
cardId: card_ecos_pit_availability_001
title: ECOS StatisticSearch의 PIT 보수 경계
institution: ecos
topic: ecos_pit_availability
sourceType: PROJECT_SOURCE_CARD
tier: PROJECT
accessLevel: PUBLIC
claim: ECOS StatisticSearch 결과를 historical PIT로 간주하지 않으며 ingestion-time availableAt와 provenance가 없으면 leakage-sensitive feature에 사용하지 않는다.
evidenceClass: OFFICIAL_API_DOCUMENTATION
status: VERIFIED
verifiedAt: '2026-07-30T05:07:41Z'
accessNote: 한국은행 ECOS 공식 Open API 개발명세의 StatisticSearch 출력 필드 표를 확인했다.
licenseNote: approvalId=AUTH_EXTERNAL_PROCESSING_30_PROJECT_CARDS_20260731. project-authored sanitized claim과 public locator/attribution만 external processing 대상으로 검토했다. upstream PDF, HTML, screenshot, API response, provider payload, raw/reference evidence는 복제하거나 전송하지 않는다. 사용자 동의와 third-party restriction review를 각각 확인했다.
attribution: 한국은행 경제통계시스템 ECOS Open API 공식 개발명세
canonicalUrl: https://ecos.bok.or.kr/api/
canonicalUrlSha256: c096c3653729cd41e63fa5040bf8471cc95f9b0c71bd8a024788380e8f8439a4
evidenceContentSha256: abc45643d72a947eeefd66ebb72b3425299b851aa94a86709165e7d2c0b1b130
upstreamSourceIds:
- src_ecos_api_overview_001
- src_ecos_statistic_search_001
retentionOwner: python-rag-corpus-privacy
retentionDays: 365
contentClass: PROJECT_AUTHORED_SANITIZED_CARD
externalProcessingAllowed: true
externalProcessingGate: LICENSE_AND_CONSENT_VERIFIED
adoptedSession: S4.7A
contradicts: []
modelSensitive: false
modelAssumptions: []
limitations:
- 확인한 출력 필드 표만으로 historical publication semantics는 입증되지 않았다.
- PIT 기능이 없다고 단정하지 않는다.
allowedUses:
- leakage-sensitive feature의 fail-closed provenance 정책 설명
- sanitized offline retrieval citation과 경계 설명
forbiddenInferences:
- TIME을 publication time이나 revision vintage와 동일시하지 않는다.
- ECOS가 historical PIT를 지원하지 않는다고 단정하지 않는다.
representativeQuestions:
- ECOS StatisticSearch 값을 leakage-sensitive feature에 바로 쓰면 왜 안 되나요?
---
# Source Card: ECOS StatisticSearch의 PIT 보수 경계

## 핵심 claim
ECOS StatisticSearch 결과를 historical PIT로 간주하지 않으며 ingestion-time availableAt와 provenance가 없으면 leakage-sensitive feature에 사용하지 않는다.

## 적용 범위와 전제
공식 출력 필드 표에서 publication, revision, vintage semantics가 입증되지 않은 범위다.

## 프로젝트 적용
ECOS StatisticSearch 값을 leakage-sensitive feature에 바로 쓰면 왜 안 되나요?
ingestion-time availableAt와 source revision이 없으면 feature를 fail-closed한다.

## 한계와 반례
- 확인한 출력 필드 표만으로 historical publication semantics는 입증되지 않았다.
- PIT 기능이 없다고 단정하지 않는다.

## 허용 사용
- leakage-sensitive feature의 fail-closed provenance 정책 설명
- sanitized offline retrieval citation과 경계 설명

## 금지 추론
- TIME을 publication time이나 revision vintage와 동일시하지 않는다.
- ECOS가 historical PIT를 지원하지 않는다고 단정하지 않는다.

## 근거 위치
front matter의 primary/official canonicalUrl, locator SHA-256과 bounded evidenceContentSha256을 확인한다.
