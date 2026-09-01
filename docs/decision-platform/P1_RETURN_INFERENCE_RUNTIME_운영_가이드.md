# P1 Return inference runtime 운영 가이드

## 현재 상태

```text
P1_RETURN_INFERENCE_KERNEL=VERIFIED_SYNTHETIC_TEST_PROFILE
P1_RETURN_INFERENCE_GRPC=VERIFIED_LOOPBACK_BOUNDED
P1_RETURN_INFERENCE_SUPERVISOR=VERIFIED_COMPOSE_HEALTH
P1_RETURN_INFERENCE_PRODUCTION_POINTER=NONE_PENDING_REAL_TEAM_B
P1_RETURN_INFERENCE_NO_POINTER=FAILED_PRECONDITION
RETURN_INFERENCE_PROVIDER_DB_DML_ORDER_AUTHORITY=0
```

2026-09-01 V116 current 경계는 Git Model Seed를 read-only로 load하고 automation session claim 직전에
`ensure_daily_signals(sessionDate)`를 실행한다.

```text
P1_DAILY_INFERENCE_IMPLEMENTED=PASS
P1_DAILY_SIGNAL_CONTRACT=p1-return-daily-signal-batch.v1
P1_DAILY_SIGNAL_CONFIDENCE_FIELDS=0
P1_DAILY_SIGNAL_SOURCE=PREVIOUS_COMPLETED_XKRX_SESSION
P1_DAILY_SIGNAL_IDENTITY=MODEL_HASH+MARKET_MANIFEST+SOURCE_TARGET_SESSION
P1_DAILY_SIGNAL_RETRY=ONE_IDENTICAL_INFERENCE_RETRY
```

2026-08-27 synthetic golden model로 fixed 3-layer LSTM의 exact-31 batch를 두 번 실행해 byte-identical
response를 확인했다. auth 누락, 5초 초과 deadline, noncanonical/shape drift는 거부됐다. 실제
provider-free 5-container Compose에서는 model pointer 없이 inference process와 gRPC health가 정상이고,
인증된 Infer 호출은 `FAILED_PRECONDITION`이었다. 전체 Compose smoke provider call은 0이었다.

## ABI

- symbol: 정확히 31개, `132030` 정확히 1개
- feature order: `open, high, low, raw_close, volume, return_1d, ma5, ma20, rsi14`
- window: 20
- LSTM: 3 layer, hidden 128, PyTorch gate order `input, forget, cell, output`
- head: `1 × 128` + bias
- tensor: F32, finite, exact symbol namespace와 shape
- scaler: symbol별 mean/scale 9개, scale 양수, train-only

## gRPC 경계

- service: `capstone.return_inference.v1.ReturnInferenceService`
- method: unary `Infer`
- bind: `127.0.0.1:50057`
- transport payload: canonical JSON bytes
- request 상한: 256 KiB
- response 상한: 64 KiB
- 동시 실행: 최대 2
- deadline: 필수, 최대 5초
- auth metadata: purpose-separated shared secret
- reflection: 비활성

request는 artifact ID, bundle SHA-256, session date와 exact-31 row를 포함한다. 각 row는 동일 session,
positive current close, 20×9 finite feature를 가져야 하며 마지막 `raw_close`는 current close와 같아야 한다.
response에는 prediction만 있고 Signal composite, RiskDecision, account, order field는 없다.

## daily 연결

기존 scheduler를 추가하지 않는다. `AutomationRuntimeService.serve()`가 session claim 전에 daily batch를
확인하고, 없을 때만 운영 market-data 40개 bar에서 20×9 exact-31 feature를 만든다. 기존 loopback
inference를 최대 두 번(첫 시도 + 동일 요청 1회 retry) 호출하고 MA5·MA20 cross와 RSI14 rule을 함께
V116 transaction으로 저장한다. 같은 identity와 같은 bytes는 no-op이며 다른 bytes는 전체 rollback한다.

두 시도 모두 실패하면 batch가 생기지 않아 신규 BUY 후보는 0이다. 기존 포지션 청산·미결 주문 대사
경로는 계속 claim되어 동작한다. stale·전날 signal·synthetic·legacy preview는 daily pointer가 아니다.

## model authority

- production load 후보는 `REAL_TEAM_B`, `modelQuality=PASS | BELOW_BASELINE`,
  `mockRuntimeEligible=true`를 모두 만족해야 한다. `BELOW_BASELINE`은 성능
  우월성 주장을 금지하는 공개 표식이며, 성능만으로
  input binding·leakage·재현성·비용·metric 검증을 통과한 bundle을 차단하지 않는다.
- `SYNTHETIC_GOLDEN`은 test profile에서만 load할 수 있다.
- pointer가 없거나 부적합하면 서버는 model을 꾸미지 않고 `FAILED_PRECONDITION`을 반환한다.
- 이 단계는 model activation, daily publication, RiskEngine 또는 주문 연결 권한이 아니다.

## supervisor와 health

결합 decision-platform image는 Spring, async worker, Return inference와 선택적 KIS Mock brokerage를 같은
supervisor에서 관리한다. 어느 필수 process든 종료되면 container가 실패한다. healthcheck는 Spring,
async worker, Return inference를 모두 확인하며 Docker socket이나 새 장기 container를 추가하지 않는다.
