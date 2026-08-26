# P1 Team B Return Engine 완료 요청서

## 팀원 B에게 한 문장으로 부탁할 내용

> 최신 `main`을 받은 뒤 `workspaces/return-engine/` 안에서 provider를 직접 호출하지 않는 one-shot
> Return Engine을 완성하고, 같은 입력으로 같은 실물 artifact를 다시 만들 수 있는 source·lockfile·
> Dockerfile·테스트·재현 manifest를 한 PR로 올려 주세요.

## 언제 전달하는 문서인가

이 요청서는 [우리 쪽 선행 체크리스트](P1_OWNER_선행_완료_체크리스트.md)가 `OWNER_HANDOFF_READY=TRUE`가
된 뒤 전달한다. 현재 포함된 수신본은 dependency lock, Dockerfile, 테스트와 신뢰 가능한 model provenance가
없어 `TEAM_B_REAL_ARTIFACT=BLOCKED`다.

## Team B가 시작하는 방법

```bash
git switch main
git pull --ff-only origin main
git switch -c feature/p1-team-b-return-engine
```

작업 위치는 `workspaces/return-engine/`이다. `yfinance`, KIS, ECOS 등 외부 provider를 Return Engine에서
직접 호출하지 않는다. 입력은 Decision Platform이 제공하는 sanitized snapshot만 사용한다.

## 해 달라고 할 일

1. Python/Torch exact dependency lock과 linux/amd64 Dockerfile을 추가한다.
2. 입력 snapshot, feature 순서, scaler, window, split, seed, config와 training code hash를 manifest에
   기록한다. 오늘 날짜나 실행 PC 경로에 따라 결과가 달라지면 안 된다.
3. 다음 예측일은 repository의 XKRX calendar 권위에서 구한다.
4. 예상 수익률은 정확히 `forecastClose / currentClose - 1`로 계산한다.
5. `BASELINE`, `GUIDE`, `STRICT` 세 전략을 모두 실행하고 각 전략의 거래비용, 세금, slippage를 기록한다.
6. 아래 exact basename 10개를 생성한다.
   - `model.safetensors`
   - `scaler.json`
   - `config.json`
   - `lstm_signals.parquet`
   - `rule_baseline_signals.parquet`
   - `backtest_result.json`
   - `trade_log.parquet`
   - `equity_log.parquet`
   - `golden_output.json`
   - `model_report.md`
7. 최상위 manifest는 `p1-return-engine-artifact-manifest.v1` 계약과 `REAL_TEAM_B` evidence mode를
   사용하고, 위 10개 파일의 크기와 SHA-256을 모두 묶는다.
8. unit, schema/contract, golden, integration, backtest, one-shot Docker E2E를 통과시킨다.

기존 provenance 없는 pickle `.pth`, raw CSV, cache 또는 synthetic output을 REAL artifact로 바꾸지 않는다.
실계좌, 주문, 잔고 또는 credential은 입력과 출력 모두 0개다.

## PR에 반드시 들어갈 것

- `workspaces/return-engine/`의 production source와 tests
- exact dependency lockfile과 Dockerfile
- sanitized 입력 snapshot의 생성 계약 또는 content hash
- model/scaler/feature/split/config의 재현 정보
- 한 명령으로 artifact 10개와 manifest를 만드는 one-shot command
- golden output과 실제 테스트 결과
- SBOM 생성 명령과 raw/provider/credential 0건 확인

로컬 `artifacts/`, `output/`, `raw/`, cache와 `.pyc`는 Git에서 ignore되므로 그 폴더만 복사해 보내면
`git pull` 재현이 되지 않는다. PR에는 artifact를 다시 만드는 source와 입력 계약이 반드시 있어야 한다.
최종 배포에서는 검증된 artifact와 Return Engine을 digest-pinned OCI image에 넣고 Compose가 받는다.

## Team B가 완료 여부를 확인하는 명령

README에 실제 명령을 한 줄씩 적고, 새 checkout에서 다음 흐름이 성공하게 한다.

```bash
uv sync --frozen
uv run --frozen pytest
docker build --platform linux/amd64 -t capstone-return-engine:p1-local .
docker run --rm capstone-return-engine:p1-local --help
```

그다음 one-shot 명령으로 artifact를 두 번 만들고, 입력을 바꾸지 않았을 때 byte-stable하거나 manifest에
적은 deterministic tolerance 안에서 같은지 확인한다.

## 우리가 PR과 실물 bundle을 받은 뒤 확인할 것

1. 원본 receipt와 source commit을 확인하고 secret, symlink, cache, raw provider data를 검사한다.
2. Team B 명령으로 clean build·테스트·artifact 생성을 다시 수행한다.
3. 아래 검증기를 실행한다.

```bash
python3 contracts/verify_p1_full_app_assets.py \
  --return-manifest '<검증용 bundle>/p1-return-engine-manifest.v1.json' \
  --repository-root .
```

4. trade/equity log에서 수익률·Sharpe·MDD를 독립 재계산한다.
5. 검증된 image digest와 content-free artifact manifest hash를 full Compose/release manifest에 결속한다.
6. 새 checkout에서 `./capstone doctor`, `./capstone install`, `./capstone start`를 다시 검증한다.

Team B가 보내 준 summary만 있거나 10개 실물 중 하나라도 없으면 `TEAM_B_REAL_ARTIFACT=BLOCKED`를 유지한다.

## Team B에게 그대로 보내는 짧은 메시지

```text
최신 main을 pull한 뒤 workspaces/return-engine/만 작업해 주세요.
provider를 직접 부르지 않는 one-shot Return Engine source, lockfile, production Dockerfile, 테스트와
재현 manifest를 한 PR로 주세요. yfinance, raw provider data, .pth pickle, cache, credential은 보내지 마세요.
자세한 exact 파일과 완료 기준은 docs/decision-platform/P1_TEAM_B_RETURN_ENGINE_완료_요청서.md에 있습니다.
```
