# P1 Team B Return Engine 완료 요청서

## 현재 상태

수신본 30개 파일은 원본 해시를 보존해 intake했다. dependency lock, Dockerfile, 테스트, Decision snapshot
adapter, feature/scaler/model 재현 manifest가 없고 기존 코드는 `yfinance` 다운로드와 오늘 날짜에 의존한다.
따라서 현재 `TEAM_B_REAL_ARTIFACT=BLOCKED`이며 포함된 `.pth`나 JSON을 REAL artifact로 승격하지 않는다.

## 완료 체크리스트

- Python/Torch exact dependency lock, linux/amd64 Dockerfile과 SBOM
- model/scaler format, feature exact order, window, split, seed, config, source snapshot SHA와 code SHA manifest
- `yfinance`와 모든 provider client 제거, Decision Platform sanitized snapshot one-shot adapter만 사용
- XKRX 권위에서 다음 session 예측일을 파생하고 return을 `forecast / currentClose - 1`로 계산
- Baseline, Guide, Strict 세 전략과 비용·세금·slippage를 명시
- LSTM/rule signal, model report, backtest result, trade log, equity log와 상위 manifest를 exact schema로 생성
- pickle 입력은 신뢰 root/provenance가 없으면 거부하고 가능하면 data-only model format 사용
- unit, schema/contract, golden, integration, one-shot Compose E2E PASS
- 입력 snapshot을 바꾸지 않은 재실행은 byte-stable 또는 명시된 deterministic tolerance 안에서 일치
- raw provider data, cache, `.pyc`, local output과 credential이 Git/image/release archive에 없음

이 체크리스트가 전부 증명되기 전에는 새 모델을 대신 학습하거나 synthetic output으로 hard gate를
통과시키지 않는다.
