---
schemaVersion: '2'
cardVariant: OFFICIAL_UPSTREAM_CARD
sourceId: src_project_kis_market_calendar_001
cardId: card_kis_market_calendar_001
title: KIS 휴장일과 거래일 as-of 경계
institution: kis
topic: kis_market_calendar
sourceType: PROJECT_SOURCE_CARD
tier: PROJECT
accessLevel: PUBLIC
claim: 휴장일·거래일 판정은 공식 calendar source와 as-of를 보존한다.
evidenceClass: OFFICIAL_API_DOCUMENTATION
status: VERIFIED
verifiedAt: '2026-07-31T00:00:00Z'
accessNote: primary DOI metadata 또는 공식 institution page의 bounded locator와 claim-supporting metadata만 읽기 전용으로 확인했다.
licenseNote: approvalId=AUTH_EXTERNAL_PROCESSING_30_PROJECT_CARDS_20260731. project-authored sanitized claim과 public locator/attribution만 external processing 대상으로 검토했다. upstream PDF, HTML, screenshot, API response, provider payload, raw/reference evidence는 복제하거나 전송하지 않는다. 사용자 동의와 third-party restriction review를 각각 확인했다.
attribution: 한국투자증권 Open Trading API 공식 휴장일 sample
canonicalUrl: https://github.com/koreainvestment/open-trading-api/blob/b093e42ba32d1df5f5ddad7a71cb715cbc800832/examples_llm/domestic_stock/chk_holiday/chk_holiday.py
canonicalUrlSha256: c79b7fb6c84f7f6618d1a43b82e625f9b7a2cda187c31c36437b2722265b9c2a
evidenceContentSha256: 92b6ba127f40f2db58727842d0485ab306741bf8f9d7be2fe43cd56fbd906aeb
upstreamSourceIds:
- src_kis_market_calendar_001
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
- 향후 임시휴장이나 provider contract 변경은 새 snapshot 검증이 필요하다.
allowedUses:
- sanitized offline retrieval citation과 경계 설명
- 휴장일 fail-closed 정책 설명
forbiddenInferences:
- 오래된 calendar snapshot을 현재 거래 가능성의 보장으로 쓰지 않는다.
representativeQuestions:
- 거래일 판정에 어떤 provenance를 남겨야 하나요?
---
# Source Card: KIS 휴장일과 거래일 as-of 경계

## 핵심 claim
휴장일·거래일 판정은 공식 calendar source와 as-of를 보존한다.

## 적용 범위와 전제
commit-pinned 공식 holiday endpoint와 조회 as-of의 조합에 적용한다.

## 프로젝트 적용
거래일 판정에 어떤 provenance를 남겨야 하나요?
calendar snapshot에 market, date, as-of, source revision과 판정값을 보존한다.

## 한계와 반례
- 향후 임시휴장이나 provider contract 변경은 새 snapshot 검증이 필요하다.

## 허용 사용
- sanitized offline retrieval citation과 경계 설명
- 휴장일 fail-closed 정책 설명

## 금지 추론
- 오래된 calendar snapshot을 현재 거래 가능성의 보장으로 쓰지 않는다.

## 근거 위치
front matter의 primary/official canonicalUrl, locator SHA-256과 bounded evidenceContentSha256을 확인한다.
