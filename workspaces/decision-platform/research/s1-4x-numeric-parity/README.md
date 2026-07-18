# S1.4X Scala/Haskell numeric parity research

S1.4X는 S1.4 production NumPy 11개 함수와 S1.4R NumPy/JAX 9개 함수의 수치·오류
계약을 Scala와 Haskell의 독립 process로 재현하기 위한 비생산 연구다. 이 subtree는
production `RiskEngine`, 공개 API, root `contracts/` 또는 다른 팀 workspace와 연결되지
않는다.

## 현재 gate

이 tree는 **Gate 1 neutral fixture freeze**만 담는다.

- 언어 중립 JSON/binary exchange schema와 20개 함수·32개 stable error registry
- upstream Python/NumPy/JAX reference hash lock
- canonical small/invalid/property fixture와 deterministic large-fixture generator
- Python oracle, contract/provenance/environment validator
- capability/property/safety/compatibility 정책
- 6개 family, 89개 case, 3회 반복의 사전 동결 benchmark plan
- PR과 `main` push에서 모두 실행되는 contract correctness workflow

Gate 1이 `main`에 병합되기 전에는 `scala/`, `haskell/`, 언어별 correctness workflow와
candidate report를 추적하지 않는다. 이 Gate에서는 benchmark timing도 실행하지 않는다.

## 경계

- Python/NumPy/JAX source와 fixture는 읽기 전용 oracle이다.
- candidate가 Python/JAX를 실행·embed하거나 FFI/JNI/native extension, HTTP, gRPC로
  계산을 위임하는 것은 금지한다.
- canonical success는 finite Float64만 허용하고 `-0.0`을 `0.0`으로 정규화한다.
- small/paper case는 `rtol=1e-12`, `atol=1e-12`, large/property case는
  `rtol=1e-10`, `atol=1e-12`를 사용한다.
- generated `*.f64le`, local doctor evidence, tool output과 benchmark artifact는
  path-scoped ignore 대상이며 계약 입력이 아니다.

## 재현

repo root에서 실행한다.

```bash
S1_4X=workspaces/decision-platform/research/s1-4x-numeric-parity
CACHE_ROOT="$HOME/.cache/s1-4x"
mkdir -p "$CACHE_ROOT/tmp" "$CACHE_ROOT/uv"
export TMPDIR="$CACHE_ROOT/tmp"
export TEMP="$TMPDIR"
export TMP="$TMPDIR"
export UV_CACHE_DIR="$CACHE_ROOT/uv"
export UV_PYTHON=3.12.13

uv sync --frozen --project "$S1_4X/oracle"

uv run --frozen --project "$S1_4X/oracle" \
  python "$S1_4X/oracle/generate_large_fixtures.py" --check

uv run --frozen --project "$S1_4X/oracle" \
  python "$S1_4X/oracle/capture_reference_results.py" --check

uv run --frozen --project "$S1_4X/oracle" \
  python "$S1_4X/oracle/capture_reference_results.py" \
    --request "$S1_4X/contract/fixtures/invalid/semantic-errors.v1.json" \
    --output "$S1_4X/contract/fixtures/invalid/semantic-errors.expected.v1.json" \
    --check

uv run --frozen --project "$S1_4X/oracle" \
  python "$S1_4X/benchmarks/render_benchmark_plan.py" --check

uv run --frozen --project "$S1_4X/oracle" \
  python "$S1_4X/benchmarks/run_rotated_blocks.py" validate-plan

uv run --frozen --project "$S1_4X/oracle" \
  python "$S1_4X/oracle/validate_contract.py" --check-all

uv run --frozen --project "$S1_4X/oracle" \
  pytest -q "$S1_4X/oracle/tests" "$S1_4X/benchmarks/tests"
```

`contract-manifest.v1.json`은 자기 자신을 제외한 immutable Gate 1 input의 hash
closure다. `referenceBaseCommit`은 S1.4X 파일이 생기기 전의 upstream commit이며,
향후 Gate 1 merge SHA를 lock에 다시 써 self-reference를 만들지 않는다. 최초 Gate 2
integration report가 immutable GitHub merge event의 Gate 1 SHA와 이 tree의
byte-identical 상태를 연결한다.

## 다음 승인

Gate 1 PR을 merge하지 않는다. review와 required checks가 끝난 뒤 사용자가 별도 exact
merge 승인을 보내야 한다. Gate 2 연속 실행은 병합된 `fixtureFreezeMergeSha`와 이
tree의 immutable SHA를 포함한 별도 readiness packet을 소비한다.
