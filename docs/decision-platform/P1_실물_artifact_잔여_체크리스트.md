# P1 실물 artifact 잔여 체크리스트

## 현재 상태

```text
S8_1_FAKE_E2E_VERIFIED
S8_1_REAL_ARTIFACT_BLOCKED
P1_OVERALL=INCOMPLETE_EXTERNAL_ARTIFACT
S1_4X_ENTRY_BLOCKED_S8_1_REAL_ARTIFACT
```

현재 repository에는 Return Engine이 생성한 실물 bundle이 없다. synthetic bundle은 async ingest와
Dashboard projection 연결을 검증하는 용도일 뿐 Team B artifact, 실제 백테스트 또는 투자 성과가 아니다.

## Artifact 수신 뒤 수행할 절차

1. 전달받은 bundle을 owner-private read-only 위치에 배치한다. repository나 tracked `artifacts/`에 원본을
   커밋하지 않는다.
2. manifest의 producer, schema version, content hash, 기간, universe, 비용 설정을 검증한다. producer를
   추정하거나 누락값을 보충하지 않는다.
3. `shared-docs/backtest_config.yaml`과 비용 조건이 일치하는지 확인한 뒤 동일 async ingest를 수행한다.
4. trade/equity source에서 지표를 독립 재계산한다. 이미 제공된 summary를 재포장해 재검증으로 표시하지
   않는다.
5. Baseline/Guide/Strict 정확히 세 결과를 `shared-docs/metrics_definitions.md`의 정의와 허용오차로 대조한다.
6. 불일치는 먼저 정의 차이와 구현 버그로 이분한다. 정의가 다르면 어느 쪽이 SSOT인지 명시하고, 버그면
   focused regression을 추가한다.
7. hash, 재계산 표, 테스트, secret/PII scan이 모두 통과한 뒤에만 `REAL_ARTIFACT_VERIFIED`와 P1 상태를
   별도 승인 세션에서 갱신한다.

## 금지되는 승격

- synthetic projection을 `REAL_ARTIFACT`로 바꾸지 않는다.
- Return Engine producer identity를 Decision Platform fixture에 붙이지 않는다.
- summary만으로 수익률·Sharpe·MDD를 검증했다고 쓰지 않는다.
- 허용오차 밖 결과를 반올림하거나 기준 변경으로 통과시키지 않는다.
- 실제 artifact가 오기 전에 P1 완료나 S1.4X entry gate를 열지 않는다.

## 완료 evidence

최종 evidence에는 content-free manifest/hash, exact config hash, 독립 재계산 표, 정의/버그 판정,
DB/Kafka projection parity, 테스트 명령과 결과, secret/account/PII 0건 scan을 포함한다. raw 거래자료와
계좌·provider payload는 포함하지 않는다.
