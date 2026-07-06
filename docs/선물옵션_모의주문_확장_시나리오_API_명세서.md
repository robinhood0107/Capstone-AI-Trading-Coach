# 선물옵션 모의주문 확장 시나리오 API 명세서

작성일: 2026-06-23
상위 문서: `최종_프로젝트_명세서.md`, `API_명세서.md`, `선물옵션_모의주문_확장_시나리오.md`
범위: P1 API를 전제로 추가되는 P2 국내선물옵션 주간 KIS Mock API

> 구현 상태: 이 문서는 v1/P1 필수 구현 계약이 아니라 후속 P2 확장 API 초안이다. 팀원 1의 현재 구현 시작 범위에서는 국내주식/금 ETF·ETN 기반 Principle, Decision, Risk, RAG, KIS Mock API를 우선한다.

---

## 1. 문서 목적

이 문서는 P1 API를 다시 정의하지 않는다. P1의 Principle, Decision, Risk, RAG, Signal, Backtest, Brokerage Mock, Journal, Financial Engineering API를 전제로 두고, 그 상위에 추가되는 P2 국내선물옵션 Mock 주문 API만 정의한다.

KIS endpoint와 TR ID 매핑, 모의 지원/미지원 경계는 이 문서에서만 관리한다. P1 공식 문서에는 국내선물옵션 주문 API를 포함하지 않는다.

---

## 2. P1 상속/전제 API

| P1 API | P2에서 상속하는 역할 | P2 확장 방식 |
|---|---|---|
| Principle API | 사용자 투자 원칙 CRUD, rule versioning, enabled/disabled 관리 | 파생상품 후보도 동일한 원칙 version을 참조 |
| Decision API | 주문 후보 평가, ALLOW/WARN/HOLD/BLOCK 판정 | `derivative_order_intent`를 입력받는 확장 경로 추가 |
| Risk API | 손실한도, 포지션한도, stale data, kill-switch | 상품군 allowlist, 증거금, DTE, Greeks, IV, 유동성 guard 추가 |
| RAG API | 출처 기반 금융 설명, citationCoverage, retrievalFailure | 옵션 매도 위험, 증거금, TR 응답 근거 설명에 사용 |
| Signal API | rule baseline, LSTM, LightGBM, HMM 국면 조회 | 방향성 후보 생성 시 보조 신호로만 사용 |
| Backtest API | Baseline/Guide/Strict 성과 비교 | P2 주문은 backtest 성과가 아니라 Mock risk scenario 재현으로 검증 |
| Brokerage Mock API | 주식/ETF·ETN Mock 주문, 취소, 잔고, 체결 event | 별도 `DerivativeMockGateway`를 추가하되 idempotency/audit 규칙은 상속 |
| Journal API | decision/backtest/RAG 근거 기록 | `marginSnapshotId`, KIS TR ID, fill/position reconciliation 기록 추가 |
| Financial Engineering API | BSM, Greeks, IV, VaR/CVaR, HMM, Monte Carlo 계산 | 파생 주문 설명과 guard 입력으로 사용하되 최종 판단은 RiskEngine이 수행 |
| Async Status API | async job 상태, stream metric, artifact ingest 상태 | P2 내부 async event 상태는 Spring 상태 조회 API로만 노출 |

---

## 3. 공통 원칙

1. 프론트엔드는 Spring REST API만 호출한다.
2. Python KIS Adapter는 내부 gRPC 실행 어댑터다.
3. 최종 주문 승인권은 Spring Decision Platform과 RiskEngine에 있다.
4. P2 주문은 `KIS_DERIVATIVE_MOCK`에서만 가능하다.
5. P2 주문 session은 `DAY`만 허용한다.
6. Live-ready 경로는 기본 OFF다.
7. 주문 후보 생성은 주문 실행이 아니다.
8. 주문 전 `marginSnapshot`과 `riskDecision`이 없으면 fail-closed 한다.
9. 모든 쓰기 API는 P1 공통 규칙과 동일하게 `X-Idempotency-Key` header를 요구한다.
10. 모든 이벤트는 `decisionId`, `marginSnapshotId`, `orderIntentId`, `kisTrId`, `requestId`를 audit log에 남긴다.
11. P2 내부 async event는 기본 OFF이며, 실제 내부 구현 상세는 팀원 1 상세 구현명세서에서 관리한다.
12. P1의 Decision 유효시간 규칙(`validUntil`, 만료 시 `DECISION_EXPIRED`)을 파생 decision에도 그대로 상속한다. margin snapshot은 자체 `expiresAt`을 추가로 가지며, 둘 중 하나라도 만료되면 주문 제출을 거부한다.

---

## 4. P2 공통 타입

### 4.1 AssetType

| 값 | 의미 |
|---|---|
| `DOMESTIC_FUTURE` | 국내선물 |
| `DOMESTIC_OPTION` | 국내옵션 |

### 4.2 OptionRight

| 값 | 의미 |
|---|---|
| `CALL` | 콜 |
| `PUT` | 풋 |
| `NONE` | 선물처럼 권리 구분이 없는 상품 |

### 4.3 PositionEffect

| 값 | 의미 |
|---|---|
| `OPEN` | 신규 진입 |
| `CLOSE` | 기존 포지션 청산 |
| `REDUCE` | 위험 노출 축소 |
| `ROLL` | 만기/행사가 이동 |

### 4.4 DerivativeOrderType

| 값 | 의미 | 정책 |
|---|---|---|
| `LIMIT` | 지정가 | 기본 허용 |
| `MARKET` | 시장가 | 청산/위험감소 주문만 허용 |
| `CONDITION_LIMIT` | 조건부 지정가 | KIS 지원 범위와 dashboard 확인 후 허용 |

---

## 5. Schema

### 5.1 derivative_order_intent

```json
{
  "schema": "derivative_order_intent",
  "orderIntentId": "doi_20260623_001",
  "accountId": "mock-derivatives-01",
  "assetType": "DOMESTIC_OPTION",
  "productGroup": "KOSPI200_OPTION",
  "symbol": "201V6300",
  "underlyingSymbol": "KOSPI200",
  "maturity": "2026-09",
  "expiryDate": "2026-09-10",
  "strikePrice": 360.0,
  "optionRight": "PUT",
  "side": "BUY",
  "positionEffect": "OPEN",
  "contracts": 1,
  "orderType": "LIMIT",
  "limitPrice": 2.40,
  "timeInForce": "DAY",
  "strategyRole": "HEDGE",
  "riskReductionOnly": false,
  "dte": 79,
  "marginSnapshotId": "dms_20260623_001",
  "greeks": {
    "delta": -0.42,
    "gamma": 0.018,
    "vega": 0.11,
    "theta": -0.02,
    "rho": -0.03
  },
  "impliedVolatility": 0.247,
  "takeProfit": {
    "type": "PERCENT",
    "value": 35.0
  },
  "stopLoss": {
    "type": "PERCENT",
    "value": 25.0
  },
  "timeLimit": {
    "maxHoldingDays": 10
  },
  "source": {
    "signalId": "sig_001",
    "backtestId": "bt_001",
    "ragSourceIds": ["src_001", "src_002"]
  }
}
```

필수 필드:

| 필드 | 필수 | 설명 |
|---|---:|---|
| `assetType` | 예 | `DOMESTIC_FUTURE` 또는 `DOMESTIC_OPTION` |
| `productGroup` | 예 | allowlist와 kill-switch 판단 단위 |
| `symbol` | 예 | KIS 상품 코드 |
| `maturity`, `expiryDate` | 예 | 만기 식별 |
| `strikePrice`, `optionRight` | 옵션만 예 | 선물은 `optionRight=NONE` |
| `side` | 예 | `BUY` 또는 `SELL` |
| `positionEffect` | 예 | 신규/청산/위험감소 구분 |
| `contracts` | 예 | 계약수 |
| `orderType` | 예 | 지정가 기본 |
| `limitPrice` | 지정가면 예 | 지정가 주문 가격 |
| `marginSnapshotId` | 예 | 주문 전 생성된 snapshot |
| `dte` | 예 | Days To Expiry |
| `greeks`, `impliedVolatility` | 옵션이면 예 | stale guard 입력 |

### 5.2 derivative_margin_snapshot

```json
{
  "schema": "derivative_margin_snapshot",
  "marginSnapshotId": "dms_20260623_001",
  "accountId": "mock-derivatives-01",
  "orderIntentId": "doi_20260623_001",
  "brokerageMode": "KIS_DERIVATIVE_MOCK",
  "session": "DAY",
  "kisTrId": "VTTO5105R",
  "orderableContracts": 3,
  "liquidatableContracts": 1,
  "orderableCash": 7200000,
  "requiredMarginEstimate": 4100000,
  "additionalMarginEstimate": 800000,
  "marginBufferRate": 0.2,
  "marginBufferAmount": 820000,
  "marginBufferOk": true,
  "rawFields": {
    "ord_psbl_cash": "7200000",
    "ord_psbl_qty": "3",
    "tot_psbl_qty": "3",
    "lqd_psbl_qty1": "1"
  },
  "asOf": "2026-06-23T10:15:22+09:00",
  "expiresAt": "2026-06-23T10:16:22+09:00"
}
```

### 5.3 derivative_risk_decision

```json
{
  "schema": "derivative_risk_decision",
  "decisionId": "dec_deriv_20260623_001",
  "orderIntentId": "doi_20260623_001",
  "marginSnapshotId": "dms_20260623_001",
  "decision": "WARN",
  "reasons": [
    "옵션 매수 premium이 전략별 한도 안에 있음",
    "HMM 고변동 국면이므로 신규 방향성 주문은 계약수 1개로 제한"
  ],
  "guards": {
    "productGroupAllowed": true,
    "productKillSwitchActive": false,
    "sessionAllowed": true,
    "orderableQuantityOk": true,
    "marginBufferOk": true,
    "maxContractsOk": true,
    "orderTypeAllowed": true,
    "optionShortConfirmationRequired": false,
    "liquidityOk": true,
    "dataFreshnessOk": true,
    "dailyLossOk": true,
    "cooldownOk": true
  },
  "limits": {
    "maxContracts": 1,
    "dailyLossLimit": 250000,
    "maxPremium": 500000,
    "maxDte": 120
  },
  "greeksIvSummary": {
    "delta": -0.42,
    "vega": 0.11,
    "impliedVolatility": 0.247,
    "stale": false
  },
  "nextAction": "사용자 확인 후 지정가 Mock 주문 제출 가능"
}
```

### 5.4 derivative_order_event

```json
{
  "schema": "derivative_order_event",
  "eventId": "doe_20260623_001",
  "orderId": "kis_deriv_mock_001",
  "orderIntentId": "doi_20260623_001",
  "decisionId": "dec_deriv_20260623_001",
  "marginSnapshotId": "dms_20260623_001",
  "brokerageMode": "KIS_DERIVATIVE_MOCK",
  "kisTrId": "VTTO1101U",
  "status": "SUBMITTED",
  "symbol": "201V6300",
  "contracts": 1,
  "filledContracts": 0,
  "avgFillPrice": null,
  "fills": [],
  "positionsAfter": [],
  "rawResponseRef": "artifacts/decision-platform/kis-raw/20260623/order_001.json",
  "occurredAt": "2026-06-23T10:15:25+09:00"
}
```

### 5.5 P2 내부 async event 원칙

P2 derivative event는 공용 API 계약이 아니다. P2 기능 flag가 OFF이면 내부 async event도 발행하지 않는다. 이벤트 형식, 재처리, 중복 처리, payload 제한은 팀원 1 상세 구현명세서의 내부 비동기 처리 섹션에서 관리한다.

---

## 6. REST API

### 6.1 Derivative Universe 조회

`GET /api/v1/derivatives/universe?productGroup=KOSPI200_OPTION&enabledOnly=true`

응답:

```json
{
  "success": true,
  "data": {
    "asOf": "2026-06-23T10:00:00+09:00",
    "items": [
      {
        "assetType": "DOMESTIC_OPTION",
        "productGroup": "KOSPI200_OPTION",
        "symbol": "201V6300",
        "underlyingSymbol": "KOSPI200",
        "maturity": "2026-09",
        "strikePrice": 360.0,
        "optionRight": "PUT",
        "allowlisted": true,
        "killSwitchActive": false,
        "liquidityStatus": "OK"
      }
    ]
  }
}
```

### 6.2 Margin Snapshot 생성

`POST /api/v1/derivatives/margin-snapshots`

요청:

```json
{
  "accountId": "mock-derivatives-01",
  "orderIntentId": "doi_20260623_001",
  "assetType": "DOMESTIC_OPTION",
  "symbol": "201V6300",
  "side": "BUY",
  "contracts": 1,
  "orderType": "LIMIT",
  "limitPrice": 2.40,
  "session": "DAY"
}
```

응답은 `derivative_margin_snapshot` schema를 따른다.

### 6.3 Order Candidate 생성

`POST /api/v1/derivatives/order-candidates`

요청:

```json
{
  "portfolioId": "pf_p1_001",
  "strategyRole": "HEDGE",
  "candidateMode": "GUIDE",
  "maxCandidates": 3,
  "basis": {
    "signalId": "sig_001",
    "riskRegime": "HIGH_VOLATILITY",
    "portfolioBeta": 1.25
  }
}
```

응답:

```json
{
  "success": true,
  "data": {
    "candidates": [
      {
        "orderIntentId": "doi_20260623_001",
        "assetType": "DOMESTIC_OPTION",
        "productGroup": "KOSPI200_OPTION",
        "symbol": "201V6300",
        "strategyRole": "HEDGE",
        "side": "BUY",
        "positionEffect": "OPEN",
        "contracts": 1,
        "orderType": "LIMIT",
        "limitPrice": 2.40,
        "reason": "P1 포트폴리오 beta와 고변동 국면을 줄이는 보호적 put 후보"
      }
    ]
  }
}
```

### 6.4 Decision API 확장

`POST /api/v1/decisions/evaluate-order`

P1 endpoint를 그대로 사용하되, 요청 body에 `orderIntentType=DERIVATIVE`와 `derivativeOrderIntent`를 포함한다.

```json
{
  "orderIntentType": "DERIVATIVE",
  "derivativeOrderIntent": {
    "orderIntentId": "doi_20260623_001",
    "assetType": "DOMESTIC_OPTION",
    "productGroup": "KOSPI200_OPTION",
    "symbol": "201V6300",
    "side": "BUY",
    "positionEffect": "OPEN",
    "contracts": 1,
    "orderType": "LIMIT",
    "limitPrice": 2.40,
    "marginSnapshotId": "dms_20260623_001"
  },
  "principleId": "prc_001",
  "mode": "GUIDE"
}
```

응답은 `derivative_risk_decision`을 `data.derivativeRiskDecision`에 포함한다.

### 6.5 Mock 주문 제출

`POST /api/v1/derivatives/mock/orders`

필수 header:

| Header | 설명 |
|---|---|
| `X-Idempotency-Key` | 중복 주문 방지 (P1 공통 규칙과 동일) |

`decisionId`와 `marginSnapshotId`는 header가 아니라 요청 body로만 전달한다. 같은 값을 header와 body에 중복시키면 불일치 시 처리 규칙이 따로 필요해지므로 body로 일원화한다.

요청:

```json
{
  "orderIntentId": "doi_20260623_001",
  "decisionId": "dec_deriv_20260623_001",
  "marginSnapshotId": "dms_20260623_001",
  "confirmations": {
    "riskReviewAccepted": true,
    "optionShortFirstConfirm": false,
    "optionShortSecondConfirm": false
  }
}
```

응답은 `derivative_order_event` schema를 따른다.

### 6.6 정정취소

`POST /api/v1/derivatives/mock/orders/{orderId}/cancel-or-modify`

```json
{
  "action": "MODIFY",
  "newLimitPrice": 2.35,
  "newContracts": 1,
  "reason": "지정가 개선",
  "requestId": "req_20260623_001"
}
```

체결된 수량은 정정/취소 대상에서 제외한다. KIS 응답이 불명확하면 주문 상태를 `PENDING_RECONCILIATION`으로 두고 체결조회로 재확인한다.

### 6.7 체결조회

`GET /api/v1/derivatives/mock/accounts/{accountId}/fills?from=2026-06-23&to=2026-06-23`

### 6.8 잔고현황

`GET /api/v1/derivatives/mock/accounts/{accountId}/positions`

### 6.9 Live-readiness

`GET /api/v1/derivatives/live-readiness`

```json
{
  "success": true,
  "data": {
    "enabled": false,
    "defaultState": "OFF",
    "requiredConditions": [
      "mockPerformanceReviewPassed",
      "accountQualificationConfirmed",
      "explicitUserConsentRecorded",
      "marginPolicyApproved",
      "operatorKillSwitchOff"
    ],
    "currentStatus": {
      "mockPerformanceReviewPassed": false,
      "accountQualificationConfirmed": false,
      "explicitUserConsentRecorded": false,
      "marginPolicyApproved": false,
      "operatorKillSwitchOff": true
    }
  }
}
```

---

## 7. Python gRPC 확장

### 7.1 DerivativeBrokerageService

```proto
service DerivativeBrokerageService {
  rpc GetDerivativeUniverse(GetDerivativeUniverseRequest) returns (GetDerivativeUniverseResponse);
  rpc CreateDerivativeMarginSnapshot(CreateDerivativeMarginSnapshotRequest) returns (CreateDerivativeMarginSnapshotResponse);
  rpc SubmitDerivativeMockOrder(SubmitDerivativeMockOrderRequest) returns (SubmitDerivativeMockOrderResponse);
  rpc ModifyOrCancelDerivativeOrder(ModifyOrCancelDerivativeOrderRequest) returns (ModifyOrCancelDerivativeOrderResponse);
  rpc GetDerivativeFills(GetDerivativeFillsRequest) returns (GetDerivativeFillsResponse);
  rpc GetDerivativePositions(GetDerivativePositionsRequest) returns (GetDerivativePositionsResponse);
  rpc StreamDerivativeOrderEvents(StreamDerivativeOrderEventsRequest) returns (stream DerivativeOrderEvent);
}
```

### 7.2 DerivativeMarketDataService

```proto
service DerivativeMarketDataService {
  rpc GetDerivativeQuote(GetDerivativeQuoteRequest) returns (GetDerivativeQuoteResponse);
  rpc GetDerivativeOrderBook(GetDerivativeOrderBookRequest) returns (GetDerivativeOrderBookResponse);
  rpc GetDerivativeDailySeries(GetDerivativeDailySeriesRequest) returns (GetDerivativeDailySeriesResponse);
}
```

FinancialEngineeringService는 P1의 BSM/Greeks/IV 계산 RPC를 재사용한다.

---

## 8. KIS TR ID 매핑

KIS TR ID와 모의 지원 경계는 이 표를 기준으로 한다. 구현 시 로컬 XLSX의 `API 목록` sheet와 `open-trading-api` 예제를 source manifest에 남긴다.

### 8.1 사용 대상

| 기능 | Endpoint | 실전 TR ID | 모의 TR ID | P2 사용 |
|---|---|---|---|---|
| 선물옵션 시세 | `/uapi/domestic-futureoption/v1/quotations/inquire-price` | `FHMIF10000000` | `FHMIF10000000` | quote/stale guard |
| 선물옵션 시세호가 | `/uapi/domestic-futureoption/v1/quotations/inquire-asking-price` | `FHMIF10010000` | `FHMIF10010000` | spread/liquidity guard |
| 선물옵션 기간별시세 | `/uapi/domestic-futureoption/v1/quotations/inquire-daily-fuopchartprice` | `FHKIF03020100` | `FHKIF03020100` | trend/volatility context |
| 선물옵션 주문 | `/uapi/domestic-futureoption/v1/trading/order` | 주간 `TTTO1101U` | 주간 `VTTO1101U` | Mock order submit |
| 선물옵션 정정취소 | `/uapi/domestic-futureoption/v1/trading/order-rvsecncl` | 주간 `TTTO1103U` | 주간 `VTTO1103U` | modify/cancel |
| 선물옵션 주문가능 | `/uapi/domestic-futureoption/v1/trading/inquire-psbl-order` | `TTTO5105R` | `VTTO5105R` | margin snapshot 필수 입력 |
| 선물옵션 주문체결내역조회 | `/uapi/domestic-futureoption/v1/trading/inquire-ccnl` | `TTTO5201R` | `VTTO5201R` | fills reconciliation |
| 선물옵션 잔고현황 | `/uapi/domestic-futureoption/v1/trading/inquire-balance` | `CTFO6118R` | `VTFO6118R` | positions reconciliation |
| 선물옵션 실시간체결통보 | WebSocket | `H0IFCNI0` | `H0IFCNI9` | order event stream |

### 8.2 모의 미지원 또는 P2 제외

| 항목 | 판단 |
|---|---|
| 야간 국내선물옵션 주문/정정취소/체결조회/주문가능 | 모의 미지원으로 주문 API에서 차단 |
| 선물옵션 증거금률 API | 모의 미지원. 주문가능조회, 잔고현황, 내부 margin buffer로 보수 처리 |
| 선물옵션 분봉조회 | 모의 미지원. P2 주문 판단에는 현재가, 호가, 기간별시세 중심 사용 |
| 해외선물옵션 주문 계열 | 모의 미지원. P2 범위 제외 |
| 파생 실거래 활성화 | 기본 OFF. live-readiness 조건 충족 전까지 차단 |

---

## 9. Fail-Closed 정책

아래 P2 오류 코드(`DERIVATIVE_GATE_CLOSED`, `MARGIN_GUARD_FAILED`)는 P1 API 명세서 2.3 오류 코드 표를 확장한다. 클라이언트 분기 규칙(HTTP 상태가 아니라 `error.code` 기준) 역시 P1과 동일하다.

| 조건 | API 처리 | Error Code |
|---|---|---|
| 상품군 allowlist 미포함 | 주문 후보 생성 또는 평가 단계에서 차단 | `DERIVATIVE_GATE_CLOSED` |
| 상품별 kill-switch 활성 | 평가 단계에서 차단 | `RISK_BLOCKED` |
| session이 `DAY`가 아닌 경우 | 주문 후보와 주문 제출 차단 | `DERIVATIVE_GATE_CLOSED` |
| 주문가능조회 실패 | `marginSnapshot` 생성 실패 | `MARGIN_GUARD_FAILED` |
| 주문가능수량 부족 | 평가 단계에서 차단 | `MARGIN_GUARD_FAILED` |
| margin buffer 부족 | 평가 단계에서 차단 | `MARGIN_GUARD_FAILED` |
| max 계약수 초과 | 평가 단계에서 차단 | `RISK_BLOCKED` |
| 옵션 매도 2단계 확인 누락 | 주문 제출 단계에서 차단 | `RISK_BLOCKED` |
| 시장가가 청산/위험감소 주문에 해당하지 않음 | 평가 단계에서 차단 | `RISK_BLOCKED` |
| 시세/호가/Greeks/IV stale | 평가 단계에서 보류 | `DATA_STALE` |
| KIS Adapter 장애 | 주문 보류, reconciliation 대기 | `BROKERAGE_UNAVAILABLE` |

---

## 10. 테스트 기준

| 테스트 | 확인 |
|---|---|
| universe allowlist test | allowlist 밖 상품이 후보와 주문에서 차단되는지 확인 |
| kill-switch test | 상품군/상품별 kill-switch가 신규 주문을 차단하는지 확인 |
| session guard test | 야간 주문 요청이 `DERIVATIVE_GATE_CLOSED`로 막히는지 확인 |
| margin snapshot failure test | KIS 주문가능조회 실패 시 주문 평가가 진행되지 않는지 확인 |
| margin buffer test | buffer 부족 시 `MARGIN_GUARD_FAILED`가 반환되는지 확인 |
| option short confirmation test | 옵션 매도 2단계 확인 누락 시 주문 제출이 차단되는지 확인 |
| market order guard test | 신규 진입 시장가 차단, 위험감소 시장가 조건부 허용 확인 |
| stale data test | 시세/호가/Greeks/IV stale이면 HOLD 되는지 확인 |
| reconciliation test | 주문 제출 후 체결조회와 잔고현황이 audit trace에 연결되는지 확인 |
| live-readiness test | 기본 OFF와 필수 조건 미충족 시 실거래 활성화 불가 확인 |
| derivative async event default-off test | P2 feature flag OFF이면 내부 async event가 발행되지 않는지 확인 |
| derivative async event status test | 외부에는 Spring 상태 조회 API만 노출되는지 확인 |

---

## 11. Source Manifest

P2 구현 산출물은 다음 근거를 artifact manifest에 남긴다.

| Source | 용도 |
|---|---|
| 한국투자증권 모의투자 안내 | 국내 선물옵션 모의거래 흐름 근거 |
| 한국투자증권 수수료 안내 | 비용 config와 수수료/세금 출처 |
| `한국투자증권_오픈API_전체문서_20260504_030007.xlsx` | endpoint, TR ID, 모의 지원 경계 |
| `open-trading-api/examples_user/domestic_futureoption/domestic_futureoption_functions.py` | 주문가능, 주문, 정정취소, 잔고현황 예제 |
| `open-trading-api/examples_llm/domestic_futureoption` | 주문가능 필드와 검증 check 사례 |
