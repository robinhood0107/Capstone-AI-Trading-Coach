# Hugging Face model root

승인 뒤 내려받을 `BAAI/bge-m3`의 repository-local model root다. 실제 tokenizer, ONNX
graph, external data, cache와 임시 download는 대용량·공급망 payload이므로 Git에 넣지 않고,
`manifests/`의 pinned repository, immutable revision, 파일별 SHA-256, license, producer와
검증 결과만 추적한다.

활성화 조건:

1. 별도 download packet의 exact 승인
2. immutable Hugging Face revision과 expected file allowlist
3. `trust_remote_code=false`
4. `ONNX_DATA_ONLY`; pickle/joblib/Python object deserialization 0
5. regular-file, symlink/hardlink 거부, byte/count 상한
6. offline hash/license/model-card 검증
7. CPU thread/memory/time bound와 golden embedding fixture 통과

이 경로가 존재해도 download나 model activation이 승인된 것은 아니다.
