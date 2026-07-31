---
schemaVersion: '2'
cardVariant: OFFICIAL_UPSTREAM_CARD
sourceId: src_project_naver_news_discovery_boundary_001
cardId: card_naver_news_discovery_boundary_001
title: Naver News Search discovery와 원문 authority 경계
institution: naver
topic: naver_news_discovery_boundary
sourceType: PROJECT_SOURCE_CARD
tier: PROJECT
accessLevel: PUBLIC
claim: search metadata는 discovery/reference-only이며 원문 authority·영속 corpus가 아니다.
evidenceClass: OFFICIAL_API_DOCUMENTATION
status: VERIFIED
verifiedAt: '2026-07-31T00:00:00Z'
accessNote: primary DOI metadata 또는 공식 institution page의 bounded locator와 claim-supporting metadata만 읽기 전용으로 확인했다.
licenseNote: 서지 metadata, official locator와 project-authored bounded claim만 사용하며 원문 PDF, HTML, screenshot 또는 provider payload는 corpus에 복제하지 않는다.
attribution: 네이버 개발자센터 공식 뉴스 검색 API 문서와 legacy 종료 공지
canonicalUrl: https://developers.naver.com/docs/serviceapi/search/news/news.md
canonicalUrlSha256: dd013514bbda02d6f2d731ec39f5928f2b4fc9c3cf558671a33590898597ff48
evidenceContentSha256: 5db971a300c702643c4efd3d8e6dd833916c7d175a763a211352ed87f53c0651
upstreamSourceIds:
- src_naver_news_search_001
- src_naver_legacy_sunset_001
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
- current Search API 지원은 legacy API 지속을 의미하지 않는다.
- 기사 본문 fetch, 재배포 또는 provider 전송 권한을 만들지 않는다.
allowedUses:
- sanitized offline retrieval citation과 경계 설명
- 뉴스 locator discovery 경계 설명
forbiddenInferences:
- 검색 metadata를 기사 원문 또는 영속 content license로 해석하지 않는다.
representativeQuestions:
- Naver Search API metadata를 기사 원문 corpus로 저장해도 되나요?
---
# Source Card: Naver News Search discovery와 원문 authority 경계

## 핵심 claim
search metadata는 discovery/reference-only이며 원문 authority·영속 corpus가 아니다.

## 적용 범위와 전제
current Search API의 discovery metadata 범위와 legacy API sunset limitation을 서로 분리해 적용한다.

## 프로젝트 적용
Naver Search API metadata를 기사 원문 corpus로 저장해도 되나요?
검색 결과는 locator discovery로만 쓰고 기사 원문 authority와 retention을 별도 검증한다.

## 한계와 반례
- current Search API 지원은 legacy API 지속을 의미하지 않는다.
- 기사 본문 fetch, 재배포 또는 provider 전송 권한을 만들지 않는다.

## 허용 사용
- sanitized offline retrieval citation과 경계 설명
- 뉴스 locator discovery 경계 설명

## 금지 추론
- 검색 metadata를 기사 원문 또는 영속 content license로 해석하지 않는다.

## 근거 위치
front matter의 primary/official canonicalUrl, locator SHA-256과 bounded evidenceContentSha256을 확인한다.
