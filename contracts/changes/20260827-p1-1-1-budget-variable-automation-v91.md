# P1 1.1.0 예산·가변수량·손절익절 Automation V91

## 결정

기존 V89/V90의 `automation-*.v1`, root exact-56, Team A exact-33 bytes를 보존하고 1.1.0의
예산 정책과 가변수량 조회 표면을 additive v2로 게시한다. 신규 HTTP operation은 정확히 다섯 개이며
root는 exact-61, Team A acceptance는 versioned exact-38이다. 기존 v1 disarm은 계속 신규 주문 중지
표면이므로 v2 disarm을 추가하지 않는다.

## 정책

- 사용자 입력은 `capitalLimitKrw`, `stopLossBps`, `takeProfitBps` 세 값뿐이다.
- 보수 `3%/5%`, 균형 `5%/10%`, 공격 `8%/15%` exact pair는 서버가 preset ID로 파생하고 그 외는
  `custom`이다.
- 최대 자동운용 금액은 `10,000..10,000,000,000`원, 1만원 단위다. 손절은 `1..15%`, 익절은
  `2..30%`이며 익절이 손절보다 커야 한다.
- 최대 포지션 5개, 세션당 신규 주문 1개, 09:30 평가, 09:40 신규 BUY cutoff, 15:20 미체결
  취소·대사를 고정한다.
- 수량은 자금 슬롯, 남은 총한도, Principle 1회/종목 한도, KIS 무미수 매수가능금액·수량의 최솟값이다.
  AI/LSTM은 종목 순위만 정하며 수량 권한이 없다.

## API와 현재 차단 상태

```text
GET  /api/v2/automation/status
PUT  /api/v2/automation/policy
POST /api/v2/automation/arm
GET  /api/v2/automation/runs
GET  /api/v2/automation/positions
```

provider-free acceptance에서 status는 `BLOCKED_INCOMPLETE_RISK_BALANCE`를 노출하고 `canArm=false`다.
v2 arm은 해당 상태에서 409를 반환하며 provider/account/order call을 만들지 않는다. 따라서 코드는
구현됐지만 qualified `COMPLETE` online risk-balance 근거 전에는 활성화할 수 없다. UI도 blocker가 하나라도
있으면 Start를 비활성화하고 arm 요청을 보내지 않는다.

## 불변식

```text
ROOT_OPENAPI_OPERATION_COUNT=61
TEAM_A_ACCEPTANCE_OPERATION_COUNT=38
V1_ROOT_56_PROJECTION_SHA256=8a94b6cae3bafbc4d353bde7bae88aa568c3fe691e08b97f8cdb52996612a8a0
AUTOMATION_DEFAULT=DISARMED
V2_ARM_CURRENT_ACCEPTANCE_STATUS=409
AUTOMATION_ACTIVATABLE=FALSE_BLOCKED_INCOMPLETE_RISK_BALANCE
KIS_LIVE_CALLS=0
PROVIDER_PHYSICAL_CALLS=0
AUTOMATIC_INTERNAL_PAPER_FALLBACK=0
```
