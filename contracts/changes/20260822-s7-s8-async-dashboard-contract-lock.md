# S7/S8 async·Dashboard 계약 잠금

## 결정

- 내부 async event는 reference-only closed envelope와 exact topic catalog를 사용한다.
- 공개 상태 조회는 async job 단건·목록, stream metric, artifact ingest status 네 경로다.
- Dashboard handoff는 model evaluation, backtest, persisted risk result, persisted RAG sources 네 경로다.
- `loading`은 client-only이고 서버는 `READY | EMPTY | STALE` 또는 표준 HTTP error를 반환한다.
- 성과 보장 오인을 막기 위해 v1 `performanceClaimAllowed`는 항상 `false`다.

## 안전 경계

cross-market endpoint, scheduler, overlay, rule 15와 Decision/RAG/Signal payload 변경은 없다.
synthetic 결과는 `demo_` namespace와 `SYNTHETIC_DEMO` evidence mode로만 표시한다. 외부 workspace
구현, provider/live/account/order 호출, S1.4 production 교체는 이 변경의 권한이 아니다.

## 생성 권위

`contracts/generate_s7_s8_contracts.py`가 schema, positive/negative fixture, exact topic catalog와
독립 OpenAPI contract를 생성한다. Spring 전체 OpenAPI는 runtime controller가 이 계약을 구현한 뒤
기존 drift gate로 별도 검증한다.
