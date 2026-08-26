# P1 Owner-First Phase A 계약 잠금 / Contract lock

## KR

Team A/B 결과가 도착한 뒤 Owner adapter나 계약 완화를 추가하지 않도록, 외부 팀 요청 전에 필요한
wire·모델 ABI·자동운용 상태를 contract-only로 고정한다.

- `p1-return-engine-input-pack.v1`: exact-31, XKRX correction generation, 최소 3년 daily OHLCV,
  symbol coverage, global time split, exact feature order, fixed model/cost/Risk evaluator binding
- `p1-return-engine-artifact-manifest.v2`: exact 10개 결과 파일과 semantic schema, producer/input hash,
  `REAL_TEAM_B | SYNTHETIC_GOLDEN` truth marker
- exact 10 semantic schema: safetensors/scaler/config/LSTM signal/rule signal/backtest/trade/equity/
  golden output/model report
- `p1-scenario-replay-policy.v1`: 동일 35bps 비용으로 BASELINE/GUIDE/STRICT를 재생하며 Team B가
  RiskEngine을 재구현하지 않음
- `vertex-news-veto.v1`: grounding 기반 신규 BUY veto 전용. SELL/quantity/price/orderType 권한 없음
- `automation-control|run|position|event.v1`, `journal.v1`: append-only·owner-scoped runtime의 선행 wire
- `p1-lightgbm-research-evaluation.v1`: research-only disclosure, production authority 0
- `p1-full-app-release-contract.v3`와 manifest v3: exact 16 hard gate
- Automation/Journal 8개 operation additive OpenAPI. root OpenAPI exact-56 승격은 runtime PR에서 수행

historical v1/v2 release, Return manifest v1, `news_sentiment_summary.v2`, LICENSE bytes를 변경하지 않는다.
`return-engine-news-feature.v1`은 만들지 않는다. 이 변경의 provider/account/order/GDELT/KIS Live call은 0이다.

## EN

This contract-only change freezes every Owner-facing wire and model ABI needed before Team A/B delivery so
the Owner will not add an adapter or relax contracts after team results arrive.

- Locks the exact-31 input pack, fixed price-only LSTM ABI, train-only scaling, global time split, and the
  identical conservative 35 bps comparison cost.
- Locks the exact ten-file Return Engine manifest v2 and one semantic schema per file.
- Separates synthetic golden evidence from real Team B evidence without performance or order authority.
- Assigns news to the grounded Vertex new-BUY veto lane and keeps Team B news/GDELT/Vertex calls at zero.
- Locks automation, journal, and LightGBM research-only contracts plus the sixteen-gate release v3 contract.
- Publishes the eight Automation/Journal operations as an additive OpenAPI contract; the root exact-56
  transition belongs to the later runtime PR.

Historical release v1/v2 bytes, Return manifest v1, `news_sentiment_summary.v2`, and LICENSE remain unchanged.
This change performs zero provider, account, balance, order, GDELT, or KIS Live calls.
