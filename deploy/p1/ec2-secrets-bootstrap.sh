#!/usr/bin/env bash
set -euo pipefail

if [[ $# != 2 ]]; then
  echo "usage: $0 SECRET_ID DESTINATION" >&2
  exit 64
fi

secret_id=$1
destination=$2
parent=$(dirname -- "$destination")
install -d -m 0700 -- "$parent" "$destination"

work_dir=$(mktemp -d)
trap 'rm -rf -- "$work_dir"' EXIT
bundle="$work_dir/bundle.json"

umask 077
aws secretsmanager get-secret-value \
  --secret-id "$secret_id" \
  --query SecretString \
  --output text >"$bundle"

jq -e 'type == "object" and all(keys[]; test("^[A-Za-z0-9._-]+$")) and all(.[]; type == "string")' \
  "$bundle" >/dev/null

while IFS=$'\t' read -r name encoded; do
  target="$destination/$name"
  temporary="$work_dir/$name"
  printf '%s' "$encoded" | base64 --decode >"$temporary"
  chmod 0600 "$temporary"
  install -m 0600 -- "$temporary" "$target"
done < <(jq -r 'to_entries[] | [.key, .value] | @tsv' "$bundle")

