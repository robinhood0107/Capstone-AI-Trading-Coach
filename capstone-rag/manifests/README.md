# RAG manifests

추적 가능한 manifest만 둔다. 각 manifest는 schema version, producer, source revision,
content SHA-256, byte/row/chunk count, 생성 시각, 승인 packet과 provenance를 포함하고,
절대경로·credential·raw 질문·사용자 subject·원 provider payload는 포함하지 않는다.

- `s4-2a-five-card-corpus.v1.json`: S4.2A exact 공식 5-card PoC membership과
  deterministic generation identity
- `s4-7b-project-source-cards-30.v1.json`: 금융공학 15개와 공식자료/API 15개를
  합친 exact project corpus membership, per-card canonical hash, access/license
  decision, parser/chunker/tokenizer identity와 ordered-pair corpus hash
