# CI 워크플로

`.github/workflows/` 11종.

| 워크플로 | 성격 | 로컬 대응 |
|---|---|---|
| `python-ci.yml` | python 게이트 | `pytest` / `ruff` / `mypy` |
| `kotlin-build.yml` | Kotlin 게이트 | `./gradlew ktlintCheck build` |
| `contracts-ci.yml` | 계약 게이트 | `contracts/validate.py` + `unittest discover` |
| `lint-static-analysis.yml` | 정적 분석 | `scripts/lint-static-analysis.sh` (로컬은 도구 미설치) |
| `repo-hygiene.yml` | 추적 규칙 | `git check-ignore` 단언 |
| `p1-full-app-security.yml` | 이미지 스캔 | 로컬 Trivy 캐시 |
| `p1-full-app-release.yml` | 릴리스 번들 | 로컬 대응 없음 (서명 필요) |
| `p1-offline-demo-release.yml` | 오프라인 데모 릴리스 | 같음 |
| `s1-4r-research-benchmark.yml` | 연구 벤치마크 | 범위 밖 |
| `s1-4r-research-correctness.yml` | 연구 정확성 | 범위 밖 |
| `s1-4x-contract-correctness.yml` | 격리 연구 계약 | 범위 밖 |

## 로컬 게이트 결과

전 게이트를 한 번 돌린 결과는 [README.md](README.md)의 실행 순서 아래 각 문서에 있다.
릴리스 두 종은 서명된 번들을 만들 수 있는 환경에서만 돌아간다.
