# P1 Return model shape comes from config.json, not from fixed constants

## KR

Team B가 [PR #197](https://github.com/robinhood0107/Capstone-AI-Trading-Coach/pull/197)에서 학습 설정을
`hidden 128 / 3층 / dropout 0.2 / lr 0.0005`에서 `hidden 64 / 1층 / dropout 0 / lr 0.001`로 바꿨다.
성능이 기대에 못 미쳐 앞으로도 더 조정할 예정이라고 PR 본문에 적혀 있다.

문제는 Owner가 이 형상을 **네 곳에 하드코딩**해 뒀다는 것이다. `importer.py`의 `_TENSOR_SHAPES`,
`inference.py`의 텐서 접미사 집합, 같은 파일의 numpy LSTM 커널(`range(3)`, `np.zeros(128)`,
shape assert `(512, …)`), `assets.py`의 골든 번들 생성기. hidden 64 / 1층이면 게이트 폭이
`4×64=256`이 되고 텐서가 종목당 `4×1+2=6`개가 되어 네 곳 모두 어긋난다. 값을 64/1로 다시 못박으면
다음 조정에서 같은 일을 반복한다.

**그래서 형상을 상수가 아니라 데이터로 만든다.** 진실 소스는 번들의 `config.json` 하나다.
safetensors 헤더에서 역추론하는 폴백은 두지 않는다 — 소스가 둘이면 어긋날 때 어느 쪽이 맞는지
정할 수 없고, 필요한 필드가 없으면 fail-closed하는 편이 낫다.

### 신설

`app/p1_owner/model_shape.py`. 순수 함수만 두고 provider·account·order·DB client를 import하지 않으며
`app.p1_owner.assets`도 import하지 않아 순환을 만들지 않는다.

```
gate_width  = 4 * hiddenSize
suffixes    = [weight_ih_l{i}, weight_hh_l{i}, bias_ih_l{i}, bias_hh_l{i} for i in 0..layerCount-1]
              + [head.weight, head.bias]
weight_ih_l0     (gate_width, featureCount)      weight_ih_l{i>0} (gate_width, hiddenSize)
weight_hh_l{i}   (gate_width, hiddenSize)        bias_*_l{i}      (gate_width,)
head.weight      (1, hiddenSize)                 head.bias        (1,)
tensorCount = 31 * (4 * layerCount + 2)
```

허용 범위는 `hiddenSize ∈ [8, 1024]`, `layerCount ∈ [1, 8]`, `dropout ∈ [0, 1)`,
`learningRate ∈ (0, 1]`. `windowSize`와 `outputSize`는 계속 고정한다 — daily inference가 20행 창을
조립하고 `_RAW_HISTORY_LIMIT`이 여기에 맞춰져 있으며 `head`가 `(1, hiddenSize)`라 출력 폭 1만
파생 가능하다. 게이트 순서 i/f/g/o는 `nn.LSTM` 규약이므로 그대로다.

`inference.py`의 커널도 `range(layerCount)`, `np.zeros(hiddenSize)`,
`input_size = featureCount if layer == 0 else hiddenSize`로 일반화했다.

### signal 임계값은 Team B 규칙을 따른다

`SIGNAL_DEADBAND = 0.005`. 판정 기준량은 `expectedReturn`(= `forecastClose/currentClose - 1`)이다.

```
BUY  if expectedReturn >  0.005
SELL if expectedReturn < -0.005
HOLD otherwise
```

Team B의 `BUY_THRESHOLD`/`SELL_THRESHOLD`(±0.5%)를 채택하되 기준량만 바꿨다. 기존
`SignalGenerator.from_prediction`은 `Prediction.pct_change()`, 즉 **전일 예측 대비** 변화율을 쓰는데
그대로 쓰면 `expectedReturn` 컬럼과 `signal` 컬럼이 서로 다른 양에서 나와 seed 자체가 자기모순이
된다. `importer.py`와 `inference.py`가 **둘 다** 임계값 0(`forecast > current`)이었으므로 두 곳을
함께 교체했다. `golden_output.json`의 `forecastFormula`는 수익률 정의이고 signal 규칙과 별개이므로
`forecastClose/currentClose-1`을 유지한다.

SYNTHETIC_GOLDEN의 시그널 델타는 ±1원(≈0.01%)이라 새 deadband 안이었다. ±200원으로 올려
최저가 10,300원에서도 1.9%가 되게 했고, 분류는 이제 공유 `classify_signal`이 한다.

### 요청서에 없던 강제 조건 완화

요청서(`docs/handoff/P1_TEAM_B_최종_통합_요청서.md`)가 규정하지 않았는데 Owner가 강제하던 것들을
`workspaces/return-engine/` 현행 동작에 맞춰 풀었다.

| 항목 | 이전 | 이후 | 근거 |
|---|---|---|---|
| backtest 시나리오 | `(BASELINE, GUIDE, STRICT)` 정확 일치 | 순서 보존 부분집합, **GUIDE 필수** | 요청서에 개수 언급 없음 |
| equity 곡선 행 수 | `minimum 3` | `minimum 1` | 시나리오 개수에 연동 |
| trade `quantity` | Python `int` | 양수 정수값 float 허용 | `cash // price`가 float를 만든다 |
| Sharpe 연율화 | `sqrt(253)` 단독 | `sqrt(252)` 캐논, `sqrt(253)`도 통과 | `backtest_engine.calculate_sharpe` |
| `config.json` | closed 14키 | 필수 14키 subset, 초과 키 무시 | 요청서가 `epochs`, `batchSize`, `roundTripCostBps` 등을 나열 |
| `golden_output.json` | closed 8키 | 필수 8키 subset | 요청서에 필드 규정 없음 |
| `model_report.md` | `## ` 6개 절 필수 | UTF-8 + 비어 있지 않음 | 요청서에 절 구성 없음 |
| safetensors `__metadata__` | `symbolCount == "31"` 필수 | 있으면 검증, 없으면 통과 | 요청서에 언급 없음 |

GUIDE를 필수로 남긴 이유는 `_build_import_packet`이 GUIDE를 LSTM에 매핑하고 dashboard의
`metricCards`가 그 값을 직접 읽기 때문이다. BASELINE과 STRICT가 없으면 해당 전략은 지표 없이
`ABSTAIN`으로 투영된다. 완화는 모두 "필수 키 존재 + 값 검증"으로만 하고, 알 수 없는 키는 거부하지
않되 **import packet에는 싣지 않는다** — 패킷 22키 계약은 그대로다.

유지한 것: `costBps == 35`, `netReturn = grossReturn − 0.0035`, exact-31, exact-10 파일명과 순서,
`featureOrder` 9개 고정, `fitScope == TRAIN_ONLY`, `perSymbolIndependent`,
`orderAuthority == NONE`, `performanceClaimAllowed == false`,
`modelQuality ∈ {PASS, BELOW_BASELINE}`, 신호 산술의 `abs_tol=1e-12` 독립 재계산.

### 바뀐 계약 bytes

generator(`contracts/generate_p1_owner_phase_a_contracts.py`)가 소유한 스키마 **4개**만 재생성됐다.

```
contracts/schemas/p1-return-config.v2.schema.json
contracts/schemas/p1-return-backtest-result.v2.schema.json
contracts/schemas/p1-return-equity-log.v2.schema.json
contracts/schemas/p1-return-model-report.v2.schema.json
```

`SCHEMA_IDS` 순서와 `_fixtures()` 짝은 그대로이며 새 스키마 ID를 추가하지 않았다.
generator의 `FROZEN_SHA256`에는 `p1-return-engine-artifact-manifest.v1`만 있고 v2/v3는 없으므로
동결 위반이 아니다. `p1-return-lstm-signals.v3`, `p1-return-rule-baseline-signals.v3`,
`p1-return-model-safetensors.v2`(`tensorCount` 하한 31은 31×6=186도 만족), `p1-return-scaler.v2`,
`p1-return-engine-artifact-manifest.v3`의 bytes는 바뀌지 않았다.

### input pack의 `modelConfig`는 참고값으로 격하한다

이미 Team B에 전달된 input pack(input manifest SHA-256
`8ba0b439c5ff4e39b3136c17d31d648178d7e0064c35adead46837f953c5fafd`)의 `modelConfig`는 128/3/0.2/0.0005로
고정돼 있다. `importer`는 산출물 `config.json`을 input pack의 `modelConfig`와 교차 검증하지 않으므로
ZIP 재생성이나 재전달 없이 그 필드를 **advisory**로 둔다. `p1-return-engine-input-pack.v1` 스키마의
`const`는 바꾸지 않았다 — 이미 전달된 manifest가 여전히 통과하므로 바꿀 이유가 없다.

### migration은 없다

`V116__p1_exact31_daily_lstm.sql`을 건드리지 않는다. 신호 62개(31×2), 패킷 22키, producer enum,
`132030` 2회, `signal ? 'confidence'` 금지가 모두 그대로이므로 새 Flyway migration이 필요하지 않고
`P1Exact31DailyLstmMigrationContractTest.kt`도 수정하지 않는다. REST 표면도 바뀌지 않아 root OpenAPI
게이트 대상이 아니다.

### 아직 BLOCKED인 것

PR #197의 `_write_seed_bundle`은 exact-10 열 파일 모두에 `b"placeholder-..."`를 쓰고
`p1-return-engine-manifest.v3.json`에는 입력 manifest를 복사한다. 그 뒤 legacy `ReturnEngine.run()`이
`golden_output.json`을 legacy artifact JSON으로 덮어쓴다. 31종목 루프, safetensors 직렬화,
`scaler.json` export, parquet 3종, 35bps 비용은 아직 없다.

따라서 이 변경은 **수용 경로를 준비한 것이고 실제 Team B 산출물이 들어온 것이 아니다.**
`TEAM_B_REAL_ARTIFACT`는 계속 `BLOCKED`이고 `P1_GIT_MODEL_SEED`도
`BLOCKED_PENDING_TEAM_B_EXACT10`이다. `deploy/p1/seed/team-b/`에는 `.gitkeep`만 있다.

### 검증

```
tests/p1_owner                                                    245 passed, 13 subtests
python-services 전체                                              ruff PASS, mypy PASS
contracts/generate_p1_owner_phase_a_contracts.py --check           P1_OWNER_PHASE_A_CONTRACT_LOCK_VERIFIED
contracts/validate.py                                             contracts validation succeeded
unittest discover -s contracts/tests                              401 tests OK
```

신규 `tests/p1_owner/test_model_shape.py`가 hidden 64/1층 형상 파생, 접미사 6개, 범위 밖 값과
featureOrder 드리프트의 fail-closed, deadband 경계(±0.005 정확히 = HOLD)를 고정한다.
`tests/p1_owner/test_importer.py`에 GUIDE만 남긴 부분집합 번들이 적재되고 BASELINE·STRICT가 지표
없이 투영되는 회귀를 추가했다.

numpy 커널과 torch `nn.LSTM`의 값 일치를 대조하는 parity 검증은 이번 범위에서 제외했다. 어긋나면
shape는 맞고 값만 틀리므로, seed 수령 직후 `lstm_signals.parquet`의 `forecastClose`가
`currentClose`와 같은 자릿수인지 확인하는 것이 현재 유일한 방어선이다.

## EN

Team B changed the training configuration in PR #197 from `hidden 128 / 3 layers / dropout 0.2 / lr 0.0005`
to `hidden 64 / 1 layer / dropout 0 / lr 0.001`, and the PR says more tuning is expected. The Owner side had
that geometry hardcoded in four places — `_TENSOR_SHAPES` in the importer, the tensor-suffix set in the
inference module, the numpy LSTM kernel in the same file, and the golden-bundle generator in assets — so a
64-wide single-layer model breaks all four: the gate width becomes 256 and each symbol carries six tensors
instead of fourteen.

**Model geometry is therefore derived from data rather than pinned as constants.** The single source of truth
is the bundle's own `config.json`; there is deliberately no fallback that infers geometry from the safetensors
header, because two sources cannot be reconciled when they disagree and failing closed on a missing field is
the safer outcome. The new `app/p1_owner/model_shape.py` derives gate width, tensor suffixes, per-tensor
shapes and tensor count from `hiddenSize` and `layerCount`, bounded to `hiddenSize ∈ [8, 1024]` and
`layerCount ∈ [1, 8]`. `windowSize` and `outputSize` stay fixed because daily inference assembles a 20-row
window and the head is `(1, hiddenSize)`.

The signal rule now follows Team B's ±0.5% deadband, classified on `expectedReturn` rather than on the
previous zero threshold, and both the importer and the inference kernel were changed because both enforced
the zero threshold. Conditions the handoff request never specified were relaxed toward the current
return-engine behaviour: the backtest scenario list may be any order-preserving subset that still declares
`GUIDE`, the equity curve minimum drops from three rows to one, trade quantity accepts an integral float,
Sharpe annualises on `sqrt(252)` while still accepting `sqrt(253)`, `config.json` and `golden_output.json`
require their key sets rather than closing them, the model report only has to be non-empty UTF-8, and
safetensors `__metadata__` is optional. Fixed-cost arithmetic, exact-31, the exact-10 inventory, the nine-feature
order, train-only scaler scope, and the order-authority and performance-claim markers are unchanged, and
unknown keys are ignored rather than forwarded into the 22-key import packet.

Four generator-owned schemas were regenerated: config, backtest result, equity log and model report. No new
schema id was added, the signals, scaler, safetensors and manifest v3 bytes are untouched, and the generator's
frozen manifest v1 hash is unaffected. The already-delivered input pack keeps its bytes; its `modelConfig` is
demoted to advisory because the importer never cross-checks it against the artifact config. `V116` and the
REST surface are untouched, so no Flyway migration and no OpenAPI gate run is required.

This prepares the acceptance path only. PR #197 still writes placeholder bytes for all ten seed artifacts and
copies the input manifest in place of the output manifest, so `TEAM_B_REAL_ARTIFACT` remains `BLOCKED` and
`P1_GIT_MODEL_SEED` remains `BLOCKED_PENDING_TEAM_B_EXACT10`. Numpy-versus-torch value parity is not verified
in this change; a mismatch would keep the shapes valid and only corrupt the values, so the first real bundle
must be eyeballed for `forecastClose` sitting in the same order of magnitude as `currentClose`.
