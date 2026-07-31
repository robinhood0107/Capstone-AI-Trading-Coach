---
schemaVersion: '2'
cardVariant: OFFICIAL_UPSTREAM_CARD
sourceId: src_project_opendart_status_quota_001
cardId: card_opendart_status_quota_001
title: OpenDART status 020과 가변 요청 제한
institution: opendart
topic: opendart_status_quota
sourceType: PROJECT_SOURCE_CARD
tier: PROJECT
accessLevel: PUBLIC
claim: OpenDART status 020은 요청 제한 초과로 처리하되 20,000건을 영구 한도로 간주하지 않고 자동 재시도 없는 typed failure로 기록한다.
evidenceClass: OFFICIAL_API_DOCUMENTATION
status: VERIFIED
verifiedAt: '2026-07-30T05:07:41Z'
accessNote: OpenDART 공식 개발가이드의 메시지 설명에서 status 020과 가변 제한 문구를 확인했다.
licenseNote: approvalId=AUTH_EXTERNAL_PROCESSING_30_PROJECT_CARDS_20260731. project-authored sanitized claim과 public locator/attribution만 external processing 대상으로 검토했다. upstream PDF, HTML, screenshot, API response, provider payload, raw/reference evidence는 복제하거나 전송하지 않는다. 사용자 동의와 third-party restriction review를 각각 확인했다.
attribution: 금융감독원 전자공시 OpenDART 공식 개발가이드
canonicalUrl: https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS005&apiId=2020052
canonicalUrlSha256: af9a8a642d8761a7df6c25fa4d7625f85e97321382dbdfedb293f52e5afccc41
evidenceContentSha256: 61c0335c33cf77a1143c31ef6566152bf5115314214f266115c91e0f60102fc6
upstreamSourceIds:
- src_opendart_major_report_001
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
- 20,000건은 일반적 설명이며 계정별 영구 보장값이 아니다.
- 현재 잔여 quota나 credential 상태를 증명하지 않는다.
allowedUses:
- status 020의 typed failure 분류와 운영 runbook 설명
- sanitized offline retrieval citation과 경계 설명
forbiddenInferences:
- status 020을 성공이나 빈 결과로 바꾸지 않는다.
- API key의 유효성이나 현재 한도를 추론하지 않는다.
representativeQuestions:
- OpenDART status 020을 만나면 왜 20,000건 고정 한도로 처리하면 안 되나요?
---
# Source Card: OpenDART status 020과 가변 요청 제한

## 핵심 claim
OpenDART status 020은 요청 제한 초과로 처리하되 20,000건을 영구 한도로 간주하지 않고 자동 재시도 없는 typed failure로 기록한다.

## 적용 범위와 전제
공식 개발가이드의 status 020 의미와 가변 제한 문구에만 적용한다.

## 프로젝트 적용
OpenDART status 020을 만나면 왜 20,000건 고정 한도로 처리하면 안 되나요?
수집기는 status 020을 typed quota failure로 보존하고 자동 재호출하지 않는다.

## 한계와 반례
- 20,000건은 일반적 설명이며 계정별 영구 보장값이 아니다.
- 현재 잔여 quota나 credential 상태를 증명하지 않는다.

## 허용 사용
- status 020의 typed failure 분류와 운영 runbook 설명
- sanitized offline retrieval citation과 경계 설명

## 금지 추론
- status 020을 성공이나 빈 결과로 바꾸지 않는다.
- API key의 유효성이나 현재 한도를 추론하지 않는다.

## 근거 위치
front matter의 primary/official canonicalUrl, locator SHA-256과 bounded evidenceContentSha256을 확인한다.
