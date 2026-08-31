# P1 Automation V3 근거·청산정책·시장데이터 계약 잠금

## 결정

기존 Automation V1/V2와 Team A v1/v2 bytes를 변경하지 않고 V3를 additive로 추가한다. V3는
후보 선택 전에 전수 근거 screening을 수행하고, 검증된 quote가 없는 score와 veto를 각각 `0.5`와
`false`로 강등한다. 후보가 전부 근거 0건이면 provider judge를 호출하지 않고 기존 규칙 순위를
사용한다.

청산 정책은 자본·고정 손절익절에 `maxHoldingSessions`, Wilder ATR period/multiplier,
`modelSellEnabled`를 더한다. `maxHoldingSessions=0`은 무제한이며 exit priority는
`STOP_LOSS -> ATR_TRAILING -> MODEL_SELL -> TAKE_PROFIT -> MAX_HOLDING_SESSIONS`다.

## API

V3는 다음 여섯 operation만 추가한다.

```text
GET  /api/v3/automation/status
PUT  /api/v3/automation/policy
POST /api/v3/automation/arm
GET  /api/v3/automation/runs
GET  /api/v3/automation/runs/{runId}
GET  /api/v3/automation/positions
```

runtime 구현 뒤 root OpenAPI는 exact-69에서 exact-75로 전환한다. 기존 V1 disarm이 계속 신규 주문
중지 표면이며 V3 disarm은 추가하지 않는다. contract-only 단계의 current root는 exact-69로 유지하고
`p1-automation-v3.v1.openapi.json` overlay로 exact-six만 잠근다.

## 시장데이터

기존 sealed `market-data-seed.v1`이 있으면 provider-free adoption을 우선한다. 없으면
`p1-automation-market-bootstrap.v1`이 current exact-31과 최대 1,260 XKRX 세션의 adjusted KIS
OHLCV를 normalized archive로 만든다. 최악 상한은 KIS daily 403, token 1, KRX membership 5,
retry/account/order 0이다. bootstrap 계약은 성과 주장을 허용하지 않고 raw response와 source path를
저장하지 않는다.

## 권한과 활성화

- V3 기본 control은 `DISARMED`, AI judgement 기본값은 `false`다.
- runtime은 base `market_data_bars` SELECT를 받지 않고 bounded definer function만 사용한다.
- Google Search, Vertex, KIS read-only, KIS Mock 물리 호출은 이 contract-only 변경에 포함되지 않는다.
- KIS Live 주문, GDELT outbound, SearXNG, 자동 INTERNAL_PAPER fallback은 0이다.
- synthetic/history replay는 실제 장중 KIS Mock 또는 실제 3세션 soak를 대체하지 않는다.

```text
P1_AUTOMATION_V3=CONTRACT_LOCKED_RUNTIME_NOT_IMPLEMENTED
ROOT_OPENAPI_CURRENT=69
ROOT_OPENAPI_TARGET=75
TEAM_A_V1=EXACT_33_PRESERVED
TEAM_A_V2=EXACT_38_PRESERVED
TEAM_A_V3_TARGET=EXACT_45
PROVIDER_PHYSICAL_CALLS=0
KIS_LIVE_ORDER_CALLS=0
GDELT_OUTBOUND_CALLS=0
CODEX_SECURITY_DEEP_SCAN=NOT_RUN_USER_SCOPED_OUT
```
