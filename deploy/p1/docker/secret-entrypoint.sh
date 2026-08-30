#!/bin/sh
set -eu

profile=${1:?secret profile is required}
shift

case "$profile" in
  spring) secret_files=/run/secrets/spring_env ;;
  decision-platform) secret_files="/run/secrets/spring_env /run/secrets/python_env /run/secrets/kis_mock_env /run/secrets/return_inference_env /run/secrets/automation_runtime_env /run/secrets/rag_v2_env" ;;
  automation-runtime) secret_files=/run/secrets/automation_runtime_env ;;
  automation-cli) secret_files="/run/secrets/automation_runtime_env /run/secrets/kis_mock_env" ;;
  automation-gate-author) secret_files=/run/secrets/automation_gate_author_env ;;
  certification) secret_files="/run/secrets/spring_env /run/secrets/python_env /run/secrets/kis_mock_env" ;;
  authority) secret_files=/run/secrets/actor_capability_authority_env ;;
  role-bootstrap) secret_files=/run/secrets/role_bootstrap_env ;;
  migration) secret_files=/run/secrets/migration_env ;;
  seed-import) secret_files=/run/secrets/seed_import_env ;;
  artifact-import) secret_files=/run/secrets/artifact_import_env ;;
  team-a-acceptance) secret_files=/run/secrets/team_a_acceptance_env ;;
  bootstrap) secret_files=/run/secrets/bootstrap_env ;;
  python) secret_files=/run/secrets/python_env ;;
  kafka-publisher) secret_files=/run/secrets/kafka_publisher_env ;;
  poison-recorder) secret_files=/run/secrets/poison_recorder_env ;;
  kafka-admin) secret_files=/run/secrets/kafka_admin_env ;;
  demo) secret_files=/run/secrets/demo_env ;;
  postgres) secret_files=/run/secrets/postgres_env ;;
  redis) secret_files=/run/secrets/redis_env ;;
  *) echo "p1 secret loading failed: unknown_profile" >&2; exit 1 ;;
esac

for secret_file in $secret_files; do
  if [ ! -f "$secret_file" ] || [ -L "$secret_file" ]; then
    echo "p1 secret loading failed: invalid_secret_file" >&2
    exit 1
  fi
  size=$(wc -c < "$secret_file")
  if [ "$size" -lt 1 ] || [ "$size" -gt 65536 ]; then
    echo "p1 secret loading failed: invalid_secret_size" >&2
    exit 1
  fi
done

allowed_key() {
  key_profile=$profile
  [ "$key_profile" != certification ] || key_profile=decision-platform
  case "$key_profile:$1" in
    postgres:POSTGRES_PASSWORD|postgres:POSTGRES_APP_PASSWORD|postgres:POSTGRES_MIGRATION_PASSWORD|postgres:POSTGRES_COLLECTOR_PASSWORD|postgres:POSTGRES_DISCLOSURE_READER_PASSWORD|postgres:POSTGRES_MARKET_WRITER_PASSWORD|postgres:POSTGRES_PORTFOLIO_WRITER_PASSWORD|postgres:POSTGRES_RISK_WRITER_PASSWORD|postgres:POSTGRES_FILL_WRITER_PASSWORD|postgres:POSTGRES_RAG_WRITER_PASSWORD|postgres:POSTGRES_RAG_ADMIN_PASSWORD|postgres:POSTGRES_RAG_QUERY_PASSWORD|postgres:POSTGRES_SIGNAL_WRITER_PASSWORD|postgres:POSTGRES_SIGNAL_SCHEDULER_PASSWORD|postgres:POSTGRES_SIGNAL_ADMIN_PASSWORD|postgres:POSTGRES_WORKER_PASSWORD|postgres:POSTGRES_AUTOMATION_RUNTIME_PASSWORD|postgres:POSTGRES_OUTBOX_PUBLISHER_PASSWORD|postgres:POSTGRES_POISON_RECORDER_PASSWORD|postgres:POSTGRES_REPLAY_PASSWORD|postgres:POSTGRES_IDENTITY_PASSWORD|postgres:POSTGRES_AUTH_PASSWORD|postgres:POSTGRES_REPLAY_AUTHORIZER_PASSWORD|postgres:POSTGRES_DEMO_PASSWORD) return 0 ;;
    role-bootstrap:POSTGRES_ADMIN_USER|role-bootstrap:POSTGRES_PASSWORD|role-bootstrap:POSTGRES_AUTH_PASSWORD|role-bootstrap:POSTGRES_AUTOMATION_RUNTIME_PASSWORD|role-bootstrap:POSTGRES_OUTBOX_PUBLISHER_PASSWORD|role-bootstrap:POSTGRES_POISON_RECORDER_PASSWORD) return 0 ;;
    spring:POSTGRES_APP_PASSWORD|spring:POSTGRES_WORKER_PASSWORD|spring:POSTGRES_AUTH_PASSWORD|spring:ACTOR_CAPABILITY_SHARED_SECRET|spring:ACTOR_CAPABILITY_PUBLIC_KEY|spring:REDIS_PASSWORD|spring:JWT_SECRET|spring:JWT_ISSUER|spring:JWT_AUDIENCE|spring:LOGIN_SCOPE_HMAC_KEY|spring:PRINCIPLE_CURSOR_HMAC_KEY|spring:DECISION_IDEMPOTENCY_SCOPE_HMAC_KEY|spring:DECISION_GRPC_SHARED_SECRET|spring:BROKERAGE_IDEMPOTENCY_SCOPE_HMAC_KEY|spring:RAG_IDEMPOTENCY_SCOPE_HMAC_KEY|spring:RAG_REQUEST_FINGERPRINT_HMAC_KEY|spring:RAG_PROVIDER_USAGE_HMAC_KEY|spring:RAG_RATE_LIMIT_HMAC_KEY|spring:RAG_HISTORY_CURSOR_HMAC_KEY|spring:DEMO_CREDENTIAL_SEPARATION_KEY|spring:DEMO_USER_CREDENTIAL_BUNDLE|spring:DEMO_ADMIN_CREDENTIAL_BUNDLE|spring:ASYNC_CURSOR_HMAC_KEY|spring:ASYNC_PARTITION_HMAC_KEY|spring:ASYNC_WORKER_GRPC_SHARED_SECRET) return 0 ;;
    decision-platform:POSTGRES_APP_PASSWORD|decision-platform:POSTGRES_WORKER_PASSWORD|decision-platform:POSTGRES_AUTH_PASSWORD|decision-platform:ACTOR_CAPABILITY_SHARED_SECRET|decision-platform:ACTOR_CAPABILITY_PUBLIC_KEY|decision-platform:ACTOR_CAPABILITY_TLS_KEY_STORE_PASSWORD|decision-platform:REDIS_PASSWORD|decision-platform:JWT_SECRET|decision-platform:JWT_ISSUER|decision-platform:JWT_AUDIENCE|decision-platform:LOGIN_SCOPE_HMAC_KEY|decision-platform:PRINCIPLE_CURSOR_HMAC_KEY|decision-platform:DECISION_IDEMPOTENCY_SCOPE_HMAC_KEY|decision-platform:DECISION_GRPC_SHARED_SECRET|decision-platform:BROKERAGE_IDEMPOTENCY_SCOPE_HMAC_KEY|decision-platform:BROKERAGE_GRPC_SHARED_SECRET|decision-platform:RAG_IDEMPOTENCY_SCOPE_HMAC_KEY|decision-platform:RAG_REQUEST_FINGERPRINT_HMAC_KEY|decision-platform:RAG_PROVIDER_USAGE_HMAC_KEY|decision-platform:RAG_RATE_LIMIT_HMAC_KEY|decision-platform:RAG_HISTORY_CURSOR_HMAC_KEY|decision-platform:DEMO_CREDENTIAL_SEPARATION_KEY|decision-platform:DEMO_USER_CREDENTIAL_BUNDLE|decision-platform:DEMO_ADMIN_CREDENTIAL_BUNDLE|decision-platform:ASYNC_CURSOR_HMAC_KEY|decision-platform:ASYNC_PARTITION_HMAC_KEY|decision-platform:ASYNC_WORKER_GRPC_SHARED_SECRET|decision-platform:ASYNC_WORKER_DATABASE_DSN|decision-platform:KAFKA_SASL_USERNAME|decision-platform:KAFKA_SASL_PASSWORD|decision-platform:KAFKA_ENVELOPE_PUBLIC_KEY|decision-platform:POISON_RECORDER_URL|decision-platform:POISON_RECORDER_SHARED_SECRET|decision-platform:KIS_MOCK_CONFIGURED|decision-platform:KIS_MOCK_APP_KEY|decision-platform:KIS_MOCK_APP_SECRET|decision-platform:KIS_MOCK_ACCOUNT_NO|decision-platform:KIS_MOCK_BOUND_ACCOUNT_ID|decision-platform:KIS_MOCK_ORDER_REFERENCE_KEY|decision-platform:KIS_BROKERAGE_TOKEN_P_PHYSICAL_CAP|decision-platform:KIS_BROKERAGE_PHYSICAL_CAP) return 0 ;;
    decision-platform:RETURN_INFERENCE_GRPC_SHARED_SECRET) return 0 ;;
    decision-platform:RAG_V2_QUERY_DATABASE_DSN|decision-platform:RAG_V2_VOYAGE_QUERY_WRITER_DSN|decision-platform:RAG_V2_GRPC_SHARED_SECRET|decision-platform:VOYAGE_API_KEY) return 0 ;;
    decision-platform:STRONG_LLM_GRPC_SHARED_SECRET|decision-platform:STRONG_LLM_API_KEY|decision-platform:STRONG_LLM_FALLBACK_API_KEY) return 0 ;;
    decision-platform:P1_AUTOMATION_DATABASE_DSN|decision-platform:AUTOMATION_RUNTIME_SHARED_SECRET|decision-platform:P1_AUTOMATION_OWNER_USER_ID|decision-platform:P1_AUTOMATION_OWNER_USERNAME|decision-platform:P1_AUTOMATION_OWNER_PASSWORD) return 0 ;;
    automation-runtime:P1_AUTOMATION_DATABASE_DSN|automation-runtime:AUTOMATION_RUNTIME_SHARED_SECRET|automation-runtime:P1_AUTOMATION_OWNER_USER_ID|automation-runtime:P1_AUTOMATION_OWNER_USERNAME|automation-runtime:P1_AUTOMATION_OWNER_PASSWORD) return 0 ;;
    automation-cli:P1_AUTOMATION_DATABASE_DSN|automation-cli:AUTOMATION_RUNTIME_SHARED_SECRET|automation-cli:P1_AUTOMATION_OWNER_USER_ID|automation-cli:P1_AUTOMATION_OWNER_USERNAME|automation-cli:P1_AUTOMATION_OWNER_PASSWORD|automation-cli:KIS_MOCK_CONFIGURED|automation-cli:KIS_MOCK_APP_KEY|automation-cli:KIS_MOCK_APP_SECRET|automation-cli:KIS_MOCK_ACCOUNT_NO|automation-cli:KIS_MOCK_BOUND_ACCOUNT_ID|automation-cli:KIS_MOCK_ORDER_REFERENCE_KEY|automation-cli:KIS_BROKERAGE_TOKEN_P_PHYSICAL_CAP|automation-cli:KIS_BROKERAGE_PHYSICAL_CAP) return 0 ;;
    automation-gate-author:P1_AUTOMATION_GATE_AUTHOR_DSN) return 0 ;;
    authority:POSTGRES_IDENTITY_PASSWORD|authority:ACTOR_CAPABILITY_SHARED_SECRET|authority:ACTOR_CAPABILITY_PRIVATE_KEY|authority:ACTOR_CAPABILITY_PUBLIC_KEY|authority:ACTOR_CAPABILITY_TLS_KEY_STORE_PASSWORD) return 0 ;;
    migration:POSTGRES_MIGRATION_PASSWORD|migration:BROKERAGE_DB_CAPABILITY_TOKEN_SHA256|migration:DEMO_CREDENTIAL_SEPARATION_KEY|migration:DEMO_USER_CREDENTIAL_BUNDLE|migration:DEMO_ADMIN_CREDENTIAL_BUNDLE) return 0 ;;
    seed-import:P1_SEED_DATABASE_DSN) return 0 ;;
    artifact-import:P1_ARTIFACT_IMPORT_DATABASE_DSN) return 0 ;;
    team-a-acceptance:P1_TEAM_A_ACCEPTANCE_DATABASE_DSN) return 0 ;;
    bootstrap:POSTGRES_MIGRATION_PASSWORD|bootstrap:DEMO_CREDENTIAL_SEPARATION_KEY|bootstrap:DEMO_USER_CREDENTIAL_BUNDLE|bootstrap:DEMO_ADMIN_CREDENTIAL_BUNDLE) return 0 ;;
    python:ASYNC_WORKER_DATABASE_DSN|python:ASYNC_PARTITION_HMAC_KEY|python:ASYNC_WORKER_GRPC_SHARED_SECRET|python:KAFKA_SASL_USERNAME|python:KAFKA_SASL_PASSWORD|python:KAFKA_ENVELOPE_PUBLIC_KEY|python:POISON_RECORDER_URL|python:POISON_RECORDER_SHARED_SECRET) return 0 ;;
    kafka-publisher:OUTBOX_PUBLISHER_DATABASE_DSN|kafka-publisher:KAFKA_SASL_USERNAME|kafka-publisher:KAFKA_SASL_PASSWORD|kafka-publisher:KAFKA_ENVELOPE_PRIVATE_KEY) return 0 ;;
    poison-recorder:POISON_RECORDER_DATABASE_DSN|poison-recorder:POISON_RECORDER_SHARED_SECRET) return 0 ;;
    kafka-admin:KAFKA_SASL_USERNAME|kafka-admin:KAFKA_SASL_PASSWORD) return 0 ;;
    demo:P1_DEMO_DATABASE_DSN|demo:ASYNC_PARTITION_HMAC_KEY) return 0 ;;
    redis:REDIS_PASSWORD) return 0 ;;
    *) return 1 ;;
  esac
}

required_keys() {
  key_profile=$profile
  case "$key_profile" in
    # certification runner는 spring_env, python_env, kis_mock_env만 마운트한다. 전체
    # decision-platform 필수 집합을 요구하면 automation_runtime_env 키에서 항상 실패한다.
    certification) printf '%s\n' 'REDIS_PASSWORD KIS_MOCK_CONFIGURED KIS_MOCK_APP_KEY KIS_MOCK_APP_SECRET KIS_MOCK_ACCOUNT_NO KIS_MOCK_BOUND_ACCOUNT_ID KIS_MOCK_ORDER_REFERENCE_KEY KIS_BROKERAGE_TOKEN_P_PHYSICAL_CAP KIS_BROKERAGE_PHYSICAL_CAP'
      return ;;
  esac
  case "$key_profile" in
    postgres) printf '%s\n' 'POSTGRES_PASSWORD POSTGRES_APP_PASSWORD POSTGRES_MIGRATION_PASSWORD POSTGRES_COLLECTOR_PASSWORD POSTGRES_DISCLOSURE_READER_PASSWORD POSTGRES_MARKET_WRITER_PASSWORD POSTGRES_PORTFOLIO_WRITER_PASSWORD POSTGRES_RISK_WRITER_PASSWORD POSTGRES_FILL_WRITER_PASSWORD POSTGRES_RAG_WRITER_PASSWORD POSTGRES_RAG_ADMIN_PASSWORD POSTGRES_RAG_QUERY_PASSWORD POSTGRES_SIGNAL_WRITER_PASSWORD POSTGRES_SIGNAL_SCHEDULER_PASSWORD POSTGRES_SIGNAL_ADMIN_PASSWORD POSTGRES_WORKER_PASSWORD POSTGRES_AUTOMATION_RUNTIME_PASSWORD POSTGRES_OUTBOX_PUBLISHER_PASSWORD POSTGRES_POISON_RECORDER_PASSWORD POSTGRES_REPLAY_PASSWORD POSTGRES_IDENTITY_PASSWORD POSTGRES_AUTH_PASSWORD POSTGRES_REPLAY_AUTHORIZER_PASSWORD POSTGRES_DEMO_PASSWORD' ;;
    role-bootstrap) printf '%s\n' 'POSTGRES_ADMIN_USER POSTGRES_PASSWORD POSTGRES_AUTH_PASSWORD POSTGRES_AUTOMATION_RUNTIME_PASSWORD POSTGRES_OUTBOX_PUBLISHER_PASSWORD POSTGRES_POISON_RECORDER_PASSWORD' ;;
    spring) printf '%s\n' 'POSTGRES_APP_PASSWORD POSTGRES_WORKER_PASSWORD POSTGRES_AUTH_PASSWORD ACTOR_CAPABILITY_SHARED_SECRET ACTOR_CAPABILITY_PUBLIC_KEY REDIS_PASSWORD JWT_SECRET JWT_ISSUER JWT_AUDIENCE LOGIN_SCOPE_HMAC_KEY PRINCIPLE_CURSOR_HMAC_KEY DECISION_IDEMPOTENCY_SCOPE_HMAC_KEY DECISION_GRPC_SHARED_SECRET BROKERAGE_IDEMPOTENCY_SCOPE_HMAC_KEY RAG_IDEMPOTENCY_SCOPE_HMAC_KEY RAG_REQUEST_FINGERPRINT_HMAC_KEY RAG_PROVIDER_USAGE_HMAC_KEY RAG_RATE_LIMIT_HMAC_KEY RAG_HISTORY_CURSOR_HMAC_KEY DEMO_CREDENTIAL_SEPARATION_KEY DEMO_USER_CREDENTIAL_BUNDLE DEMO_ADMIN_CREDENTIAL_BUNDLE ASYNC_CURSOR_HMAC_KEY ASYNC_PARTITION_HMAC_KEY ASYNC_WORKER_GRPC_SHARED_SECRET' ;;
    decision-platform) printf '%s\n' 'POSTGRES_APP_PASSWORD POSTGRES_WORKER_PASSWORD POSTGRES_AUTH_PASSWORD ACTOR_CAPABILITY_SHARED_SECRET ACTOR_CAPABILITY_PUBLIC_KEY ACTOR_CAPABILITY_TLS_KEY_STORE_PASSWORD REDIS_PASSWORD JWT_SECRET JWT_ISSUER JWT_AUDIENCE LOGIN_SCOPE_HMAC_KEY PRINCIPLE_CURSOR_HMAC_KEY DECISION_IDEMPOTENCY_SCOPE_HMAC_KEY DECISION_GRPC_SHARED_SECRET BROKERAGE_IDEMPOTENCY_SCOPE_HMAC_KEY BROKERAGE_GRPC_SHARED_SECRET RAG_IDEMPOTENCY_SCOPE_HMAC_KEY RAG_REQUEST_FINGERPRINT_HMAC_KEY RAG_PROVIDER_USAGE_HMAC_KEY RAG_RATE_LIMIT_HMAC_KEY RAG_HISTORY_CURSOR_HMAC_KEY DEMO_CREDENTIAL_SEPARATION_KEY DEMO_USER_CREDENTIAL_BUNDLE DEMO_ADMIN_CREDENTIAL_BUNDLE ASYNC_CURSOR_HMAC_KEY ASYNC_PARTITION_HMAC_KEY ASYNC_WORKER_GRPC_SHARED_SECRET ASYNC_WORKER_DATABASE_DSN KAFKA_SASL_USERNAME KAFKA_SASL_PASSWORD KAFKA_ENVELOPE_PUBLIC_KEY POISON_RECORDER_URL POISON_RECORDER_SHARED_SECRET KIS_MOCK_CONFIGURED KIS_MOCK_APP_KEY KIS_MOCK_APP_SECRET KIS_MOCK_ACCOUNT_NO KIS_MOCK_BOUND_ACCOUNT_ID KIS_MOCK_ORDER_REFERENCE_KEY KIS_BROKERAGE_TOKEN_P_PHYSICAL_CAP KIS_BROKERAGE_PHYSICAL_CAP P1_AUTOMATION_DATABASE_DSN AUTOMATION_RUNTIME_SHARED_SECRET P1_AUTOMATION_OWNER_USER_ID P1_AUTOMATION_OWNER_USERNAME P1_AUTOMATION_OWNER_PASSWORD' ;;
    automation-runtime) printf '%s\n' 'P1_AUTOMATION_DATABASE_DSN AUTOMATION_RUNTIME_SHARED_SECRET P1_AUTOMATION_OWNER_USER_ID P1_AUTOMATION_OWNER_USERNAME P1_AUTOMATION_OWNER_PASSWORD' ;;
    automation-cli) printf '%s\n' 'P1_AUTOMATION_DATABASE_DSN AUTOMATION_RUNTIME_SHARED_SECRET P1_AUTOMATION_OWNER_USER_ID P1_AUTOMATION_OWNER_USERNAME P1_AUTOMATION_OWNER_PASSWORD KIS_MOCK_CONFIGURED KIS_MOCK_APP_KEY KIS_MOCK_APP_SECRET KIS_MOCK_ACCOUNT_NO KIS_MOCK_BOUND_ACCOUNT_ID KIS_MOCK_ORDER_REFERENCE_KEY KIS_BROKERAGE_TOKEN_P_PHYSICAL_CAP KIS_BROKERAGE_PHYSICAL_CAP' ;;
    automation-gate-author) printf '%s\n' 'P1_AUTOMATION_GATE_AUTHOR_DSN' ;;
    authority) printf '%s\n' 'POSTGRES_IDENTITY_PASSWORD ACTOR_CAPABILITY_SHARED_SECRET ACTOR_CAPABILITY_PRIVATE_KEY ACTOR_CAPABILITY_PUBLIC_KEY ACTOR_CAPABILITY_TLS_KEY_STORE_PASSWORD' ;;
    migration) printf '%s\n' 'POSTGRES_MIGRATION_PASSWORD BROKERAGE_DB_CAPABILITY_TOKEN_SHA256 DEMO_CREDENTIAL_SEPARATION_KEY DEMO_USER_CREDENTIAL_BUNDLE DEMO_ADMIN_CREDENTIAL_BUNDLE' ;;
    seed-import) printf '%s\n' 'P1_SEED_DATABASE_DSN' ;;
    artifact-import) printf '%s\n' 'P1_ARTIFACT_IMPORT_DATABASE_DSN' ;;
    team-a-acceptance) printf '%s\n' 'P1_TEAM_A_ACCEPTANCE_DATABASE_DSN' ;;
    bootstrap) printf '%s\n' 'POSTGRES_MIGRATION_PASSWORD DEMO_CREDENTIAL_SEPARATION_KEY DEMO_USER_CREDENTIAL_BUNDLE DEMO_ADMIN_CREDENTIAL_BUNDLE' ;;
    python) printf '%s\n' 'ASYNC_WORKER_DATABASE_DSN ASYNC_PARTITION_HMAC_KEY ASYNC_WORKER_GRPC_SHARED_SECRET KAFKA_SASL_USERNAME KAFKA_SASL_PASSWORD KAFKA_ENVELOPE_PUBLIC_KEY POISON_RECORDER_URL POISON_RECORDER_SHARED_SECRET' ;;
    kafka-publisher) printf '%s\n' 'OUTBOX_PUBLISHER_DATABASE_DSN KAFKA_SASL_USERNAME KAFKA_SASL_PASSWORD KAFKA_ENVELOPE_PRIVATE_KEY' ;;
    poison-recorder) printf '%s\n' 'POISON_RECORDER_DATABASE_DSN POISON_RECORDER_SHARED_SECRET' ;;
    kafka-admin) printf '%s\n' 'KAFKA_SASL_USERNAME KAFKA_SASL_PASSWORD' ;;
    demo) printf '%s\n' 'P1_DEMO_DATABASE_DSN ASYNC_PARTITION_HMAC_KEY' ;;
    redis) printf '%s\n' 'REDIS_PASSWORD' ;;
  esac
}

seen='|'
for secret_file in $secret_files; do
while read -r line || [ -n "$line" ]; do
  # IFS='=' 분해는 base64 padding으로 끝나는 값의 마지막 '='를 버린다. 첫 '=' 기준으로만
  # 나눠 값 원문을 그대로 보존한다.
  key=${line%%=*}
  value=${line#*=}
  if [ -z "$key" ] || [ "$key" = "$line" ] || ! allowed_key "$key"; then
    echo "p1 secret loading failed: unexpected_key" >&2
    exit 1
  fi
  case "$seen" in
    *"|$key|"*)
      current=$(printenv "$key" 2>/dev/null || true)
      [ "$current" = "$value" ] || { echo "p1 secret loading failed: duplicate_key" >&2; exit 1; }
      continue
      ;;
  esac
  if [ -z "$value" ]; then
    echo "p1 secret loading failed: invalid_value" >&2
    exit 1
  fi
  seen="$seen$key|"
  export "$key=$value"
done < "$secret_file"
done

for key in $(required_keys); do
  case "$seen" in
    *"|$key|"*) ;;
    *) echo "p1 secret loading failed: missing_key" >&2; exit 1 ;;
  esac
done

if [ "$profile" = decision-platform ]; then
  case "$seen" in
    *'|RETURN_INFERENCE_GRPC_SHARED_SECRET|'*) ;;
    *) echo "p1 secret loading failed: missing_return_inference_secret" >&2; exit 1 ;;
  esac
fi

if [ "$profile" = decision-platform ] || [ "$profile" = certification ] || [ "$profile" = automation-cli ]; then
  case "${KIS_MOCK_BROKERAGE_ONLINE_ENABLED:-false}:$KIS_MOCK_CONFIGURED" in
    false:false|false:true|true:true) ;;
    true:false) echo "p1 secret loading failed: kis_mock_not_configured" >&2; exit 1 ;;
    *) echo "p1 secret loading failed: invalid_kis_mock_gate" >&2; exit 1 ;;
  esac
fi

if [ "$profile" = spring ] || [ "$profile" = decision-platform ]; then
  if [ ! -f /run/secrets/rag_history_kek ] || [ -L /run/secrets/rag_history_kek ]; then
    echo "p1 secret loading failed: invalid_rag_key" >&2
    exit 1
  fi
  install -d -m 700 /tmp/rag-history
  rag_key_size=$(wc -c < /run/secrets/rag_history_kek)
  if [ "$rag_key_size" -eq 32 ]; then
    # Older p1ctl states stored the same 32 key bytes as raw binary. Convert
    # only the container-local copy to the provider's lowercase hex envelope.
    od -An -v -tx1 /run/secrets/rag_history_kek \
      | tr -d ' \n' > /tmp/rag-history/rag-history-kek-v1.key
    chmod 600 /tmp/rag-history/rag-history-kek-v1.key
  else
    install -m 600 /run/secrets/rag_history_kek /tmp/rag-history/rag-history-kek-v1.key
  fi
  export RAG_HISTORY_SECRET_DIRECTORY=/tmp/rag-history
  export RAG_HISTORY_CURRENT_KEK_VERSION=kek-v1
fi

if [ "$profile" = decision-platform ] && [ "${RAG_V2_GRPC_ENABLED:-false}" = true ]; then
  # RAG v2 질의 경로의 local root를 컨테이너 안 tmpfs에 만든다. loader가 0700 디렉터리와 0600
  # 파일을, 그리고 그 소유자가 실행 uid와 같기를 요구하는데, host bind mount는 host uid를 그대로
  # 들고 온다. rag-history가 이미 쓰는 방식대로 읽기전용 원본에서 복사만 한다.
  src=${P1_RAG_RUNTIME_DIR_MOUNT:-/run/rag-runtime}
  if [ ! -d "$src" ] || [ -L "$src" ]; then
    echo "p1 secret loading failed: rag_v2_runtime_root_missing" >&2
    exit 1
  fi
  for leaf in control/pre-s5-voyage-query-runtime.json \
              artifacts/voyage-context-4/tokenizer.json; do
    if [ ! -f "$src/$leaf" ] || [ -L "$src/$leaf" ]; then
      echo "p1 secret loading failed: rag_v2_runtime_leaf_missing" >&2
      exit 1
    fi
  done
  install -d -m 700 /tmp/rag-v2-root /tmp/rag-v2-root/control /tmp/rag-v2-root/secrets \
    /tmp/rag-v2-root/artifacts /tmp/rag-v2-root/artifacts/voyage-context-4
  install -m 600 "$src/control/pre-s5-voyage-query-runtime.json" \
    /tmp/rag-v2-root/control/pre-s5-voyage-query-runtime.json
  # writer DSN은 bind mount로 들여오지 않는다. compose secret으로 이미 들어와 있는 값을
  # 로컬 루트가 요구하는 0600 leaf로 옮겨 적을 뿐이다. 비밀이 host 쪽에서 넓게 읽히지 않는다.
  if [ -z "${RAG_V2_VOYAGE_QUERY_WRITER_DSN:-}" ]; then
    echo "p1 secret loading failed: rag_v2_query_writer_dsn_missing" >&2
    exit 1
  fi
  printf '%s' "$RAG_V2_VOYAGE_QUERY_WRITER_DSN" > /tmp/rag-v2-root/secrets/rag-v2-voyage-query-writer-dsn
  chmod 600 /tmp/rag-v2-root/secrets/rag-v2-voyage-query-writer-dsn
  install -m 600 "$src/artifacts/voyage-context-4/tokenizer.json" \
    /tmp/rag-v2-root/artifacts/voyage-context-4/tokenizer.json
  # Vertex 생성형 답변은 같은 로컬 루트의 secrets/에서 서비스 계정을 읽는다. 이것도 compose
  # secret으로 들어와 있으므로 소유자 전용 사본만 만든다. 없으면 Vertex만 닫히고 검색은 산다.
  if [ -f /run/secrets/vertex_service_account ] && [ ! -L /run/secrets/vertex_service_account ]; then
    install -m 600 /run/secrets/vertex_service_account \
      /tmp/rag-v2-root/secrets/pre-s5-vertex-service-account.json
  fi
  # 자동 활성화를 켰다면 그 정책 파일도 소유자 전용 사본으로 옮긴다. 켜 두고 정책이 없으면
  # 부팅에서 닫는다. 비용 상한 없이 provider를 자동으로 부르게 두는 것보다 안 뜨는 편이 낫다.
  if [ "${RAG_V2_VERTEX_AUTO_ACTIVATION_ENABLED:-false}" = true ]; then
    policy=$src/control/pre-s5-vertex-auto-activation-policy.json
    if [ ! -f "$policy" ] || [ -L "$policy" ]; then
      echo "p1 secret loading failed: rag_v2_vertex_auto_activation_policy_missing" >&2
      exit 1
    fi
    install -m 600 "$policy" /tmp/rag-v2-root/control/pre-s5-vertex-auto-activation-policy.json
    export RAG_V2_VERTEX_AUTO_ACTIVATION_POLICY_FILE=\
/tmp/rag-v2-root/control/pre-s5-vertex-auto-activation-policy.json
  fi
  export CAPSTONE_RAG_LOCAL_ROOT=/tmp/rag-v2-root
fi

if [ "$profile" = decision-platform ] && [ "${S4_9_STRONG_LLM_ENABLED:-false}" = true ]; then
  # Kotlin host와 Python agent는 이 공유 비밀로만 서로를 확인한다. 없으면 인증 없는 loopback
  # 서비스가 되므로 부팅에서 닫는다. 조용히 열린 채로 뜨지 않는다.
  if [ -z "${STRONG_LLM_GRPC_SHARED_SECRET:-}" ]; then
    echo "p1 secret loading failed: strong_llm_shared_secret_missing" >&2
    exit 1
  fi
  # Vertex를 1차나 2차로 쓰면 서비스계정이 있어야 한다. 그 소유자 전용 사본은 위 RAG v2
  # 블록이 만들므로, RAG v2를 끈 채 Vertex만 켜면 여기서 닫힌다. 켜 두고 매 요청 실패하는
  # 배포보다 안 뜨는 편이 낫다.
  if [ "${STRONG_LLM_PROVIDER:-vertex}" = vertex ] ||
    [ "${STRONG_LLM_FALLBACK_PROVIDER:-}" = vertex ]; then
    vertex_key=/tmp/rag-v2-root/secrets/pre-s5-vertex-service-account.json
    if [ ! -f "$vertex_key" ] || [ -L "$vertex_key" ]; then
      echo "p1 secret loading failed: strong_llm_vertex_service_account_missing" >&2
      exit 1
    fi
    export STRONG_LLM_VERTEX_SERVICE_ACCOUNT_JSON=$vertex_key
  fi
fi

if [ "$profile" = redis ]; then
  exec docker-entrypoint.sh redis-server --appendonly yes --requirepass "$REDIS_PASSWORD"
fi

if [ "$profile" = postgres ]; then
  exec "$@"
fi

if [ "$profile" = certification ] || [ "$profile" = artifact-import ]; then
  [ "$(id -u)" = "${P1_OPERATOR_UID:?missing operator uid}" ] || {
    echo "p1 secret loading failed: invalid_operator_uid" >&2
    exit 1
  }
  exec "$@"
fi

if [ "$(id -u)" = 0 ]; then
  exec setpriv --reuid 65532 --regid 65532 --clear-groups "$@"
fi
[ "$(id -u)" = 65532 ] || {
  echo "p1 secret loading failed: invalid_runtime_uid" >&2
  exit 1
}
exec "$@"
