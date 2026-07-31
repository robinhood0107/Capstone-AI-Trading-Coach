---
schemaVersion: '2'
cardVariant: OFFICIAL_UPSTREAM_CARD
sourceId: src_project_krx_service_coverage_001
cardId: card_krx_service_coverage_001
title: KRX OpenAPI 서비스별 제공기간 범위
institution: krx
topic: krx_service_coverage
sourceType: PROJECT_SOURCE_CARD
tier: PROJECT
accessLevel: PUBLIC
claim: KRX OpenAPI의 2010년 이후 제공 범위는 서비스 목록의 개별 항목별 시작일에 한정해 해석하고 전체 시장·상품에 일반화하지 않는다.
evidenceClass: OFFICIAL_SERVICE_DOCUMENTATION
status: VERIFIED
verifiedAt: '2026-07-30T05:07:41Z'
accessNote: KRX Data Marketplace Open API 공식 서비스 목록의 대상기간 안내를 확인했다.
licenseNote: approvalId=AUTH_EXTERNAL_PROCESSING_30_PROJECT_CARDS_20260731. project-authored sanitized claim과 public locator/attribution만 external processing 대상으로 검토했다. upstream PDF, HTML, screenshot, API response, provider payload, raw/reference evidence는 복제하거나 전송하지 않는다. 사용자 동의와 third-party restriction review를 각각 확인했다.
attribution: 한국거래소 KRX Data Marketplace Open API 공식 서비스 목록
canonicalUrl: https://openapi.krx.co.kr/contents/OPP/INFO/service/OPPINFO004.cmd
canonicalUrlSha256: c7677f6db761f5a209f209df993ca6e84a96ab013dcccac707d96c497c70cd35
evidenceContentSha256: 016eb702d7f587c6ae7a50f76c7ec24a1e2fe80b2d8a10709230b0ef9b3567ed
upstreamSourceIds:
- src_krx_openapi_service_catalog_001
- src_krx_openapi_terms_001
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
- 한 서비스의 시작일은 다른 서비스의 시작일을 대신하지 않는다.
- 현재 entitlement 보유 여부를 증명하지 않는다.
allowedUses:
- KRX source coverage를 서비스별로 검증하는 provenance 설명
- sanitized offline retrieval citation과 경계 설명
forbiddenInferences:
- 모든 시장·상품·field가 같은 날짜부터 제공된다고 일반화하지 않는다.
- 현재 entitlement나 API key 보유를 주장하지 않는다.
representativeQuestions:
- KRX OpenAPI의 2010년 이후 범위는 모든 서비스에 똑같이 적용되나요?
---
# Source Card: KRX OpenAPI 서비스별 제공기간 범위

## 핵심 claim
KRX OpenAPI의 2010년 이후 제공 범위는 서비스 목록의 개별 항목별 시작일에 한정해 해석하고 전체 시장·상품에 일반화하지 않는다.

## 적용 범위와 전제
공식 서비스 목록의 대상기간과 개별 서비스 시작일에만 적용한다.

## 프로젝트 적용
KRX OpenAPI의 2010년 이후 범위는 모든 서비스에 똑같이 적용되나요?
registry에는 조회 서비스 ID와 해당 항목의 명시된 시작일을 함께 기록한다.

## 한계와 반례
- 한 서비스의 시작일은 다른 서비스의 시작일을 대신하지 않는다.
- 현재 entitlement 보유 여부를 증명하지 않는다.

## 허용 사용
- KRX source coverage를 서비스별로 검증하는 provenance 설명
- sanitized offline retrieval citation과 경계 설명

## 금지 추론
- 모든 시장·상품·field가 같은 날짜부터 제공된다고 일반화하지 않는다.
- 현재 entitlement나 API key 보유를 주장하지 않는다.

## 근거 위치
front matter의 primary/official canonicalUrl, locator SHA-256과 bounded evidenceContentSha256을 확인한다.
