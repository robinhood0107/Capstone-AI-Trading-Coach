# Team B Return Engine handoff

## 1. 최종 프로그램 목표

Owner가 봉인한 exact-31 가격 입력에서 재현 가능한 LSTM과 규칙 baseline 결과를 만들고, 주문 권한 없는
exact-10 artifact bundle로 전달합니다.

## 2. Owner가 이미 준비한 것

sealed input pack, fixed price-only ABI, semantic schema, synthetic golden, hostile-input-safe importer와
restricted GHCR digest/SBOM/provenance/signature intake 계약이 준비돼 있습니다.

## 3. 수정할 것

`workspaces/return-engine/` 안에서만 train-only scaler, global time split, leakage test, fixed 35bps 비교,
Baseline/Guide/Strict replay, one-shot train과 one-shot daily inference를 완성합니다. 기존 preview 소스와
PTH는 보존하고 실제 경로를 분리합니다.

## 4. 실행 명령

```bash
cd workspaces/return-engine
uv sync --frozen
uv run pytest -q
uv run python -m return_engine --help
```

Owner가 전달한 input pack 경로와 manifest SHA-256을 실제 실행 인자로 사용하며 다른 입력을 섞지 않습니다.

```bash
./capstone artifact validate <output-directory> --manifest-sha256 <manifest-sha256>
```

## 5. 완료 테스트

exact-31, exact-10, 두 번 실행 byte determinism, semantic schema, 독립 metric 재계산, input-pack binding,
provider/Spring/account/order call 0과 `mockRuntimeEligible=true`, `furtherTuningRequired=false`를 증명합니다.

## 6. 제출할 파일·commit·OCI digest

PR URL, commit SHA, lock/Dockerfile SHA-256, input/output manifest, exact-10 file hash와 두 실행 비교 결과를
제출합니다. restricted OCI packaging, SBOM, provenance, signature와 digest 검증은 Owner가 수행합니다.

## 7. 하지 말아야 할 것

뉴스·Vertex·GDELT·Spring·계좌·주문을 호출하거나 feature로 사용하지 않습니다. raw provider data,
출처 없는 pickle, mutable OCI tag, 주문 권한, synthetic 결과의 실제 성과 승격을 추가하지 않습니다.
