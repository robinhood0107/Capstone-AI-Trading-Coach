# P1 Team B Return Engine 완료 요청서

## 아주 쉽게 말하면

> 현재 CSV·PTH·소스·JSON은 하나도 버리지 않고 preview로 보존했습니다. 이제 provider를 직접 부르지
> 않는 결정적 one-shot 엔진으로 정리하고, 명세의 exact artifact 10개와 manifest를 만들어 주세요.
> Spring REST API를 억지로 호출할 필요는 없습니다.

작업 위치는 `workspaces/return-engine/`입니다. 현재 수신 PTH는 SHA-256 확인 후
`weights_only=True`로만 읽으며, 결과에는 `LEGACY_RECEIVED_PREVIEW`, `realTeamB=false`가 붙습니다.
이 preview는 실물 Team B artifact가 아닙니다.

## 입력 계약

- 계약된 KIS 가격 snapshot/artifact
- ECOS macro snapshot
- 별도 승인됐을 때만 `news_sentiment_summary.v2`
- `yfinance`, KIS, ECOS 등 provider 직접 호출 0
- 실계좌·잔고·실주문·credential 0

## 구현할 계산

- Python 3.12와 PyTorch 2.13.0 CPU, `uv.lock`을 유지합니다.
- feature 순서, scaler, window, split, seed, source/config hash를 manifest에 기록합니다.
- 다음 예측일은 pinned XKRX calendar의 다음 session을 사용합니다.
- `forecastClose / currentClose - 1`을 예상 수익률로 사용합니다.
- `BASELINE`, `GUIDE`, `STRICT` 세 전략을 실행합니다.
- 수수료, 세금, slippage를 전략별 backtest에 실제 반영하고 값과 단위를 기록합니다.
- 같은 입력으로 one-shot을 두 번 실행했을 때 byte-stable하거나 승인된 deterministic tolerance 안에서
  같아야 합니다.

## exact artifact 10개

1. `model.safetensors`
2. `scaler.json`
3. `config.json`
4. `lstm_signals.parquet`
5. `rule_baseline_signals.parquet`
6. `backtest_result.json`
7. `trade_log.parquet`
8. `equity_log.parquet`
9. `golden_output.json`
10. `model_report.md`

최상위 manifest는 `p1-return-engine-artifact-manifest.v1`, evidence mode는 `REAL_TEAM_B`이고 위 10개
파일의 basename, size, SHA-256을 전부 묶어야 합니다. 현재 수신 PTH·CSV·legacy JSON과 `__pycache__`는
원본 receipt로 그대로 남겨도 되지만 REAL artifact의 provenance 근거로 둔갑시키면 안 됩니다.

## API 책임

Team B가 직접 호출해야 할 Spring REST API는 명세상 0개입니다. 파일 계약으로 출력한 뒤 owner가 아래를
검증합니다.

- `GET /api/v2/signals/{symbol}`
- `GET /api/v1/dashboard/model-evaluations/{runId}`
- `GET /api/v1/dashboard/backtests/{runId}`
- `GET /api/v1/artifacts/ingest-status`

## 완료 명령

```bash
uv sync --frozen
uv run --frozen pytest
docker build --platform linux/amd64 -t capstone-return-engine:p1-local .
docker run --rm --network none capstone-return-engine:p1-local
```

동일 입력으로 Docker one-shot을 두 번 실행하고 manifest·산출물을 비교합니다. owner는 trade/equity
log에서 수익률·Sharpe·MDD를 독립 재계산하고 `contracts/verify_p1_full_app_assets.py`로 manifest를
다시 검증합니다.

## 그대로 보내는 짧은 메시지

```text
최신 main을 받고 workspaces/return-engine/에서 작업해 주세요.
현재 받은 CSV/PTH/소스/JSON은 모두 preview receipt로 보존되어 있습니다. 삭제하지 말고, provider 호출
없이 exact artifact 10개와 p1-return-engine-artifact-manifest.v1을 만드는 결정적 Docker one-shot으로
완성해 주세요. Spring REST API를 새로 호출할 필요는 없습니다.
```
