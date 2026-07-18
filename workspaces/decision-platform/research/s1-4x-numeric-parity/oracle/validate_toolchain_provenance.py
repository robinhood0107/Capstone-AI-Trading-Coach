"""Gate 0 Stack/GHCup provenance lock과 local executable mapping을 fail-closed 검증한다."""

from __future__ import annotations

import argparse
import os
import subprocess
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from oracle_common import (
    OracleContractError,
    atomic_write_json,
    canonical_json_bytes,
    find_repo_root,
    require_lower_sha256,
    resolve_within,
    sha256_bytes,
    sha256_file,
    strict_json_load,
)

SCHEMA_VERSION = "s1.4x-toolchain-provenance-v1"
STACK_PATH_ID = "GHCUP_STACK_3_11_1"
GHCUP_PATH_ID = "GHCUP_0_2_6_2_LINUX_X86_64"

FROZEN_FIELDS: dict[str, Any] = {
    "schemaVersion": SCHEMA_VERSION,
    "stackPolicy": "GHCup-managed exact-version installation",
    "stackInstallCommand": "ghcup install stack 3.11.1",
    "ghcupToolId": GHCUP_PATH_ID,
    "ghcupVersion": "0.2.6.2",
    "ghcupReleaseUri": "https://github.com/haskell/ghcup-hs/releases/tag/v0.2.6.2",
    "ghcupAssetUri": (
        "https://github.com/haskell/ghcup-hs/releases/download/"
        "v0.2.6.2/x86_64-linux-ghcup-0.2.6.2"
    ),
    "ghcupAssetSha256": (
        "9ed5da5449b48043a0d17e767c05d2ef585e25a639bb934329496c6d2fad9cf8"
    ),
    "ghcupMetadataCommit": "0341867f2d419567cf42ea6931e031b00ab3a922",
    "ghcupMetadataUri": (
        "https://github.com/haskell/ghcup-metadata/commit/"
        "0341867f2d419567cf42ea6931e031b00ab3a922"
    ),
    "ghcupMetadataRawUri": (
        "https://raw.githubusercontent.com/haskell/ghcup-metadata/"
        "0341867f2d419567cf42ea6931e031b00ab3a922/ghcup-0.1.0.yaml"
    ),
    "ghcupMetadataRawSha256": (
        "49c8a036ce399587205a11ac24e73465cadc5f3232e9418a9d87f4b7f746c4ec"
    ),
    "stackDistributionChannel": "ghcup-managed",
    "stackArchiveUri": (
        "https://downloads.haskell.org/~ghcup/unofficial-bindists/stack/"
        "3.11.1/stack-3.11.1-linux-x86_64.tar.gz"
    ),
    "stackArchiveSha256": (
        "ca3cc5e89d87d1b85594a866de4062671d19ec039cd2401df70d4ccff03ffed9"
    ),
    "stackBinPathId": STACK_PATH_ID,
    "stackBinResolver": "ghcup whereis stack 3.11.1",
    "stackBinSha256": (
        "923dbd137756652c67b376e2447c655b87fcc373f4d104b5073bca913471ecbe"
    ),
    "stackNumericVersion": "3.11.1",
    "upstreamStandaloneAssetUri": (
        "https://github.com/commercialhaskell/stack/releases/download/"
        "v3.11.1/stack-3.11.1-linux-x86_64-bin"
    ),
    "upstreamStandaloneAssetSha256": (
        "67c66e918801c41ae4d286b1c91f9124f691c1c7d56071b53889cf4a5c667550"
    ),
    "upstreamStandaloneAssetRole": "comparison-only-not-installed-provenance",
}
REQUIRED_TOP_LEVEL = frozenset(
    {*FROZEN_FIELDS, "verifiedAt", "gate0MetadataEntryEvidence"}
)


class RuntimeAdapter(Protocol):
    """실제 executable 확인과 unit-test fake를 같은 경계로 제공한다."""

    def resolve(self, path: Path) -> str: ...

    def executable(self, path: Path) -> bool: ...

    def sha256(self, path: Path) -> str: ...

    def numeric_version(self, path: Path) -> str: ...


class LocalRuntimeAdapter:
    """local filesystem과 bounded subprocess만 사용하는 runtime adapter다."""

    def resolve(self, path: Path) -> str:
        return str(path.resolve(strict=True))

    def executable(self, path: Path) -> bool:
        return path.is_file() and os.access(path, os.X_OK)

    def sha256(self, path: Path) -> str:
        return sha256_file(path)

    def numeric_version(self, path: Path) -> str:
        completed = subprocess.run(
            [str(path), "--numeric-version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if completed.returncode != 0:
            raise OracleContractError(
                f"numeric version command failed with exit {completed.returncode}"
            )
        return completed.stdout.strip()


def _check(
    checks: list[dict[str, Any]],
    *,
    check_id: str,
    expected: Any,
    actual: Any,
    passed: bool | None = None,
) -> None:
    status = (actual == expected) if passed is None else passed
    checks.append(
        {
            "id": check_id,
            "expected": expected,
            "actual": actual,
            "status": "PASS" if status else "FAIL",
        }
    )


def _recursive_contains(value: Any, expected: str) -> bool:
    if value == expected:
        return True
    if isinstance(value, dict):
        return any(_recursive_contains(item, expected) for item in value.values())
    if isinstance(value, list):
        return any(_recursive_contains(item, expected) for item in value)
    if isinstance(value, str):
        return expected in value
    return False


def _evidence_payload_bytes(evidence: Mapping[str, Any]) -> bytes:
    serialization = evidence.get("serialization")
    payload = evidence.get("payload")
    if serialization == "canonical-json-utf8":
        return canonical_json_bytes(payload, trailing_newline=False)
    raise OracleContractError("unsupported Gate 0 evidence serialization")


def _valid_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError:
        return False
    return True


def validate_provenance(
    contract_path: Path,
    *,
    expected_contract_sha256: str,
    stack_bin: Path,
    expected_stack_bin_path: Path,
    ghcup_bin: Path | None = None,
    runtime: RuntimeAdapter | None = None,
) -> dict[str, Any]:
    """portable lock과 runtime path/SHA/version을 검증하고 모든 check를 수집한다."""

    adapter = runtime or LocalRuntimeAdapter()
    checks: list[dict[str, Any]] = []
    require_lower_sha256(
        expected_contract_sha256,
        field="expected-contract-sha256",
    )
    actual_contract_sha = sha256_file(contract_path)
    _check(
        checks,
        check_id="contract.sha256",
        expected=expected_contract_sha256,
        actual=actual_contract_sha,
    )
    loaded = strict_json_load(contract_path)
    if not isinstance(loaded, dict):
        raise OracleContractError("toolchain provenance contract must be an object")
    _check(
        checks,
        check_id="contract.top-level-keys",
        expected=sorted(REQUIRED_TOP_LEVEL),
        actual=sorted(loaded),
    )
    for field, expected in FROZEN_FIELDS.items():
        _check(
            checks,
            check_id=f"contract.{field}",
            expected=expected,
            actual=loaded.get(field),
        )
    _check(
        checks,
        check_id="contract.verifiedAt",
        expected="RFC3339 UTC timestamp",
        actual=loaded.get("verifiedAt"),
        passed=_valid_timestamp(loaded.get("verifiedAt")),
    )

    evidence = loaded.get("gate0MetadataEntryEvidence")
    evidence_keys = {
        "sourcePath",
        "sourceSha256",
        "serialization",
        "payload",
        "digestSha256",
    }
    if not isinstance(evidence, dict):
        _check(
            checks,
            check_id="evidence.object",
            expected="object",
            actual=type(evidence).__name__,
            passed=False,
        )
    else:
        _check(
            checks,
            check_id="evidence.keys",
            expected=sorted(evidence_keys),
            actual=sorted(evidence),
        )
        _check(
            checks,
            check_id="evidence.serialization",
            expected="canonical-json-utf8",
            actual=evidence.get("serialization"),
        )
        declared_source_sha: object = evidence.get("sourceSha256")
        try:
            declared_source_sha = require_lower_sha256(
                declared_source_sha,
                field="gate0MetadataEntryEvidence.sourceSha256",
            )
            source_path = evidence.get("sourcePath")
            if not isinstance(source_path, str):
                raise OracleContractError("Gate 0 evidence sourcePath must be a string")
            actual_source_sha = sha256_file(
                resolve_within(find_repo_root(), source_path, must_exist=True)
            )
        except OracleContractError as exc:
            actual_source_sha = f"INVALID:{exc}"
        _check(
            checks,
            check_id="evidence.sourceSha256",
            expected=declared_source_sha,
            actual=actual_source_sha,
        )
        declared_digest: object = evidence.get("digestSha256")
        try:
            declared_digest = require_lower_sha256(
                declared_digest,
                field="gate0MetadataEntryEvidence.digestSha256",
            )
            actual_digest = sha256_bytes(_evidence_payload_bytes(evidence))
        except OracleContractError as exc:
            actual_digest = f"INVALID:{exc}"
        _check(
            checks,
            check_id="evidence.digestSha256",
            expected=declared_digest,
            actual=actual_digest,
        )
        _check(
            checks,
            check_id="evidence.metadataRawSha256",
            expected=FROZEN_FIELDS["ghcupMetadataRawSha256"],
            actual="present" if _recursive_contains(
                evidence.get("payload"),
                FROZEN_FIELDS["ghcupMetadataRawSha256"],
            ) else "absent",
            passed=_recursive_contains(
                evidence.get("payload"),
                FROZEN_FIELDS["ghcupMetadataRawSha256"],
            ),
        )
        _check(
            checks,
            check_id="evidence.archiveUri",
            expected=FROZEN_FIELDS["stackArchiveUri"],
            actual="present" if _recursive_contains(
                evidence.get("payload"),
                FROZEN_FIELDS["stackArchiveUri"],
            ) else "absent",
            passed=_recursive_contains(
                evidence.get("payload"),
                FROZEN_FIELDS["stackArchiveUri"],
            ),
        )
        _check(
            checks,
            check_id="evidence.archiveSha256",
            expected=FROZEN_FIELDS["stackArchiveSha256"],
            actual="present" if _recursive_contains(
                evidence.get("payload"),
                FROZEN_FIELDS["stackArchiveSha256"],
            ) else "absent",
            passed=_recursive_contains(
                evidence.get("payload"),
                FROZEN_FIELDS["stackArchiveSha256"],
            ),
        )

    if loaded.get("stackBinPathId") != STACK_PATH_ID:
        _check(
            checks,
            check_id="runtime.stack.path-id",
            expected=STACK_PATH_ID,
            actual=loaded.get("stackBinPathId"),
            passed=False,
        )
    try:
        actual_stack_path = adapter.resolve(stack_bin)
        declared_stack_path = adapter.resolve(expected_stack_bin_path)
        stack_executable = adapter.executable(stack_bin)
        stack_sha = adapter.sha256(stack_bin)
        stack_version = adapter.numeric_version(stack_bin)
    except (OSError, OracleContractError) as exc:
        actual_stack_path = f"INVALID:{type(exc).__name__}"
        declared_stack_path = str(expected_stack_bin_path)
        stack_executable = False
        stack_sha = "UNAVAILABLE"
        stack_version = "UNAVAILABLE"
    _check(
        checks,
        check_id="runtime.stack.path",
        expected=declared_stack_path,
        actual=actual_stack_path,
    )
    _check(
        checks,
        check_id="runtime.stack.executable",
        expected=True,
        actual=stack_executable,
    )
    _check(
        checks,
        check_id="runtime.stack.sha256",
        expected=FROZEN_FIELDS["stackBinSha256"],
        actual=stack_sha,
    )
    _check(
        checks,
        check_id="runtime.stack.numeric-version",
        expected=FROZEN_FIELDS["stackNumericVersion"],
        actual=stack_version,
    )

    if ghcup_bin is not None:
        try:
            ghcup_executable = adapter.executable(ghcup_bin)
            ghcup_sha = adapter.sha256(ghcup_bin)
            ghcup_version = adapter.numeric_version(ghcup_bin)
        except (OSError, OracleContractError):
            ghcup_executable = False
            ghcup_sha = "UNAVAILABLE"
            ghcup_version = "UNAVAILABLE"
        _check(
            checks,
            check_id="runtime.ghcup.executable",
            expected=True,
            actual=ghcup_executable,
        )
        _check(
            checks,
            check_id="runtime.ghcup.sha256",
            expected=FROZEN_FIELDS["ghcupAssetSha256"],
            actual=ghcup_sha,
        )
        _check(
            checks,
            check_id="runtime.ghcup.numeric-version",
            expected=FROZEN_FIELDS["ghcupVersion"],
            actual=ghcup_version,
        )

    failures = [check for check in checks if check["status"] != "PASS"]
    return {
        "schemaVersion": "s1.4x-toolchain-provenance-validation-v1",
        "contractSha256": actual_contract_sha,
        "checks": checks,
        "failureCount": len(failures),
        "status": "PASS" if not failures else "FAIL",
    }


def _parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the merged portable GHCup/Stack provenance lock without network access. "
            "All checks are collected into typed JSON and any failure returns nonzero."
        )
    )
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--expected-contract-sha256", required=True)
    parser.add_argument("--stack-bin", required=True, type=Path)
    parser.add_argument("--expected-stack-bin-path", required=True, type=Path)
    parser.add_argument("--ghcup-bin", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint이며 실패해도 가능한 check를 모두 JSON에 기록한다."""

    arguments = _parse_arguments(argv)
    try:
        report = validate_provenance(
            arguments.contract.resolve(),
            expected_contract_sha256=arguments.expected_contract_sha256,
            stack_bin=arguments.stack_bin,
            expected_stack_bin_path=arguments.expected_stack_bin_path,
            ghcup_bin=arguments.ghcup_bin,
        )
    except OracleContractError as exc:
        report = {
            "schemaVersion": "s1.4x-toolchain-provenance-validation-v1",
            "contractSha256": None,
            "checks": [
                {
                    "id": "contract.load",
                    "expected": "valid provenance lock",
                    "actual": str(exc),
                    "status": "FAIL",
                }
            ],
            "failureCount": 1,
            "status": "FAIL",
        }
    atomic_write_json(arguments.output, report)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
