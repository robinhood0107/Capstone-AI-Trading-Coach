---
schemaVersion: '2'
cardVariant: OFFICIAL_UPSTREAM_CARD
sourceId: src_project_kis_current_price_snapshot_001
cardId: card_kis_current_price_snapshot_001
title: KIS 현재가의 bounded snapshot 경계
institution: kis
topic: kis_current_price_snapshot
sourceType: PROJECT_SOURCE_CARD
tier: PROJECT
accessLevel: PUBLIC
claim: KIS current-price는 bounded snapshot이며 historical/PIT series를 대신하지 않는다.
evidenceClass: OFFICIAL_API_DOCUMENTATION
status: VERIFIED
verifiedAt: '2026-07-31T00:00:00Z'
accessNote: primary DOI metadata 또는 공식 institution page의 bounded locator와 claim-supporting metadata만 읽기 전용으로 확인했다.
licenseNote: 서지 metadata, official locator와 project-authored bounded claim만 사용하며 원문 PDF, HTML, screenshot 또는 provider payload는 corpus에 복제하지 않는다.
attribution: 한국투자증권 Open Trading API 공식 현재가 sample
canonicalUrl: https://github.com/koreainvestment/open-trading-api/blob/b093e42ba32d1df5f5ddad7a71cb715cbc800832/examples_llm/domestic_stock/inquire_price/inquire_price.py
canonicalUrlSha256: 36acf358b3e4130ae62bcd2d453279b25cac11bdb28858f42847d8c30ba56889
evidenceContentSha256: 6b0a15c2da42bacbc09640d6883e6e4e1bf7cd22cfb0eedcf39f6a58d707c8f9
upstreamSourceIds:
- src_kis_marketdata_price_001
retentionOwner: python-rag-corpus-privacy
retentionDays: 365
contentClass: PROJECT_AUTHORED_SANITIZED_CARD
externalProcessingAllowed: false
externalProcessingGate: NOT_GRANTED
adoptedSession: S4.7B
contradicts: []
modelSensitive: false
modelAssumptions: []
limitations:
- 단일 snapshot은 revision history나 historical availability를 제공하지 않는다.
allowedUses:
- sanitized offline retrieval citation과 경계 설명
- 현재가 snapshot provenance 설명
forbiddenInferences:
- 현재 snapshot을 과거 시점의 PIT observation으로 소급하지 않는다.
representativeQuestions:
- KIS 현재가 응답을 historical PIT series로 사용해도 되나요?
---
# Source Card: KIS 현재가의 bounded snapshot 경계

## 핵심 claim
KIS current-price는 bounded snapshot이며 historical/PIT series를 대신하지 않는다.

## 적용 범위와 전제
commit-pinned 공식 current-price sample의 snapshot endpoint 의미에만 적용한다.

## 프로젝트 적용
KIS 현재가 응답을 historical PIT series로 사용해도 되나요?
snapshot에는 observedAt, ingestedAt, source revision과 request scope를 기록한다.

## 한계와 반례
- 단일 snapshot은 revision history나 historical availability를 제공하지 않는다.

## 허용 사용
- sanitized offline retrieval citation과 경계 설명
- 현재가 snapshot provenance 설명

## 금지 추론
- 현재 snapshot을 과거 시점의 PIT observation으로 소급하지 않는다.

## 근거 위치
front matter의 primary/official canonicalUrl, locator SHA-256과 bounded evidenceContentSha256을 확인한다.
