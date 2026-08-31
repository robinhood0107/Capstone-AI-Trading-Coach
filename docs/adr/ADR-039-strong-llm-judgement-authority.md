# ADR-039: Strong LLM 판단을 자동매매 결정 입력으로 승격

- 상태: Accepted
- 결정일: 2026-08-30
- 구현 상태: 계획 승인, 구현 진행 중
- 관련 결정: ADR-038 (`decisionAuthority=NONE` 뉴스 경계는 그대로 유지)

## Context

이 프로젝트의 주제는 "AI 트레이딩 코치"인데, 지금까지 생성형 LLM은 매매 판단에 거의 관여하지
않았다. `docs/최종_프로젝트_명세서.md` 8.1이 열거한 최종 행동 결정의 입력 여덟 개에 LLM은 없고,
RAG는 "결과를 설명하고 출처를 연결하는 display-only 근거"로 명시적으로 제외돼 있었다. 여기서
"AI"라 불린 것은 LSTM·LightGBM 같은 수치 모델이었다.

LLM이 매매에 닿는 유일한 설계 경로는 Vertex 뉴스 거부권이었고, 그것도 신규 BUY 후보 하나에
대한 **차단 방향뿐**이었다. `contracts/schemas/vertex-news-veto.v1.schema.json`은 어느 분기에서도
`orderAuthority`를 `"NONE"` 외의 값으로 둘 수 없게 닫혀 있다.

동시에 LSTM 성능이 낮을 때 이 구조가 손해를 줄일 방법이 없었다. 후보 선정은
`app/p1_owner/automation.py`에서 `lstm_signal == baseline_signal == "BUY"` 2-of-2 합의를 요구해
LSTM 단독 매수는 이미 막혀 있지만, 후보가 여럿일 때 `expected_return` 내림차순 1등을 그대로
사는 구조라 **나쁜 1등을 피할 수단이 없었다.**

## Decision

1. **Strong LLM 판단을 결정 입력 아홉 번째로 승격한다.** 명세서 8.1에 추가하고
   `STRONG_LLM_JUDGEMENT_AUTHORITY=CANDIDATE_RANK_VETO_SIZE_ONLY`로 그 범위를 고정한다.

2. **권한은 정확히 셋이다.** 후보 순위 변경, 매수 차단, 정책 상한 안에서의 수량 축소.
   그 밖은 전부 금지다 — 후보 집합 밖 종목 추가, 수량 증가, 정책 상한 초과, 주문 직접 생성.

3. **모델은 숫자만 내고 배분은 코드가 한다.** 응답은 후보별 `score`·`veto`·`reason`과 전체
   `confidence`뿐이다. 순위는 `score`로, 차단은 `veto`로, 수량은 `confidence`로 결정론적으로
   계산한다. 같은 입력에 같은 배분이 나오고 그 계산은 감사 가능하다.

4. **후보 집합의 소유자는 여전히 Return Engine이다.** Strong LLM은 주어진 집합 안에서만
   움직인다. 이것이 Team B 산출물의 역할을 지키는 경계다.

5. **자동매매는 Strong LLM 없이도 완결된다.** provider 1·2차가 모두 실패하거나 하루 한도를
   소진하면 판단에 `AI_NOT_PARTICIPATED`를 남기고 기존 규칙만으로 진행한다. 이는 기존
   `news_veto_provider_bound` 조건부 동작(`automation.py`)을 정책 수준으로 일반화한 것이다.

6. **RAG 설명 경로의 권한은 바뀌지 않는다.** `RAG_DECISION_SIGNAL_ORDER_AUTHORITY=0`과
   `RAG_NEWS_ANALYST_DECISION_SIGNAL_ORDER_AUTHORITY=0`은 그대로 유지한다. 검색·설명은 여전히
   Signal feature·RiskDecision·주문 의도·판단 hash를 바꾸지 않는다. 승격된 것은 **판단
   컴포넌트**이지 RAG가 아니며, 둘은 다른 경로다.

7. **ADR-038의 뉴스 경계도 그대로다.** `gdelt_news_tone_observation.v1`과
   `news_sentiment_summary.v2`는 계속 `decisionAuthority=NONE`이다.

8. **Vertex 뉴스 거부권은 유지한다.** 후보 확정 뒤의 차단 경로로 남으며 스키마의
   `orderAuthority: const "NONE"`도 그대로다. Strong LLM 판단은 그보다 앞 단계에서 별개로
   동작한다.

9. **provider는 교체 가능해야 한다.** 판단은 특정 벤더에 묶이지 않는다. OpenAI·Anthropic·
   Google 무엇이든 같은 프롬프트와 같은 출력 계약을 통과해야 하고, 실패 시 2차로 넘어간다.

## Consequences

- 릴리스 원장 marker가 하나 늘어난다(`app/rag/pre_s5_final_gate.py`). 값이 정확히
  `CANDIDATE_RANK_VETO_SIZE_ONLY`가 아니면 릴리스가 닫힌다.
- 자동운용 상태기에 상태가 하나 늘어난다. Python 전이 화이트리스트와 DB 전이 함수가 한 글자라도
  어긋나면 CAS 충돌로 죽으므로 둘을 함께 바꿔야 한다.
- 판단 기록에 AI 참여 여부와 적용 전후 값이 남아야 한다. 남지 않으면 "AI가 무엇을 바꿨는지"를
  사후에 말할 수 없고, 그러면 이 승격이 검증 불가능해진다.
- 프롬프트가 판단에 영향을 주므로 프롬프트 변경이 곧 매매 동작 변경이다. 프롬프트 조각과 순서를
  계약 테스트로 고정하고 버전을 응답·원장에 남긴다.

## 대안과 기각 사유

- **거부권만 확장한다**: 변경이 가장 작지만 "AI가 판단한다"는 주제에 미치지 못하고, 나쁜 1등을
  피하는 문제를 풀지 못한다.
- **AI가 후보도 생성한다**: Team B 산출물의 역할이 사라지고 교육·모의 범위를 넘는다. 후보 집합
  소유권을 모델에 넘기면 이 시스템의 안전 논거 대부분이 무너진다.
- **모델이 최종 수량을 직접 낸다**: 단순하지만 정책 상한 초과를 검증으로만 막아야 하고,
  같은 입력에 같은 결과라는 성질을 잃는다.
