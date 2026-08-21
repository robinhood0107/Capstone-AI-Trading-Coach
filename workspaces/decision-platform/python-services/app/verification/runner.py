"""Provider-free P1.V0 state-chain verification runner."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
from pathlib import Path
import subprocess
from typing import Final, Protocol

from app.data._shared.canonical_json import canonical_json_sha256
from app.verification.git_identity import current_clean_git_identity
from app.verification.models import GateResult, VerificationReport


@dataclass(frozen=True, slots=True)
class Command:
    args: tuple[str, ...]
    cwd: Path


@dataclass(frozen=True, slots=True)
class CommandEvidence:
    return_code: int
    output_sha256: str


@dataclass(frozen=True, slots=True)
class GateSpec:
    gate_id: str
    commands: tuple[Command, ...]


class CommandExecutor(Protocol):
    def __call__(self, command: Command) -> CommandEvidence: ...


S0_S5_GATE_IDS: Final[tuple[str, ...]] = (
    "MARKET_DATA_OFFLINE_STATE_CHAIN",
    "DECISION_INTERNAL_PAPER_STATE_CHAIN",
    "LIGHTGBM_RESEARCH_ONLY_BOUNDARY",
    "MARKET_DATA_CHAIN_GUARD",
)


def s0_s5_gate_specs(repository_root: Path) -> tuple[GateSpec, ...]:
    """Return the exact provider-free commands owned by the current profile."""

    root = repository_root.resolve(strict=True)
    python_root = root / "workspaces/decision-platform/python-services"
    spring_root = root / "workspaces/decision-platform/spring-api"
    gradle = str(spring_root / "gradlew")
    return (
        GateSpec(
            "MARKET_DATA_OFFLINE_STATE_CHAIN",
            (
                Command(
                    (
                        "uv", "run", "--frozen", "pytest", "-q",
                        "tests/data/market_data",
                        "tests/verification/test_network_guard.py",
                    ),
                    python_root,
                ),
            ),
        ),
        GateSpec(
            "DECISION_INTERNAL_PAPER_STATE_CHAIN",
            (
                Command(
                    ("uv", "run", "--frozen", "pytest", "-q", "tests/test_decision_source_writers.py"),
                    python_root,
                ),
                Command(
                    (
                        gradle,
                        "--no-daemon",
                        "test",
                        "--tests", "com.capstone.decision.BrokerageApiIntegrationTest",
                    ),
                    spring_root,
                ),
            ),
        ),
        GateSpec(
            "LIGHTGBM_RESEARCH_ONLY_BOUNDARY",
            (
                Command(
                    (
                        "uv", "run", "--frozen", "pytest", "-q",
                        "tests/lightgbm/test_s5_research_only_runtime.py",
                    ),
                    python_root,
                ),
                Command(
                    (
                        gradle,
                        "--no-daemon",
                        "test",
                        "--tests", "com.capstone.decision.SignalV2ApiIntegrationTest",
                    ),
                    spring_root,
                ),
            ),
        ),
        GateSpec(
            "MARKET_DATA_CHAIN_GUARD",
            (
                Command(
                    (
                        gradle,
                        "--no-daemon",
                        "test",
                        "--tests", "com.capstone.decision.P1MarketDataChainMigrationContractTest",
                    ),
                    spring_root,
                ),
            ),
        ),
    )


def run_s0_s5_current(
    *,
    repository_root: Path,
    now: datetime | None = None,
    executor: CommandExecutor | None = None,
    git_identity: Callable[[Path], tuple[str, str]] = current_clean_git_identity,
) -> VerificationReport:
    """Run both offline black-box lanes without creating provider authority."""

    started_at = (now or datetime.now(UTC)).astimezone(UTC)
    head_sha, _ = git_identity(repository_root.resolve(strict=True))
    execute = executor or execute_command
    gates: list[GateResult] = []
    for spec in s0_s5_gate_specs(repository_root):
        evidence: list[dict[str, object]] = []
        passed = True
        for command in spec.commands:
            result = execute(command)
            evidence.append(
                {
                    "argsSha256": hashlib.sha256("\0".join(command.args).encode()).hexdigest(),
                    "outputSha256": result.output_sha256,
                    "returnCode": result.return_code,
                }
            )
            if result.return_code != 0:
                passed = False
                break
        gates.append(
            GateResult(
                gate_id=spec.gate_id,
                required=True,
                implementation_state="IMPLEMENTED",
                execution_state="PASS" if passed else "FAIL",
                physical_call_count=0,
                evidence_sha256=canonical_json_sha256(evidence),
                failure_code=None if passed else "REGRESSION_GATE_FAILED",
            )
        )
    completed_at = datetime.now(UTC) if now is None else started_at
    passed = all(gate.execution_state == "PASS" for gate in gates)
    report = VerificationReport(
        run_id=f"p1v0-{started_at.strftime('%Y%m%dt%H%M%S')}-{head_sha[:8]}",
        profile="S0_S5_CURRENT",
        head_sha=head_sha,
        started_at=started_at,
        completed_at=completed_at,
        implementation_state="IMPLEMENTED",
        execution_state="PASS" if passed else "FAIL",
        aggregate_outcome="PASS" if passed else "FAIL",
        gates=tuple(gates),
    )
    report.validate()
    return report


def execute_command(command: Command) -> CommandEvidence:
    """Execute one fixed argv command without a shell and retain only its output hash."""

    try:
        completed = subprocess.run(
            command.args,
            cwd=command.cwd,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=1_800,
        )
    except (OSError, subprocess.SubprocessError):
        return CommandEvidence(return_code=255, output_sha256=hashlib.sha256(b"").hexdigest())
    return CommandEvidence(
        return_code=completed.returncode,
        output_sha256=hashlib.sha256(completed.stdout).hexdigest(),
    )
