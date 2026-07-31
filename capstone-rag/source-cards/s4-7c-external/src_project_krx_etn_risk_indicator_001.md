---
schemaVersion: '2'
cardVariant: OFFICIAL_UPSTREAM_CARD
sourceId: src_project_krx_etn_risk_indicator_001
cardId: card_krx_etn_risk_indicator_001
title: KRX ETN risk indicator의 정의와 한계
institution: krx
topic: krx_etn_risk_indicator
sourceType: PROJECT_SOURCE_CARD
tier: PROJECT
accessLevel: PUBLIC
claim: ETN risk indicator의 공식 정의·한계를 보존한다.
evidenceClass: OFFICIAL_SERVICE_DOCUMENTATION
status: VERIFIED
verifiedAt: '2026-07-31T00:00:00Z'
accessNote: primary DOI metadata 또는 공식 institution page의 bounded locator와 claim-supporting metadata만 읽기 전용으로 확인했다.
licenseNote: approvalId=AUTH_EXTERNAL_PROCESSING_30_PROJECT_CARDS_20260731. project-authored sanitized claim과 public locator/attribution만 external processing 대상으로 검토했다. upstream PDF, HTML, screenshot, API response, provider payload, raw/reference evidence는 복제하거나 전송하지 않는다. 사용자 동의와 third-party restriction review를 각각 확인했다.
attribution: 한국거래소 정보데이터시스템 공식 ETN 투자지표
canonicalUrl: https://open.krx.co.kr/contents/OPN/01/01030302/OPN01030302.jsp
canonicalUrlSha256: 8217018029836fad13907730f1df5092112df68808a492ab95be4f77223eafbd
evidenceContentSha256: 006d42a3b1d09fa4f0d506e46cd2f47b7abf743b4b89234043d9319c31ad6c2e
upstreamSourceIds:
- src_krx_etn_risk_indicator_001
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
- 단일 indicator는 liquidity, issuer credit와 future loss를 완전히 설명하지 않는다.
allowedUses:
- sanitized offline retrieval citation과 경계 설명
- ETN indicator bounded 설명
forbiddenInferences:
- indicator 하나를 투자 적합성 또는 전체 위험 보장으로 확대하지 않는다.
representativeQuestions:
- KRX ETN risk indicator 하나로 전체 위험을 판단해도 되나요?
---
# Source Card: KRX ETN risk indicator의 정의와 한계

## 핵심 claim
ETN risk indicator의 공식 정의·한계를 보존한다.

## 적용 범위와 전제
공식 페이지에 정의된 ETN indicator의 이름, 산식 의미와 표시 범위에 적용한다.

## 프로젝트 적용
KRX ETN risk indicator 하나로 전체 위험을 판단해도 되나요?
indicator value에는 definition version, as-of와 product identity를 함께 표시한다.

## 한계와 반례
- 단일 indicator는 liquidity, issuer credit와 future loss를 완전히 설명하지 않는다.

## 허용 사용
- sanitized offline retrieval citation과 경계 설명
- ETN indicator bounded 설명

## 금지 추론
- indicator 하나를 투자 적합성 또는 전체 위험 보장으로 확대하지 않는다.

## 근거 위치
front matter의 primary/official canonicalUrl, locator SHA-256과 bounded evidenceContentSha256을 확인한다.
