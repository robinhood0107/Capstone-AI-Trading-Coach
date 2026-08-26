# P1 Return artifact importer and projection

## KR

`p1-return-engine-artifact-manifest.v2` exact-10 bundle을 Owner가 검증·보관·투영하는 runtime을 추가한다.

- approved-root regular file만 읽고 symlink, hardlink, path traversal, extra/missing file을 거부한다.
- JSON closed field, Parquet semantic schema, safetensors exact tensor ABI/finite, exact-31/XKRX를 검증한다.
- forecast와 고정 35bps trade return, equity 기반 return/MDD/Sharpe/trade count를 독립 재계산한다.
- 검증된 bytes를 owner-private content-addressed archive에 manifest-last로 게시한다.
- V88 function-only worker 경계가 bundle, exact-62 Signal component, Model Evaluation, Backtest,
  ADMIN Ingest Status를 한 transaction으로 게시한다.
- 같은 bundle packet은 `REPLAYED`, 같은 `runId`의 다른 bundle은 conflict이며 실패 transaction은 pointer 0이다.
- synthetic Signal은 명시적 test profile에서만 보이고 production 기본값에서는 all-ABSTAIN이다.
- sealed public Seed의 V87 manifest bytes는 보존하며 additive V88 target만 forward-compatible하게 인정한다.

provider/account/balance/order/Vertex/GDELT/KIS Live physical call은 0이다. 실제 Team B artifact와
production runtime pointer authority는 이 변경으로 충족되지 않는다.

## EN

Adds the Owner runtime that validates, archives, and atomically projects the exact-ten Return Engine v2
bundle. Validation covers filesystem safety, closed JSON and Parquet semantics, fixed safetensors ABI,
finite values, exact-31/XKRX membership, and independent forecast/backtest arithmetic. V88 exposes only a
function-level worker import and bounded application reads. Synthetic signals require an explicit test
profile and remain invisible to the production pointer. The sealed V87 public Seed remains byte-stable.
