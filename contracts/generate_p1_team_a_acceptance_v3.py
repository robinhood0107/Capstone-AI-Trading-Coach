"""Generate the exact-45 Team A V3 acceptance catalog and TypeScript client."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Final

ROOT: Final = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contracts.generated_artifact_io import write_generated_path  # noqa: E402
from contracts.generate_p1_team_a_acceptance import (  # noqa: E402
    BADGE_PATH,
    CLIENT_PARAMETER_OVERRIDES,
    OPENAPI_PATH,
    ContractError,
    canonical_json,
    generate_client,
    object_value,
    operations,
    sha256,
)
from contracts.generate_p1_team_a_acceptance_v2 import (  # noqa: E402
    CATALOG_PATH as V2_CATALOG_PATH,
    CLIENT_PATH as V2_CLIENT_PATH,
    EXPECTED_OPERATIONS_V2,
)

CATALOG_PATH: Final = ROOT / "contracts/catalogs/p1-team-a-acceptance.v3.json"
CLIENT_PATH: Final = (
    ROOT / "workspaces/experience-dashboard/src/shared/api/generated/p1-team-a-client.v3.ts"
)
V3_OPERATIONS: Final = (
    ("TEAM_A_REQUIRED", "PUT", "/api/v2/strong-llm/settings", "putStrongLlmSettings", (200,)),
    ("TEAM_A_REQUIRED", "GET", "/api/v3/automation/status", "getAutomationStatusV3", (200,)),
    ("TEAM_A_REQUIRED", "PUT", "/api/v3/automation/policy", "putAutomationPolicyV3", (200,)),
    ("TEAM_A_REQUIRED", "POST", "/api/v3/automation/arm", "armAutomationV3", (409,)),
    ("TEAM_A_REQUIRED", "GET", "/api/v3/automation/runs", "listAutomationRunsV3", (200,)),
    ("TEAM_A_REQUIRED", "GET", "/api/v3/automation/runs/{runId}", "getAutomationRunV3", (200,)),
    ("TEAM_A_REQUIRED", "GET", "/api/v3/automation/positions", "listAutomationPositionsV3", (200,)),
)
EXPECTED_OPERATIONS_V3: Final = (
    *EXPECTED_OPERATIONS_V2[:-2],
    *V3_OPERATIONS,
    *EXPECTED_OPERATIONS_V2[-2:],
)


def build_catalog(openapi: dict[str, Any], openapi_bytes: bytes, badge_bytes: bytes) -> dict[str, Any]:
    root_operations = operations(openapi, 75)
    entries: list[dict[str, Any]] = []
    for sequence, (category, method, path, operation_id, statuses) in enumerate(EXPECTED_OPERATIONS_V3, 1):
        operation = root_operations.get((method, path))
        if operation is None or operation.get("operationId") != operation_id:
            raise ContractError(f"Team A v3 operation drifted: {method} {path}")
        actual_statuses = {
            int(code)
            for code in object_value(operation.get("responses"), "responses")
            if str(code).isdigit()
        }
        if not set(statuses).issubset(actual_statuses):
            raise ContractError(f"Team A v3 expected status drifted: {operation_id}")
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
    if len(entries) != 45:
        raise ContractError("Team A v3 catalog must contain exact 45 operations")
    return {
        "acceptanceOperationCount": 45,
        "badgeContract": {
            "path": BADGE_PATH.relative_to(ROOT).as_posix(),
            "sha256": sha256(badge_bytes),
        },
        "contractId": "p1-team-a-acceptance.v3",
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
        "preservedV2": {
            "catalogPath": V2_CATALOG_PATH.relative_to(ROOT).as_posix(),
            "catalogSha256": sha256(V2_CATALOG_PATH.read_bytes()),
            "clientPath": V2_CLIENT_PATH.relative_to(ROOT).as_posix(),
            "clientSha256": sha256(V2_CLIENT_PATH.read_bytes()),
            "operationCount": 38,
        },
        "rootOpenApi": {
            "operationCount": 75,
            "path": OPENAPI_PATH.relative_to(ROOT).as_posix(),
            "sha256": sha256(openapi_bytes),
        },
        "runner": "./capstone team-a acceptance",
        "sameOriginPrefix": "/api",
        "stateRestoration": ["AUTOMATION_DISARMED", "KILL_SWITCH_RESTORED"],
    }


def build_artifacts(openapi_bytes: bytes) -> dict[Path, bytes]:
    openapi = object_value(json.loads(openapi_bytes), "OpenAPI")
    operations(openapi, 75)
    badge_bytes = BADGE_PATH.read_bytes()
    return {
        CATALOG_PATH: canonical_json(build_catalog(openapi, openapi_bytes, badge_bytes)),
        CLIENT_PATH: generate_client(
            openapi,
            expected_operations=EXPECTED_OPERATIONS_V3,
            expected_root_count=75,
            generated_by="contracts/generate_p1_team_a_acceptance_v3.py",
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args(argv)
    try:
        artifacts = build_artifacts(OPENAPI_PATH.read_bytes())
        for path, payload in artifacts.items():
            if arguments.check:
                if not path.is_file() or path.read_bytes() != payload:
                    raise ContractError(f"generated Team A v3 artifact drifted: {path.relative_to(ROOT)}")
            else:
                write_generated_path(ROOT, path, payload)
    except (ContractError, OSError, json.JSONDecodeError) as error:
        print(f"P1_TEAM_A_ACCEPTANCE_V3_GENERATION=FAIL: {error}", file=sys.stderr)
        return 1
    print("P1_TEAM_A_ACCEPTANCE_V3_GENERATION=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
