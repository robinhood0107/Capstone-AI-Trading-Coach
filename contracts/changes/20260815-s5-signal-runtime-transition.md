# S5 Signal runtime transition and LightGBM implementation lock

## 상태

```text
S5_0_TRANSITION_AMENDMENT=APPROVED
S5_1_TO_S5_5_REPO_LOCAL_IMPLEMENTATION=APPROVED
SIGNAL_V2_RUNTIME_SYMBOL_ONLY_ROUTE=APPROVED_AFTER_CONTRACT_GATES
EXTERNAL_DATA_ACQUISITION=NO_GO
REAL_MODEL_PRODUCTION_ACTIVATION=NO_GO
RISK_DECISION_ORDER_INTEGRATION=NO_GO
S6_6_FIRST_CROSS_MARKET_JOIN=UNCHANGED
```

이 변경은 2026-08-01에 승인된 `signal-v2.schema.json`과 생성 산출물을 수정하지 않는다. 기존
계약은 contract-only historical record로 유지하고, 실제 조회에 필요한 all-ABSTAIN 표현과
OpenAPI 전환은 별도 versioned contract로 잠근다.

## 변경 이유

기존 Signal v2는 root `asOf`를 필수로 요구하고 최소 한 component가 `AVAILABLE`이어야 한다.
현재처럼 검증된 모델 artifact와 HMM evidence가 모두 없을 때 해당 계약으로 응답하면 시각이나
상태를 위조하게 된다. 또한 historical OpenAPI raw hash 검사는 승인된 단일 v2 route조차
추가하지 못하게 한다. 따라서 다음 경계를 함께 승인한다.

1. Signal v1과 v2의 인접 권한 unknown field를 한 필드씩 거부하는 생성 corpus
2. all-ABSTAIN을 정직하게 표현하는 `signal-v2-runtime-v1`
3. public payload와 분리된 `lightgbm-signal-artifact-v1`
4. 정확히 `/api/v2/signals/{symbol}` GET만 허용하는 preserved-projection OpenAPI verifier
5. S5.1~S5.5가 공유하는 `s5-lightgbm-implementation-lock.v1`

## Signal runtime 불변식

- required component는 `ruleBaseline`, `lstm`, `lightgbm`, `hmmRegime` 정확히 네 개다.
- 각 component는 closed `AVAILABLE | ABSTAIN` union이다.
- `AVAILABLE`에서만 prediction/state와 `confidence`, `asOf`를 허용한다.
- 정상 `AVAILABLE + HOLD`는 중립 예측이며 threshold로 ABSTAIN으로 바꾸지 않는다.
- component 하나라도 ABSTAIN이면 composite는 `REQUIRED_COMPONENT_UNAVAILABLE`이다.
- 하나 이상 AVAILABLE이면 root `asOf`는 AVAILABLE component의 최신 `asOf`다.
- 전부 ABSTAIN이면 root `asOf`와 top-level `modelReportId`를 생략한다.
- stale, calibration failure, drift, missing evidence, producer failure, 식별 불가는 typed ABSTAIN이다.
- `NOT_AVAILABLE`은 enum에 추가하지 않는다. evidence 부재는 `MISSING_EVIDENCE`다.
- public payload는 `modelScore`, artifact/path/user/account/order/RiskDecision 권한을 갖지 않는다.

## Unknown-field corpus

Signal v1과 v2 각각에서 다음 필드를 한 번에 하나만 top-level에 주입하고 JSON Schema,
Python semantic validator, Spring parser, HTTP deserializer가 모두 거부한다.

```text
crossMarketScore
crossMarketMode
crossMarketFreshness
crossMarketExposure
analyst
news
cause
rag
llm
riskDecision
orderAuthority
```

HMM ABSTAIN의 `state`, `asOf`, `signal`, `confidence`, `predictedReturn` 위조와 required component
ABSTAIN 상태의 AVAILABLE composite도 각각 독립 fixture로 거부한다. 모든 object branch는
`additionalProperties=false`다.

## OpenAPI preserved projection

historical raw OpenAPI SHA-256과 canonical preserved-projection SHA-256은 모두 다음 값이다.

```text
94414736f6a1c17b95eafffd53a07a5d33d7a66705890c53dcc971eb5ded3f89
```

current OpenAPI에서 transition catalog에 열거한 exact path, component schema, root tag만 제거한
canonical projection이 이 digest와 동일해야 한다. 기존 path, response, component, info,
security, envelope, Signal v1 의미가 하나라도 바뀌면 실패한다. 기존 S5.0 generator와 Pre-S5
truth-freeze verifier는 검사를 생략하지 않고 이 transition verifier에 위임한다.

## Internal artifact 경계

`lightgbm-signal-artifact-v1`은 public wire 입력이 아니다. AVAILABLE에서만 `asOf`, `signal`,
calibrated `confidence`를 허용하고 ABSTAIN에서만 `reason`을 허용한다. dataset/model/report/
payload/provenance digest는 lowercase SHA-256으로 고정한다. fake는 항상 `fixture=true`와
`provenanceClass=FAKE_CONTRACT`이며 production pointer 후보가 될 수 없다. raw margin과
probability인 `modelScore`는 evaluation report 내부에만 존재하고 public API 및 DB read
projection에서 제외한다.

## LightGBM project policy lock

- objective는 `multiclass`, `num_class=3`; class order는 `SELL=0, HOLD=1, BUY=2`다.
- `r < -0.006`은 SELL, `r > 0.006`은 BUY, 양쪽 equality를 포함한 사이는 HOLD다.
- probability tie는 HOLD, SELL, BUY 순이다.
- grid는 `(15,NONE)`, `(15,CAPPED_BALANCED)`, `(31,NONE)`,
  `(31,CAPPED_BALANCED)` 정확히 네 개다.
- candidate는 세 fold 모두 PASS한 경우만 mean calibrated log loss, Brier, ECE, grid order로
  선택한다. 통과 후보가 없으면 final test를 읽지 않고 ABSTAIN한다.
- Brier는 `mean sum(y-p)^2`인 unscaled multiclass 값이고, log loss는 자연로그다.
- ECE는 top-label 10 equal-width weighted absolute gap이다.
- 실제 PIT 입력이 없으면 `DATASET_UNAVAILABLE`, production pointer 0이다.
- cross-market reader 호출은 0이고 첫 join은 S6.6 policy replay다.

`CAPPED_BALANCED`는 fit block에만 적용한다.

```text
rawWeight[c] = N / (3 * N_c)
cappedWeight[c] = min(rawWeight[c], 5.0)
sampleWeight[row] = cappedWeight[label[row]]
sampleWeight의 산술평균을 1로 정규화
```

fit/calibration block에서 어느 class라도 식별 불가능하면 보정·대체 없이
`UNIDENTIFIABLE_OUTPUT`이다.

## 승인 범위와 hard stop

fixture-first repository-local 구현과 symbol-only read route까지 승인한다. 실제 시장 데이터
취득, provider/live account/order 호출, real AVAILABLE artifact 생산, production pointer 활성화,
RiskDecision/order 연결, S6.6 join은 승인하지 않는다. evidence가 없으면 200 all-ABSTAIN이며,
DB 상태 자체를 판정할 수 없을 때만 typed 503을 반환한다.

## Repository-local 구현 receipt

S5.1~S5.5는 fixture-first merge candidate로 구현됐다. exact calendar/PIT/feature/label/split,
four-grid/Platt/metrics/export/drift, deterministic fake bundle, bounded safe ingest, V72 exact DML과
symbol-only read route가 focused parity를 통과한다. 실제 source row를 취득하지 않았으므로
`DATASET_UNAVAILABLE`, real model `AVAILABLE=false`, production pointer 0이다. 이 receipt는 fake를
성과 증거로 승격하거나 RiskDecision/order/S6.6/provider 권한을 열지 않는다.
