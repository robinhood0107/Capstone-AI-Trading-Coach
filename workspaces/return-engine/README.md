# return-engine

담당: 팀원 B / P1 full-app v2 integration owner

README-only placeholder 경계는 `HISTORICAL_SUPERSEDED`다. 수신 원본은 ignored
`dev/upstream-intake/<manifest-sha256>`에 보존하고, 검토·보완된 one-shot production source와 테스트만
이 workspace에 승격한다. 현재 수신본은 dependency lock, Dockerfile, 테스트, feature/scaler/model
재현 manifest와 Decision snapshot adapter가 없어 `TEAM_B_REAL_ARTIFACT=BLOCKED`다. 포함된 pickle 기반
`.pth`는 신뢰 가능한 provenance와 data-only 또는 restricted loader 경계 전에는 실행·커밋하지 않는다.

예상 구조 (최종 프로젝트 명세서 6.1):

```
src/
  data/            # Decision Platform이 제공한 계약 price/news/macro snapshot·artifact consumer
  features/        # technical/news/macro feature builder
  lstm/            # dataset, model, train, predict
  rule_baseline/   # MA/RSI/MACD/momentum/mean-reversion strategy
  backtest_core/   # execution simulator, cost/slippage, metrics
  artifact_export/ # contract-compliant artifact writer
```

KIS outbound 호출과 계좌/appkey 단위 유량 조정은 Decision Platform의 단일 owner다. Return Engine은 독립 KIS client/loader를 만들지 않고 계약된 snapshot·artifact/API만 소비해 팀 workspace별 호출 합산 초과를 막는다.

완료 요구사항은 [Team B 완료 요청서](../../docs/decision-platform/P1_TEAM_B_RETURN_ENGINE_완료_요청서.md)를
따른다. 실제 artifact가 없으면 synthetic 학습이나 기존 `.pth` 추론으로 `REAL` 상태를 만들지 않는다.
