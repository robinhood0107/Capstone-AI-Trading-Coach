# DEMO fixture generation

The raw Yahoo Finance parquet and original collection receipt stay under the
local-only `private-reference/` tree. The repository contains only the
permission-attested, versioned fixture that the DEMO image needs.

Run with Python 3.12 and the pinned packages in `requirements-fixture.txt`:

```sh
python -m pip install -r workspaces/mars-demo/scripts/requirements-fixture.txt
python workspaces/mars-demo/scripts/build_calendar.py \
  --output workspaces/mars-demo/data/krx-calendar.v1.json
python workspaces/mars-demo/scripts/collect_increment.py \
  --base-parquet private-reference/research/active-trading-20260921/long_history.parquet \
  --base-receipt private-reference/research/active-trading-20260921/data-receipt.json \
  --universe contracts/catalogs/p1-return-universe.v1.json \
  --calendar workspaces/mars-demo/data/krx-calendar.v1.json \
  --start 2026-09-19 --end-exclusive 2026-09-30 \
  --output-dir private-reference/research/active-trading-20260930
python workspaces/mars-demo/scripts/build_fixture.py \
  --prices private-reference/research/active-trading-20260930/long_history.parquet \
  --data-receipt private-reference/research/active-trading-20260930/data-receipt.json \
  --universe contracts/catalogs/p1-return-universe.v1.json \
  --calendar workspaces/mars-demo/data/krx-calendar.v1.json \
  --source-collected-at 2026-09-30T11:58:26+09:00 \
  --permission-attested-on 2026-09-29 \
  --output workspaces/mars-demo/data/scenario.v1.json
python workspaces/mars-demo/scripts/validate_fixture.py \
  workspaces/mars-demo/data/scenario.v1.json
python workspaces/mars-demo/scripts/build_reports.py \
  --scenario workspaces/mars-demo/data/scenario.v1.json \
  --prices private-reference/research/active-trading-20260930/long_history.parquet \
  --predictions-root private-reference/research/active-trading-20260921/v2/lstm-w756-quarterly-calendar \
  --output workspaces/mars-demo/data/reports.v1.json
python workspaces/mars-demo/scripts/validate_reports.py \
  workspaces/mars-demo/data/reports.v1.json \
  workspaces/mars-demo/data/scenario.v1.json
```

The showcase portfolio deliberately selects the six strongest close-to-close
returns observed through 2026-09-18 after that selection period ended. Its
one-order-per-session dates include staged purchases, partial sales and two
full exits through 2026-09-18. The remaining holdings are marked through
2026-09-29 without changing those trade events. Individual sales include
gains and losses, while the final portfolio remains positive after modeled costs.
This is a `사후 구성 가상 사례`, not a strategy evaluation or real trading record. The
separate 20/50 SMA example uses only prior-session closes to make
next-session-open decisions and keeps no-action and losing days.

The current fixture's 29-session receipt starts with 10,000,000 KRW and ends
with 10,497,408 KRW (+497 bp after modeled costs): 4 open positions, 2 fully
closed positions, 11 orders/fills, 18 sessions without an order, and 5
sales (3 gains and 2 losses). Realized PnL is 338,657 KRW, unrealized PnL
is 158,429 KRW, and net dividend cash is 322 KRW. Their sum is the 497,408
KRW net increase. Commission totals 2,209 KRW, sale tax 11,187 KRW, assumed
slippage 14,718 KRW, and dividend withholding 53 KRW. The original parquet
SHA-256 is `b70b46b41012b640d8c81b9ade4f31d324e198c593441466ec20d1fa56625377`;
the extension SHA-256 is `8ab644e5b59ecd69735f90f8ebaf13febafd9b45fca0cdd6ec9b557e31199b40`.
The combined source SHA-256 is `a66f966751cb7ebb48cca564309cf7112934e52d1b348611d1012717b850d680`,
and the generated fixture SHA-256 is `f5fad9eea505d6c553e3e36ac1f508e804424c51c50eed730758741faec61e7c`.

`reports.v1.json` is built offline and packaged only with the DEMO image. Its
29-session Backtest report has complete Baseline, Guide, and Strict curves,
risk metrics, monthly returns, and cost-aware trade receipts. Baseline and
Guide execute the same SMA candidate schedule because Guide warnings do not
block orders. Strict rejects new buys when the prior five-session raw-close
return is below -3%; this fixed rule blocked two candidates in this period.
All three use the same initial capital, source bars, slippage, commission, and
sale tax. The 24-session model comparison covers 2026-08-18 through 2026-09-18,
the overlap where the recorded `w756-quarterly` LSTM predictions exist for
all 31 symbols. Both model arms buy the five highest prior-close scores at
the next open and sell at that session's close with whole shares and modeled
costs. LSTM predictions were produced with training data ending no later than
2026-06-30; missing later predictions are not manufactured. This short model
window is not evidence of long-term superiority. The prediction parquet files
and training inputs remain local-only; only derived report rows and SHA-256
receipts enter the DEMO image. The combined prediction-source SHA-256 is
`7a0cc2b56f655d61400c6eaf59237cf372db74a6e4f12eaa1d8ec86b2048678c`;
the report fixture SHA-256 is
`6ee8bfefe5707a2a3641e61fbcb769291569b23a0daa4ec3d5495397cf9ed911`.

Costs are explicit assumptions: 1.5 bp commission and 10 bp slippage per side,
20 bp KOSPI sell tax, and 14% national dividend withholding. The example does
not model an individual's final/local dividend tax liability. The tax schedule
must be reviewed before a later fixture refresh.
