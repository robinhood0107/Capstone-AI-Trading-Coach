---
schemaVersion: '2'
cardVariant: OFFICIAL_UPSTREAM_CARD
sourceId: src_project_krx_etf_etn_structure_001
cardId: card_krx_etf_etn_structure_001
title: KRX ETF fund와 ETN issuer-credit 구조 구분
institution: krx
topic: krx_etf_etn_structure
sourceType: PROJECT_SOURCE_CARD
tier: PROJECT
accessLevel: PUBLIC
claim: ETF fund와 ETN issuer-credit structure를 혼동하지 않는다.
evidenceClass: OFFICIAL_SERVICE_DOCUMENTATION
status: VERIFIED
verifiedAt: '2026-07-31T00:00:00Z'
accessNote: primary DOI metadata 또는 공식 institution page의 bounded locator와 claim-supporting metadata만 읽기 전용으로 확인했다.
licenseNote: 서지 metadata, official locator와 project-authored bounded claim만 사용하며 원문 PDF, HTML, screenshot 또는 provider payload는 corpus에 복제하지 않는다.
attribution: 한국거래소 정보데이터시스템 공식 증권상품 소개
canonicalUrl: https://open.krx.co.kr/contents/OPN/01/01030100/OPN01030100.jsp
canonicalUrlSha256: 7a6fc5162abd15dab83db18e24f7442a000c0d619a49f20ca38e15faa773777a
evidenceContentSha256: 4485a8ebae04677de0e5a8fe759a40137ca46d4608c67ac66d65880263964ca5
upstreamSourceIds:
- src_krx_etf_etn_structure_001
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
- 개별 상품의 현재 credit quality나 투자 적합성을 산출하지 않는다.
allowedUses:
- sanitized offline retrieval citation과 경계 설명
- ETF와 ETN 구조 차이 설명
forbiddenInferences:
- ETN을 ETF fund 지분과 같은 구조로 표현하지 않는다.
representativeQuestions:
- ETF와 ETN을 같은 법적·credit 구조로 설명해도 되나요?
---
# Source Card: KRX ETF fund와 ETN issuer-credit 구조 구분

## 핵심 claim
ETF fund와 ETN issuer-credit structure를 혼동하지 않는다.

## 적용 범위와 전제
KRX 공식 상품 구조 설명에서 fund와 issuer note의 차이에 적용한다.

## 프로젝트 적용
ETF와 ETN을 같은 법적·credit 구조로 설명해도 되나요?
상품 설명에 vehicle type, issuer 또는 fund structure와 credit-risk boundary를 표시한다.

## 한계와 반례
- 개별 상품의 현재 credit quality나 투자 적합성을 산출하지 않는다.

## 허용 사용
- sanitized offline retrieval citation과 경계 설명
- ETF와 ETN 구조 차이 설명

## 금지 추론
- ETN을 ETF fund 지분과 같은 구조로 표현하지 않는다.

## 근거 위치
front matter의 primary/official canonicalUrl, locator SHA-256과 bounded evidenceContentSha256을 확인한다.
