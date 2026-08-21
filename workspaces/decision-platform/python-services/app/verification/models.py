from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal, cast

from app.data._shared.canonical_json import canonical_json_sha256


ImplementationState = Literal[
    "IMPLEMENTED", "INTENTIONALLY_DISABLED", "NOT_IMPLEMENTED", "EXTERNAL_PLACEHOLDER"
]
ExecutionState = Literal["PASS", "FAIL", "BLOCKED", "NOT_RUN", "NOT_APPLICABLE"]
AggregateOutcome = Literal["PASS", "FAIL", "BLOCKED", "INCOMPLETE"]

PROFILE_IDS: Final = frozenset(
    {
        "S0_S5_CURRENT",
        "PROVIDER_READ_SMOKE",
        "S6_OFFLINE",
        "S7_RUNTIME",
        "P1_DEMO",
        "P1_LIVE_READINESS",
        "P1_FULL",
    }
)
S0_S5_REQUIRED_GATES: Final = frozenset(
    {
        "MARKET_DATA_OFFLINE_STATE_CHAIN",
        "DECISION_INTERNAL_PAPER_STATE_CHAIN",
        "LIGHTGBM_RESEARCH_ONLY_BOUNDARY",
        "MARKET_DATA_CHAIN_GUARD",
    }
)
PROVIDER_READ_SMOKE_GATES: Final = frozenset(
    {
        "KRX_KOSPI_DAILY",
        "KRX_KOSDAQ_DAILY",
        "KIS_CURRENT_PRICE",
        "KIS_DAILY_BAR",
        "ECOS_POLICY_RATE_DAILY",
        "ECOS_KRW_USD_DAILY",
    }
)


@dataclass(frozen=True, slots=True)
class GateResult:
    gate_id: str
    required: bool
    implementation_state: ImplementationState
    execution_state: ExecutionState
    physical_call_count: int
    evidence_sha256: str | None
    failure_code: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "evidenceSha256": self.evidence_sha256,
            "executionState": self.execution_state,
            "failureCode": self.failure_code,
            "gateId": self.gate_id,
            "implementationState": self.implementation_state,
            "physicalCallCount": self.physical_call_count,
            "required": self.required,
        }


@dataclass(frozen=True, slots=True)
class VerificationReport:
    run_id: str
    profile: str
    head_sha: str
    started_at: datetime
    completed_at: datetime
    implementation_state: ImplementationState
    execution_state: ExecutionState
    aggregate_outcome: AggregateOutcome
    gates: tuple[GateResult, ...]
    provider_data_physical_calls: int = 0
    kis_token_physical_calls: int = 0
    account_calls: int = 0
    balance_calls: int = 0
    order_calls: int = 0
    live_order_calls: int = 0
    product_db_writes: int = 0
    packet_sha256: str | None = None

    def to_dict(self) -> dict[str, object]:
        body: dict[str, object] = {
            "accountCalls": self.account_calls,
            "aggregateOutcome": self.aggregate_outcome,
            "balanceCalls": self.balance_calls,
            "completedAt": _iso(self.completed_at),
            "contractId": "p1-verification-report.v1",
            "executionState": self.execution_state,
            "gates": [gate.to_dict() for gate in self.gates],
            "headSha": self.head_sha,
            "implementationState": self.implementation_state,
            "kisTokenPhysicalCalls": self.kis_token_physical_calls,
            "liveOrderCalls": self.live_order_calls,
            "orderCalls": self.order_calls,
            "productDbWrites": self.product_db_writes,
            "profile": self.profile,
            "providerDataPhysicalCalls": self.provider_data_physical_calls,
            "runId": self.run_id,
            "startedAt": _iso(self.started_at),
        }
        if self.packet_sha256 is not None:
            body["packetSha256"] = self.packet_sha256
        return {**body, "evidenceSha256": canonical_json_sha256(body)}

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "VerificationReport":
        evidence = value.get("evidenceSha256")
        body = {key: item for key, item in value.items() if key != "evidenceSha256"}
        if not isinstance(evidence, str) or canonical_json_sha256(body) != evidence:
            raise ValueError("P1 verification report evidence hash mismatch")
        profile = value.get("profile")
        if profile not in PROFILE_IDS:
            raise ValueError("P1 verification report profile is invalid")
        raw_gates = value.get("gates")
        if not isinstance(raw_gates, list):
            raise ValueError("P1 verification report gates are invalid")
        gates = tuple(_gate_from_dict(item) for item in raw_gates)
        if len({gate.gate_id for gate in gates}) != len(gates):
            raise ValueError("P1 verification report gate ids are not unique")
        report = cls(
            run_id=cast(str, value["runId"]),
            profile=profile,
            head_sha=cast(str, value["headSha"]),
            started_at=_datetime(cast(str, value["startedAt"])),
            completed_at=_datetime(cast(str, value["completedAt"])),
            implementation_state=cast(ImplementationState, value["implementationState"]),
            execution_state=cast(ExecutionState, value["executionState"]),
            aggregate_outcome=cast(AggregateOutcome, value["aggregateOutcome"]),
            gates=gates,
            provider_data_physical_calls=cast(int, value["providerDataPhysicalCalls"]),
            kis_token_physical_calls=cast(int, value["kisTokenPhysicalCalls"]),
            account_calls=cast(int, value["accountCalls"]),
            balance_calls=cast(int, value["balanceCalls"]),
            order_calls=cast(int, value["orderCalls"]),
            live_order_calls=cast(int, value["liveOrderCalls"]),
            product_db_writes=cast(int, value["productDbWrites"]),
            packet_sha256=cast(str | None, value.get("packetSha256")),
        )
        report.validate()
        return report

    def validate(self) -> None:
        if self.completed_at < self.started_at:
            raise ValueError("P1 verification report time order is invalid")
        if any(
            value != 0
            for value in (
                self.account_calls,
                self.balance_calls,
                self.order_calls,
                self.live_order_calls,
            )
        ):
            raise ValueError("P1 verification report contains forbidden live authority")
        if self.execution_state == "PASS" and self.aggregate_outcome != "PASS":
            raise ValueError("passing P1 verification must have PASS aggregate")
        if any(
            gate.implementation_state in {"NOT_IMPLEMENTED", "EXTERNAL_PLACEHOLDER"}
            and gate.execution_state == "PASS"
            for gate in self.gates
        ):
            raise ValueError("unimplemented P1 gate cannot pass")
        if any(gate.physical_call_count < 0 for gate in self.gates):
            raise ValueError("P1 verification physical call count is invalid")
        if any(
            gate.execution_state == "PASS"
            and (gate.evidence_sha256 is None or gate.failure_code is not None)
            for gate in self.gates
        ):
            raise ValueError("passing P1 gate evidence is invalid")
        if self.profile == "S0_S5_CURRENT":
            if self.provider_data_physical_calls != 0 or self.kis_token_physical_calls != 0:
                raise ValueError("S0-S5 verification must be provider-free")
            if self.execution_state == "PASS":
                by_id = {gate.gate_id: gate for gate in self.gates}
                if set(by_id) != S0_S5_REQUIRED_GATES:
                    raise ValueError("S0-S5 PASS requires the exact offline gates")
                if any(
                    gate.implementation_state != "IMPLEMENTED"
                    or gate.execution_state != "PASS"
                    or gate.physical_call_count != 0
                    or gate.failure_code is not None
                    for gate in by_id.values()
                ):
                    raise ValueError("S0-S5 PASS requires provider-free gate success")
        if self.profile == "PROVIDER_READ_SMOKE" and self.execution_state == "PASS":
            by_id = {gate.gate_id: gate for gate in self.gates}
            if set(by_id) != PROVIDER_READ_SMOKE_GATES:
                raise ValueError("provider smoke PASS requires the exact six gates")
            if self.provider_data_physical_calls != 6 or self.product_db_writes != 0:
                raise ValueError("provider smoke PASS accounting is invalid")
            if any(
                gate.implementation_state != "IMPLEMENTED"
                or gate.execution_state != "PASS"
                or gate.physical_call_count != 1
                for gate in by_id.values()
            ):
                raise ValueError("provider smoke PASS requires six single-attempt successes")


def _gate_from_dict(value: object) -> GateResult:
    if not isinstance(value, dict):
        raise ValueError("P1 verification gate is invalid")
    return GateResult(
        gate_id=cast(str, value["gateId"]),
        required=cast(bool, value["required"]),
        implementation_state=cast(ImplementationState, value["implementationState"]),
        execution_state=cast(ExecutionState, value["executionState"]),
        physical_call_count=cast(int, value["physicalCallCount"]),
        evidence_sha256=cast(str | None, value.get("evidenceSha256")),
        failure_code=cast(str | None, value.get("failureCode")),
    )


def _datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("P1 verification timestamp must be timezone aware")
    return parsed


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("P1 verification timestamp must be timezone aware")
    return value.isoformat().replace("+00:00", "Z")
