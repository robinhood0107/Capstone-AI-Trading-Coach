#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../../../.." && pwd)"
SPRING_ROOT="${REPO_ROOT}/workspaces/decision-platform/spring-api"
PYTHON_ROOT="${REPO_ROOT}/workspaces/decision-platform/python-services"
COMPOSE_FILE="${SCRIPT_DIR}/compose.yml"
DEMO_OUTPUT="${REPO_ROOT}/artifacts/decision-platform/s8-demo"
CONFIG_FILE="${REPO_ROOT}/shared-docs/backtest_config.yaml"

adapter="db"
mode=""
action="prepare"
for argument in "$@"; do
  case "${argument}" in
    --adapter=db) adapter="db" ;;
    --adapter=kafka) adapter="kafka" ;;
    --brokerage-mode=INTERNAL_PAPER) mode="INTERNAL_PAPER" ;;
    --prepare) action="prepare" ;;
    --stop) action="stop" ;;
    *) echo "S8_DEMO_ARGUMENT_REJECTED" >&2; exit 2 ;;
  esac
done

if [[ "${mode}" != "INTERNAL_PAPER" ]]; then
  echo "S8_DEMO_EXPLICIT_INTERNAL_PAPER_REQUIRED" >&2
  exit 2
fi

export POSTGRES_HOST_PORT=55438
export POSTGRES_PORT=55438
export REDIS_HOST_PORT=56388
export REDIS_PORT=56388
export KAFKA_HOST_PORT=59092
export KAFKA_UI_HOST_PORT=58081
export ASYNC_ADAPTER="${adapter}"
export ASYNC_POLLING_ENABLED=false
export ASYNC_WORKER_ENABLED=false
export RAG_ANSWERER=FIXTURE_ONLY
export RAG_GRPC_ENABLED=false
export RAG_V2_GRPC_ENABLED=false
export RAG_V2_VERTEX_ENABLED=false
export BROKERAGE_GRPC_ENABLED=false
export KIS_MOCK_BROKERAGE_ONLINE_ENABLED=false
export KIS_OFFLINE=1

mkdir -p "${DEMO_OUTPUT}"
FIXTURE_ENV="${DEMO_OUTPUT}/demo.env"
if [[ -L "${FIXTURE_ENV}" ]]; then
  echo "S8_DEMO_ENV_SYMLINK_REJECTED" >&2
  exit 2
fi
if [[ ! -f "${FIXTURE_ENV}" ]]; then
  "${SPRING_ROOT}/gradlew" -p "${SPRING_ROOT}" --no-daemon prepareOpenApiFixtureEnv >/dev/null
  umask 077
  cp -- "${SPRING_ROOT}/build/openapi-fixture/openapi.env" "${FIXTURE_ENV}"
fi
chmod 0600 "${FIXTURE_ENV}"

compose=(docker compose --project-name capstone-s8-demo --env-file "${FIXTURE_ENV}" -f "${COMPOSE_FILE}")

if [[ "${action}" == "stop" ]]; then
  "${compose[@]}" --profile kafka --profile kafka-ui stop
  echo "S8_DEMO_STOPPED_VOLUMES_PRESERVED"
  exit 0
fi

"${compose[@]}" config --quiet
if [[ "${adapter}" == "kafka" ]]; then
  "${compose[@]}" --profile kafka up -d postgres redis kafka kafka-topic-init
  topic_init_id="$("${compose[@]}" --profile kafka ps -aq kafka-topic-init)"
  if [[ -z "${topic_init_id}" ]]; then
    echo "S8_DEMO_TOPIC_INITIALIZER_MISSING" >&2
    exit 1
  fi
  topic_init_exit="$(timeout 180 docker wait "${topic_init_id}")"
  if [[ "${topic_init_exit}" != "0" ]]; then
    echo "S8_DEMO_TOPIC_INITIALIZER_FAILED" >&2
    exit 1
  fi
else
  "${compose[@]}" up -d postgres redis
fi

(
  cd "${PYTHON_ROOT}"
  uv run --frozen python -m app.s8_demo.demo_seed \
    --config "${CONFIG_FILE}" \
    --output "${DEMO_OUTPUT}" \
    --brokerage-mode INTERNAL_PAPER
)

echo "S8_DEMO_INFRA_READY project=capstone-s8-demo adapter=${adapter}"
echo "S8_DEMO_OUTPUT=${DEMO_OUTPUT}"
