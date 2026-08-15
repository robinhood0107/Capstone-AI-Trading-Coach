# S1.4X Scala/Haskell numeric parity research

S1.4X는 S1.4 production NumPy 11개 함수와 S1.4R NumPy/JAX 9개 함수의 수치·오류
계약을 Scala와 Haskell의 독립 process로 재현하기 위한 비생산 연구다. 이 subtree는
production `RiskEngine`, 공개 API, root `contracts/` 또는 다른 팀 workspace와 연결되지
않는다.

## 현재 gate

이 tree의 **Gate 1 neutral fixture freeze**는 PR #28로 `main`에 병합되었고, 현재는
그 frozen contract와 후속 production runtime dependency amendment를 함께 재현한다.

- 언어 중립 JSON/binary exchange schema와 20개 함수·32개 stable error registry
- upstream Python/NumPy/JAX source·fixture byte lock, project runtime projection과 `uv.lock` lock
- canonical small/invalid/property fixture와 deterministic large-fixture generator
- Python oracle, contract/provenance/environment validator
- capability/property/safety/compatibility 정책
- 6개 family, 89개 case, 3회 반복의 사전 동결 benchmark plan
- PR과 `main` push에서 모두 실행되는 contract correctness workflow

Gate 1 병합 전에 `scala/`, `haskell/`, 언어별 correctness workflow와 candidate report를
추적하지 않던 경계는 이력으로 완료됐다. runtime dependency amendment는 candidate
source/report를 추가하지 않고 benchmark timing도 실행하지 않는다.

## 경계

- Python/NumPy/JAX source와 fixture는 읽기 전용 oracle이다.
- candidate가 Python/JAX를 실행·embed하거나 FFI/JNI/native extension, HTTP, gRPC로
  계산을 위임하는 것은 금지한다.
- canonical success는 finite Float64만 허용하고 `-0.0`을 `0.0`으로 정규화한다.
- small/paper case는 `rtol=1e-12`, `atol=1e-12`, large/property case는
  `rtol=1e-10`, `atol=1e-12`를 사용한다.
- production/research `pyproject.toml`은 dependency, Python 범위, build-system,
  dependency-groups, `tool.uv`, `tool.hatch.build`만 canonical projection으로 hash한다.
  `[project.scripts]`와 lint/test/type-check 설정은 제외하고 `uv.lock`은 byte-exact로 유지한다.
- tracked expected JSON bytes는 sidecar와 contract manifest로 exact hash-lock한다.
  `capture_reference_results.py --check`의 live 재생성은 libc `libm`의 ULP 차이를 숨기지
  않고 같은 typed tolerance로 판정하며, ID·순서·필드·정수·불리언·stable error는 exact다.
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
closure다. `referenceBaseCommit`은 frozen reference가 실제로 읽는 upstream runtime input을
담은 좁은 commit이다. 최초 freeze에서는 S1.4X 생성 전 upstream commit이었고, 승인된 runtime
dependency amendment에서는 dependency 파일만 바꾼 commit이 된다. reference refresh commit이나
향후 merge SHA를 가리켜 self-reference를 만들지 않는다. 최초 Gate 2 integration report가
immutable GitHub merge event의 Gate 1 SHA와 이 tree의 byte-identical 상태를 연결한다.

runtime dependency amendment PR의 병합은 dependency commit이 최종 `main` HEAD의 조상으로 남는
**merge commit 방식만** 허용한다. squash/rebase merge는 `referenceBaseCommit` 조상성을
없애 `main` push correctness check를 깨므로 금지한다. 승인된 방식 marker는
`METHOD-MERGE-COMMIT`이며, 실행 단계는 PR 번호와 검증한 head SHA를 결속해
`gh pr merge <pr-number> --merge --match-head-commit <head-sha>`를 사용한다.

## 승인된 runtime dependency amendment 재생성

S4.7B source-card v2 validator amendment는 production Python runtime dependency와
`uv.lock`만 바꾼 commit
`13b7b21a904fc37ce0947d5da2de7d04794e497a`를 새 reference input으로 승인했다. 이 commit의
자체 diff가 아래 두 파일뿐이고 최종 HEAD의 조상인지 먼저 확인한다.

```bash
set -euo pipefail
export TMPDIR=/tmp TEMP=/tmp TMP=/tmp
S47B_DEPENDENCY_SHA=13b7b21a904fc37ce0947d5da2de7d04794e497a
S47B_S1_4X=workspaces/decision-platform/research/s1-4x-numeric-parity
S47B_ORACLE="$S47B_S1_4X/oracle"
export S47B_DEPENDENCY_SHA S47B_S1_4X

git cat-file -e "$S47B_DEPENDENCY_SHA^{commit}"
git merge-base --is-ancestor "$S47B_DEPENDENCY_SHA" HEAD
diff -u \
  <(printf '%s\n' \
    workspaces/decision-platform/python-services/pyproject.toml \
    workspaces/decision-platform/python-services/uv.lock) \
  <(git diff-tree --no-commit-id --name-only -r "$S47B_DEPENDENCY_SHA" | LC_ALL=C sort)
```

reference lock writer는 현재 validator의 projection/tree helper를 그대로 사용한다. JSON 표시
형식은 유지하고 runtime 의미 hash만 재계산한다.

```bash
set -euo pipefail
PYTHONPATH="$S47B_ORACLE" uv run --frozen --project "$S47B_ORACLE" python - <<'PY'
import json
import os
from pathlib import Path

from oracle_common import (
    atomic_write_bytes,
    sha256_bytes,
    sorted_relative_files,
    strict_json_load,
)
from validate_contract import _reference_source_sha256, _reference_tree_manifest

repo = Path.cwd().resolve(strict=True)
lock_path = repo / os.environ["S47B_S1_4X"] / "contract/reference-lock.v1.json"
lock = strict_json_load(lock_path)
lock["referenceBaseCommit"] = os.environ["S47B_DEPENDENCY_SHA"]
source_roles_by_path = {
    source["path"]: source["role"] for source in lock["sources"]
}

for source in lock["sources"]:
    source["sha256"] = _reference_source_sha256(
        repo / source["path"],
        role=source["role"],
    )

for tree in lock["sourceTrees"]:
    root = repo / tree["root"]
    files = sorted_relative_files(root, tree["includeGlobs"])
    payload, entries = _reference_tree_manifest(
        root,
        files,
        root_relative=tree["root"],
        source_roles_by_path=source_roles_by_path,
    )
    tree["fileCount"] = len(entries)
    tree["canonicalManifestSha256"] = sha256_bytes(payload)
    tree["files"] = entries

rendered = (json.dumps(lock, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
atomic_write_bytes(lock_path, rendered)
PY

uv run --frozen --project "$S47B_ORACLE" \
  python "$S47B_S1_4X/benchmarks/render_benchmark_plan.py" --write

# manifest는 reference lock, oracle test, benchmark plan/sidecar가 모두 고정된 뒤 마지막에 쓴다.
uv run --frozen --project "$S47B_ORACLE" \
  python "$S47B_ORACLE/validate_contract.py" --write-manifest
```

그 뒤 이 문서의 재현 명령 전체를 실행한다. PR에서는 base SHA, `main` push에서는
push-before SHA를 diff base로 사용해 `shared-docs/metrics_definitions.md`, production
`financial_engineering` source/tests, S1.4R `src`/`tests`, S1.4X `contract/`(의도적
reference lock/manifest 제외), `oracle/`(추가된 validator regression test 제외),
`benchmarks/`(재생성된 plan/sidecar 제외)의 drift가 0임을 검증한다. 로컬에서는
같은 범위를 `origin/main` 대비로 확인한다. 이번 amendment의
reference lock SHA는
`c3261e0c6e7d074ab7099b9d050f646ee552e129da2e3b29319646df572cba87`,
benchmark plan SHA는
`5ca4beb69938371b47702e35eb6943ac85f8b5ba84e54017c572caa3871e7549`,
benchmark plan sidecar file SHA는
`75530891b5b78c4c2dc3d4512fc01991bf60cec7c92cb5ec18018dd5453466e1`,
contract manifest SHA는
`1884a130724a54041c80a37ecbdce6cc6cf088b9a89b824833a6c2585d0c939f`이며 numeric
source/fixture bytes는 바뀌지 않았다. dependency, lock,
reference/plan/manifest와 이 의미 설명은 한 rollback unit이며 일부만 cherry-pick/revert하지 않는다.

## 현재 연속 실행

Gate 1 병합은 PR #28에서 완료됐다. S4.7B contract-only PR은 required checks가 모두
통과한 검증 head를 merge commit 방식으로만 병합한다. squash/rebase는 허용하지 않는다.
이 amendment는 S4.7B source-card corpus freeze를 위한 선행 계약이며 S1.4X candidate
구현이나 benchmark 실행을 승인하지 않는다.

## S4.7D parser/OCR dependency amendment

S4.7D의 9-format parser와 OCR runtime dependency는
`bf8472dfcc5f9d883ca83bd461a62f254332b39f`에서 production `pyproject.toml`과
`uv.lock` 두 파일만으로 다시 승인했다. 이 변경은 S1.4X 수치 kernel·fixture·oracle을
바꾸지 않으며, 다음 재생성 산출물을 같은 rollback unit으로 취급한다.

- reference lock SHA-256: `d267c084592c14e6ab167e9a9afd370e45f1caf9a0959b7927b0d4a806fc5581`
- benchmark plan SHA-256: `a92769b47022a3e9972ca170b30221b9dc46ceeaef283e191bb1d7f570e8503f`
- benchmark plan sidecar file SHA-256: `03a6b9e5b4e856991c2ff4590674885d5596df89109a3fc06e4632f98590c67e`
- contract manifest SHA-256: `2eaf9bca0a29a8a683f6387be423af581126679928bed3c29f1dd92581a6c080`

이 amendment는 OCR dependency 정합성만 승인하며 S1.4X candidate 구현이나 benchmark
재실행 권한을 추가하지 않는다.
