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
