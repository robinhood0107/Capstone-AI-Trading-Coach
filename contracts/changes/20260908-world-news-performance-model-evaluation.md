# 2026-09-08 세계 뉴스·누적 성과·모델 평가 후속 변경

## 승인된 목적

- 세계 뉴스는 조회와 RAG부터 도입하고 종목 VETO 권한은 주지 않는다.
- 공시 evidence 판단은 기존 Strong LLM 설정·예산·usage ledger를 재사용한다.
- 누적 성과는 재계산 백테스트, 고정 예측 실현, 실제 운용 손익을 분리한다.
- 모델 후보는 예측 오차와 비용 반영 성과를 모두 통과해야 shadow로 간다.

## additive surface

- `GET /api/v2/rag/world-news`
- `GET /api/v1/dashboard/performance-reports/latest`
- V157 `world_news_*_v2`: document/version/observation/collection append-only 저장과 RAG lexical channel.
- V158 `owner_performance_report_generations`: source generation별 성공/실패/correction lineage.

기존 V49 foreign-news aggregate, immutable RAG v2 bundle, Decision/Signal/Risk/Order hash와
historical OpenAPI bytes는 변경하지 않는다. `p1-world-news-v2.previous.openapi.json`은 변경 직전
root를 보존하고 generator가 두 operation과 schema/positive/negative fixture를 더한다.

## 권리·시간·실패 경계

- `publishedAt`이 없으면 null + `MISSING`; UI는 `발행일 미확인`과 `firstSeenAt`을 표시한다.
- 최신 `providerObservedAt`, 최초 `firstSeenAt`, 소비 가능 `availableAt`은 분리한다.
- Finnhub personal-local은 `externalLlmAllowed=false`다.
- raw provider body/header, 기사 원문, credential은 저장하지 않는다.
- 같은 URL/content version은 문서를 다시 만들지 않고 observation만 append한다.
- 수집 empty/partial/failure/not-collected를 서로 다른 상태로 보존한다.
- 세계 뉴스 RAG citation은 `availableAt` 이후 local retrieval에만 합류하며 종목 VETO 권한은 0이다.

## AI·성과·채택

- V3 NEWS_SCREENING은 저장 공시를 deterministic projection하고 provider call은 0이다.
- evidence가 있을 때만 기존 Spring Strong LLM JUDGE가 설정 hash, 원칙 version, 후보/evidence hash,
  operation과 run에 결속해 판단한다. Python 전용 NEWS_CHECKING Vertex 호출은 V3에서 재사용하지 않는다.
- source generation 변경 뒤 기존 automation daemon이 scenario materializer를 재호출한다. 같은 입력은
  NO_OP이고 실패는 마지막 성공 report를 보존한다.
- 현재 재평가에서 dual-acceptance 후보는 0이므로 `EQUAL_WEIGHT_50_50`을 유지하고 production pointer는
  바꾸지 않는다. 20-session 후보는 성숙 전 `BLOCKED_FORWARD_EVIDENCE_NOT_MATURE`다.

외부 provider, 계좌, 주문 호출과 자동 production activation은 이 변경의 권한이 아니다.
