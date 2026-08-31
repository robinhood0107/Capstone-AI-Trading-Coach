# P1 Automation 시장데이터 V110 운영 가이드

## 현재 상태

V110은 자동운용용 과거 OHLCV를 안전하게 계획·검증·적재하고 ATR runtime이 최소권한으로 읽는
기반이다. 이 구현만으로 외부 API가 자동 활성화되거나 `market_data_bars`가 채워지지는 않는다.
현재 DB가 0행이면 `inventory`는 `EMPTY`를 반환하고 이후 Automation V3 arm은 닫힌 상태로 남는다.

기존 exact-7,218 source의 `market-data-seed.v1` archive가 있으면 그 provider-free adoption을 먼저
사용한다. 없을 때만 current exact-31·최근 1,260 XKRX session bootstrap을 별도 승인한다.

## provider-free 명령

```bash
./capstone market-data inventory

./capstone market-data universe refresh \
  --as-of 2026-08-31 \
  --output-root /절대경로/새-universe-root

./capstone market-data bootstrap plan \
  --universe-manifest /절대경로/universe-manifest.json \
  --end-session 2026-08-31

./capstone market-data bootstrap run \
  --packet /절대경로/approved-packet.json \
  --universe-manifest /절대경로/universe-manifest.json \
  --end-session 2026-08-31 \
  --output-root /절대경로/새-archive-root

# 장전 ATR readiness용 bounded quick horizon (exact-31 × 100 sessions, KIS cap 31)
./capstone market-data bootstrap plan \
  --universe-manifest /절대경로/universe-manifest.json \
  --end-session 2026-08-31 --session-count 100

./capstone market-data bootstrap validate /절대경로/archive \
  --manifest-sha256 <manifest-file-sha256>

./capstone market-data bootstrap stage /절대경로/archive \
  --manifest-sha256 <manifest-file-sha256>
```

- `inventory`, `plan`, `validate`, `stage` 자체는 provider 호출 0이다.
- `universe refresh`는 KRX KOSPI·KOSDAQ 일별매매정보를 각 1회, 합계 2회 조회하고 retry는 0이다.
- `stage`는 archive 전체를 검증한 뒤 writer DB를 열며 manifest·bars·universe를 한 transaction에
  적재한다.
- 기본 horizon은 1,260세션이다. `--session-count 100` quick horizon은 ATR 최소 23행을 넘기는
  장전 readiness용이며 full-1,260 완료로 보고하지 않는다. session count는 `23..1260`만 허용하고
  KIS cap은 exact-31 window 수에서 유도한다.
- 같은 manifest 재실행은 `NO_OP`이고 충돌 row/hash는 전체 rollback이다.
- runtime reader는 `market_data_bars`를 직접 읽지 못한다.

## 실제 read-only bootstrap 경계

Python의 `automation-market-data run`은 아래 값이 모두 맞을 때만 KIS read-only client를 만든다.

- `P1_AUTOMATION_MARKET_BOOTSTRAP_ENABLED=true`
- mode `0600` canonical execution packet의 plan SHA·KIS mode·provider cap 일치
- bounded approval ID
- exact cap: KIS daily 403, token 1, KRX membership 최대 5, retry 0

현재 mock-only 배포는 packet의 `kisMode=mock`과 기존 mock API key/secret만 사용한다. collector secret에는
계좌번호·order reference가 들어가지 않고 시세조회 interface만 존재한다. live mode는 별도 read-only
credential이 실제로 준비된 배포에서만 사용할 수 있다. `./capstone`은 승인 packet 없이 provider
socket을 열지 않는다.

## 일일 수집

- fixture coordinator의 기본값은 계속 OFF다.
- live-readonly transport도 `P1_DATA_ONLY_COLLECTOR_ENABLED=false`가 기본이다.
- 활성화 후에도 exact 38 normal 또는 41 month-boundary operation을 한 번씩만 수행하며 retry는 0이다.
- 성공 record는 private collection root에 봉인되고, 다음 XKRX 08:10에 기존 offline replay가
  complete set만 DB로 승격한다.
- 실패 뒤 자동 재호출·무제한 catch-up·모델 학습·pointer activation·주문은 없다.

## 테스트하는 방법

```bash
cd workspaces/decision-platform/python-services
uv lock --check
.venv/bin/ruff check app/data/market_data app/p1_owner/data_only_collector.py \
  app/p1_owner/data_only_collector_live.py tests/data/market_data \
  tests/p1_owner/test_data_only_collector.py
.venv/bin/mypy app/data/market_data app/p1_owner/data_only_collector.py \
  app/p1_owner/data_only_collector_live.py
TMPDIR=/tmp TEMP=/tmp TMP=/tmp uv run --frozen pytest -q \
  tests/data/market_data tests/p1_owner/test_data_only_collector.py

cd ../spring-api
./gradlew --no-daemon test \
  --tests com.capstone.decision.P1AutomationMarketDataV110MigrationContractTest \
  --tests com.capstone.decision.P1AutomationCurrentSessionV109MigrationContractTest \
  --tests com.capstone.decision.P1BaselineIntegrationTest
```

실제 API 호출 성공, historical replay, ATR 청산, KIS Mock 장중 검증은 이 문서의 완료 주장이 아니다.
