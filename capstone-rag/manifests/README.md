# RAG manifests

추적 가능한 manifest만 둔다. 각 manifest는 schema version, producer, source revision,
content SHA-256, byte/row/chunk count, 생성 시각, 승인 packet과 provenance를 포함하고,
절대경로·credential·raw 질문·사용자 subject·원 provider payload는 포함하지 않는다.

- `s4-2a-five-card-corpus.v1.json`: S4.2A exact 공식 5-card PoC membership과
  deterministic generation identity
- `s4-7b-project-source-cards-30.v1.json`: 금융공학 15개와 공식자료/API 15개를
  합친 exact project corpus membership, per-card canonical hash, access/license
  decision, parser/chunker/tokenizer identity와 ordered-pair corpus hash
- `s4-7c-project-source-cards-30-external.v1.json`: S4.7B exact membership/body를 유지한
  새 external-processing profile, 30개 license/consent receipt, old/new hash 관계와
  raw/reference evidence outbound 0 경계
- `s4-7d-oa140-release.v1.json`: S4.7D OA release candidate. 14개 curriculum track
  각각 8개, 총 112개 source의 fixed HTTPS locator와 raw SHA-256만 추적한다. OA 원문,
  추출 text, embedding은 재배포하지 않고 각 설치자가 공식 원천에서 다시 검증·구축한다.
- `s4-7d-oa140-curriculum-map.v1.md`: 위 release candidate의 track 순서, 학습 목표,
  대표 질문과 설치/검증 경계를 설명하는 공개 curriculum map
- `s4-7d-oa140-checksums.sha256`: OA112 metadata artifact 3개의 file-level SHA-256
- `s4-7d-oa140-distribution.v1.json`: GitHub Release와 Hugging Face Dataset에 동일 digest로
  게시할 metadata-only artifact set. credential이 없으면 publication status는
  `READY_NOT_PUBLISHED_NO_CREDENTIAL`로 남긴다.
