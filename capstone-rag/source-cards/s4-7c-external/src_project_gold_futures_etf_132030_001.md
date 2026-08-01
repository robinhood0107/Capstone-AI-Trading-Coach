---
schemaVersion: '2'
cardVariant: OFFICIAL_UPSTREAM_CARD
sourceId: src_project_gold_futures_etf_132030_001
cardId: card_gold_futures_etf_132030_001
title: 132030 금선물 ETF의 선물·환헤지·롤오버 경계
institution: samsungfund
topic: gold_futures_etf_132030
sourceType: PROJECT_SOURCE_CARD
tier: PROJECT
accessLevel: PUBLIC
claim: 132030 KODEX 골드선물(H)은 S&P GSCI Gold Index Total Return을 추종하는 COMEX 금선물 기반 환헤지 상품이므로 현물 금과 동일한 성과로 간주하지 않는다.
evidenceClass: OFFICIAL_PRODUCT_DOCUMENTATION
status: VERIFIED
verifiedAt: '2026-07-30T05:07:41Z'
accessNote: 삼성자산운용 공식 상품 페이지에서 상품 구조의 bounded 항목을 확인했다.
licenseNote: approvalId=AUTH_EXTERNAL_PROCESSING_30_PROJECT_CARDS_20260731. project-authored sanitized claim과 public locator/attribution만 external processing 대상으로 검토했다. upstream PDF, HTML, screenshot, API response, provider payload, raw/reference evidence는 복제하거나 전송하지 않는다. 사용자 동의와 third-party restriction review를 각각 확인했다.
attribution: 삼성자산운용 Kodex 공식 KODEX 골드선물(H) 상품 페이지
canonicalUrl: https://www.samsungfund.com/etf/product/view.do?id=2ETF24
canonicalUrlSha256: 7262d88ba2d97eb2378a5240d4e2fc43f4d40264ff7d2f62ede809760ac35364
evidenceContentSha256: c10e221942fb845c8ae8aa8a04240e5c6cc717cf098a5328b58911c266c34a7d
upstreamSourceIds:
- src_samsungfund_gold_futures_etf_001
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
- 선물 rollover와 환헤지 효과로 현물 금과 성과가 달라질 수 있다.
- live NAV, 가격, 수익률과 미래 성과는 범위 밖이다.
allowedUses:
- 132030 상품 구조와 basis, rollover, hedge 한계 설명
- sanitized offline retrieval citation과 경계 설명
forbiddenInferences:
- 투자 권유, 수익 보장, live 가격을 추론하지 않는다.
- 현물 금과 동일한 성과라고 표현하지 않는다.
representativeQuestions:
- 132030 KODEX 골드선물(H)을 현물 금과 같은 투자로 보면 왜 안 되나요?
---
# Source Card: 132030 금선물 ETF의 선물·환헤지·롤오버 경계

## 핵심 claim
132030 KODEX 골드선물(H)은 S&P GSCI Gold Index Total Return을 추종하는 COMEX 금선물 기반 환헤지 상품이므로 현물 금과 동일한 성과로 간주하지 않는다.

## 적용 범위와 전제
공식 상품 페이지의 종목코드, 기초지수, 선물 exposure, 환헤지와 rollover에 적용한다.

## 프로젝트 적용
132030 KODEX 골드선물(H)을 현물 금과 같은 투자로 보면 왜 안 되나요?
설명에는 futures exposure, 환헤지와 roll cost 가능성을 함께 제시한다.

## 한계와 반례
- 선물 rollover와 환헤지 효과로 현물 금과 성과가 달라질 수 있다.
- live NAV, 가격, 수익률과 미래 성과는 범위 밖이다.

## 허용 사용
- 132030 상품 구조와 basis, rollover, hedge 한계 설명
- sanitized offline retrieval citation과 경계 설명

## 금지 추론
- 투자 권유, 수익 보장, live 가격을 추론하지 않는다.
- 현물 금과 동일한 성과라고 표현하지 않는다.

## 근거 위치
front matter의 primary/official canonicalUrl, locator SHA-256과 bounded evidenceContentSha256을 확인한다.
