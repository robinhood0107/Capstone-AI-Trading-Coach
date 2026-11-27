#!/usr/bin/env bash
set -Eeuo pipefail

version=0.70.0
asset="trivy_${version}_Linux-64bit.tar.gz"
tool_dir=$(mktemp -d "$RUNNER_TEMP/mars-trivy.XXXXXX")
archive="$tool_dir/$asset"

gh release download "v$version" -R aquasecurity/trivy --pattern "$asset" --output "$archive"
unset GH_TOKEN
printf '%s  %s\n' '8b4376d5d6befe5c24d503f10ff136d9e0c49f9127a4279fd110b727929a5aa9' "$archive" |
  sha256sum --check --strict
tar -xzf "$archive" -C "$tool_dir" trivy
"$tool_dir/trivy" --version --format json | jq -e --arg version "$version" '.Version == $version'
echo "$tool_dir" >> "$GITHUB_PATH"
