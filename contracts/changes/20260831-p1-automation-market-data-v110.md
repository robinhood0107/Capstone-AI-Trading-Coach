# P1 Automation V3 시장데이터 V110 구현

## 구현 범위

- `market_data_manifests`에 additive `AUTOMATION_BOOTSTRAP / p1-automation-market-bootstrap.v1`
  조합을 허용한다. 기존 `SEED`와 `DAILY` 의미는 변경하지 않는다.
- `decision_automation_runtime`은 base table이나 research view SELECT를 받지 않는다.
  `p1_read_automation_atr_bars_v1`의 current exact-31·이전 세션·최대 101행만 읽는다.
- `p1_read_automation_market_history_status_v1`은 `EMPTY | PARTIAL | READY`와 content-free
  count만 반환한다.
- exact-31·1,260 XKRX session을 100-session window 403개로 나누는 provider-free plan과,
  explicit approval 뒤에만 생성 가능한 KIS Live read-only adapter를 구현한다.
- bootstrap archive는 adjusted OHLCV·market·receipt hash만 보존하고 raw provider response와
  source path, account, balance, order를 저장하지 않는다.
- 기존 fixture-only 16:10 coordinator는 bytes와 기본 동작을 보존한다. 별도
  `LiveDailyCollectionTransport`와 `stage_live_daily_collection`은 flag ON일 때만 exact 38/41
  operation을 한 번씩 수행하고, 승격은 기존 provider-free replay 경로를 사용한다.

## 사용자 표면

```text
./capstone market-data inventory
./capstone market-data bootstrap plan --universe-manifest <absolute-file> --end-session YYYY-MM-DD
./capstone market-data bootstrap validate <absolute-root> --manifest-sha256 <sha256>
./capstone market-data bootstrap stage <absolute-root> --manifest-sha256 <sha256>
```

Compose `market-data-cli`는 internal network에만 있고
`P1_AUTOMATION_MARKET_BOOTSTRAP_ENABLED=false`, `P1_DATA_ONLY_COLLECTOR_ENABLED=false`가 기본이다.
실제 collection command는 provider approval/credential lane과 함께 별도 활성화하기 전에는 이
표면에 노출하지 않는다.

## 검증·상태

```text
V110_MARKET_DATA_FOUNDATION=IMPLEMENTED
AUTOMATION_BOOTSTRAP_DEFAULT=DISABLED
DATA_ONLY_LIVE_COLLECTOR_DEFAULT=DISABLED
MARKET_DATA_BARS_CURRENT_RUNTIME=NOT_POPULATED_BY_THIS_PR
PROVIDER_PHYSICAL_CALLS=0
ACCOUNT_CALLS=0
ORDER_CALLS=0
KIS_LIVE_ORDER_CALLS=0
GDELT_OUTBOUND_CALLS=0
CODEX_SECURITY_DEEP_SCAN=NOT_RUN_USER_SCOPED_OUT
```
