#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

export P1_POSTGRES_IMAGE=$1
scratch=$(mktemp -d)
export P1_STATE_DIR=$scratch/state TMPDIR=$scratch/staging
P1_PROJECT_NAME=mars-backup-qa-$(openssl rand -hex 6)
export P1_PROJECT_NAME
mkdir "$TMPDIR"
python3 deploy/p1/release_guard.py state-init "$P1_STATE_DIR"
{
  printf 'POSTGRES_PASSWORD=%s\n' "$(openssl rand -hex 32)"
  while IFS= read -r key; do
    printf '%s=%s\n' "$key" "$(openssl rand -hex 32)"
  done < <(sed -nE 's/^: "\$\{(POSTGRES_[A-Z_]+_PASSWORD):\?.*/\1/p' infra/init/02-application-roles.sh)
} > "$P1_STATE_DIR/secrets/postgres.env"
chmod 0640 "$P1_STATE_DIR/secrets/postgres.env"
printf 'P1_SECRET_GID=%s\nP1_SECRETS_DIR=%s/secrets\n' "$(id -g)" "$P1_STATE_DIR" > "$P1_STATE_DIR/runtime.env"

compose=(docker compose --project-name "$P1_PROJECT_NAME" --env-file "$P1_STATE_DIR/runtime.env" -f deploy/p1/compose.yml)
cleanup() { "${compose[@]}" down --remove-orphans >/dev/null 2>&1 || true; }
trap cleanup EXIT
"${compose[@]}" up -d --wait --wait-timeout 90 --no-build --pull never postgres
sql() {
  "${compose[@]}" exec -T postgres psql -X -v ON_ERROR_STOP=1 -U postgres -d capstone_p1
}
sql <<'SQL'
create table public.flyway_schema_history (installed_rank int, version text, success bool);
insert into public.flyway_schema_history values (1, '213', true);
create table public.users (user_id text primary key, username text, role text, status text, security_version int);
alter table public.users owner to flyway;
insert into public.users values ('usr_demo_user','demo-user','ADMIN','ACTIVE',1);
create function public.persist_decision_bundle_authorized(text,jsonb) returns int language sql as 'select 1';
alter function public.persist_decision_bundle_authorized(text,jsonb) owner to flyway;
create table public.backup_qa_data as
select id, (select string_agg(gen_random_bytes(1024), ''::bytea) from generate_series(1, 32 + id % 2)) as payload
from generate_series(1, 256) as ids(id);
SQL
dump=$(./capstone backup)
dump=${dump#P1_BACKUP_CREATED=}
[[ $(stat -c %s "$dump") -gt 4194304 && $(stat -c %a "$dump") == 600 ]]
(cd "$(dirname "$dump")" && sha256sum --check "$(basename "$dump").sha256")
./capstone restore-test | grep -E '^P1_RESTORE_TEST=PASS '
invalid=$P1_STATE_DIR/backups/capstone-p1-20000101T000000Z.dump
cp -- "$dump" "$invalid"
printf '%064d  %s\n' 0 "$(basename "$invalid")" > "$invalid.sha256"
if P1_BACKUP_FILE=$(basename "$invalid") ./capstone restore-test >"$scratch/rejected.log" 2>&1; then
  echo 'Checksum mismatch unexpectedly restored' >&2
  exit 1
fi
grep -Fx 'P1_ERROR=backup_checksum_mismatch' "$scratch/rejected.log"
[[ -z $(find "$TMPDIR" -mindepth 1 -print -quit) ]]

sql <<'SQL'
update public.flyway_schema_history set version='87';
update public.users set role='USER' where user_id='usr_demo_user';
insert into public.users values ('usr_demo_admin','demo-admin','ADMIN','ACTIVE',1);
create table public.p1_offline_demo_authority (authority_id text, active bool, credential_bundle_digest text);
insert into public.p1_offline_demo_authority values ('P1_OFFLINE_DEMO',true,repeat('a',64));
SQL
legacy=$(./deploy/p1/p1ctl backup)
legacy=${legacy#P1_BACKUP_CREATED=}
P1_BACKUP_FILE=$(basename "$legacy") ./deploy/p1/p1ctl restore-test | grep -E '^P1_RESTORE_TEST=PASS '
if P1_BACKUP_FILE=$(basename "$legacy") ./capstone restore-test >"$scratch/rejected.log" 2>&1; then
  echo 'FULL restore unexpectedly accepted V87' >&2
  exit 1
fi
grep -Fx 'P1_ERROR=restore_version' "$scratch/rejected.log"
[[ -z $(find "$TMPDIR" -mindepth 1 -print -quit) ]]
printf 'MARS_BACKUP_QA=PASS\n'
