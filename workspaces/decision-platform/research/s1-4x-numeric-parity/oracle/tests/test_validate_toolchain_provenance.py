from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from oracle_common import atomic_write_json, find_repo_root, sha256_file, strict_json_load
from validate_toolchain_provenance import (
    FROZEN_FIELDS,
    validate_provenance,
)


class FakeRuntime:
    """portable path ID가 local absolute path 문자열에 의존하지 않게 검증한다."""

    def __init__(
        self,
        *,
        resolved_stack: str = "/portable/ghcup/stack/3.11.1/bin/stack",
        resolved_expected_stack: str | None = None,
        stack_sha: str | None = None,
        stack_version: str = "3.11.1",
    ) -> None:
        self.resolved_stack = resolved_stack
        self.resolved_expected_stack = resolved_expected_stack
        self.stack_sha = stack_sha or FROZEN_FIELDS["stackBinSha256"]
        self.stack_version = stack_version

    def resolve(self, path: Path) -> str:
        if self.resolved_expected_stack is not None and "expected" in path.parts:
            return self.resolved_expected_stack
        return self.resolved_stack

    def executable(self, path: Path) -> bool:
        del path
        return True

    def sha256(self, path: Path) -> str:
        if "ghcup" in path.name:
            value = FROZEN_FIELDS["ghcupAssetSha256"]
            assert isinstance(value, str)
            return value
        return self.stack_sha

    def numeric_version(self, path: Path) -> str:
        return "0.2.6.2" if "ghcup" in path.name else self.stack_version


def _contract_path() -> Path:
    return (
        find_repo_root()
        / "workspaces"
        / "decision-platform"
        / "research"
        / "s1-4x-numeric-parity"
        / "contract"
        / "toolchain-provenance.v1.json"
    )


def _validate(path: Path, *, runtime: FakeRuntime | None = None) -> dict[str, Any]:
    stack = Path("/logical/stack")
    return validate_provenance(
        path,
        expected_contract_sha256=sha256_file(path),
        stack_bin=stack,
        expected_stack_bin_path=stack,
        ghcup_bin=Path("/logical/ghcup"),
        runtime=runtime or FakeRuntime(),
    )


def test_exact_portable_lock_and_runtime_mapping_pass() -> None:
    report = _validate(_contract_path())

    assert report["status"] == "PASS"
    assert report["failureCount"] == 0


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("stackArchiveSha256", "0" * 64),
        ("stackBinSha256", "1" * 64),
        ("upstreamStandaloneAssetRole", "installed-provenance"),
        ("stackBinPathId", "UNKNOWN_STACK_PATH"),
    ],
)
def test_frozen_provenance_field_drift_fails(
    tmp_path: Path,
    field: str,
    replacement: str,
) -> None:
    contract = copy.deepcopy(strict_json_load(_contract_path()))
    contract[field] = replacement
    path = tmp_path / "contract.json"
    atomic_write_json(path, contract)

    report = _validate(path)

    assert report["status"] == "FAIL"
    assert report["failureCount"] >= 1


def test_stale_evidence_digest_fails(tmp_path: Path) -> None:
    contract = copy.deepcopy(strict_json_load(_contract_path()))
    contract["gate0MetadataEntryEvidence"]["payload"]["verifiedAt"] = "2026-07-18T05:52:13Z"
    path = tmp_path / "contract.json"
    atomic_write_json(path, contract)

    report = _validate(path)

    assert report["status"] == "FAIL"
    assert any(
        check["id"] == "evidence.digestSha256" and check["status"] == "FAIL"
        for check in report["checks"]
    )


def test_contract_hash_and_runtime_path_sha_version_mismatch_fail() -> None:
    path = _contract_path()
    stack = Path("/logical/stack")
    hash_report = validate_provenance(
        path,
        expected_contract_sha256="0" * 64,
        stack_bin=stack,
        expected_stack_bin_path=stack,
        runtime=FakeRuntime(),
    )
    runtime_report = validate_provenance(
        path,
        expected_contract_sha256=sha256_file(path),
        stack_bin=Path("/actual/stack"),
        expected_stack_bin_path=Path("/expected/stack"),
        runtime=FakeRuntime(
            resolved_expected_stack="/portable/expected/stack",
            stack_sha="f" * 64,
            stack_version="3.11.0",
        ),
    )

    assert hash_report["status"] == "FAIL"
    assert runtime_report["status"] == "FAIL"
    failed_ids = {
        check["id"] for check in runtime_report["checks"] if check["status"] == "FAIL"
    }
    assert {
        "runtime.stack.path",
        "runtime.stack.sha256",
        "runtime.stack.numeric-version",
    } <= failed_ids
