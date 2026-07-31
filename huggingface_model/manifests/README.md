# Model manifests

모델 payload 대신 검증 가능한 metadata만 추적한다. manifest는 profile ID,
upstream repository, immutable revision, filename allowlist, byte size, SHA-256, license,
artifact format, opset/runtime version과 검증 시각을 포함한다.

- `bge-m3-onnx-5617a9f61b028005a4858fdac845db406aefb181.v1.json`: 승인된 exact
  10-file ONNX packet과 graph contract
- `bge-m3-onnx-runtime-sbom.v1.json`: Python 3.12 CPU runtime의 exact dependency와
  wheel hash
