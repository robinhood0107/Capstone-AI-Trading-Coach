# P1 API usage matrix V4 addendum

기존 `P1_API_USAGE_MATRIX.md` 69개와 V3 addendum 6개 행은 역사적 회귀로 보존한다. 아래
confidence-free Signal v3 한 operation을 더한 현재 root는 exact-76이며 current
`p1-team-a-acceptance.v4` exact-45의 `Team A 필수`다.

| 번호 | Method | Path | 분류 | 용도 |
|---:|---|---|---|---|
| 76 | GET | `/api/v3/signals/{symbol}` | `Team A 필수` | confidence 없이 current exact-31 Rule+LSTM 신호와 ABSTAIN 상태를 읽는다 |
