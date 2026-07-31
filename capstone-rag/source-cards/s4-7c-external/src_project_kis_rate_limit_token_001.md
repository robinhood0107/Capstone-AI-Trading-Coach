---
schemaVersion: '2'
cardVariant: OFFICIAL_UPSTREAM_CARD
sourceId: src_project_kis_rate_limit_token_001
cardId: card_kis_rate_limit_token_001
title: KIS REST와 token issuance 유량 분리
institution: kis
topic: kis_rate_limit_token
sourceType: PROJECT_SOURCE_CARD
tier: PROJECT
accessLevel: PUBLIC
claim: REST와 token issuance budget을 분리하고 공식 상한 아래 fail-closed한다.
evidenceClass: OFFICIAL_API_DOCUMENTATION
status: VERIFIED
verifiedAt: '2026-07-31T00:00:00Z'
accessNote: primary DOI metadata 또는 공식 institution page의 bounded locator와 claim-supporting metadata만 읽기 전용으로 확인했다.
licenseNote: approvalId=AUTH_EXTERNAL_PROCESSING_30_PROJECT_CARDS_20260731. project-authored sanitized claim과 public locator/attribution만 external processing 대상으로 검토했다. upstream PDF, HTML, screenshot, API response, provider payload, raw/reference evidence는 복제하거나 전송하지 않는다. 사용자 동의와 third-party restriction review를 각각 확인했다.
attribution: 한국투자증권 Open API 공식 호출 유량 공지
canonicalUrl: https://apiportal.koreainvestment.com/community/10000000-0000-0011-0000-000000000001/post/d0d1a83f-6f8d-4437-9700-6d26702fd989
canonicalUrlSha256: ded98b129f7700c285a863cbaa1da8f0010a404eeb179a06595c3e6404b260ae
evidenceContentSha256: dec93dea0ce4681312911605a2ad5528b1072ce5890e5061ba58aeccd0f576cb
upstreamSourceIds:
- src_kis_rate_limit_001
- src_kis_openapi_overview_001
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
- 공식 상한은 현재 잔여 quota나 성공 가능성을 보장하지 않는다.
allowedUses:
- sanitized offline retrieval citation과 경계 설명
- rate-limit fail-closed 정책 설명
forbiddenInferences:
- 유량 상한을 자동 재시도 또는 burst 허가로 해석하지 않는다.
representativeQuestions:
- KIS REST와 token 발급을 하나의 retry budget으로 합쳐도 되나요?
---
# Source Card: KIS REST와 token issuance 유량 분리

## 핵심 claim
REST와 token issuance budget을 분리하고 공식 상한 아래 fail-closed한다.

## 적용 범위와 전제
공식 REST, 모의, token issuance 유량 공지와 project fail-closed policy에 적용한다.

## 프로젝트 적용
KIS REST와 token 발급을 하나의 retry budget으로 합쳐도 되나요?
credential mode별 opaque scope에서 REST limiter와 token limiter를 분리한다.

## 한계와 반례
- 공식 상한은 현재 잔여 quota나 성공 가능성을 보장하지 않는다.

## 허용 사용
- sanitized offline retrieval citation과 경계 설명
- rate-limit fail-closed 정책 설명

## 금지 추론
- 유량 상한을 자동 재시도 또는 burst 허가로 해석하지 않는다.

## 근거 위치
front matter의 primary/official canonicalUrl, locator SHA-256과 bounded evidenceContentSha256을 확인한다.
