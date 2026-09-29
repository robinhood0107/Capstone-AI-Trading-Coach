# DEMO fixture generation

The raw Yahoo Finance parquet and original collection receipt stay under the
local-only `private-reference/` tree. The repository contains only the
permission-attested, versioned fixture that the DEMO image needs.

Run with Python 3.12 and the pinned packages in `requirements-fixture.txt`:

```sh
python -m pip install -r workspaces/mars-demo/scripts/requirements-fixture.txt
python workspaces/mars-demo/scripts/build_calendar.py \
  --output workspaces/mars-demo/data/krx-calendar.v1.json
python workspaces/mars-demo/scripts/build_fixture.py \
  --prices private-reference/research/active-trading-20260921/long_history.parquet \
  --data-receipt private-reference/research/active-trading-20260921/data-receipt.json \
  --universe contracts/catalogs/p1-return-universe.v1.json \
  --calendar workspaces/mars-demo/data/krx-calendar.v1.json \
  --source-collected-at 2026-09-21T11:26:13+09:00 \
  --permission-attested-on 2026-09-29 \
  --output workspaces/mars-demo/data/scenario.v1.json
python workspaces/mars-demo/scripts/validate_fixture.py \
  workspaces/mars-demo/data/scenario.v1.json
```

The showcase portfolio deliberately selects the two strongest close-to-close
returns after the period ended. It is labeled `사후 구성 가상 사례` and is not
the strategy evaluation. The separate 20/50 SMA example uses only prior-session
closes to make next-session-open decisions and keeps no-action and losing days.

Costs are explicit assumptions: 1.5 bp commission and 10 bp slippage per side,
20 bp KOSPI sell tax, and 14% national dividend withholding. The example does
not model an individual's final/local dividend tax liability. The tax schedule
must be reviewed before a later fixture refresh.
