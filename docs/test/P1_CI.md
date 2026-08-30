# CI 워크플로

`.github/workflows/` 11종. 아래 결과는 PR #177의 커밋 `664f3ce8`에서 실제로 관측한 것이다.

| 워크플로 | 트리거 | 결과 |
|---|---|---|
| `repo-hygiene.yml` | PR | **success** |
| `contracts-ci.yml` | PR | **success** |
| `lint-static-analysis.yml` | PR | **success** |
| `python-ci.yml` | PR | **success** |
| `kotlin-build.yml` | PR | **success** |
| `p1-full-app-security.yml` | PR | **success** |
| `s1-4x-contract-correctness.yml` | PR | **success** |
| `s1-4r-research-correctness.yml` | PR (연구 경로 변경 시에만) | 이번 diff가 그 경로를 건드리지 않아 **미실행** |
| `p1-full-app-release.yml` | `workflow_dispatch` | 릴리스 시점에만. main HEAD의 merge SHA와 버전 1.0.0을 입력받는다 |
| `p1-offline-demo-release.yml` | `workflow_dispatch` | 같음 |
| `s1-4r-research-benchmark.yml` | `workflow_dispatch` | 연구 벤치마크. 배포와 무관 |

## 이번 실행에서 CI가 잡아낸 것

로컬 게이트는 통과했는데 CI에서 두 가지가 더 걸렸다. 둘 다 로컬 명령에 들어 있지 않은 검사였다.

1. **`detekt`** — `ktlintCheck build`에는 없다. 새 로거 속성의 암시적 플랫폼 타입과, 일반 catch
   안에서 예외 타입을 되묻는 자리 둘이 걸렸다. 전용 catch 블록으로 나누고 타입을 명시했다.
2. **Pre-S5 단독 소유 잠금** — `HISTORICAL_SUPERSEDED`로 동결된
   `P1_TEAM_A_B_수신_후_통합_체크리스트.md`를 고친 것이 걸렸다. 그 문서를 base 상태로 되돌리고
   사실 정정은 `P1_TEAM_수신.md`로 옮겼다.

로컬에서 `scripts/lint-static-analysis.sh`를 그대로 돌리지 못한 이유는 핀 고정 도구 8종 중
`shellcheck` 0.11.0, `actionlint` 1.7.12, `hadolint` 2.15.1이 이 머신에 없기 때문이다. 대신 각
검사를 개별로 돌려 확인했다 — ruff, mypy, ktlint, detekt, yamllint, pymarkdown, shellcheck는
전부 통과했다.

## 배포 전에 남은 것

릴리스 두 워크플로는 **main HEAD의 merge SHA**를 입력으로 받는다. 즉 이 PR이 main에 병합된
뒤에야 돌릴 수 있다. 그때 서명된 번들이 만들어지고, 그 번들이 있어야 `deploy/p1/verify-release`와
`verify-offline-runtime`도 돌릴 수 있다([P1_검증_러너.md](P1_검증_러너.md) 참조).
