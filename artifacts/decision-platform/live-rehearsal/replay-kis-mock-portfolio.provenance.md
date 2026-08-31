# `replay-kis-mock-portfolio.v1.json` 출처

이 파일은 **재생본**이다. 실시간 관측이 아니다.

숫자는 2026-08-28 KIS 모의계좌에서 실제로 받아 기록한 값이다. 원본은 같은 디렉터리의
`closed-loop-000660-model-sell.json` 등이며, 그 안의 `preBalance` / `postBalance` 단계가 출처다.

```json
{"step": "preBalance",  "cashKrw": 100000000, "symbolQuantity": 0}
{"step": "postBalance", "cashKrw": 100000000, "symbolQuantity": 0}
```

`observedAt`은 원본 트레이스의 `sessionKst`(2026-08-28T14:44:38+09:00 = 05:44:38Z)를 그대로 옮겼다.
즉 **관측 시각도 실제 시각이며 지금 시각으로 바꾸지 않았다.**

| 필드 | 값 | 근거 |
|---|---|---|
| `cashKrw` | 100,000,000 | KIS 잔고조회 `dnca_tot_amt` 실측 |
| `portfolioEquityKrw` | 100,000,000 | 보유 0이므로 예수금과 같다 |
| `marginRequirementKrw` | 0 | 모의 **현금**계좌라 신용·대주 한도가 없다. 계좌 유형에서 나오는 사실이며 payload 추정이 아니다 |
| `positions` | `[]` | 왕복 매매가 끝나 보유수량이 0으로 복귀한 상태 |
| `ownerScopeHash` | `aaaa…` 접두 | `automation_control.account_id`가 `acct_` + 32×`a`라 `substr(account_id,6)` 접두 규칙에 맞춘 값이다 |

## 왜 재생했나

`p1_automation_risk_balance_projection_v2`(V91)가 arm 판정에서 `source_version='kis-mock-online-complete-v2'`
관측을 요구한다. 이 관측을 만드는 유일한 실시간 경로는 거래시간(09:10~15:00 KST)의 KIS 잔고조회인데,
배포 시점이 장 마감 이후라 실행할 수 없었다.

그래서 **같은 날 실제로 받은 잔고를 그대로 재생**해 파이프라인을 끝까지 태웠다. 값을 지어내지 않았고,
시각을 현재로 위조하지 않았다.

## 이 재생본이 만들지 않는 것

`BLOCKED_INCOMPLETE_RISK_BALANCE` 하나만 닫는다. arm은 여전히 열리지 않는다 —
`REAL_TEAM_B_POINTER_INACTIVE`(외부 팀 산출물)와 `CERTIFICATION_INVALID`(거래시간 재인증)가 남는다.
자동 주문은 이 재생본으로 발생하지 않는다.
