---
schemaVersion: '2'
cardVariant: OFFICIAL_UPSTREAM_CARD
sourceId: src_project_opendart_corporation_code_001
cardId: card_opendart_corporation_code_001
title: OpenDART 고유번호 join과 revision 경계
institution: opendart
topic: opendart_corporation_code
sourceType: PROJECT_SOURCE_CARD
tier: PROJECT
accessLevel: PUBLIC
claim: corp code join·revision/as-of 경계를 보존한다.
evidenceClass: OFFICIAL_API_DOCUMENTATION
status: VERIFIED
verifiedAt: '2026-07-31T00:00:00Z'
accessNote: primary DOI metadata 또는 공식 institution page의 bounded locator와 claim-supporting metadata만 읽기 전용으로 확인했다.
licenseNote: 서지 metadata, official locator와 project-authored bounded claim만 사용하며 원문 PDF, HTML, screenshot 또는 provider payload는 corpus에 복제하지 않는다.
attribution: 금융감독원 OpenDART 공식 고유번호 개발가이드
canonicalUrl: https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019018
canonicalUrlSha256: c42aef7d71b180d36a104a2cf6b2dc848c790c37fe5083d86effd1edd1b76cd4
evidenceContentSha256: 56e671fae63861c582a900d25adebdba2df874bc35e547705ab1e1fd46d47f2c
upstreamSourceIds:
- src_opendart_corporation_code_001
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
- 회사명만으로 영구 동일 identity를 보장하지 않는다.
allowedUses:
- sanitized offline retrieval citation과 경계 설명
- corp code join provenance 설명
forbiddenInferences:
- 현재 mapping을 모든 과거 시점의 identity로 소급하지 않는다.
representativeQuestions:
- OpenDART corp code mapping에는 어떤 revision 경계가 필요한가요?
---
# Source Card: OpenDART 고유번호 join과 revision 경계

## 핵심 claim
corp code join·revision/as-of 경계를 보존한다.

## 적용 범위와 전제
공식 고유번호 source와 기업 identifier join에 적용한다.

## 프로젝트 적용
OpenDART corp code mapping에는 어떤 revision 경계가 필요한가요?
mapping snapshot에 corp code, source revision, retrievedAt와 valid-as-of를 기록한다.

## 한계와 반례
- 회사명만으로 영구 동일 identity를 보장하지 않는다.

## 허용 사용
- sanitized offline retrieval citation과 경계 설명
- corp code join provenance 설명

## 금지 추론
- 현재 mapping을 모든 과거 시점의 identity로 소급하지 않는다.

## 근거 위치
front matter의 primary/official canonicalUrl, locator SHA-256과 bounded evidenceContentSha256을 확인한다.
