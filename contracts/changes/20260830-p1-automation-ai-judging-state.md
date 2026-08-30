# P1 automation AI judging state

## KR

후보 선정 앞에 `AI_JUDGING` 상태를 하나 넣고, Strong LLM의 판단이 매수 결정에 실제로 반영되게
한다. 권한 범위는 `STRONG_LLM_JUDGEMENT_AUTHORITY=CANDIDATE_RANK_VETO_SIZE_ONLY`가 고정한다.

- 후보 집합의 소유자는 여전히 Return Engine이다. 모델은 그 집합 안에서만 답하고, 집합 밖
  종목은 순위에도 주문에도 닿지 못한다.
- 모델이 내는 것은 후보별 `score`·`veto`·`reason`과 전체 `confidence`뿐이다. 재순위·차단·수량은
  엔진이 정수 산술로 계산하므로 같은 판단에 같은 주문이 나온다.
- 수량은 줄기만 한다. 확신도 0이어도 절반까지이고 1주 아래로는 내려가지 않는다. 0으로 만드는
  것은 축소가 아니라 거부권이고, 거부권은 `veto`가 따로 표현한다.
- 1차·2차 provider가 모두 답하지 못하거나 붙어 있지 않으면 `AI_NOT_PARTICIPATED`를 남기고 기존
  규칙만으로 진행한다. AI가 없다고 그날의 자동매매가 멈추지 않는다.
- 전이 표는 `app/p1_owner/automation.py::_LEGAL_TRANSITIONS`와 V106의
  `p1_automation_transition_valid_v2`를 함께 바꾼다. `PRECHECK -> BUY_CANDIDATE_SELECTED` 직행은
  남겨 둔다. 이 변경 전에 그 상태로 checkpoint된 run이 재개될 수 있어야 한다.
- 판단 기록은 `automation_ai_judgements`에 남는다. checkpoint CAS 함수에 넣지 않는다. 그 함수는
  31개 인자를 받는 매매 전이 경로이고, 기록이 늘 때마다 그것을 다시 쓰면 판단 기록 스키마가
  주문 전이의 원자성을 흔든다. `(run_id,checkpoint_version)` upsert라 재생에도 수렴한다.
- 확신도는 basis point 정수로만 저장하고 오간다. 판단 tick과 사이징 tick이 서로 달라 저장을
  거쳐야 하는데, 부동소수로 저장하면 같은 판단이 왕복에서 달라져 수량을 재현할 수 없다.
- 자동운용은 같은 컨테이너의 loopback S4.9 agent에 직접 묻는다. RAG 설명 경로가 Kotlin host를
  거치는 것은 Kotlin이 근거·인용·사용량 원장을 소유하기 때문이고, 판단 경로에는 그 셋이 없다.

`automation-run.v2`의 상태 열거에 `AI_JUDGING`을 더한다. 그 밖의 필드는 더하지 않는다 —
root OpenAPI의 68→61 투영은 RAG v2 스키마만 걷어내므로 `AutomationRunV2`는 exact-61 해시 안에
남아 있고, 필드 하나만 더해도 동결 사슬이 깨진다. 판단 표면은 새 승인 additive set에서 연다.

DB DML, provider/account/order 호출, KIS Live 호출은 0이다. Strong LLM이 붙지 않은 배포에서는
동작이 이전과 같고 기록만 `NOT_PARTICIPATED`로 남는다.

## EN

Adds one `AI_JUDGING` state before buy-candidate selection so the Strong LLM's judgement reaches the
order decision within a fixed authority: re-rank, veto, and shrink-only sizing inside the policy cap.
The model returns scores and vetoes; the engine computes rank, block, and quantity with integer
arithmetic, so the same judgement yields the same order. When no provider answers, the run records
`AI_NOT_PARTICIPATED` and continues on the deterministic rules alone. The engine transition table and
the V106 database whitelist move together, and the judgement record lives in its own table rather than
in the 31-argument checkpoint CAS path. The root OpenAPI surface gains only the state enum value.
