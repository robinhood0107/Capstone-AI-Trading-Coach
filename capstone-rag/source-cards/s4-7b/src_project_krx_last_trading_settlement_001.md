---
schemaVersion: '2'
cardVariant: OFFICIAL_UPSTREAM_CARD
sourceId: src_project_krx_last_trading_settlement_001
cardId: card_krx_last_trading_settlement_001
title: KRX last trading instant와 final settlement date 구분
institution: krx
topic: krx_last_trading_settlement
sourceType: PROJECT_SOURCE_CARD
tier: PROJECT
accessLevel: PUBLIC
claim: last trading instant와 final settlement date는 별도 값이다.
evidenceClass: OFFICIAL_SERVICE_DOCUMENTATION
status: VERIFIED
verifiedAt: '2026-07-31T00:00:00Z'
accessNote: primary DOI metadata 또는 공식 institution page의 bounded locator와 claim-supporting metadata만 읽기 전용으로 확인했다.
licenseNote: 서지 metadata, official locator와 project-authored bounded claim만 사용하며 원문 PDF, HTML, screenshot 또는 provider payload는 corpus에 복제하지 않는다.
attribution: 한국거래소 Global KRX 공식 KOSPI 200 Options specification
canonicalUrl: https://global.krx.co.kr/contents/GLB/02/0201/0201040202/GLB0201040202.jsp
canonicalUrlSha256: be749101c776fb71572f292914fd5e7aec374fa99dda9e40fa30d9e6f300e04a
evidenceContentSha256: 16f7755a66b7970e4ee968877275843fa3a98ca9ff1aa130158c1dd608f3706f
upstreamSourceIds:
- src_krx_etf_etn_structure_001
- src_krx_openapi_service_catalog_001
retentionOwner: python-rag-corpus-privacy
retentionDays: 365
contentClass: PROJECT_AUTHORED_SANITIZED_CARD
externalProcessingAllowed: false
externalProcessingGate: NOT_GRANTED
adoptedSession: S4.7B
contradicts: []
modelSensitive: true
modelAssumptions:
- key: LAST_TRADING_AT_NOT_SETTLEMENT_DATE
  statement: lastTradingAt과 finalSettlementDate를 서로 다른 contract field로 유지한다.
limitations:
- product별 날짜와 settlement 방식은 해당 contract specification을 다시 확인해야 한다.
allowedUses:
- sanitized offline retrieval citation과 경계 설명
- derivatives contract time boundary 설명
forbiddenInferences:
- settlement date를 last trading instant로 대체하지 않는다.
representativeQuestions:
- last trading day와 final settlement day를 같은 expiry 값으로 써도 되나요?
---
# Source Card: KRX last trading instant와 final settlement date 구분

## 핵심 claim
last trading instant와 final settlement date는 별도 값이다.

## 적용 범위와 전제
KRX product specification이 두 날짜와 last-day trading hours를 별도 field로 제시하는 범위다.

## 프로젝트 적용
last trading day와 final settlement day를 같은 expiry 값으로 써도 되나요?
contract snapshot에 lastTradingAt과 finalSettlementDate를 별도 timezone-aware field로 둔다.

## 한계와 반례
- product별 날짜와 settlement 방식은 해당 contract specification을 다시 확인해야 한다.

## 허용 사용
- sanitized offline retrieval citation과 경계 설명
- derivatives contract time boundary 설명

## 금지 추론
- settlement date를 last trading instant로 대체하지 않는다.

## 근거 위치
front matter의 primary/official canonicalUrl, locator SHA-256과 bounded evidenceContentSha256을 확인한다.
