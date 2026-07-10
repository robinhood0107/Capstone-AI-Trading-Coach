#!/bin/sh
set -eu

# config injection과 실수로 짧은 local password를 쓰는 경우를 시작 전에 거부한다.
case "${REDIS_PASSWORD:-}" in
  ""|*[!A-Za-z0-9._~-]*)
    echo "REDIS_PASSWORD must be 32-128 URL-safe ASCII characters" >&2
    exit 1
    ;;
esac
if [ "${#REDIS_PASSWORD}" -lt 32 ] || [ "${#REDIS_PASSWORD}" -gt 128 ]; then
  echo "REDIS_PASSWORD must be 32-128 URL-safe ASCII characters" >&2
  exit 1
fi

umask 077
config_file="$(mktemp)"
{
  echo "bind 0.0.0.0 ::"
  echo "protected-mode yes"
  echo "appendonly yes"
  echo "dir /data"
  echo "maxmemory 256mb"
  echo "maxmemory-policy noeviction"
  printf 'requirepass %s\n' "$REDIS_PASSWORD"
} > "$config_file"
chown redis:redis "$config_file"

# 공식 entrypoint에 다시 위임해 /data 권한 정리와 redis uid/gid 권한 강등을 그대로 적용한다.
exec /usr/local/bin/docker-entrypoint.sh redis-server "$config_file"
