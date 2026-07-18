"""GHC 9.14.1 typed result가 실패 phase 이후 실행을 성공으로 위장하지 못하는지 검증한다."""

from __future__ import annotations

import copy
import shutil
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker  # type: ignore[import-untyped]

from oracle_common import (
    OracleContractError,
    atomic_write_json,
    sha256_file,
    strict_json_load,
)
from validate_contract import (
    validate_ghc_compatibility_result,
    validate_json_schemas,
)

SHA256 = "0" * 64
CONTRACT = Path(__file__).resolve().parents[2] / "contract"
QUALIFICATION_FIELDS = {
    "toolchain": "toolchainQualification",
    "configuration": "configurationQualification",
    "dependency": "dependencyQualification",
    "candidateCompile": "candidateCompile",
}
REPLAY_FIELDS = {
    "fullCorrectness": "fullCorrectness",
    "stableErrorReplay": "stableErrorReplay",
    "processReplay": "processReplay",
    "oracleReplay": "oracleReplay",
    "crossReplay": "crossReplay",
}
PHASES = [*QUALIFICATION_FIELDS, *REPLAY_FIELDS]


def _schema() -> dict[str, Any]:
    path = CONTRACT / "schemas" / "ghc-compatibility-result.schema.json"
    loaded = strict_json_load(path)
    assert isinstance(loaded, dict)
    return loaded


def _phase(status: str) -> dict[str, Any]:
    return {
        "status": status,
        "evidenceSha256": None if status == "NOT_RUN" else SHA256,
    }


def _replay(status: str) -> dict[str, Any]:
    return {
        "status": status,
        "mismatchCount": None if status == "NOT_RUN" else 0,
        "evidenceSha256": None if status == "NOT_RUN" else SHA256,
    }


def _passing_result() -> dict[str, Any]:
    return {
        "schemaVersion": "s1.4x-ghc-compatibility-result-v1",
        "laneId": "ghc-9.14.1-non-scoring",
        "nonScoring": True,
        "result": "PASS",
        "compilerVersion": "9.14.1",
        "compilerPathId": "GHCUP_GHC_9_14_1",
        "compilerSha256": SHA256,
        "ghcupToolId": "GHCUP_0_2_6_2_LINUX_X86_64",
        "ghcupVersion": "0.2.6.2",
        "ghcupSha256": "9ed5da5449b48043a0d17e767c05d2ef585e25a639bb934329496c6d2fad9cf8",
        "ghcupMetadataCommit": "0341867f2d419567cf42ea6931e031b00ab3a922",
        "ghcupMetadataUri": (
            "https://github.com/haskell/ghcup-metadata/commit/"
            "0341867f2d419567cf42ea6931e031b00ab3a922"
        ),
        "stackPolicy": "GHCup-managed exact-version installation",
        "stackInstallCommand": "ghcup install stack 3.11.1",
        "stackBinPathId": "GHCUP_STACK_3_11_1",
        "stackNumericVersion": "3.11.1",
        "stackDistributionChannel": "ghcup-managed",
        "stackArchiveUri": (
            "https://downloads.haskell.org/~ghcup/unofficial-bindists/stack/3.11.1/"
            "stack-3.11.1-linux-x86_64.tar.gz"
        ),
        "stackArchiveSha256": (
            "ca3cc5e89d87d1b85594a866de4062671d19ec039cd2401df70d4ccff03ffed9"
        ),
        "stackBinSha256": (
            "923dbd137756652c67b376e2447c655b87fcc373f4d104b5073bca913471ecbe"
        ),
        "toolchainProvenanceSha256": SHA256,
        "upstreamStandaloneAssetSha256": (
            "67c66e918801c41ae4d286b1c91f9124f691c1c7d56071b53889cf4a5c667550"
        ),
        "upstreamStandaloneAssetRole": "comparison-only-not-installed-provenance",
        "authoritativeStackYamlSha256": SHA256,
        "authoritativeStackLockSha256": SHA256,
        "compatibilityStackYamlSha256": SHA256,
        "compatibilityStackLockSha256": SHA256,
        "compatibilityPolicySha256": SHA256,
        "authoritativePackageSetSha256": SHA256,
        "authoritativeNonBootPlanSha256": SHA256,
        "compatibilityNonBootPlanSha256": SHA256,
        "authoritativeBootSetSha256": SHA256,
        "compatibilityBootSetSha256": SHA256,
        "nonBootPlanEquivalent": True,
        "expectedBootSetDifferenceOnly": True,
        "forbiddenOverrideKeysPresent": [],
        "commands": [
            {
                "phase": "dependency",
                "argv": ["stack", "build"],
                "cwdId": "HASKELL_COMPAT_ROOT",
                "startedAt": "2026-07-18T00:00:00Z",
                "endedAt": "2026-07-18T00:00:01Z",
                "exitCode": 0,
                "stdoutSha256": SHA256,
                "stderrSha256": SHA256,
            }
        ],
        "toolchainQualification": _phase("PASS"),
        "configurationQualification": _phase("PASS"),
        "dependencyQualification": _phase("PASS"),
        "candidateCompile": _phase("PASS"),
        "fullCorrectness": _replay("PASS"),
        "stableErrorReplay": _replay("PASS"),
        "processReplay": _replay("PASS"),
        "oracleReplay": _replay("PASS"),
        "crossReplay": _replay("PASS"),
        "failurePhase": None,
        "downstreamNotRun": [],
        "minimalReproducerSha256": None,
        "candidateSourceTreeSha256": SHA256,
        "performanceInput": False,
    }


def _failed_result(result: str, phase: str) -> dict[str, Any]:
    instance = _passing_result()
    instance["result"] = result
    instance["failurePhase"] = phase
    instance["minimalReproducerSha256"] = None if result == "FAIL_ENVIRONMENT" else SHA256
    failure_index = PHASES.index(phase)
    command_template = instance["commands"][0]
    instance["commands"] = [
        {
            **command_template,
            "phase": phase_name,
            "exitCode": 1 if phase_name == phase else 0,
        }
        for phase_name in PHASES[: failure_index + 1]
    ]
    for index, phase_name in enumerate(PHASES):
        status = "PASS" if index < failure_index else "NOT_RUN"
        if index == failure_index:
            status = "FAIL"
        field = QUALIFICATION_FIELDS.get(phase_name) or REPLAY_FIELDS[phase_name]
        instance[field] = (
            _phase(status) if phase_name in QUALIFICATION_FIELDS else _replay(status)
        )
    instance["downstreamNotRun"] = PHASES[failure_index + 1 :]
    return instance


def _errors(instance: dict[str, Any]) -> list[Any]:
    return list(
        Draft202012Validator(
            _schema(),
            format_checker=FormatChecker(),
        ).iter_errors(instance)
    )


def _validate_semantics(instance: dict[str, Any]) -> int:
    policy_path = CONTRACT / "ghc-compatibility-policy.v1.json"
    policy = strict_json_load(policy_path)
    assert isinstance(policy, dict)
    instance["compatibilityPolicySha256"] = sha256_file(policy_path)
    return validate_ghc_compatibility_result(
        instance,
        policy=policy,
        policy_sha256=sha256_file(policy_path),
        field="reports/ghc-compatibility-result.v1.json",
    )


def test_policy_and_result_schema_freeze_the_same_lane_identity() -> None:
    policy = strict_json_load(CONTRACT / "ghc-compatibility-policy.v1.json")
    schema = _schema()
    assert isinstance(policy, dict)
    assert policy["laneId"] == schema["properties"]["laneId"]["const"]


def test_pass_requires_every_phase_to_pass() -> None:
    instance = _passing_result()
    assert _errors(instance) == []
    assert _validate_semantics(instance) == 1

    instance["oracleReplay"] = _replay("FAIL")
    assert _errors(instance)


@pytest.mark.parametrize(
    ("result", "phase"),
    [
        ("FAIL_FROZEN_DEPENDENCY", "dependency"),
        ("FAIL_CANDIDATE_SOURCE", "candidateCompile"),
        ("FAIL_CANDIDATE_SOURCE", "fullCorrectness"),
        ("FAIL_CANDIDATE_SOURCE", "stableErrorReplay"),
        ("FAIL_CANDIDATE_SOURCE", "processReplay"),
        ("FAIL_CANDIDATE_SOURCE", "oracleReplay"),
        ("FAIL_CANDIDATE_SOURCE", "crossReplay"),
        ("FAIL_ENVIRONMENT", "toolchain"),
        ("FAIL_ENVIRONMENT", "configuration"),
        ("FAIL_UNCLASSIFIED", "dependency"),
        ("FAIL_UNCLASSIFIED", "crossReplay"),
    ],
)
def test_failure_enum_enforces_exact_phase_closure(result: str, phase: str) -> None:
    instance = _failed_result(result, phase)
    assert _errors(instance) == []
    assert _validate_semantics(instance) == PHASES.index(phase) + 1

    tampered = copy.deepcopy(instance)
    last_phase = PHASES[-1]
    if last_phase != phase:
        tampered[REPLAY_FIELDS[last_phase]] = _replay("PASS")
    else:
        tampered["failurePhase"] = "oracleReplay"
    assert _errors(tampered)


def test_failure_phase_and_evidence_cannot_be_omitted() -> None:
    instance = _failed_result("FAIL_CANDIDATE_SOURCE", "candidateCompile")
    del instance["failurePhase"]
    assert _errors(instance)

    instance = _failed_result("FAIL_ENVIRONMENT", "toolchain")
    instance["toolchainQualification"]["evidenceSha256"] = None
    assert _errors(instance)


def test_frozen_dependency_requires_nonzero_dependency_command_and_no_downstream() -> None:
    instance = _failed_result("FAIL_FROZEN_DEPENDENCY", "dependency")
    assert _errors(instance) == []

    zero_exit = copy.deepcopy(instance)
    zero_exit["commands"][-1]["exitCode"] = 0
    assert _errors(zero_exit)

    downstream_started = copy.deepcopy(instance)
    downstream_started["commands"].append(
        {
            **downstream_started["commands"][-1],
            "phase": "candidateCompile",
            "exitCode": 1,
        }
    )
    assert _errors(downstream_started)


def test_pass_command_nonzero_is_rejected_by_phase_exit_join() -> None:
    instance = _passing_result()
    instance["commands"][0]["exitCode"] = 1
    assert _errors(instance) == []

    with pytest.raises(OracleContractError, match="PASS phase dependency"):
        _validate_semantics(instance)


@pytest.mark.parametrize(
    ("result", "failure_phase", "earlier_phase"),
    [
        ("FAIL_FROZEN_DEPENDENCY", "dependency", "toolchain"),
        ("FAIL_CANDIDATE_SOURCE", "fullCorrectness", "candidateCompile"),
        ("FAIL_ENVIRONMENT", "configuration", "toolchain"),
        ("FAIL_UNCLASSIFIED", "dependency", "toolchain"),
    ],
)
def test_failure_rejects_nonzero_command_in_earlier_pass_phase(
    result: str,
    failure_phase: str,
    earlier_phase: str,
) -> None:
    instance = _failed_result(result, failure_phase)
    earlier_command = next(
        command
        for command in instance["commands"]
        if command["phase"] == earlier_phase
    )
    earlier_command["exitCode"] = 1
    assert _errors(instance) == []

    with pytest.raises(
        OracleContractError,
        match=rf"PASS phase {earlier_phase}",
    ):
        _validate_semantics(instance)


def test_failure_commands_must_follow_policy_phase_order() -> None:
    instance = _failed_result("FAIL_CANDIDATE_SOURCE", "crossReplay")
    instance["commands"].reverse()
    assert _errors(instance) == []

    with pytest.raises(OracleContractError, match="phase order is not nondecreasing"):
        _validate_semantics(instance)


def test_semantic_validator_binds_exact_policy_sha() -> None:
    instance = _passing_result()
    policy_path = CONTRACT / "ghc-compatibility-policy.v1.json"
    policy = strict_json_load(policy_path)
    assert isinstance(policy, dict)

    with pytest.raises(OracleContractError, match="does not match the policy bytes"):
        validate_ghc_compatibility_result(
            instance,
            policy=policy,
            policy_sha256=sha256_file(policy_path),
            field="reports/ghc-compatibility-result.v1.json",
        )


def test_optional_reports_scan_dispatches_ghc_semantic_validator(
    tmp_path: Path,
) -> None:
    s1_root = tmp_path / "s1-4x-numeric-parity"
    contract = s1_root / "contract"
    reports = s1_root / "reports"
    shutil.copytree(CONTRACT, contract)
    reports.mkdir()
    report_path = reports / "ghc-compatibility-result.v1.json"
    instance = _passing_result()
    instance["compatibilityPolicySha256"] = sha256_file(
        contract / "ghc-compatibility-policy.v1.json"
    )
    instance["commands"][0]["exitCode"] = 1
    atomic_write_json(report_path, instance)

    with pytest.raises(OracleContractError, match="PASS phase dependency"):
        validate_json_schemas(contract)

    instance["commands"][0]["exitCode"] = 0
    atomic_write_json(report_path, instance)
    validated = validate_json_schemas(contract)
    assert (
        validated["reports/ghc-compatibility-result.v1.json"]
        == "ghc-compatibility-result.schema.json"
    )
