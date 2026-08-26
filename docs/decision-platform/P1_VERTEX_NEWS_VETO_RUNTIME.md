# P1 Vertex 신규 BUY 뉴스 veto runtime

## 판정

```text
OWNER_VERTEX_NEWS_VETO_FIXTURE=IMPLEMENTED_MERGE_CANDIDATE
VERTEX_PHYSICAL_CALLS=0
SELL_VERTEX_CALLS=0
SECOND_CANDIDATE_FALLBACK=0
ORDER_AUTHORITY=NONE
VERTEX_LIVE=NOT_RUN
```

이 문서는 `vertex-news-veto.v1` 계약을 실행하는 Owner runtime 경계를 설명한다. fixture 결과는
실제 Vertex Google grounding 실행 증거가 아니며 `VERTEX_LIVE=PASS`를 만들지 않는다.

## 단일 후보 입력 경계

runtime은 canonical JSON object 하나만 받고 `candidate.action=NEW_BUY`를 강제한다. 후보 배열이나
SELL 요청을 받지 않으므로 하루의 final 신규 BUY 후보 하나를 검사한 뒤 2순위 후보로 이동할 수
없다. 입력은 다음 공개 필드만 포함한다.

- 종목코드, 공식 회사명, 현재·직전 XKRX session, 직전 종가
- 공개 evidence 최대 5개와 공개 timestamp
- prompt version, source-registry version, model ID

사용자·계좌·잔고·보유량·원칙 원문·주문 금액/수량·credential·owner-private text 필드는 닫힌
입력 object에 들어갈 수 없다.

## 검증 순서

1. request byte bound, canonical JSON, closed field, 단일 `NEW_BUY` candidate를 확인한다.
2. Unicode NFKC와 zero-width 제거 뒤 공개 evidence의 prompt injection을 provider 전에 차단한다.
3. 주입된 one-shot transport를 정확히 한 번 호출한다. Owner fixture transport의 physical call은 0이다.
4. host가 관측한 provider/query count와 model JSON의 count가 모두 정확히 1인지 확인한다.
5. host grounding support에 `boundedQuote`가 실제 substring으로 존재하는지 source ID별로 확인한다.
6. source event date가 현재 session보다 미래가 아니고 최근 7 calendar days 안인지 확인한다.
7. 공식 1차 출처 1개 또는 서로 다른 등록 독립 출처 2개, 상호 일관성과 closed schema를 확인한다.
8. input SHA와 model/prompt binding, `outputSha256` preimage를 확인한다. output preimage는
   `outputSha256` 필드만 제거한 AVAILABLE object의 canonical JSON bytes다.
9. `VETO_BUY`는 `NEGATIVE` tone과 negative event 1개 이상을 요구한다. `NO_VETO`는 negative
   event가 없어야 하며 RiskEngine 진행 가능성만 뜻한다.

모든 불확실성과 timeout, budget 소진, unknown field, model/packet drift, count 불일치는 typed
`ABSTAIN`으로 닫힌다. `VETO_BUY`와 `ABSTAIN`은 신규 BUY를 중단하고 `NO_VETO`도 주문 권한을
부여하지 않는다.

## fixture acceptance

deterministic fixture transport는 다음을 물리 호출 없이 검증한다.

- valid `VETO_BUY`, valid `NO_VETO`
- grounding 없음, stale/unknown-date evidence, 상충 출처, 독립 약한 출처 1개
- input과 grounded output의 prompt injection
- unknown field, invalid schema/hash, model packet drift
- timeout, budget exhausted, provider/query count mismatch
- malformed/non-canonical request의 provider-before fail-close

검증 명령:

```bash
cd workspaces/decision-platform/python-services
.venv/bin/ruff check app/p1_owner/vertex_veto.py tests/p1_owner/test_vertex_veto.py
.venv/bin/mypy app/p1_owner/vertex_veto.py
.venv/bin/pytest -q tests/p1_owner/test_vertex_veto.py
```

실제 credential, OAuth, Vertex SDK/HTTP transport와 물리 Google grounding은 이 runtime 파일에
없다. Team A/B 결과 병합 및 별도 exact approval 전에는 추가하거나 실행하지 않는다.
