# AI 판단 경로

`tests/e2e/ai_judgement_e2e.py` · 증거 `artifacts/decision-platform/e2e/ai-judgement.json`

| 기능 | 방식 | 결과 |
|---|---|---|
| `AI_JUDGING` 상태가 배포에 실재한다 | stack | PASS — state CHECK 2/2, 전이 함수 1/1 |
| 엔진 전이 표와 DB 전이 함수가 같다 | stack | PASS — 엔진만 0, DB만 0 |
| 판단 기록이 무엇을 남기는가 | stack | PASS — 일곱 열 전부, 확신도는 `integer` |
| 판단 기록 쓰기는 definer 함수뿐이다 | stack | PASS — 표 권한 0, 함수 EXECUTE 2/2 |
| 수량은 줄기만 한다 | stack | PASS — DB 제약 1/1 |
| 설정 저장과 키 노출 | stack | PASS — 저장 204·본문 0, 노출은 마지막 네 글자, 평문 발견 0 |

## 왜 이 러너가 따로 필요한가

엔진 단위 테스트 20개(`tests/p1_owner/test_automation_ai_judgement.py`)는 판단이 순위와 수량을
바꾼다는 것을 보인다. 그러나 그 상태와 그 기록이 **배포된 DB에 실제로 있는지**는 말하지 못한다.

상태기 화이트리스트가 DB 전이 함수와 한 글자라도 어긋나면 tick이 CAS 충돌로 죽는다. 그 어긋남은
코드를 읽어서는 보이지 않고 돌려 봐야 보인다. 실제로 이 러너를 처음 돌렸을 때 비교 대상이 옛
정의(V93)였고, 파일 이름을 사전순으로 정렬해 V93이 V106보다 뒤에 온 탓이었다. 버전 순으로
고쳤다. 같은 함정이 `tests/p1_owner/test_automation_sql_alignment.py`에도 있어 함께 고쳤다.

## AI가 무엇을 바꾸고 무엇을 바꾸지 못하나

`STRONG_LLM_JUDGEMENT_AUTHORITY=CANDIDATE_RANK_VETO_SIZE_ONLY`가 경계다.

| 할 수 있는 것 | 어떻게 |
|---|---|
| 후보 순위 변경 | 후보별 `score`. 엔진이 basis point 정수로 다시 세운다 |
| 매수 차단 | 후보별 `veto`. 전부 차단되면 `SKIPPED_NO_ACTION` |
| 수량 축소 | 전체 `confidence`. 절반까지만, 1주 아래로는 안 간다 |

| 할 수 없는 것 | 무엇이 막는가 |
|---|---|
| 후보 집합 밖 종목 추가 | `_apply_judgement`가 집합 안에서만 고른다 |
| 수량 증가 | 코드가 `min`으로 자르고 DB 제약이 한 번 더 막는다 |
| 정책 상한 초과 | 축소는 `_variable_buy_quantity` 결과 안에서만 일어난다 |
| 주문 직접 생성 | 모델 응답에 수량·가격·주문 유형 자리가 없다 |

## AI 없이도 돈다

1차도 2차도 답하지 못하거나 provider가 붙어 있지 않으면 판단에 `AI_NOT_PARTICIPATED`를 남기고
기존 규칙만으로 진행한다. "물어본 적이 없다"와 "물었는데 답을 못 받았다"는 `judge_call_count`로
구분된다. AI를 붙이는 일이 곧 가용성을 낮추는 일이 되면 이 승격이 노린 것과 반대가 된다.

## 이 러너가 확인하지 않는 것

- **실제 provider 판단을 받아 오지 않는다.** 외부 호출이고 비용이다. 판단이 순위와 수량을 어떻게
  바꾸는지는 엔진 테스트 20개가 결정론적으로 덮는다. 여기서 보는 것은 그 경로가 배포에 실재하는지다.
- **AI가 1등을 바꾼 실제 run 기록이 아니다.** 그것은 자동운용이 실제로 도는 거래시간에만 생긴다.
  `P1_장시간_의존_항목.md`가 그 항목을 따로 다룬다.
- 실제 Team A UI가 아니다. 대시보드는 현재 tree의 화면이며 최종본이 아니다.
- 실계좌 거래가 아니다. KIS Live 호출은 영구 금지이고 이 기록 어디에도 그 경로가 없다.
