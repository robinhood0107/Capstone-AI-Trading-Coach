"""Verify P1 Compose topology, Team B OCI intake preparation, and handoff docs."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from yaml.constructor import SafeConstructor

if __package__:
    from .generate_p1_owner_phase_a_contracts import (
        ContractValidationError,
        validate_semantics,
    )
else:
    from generate_p1_owner_phase_a_contracts import (
        ContractValidationError,
        validate_semantics,
    )


ROOT: Final = Path(__file__).resolve().parents[1]
COMPOSE_PATH: Final = ROOT / "deploy/p1/compose.yml"
CONTROL_PATH: Final = ROOT / "deploy/p1/full-appctl"
OCI_VERIFIER_PATH: Final = ROOT / "deploy/p1/verify-team-b-oci"
CATALOG_PATH: Final = ROOT / "contracts/catalogs/p1-team-b-oci-supply-chain.v1.json"
MANIFEST_SCHEMA_PATH: Final = (
    ROOT / "contracts/schemas/p1-return-engine-artifact-manifest.v2.schema.json"
)
MAX_RECEIPT_BYTES: Final = 1024 * 1024
MAX_MANIFEST_BYTES: Final = 8 * 1024 * 1024
HANDOFF_PATHS: Final = (
    "docs/handoff/START_HERE.md",
    "docs/handoff/team-a/README.md",
    "docs/handoff/team-b/README.md",
    "docs/handoff/owner/README.md",
)
PERSISTENT_BASE: Final = frozenset(
    {
        "postgres",
        "redis",
        "actor-authority",
        "decision-platform",
        "experience-dashboard",
    }
)
PERSISTENT_MODELS: Final = frozenset({"bge-m3", "paddleocr-vl"})
ONE_SHOT_SERVICES: Final = frozenset(
    {
        "role-bootstrap",
        "migrate",
        "seed-import",
        "identity-bootstrap",
        "dashboard-preview-seed",
        "return-engine-preview-prepare",
        "synthetic-async-smoke",
        "artifact-importer",
        "kis-mock-certification-runner",
        "team-a-acceptance-seed",
        "paddleocr-vl-model-fetch",
    }
)
EXACT_TEAM_B_FILES: Final = (
    "model.safetensors",
    "scaler.json",
    "config.json",
    "lstm_signals.parquet",
    "rule_baseline_signals.parquet",
    "backtest_result.json",
    "trade_log.parquet",
    "equity_log.parquet",
    "golden_output.json",
    "model_report.md",
)
HEADINGS: Final = (
    "## 1. 최종 프로그램 목표",
    "## 2. Owner가 이미 준비한 것",
    "## 3. 수정할 것",
    "## 4. 실행 명령",
    "## 5. 완료 테스트",
    "## 6. 제출할 파일·commit·OCI digest",
    "## 7. 하지 말아야 할 것",
)
IMMUTABLE_REFERENCE = re.compile(
    r"^ghcr\.io/robinhood0107/capstone-team-b-return-artifact@sha256:(?!0{64}$)[0-9a-f]{64}$"
)
SHA256 = re.compile(r"^(?!0{64}$)[0-9a-f]{64}$")
COMMIT_SHA = re.compile(r"^(?!0{40}$)[0-9a-f]{40}$")
LOCAL_REFERENCE_TOKEN: Final = "-".join(("private", "reference"))


class ContractError(ValueError):
    """Raised when an A-3 hard boundary drifts."""


class DuplicateJsonKey(ValueError):
    """Raised when signed or pulled JSON contains a duplicate key."""


class UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_mapping(
    loader: UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False
) -> object:
    direct_keys: set[object] = set()
    for key_node, _ in node.value:
        if isinstance(key_node, yaml.ScalarNode) and key_node.value == "<<":
            continue
        key = loader.construct_object(key_node, deep=deep)
        if key in direct_keys:
            raise ContractError(f"duplicate YAML key: {key}")
        direct_keys.add(key)
    return SafeConstructor.construct_mapping(loader, node, deep=deep)


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping,
)


def canonical_json(payload: object) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJsonKey(key)
        result[key] = value
    return result


def _load_bounded_json_object(path: Path, label: str, max_bytes: int) -> dict[str, Any]:
    try:
        metadata = path.lstat()
        if (
            path.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or not 0 < metadata.st_size <= max_bytes
        ):
            raise ContractError(f"{label} is missing, unsafe, empty, or oversized")
        payload = json.loads(path.read_bytes(), object_pairs_hook=_unique_json_object)
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        DuplicateJsonKey,
    ) as error:
        raise ContractError(f"{label} is not strict JSON") from error
    if not isinstance(payload, dict):
        raise ContractError(f"{label} must be an object")
    return payload


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def supply_catalog() -> dict[str, Any]:
    return {
        "allowedReferencePattern": IMMUTABLE_REFERENCE.pattern,
        "contractId": "p1-team-b-oci-supply-chain.v1",
        "exactArtifactFiles": list(EXACT_TEAM_B_FILES),
        "hashBindings": [
            "subjectCommitSha",
            "producerCommitSha256",
            "dependencyLockSha256",
            "dockerfileSha256",
            "sourceArchiveSha256",
            "inputPackSha256",
            "artifactManifestSha256",
            "outputArtifacts[].sha256",
        ],
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "minimumTools": {"cosign": "3.1.2", "oras": "1.3.3"},
        "providerAuthority": {
            "accountCalls": 0,
            "gdeltCalls": 0,
            "orderCalls": 0,
            "providerCalls": 0,
            "springCalls": 0,
        },
        "registry": {
            "host": "ghcr.io",
            "repository": "robinhood0107/capstone-team-b-return-artifact",
            "visibility": "PRIVATE_RESTRICTED",
        },
        "requiredEvidenceKinds": [
            "OCI_MANIFEST",
            "SPDX_SBOM",
            "CYCLONEDX_SBOM",
            "SLSA_PROVENANCE",
            "SIGSTORE_BUNDLE",
        ],
        "receiptSignature": {
            "bundleFormat": "SIGSTORE_BUNDLE",
            "mode": "KEYLESS_SIGNED_BLOB",
            "required": True,
        },
        "signatureIdentity": {
            "certificateIdentityRegexp": (
                "^https://github.com/robinhood0107/Capstone-AI-Trading-Coach/"
                ".github/workflows/p1-team-b-artifact.yml@refs/heads/main$"
            ),
            "certificateOidcIssuer": "https://token.actions.githubusercontent.com",
        },
        "tagReferencesAllowed": False,
        "verificationOrder": [
            "IMMUTABLE_REFERENCE",
            "COSIGN_SIGNATURE",
            "COSIGN_RECEIPT_BLOB_SIGNATURE",
            "SLSA_PROVENANCE",
            "SPDX_AND_CYCLONEDX_SBOM",
            "ORAS_PULL_BY_DIGEST",
            "MANIFEST_INPUT_SOURCE_OUTPUT_HASH_BINDING",
            "EXACT10_SEMANTIC_VALIDATION",
        ],
    }


def verify_compose() -> None:
    raw = COMPOSE_PATH.read_text(encoding="utf-8")
    document = yaml.load(raw, Loader=UniqueKeyLoader)
    if not isinstance(document, dict) or not isinstance(document.get("services"), dict):
        raise ContractError("Compose service map is missing")
    services: dict[str, Any] = document["services"]
    if not PERSISTENT_BASE.issubset(services) or not PERSISTENT_MODELS.issubset(
        services
    ):
        raise ContractError("Compose persistent service inventory is incomplete")
    for name in PERSISTENT_BASE:
        service = services[name]
        if not isinstance(service, dict) or service.get("profiles"):
            raise ContractError(f"default persistent service drifted: {name}")
        if service.get("restart") not in {"on-failure:3", "unless-stopped", "always"}:
            raise ContractError(f"persistent restart policy is missing: {name}")
    for name in PERSISTENT_MODELS:
        service = services[name]
        if not isinstance(service, Mapping) or service.get("profiles") != ["models"]:
            raise ContractError(f"model profile drifted: {name}")
    for name in ONE_SHOT_SERVICES:
        service = services.get(name)
        if not isinstance(service, dict) or service.get("restart") != "no":
            raise ContractError(f"one-shot restart boundary drifted: {name}")
    if "return-inference" in services or "return-engine" in services:
        raise ContractError("Return inference must remain inside decision-platform")
    decision_environment = services["decision-platform"].get("environment", {})
    if not isinstance(decision_environment, Mapping):
        raise ContractError("decision-platform environment is invalid")
    if (
        decision_environment.get("RETURN_INFERENCE_GRPC_BIND_ADDRESS")
        != "127.0.0.1:50057"
    ):
        raise ContractError("Return inference process boundary drifted")
    if decision_environment.get("PROVIDER_LIVE_CALLS_ENABLED") != "false":
        raise ContractError("default provider boundary opened")
    for service_name, service in services.items():
        if not isinstance(service, Mapping):
            raise ContractError(f"Compose service is not an object: {service_name}")
        if service.get("privileged") is True:
            raise ContractError(f"privileged container is forbidden: {service_name}")
        for volume in service.get("volumes", []) or []:
            if "docker.sock" in str(volume):
                raise ContractError("Docker socket mount is forbidden")
        for port in service.get("ports", []) or []:
            if not str(port).startswith("127.0.0.1:"):
                raise ContractError(f"host port is not loopback-bound: {service_name}")
    control = CONTROL_PATH.read_text(encoding="utf-8")
    for service in (
        "migrate",
        "seed-import",
        "identity-bootstrap",
        "artifact-importer",
        "team-a-acceptance-seed",
    ):
        if not re.search(
            rf"run --rm[^\n]*{re.escape(service)}|run --rm[\s\\\n-]*[^\n]*{re.escape(service)}",
            control,
        ):
            raise ContractError(f"one-shot command is not run --rm: {service}")
    oci_verifier = OCI_VERIFIER_PATH.read_text(encoding="utf-8")
    for required in (
        "cosign verify-blob",
        '--bundle "$receipt_bundle"',
        '--expected-reference "$reference"',
    ):
        if required not in oci_verifier:
            raise ContractError(f"Team B OCI verifier boundary is missing: {required}")
    for required in (
        "artifact_validate()",
        "--network none",
        "--read-only",
        "--validate-only",
        "TEAM_B_LOCAL_VALIDATION=PASS",
    ):
        if required not in control:
            raise ContractError(f"Team B local validator boundary is missing: {required}")


def verify_handoff_docs() -> None:
    for relative in HANDOFF_PATHS:
        path = ROOT / relative
        if not path.is_file() or path.is_symlink():
            raise ContractError(f"handoff file is missing or unsafe: {relative}")
        text = path.read_text(encoding="utf-8")
        headings = tuple(line for line in text.splitlines() if line.startswith("## "))
        if headings != HEADINGS:
            raise ContractError(f"handoff headings drifted: {relative}")
        if not text.endswith("\n"):
            raise ContractError(f"handoff EOF newline is missing: {relative}")
        if relative in {
            "docs/handoff/team-a/README.md",
            "docs/handoff/team-b/README.md",
        }:
            lowered = text.lower()
            for forbidden in (
                LOCAL_REFERENCE_TOKEN,
                "dev/upstream-intake",
                "s1.",
                "s2.",
            ):
                if forbidden in lowered:
                    raise ContractError(f"internal owner detail leaked into {relative}")


def verify_supply_catalog(payload: Mapping[str, Any]) -> None:
    if payload != supply_catalog():
        raise ContractError("Team B supply-chain catalog drifted")
    if payload["tagReferencesAllowed"] is not False:
        raise ContractError("arbitrary OCI tags must stay forbidden")


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        raise ContractError(f"invalid {label}")
    return value


def verify_receipt(receipt: Mapping[str, Any]) -> None:
    if not isinstance(receipt, Mapping):
        raise ContractError("receipt must be an object")
    expected_keys = {
        "artifactManifestSha256",
        "contractId",
        "dependencyLockSha256",
        "dockerfileSha256",
        "imageReference",
        "inputPackSha256",
        "manifestDigest",
        "outputArtifacts",
        "providerAuthority",
        "producerCommitSha256",
        "sourceArchiveSha256",
        "subjectCommitSha",
    }
    if (
        set(receipt) != expected_keys
        or receipt.get("contractId") != "p1-team-b-oci-receipt.v1"
    ):
        raise ContractError("receipt shape drifted")
    reference = receipt.get("imageReference")
    if not isinstance(reference, str) or not IMMUTABLE_REFERENCE.fullmatch(reference):
        raise ContractError("receipt must use the restricted immutable GHCR reference")
    manifest_digest = receipt.get("manifestDigest")
    if manifest_digest != reference.rsplit("@", 1)[1]:
        raise ContractError("OCI manifest digest binding drifted")
    if not isinstance(receipt.get("subjectCommitSha"), str) or not COMMIT_SHA.fullmatch(
        receipt["subjectCommitSha"]
    ):
        raise ContractError("subject commit SHA is invalid")
    for field in (
        "producerCommitSha256",
        "dependencyLockSha256",
        "dockerfileSha256",
        "sourceArchiveSha256",
        "inputPackSha256",
        "artifactManifestSha256",
    ):
        _digest(receipt.get(field), field)
    outputs = receipt.get("outputArtifacts")
    if (
        not isinstance(outputs, list)
        or len(outputs) != len(EXACT_TEAM_B_FILES)
        or not all(isinstance(item, Mapping) for item in outputs)
        or [item.get("path") for item in outputs] != list(EXACT_TEAM_B_FILES)
    ):
        raise ContractError("receipt output inventory is not exact 10")
    for item in outputs:
        if (
            set(item) != {"path", "sha256", "sizeBytes"}
            or not isinstance(item["sizeBytes"], int)
            or isinstance(item["sizeBytes"], bool)
            or item["sizeBytes"] < 1
        ):
            raise ContractError("receipt output entry is invalid")
        _digest(item["sha256"], f"output {item['path']}")
    authority = receipt.get("providerAuthority")
    if authority != supply_catalog()["providerAuthority"]:
        raise ContractError("Team B provider authority must remain zero")


def remote_verification_commands(reference: str) -> tuple[tuple[str, ...], ...]:
    if not IMMUTABLE_REFERENCE.fullmatch(reference):
        raise ContractError("remote verification refuses mutable or foreign references")
    identity = supply_catalog()["signatureIdentity"]
    common = (
        "--certificate-identity-regexp",
        identity["certificateIdentityRegexp"],
        "--certificate-oidc-issuer",
        identity["certificateOidcIssuer"],
    )
    return (
        ("cosign", "verify", *common, reference),
        (
            "cosign",
            "verify-blob",
            "--bundle",
            "<receipt.sigstore.json>",
            *common,
            "<receipt.json>",
        ),
        (
            "cosign",
            "verify-attestation",
            "--type",
            "https://slsa.dev/provenance/v1",
            *common,
            reference,
        ),
        (
            "cosign",
            "verify-attestation",
            "--type",
            "https://spdx.dev/Document",
            *common,
            reference,
        ),
        (
            "cosign",
            "verify-attestation",
            "--type",
            "https://cyclonedx.org/bom",
            *common,
            reference,
        ),
        ("oras", "pull", reference, "--output", "<verified-empty-directory>"),
    )


def verify_bundle(receipt: Mapping[str, Any], bundle_root: Path) -> None:
    verify_receipt(receipt)
    if not bundle_root.is_dir() or bundle_root.is_symlink():
        raise ContractError("pulled bundle root is missing or unsafe")
    expected = {"manifest.json", *EXACT_TEAM_B_FILES}
    actual: set[str] = set()
    for path in bundle_root.iterdir():
        if not path.is_file() or path.is_symlink():
            raise ContractError("pulled bundle contains a non-regular entry")
        actual.add(path.name)
    if actual != expected:
        raise ContractError("pulled bundle inventory is not manifest plus exact 10")
    manifest_path = bundle_root / "manifest.json"
    manifest = _load_bounded_json_object(
        manifest_path, "artifact manifest", MAX_MANIFEST_BYTES
    )
    if _file_sha256(manifest_path) != receipt["artifactManifestSha256"]:
        raise ContractError("artifact manifest file hash drifted")
    try:
        schema = json.loads(MANIFEST_SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator(schema).validate(manifest)
        validate_semantics("p1-return-engine-artifact-manifest.v2", manifest)
    except (ContractValidationError, ValidationError) as error:
        raise ContractError("artifact manifest schema or semantics drifted") from error
    producer = manifest["producer"]
    if (
        manifest["inputPackSha256"] != receipt["inputPackSha256"]
        or producer["commitSha256"] != receipt["producerCommitSha256"]
        or producer["dependencyLockSha256"] != receipt["dependencyLockSha256"]
        or producer["dockerfileSha256"] != receipt["dockerfileSha256"]
    ):
        raise ContractError("artifact manifest input or producer binding drifted")
    manifest_outputs = [
        {"path": item["path"], "sha256": item["sha256"], "sizeBytes": item["sizeBytes"]}
        for item in manifest["artifacts"]
    ]
    if manifest_outputs != receipt["outputArtifacts"]:
        raise ContractError("artifact manifest output binding drifted")
    for item in receipt["outputArtifacts"]:
        output_path = bundle_root / item["path"]
        if (
            output_path.stat().st_size != item["sizeBytes"]
            or _file_sha256(output_path) != item["sha256"]
        ):
            raise ContractError(f"pulled output hash drifted: {item['path']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--bundle-root", type=Path)
    parser.add_argument("--expected-reference")
    args = parser.parse_args(argv)
    try:
        expected = canonical_json(supply_catalog())
        if args.write:
            CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            CATALOG_PATH.write_bytes(expected)
        elif not CATALOG_PATH.is_file() or CATALOG_PATH.read_bytes() != expected:
            raise ContractError("generated Team B supply catalog drifted")
        verify_supply_catalog(json.loads(CATALOG_PATH.read_text(encoding="utf-8")))
        verify_compose()
        verify_handoff_docs()
        if args.receipt is not None:
            receipt = _load_bounded_json_object(
                args.receipt, "Team B receipt", MAX_RECEIPT_BYTES
            )
            verify_receipt(receipt)
            if (
                args.expected_reference is not None
                and receipt["imageReference"] != args.expected_reference
            ):
                raise ContractError(
                    "receipt image reference differs from verified reference"
                )
            if args.bundle_root is not None:
                verify_bundle(receipt, args.bundle_root)
        elif args.bundle_root is not None or args.expected_reference is not None:
            raise ContractError("bundle or reference verification requires a receipt")
    except (ContractError, OSError, json.JSONDecodeError, yaml.YAMLError) as error:
        print(f"P1_COMPOSE_SUPPLY_HANDOFF=FAIL: {error}", file=sys.stderr)
        return 1
    print("OWNER_COMPOSE_5_7_PREP=PASS")
    print("OWNER_SUPPLY_CHAIN_PREP=PASS")
    print("OWNER_HANDOFF_DOCS=PASS")
    print("OWNER_POST_TEAM_CODE_REQUIRED=0")
    print("TEAM_A_REQUEST_READY=TRUE")
    print("TEAM_B_REQUEST_READY=TRUE")
    print("TEAM_B_REAL_ARTIFACT=PENDING_EXTERNAL_TEAM")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
