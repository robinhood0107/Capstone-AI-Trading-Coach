"""Generate the confidence-free exact-45 Team A current acceptance client."""

from __future__ import annotations

import argparse
import copy
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
    OPENAPI_PATH,
    ContractError,
    canonical_json,
    generate_client,
    object_value,
    operations,
    sha256,
)
from contracts.generate_p1_team_a_acceptance_v3 import (  # noqa: E402
    CATALOG_PATH as V3_CATALOG_PATH,
    CLIENT_PATH as V3_CLIENT_PATH,
    EXPECTED_OPERATIONS_V3,
)

CATALOG_PATH: Final = ROOT / "contracts/catalogs/p1-team-a-acceptance.v4.json"
CLIENT_PATH: Final = (
    ROOT / "workspaces/experience-dashboard/src/shared/api/generated/p1-team-a-client.v4.ts"
)
EXPECTED_OPERATIONS_V4: Final = tuple(
    (
        category,
        "GET",
        "/api/v3/signals/{symbol}",
        "readSignalV3",
        statuses,
    )
    if operation_id == "read"
    else (category, method, path, operation_id, statuses)
    for category, method, path, operation_id, statuses in EXPECTED_OPERATIONS_V3
)


def build_catalog(openapi: dict[str, Any], openapi_bytes: bytes) -> dict[str, Any]:
    root_operations = operations(openapi, 76)
    entries: list[dict[str, Any]] = []
    for sequence, (category, method, path, operation_id, statuses) in enumerate(
        EXPECTED_OPERATIONS_V4, 1
    ):
        operation = root_operations.get((method, path))
        if operation is None or operation.get("operationId") != operation_id:
            raise ContractError(f"Team A v4 operation drifted: {method} {path}")
        entries.append(
            {
                "category": category,
                "expectedStatuses": list(statuses),
                "method": method,
                "operationId": operation_id,
                "path": path,
                "sequence": sequence,
            }
        )
    if len(entries) != 45:
        raise ContractError("Team A v4 catalog must contain exact 45 operations")
    return {
        "acceptanceOperationCount": 45,
        "badgeContract": {
            "path": BADGE_PATH.relative_to(ROOT).as_posix(),
            "sha256": sha256(BADGE_PATH.read_bytes()),
        },
        "contractId": "p1-team-a-acceptance.v4",
        "currentSignalContract": "confidence-free /api/v3/signals/{symbol}",
        "generatedClient": CLIENT_PATH.relative_to(ROOT).as_posix(),
        "operations": entries,
        "preservedV3": {
            "catalogPath": V3_CATALOG_PATH.relative_to(ROOT).as_posix(),
            "catalogSha256": sha256(V3_CATALOG_PATH.read_bytes()),
            "clientPath": V3_CLIENT_PATH.relative_to(ROOT).as_posix(),
            "clientSha256": sha256(V3_CLIENT_PATH.read_bytes()),
            "operationCount": 45,
        },
        "rootOpenApi": {
            "operationCount": 76,
            "path": OPENAPI_PATH.relative_to(ROOT).as_posix(),
            "sha256": sha256(openapi_bytes),
        },
        "sameOriginPrefix": "/api",
    }


def build_artifacts(openapi_bytes: bytes) -> dict[Path, bytes]:
    openapi = object_value(json.loads(openapi_bytes), "OpenAPI")
    operations(openapi, 76)
    client_openapi = copy.deepcopy(openapi)
    _remove_confidence_fields(client_openapi)
    return {
        CATALOG_PATH: canonical_json(build_catalog(openapi, openapi_bytes)),
        CLIENT_PATH: generate_client(
            client_openapi,
            expected_operations=EXPECTED_OPERATIONS_V4,
            expected_root_count=76,
            generated_by="contracts/generate_p1_team_a_acceptance_v4.py",
        ),
    }


def _remove_confidence_fields(value: object) -> None:
    if isinstance(value, dict):
        properties = value.get("properties")
        if isinstance(properties, dict):
            properties.pop("confidence", None)
            required = value.get("required")
            if isinstance(required, list):
                value["required"] = [item for item in required if item != "confidence"]
        for item in value.values():
            _remove_confidence_fields(item)
    elif isinstance(value, list):
        for item in value:
            _remove_confidence_fields(item)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    try:
        artifacts = build_artifacts(OPENAPI_PATH.read_bytes())
        for path, payload in artifacts.items():
            if args.check:
                if not path.is_file() or path.read_bytes() != payload:
                    raise ContractError(f"generated Team A v4 artifact drifted: {path.relative_to(ROOT)}")
            else:
                write_generated_path(ROOT, path, payload)
    except (ContractError, OSError, json.JSONDecodeError) as error:
        print(f"P1_TEAM_A_ACCEPTANCE_V4_GENERATION=FAIL: {error}", file=sys.stderr)
        return 1
    print("P1_TEAM_A_ACCEPTANCE_V4_GENERATION=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
