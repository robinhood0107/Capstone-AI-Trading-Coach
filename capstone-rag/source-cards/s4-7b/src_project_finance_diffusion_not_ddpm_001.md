---
schemaVersion: '2'
cardVariant: SCHOLARLY_PRIMARY_CARD
sourceId: src_project_finance_diffusion_not_ddpm_001
cardId: card_finance_diffusion_not_ddpm_001
title: 금융 SDE diffusion과 learned DDPM의 구분
institution: neurips
topic: finance_diffusion_not_ddpm
sourceType: PROJECT_SOURCE_CARD
tier: PROJECT
accessLevel: PUBLIC
claim: 금융 SDE diffusion과 learned DDPM은 수학적 연결이 있어도 같은 알고리즘·목적함수가 아니다.
evidenceClass: PRIMARY_RESEARCH
status: VERIFIED
verifiedAt: '2026-07-31T00:00:00Z'
accessNote: primary DOI metadata 또는 공식 institution page의 bounded locator와 claim-supporting metadata만 읽기 전용으로 확인했다.
licenseNote: 서지 metadata, official locator와 project-authored bounded claim만 사용하며 원문 PDF, HTML, screenshot 또는 provider payload는 corpus에 복제하지 않는다.
attribution: Jonathan Ho, Ajay Jain and Pieter Abbeel, NeurIPS 2020
canonicalUrl: https://proceedings.neurips.cc/paper/2020/hash/4c5bcfec8584af0d967f1ab10179ca4b-Abstract.html
canonicalUrlSha256: 1959861d22f46873d1106f6c078995bf9c944bc29e502130ec7fd9d3d9086130
evidenceContentSha256: c5e93b4421657b0b3f645c763b0b764190baf88b735772986288fe0e33bf339d
upstreamSourceIds: []
retentionOwner: python-rag-corpus-privacy
retentionDays: 365
contentClass: PROJECT_AUTHORED_SANITIZED_CARD
externalProcessingAllowed: false
externalProcessingGate: NOT_GRANTED
adoptedSession: S4.7B
contradicts: []
modelSensitive: true
modelAssumptions:
- key: FINANCE_DIFFUSION_NOT_DDPM
  statement: 금융 SDE와 learned DDPM은 각각의 state dynamics와 training objective로 식별한다.
limitations:
- 수학적 연결 가능성 자체를 부정하지 않으며 구현 equivalence만 금지한다.
allowedUses:
- sanitized offline retrieval citation과 경계 설명
forbiddenInferences:
- diffusion이라는 단어만으로 SDE와 DDPM의 목적함수와 algorithm을 동일시하지 않는다.
representativeQuestions:
- 금융 SDE의 diffusion과 DDPM을 같은 모델로 불러도 되나요?
bibliographicLocator:
  authorityType: OFFICIAL_INSTITUTION
  locatorType: OFFICIAL_URL
  value: https://proceedings.neurips.cc/paper/2020/hash/4c5bcfec8584af0d967f1ab10179ca4b-Abstract.html
bibliographicMetadata:
  authors:
  - Jonathan Ho
  - Ajay Jain
  - Pieter Abbeel
  editionOrVersion: Volume 33, NeurIPS 2020
  title: Denoising Diffusion Probabilistic Models
  venue: Advances in Neural Information Processing Systems
  year: 2020
---
# Source Card: 금융 SDE diffusion과 learned DDPM의 구분

## 핵심 claim
금융 SDE diffusion과 learned DDPM은 수학적 연결이 있어도 같은 알고리즘·목적함수가 아니다.

## 적용 범위와 전제
Ho 등의 learned reverse diffusion objective와 금융 SDE terminology 구분에 적용한다.

## 프로젝트 적용
금융 SDE의 diffusion과 DDPM을 같은 모델로 불러도 되나요?
설계서에 stochastic process, training objective와 sampler를 각각 명시한다.

## 한계와 반례
- 수학적 연결 가능성 자체를 부정하지 않으며 구현 equivalence만 금지한다.

## 허용 사용
- sanitized offline retrieval citation과 경계 설명

## 금지 추론
- diffusion이라는 단어만으로 SDE와 DDPM의 목적함수와 algorithm을 동일시하지 않는다.

## 근거 위치
front matter의 primary/official canonicalUrl, locator SHA-256과 bounded evidenceContentSha256을 확인한다.
