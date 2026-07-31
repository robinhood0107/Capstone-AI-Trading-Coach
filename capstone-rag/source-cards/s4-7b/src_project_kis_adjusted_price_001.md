---
schemaVersion: '2'
cardVariant: OFFICIAL_UPSTREAM_CARD
sourceId: src_project_kis_adjusted_price_001
cardId: card_kis_adjusted_price_001
title: KIS 기간별시세 조정주가 provenance
institution: kis
topic: kis_adjusted_price
sourceType: PROJECT_SOURCE_CARD
tier: PROJECT
accessLevel: PUBLIC
claim: KIS 국내주식 기간별시세를 수집할 때 FID_ORG_ADJ_PRC의 0(수정주가)·1(원주가) 선택값을 시계열 provenance에 기록한다.
evidenceClass: OFFICIAL_API_DOCUMENTATION
status: VERIFIED
verifiedAt: '2026-07-30T05:07:41Z'
accessNote: 공식 GitHub의 commit-pinned 국내주식 기간별시세 sample을 읽기 전용으로 확인했다.
licenseNote: 공식 sample 원문과 실행 예시는 corpus로 복제하지 않고 bounded evidence hash와 attribution만 보존한다.
attribution: 한국투자증권 Open Trading API 공식 GitHub sample
canonicalUrl: https://github.com/koreainvestment/open-trading-api/blob/b093e42ba32d1df5f5ddad7a71cb715cbc800832/examples_llm/domestic_stock/inquire_daily_itemchartprice/inquire_daily_itemchartprice.py
canonicalUrlSha256: d2ff26041b01ef258e2a43310f79293631c415f3339484df04887b21fac2ee74
evidenceContentSha256: dd8fab24d99f359eb7e983b40e37db54187967bbd3d498c169f432f885ac2d3d
upstreamSourceIds:
- src_kis_marketdata_daily_001
retentionOwner: python-rag-corpus-privacy
retentionDays: 365
contentClass: PROJECT_AUTHORED_SANITIZED_CARD
externalProcessingAllowed: false
externalProcessingGate: NOT_GRANTED
adoptedSession: S4.7A
contradicts: []
modelSensitive: false
modelAssumptions: []
limitations:
- 확인 범위는 pinned sample의 endpoint와 field 의미에 한정한다.
- 미래 API 변경이나 실제 응답 값은 이 카드가 보장하지 않는다.
allowedUses:
- 일봉 시계열의 조정주가 선택 provenance 설명
- sanitized offline retrieval citation과 경계 설명
forbiddenInferences:
- 현재가, 미래 수익률, 매수·매도 판단을 추론하지 않는다.
- sample의 credential 또는 실행 예시를 채택하지 않는다.
representativeQuestions:
- KIS 기간별시세에서 조정주가 선택값은 provenance에 어떻게 기록하나요?
---
# Source Card: KIS 기간별시세 조정주가 provenance

## 핵심 claim
KIS 국내주식 기간별시세를 수집할 때 FID_ORG_ADJ_PRC의 0(수정주가)·1(원주가) 선택값을 시계열 provenance에 기록한다.

## 적용 범위와 전제
commit-pinned 공식 sample의 기간별시세 endpoint와 FID_ORG_ADJ_PRC 의미에만 적용한다.

## 프로젝트 적용
KIS 기간별시세에서 조정주가 선택값은 provenance에 어떻게 기록하나요?
snapshot에는 실제 요청 선택값과 source revision을 함께 남긴다.

## 한계와 반례
- 확인 범위는 pinned sample의 endpoint와 field 의미에 한정한다.
- 미래 API 변경이나 실제 응답 값은 이 카드가 보장하지 않는다.

## 허용 사용
- 일봉 시계열의 조정주가 선택 provenance 설명
- sanitized offline retrieval citation과 경계 설명

## 금지 추론
- 현재가, 미래 수익률, 매수·매도 판단을 추론하지 않는다.
- sample의 credential 또는 실행 예시를 채택하지 않는다.

## 근거 위치
front matter의 primary/official canonicalUrl, locator SHA-256과 bounded evidenceContentSha256을 확인한다.
