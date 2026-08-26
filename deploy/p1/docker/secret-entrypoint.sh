#!/bin/sh
set -eu

profile=${1:?secret profile is required}
shift

case "$profile" in
  spring) secret_files=/run/secrets/spring_env ;;
  decision-platform|certification) secret_files="/run/secrets/spring_env /run/secrets/python_env /run/secrets/kis_mock_env" ;;
  authority) secret_files=/run/secrets/actor_capability_authority_env ;;
  role-bootstrap) secret_files=/run/secrets/role_bootstrap_env ;;
  migration) secret_files=/run/secrets/migration_env ;;
  seed-import) secret_files=/run/secrets/seed_import_env ;;
  artifact-import) secret_files=/run/secrets/artifact_import_env ;;
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
    postgres:POSTGRES_PASSWORD|postgres:POSTGRES_APP_PASSWORD|postgres:POSTGRES_MIGRATION_PASSWORD|postgres:POSTGRES_COLLECTOR_PASSWORD|postgres:POSTGRES_DISCLOSURE_READER_PASSWORD|postgres:POSTGRES_MARKET_WRITER_PASSWORD|postgres:POSTGRES_PORTFOLIO_WRITER_PASSWORD|postgres:POSTGRES_RISK_WRITER_PASSWORD|postgres:POSTGRES_FILL_WRITER_PASSWORD|postgres:POSTGRES_RAG_WRITER_PASSWORD|postgres:POSTGRES_RAG_ADMIN_PASSWORD|postgres:POSTGRES_RAG_QUERY_PASSWORD|postgres:POSTGRES_SIGNAL_WRITER_PASSWORD|postgres:POSTGRES_SIGNAL_SCHEDULER_PASSWORD|postgres:POSTGRES_SIGNAL_ADMIN_PASSWORD|postgres:POSTGRES_WORKER_PASSWORD|postgres:POSTGRES_OUTBOX_PUBLISHER_PASSWORD|postgres:POSTGRES_POISON_RECORDER_PASSWORD|postgres:POSTGRES_REPLAY_PASSWORD|postgres:POSTGRES_IDENTITY_PASSWORD|postgres:POSTGRES_AUTH_PASSWORD|postgres:POSTGRES_REPLAY_AUTHORIZER_PASSWORD|postgres:POSTGRES_DEMO_PASSWORD) return 0 ;;
    role-bootstrap:POSTGRES_ADMIN_USER|role-bootstrap:POSTGRES_PASSWORD|role-bootstrap:POSTGRES_AUTH_PASSWORD|role-bootstrap:POSTGRES_OUTBOX_PUBLISHER_PASSWORD|role-bootstrap:POSTGRES_POISON_RECORDER_PASSWORD) return 0 ;;
    spring:POSTGRES_APP_PASSWORD|spring:POSTGRES_WORKER_PASSWORD|spring:POSTGRES_AUTH_PASSWORD|spring:ACTOR_CAPABILITY_SHARED_SECRET|spring:ACTOR_CAPABILITY_PUBLIC_KEY|spring:REDIS_PASSWORD|spring:JWT_SECRET|spring:JWT_ISSUER|spring:JWT_AUDIENCE|spring:LOGIN_SCOPE_HMAC_KEY|spring:PRINCIPLE_CURSOR_HMAC_KEY|spring:DECISION_IDEMPOTENCY_SCOPE_HMAC_KEY|spring:DECISION_GRPC_SHARED_SECRET|spring:BROKERAGE_IDEMPOTENCY_SCOPE_HMAC_KEY|spring:RAG_IDEMPOTENCY_SCOPE_HMAC_KEY|spring:RAG_REQUEST_FINGERPRINT_HMAC_KEY|spring:RAG_PROVIDER_USAGE_HMAC_KEY|spring:RAG_RATE_LIMIT_HMAC_KEY|spring:RAG_HISTORY_CURSOR_HMAC_KEY|spring:DEMO_CREDENTIAL_SEPARATION_KEY|spring:DEMO_USER_CREDENTIAL_BUNDLE|spring:DEMO_ADMIN_CREDENTIAL_BUNDLE|spring:ASYNC_CURSOR_HMAC_KEY|spring:ASYNC_PARTITION_HMAC_KEY|spring:ASYNC_WORKER_GRPC_SHARED_SECRET) return 0 ;;
    decision-platform:POSTGRES_APP_PASSWORD|decision-platform:POSTGRES_WORKER_PASSWORD|decision-platform:POSTGRES_AUTH_PASSWORD|decision-platform:ACTOR_CAPABILITY_SHARED_SECRET|decision-platform:ACTOR_CAPABILITY_PUBLIC_KEY|decision-platform:ACTOR_CAPABILITY_TLS_KEY_STORE_PASSWORD|decision-platform:REDIS_PASSWORD|decision-platform:JWT_SECRET|decision-platform:JWT_ISSUER|decision-platform:JWT_AUDIENCE|decision-platform:LOGIN_SCOPE_HMAC_KEY|decision-platform:PRINCIPLE_CURSOR_HMAC_KEY|decision-platform:DECISION_IDEMPOTENCY_SCOPE_HMAC_KEY|decision-platform:DECISION_GRPC_SHARED_SECRET|decision-platform:BROKERAGE_IDEMPOTENCY_SCOPE_HMAC_KEY|decision-platform:BROKERAGE_GRPC_SHARED_SECRET|decision-platform:RAG_IDEMPOTENCY_SCOPE_HMAC_KEY|decision-platform:RAG_REQUEST_FINGERPRINT_HMAC_KEY|decision-platform:RAG_PROVIDER_USAGE_HMAC_KEY|decision-platform:RAG_RATE_LIMIT_HMAC_KEY|decision-platform:RAG_HISTORY_CURSOR_HMAC_KEY|decision-platform:DEMO_CREDENTIAL_SEPARATION_KEY|decision-platform:DEMO_USER_CREDENTIAL_BUNDLE|decision-platform:DEMO_ADMIN_CREDENTIAL_BUNDLE|decision-platform:ASYNC_CURSOR_HMAC_KEY|decision-platform:ASYNC_PARTITION_HMAC_KEY|decision-platform:ASYNC_WORKER_GRPC_SHARED_SECRET|decision-platform:ASYNC_WORKER_DATABASE_DSN|decision-platform:KAFKA_SASL_USERNAME|decision-platform:KAFKA_SASL_PASSWORD|decision-platform:KAFKA_ENVELOPE_PUBLIC_KEY|decision-platform:POISON_RECORDER_URL|decision-platform:POISON_RECORDER_SHARED_SECRET|decision-platform:KIS_MOCK_CONFIGURED|decision-platform:KIS_MOCK_APP_KEY|decision-platform:KIS_MOCK_APP_SECRET|decision-platform:KIS_MOCK_ACCOUNT_NO|decision-platform:KIS_MOCK_BOUND_ACCOUNT_ID|decision-platform:KIS_MOCK_ORDER_REFERENCE_KEY|decision-platform:KIS_BROKERAGE_TOKEN_P_PHYSICAL_CAP|decision-platform:KIS_BROKERAGE_PHYSICAL_CAP) return 0 ;;
    authority:POSTGRES_IDENTITY_PASSWORD|authority:ACTOR_CAPABILITY_SHARED_SECRET|authority:ACTOR_CAPABILITY_PRIVATE_KEY|authority:ACTOR_CAPABILITY_PUBLIC_KEY|authority:ACTOR_CAPABILITY_TLS_KEY_STORE_PASSWORD) return 0 ;;
    migration:POSTGRES_MIGRATION_PASSWORD|migration:BROKERAGE_DB_CAPABILITY_TOKEN_SHA256|migration:DEMO_CREDENTIAL_SEPARATION_KEY|migration:DEMO_USER_CREDENTIAL_BUNDLE|migration:DEMO_ADMIN_CREDENTIAL_BUNDLE) return 0 ;;
    seed-import:P1_SEED_DATABASE_DSN) return 0 ;;
    artifact-import:P1_ARTIFACT_IMPORT_DATABASE_DSN) return 0 ;;
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
  [ "$key_profile" != certification ] || key_profile=decision-platform
  case "$key_profile" in
    postgres) printf '%s\n' 'POSTGRES_PASSWORD POSTGRES_APP_PASSWORD POSTGRES_MIGRATION_PASSWORD POSTGRES_COLLECTOR_PASSWORD POSTGRES_DISCLOSURE_READER_PASSWORD POSTGRES_MARKET_WRITER_PASSWORD POSTGRES_PORTFOLIO_WRITER_PASSWORD POSTGRES_RISK_WRITER_PASSWORD POSTGRES_FILL_WRITER_PASSWORD POSTGRES_RAG_WRITER_PASSWORD POSTGRES_RAG_ADMIN_PASSWORD POSTGRES_RAG_QUERY_PASSWORD POSTGRES_SIGNAL_WRITER_PASSWORD POSTGRES_SIGNAL_SCHEDULER_PASSWORD POSTGRES_SIGNAL_ADMIN_PASSWORD POSTGRES_WORKER_PASSWORD POSTGRES_OUTBOX_PUBLISHER_PASSWORD POSTGRES_POISON_RECORDER_PASSWORD POSTGRES_REPLAY_PASSWORD POSTGRES_IDENTITY_PASSWORD POSTGRES_AUTH_PASSWORD POSTGRES_REPLAY_AUTHORIZER_PASSWORD POSTGRES_DEMO_PASSWORD' ;;
    role-bootstrap) printf '%s\n' 'POSTGRES_ADMIN_USER POSTGRES_PASSWORD POSTGRES_AUTH_PASSWORD POSTGRES_OUTBOX_PUBLISHER_PASSWORD POSTGRES_POISON_RECORDER_PASSWORD' ;;
    spring) printf '%s\n' 'POSTGRES_APP_PASSWORD POSTGRES_WORKER_PASSWORD POSTGRES_AUTH_PASSWORD ACTOR_CAPABILITY_SHARED_SECRET ACTOR_CAPABILITY_PUBLIC_KEY REDIS_PASSWORD JWT_SECRET JWT_ISSUER JWT_AUDIENCE LOGIN_SCOPE_HMAC_KEY PRINCIPLE_CURSOR_HMAC_KEY DECISION_IDEMPOTENCY_SCOPE_HMAC_KEY DECISION_GRPC_SHARED_SECRET BROKERAGE_IDEMPOTENCY_SCOPE_HMAC_KEY RAG_IDEMPOTENCY_SCOPE_HMAC_KEY RAG_REQUEST_FINGERPRINT_HMAC_KEY RAG_PROVIDER_USAGE_HMAC_KEY RAG_RATE_LIMIT_HMAC_KEY RAG_HISTORY_CURSOR_HMAC_KEY DEMO_CREDENTIAL_SEPARATION_KEY DEMO_USER_CREDENTIAL_BUNDLE DEMO_ADMIN_CREDENTIAL_BUNDLE ASYNC_CURSOR_HMAC_KEY ASYNC_PARTITION_HMAC_KEY ASYNC_WORKER_GRPC_SHARED_SECRET' ;;
    decision-platform) printf '%s\n' 'POSTGRES_APP_PASSWORD POSTGRES_WORKER_PASSWORD POSTGRES_AUTH_PASSWORD ACTOR_CAPABILITY_SHARED_SECRET ACTOR_CAPABILITY_PUBLIC_KEY ACTOR_CAPABILITY_TLS_KEY_STORE_PASSWORD REDIS_PASSWORD JWT_SECRET JWT_ISSUER JWT_AUDIENCE LOGIN_SCOPE_HMAC_KEY PRINCIPLE_CURSOR_HMAC_KEY DECISION_IDEMPOTENCY_SCOPE_HMAC_KEY DECISION_GRPC_SHARED_SECRET BROKERAGE_IDEMPOTENCY_SCOPE_HMAC_KEY BROKERAGE_GRPC_SHARED_SECRET RAG_IDEMPOTENCY_SCOPE_HMAC_KEY RAG_REQUEST_FINGERPRINT_HMAC_KEY RAG_PROVIDER_USAGE_HMAC_KEY RAG_RATE_LIMIT_HMAC_KEY RAG_HISTORY_CURSOR_HMAC_KEY DEMO_CREDENTIAL_SEPARATION_KEY DEMO_USER_CREDENTIAL_BUNDLE DEMO_ADMIN_CREDENTIAL_BUNDLE ASYNC_CURSOR_HMAC_KEY ASYNC_PARTITION_HMAC_KEY ASYNC_WORKER_GRPC_SHARED_SECRET ASYNC_WORKER_DATABASE_DSN KAFKA_SASL_USERNAME KAFKA_SASL_PASSWORD KAFKA_ENVELOPE_PUBLIC_KEY POISON_RECORDER_URL POISON_RECORDER_SHARED_SECRET KIS_MOCK_CONFIGURED KIS_MOCK_APP_KEY KIS_MOCK_APP_SECRET KIS_MOCK_ACCOUNT_NO KIS_MOCK_BOUND_ACCOUNT_ID KIS_MOCK_ORDER_REFERENCE_KEY KIS_BROKERAGE_TOKEN_P_PHYSICAL_CAP KIS_BROKERAGE_PHYSICAL_CAP' ;;
    authority) printf '%s\n' 'POSTGRES_IDENTITY_PASSWORD ACTOR_CAPABILITY_SHARED_SECRET ACTOR_CAPABILITY_PRIVATE_KEY ACTOR_CAPABILITY_PUBLIC_KEY ACTOR_CAPABILITY_TLS_KEY_STORE_PASSWORD' ;;
    migration) printf '%s\n' 'POSTGRES_MIGRATION_PASSWORD BROKERAGE_DB_CAPABILITY_TOKEN_SHA256 DEMO_CREDENTIAL_SEPARATION_KEY DEMO_USER_CREDENTIAL_BUNDLE DEMO_ADMIN_CREDENTIAL_BUNDLE' ;;
    seed-import) printf '%s\n' 'P1_SEED_DATABASE_DSN' ;;
    artifact-import) printf '%s\n' 'P1_ARTIFACT_IMPORT_DATABASE_DSN' ;;
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
while IFS='=' read -r key value || [ -n "$key$value" ]; do
  if [ -z "$key" ] || ! allowed_key "$key"; then
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

if [ "$profile" = decision-platform ] || [ "$profile" = certification ]; then
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
