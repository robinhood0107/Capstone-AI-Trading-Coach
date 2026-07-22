# S1.4X Scala/Haskell numeric parity research

S1.4X는 S1.4 production NumPy 11개 함수와 S1.4R NumPy/JAX 9개 함수의 수치·오류
계약을 Scala와 Haskell의 독립 process로 재현하기 위한 비생산 연구다. 이 subtree는
production `RiskEngine`, 공개 API, root `contracts/` 또는 다른 팀 workspace와 연결되지
않는다.

## 현재 gate

이 tree의 **Gate 1 neutral fixture freeze**는 PR #28로 `main`에 병합되었고, 현재는
그 frozen contract와 S1.6 선행 runtime dependency amendment를 함께 재현한다.

- 언어 중립 JSON/binary exchange schema와 20개 함수·32개 stable error registry
- upstream Python/NumPy/JAX source·fixture byte lock, project runtime projection과 `uv.lock` lock
- canonical small/invalid/property fixture와 deterministic large-fixture generator
- Python oracle, contract/provenance/environment validator
- capability/property/safety/compatibility 정책
- 6개 family, 89개 case, 3회 반복의 사전 동결 benchmark plan
- PR과 `main` push에서 모두 실행되는 contract correctness workflow

Gate 1 병합 전에 `scala/`, `haskell/`, 언어별 correctness workflow와 candidate report를
추적하지 않던 경계는 이력으로 완료됐다. 현재 S1.6 선행 amendment는 candidate
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

S1.6 PR A의 병합은 dependency commit이 최종 `main` HEAD의 조상으로 남는
**merge commit 방식만** 허용한다. squash/rebase merge는 `referenceBaseCommit` 조상성을
없애 `main` push correctness check를 깨므로 금지한다. exact 승인 token은 PR 번호,
검증한 head SHA, `METHOD-MERGE-COMMIT`을 모두 결속하고, 실행 단계는
`gh pr merge <pr-number> --merge --match-head-commit <head-sha>`를 사용한다.

## 승인된 runtime dependency amendment 재생성

S1.6 prerequisite amendment는 production Python dev dependency와 `uv.lock`만 바꾼 commit
`93b0176ef3f114ca2182ad170449fd419437cfb6`을 새 reference input으로 승인했다. 이 commit의
자체 diff가 아래 두 파일뿐이고 최종 HEAD의 조상인지 먼저 확인한다.

```bash
set -euo pipefail
export TMPDIR=/tmp TEMP=/tmp TMP=/tmp
S16_DEPENDENCY_SHA=93b0176ef3f114ca2182ad170449fd419437cfb6
S16_S1_4X=workspaces/decision-platform/research/s1-4x-numeric-parity
S16_ORACLE="$S16_S1_4X/oracle"
export S16_DEPENDENCY_SHA S16_S1_4X

git cat-file -e "$S16_DEPENDENCY_SHA^{commit}"
git merge-base --is-ancestor "$S16_DEPENDENCY_SHA" HEAD
diff -u \
  <(printf '%s\n' \
    workspaces/decision-platform/python-services/pyproject.toml \
    workspaces/decision-platform/python-services/uv.lock) \
  <(git diff-tree --no-commit-id --name-only -r "$S16_DEPENDENCY_SHA" | LC_ALL=C sort)
```

reference lock writer는 현재 validator의 projection/tree helper를 그대로 사용한다. JSON 표시
형식은 유지하고 runtime 의미 hash만 재계산한다.

```bash
set -euo pipefail
PYTHONPATH="$S16_ORACLE" uv run --frozen --project "$S16_ORACLE" python - <<'PY'
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
lock_path = repo / os.environ["S16_S1_4X"] / "contract/reference-lock.v1.json"
lock = strict_json_load(lock_path)
lock["referenceBaseCommit"] = os.environ["S16_DEPENDENCY_SHA"]
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

uv run --frozen --project "$S16_ORACLE" \
  python "$S16_S1_4X/benchmarks/render_benchmark_plan.py" --write

# manifest는 reference lock, oracle test, benchmark plan/sidecar가 모두 고정된 뒤 마지막에 쓴다.
uv run --frozen --project "$S16_ORACLE" \
  python "$S16_ORACLE/validate_contract.py" --write-manifest
```

그 뒤 이 문서의 재현 명령 전체를 실행한다. PR에서는 base SHA, `main` push에서는
push-before SHA를 diff base로 사용해 `shared-docs/metrics_definitions.md`, production
`financial_engineering` source/tests, S1.4R `src`/`tests`, S1.4X `contract/`(의도적
reference lock/manifest 제외), `oracle/`(추가된 validator regression test 제외),
`benchmarks/`(재생성된 plan/sidecar 제외)의 drift가 0임을 검증한다. 로컬에서는
같은 범위를 `origin/main` 대비로 확인한다. 이번 amendment의
reference lock SHA는 `6357f224b1c9bcb4d08c89281e3442c7bfab31fef14fb6f54616a99bb206523f`에서
`356dda6cd719f81fcc78a6516c324c47ef5d6da59afe7e44b932ca2af286b738`, benchmark plan
SHA는 `387ea1fa36e5377e597cb32b05ca5ef0faed90e189cf147a383531287c4202a6`에서
`6bb394368ef784d291e7795cb20ca6d7631f12661ccdb172acec59c6c9edea45`, benchmark plan
sidecar file SHA는 `ce34347f895a55731bce3a0f0ab643df4baf9fa6d669f65fdf6d8076e1a7ca79`에서
`6c0088d8ad203dfaf2fe299067c5803eb48d7a6f978542633d16d826ddc16fd2`, contract manifest SHA는
`d9f5a4842a64c8655e1a3f0a0574c94f75bcba3ab3f79ee9435965a1c4a5f393`에서
`6df5318aa969b63bccf9281ec761f6063e1e74a8e11489f88bb948022b6ba6d2`로 바뀌었고 numeric
source/fixture bytes는 바뀌지 않았다. dependency, lock,
reference/plan/manifest와 이 의미 설명은 한 rollback unit이며 일부만 cherry-pick/revert하지 않는다.

## 다음 승인

Gate 1 병합은 PR #28에서 완료됐다. 현재 S1.6 PR A는 review와 required checks가
모두 끝나더라도 사용자의 별도 exact merge 승인 전에는 병합하지 않는다.
승인은 PR/head/method에 결속한 `METHOD-MERGE-COMMIT` token으로만 받으며
squash/rebase는 허용하지 않는다. Gate 2 연속 실행은 병합된
`fixtureFreezeMergeSha`와 이 tree의 immutable SHA를 포함한 별도 readiness packet을
소비한다.
