"""Generate the exact Team A backend acceptance catalog and typed client."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Final


ROOT: Final = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from contracts.generate_principle_contracts import (  # noqa: E402
    ContractValidationError,
    canonical_json_bytes,
)

OPENAPI_PATH: Final = ROOT / "contracts/openapi/openapi.json"
CATALOG_PATH: Final = ROOT / "contracts/catalogs/p1-team-a-acceptance.v1.json"
BADGE_PATH: Final = ROOT / "contracts/catalogs/p1-ui-evidence-badges.v1.json"
CLIENT_PATH: Final = (
    ROOT / "workspaces/experience-dashboard/src/shared/api/generated/p1-team-a-client.v1.ts"
)
HTTP_METHODS: Final = frozenset(
    {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
)
CLIENT_PARAMETER_OVERRIDES: Final[dict[str, tuple[dict[str, Any], ...]]] = {
    # Historical exact-48 OpenAPI omitted the two strict parser query fields. The
    # root bytes stay frozen; the Team A client records this bounded adapter fact.
    "getMockBuyable": (
        {
            "in": "query",
            "name": "price",
            "required": True,
            "schema": {"minimum": 1, "type": "integer"},
        },
        {
            "in": "query",
            "name": "symbol",
            "required": True,
            "schema": {"pattern": "^[0-9A-Z._:-]{1,20}$", "type": "string"},
        },
    )
}

# The order is the user journey order, not lexicographic OpenAPI order.
EXPECTED_OPERATIONS: Final[tuple[tuple[str, str, str, str, tuple[int, ...]], ...]] = (
    ("CURRENT_SCREEN", "POST", "/api/v1/auth/login", "login", (200,)),
    ("CURRENT_SCREEN", "GET", "/api/v1/system/health", "health", (200,)),
    ("CURRENT_SCREEN", "GET", "/api/v1/principle-presets", "listPrinciplePresets", (200,)),
    ("TEAM_A_REQUIRED", "POST", "/api/v1/principles", "createPrinciple", (201,)),
    ("CURRENT_SCREEN", "GET", "/api/v1/principles", "listPrinciples", (200,)),
    ("CURRENT_SCREEN", "GET", "/api/v1/principles/{principleId}", "getPrinciple", (200,)),
    ("CURRENT_SCREEN", "PUT", "/api/v1/principles/{principleId}", "updatePrinciple", (200,)),
    ("CURRENT_SCREEN", "GET", "/api/v1/risk/portfolio", "getPortfolio", (200,)),
    ("TEAM_A_REQUIRED", "GET", "/api/v1/risk/kill-switch", "getKillSwitch", (200,)),
    ("TEAM_A_REQUIRED", "POST", "/api/v1/risk/kill-switch", "changeKillSwitch", (200,)),
    ("CURRENT_SCREEN", "GET", "/api/v2/signals/{symbol}", "read", (200,)),
    ("CURRENT_SCREEN", "GET", "/api/v1/rag/sources", "listSources", (200,)),
    ("TEAM_A_REQUIRED", "POST", "/api/v1/consents", "record", (200,)),
    ("CURRENT_SCREEN", "POST", "/api/v1/rag/ask", "ask", (200,)),
    ("TEAM_A_REQUIRED", "POST", "/api/v1/rag/answers/{answerId}/feedback", "feedback", (200,)),
    ("CURRENT_SCREEN", "GET", "/api/v1/dashboard/rag-sources/{answerId}", "rag", (200,)),
    (
        "CURRENT_SCREEN",
        "GET",
        "/api/v1/dashboard/model-evaluations/{runId}",
        "modelEvaluation",
        (200,),
    ),
    ("CURRENT_SCREEN", "GET", "/api/v1/dashboard/backtests/{runId}", "backtest", (200,)),
    ("TEAM_A_REQUIRED", "POST", "/api/v1/decisions/evaluate-order", "evaluateOrder", (200,)),
    ("CURRENT_SCREEN", "GET", "/api/v1/decisions/{decisionId}", "getDecision", (200,)),
    ("CURRENT_SCREEN", "GET", "/api/v1/dashboard/risk-results/{decisionId}", "risk", (200,)),
    (
        "TEAM_A_REQUIRED",
        "GET",
        "/api/v1/brokerage/mock/accounts/{accountId}/balances",
        "getMockBalance",
        (200,),
    ),
    (
        "TEAM_A_REQUIRED",
        "GET",
        "/api/v1/brokerage/mock/accounts/{accountId}/buyable",
        "getMockBuyable",
        (200,),
    ),
    ("TEAM_A_REQUIRED", "POST", "/api/v1/brokerage/mock/orders", "submitMockOrder", (200,)),
    ("TEAM_A_REQUIRED", "GET", "/api/v1/brokerage/orders/{orderId}", "getOrder", (200,)),
    ("TEAM_A_REQUIRED", "POST", "/api/v1/brokerage/orders/{orderId}/cancel", "cancelOrder", (200,)),
    (
        "TEAM_A_REQUIRED",
        "GET",
        "/api/v1/brokerage/mock/accounts/{accountId}/fills",
        "mockFills",
        (200,),
    ),
    ("TEAM_A_REQUIRED", "GET", "/api/v1/automation/status", "getAutomationStatus", (200,)),
    ("TEAM_A_REQUIRED", "POST", "/api/v1/automation/arm", "armAutomation", (200,)),
    ("TEAM_A_REQUIRED", "GET", "/api/v1/automation/runs", "listAutomationRuns", (200,)),
    ("TEAM_A_REQUIRED", "POST", "/api/v1/automation/disarm", "disarmAutomation", (200,)),
    ("TEAM_A_REQUIRED", "POST", "/api/v1/journals", "createJournal", (200,)),
    ("TEAM_A_REQUIRED", "GET", "/api/v1/journals", "listJournals", (200,)),
)


class ContractError(ValueError):
    """Raised when the exact root/subset contract drifts."""


def canonical_json(payload: object) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def object_value(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{label} must be an object")
    return value


def operations(
    openapi: Mapping[str, Any], expected_count: int = 56
) -> dict[tuple[str, str], dict[str, Any]]:
    paths = object_value(openapi.get("paths"), "paths")
    result: dict[tuple[str, str], dict[str, Any]] = {}
    ids: set[str] = set()
    for path, raw_item in paths.items():
        item = object_value(raw_item, f"path {path}")
        for method, raw_operation in item.items():
            if method not in HTTP_METHODS:
                continue
            operation = object_value(raw_operation, f"operation {method} {path}")
            operation_id = operation.get("operationId")
            if not isinstance(operation_id, str) or not operation_id or operation_id in ids:
                raise ContractError("root operationId inventory is missing or duplicated")
            ids.add(operation_id)
            result[(method.upper(), path)] = operation
    if len(result) != expected_count or len(ids) != expected_count:
        raise ContractError(
            f"root OpenAPI must contain exact {expected_count} unique operations"
        )
    return result


def badge_contract() -> dict[str, Any]:
    return {
        "contractId": "p1-ui-evidence-badges.v1",
        "brokerageModes": {
            "INTERNAL_PAPER": {
                "labelEn": "Internal paper ledger",
                "labelKo": "내부 가상원장",
                "providerPhysicalCalls": 0,
            },
            "KIS_MOCK": {
                "automaticFallbackToInternalPaper": False,
                "labelEn": "KIS mock account",
                "labelKo": "KIS 모의계좌",
            },
        },
        "modelBadges": {
            "LIGHTGBM": {
                "label": "RESEARCH_ONLY",
                "orderAuthority": "NONE",
                "productionSignalAuthority": "NONE",
            }
        },
        "teamBEvidence": {
            "REAL_TEAM_B": {
                "fixture": False,
                "labelEn": "Real Team B artifact",
                "labelKo": "Team B 실제 산출물",
                "performanceClaimAllowed": True,
                "requiresImporterVerification": True,
            },
            "SYNTHETIC_GOLDEN": {
                "fixture": True,
                "labelEn": "Synthetic golden fixture",
                "labelKo": "합성 golden fixture",
                "performanceClaimAllowed": False,
                "promotableToReal": False,
            },
        },
        "vertexVerdicts": ["VETO_BUY", "NO_VETO", "ABSTAIN"],
    }


def build_catalog(
    openapi: Mapping[str, Any], openapi_bytes: bytes, badge_bytes: bytes
) -> dict[str, Any]:
    root_operations = operations(openapi)
    entries: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for sequence, (category, method, path, operation_id, statuses) in enumerate(
        EXPECTED_OPERATIONS, 1
    ):
        identity = (method, path)
        if identity in seen:
            raise ContractError("Team A operation identity is duplicated")
        seen.add(identity)
        operation = root_operations.get(identity)
        if operation is None or operation.get("operationId") != operation_id:
            raise ContractError(f"Team A operation drifted: {method} {path}")
        actual_success = tuple(
            sorted(
                int(code) for code in operation.get("responses", {}) if str(code).startswith("2")
            )
        )
        if not set(statuses).issubset(actual_success):
            raise ContractError(f"Team A success status drifted: {operation_id}")
        entry: dict[str, Any] = {
            "category": category,
            "expectedStatuses": list(statuses),
            "method": method,
            "operationId": operation_id,
            "path": path,
            "sequence": sequence,
        }
        if operation_id in CLIENT_PARAMETER_OVERRIDES:
            entry["clientParameterOverrides"] = list(CLIENT_PARAMETER_OVERRIDES[operation_id])
        entries.append(entry)
    if len(entries) != 33 or sum(entry["category"] == "CURRENT_SCREEN" for entry in entries) != 15:
        raise ContractError("Team A catalog must contain current 15 plus required 18")
    return {
        "acceptanceOperationCount": 33,
        "badgeContract": {
            "path": BADGE_PATH.relative_to(ROOT).as_posix(),
            "sha256": sha256(badge_bytes),
        },
        "contractId": "p1-team-a-acceptance.v1",
        "corsOrigins": ["http://localhost:3000", "http://127.0.0.1:3000"],
        "fixtureBoundary": {
            "brokerageProviderCalls": 0,
            "dashboardEvidenceMode": "SYNTHETIC_GOLDEN",
            "frontendFakeProductionResponse": False,
            "kisLiveCalls": 0,
            "vertexLiveCalls": 0,
        },
        "generatedClient": CLIENT_PATH.relative_to(ROOT).as_posix(),
        "operations": entries,
        "rootOpenApi": {
            "operationCount": 56,
            "path": OPENAPI_PATH.relative_to(ROOT).as_posix(),
            "sha256": sha256(openapi_bytes),
        },
        "runner": "./capstone team-a acceptance",
        "sameOriginPrefix": "/api",
        "stateRestoration": ["AUTOMATION_DISARMED", "KILL_SWITCH_RESTORED"],
    }


def ref_name(schema: Mapping[str, Any]) -> str | None:
    ref = schema.get("$ref")
    prefix = "#/components/schemas/"
    if not isinstance(ref, str) or not ref.startswith(prefix):
        return None
    suffix = ref.removeprefix(prefix)
    return suffix if "/" not in suffix else None


def expand_nested_refs(
    node: object,
    document: Mapping[str, Any],
    local_root: Mapping[str, Any] | None = None,
    depth: int = 0,
) -> object:
    if depth > 32:
        raise ContractError("nested OpenAPI reference depth exceeded")
    if isinstance(node, list):
        return [expand_nested_refs(value, document, local_root, depth) for value in node]
    if not isinstance(node, dict):
        return node
    ref = node.get("$ref")
    component_prefix = "#/components/schemas/"
    local_prefixes = ("#/$defs/", "#/definitions/")
    if isinstance(ref, str) and (
        (ref.startswith(component_prefix) and "/" in ref.removeprefix(component_prefix))
        or ref.startswith(local_prefixes)
    ):
        if ref.startswith(local_prefixes):
            if local_root is None:
                raise ContractError(f"local OpenAPI reference has no schema root: {ref}")
            target: object = local_root
            tokens = ref.removeprefix("#/").split("/")
            target_root = local_root
        else:
            target = document
            tokens = ref.removeprefix("#/").split("/")
            component_name = tokens[2]
            target_root = object_value(
                object_value(
                    object_value(document.get("components"), "components").get("schemas"), "schemas"
                ).get(component_name),
                f"component {component_name}",
            )
        for raw_token in tokens:
            token = raw_token.replace("~1", "/").replace("~0", "~")
            if not isinstance(target, dict) or token not in target:
                raise ContractError(f"nested OpenAPI reference is missing: {ref}")
            target = target[token]
        return expand_nested_refs(target, document, target_root, depth + 1)
    return {
        key: expand_nested_refs(value, document, local_root, depth) for key, value in node.items()
    }


def ts_property(name: str) -> str:
    return name if re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", name) else json.dumps(name)


def ts_type(schema: object) -> str:
    if not isinstance(schema, dict):
        return "unknown"
    referenced = ref_name(schema)
    if referenced is not None:
        return f'Components["{referenced}"]'
    if "const" in schema:
        return json.dumps(schema["const"], ensure_ascii=False)
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        return " | ".join(json.dumps(value, ensure_ascii=False) for value in enum)
    raw_type = schema.get("type")
    if isinstance(raw_type, list):
        return " | ".join(ts_type({**schema, "type": item}) for item in raw_type)
    if raw_type == "null":
        return "null"
    if raw_type == "string":
        return "string"
    if raw_type in {"integer", "number"}:
        return "number"
    if raw_type == "boolean":
        return "boolean"
    if raw_type == "array":
        return f"ReadonlyArray<{ts_type(schema.get('items'))}>"
    properties = schema.get("properties")
    if raw_type == "object" or isinstance(properties, dict):
        if not isinstance(properties, dict) or not properties:
            additional = schema.get("additionalProperties")
            return (
                f"Readonly<Record<string, {ts_type(additional)}>>"
                if isinstance(additional, dict)
                else "Readonly<Record<string, unknown>>"
            )
        required = set(schema.get("required", []))
        fields = [
            f"readonly {ts_property(name)}{'?' if name not in required else ''}: {ts_type(value)};"
            for name, value in sorted(properties.items())
        ]
        return "{ " + " ".join(fields) + " }"
    for combinator in ("oneOf", "anyOf"):
        variants = schema.get(combinator)
        if isinstance(variants, list) and variants:
            return " | ".join(f"({ts_type(item)})" for item in variants)
    variants = schema.get("allOf")
    if isinstance(variants, list) and variants:
        return " & ".join(f"({ts_type(item)})" for item in variants)
    return "unknown"


def referenced_components(
    seed_schemas: Iterable[object], components: Mapping[str, Any]
) -> list[str]:
    pending = list(seed_schemas)
    result: set[str] = set()
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            name = ref_name(node)
            if name is not None and name not in result:
                if name not in components:
                    raise ContractError(f"missing component {name}")
                result.add(name)
                pending.append(components[name])
            pending.extend(node.values())
        elif isinstance(node, list):
            pending.extend(node)
    return sorted(result)


def operation_parameters(
    path_item: Mapping[str, Any],
    operation: Mapping[str, Any],
    operation_id: str,
) -> list[dict[str, Any]]:
    values = [*path_item.get("parameters", []), *operation.get("parameters", [])]
    return [
        *(object_value(value, "parameter") for value in values),
        *CLIENT_PARAMETER_OVERRIDES.get(operation_id, ()),
    ]


def success_schema(operation: Mapping[str, Any], statuses: tuple[int, ...]) -> object:
    responses = object_value(operation.get("responses"), "responses")
    for status in statuses:
        response = object_value(responses.get(str(status)), f"response {status}")
        if response.get("content") is None:
            return {"type": "null"}
        content = object_value(response.get("content"), "response content")
        media = object_value(content.get("application/json"), "JSON response")
        return media.get("schema", {})
    raise ContractError("success response schema is missing")


def request_schema(operation: Mapping[str, Any]) -> object | None:
    body = operation.get("requestBody")
    if body is None:
        return None
    content = object_value(object_value(body, "requestBody").get("content"), "request content")
    return object_value(content.get("application/json"), "JSON request").get("schema", {})


def request_type(parameters: list[dict[str, Any]], body: object | None) -> str:
    fields: list[str] = []
    for location in ("path", "query"):
        selected = [parameter for parameter in parameters if parameter.get("in") == location]
        if not selected:
            continue
        members = []
        for parameter in sorted(selected, key=lambda value: str(value.get("name"))):
            name = str(parameter.get("name"))
            optional = "" if parameter.get("required") else "?"
            members.append(
                f"readonly {ts_property(name)}{optional}: {ts_type(parameter.get('schema'))};"
            )
        fields.append(f"readonly {location}: {{ {' '.join(members)} }};")
    if body is not None:
        fields.append(f"readonly body: {ts_type(body)};")
    if any(
        parameter.get("in") == "header" and parameter.get("name") == "X-Idempotency-Key"
        for parameter in parameters
    ):
        fields.append("readonly idempotencyKey: string;")
    return "{ " + " ".join(fields) + " }" if fields else "Record<string, never>"


def generate_client(
    openapi: Mapping[str, Any],
    *,
    expected_operations: tuple[tuple[str, str, str, str, tuple[int, ...]], ...] = EXPECTED_OPERATIONS,
    expected_root_count: int = 56,
    generated_by: str = "contracts/generate_p1_team_a_acceptance.py",
) -> bytes:
    root_operations = operations(openapi, expected_root_count)
    paths = object_value(openapi.get("paths"), "paths")
    raw_components = object_value(
        object_value(openapi.get("components"), "components").get("schemas"), "schemas"
    )
    components = {
        name: expand_nested_refs(schema, openapi, object_value(schema, f"component {name}"))
        for name, schema in raw_components.items()
    }
    seeds: list[object] = []
    request_lines: list[str] = []
    response_lines: list[str] = []
    spec_lines: list[str] = []
    for _, method, path, operation_id, statuses in expected_operations:
        path_item = object_value(paths[path], path)
        operation = root_operations[(method, path)]
        parameters = operation_parameters(path_item, operation, operation_id)
        body = request_schema(operation)
        response = success_schema(operation, statuses)
        seeds.extend([*(parameter.get("schema") for parameter in parameters), body, response])
        request_lines.append(f"  readonly {operation_id}: {request_type(parameters, body)};")
        response_lines.append(f"  readonly {operation_id}: {ts_type(response)};")
        spec_lines.append(
            f'  {operation_id}: {{ method: "{method}", path: "{path}", expectedStatuses: [{", ".join(map(str, statuses))}] }},'
        )
    component_names = referenced_components(seeds, components)
    component_lines = [
        f'  readonly "{name}": {ts_type(components[name])};' for name in component_names
    ]
    source = f"""// Generated by {generated_by}. Do not edit.
export interface Components {{
{chr(10).join(component_lines)}
}}

export interface TeamARequests {{
{chr(10).join(request_lines)}
}}

export interface TeamAResponses {{
{chr(10).join(response_lines)}
}}

export const teamAOperations = {{
{chr(10).join(spec_lines)}
}} as const;

export type TeamAOperationId = keyof TeamARequests & keyof TeamAResponses;

export interface TeamAResult<K extends TeamAOperationId> {{
  readonly status: number;
  readonly body: TeamAResponses[K];
}}

export class TeamAHttpError extends Error {{
  constructor(readonly operationId: TeamAOperationId, readonly status: number) {{
    super(`Team A operation ${{operationId}} failed with HTTP ${{status}}.`);
    this.name = 'TeamAHttpError';
  }}
}}

export interface TeamAClientOptions {{
  readonly baseUrl?: string;
  readonly fetchImpl?: typeof fetch;
}}

export class TeamAClient {{
  private readonly baseUrl: string;
  private readonly fetchImpl: typeof fetch;
  private accessToken: string | null = null;

  constructor(options: TeamAClientOptions = {{}}) {{
    const baseUrl = options.baseUrl ?? '';
    if (baseUrl !== '') {{
      const parsed = new URL(baseUrl);
      if (parsed.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(parsed.hostname)) {{
        throw new Error('Team A client permits same-origin or loopback acceptance only.');
      }}
    }}
    this.baseUrl = baseUrl.endsWith('/') ? baseUrl.slice(0, -1) : baseUrl;
    this.fetchImpl = options.fetchImpl ?? fetch;
  }}

  setAccessToken(token: string): void {{
    if (!token || Array.from(token).some((character) => character.trim() === '')) {{
      throw new Error('Invalid access token.');
    }}
    this.accessToken = token;
  }}

  clearAccessToken(): void {{
    this.accessToken = null;
  }}

  async call<K extends TeamAOperationId>(operationId: K, input: TeamARequests[K]): Promise<TeamAResult<K>> {{
    const spec = teamAOperations[operationId];
    const values = input as Record<string, unknown>;
    const pathValues = (values.path ?? {{}}) as Record<string, unknown>;
    let path = spec.path as string;
    for (const [name, value] of Object.entries(pathValues)) {{
      path = path.replace(`{{${{name}}}}`, encodeURIComponent(String(value)));
    }}
    if (path.includes('{{') || path.includes('}}')) throw new Error(`Missing path parameter for ${{operationId}}.`);
    const query = new URLSearchParams();
    for (const [name, value] of Object.entries((values.query ?? {{}}) as Record<string, unknown>)) {{
      if (value !== undefined && value !== null) query.set(name, String(value));
    }}
    const suffix = query.size === 0 ? '' : `?${{query.toString()}}`;
    const headers: Record<string, string> = {{ Accept: 'application/json', 'X-Request-Id': requestId() }};
    if (this.accessToken !== null) headers.Authorization = `Bearer ${{this.accessToken}}`;
    if (typeof values.idempotencyKey === 'string') headers['X-Idempotency-Key'] = values.idempotencyKey;
    let body: string | undefined;
    if ('body' in values) {{
      headers['Content-Type'] = 'application/json';
      body = typeof values.body === 'string' ? values.body : JSON.stringify(values.body);
    }}
    const response = await this.fetchImpl(`${{this.baseUrl}}${{path}}${{suffix}}`, {{
      method: spec.method,
      headers,
      body,
      cache: 'no-store',
      credentials: 'omit',
    }});
    if (!(spec.expectedStatuses as readonly number[]).includes(response.status)) {{
      throw new TeamAHttpError(operationId, response.status);
    }}
    return {{ status: response.status, body: (await response.json()) as TeamAResponses[K] }};
  }}
}}

function requestId(): string {{
  return `team-a.${{crypto.randomUUID().replaceAll('-', '').slice(0, 24)}}`;
}}
"""
    return source.encode()


def build_artifacts(openapi_bytes: bytes) -> dict[Path, bytes]:
    openapi = object_value(json.loads(openapi_bytes), "OpenAPI")
    try:
        operations(openapi, 75)
    except ContractError:
        pass
    else:
        from contracts.verify_p1_v3_automation_openapi_transition import (
            ADDITIVE_PATH as V3_ADDITIVE_PATH,
            project_pre_v3_openapi,
        )

        v3_additive = object_value(
            json.loads(V3_ADDITIVE_PATH.read_bytes()), "Automation V3 additive OpenAPI"
        )
        try:
            openapi = project_pre_v3_openapi(openapi, v3_additive)
        except ContractValidationError as error:
            raise ContractError(str(error)) from error
        openapi_bytes = canonical_json_bytes(openapi)
    try:
        operations(openapi, 69)
    except ContractError:
        pass
    else:
        # 체인의 맨 앞 단계다. Strong LLM 설정 표면 하나를 먼저 걷어 exact-68로 내린다.
        from contracts.verify_p1_strong_llm_settings_openapi_transition import (
            ADDITIVE_PATH as STRONG_LLM_ADDITIVE_PATH,
            strip_strong_llm_settings,
        )

        strong_llm_additive = object_value(
            json.loads(STRONG_LLM_ADDITIVE_PATH.read_bytes()), "Strong LLM additive OpenAPI"
        )
        try:
            openapi = strip_strong_llm_settings(openapi, strong_llm_additive)
        except ContractValidationError as error:
            raise ContractError(str(error)) from error
        openapi_bytes = canonical_json_bytes(openapi)
    try:
        operations(openapi, 68)
    except ContractError:
        pass
    else:
        # RAG v2 공개 표면을 걷어 역사적 exact-61로 내린다.
        from contracts.verify_p1_rag_v2_openapi_transition import (
            ADDITIVE_PATH as RAG_V2_ADDITIVE_PATH,
            project_pre_rag_v2_openapi,
        )

        rag_v2_additive = object_value(
            json.loads(RAG_V2_ADDITIVE_PATH.read_bytes()), "RAG v2 additive OpenAPI"
        )
        try:
            openapi = project_pre_rag_v2_openapi(openapi, rag_v2_additive)
        except ContractValidationError as error:
            raise ContractError(str(error)) from error
        openapi_bytes = canonical_json_bytes(openapi)
    try:
        operations(openapi, 61)
    except ContractError:
        operations(openapi, 56)
    else:
        from contracts.verify_p1_v91_automation_openapi_transition import (
            ADDITIVE_PATH as V91_ADDITIVE_PATH,
            project_pre_v91_openapi,
        )

        v91_additive = object_value(
            json.loads(V91_ADDITIVE_PATH.read_bytes()), "V91 additive OpenAPI"
        )
        try:
            openapi = project_pre_v91_openapi(openapi, v91_additive)
        except ContractValidationError as error:
            raise ContractError(str(error)) from error
        openapi_bytes = canonical_json_bytes(openapi)
    badge_bytes = canonical_json(badge_contract())
    catalog_bytes = canonical_json(build_catalog(openapi, openapi_bytes, badge_bytes))
    return {
        BADGE_PATH: badge_bytes,
        CATALOG_PATH: catalog_bytes,
        CLIENT_PATH: generate_client(openapi),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    try:
        artifacts = build_artifacts(OPENAPI_PATH.read_bytes())
        if args.check:
            drift = [
                path.relative_to(ROOT).as_posix()
                for path, payload in artifacts.items()
                if not path.is_file() or path.read_bytes() != payload
            ]
            if drift:
                raise ContractError("generated artifacts drifted: " + ", ".join(drift))
        else:
            for path, payload in artifacts.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
    except (ContractError, OSError, json.JSONDecodeError) as error:
        print(f"P1_TEAM_A_ACCEPTANCE_GENERATION=FAIL: {error}", file=sys.stderr)
        return 1
    print("P1_TEAM_A_ACCEPTANCE_GENERATION=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
