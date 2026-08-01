---
schemaVersion: '2'
cardVariant: SCHOLARLY_PRIMARY_CARD
sourceId: src_project_hmm_latent_state_boundary_001
cardId: card_hmm_latent_state_boundary_001
title: HMM latent state의 인식론적 경계
institution: ieee
topic: hmm_latent_state_boundary
sourceType: PROJECT_SOURCE_CARD
tier: PROJECT
accessLevel: PUBLIC
claim: HMM state는 관측모형이 추정한 latent label이지 시장 원인의 사실 선언이 아니다.
evidenceClass: PRIMARY_RESEARCH
status: VERIFIED
verifiedAt: '2026-07-31T00:00:00Z'
accessNote: primary DOI metadata 또는 공식 institution page의 bounded locator와 claim-supporting metadata만 읽기 전용으로 확인했다.
licenseNote: approvalId=AUTH_EXTERNAL_PROCESSING_30_PROJECT_CARDS_20260731. project-authored sanitized claim과 public locator/attribution만 external processing 대상으로 검토했다. upstream PDF, HTML, screenshot, API response, provider payload, raw/reference evidence는 복제하거나 전송하지 않는다. 사용자 동의와 third-party restriction review를 각각 확인했다.
attribution: Lawrence R. Rabiner, Proceedings of the IEEE
canonicalUrl: https://doi.org/10.1109/5.18626
canonicalUrlSha256: 7f379966a45cdc9b090ac8a1d0af040b35f731826b7a9d8faf31db07efa23e17
evidenceContentSha256: c3c655169b3e3c9860b2f49237c154e3cf96aebbe682ed5b1695acf2c1086326
upstreamSourceIds: []
retentionOwner: python-rag-corpus-privacy
retentionDays: 365
contentClass: PROJECT_AUTHORED_SANITIZED_CARD
externalProcessingAllowed: true
externalProcessingGate: LICENSE_AND_CONSENT_VERIFIED
adoptedSession: S4.7B
contradicts: []
modelSensitive: true
modelAssumptions:
- key: HMM_STATE_NOT_CAUSAL_FACT
  statement: 관측모형이 추정한 latent label을 확인된 causal market fact로 바꾸지 않는다.
limitations:
- latent label은 경제 사건의 causal identification을 제공하지 않는다.
allowedUses:
- sanitized offline retrieval citation과 경계 설명
forbiddenInferences:
- HMM state 이름을 확인된 시장 원인이나 사건으로 선언하지 않는다.
representativeQuestions:
- HMM regime label을 시장 원인의 확정 사실로 말해도 되나요?
bibliographicLocator:
  authorityType: DOI_REGISTRY
  locatorType: DOI
  value: 10.1109/5.18626
bibliographicMetadata:
  authors:
  - Lawrence R. Rabiner
  editionOrVersion: Volume 77, Number 2, pages 257-286
  title: A tutorial on hidden Markov models and selected applications in speech recognition
  venue: Proceedings of the IEEE
  year: 1989
---
# Source Card: HMM latent state의 인식론적 경계

## 핵심 claim
HMM state는 관측모형이 추정한 latent label이지 시장 원인의 사실 선언이 아니다.

## 적용 범위와 전제
hidden state가 observation model과 transition assumptions 아래 추론되는 범위에 적용한다.

## 프로젝트 적용
HMM regime label을 시장 원인의 확정 사실로 말해도 되나요?
regime output에는 model version, posterior와 latent-label wording을 함께 보존한다.

## 한계와 반례
- latent label은 경제 사건의 causal identification을 제공하지 않는다.

## 허용 사용
- sanitized offline retrieval citation과 경계 설명

## 금지 추론
- HMM state 이름을 확인된 시장 원인이나 사건으로 선언하지 않는다.

## 근거 위치
front matter의 primary/official canonicalUrl, locator SHA-256과 bounded evidenceContentSha256을 확인한다.
