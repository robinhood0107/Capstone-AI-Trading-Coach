# RAG reports

평가 조건, manifest hash, 집계 지표와 curated/synthetic 실패 사례만 추적한다. 개인화
질문·원문 로그·credential fingerprint 원재료·외부 API request/response body는 제외한다.

- `s4-2a-five-card-poc-benchmark.v1.json`: network-off preliminary warm latency,
  stage별 percentile, fixed query/corpus hash와 active-pointer 불변식
- `s4-2b-batch-memory-benchmark.v1.json`: exact 30 corpus의 batch 16/32/64 peak RSS와
  batch 32 선택을 환경 fingerprint와 함께 고정한 local-only receipt
- `s4-2b-full-generation-benchmark.v1.json`: full generation/DB parity, 20회
  warmup·100회 measured stage percentile, top-5 hit와 bounded admin CAS 결과를 고정한
  최종 local benchmark. 이 결과는 대규모 production capacity 주장이 아니다.
- `s4-7c-external-generation.v1.json`: old/new exact-30 local BGE vector 동등성,
  retrieval non-regression, stale CAS rollback, old `DISABLED`/new `ACTIVE`와 exactly-one-active
  상태를 고정한 offline 전환 receipt. 외부 provider physical call은 0이다.
- `s4-7d-oa140-remote-hash-receipt.v1.json`: S4.7D OA112 release manifest의 112개
  fixed HTTPS download URL을 redirect 없이 다시 읽어 raw SHA-256과 byte 수만 확인한 receipt.
  원문·추출 text·embedding·provider body는 포함하지 않는다.
- `s4-5-fixture-evaluation.v1.json`: exact 60의 metric 분모·분자·gate·비식별 실패 question ID와
  provider physical call 0을 고정한 deterministic 평가 report
- `s4-5-provider-control-plane.v1.json`: Voyage one-shot plan과 Gemini Interactions DTO hash,
  packet 부재, outbound hard-disable, materialization/activation 0을 고정한 offline control receipt
