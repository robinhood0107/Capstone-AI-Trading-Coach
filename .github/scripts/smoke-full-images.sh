#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

images=$1
scratch=$(mktemp -d)
P1_PROJECT_NAME=mars-ci-smoke-$(openssl rand -hex 6)
export P1_PROJECT_NAME P1_STATE_DIR=$scratch/state
export P1_SPRING_IMAGE=$images:api P1_PYTHON_IMAGE=$images:api
export P1_POSTGRES_IMAGE=$images:postgres P1_REDIS_IMAGE=$images:redis P1_DASHBOARD_IMAGE=$images:web
export MARS_PUBLIC_SURFACE_MODE=LOCAL
export P1_KIS_OFFLINE=1 P1_KIS_MOCK_ONLINE_ENABLED=false P1_AUTOMATION_RUNTIME_ENABLED=false
export P1_RAG_V2_ENABLED=false P1_RAG_V2_VERTEX_ENABLED=false P1_RAG_V2_VERTEX_AUTO_ACTIVATION_ENABLED=false
export P1_STRONG_LLM_ENABLED=false P1_API_PORT=28080 P1_DASHBOARD_PORT=23000

compose=(docker compose --project-name "$P1_PROJECT_NAME" --env-file "$P1_STATE_DIR/runtime.env" -f deploy/p1/compose.yml)
cleanup() {
  if [[ -f $P1_STATE_DIR/runtime.env ]]; then
    "${compose[@]}" down --remove-orphans >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

./deploy/p1/p1ctl init
"${compose[@]}" up -d --wait --wait-timeout 90 --no-build --pull never postgres redis
for job in role-bootstrap migrate seed-import identity-bootstrap; do
  "${compose[@]}" run --rm --no-deps --pull never "$job"
done
"${compose[@]}" up -d --wait --wait-timeout 180 --no-build --pull never \
  actor-authority decision-platform experience-dashboard
./capstone smoke
version=$("${compose[@]}" exec -T postgres psql -X -v ON_ERROR_STOP=1 -U postgres -d capstone_p1 -At \
  -c 'select version from public.flyway_schema_history where success and version is not null order by installed_rank desc limit 1')
[[ $version =~ ^[0-9]+$ ]]
printf 'MARS_FULL_SMOKE=PASS FLYWAY_VERSION=%s\n' "$version"
