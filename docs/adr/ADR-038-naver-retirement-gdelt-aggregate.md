# ADR-038: Naver active 뉴스 경계 퇴역과 GDELT aggregate-only 도입

- 상태: Accepted
- 결정일: 2026-07-31
- 관련 Issue: #20
- supersedes: S1.3 Naver active provider/runtime/storage authority

## Context

기존 S1.3은 Naver Search 결과의 제목·설명·URL·발행시각을 최대 30일 보존하는 내부
snapshot을 만들었다. 이 이력과 당시 감사 evidence는 재현 가능한 과거 기록이지만, 신규
application 정책과 뉴스 metadata 영속화의 이용 조건을 고려하면 active 뉴스 입력으로 계속
운영하지 않는다. 프로젝트가 직접 작성한 Naver discovery boundary source card는 provider
결과가 아니므로 RAG exact-30 corpus 안에 그대로 보존한다.

GDELT는 Naver 검색 결과의 대체재가 아니다. P1에서 필요한 범위는 `TimelineTone`과
`TimelineVolRaw`로부터 얻은 bounded numeric aggregate이며, 기사 discovery·본문·제목·URL·
publisher archive를 포함하지 않는다.

## Decision

1. `NAVER_ACTIVE_PROVIDER_RUNTIME_STORAGE=RETIRED`로 고정한다. 과거 contract-change와
   audit 설명은 `HISTORICAL_SUPERSEDED`로 보존하고 이 ADR을 active authority로 가리킨다.
2. Decision Platform이 provider-neutral `gdelt_news_tone_observation.v1` 생산 경계를
   소유한다. 초기 transport는 synthetic fixture 전용이며 실제 GDELT outbound는 별도 승인
   packet 전까지 0회다.
3. GDELT artifact에는 `The GDELT Project`, 프로젝트 URL, 공식 About/Terms URL을 항상
   기록한다. 기사 metadata와 raw provider payload는 저장하지 않는다.
4. 품질이 불완전하거나 source가 없으면 수치 0을 만들지 않고 `ABSTAIN`한다.
   `AVAILABLE`은 완전한 aggregate와 유한 수치만 허용한다.
5. `news_sentiment_summary.v2`는 Decision Platform producer가 만드는 설명용 계약이다.
   Return Engine은 별도 cross-workspace 구현 뒤 public artifact만 소비할 수 있다.
6. 두 계약은 `decisionAuthority=NONE`, `riskDecisionHashIncluded=false`,
   `s5FeatureEligible=false`다. RiskDecision·판단 hash·주문을 변경하지 않고, 별도 조건부
   feature group gate 전에는 S5 입력으로 주입하지 않는다.
7. Naver runtime·credential·active schema/test 제거와 승인된 로컬 snapshot의 exact 삭제는
   이 계약 병합 뒤 구현 wave에서 수행한다. 기존 V16~V20 migration과 historical DB row는
   수정하지 않는다.
8. 이번 통합 작업의 Codex Security full-repository scan은 모든 offline 구현과 일반 gate
   완료 뒤 한 번의 consolidated campaign으로 실행한다. 계약 CI·repo hygiene·gitleaks는
   각 wave에서 계속 실행한다.

## Consequences

- Naver 결과를 GDELT, RAG, 외부 LLM, S4.8, Return Engine으로 전달하는 active 경로는 없다.
- GDELT failure는 neutral signal이 아니며 downstream은 `ABSTAIN`을 명시적으로 처리한다.
- exact-30 RAG corpus, active local BGE generation, ECOS 계약, KIS/Decision API는 이 결정으로
  변경되지 않는다.
- 외부 provider, model, account, order physical call은 모두 0을 유지한다.

## Contract pointers

- `contracts/changes/20260731-s1-3g-naver-retirement-gdelt-aggregate-lock.md`
- `contracts/schemas/gdelt_news_tone_observation.v1.schema.json`
- `contracts/schemas/news_sentiment_summary.v2.schema.json`
