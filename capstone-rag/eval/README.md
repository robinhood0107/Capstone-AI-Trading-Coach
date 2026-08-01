# RAG evaluation data

공개·합성 질문과 비식별 gold metadata만 추적한다. 실사용자 자유 질문, 계좌·보유종목·주문
정보, secret 또는 외부 provider raw 응답은 저장하지 않는다.

- `s4-2b-30-card-smoke.v1.json`: exact 30 frozen project corpus를 대상으로 하는
  비식별 합성 10문항과 expected source top-5 metadata
- `s4-5-evaluation-60.v1.json`: S4.7C exact-30에 결속된 공개·합성 gold 50과 adversarial
  10의 question ID, category, provenance, gold source, answer/block, assumption과 authorized
  citation set. 실사용자 자유 질문과 owner/account/session identifier는 0이다.
