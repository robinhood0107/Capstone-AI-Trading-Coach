# 외부 artifact 수신 절차

현재 상태는 [공개 상태 권위](../README.md)를 따른다. 이 문서는 Return Engine의 실제 artifact가 전달된
뒤 수행할 검증 절차만 정의하며 P1 완료 상태를 따로 선언하지 않는다.

## 수신과 검증

1. bundle을 repository 밖 owner-private read-only 위치에 둔다. 원본을 Git이나 tracked `artifacts/`에
   커밋하지 않는다.
2. manifest의 producer, schema version, content hash, 기간, universe, 비용 설정을 검증한다. 누락값을
   추정하거나 보충하지 않는다.
3. `shared-docs/backtest_config.yaml`과 비용 조건을 확인한 뒤 기존 async ingest를 사용한다.
4. trade/equity source에서 지표를 독립 재계산한다. 제공된 summary를 재검증 결과로 재포장하지 않는다.
5. Baseline, Guide, Strict 세 결과를 `shared-docs/metrics_definitions.md`의 정의와 허용오차로 비교한다.
6. 불일치는 정의 차이와 구현 결함으로 나누고, 결함이면 focused regression을 추가한다.
7. content hash, 재계산, DB/Kafka projection parity, test, secret/account/PII scan이 모두 통과한 뒤에만
   중앙 상태 문서를 별도 docs-only closure PR에서 갱신한다.

## 금지 사항

- synthetic projection을 실제 artifact나 투자 성과로 승격하지 않는다.
- fixture에 Return Engine producer identity를 붙이지 않는다.
- summary만으로 수익률, Sharpe, MDD를 검증했다고 쓰지 않는다.
- 허용오차 밖 결과를 반올림하거나 기준 변경으로 통과시키지 않는다.
- raw 거래자료, 계좌정보, provider payload를 evidence에 포함하지 않는다.

최종 evidence에는 content-free manifest/hash, exact config hash, 독립 재계산 표, 정의/결함 판정,
projection parity와 실행한 검증 명령만 포함한다.
