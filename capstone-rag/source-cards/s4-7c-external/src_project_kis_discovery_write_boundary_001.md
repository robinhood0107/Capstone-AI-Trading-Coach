---
schemaVersion: '2'
cardVariant: OFFICIAL_UPSTREAM_CARD
sourceId: src_project_kis_discovery_write_boundary_001
cardId: card_kis_discovery_write_boundary_001
title: KIS discovery와 write activation 권한 분리
institution: kis
topic: kis_discovery_write_boundary
sourceType: PROJECT_SOURCE_CARD
tier: PROJECT
accessLevel: PUBLIC
claim: read/discovery 지원은 order/cancel/reconcile write 활성화가 아니다.
evidenceClass: OFFICIAL_API_DOCUMENTATION
status: VERIFIED
verifiedAt: '2026-07-31T00:00:00Z'
accessNote: primary DOI metadata 또는 공식 institution page의 bounded locator와 claim-supporting metadata만 읽기 전용으로 확인했다.
licenseNote: approvalId=AUTH_EXTERNAL_PROCESSING_30_PROJECT_CARDS_20260731. project-authored sanitized claim과 public locator/attribution만 external processing 대상으로 검토했다. upstream PDF, HTML, screenshot, API response, provider payload, raw/reference evidence는 복제하거나 전송하지 않는다. 사용자 동의와 third-party restriction review를 각각 확인했다.
attribution: 한국투자증권 Open API 공식 소개와 pinned endpoint registry
canonicalUrl: https://apiportal.koreainvestment.com/about-open-api
canonicalUrlSha256: facb9e1e911b0810e6e4034b90fea5b7d2f28b0aed74a16df7bcd3d14ed30369
evidenceContentSha256: 1dd1487746c775d0f95095a5edfd93a02855f60392e24b09cd2c49d855aa312b
upstreamSourceIds:
- src_kis_openapi_overview_001
- src_kis_trading_cash_order_001
- src_kis_account_balance_001
retentionOwner: python-rag-corpus-privacy
retentionDays: 365
contentClass: PROJECT_AUTHORED_SANITIZED_CARD
externalProcessingAllowed: true
externalProcessingGate: LICENSE_AND_CONSENT_VERIFIED
adoptedSession: S4.7B
contradicts: []
modelSensitive: true
modelAssumptions:
- key: DISCOVERY_NOT_WRITE_ACTIVATION
  statement: API surface discovery와 live write activation은 별도 승인 상태로 유지한다.
limitations:
- 공식 write endpoint 존재는 project 배포의 write 권한을 생성하지 않는다.
allowedUses:
- sanitized offline retrieval citation과 경계 설명
- capability boundary 설명
forbiddenInferences:
- read 지원을 live write activation이나 account authority로 확대하지 않는다.
representativeQuestions:
- read endpoint를 확인한 것이 write 기능 승인도 뜻하나요?
---
# Source Card: KIS discovery와 write activation 권한 분리

## 핵심 claim
read/discovery 지원은 order/cancel/reconcile write 활성화가 아니다.

## 적용 범위와 전제
공식 API surface discovery와 project의 별도 live-write gate 분리에 적용한다.

## 프로젝트 적용
read endpoint를 확인한 것이 write 기능 승인도 뜻하나요?
read allowlist와 write capability를 별도 policy, credential과 activation state로 둔다.

## 한계와 반례
- 공식 write endpoint 존재는 project 배포의 write 권한을 생성하지 않는다.

## 허용 사용
- sanitized offline retrieval citation과 경계 설명
- capability boundary 설명

## 금지 추론
- read 지원을 live write activation이나 account authority로 확대하지 않는다.

## 근거 위치
front matter의 primary/official canonicalUrl, locator SHA-256과 bounded evidenceContentSha256을 확인한다.
