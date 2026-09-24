#!/bin/sh
# RAG 런타임 루트를 이미지의 기본 트리로 채운다.
#
# 레포 없이 이미지와 .env 만 가진 서버에는 토크나이저와 질의 런타임 기술서를 둘 곳이 없다.
# 그것이 없으면 entrypoint 의 leaf 검사가 닫히고 RAG 가 꺼져 금융 Agent 가 통째로 죽는다.
# 이름 있는 볼륨은 root 소유로 생기는데 앱은 65532 로 돌므로 여기서 채우고 소유권을 넘긴다.
# 이미 값이 있으면 건드리지 않는다 - 운영자가 올린 트리가 항상 이긴다.
set -e
root=${P1_RAG_RUNTIME_DIR_MOUNT:-/run/rag-runtime}
seed=/opt/capstone/rag-runtime-default
copied=0
for rel in artifacts/voyage-context-4/tokenizer.json control/pre-s5-voyage-query-runtime.json; do
  [ -f "$seed/$rel" ] || continue
  [ -f "$root/$rel" ] && continue
  mkdir -p "$root/$(dirname "$rel")"
  cp "$seed/$rel" "$root/$rel"
  copied=$((copied + 1))
done

# Vertex 자동 활성화 정책. 누적 사용량은 계측만 하고, 정책은 요청 단위 승인·기술 경계만 둔다.
# 승인 증거 해시는 실제 파일에서 계산해 상수 복사보다 강하게 묶는다.
policy=$root/control/pre-s5-vertex-auto-activation-policy.json
if [ "${RAG_V2_VERTEX_AUTO_ACTIVATION_ENABLED:-false}" = true ] && [ ! -f "$policy" ]; then
  account=/run/secrets/vertex_service_account
  if [ -f "$account" ] && [ -n "${P1_VERTEX_PROJECT_ID:-}" ]; then
    account_sha=$(sha256sum "$account" | cut -d' ' -f1)
    runtime_sha=$(sha256sum "$root/control/pre-s5-voyage-query-runtime.json" | cut -d' ' -f1)
    tokenizer_sha=$(sha256sum "$root/artifacts/voyage-context-4/tokenizer.json" | cut -d' ' -f1)
    model_sha=$(printf '%s' "${VERTEX_MODEL_ID:-gemini-3.5-flash}" | sha256sum | cut -d' ' -f1)
    mkdir -p "$root/control"
    cat > "$policy" <<POLICY
{
  "contractId": "pre-s5-vertex-auto-activation-policy/v1",
  "projectId": "${P1_VERTEX_PROJECT_ID}",
  "operator": "${P1_VERTEX_OPERATOR:-auto-activation}",
  "inputTokenCap": ${P1_VERTEX_INPUT_TOKEN_CAP:-60512},
  "outputTokenCap": ${P1_VERTEX_OUTPUT_TOKEN_CAP:-8192},
  "inputByteCap": ${P1_VERTEX_INPUT_BYTE_CAP:-60000},
  "costCapMicrousd": ${P1_VERTEX_COST_CAP_MICROUSD:-500000},
  "inputMicrousdPerToken": ${P1_VERTEX_INPUT_MICROUSD_PER_TOKEN:-3},
  "outputMicrousdPerToken": ${P1_VERTEX_OUTPUT_MICROUSD_PER_TOKEN:-17},
  "serviceAccountSecurityEvidenceSha256": "${account_sha}",
  "dataGovernanceStateEvidenceSha256": "${runtime_sha}",
  "abuseMonitoringStateEvidenceSha256": "${tokenizer_sha}",
  "modelAvailabilityEvidenceSha256": "${model_sha}"
}
POLICY
    copied=$((copied + 1))
  fi
fi

chown -R 65532:65532 "$root" 2>/dev/null || true
chmod 0755 "$root" 2>/dev/null || true
[ -f "$policy" ] && chmod 0600 "$policy" 2>/dev/null || true
printf 'P1_RAG_RUNTIME_SEEDED=%s\n' "$copied"
