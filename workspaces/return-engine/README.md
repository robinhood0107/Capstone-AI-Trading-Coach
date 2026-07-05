# return-engine

담당: 팀원 B

이 계정(개인 레포)에서는 구조 자리만 확보해 둔다. 실제 구현은 팀원 B의 워크스페이스에서 진행되며, 팀 공용 레포로 합류할 때 이 폴더에 병합한다. `.gitignore`에 의해 이 폴더의 `README.md`를 제외한 파일은 이 레포에 커밋되지 않는다.

예상 구조 (최종 프로젝트 명세서 6.1):

```
src/
  data/            # KIS price/news/ECOS loaders
  features/        # technical/news/macro feature builder
  lstm/            # dataset, model, train, predict
  rule_baseline/   # MA/RSI/MACD/momentum/mean-reversion strategy
  backtest_core/   # execution simulator, cost/slippage, metrics
  artifact_export/ # contract-compliant artifact writer
```
