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

FIXTURE_ENV="${DEMO_OUTPUT}/demo.env"
FINGERPRINT_FILE="${DEMO_OUTPUT}/runtime-fingerprint"
PROJECT_NAME="capstone-s8-demo"

validate_env() {
  local env_file="$1"
  local mode_bits size line key
  local -A expected=()
  local -A observed=()
  local keys=(
    ASYNC_CURSOR_HMAC_KEY ASYNC_PARTITION_HMAC_KEY ASYNC_POLLING_ENABLED
    ASYNC_WORKER_ENABLED ASYNC_WORKER_GRPC_SHARED_SECRET BROKERAGE_DB_CAPABILITY_TOKEN
    BROKERAGE_DB_CAPABILITY_TOKEN_SHA256 BROKERAGE_IDEMPOTENCY_SCOPE_HMAC_KEY
    DECISION_GRPC_SHARED_SECRET DECISION_IDEMPOTENCY_SCOPE_HMAC_KEY
    DEMO_ADMIN_CREDENTIAL_BUNDLE DEMO_CREDENTIAL_SEPARATION_KEY DEMO_USER_CREDENTIAL_BUNDLE
    JWT_AUDIENCE JWT_ISSUER JWT_SECRET KAFKA_UI_PASSWORD KAFKA_UI_USERNAME
    LOGIN_SCOPE_HMAC_KEY MCP_SEARXNG_AUTH_TOKEN POSTGRES_ADMIN_PASSWORD POSTGRES_ADMIN_USER
    POSTGRES_APP_PASSWORD POSTGRES_AUTH_PASSWORD POSTGRES_COLLECTOR_PASSWORD POSTGRES_DB
    POSTGRES_DEMO_PASSWORD POSTGRES_DISCLOSURE_READER_PASSWORD POSTGRES_FILL_WRITER_PASSWORD
    POSTGRES_HOST POSTGRES_HOST_PORT POSTGRES_IDENTITY_PASSWORD POSTGRES_MARKET_WRITER_PASSWORD
    POSTGRES_MIGRATION_PASSWORD POSTGRES_PORT POSTGRES_PORTFOLIO_WRITER_PASSWORD
    POSTGRES_RAG_ADMIN_PASSWORD POSTGRES_RAG_QUERY_PASSWORD POSTGRES_RAG_WRITER_PASSWORD
    POSTGRES_REPLAY_AUTHORIZER_PASSWORD POSTGRES_REPLAY_PASSWORD POSTGRES_RISK_WRITER_PASSWORD
    POSTGRES_SIGNAL_ADMIN_PASSWORD POSTGRES_SIGNAL_SCHEDULER_PASSWORD
    POSTGRES_SIGNAL_WRITER_PASSWORD POSTGRES_WORKER_PASSWORD POSTGRES_OUTBOX_PUBLISHER_PASSWORD
    POSTGRES_POISON_RECORDER_PASSWORD PRINCIPLE_CURSOR_HMAC_KEY
    PYTHON_GRPC_SHARED_SECRET RAG_GRPC_SHARED_SECRET RAG_HISTORY_CURRENT_KEK_VERSION
    RAG_HISTORY_CURSOR_HMAC_KEY RAG_HISTORY_SECRET_DIRECTORY RAG_IDEMPOTENCY_SCOPE_HMAC_KEY
    RAG_PROVIDER_USAGE_HMAC_KEY RAG_RATE_LIMIT_HMAC_KEY RAG_REQUEST_FINGERPRINT_HMAC_KEY
    REDIS_PASSWORD SEARXNG_SECRET
  )
  if [[ -L "${env_file}" || ! -f "${env_file}" ]]; then
    echo "S8_DEMO_ENV_REGULAR_FILE_REQUIRED" >&2
    exit 2
  fi
  mode_bits="$(stat -c '%a' -- "${env_file}")"
  size="$(stat -c '%s' -- "${env_file}")"
  if [[ "${mode_bits}" != "600" || "${size}" -lt 1 || "${size}" -gt 65536 ]]; then
    echo "S8_DEMO_ENV_MODE_OR_SIZE_REJECTED" >&2
    exit 2
  fi
  for key in "${keys[@]}"; do expected["${key}"]=1; done
  while IFS= read -r line || [[ -n "${line}" ]]; do
    if [[ ! "${line}" =~ ^[A-Z][A-Z0-9_]*=.{1,4096}$ ]]; then
      echo "S8_DEMO_ENV_FORMAT_REJECTED" >&2
      exit 2
    fi
    key="${line%%=*}"
    if [[ -z "${expected[${key}]+set}" || -n "${observed[${key}]+set}" ]]; then
      echo "S8_DEMO_ENV_KEYSET_REJECTED" >&2
      exit 2
    fi
    observed["${key}"]=1
  done < "${env_file}"
  if [[ "${#observed[@]}" -ne "${#expected[@]}" ]]; then
    echo "S8_DEMO_ENV_KEYSET_REJECTED" >&2
    exit 2
  fi
}

if [[ "${action}" == "stop" ]]; then
  existing_containers="$(docker ps -aq --filter "label=com.docker.compose.project=${PROJECT_NAME}")"
  existing_volumes="$(docker volume ls -q --filter "label=com.docker.compose.project=${PROJECT_NAME}")"
  if [[ ! -e "${FIXTURE_ENV}" && -z "${existing_containers}" && -z "${existing_volumes}" ]]; then
    echo "S8_DEMO_NOT_INITIALIZED"
    exit 0
  fi
  if [[ -e "${FIXTURE_ENV}" ]]; then
    if [[ -L "${FIXTURE_ENV}" || ! -f "${FIXTURE_ENV}" || "$(stat -c '%a' -- "${FIXTURE_ENV}")" != "600" ]]; then
      echo "S8_DEMO_ENV_MODE_REJECTED" >&2
      exit 2
    fi
  fi
  if [[ -n "${existing_containers}" ]]; then
    mapfile -t container_ids <<< "${existing_containers}"
    docker stop "${container_ids[@]}" >/dev/null
  fi
  echo "S8_DEMO_STOPPED_VOLUMES_PRESERVED"
  exit 0
fi

if [[ "${mode}" != "INTERNAL_PAPER" ]]; then
  echo "S8_DEMO_EXPLICIT_INTERNAL_PAPER_REQUIRED" >&2
  exit 2
fi

existing_volumes="$(docker volume ls -q --filter "label=com.docker.compose.project=${PROJECT_NAME}")"
if [[ ! -e "${FIXTURE_ENV}" && -n "${existing_volumes}" ]]; then
  echo "S8_DEMO_ENV_MISSING_WITH_EXISTING_VOLUME" >&2
  exit 2
fi

mkdir -p "${DEMO_OUTPUT}"
chmod 0700 "${DEMO_OUTPUT}"
if [[ ! -e "${FIXTURE_ENV}" ]]; then
  "${SPRING_ROOT}/gradlew" -p "${SPRING_ROOT}" --no-daemon prepareOpenApiFixtureEnv >/dev/null
  umask 077
  cp -- "${SPRING_ROOT}/build/openapi-fixture/openapi.env" "${FIXTURE_ENV}"
fi
validate_env "${FIXTURE_ENV}"

compose=(docker compose --project-name "${PROJECT_NAME}" --env-file "${FIXTURE_ENV}" -f "${COMPOSE_FILE}")
compose_sha="$("${compose[@]}" config | sha256sum | cut -d' ' -f1)"
env_sha="$(sha256sum -- "${FIXTURE_ENV}" | cut -d' ' -f1)"
expected_fingerprint="PROJECT=${PROJECT_NAME} ENV_SHA256=${env_sha} COMPOSE_SHA256=${compose_sha}"
if [[ -e "${FINGERPRINT_FILE}" ]]; then
  if [[ -L "${FINGERPRINT_FILE}" || ! -f "${FINGERPRINT_FILE}" || "$(stat -c '%a' -- "${FINGERPRINT_FILE}")" != "600" ]]; then
    echo "S8_DEMO_FINGERPRINT_REJECTED" >&2
    exit 2
  fi
  if [[ "$(<"${FINGERPRINT_FILE}")" != "${expected_fingerprint}" ]]; then
    echo "S8_DEMO_ENV_VOLUME_FINGERPRINT_CONFLICT" >&2
    exit 2
  fi
elif [[ -n "${existing_volumes}" ]]; then
  echo "S8_DEMO_VOLUME_WITHOUT_FINGERPRINT" >&2
  exit 2
else
  umask 077
  printf '%s\n' "${expected_fingerprint}" > "${FINGERPRINT_FILE}"
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
