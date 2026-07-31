---
schemaVersion: '2'
cardVariant: OFFICIAL_UPSTREAM_CARD
sourceId: src_project_opendart_financial_statement_scope_001
cardId: card_opendart_financial_statement_scope_001
title: OpenDART 재무제표 endpoint와 account scope
institution: opendart
topic: opendart_financial_statement_scope
sourceType: PROJECT_SOURCE_CARD
tier: PROJECT
accessLevel: PUBLIC
claim: statement endpoint·period/account scope와 status semantics를 보존한다.
evidenceClass: OFFICIAL_API_DOCUMENTATION
status: VERIFIED
verifiedAt: '2026-07-31T00:00:00Z'
accessNote: primary DOI metadata 또는 공식 institution page의 bounded locator와 claim-supporting metadata만 읽기 전용으로 확인했다.
licenseNote: 서지 metadata, official locator와 project-authored bounded claim만 사용하며 원문 PDF, HTML, screenshot 또는 provider payload는 corpus에 복제하지 않는다.
attribution: 금융감독원 OpenDART 공식 단일회사 주요계정 개발가이드
canonicalUrl: https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS003&apiId=2019016
canonicalUrlSha256: 14663cca3467b0a678bb8c2af021626ed348fc687ce9a81cc2be085dcc294a35
evidenceContentSha256: 61ca74ac36ab9a9db073afc39fdd54ee671fd860043bd1a70c87d7b2523133bc
upstreamSourceIds:
- src_opendart_financial_statement_001
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
- 주요계정 endpoint가 전체 주석과 모든 연결 범위를 대신하지 않는다.
allowedUses:
- sanitized offline retrieval citation과 경계 설명
- financial statement scope provenance 설명
forbiddenInferences:
- 서로 다른 period, report code 또는 account scope를 같은 series로 합치지 않는다.
representativeQuestions:
- OpenDART 주요계정 값을 비교할 때 어떤 scope를 고정해야 하나요?
---
# Source Card: OpenDART 재무제표 endpoint와 account scope

## 핵심 claim
statement endpoint·period/account scope와 status semantics를 보존한다.

## 적용 범위와 전제
공식 statement endpoint의 corporation, business year, report code와 account scope에 적용한다.

## 프로젝트 적용
OpenDART 주요계정 값을 비교할 때 어떤 scope를 고정해야 하나요?
snapshot에 endpoint, period, report code, account identifier와 status를 함께 보존한다.

## 한계와 반례
- 주요계정 endpoint가 전체 주석과 모든 연결 범위를 대신하지 않는다.

## 허용 사용
- sanitized offline retrieval citation과 경계 설명
- financial statement scope provenance 설명

## 금지 추론
- 서로 다른 period, report code 또는 account scope를 같은 series로 합치지 않는다.

## 근거 위치
front matter의 primary/official canonicalUrl, locator SHA-256과 bounded evidenceContentSha256을 확인한다.
