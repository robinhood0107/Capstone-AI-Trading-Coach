#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

python_project="workspaces/decision-platform/python-services"
spring_project="workspaces/decision-platform/spring-api"
local_only_root='private-'"reference"

[[ "$(shellcheck --version | awk '/^version:/ { print $2 }')" == "0.11.0" ]]
[[ "$(actionlint -version | head -n 1)" == "1.7.12" ]]
[[ "$(hadolint --version)" == "Haskell Dockerfile Linter 2.15.1" ]]
[[ "$(uv --project "$python_project" run ruff --version)" == "ruff 0.15.20" ]]
[[ "$(uv --project "$python_project" run mypy --version)" == "mypy 2.2.0 (compiled: yes)" ]]
[[ "$(uv --project "$python_project" run yamllint --version)" == "yamllint 1.38.0" ]]
[[ "$(uv --project "$python_project" run toml-sort --version)" == "0.24.4" ]]
[[ "$(uv --project "$python_project" run pymarkdown version)" == "0.9.39" ]]

pushd "$python_project" >/dev/null
uv run ruff check app tests
uv run ruff format --check app tests
uv run mypy app
popd >/dev/null

"$spring_project/gradlew" -p "$spring_project" ktlintCheck detektMain detektTest

mapfile -d '' shell_files < <(git ls-files -z -- '*.sh' ":(exclude)$local_only_root/**")
((${#shell_files[@]} > 0))
shellcheck --external-sources "${shell_files[@]}"

mapfile -d '' workflow_files < <(git ls-files -z -- '.github/workflows/*.yml' '.github/workflows/*.yaml')
((${#workflow_files[@]} > 0))
actionlint -shellcheck="$(command -v shellcheck)" "${workflow_files[@]}"

mapfile -d '' dockerfiles < <(git ls-files -z -- '*Dockerfile*' ":(exclude)$local_only_root/**")
((${#dockerfiles[@]} > 0))
hadolint "${dockerfiles[@]}"

mapfile -d '' yaml_files < <(git ls-files -z -- '*.yml' '*.yaml' ":(exclude)$local_only_root/**")
((${#yaml_files[@]} > 0))
uv --project "$python_project" run yamllint -c .yamllint.yml "${yaml_files[@]}"

mapfile -d '' toml_files < <(git ls-files -z -- '*.toml' ":(exclude)$local_only_root/**")
((${#toml_files[@]} > 0))
uv --project "$python_project" run toml-sort --check "${toml_files[@]}"

# Human-facing public Markdown only. Immutable contracts, source cards,
# evidence records, and third-party notices keep their hash-bound bytes.
mapfile -d '' markdown_files < <(
  git -c core.quotePath=false ls-files -z -- '*.md' \
    ":(exclude)$local_only_root/**" \
    ':(exclude)contracts/changes/**' \
    ':(exclude)capstone-rag/evidence/**' \
    ':(exclude)capstone-rag/source-cards/**' \
    ':(exclude)deploy/p1/THIRD_PARTY_NOTICES.md' \
    ':(exclude)workspaces/return-engine/README.md' \
    ':(exclude)workspaces/experience-dashboard/README.md'
)
((${#markdown_files[@]} > 0))
uv --project "$python_project" run pymarkdown \
  -d '*' \
  -e md001,md025,md040,md042,md051,md052,md053 \
  scan "${markdown_files[@]}"
