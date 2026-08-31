# P1 장외 전수 replay 운영 규약

## 목적

장이 닫혀 있어도 과거 정규 OHLC 전량과 그 가격/tick을 anchor로 만든 합성
변환을 사용해 계산·상태기·계약의 모든 분기를 확인한다. 합성 fill이나 가상
세션의 결과는 실제 손익, 실제 유동성, 실제 장중 soak 증거가 아니다.

## 격리 실행

```bash
P1_PROJECT_NAME=capstone-p1-after-hours-replay \
P1_STATE_DIR=deploy/p1/.state-after-hours-replay \
./capstone test after-hours-replay --manifest-sha256 <accepted-manifest-sha256>
```

명령은 다음 조건을 모두 강제한다.

- Compose project 이름은 정확히 `capstone-p1-after-hours-replay`다.
- DB 역할은 `decision_replay`이며 base table SELECT 대신 V112의 bounded definer만
  호출한다.
- provider, account, balance, order endpoint는 호출하지 않는다.
- 결과는 state 아래 ignored `artifacts/after-hours-replay/<sha>/report.json`에만
  쓴다.
- 추적된 8월 31일 KIS Mock 왕복 1건, 청산 4종, AI rerank 3건은
  `p1-after-hours-observed-anchors.v1`의 exact SHA-256과 모두 일치해야 하며
  payload 대신 category/count/set hash만 replay report에 남긴다.
- 같은 입력을 메모리에서 두 번 replay한 canonical report가 byte-identical해야
  결과를 게시한다.

## 전량 회계

```text
inputRowCount = acceptedRowCount + rejectedRowCount
rejectedRowCount = sum(rejectedByReason)
unexplainedRows = 0
```

typed rejection에는 future bar, invalid XKRX session, duplicate conflict,
identical duplicate, middle-session gap, invalid OHLC, invalid volume, invalid symbol이 포함된다.
sampling은 없다.

`symbolCount=31`, typed rejection 0, ATR usable일 때만
`historicalExact31Status=PASS`다. 같은 조건에서 270종목일 때만
그리고 distinct XKRX session이 exact-1,072일 때만
`historicalUnion270Status=PASS`다. 다른 corpus나 입력 부재는 해당 gate를
PASS로 올리지 않으며 synthetic PASS와 합치지 않는다.

## 합성 matrix

input manifest SHA-256에서 고정 seed를 만들고, 그 seed로 고른 실제 검증 가능
row의 가격 규모와 XKRX 세션을 anchor로 정책 3종과
최소·최대·invalid 단위, unlimited holding/fill, Wilder update, monotonic
peak/stop, 진입 전 고가 격리, 다섯 exit priority 조합, eligibility unknown,
evidence 0, candidate-only prompt injection, veto 후보에만 근거가 남은 경우의
judge 0회, AI provider failure, duplicate conflict와 middle gap을 실제 커널과
상태기에 넣는다. 상수 `true`로 PASS를 만들지 않는다. 미래 근거와 unsupported
veto의 0.5/false 강등은 같은 최종 검증 묶음의 Spring host-validator 테스트가
담당한다. raw OHLC나 provider 원문은 report에 기록하지 않는다.
