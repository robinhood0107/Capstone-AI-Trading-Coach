from __future__ import annotations

import re
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
PROVIDER_READ_SMOKE_GATE_ORDER: Final = (
    "KRX_KOSPI_DAILY",
    "KRX_KOSDAQ_DAILY",
    "KIS_CURRENT_PRICE",
    "KIS_DAILY_BAR",
    "ECOS_POLICY_RATE_DAILY",
    "ECOS_KRW_USD_DAILY",
)
NON_EXECUTABLE_PROFILE_IDS: Final = PROFILE_IDS - {"S0_S5_CURRENT", "PROVIDER_READ_SMOKE"}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_HEAD_SHA = re.compile(r"^[0-9a-f]{40}$")
_RUN_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{7,95}$")
_GATE_ID = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
_REPORT_REQUIRED_KEYS: Final = frozenset(
    {
        "contractId",
        "runId",
        "profile",
        "headSha",
        "startedAt",
        "completedAt",
        "implementationState",
        "executionState",
        "aggregateOutcome",
        "providerDataPhysicalCalls",
        "kisTokenPhysicalCalls",
        "accountCalls",
        "balanceCalls",
        "orderCalls",
        "liveOrderCalls",
        "productDbWrites",
        "gates",
        "evidenceSha256",
    }
)
_GATE_KEYS: Final = frozenset(
    {
        "gateId",
        "required",
        "implementationState",
        "executionState",
        "physicalCallCount",
        "evidenceSha256",
        "failureCode",
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
    contract_id: str = "p1-verification-report.v2"

    def to_dict(self) -> dict[str, object]:
        body: dict[str, object] = {
            "accountCalls": self.account_calls,
            "aggregateOutcome": self.aggregate_outcome,
            "balanceCalls": self.balance_calls,
            "completedAt": _iso(self.completed_at),
            "contractId": self.contract_id,
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
    def from_dict(cls, value: dict[str, object]) -> VerificationReport:
        _validate_report_structure(value)
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
            contract_id=cast(str, value["contractId"]),
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
        if self.profile == "PROVIDER_READ_SMOKE":
            if self.contract_id == "p1-verification-report.v2" and self.packet_sha256 is None:
                raise ValueError("provider smoke report must bind the signed approval packet")
            by_id = {gate.gate_id: gate for gate in self.gates}
            if tuple(gate.gate_id for gate in self.gates) != PROVIDER_READ_SMOKE_GATE_ORDER:
                raise ValueError("provider smoke requires the exact six gates")
            if (
                self.product_db_writes != 0
                or self.kis_token_physical_calls not in {0, 1}
                or self.provider_data_physical_calls
                != sum(gate.physical_call_count for gate in self.gates)
                or self.provider_data_physical_calls + self.kis_token_physical_calls > 7
            ):
                raise ValueError("provider smoke PASS accounting is invalid")
            stopped = False
            for gate in self.gates:
                if stopped and gate.execution_state != "NOT_RUN":
                    raise ValueError("provider smoke must stop after its first non-pass gate")
                if gate.execution_state in {"FAIL", "BLOCKED"}:
                    stopped = True
            failures = [gate for gate in self.gates if gate.execution_state == "FAIL"]
            blocks = [gate for gate in self.gates if gate.execution_state == "BLOCKED"]
            if self.execution_state == "PASS" and any(
                gate.execution_state != "PASS" for gate in self.gates
            ):
                raise ValueError("provider smoke PASS requires every gate to pass")
            if self.execution_state == "FAIL" and (len(failures) != 1 or blocks):
                raise ValueError("provider smoke FAIL requires one terminal failed gate")
            if self.execution_state == "BLOCKED" and (len(blocks) != 1 or failures):
                raise ValueError("provider smoke BLOCKED requires one blocked gate")
            if any(
                gate.execution_state in {"FAIL", "BLOCKED"}
                and (gate.failure_code is None or gate.evidence_sha256 is not None)
                for gate in self.gates
            ):
                raise ValueError("provider smoke terminal gate evidence is invalid")
            if self.execution_state == "PASS" and self.provider_data_physical_calls != 6:
                raise ValueError("provider smoke PASS accounting is invalid")
        if self.profile == "PROVIDER_READ_SMOKE" and self.execution_state == "PASS":
            by_id = {gate.gate_id: gate for gate in self.gates}
            if any(
                gate.implementation_state != "IMPLEMENTED"
                or gate.execution_state != "PASS"
                or gate.physical_call_count != 1
                for gate in by_id.values()
            ):
                raise ValueError("provider smoke PASS requires six single-attempt successes")
        if self.profile in NON_EXECUTABLE_PROFILE_IDS and (
            self.execution_state not in {"NOT_RUN", "NOT_APPLICABLE"}
            or self.aggregate_outcome != "INCOMPLETE"
            or self.provider_data_physical_calls != 0
            or self.kis_token_physical_calls != 0
            or self.product_db_writes != 0
            or any(gate.execution_state == "PASS" for gate in self.gates)
        ):
            raise ValueError("non-executable P1 profile cannot attest completion")


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


def _validate_report_structure(value: dict[str, object]) -> None:
    keys = frozenset(value)
    if not keys >= _REPORT_REQUIRED_KEYS or keys - (_REPORT_REQUIRED_KEYS | {"packetSha256"}):
        raise ValueError("P1 verification report fields are not closed")
    if value.get("contractId") not in {
        "p1-verification-report.v1",
        "p1-verification-report.v2",
    }:
        raise ValueError("P1 verification report contract id is invalid")
    if (
        value.get("contractId") == "p1-verification-report.v2"
        and value.get("profile") == "PROVIDER_READ_SMOKE"
        and "packetSha256" not in value
    ):
        raise ValueError("P1 v2 provider report must bind the signed approval packet")
    run_id = value.get("runId")
    if not isinstance(run_id, str) or _RUN_ID.fullmatch(run_id) is None:
        raise ValueError("P1 verification report run id is invalid")
    head_sha = value.get("headSha")
    if not isinstance(head_sha, str) or _HEAD_SHA.fullmatch(head_sha) is None:
        raise ValueError("P1 verification report head sha is invalid")
    if value.get("profile") not in PROFILE_IDS:
        raise ValueError("P1 verification report profile is invalid")
    if value.get("implementationState") not in {
        "IMPLEMENTED",
        "INTENTIONALLY_DISABLED",
        "NOT_IMPLEMENTED",
        "EXTERNAL_PLACEHOLDER",
    }:
        raise ValueError("P1 verification report implementation state is invalid")
    if value.get("executionState") not in {"PASS", "FAIL", "BLOCKED", "NOT_RUN", "NOT_APPLICABLE"}:
        raise ValueError("P1 verification report execution state is invalid")
    if value.get("aggregateOutcome") not in {"PASS", "FAIL", "BLOCKED", "INCOMPLETE"}:
        raise ValueError("P1 verification report aggregate outcome is invalid")
    _require_int(value, "providerDataPhysicalCalls", 0, 6)
    _require_int(value, "kisTokenPhysicalCalls", 0, 1)
    for key in ("accountCalls", "balanceCalls", "orderCalls", "liveOrderCalls"):
        if value.get(key) != 0 or isinstance(value.get(key), bool):
            raise ValueError(f"P1 verification report {key} is invalid")
    _require_int(value, "productDbWrites", 0, None)
    for key in ("startedAt", "completedAt"):
        timestamp = value.get(key)
        if not isinstance(timestamp, str):
            raise ValueError(f"P1 verification report {key} is invalid")
        _datetime(timestamp)
    for key in ("evidenceSha256", "packetSha256"):
        item = value.get(key)
        if (key == "evidenceSha256" or item is not None) and (
            not isinstance(item, str) or _SHA256.fullmatch(item) is None
        ):
            raise ValueError(f"P1 verification report {key} is invalid")
    gates = value.get("gates")
    if not isinstance(gates, list) or not 1 <= len(gates) <= 32:
        raise ValueError("P1 verification report gates are invalid")
    for gate in gates:
        _validate_gate_structure(gate)


def _validate_gate_structure(value: object) -> None:
    if not isinstance(value, dict) or frozenset(value) != _GATE_KEYS:
        raise ValueError("P1 verification gate fields are not closed")
    gate_id = value.get("gateId")
    if not isinstance(gate_id, str) or _GATE_ID.fullmatch(gate_id) is None:
        raise ValueError("P1 verification gate id is invalid")
    if type(value.get("required")) is not bool:
        raise ValueError("P1 verification gate required flag is invalid")
    if value.get("implementationState") not in {
        "IMPLEMENTED",
        "INTENTIONALLY_DISABLED",
        "NOT_IMPLEMENTED",
        "EXTERNAL_PLACEHOLDER",
    } or value.get("executionState") not in {
        "PASS",
        "FAIL",
        "BLOCKED",
        "NOT_RUN",
        "NOT_APPLICABLE",
    }:
        raise ValueError("P1 verification gate state is invalid")
    _require_int(value, "physicalCallCount", 0, 7)
    evidence = value.get("evidenceSha256")
    if evidence is not None and (
        not isinstance(evidence, str) or _SHA256.fullmatch(evidence) is None
    ):
        raise ValueError("P1 verification gate evidence is invalid")
    failure = value.get("failureCode")
    if failure is not None and (
        not isinstance(failure, str) or _GATE_ID.fullmatch(failure) is None
    ):
        raise ValueError("P1 verification gate failure code is invalid")


def _require_int(value: dict[str, object], key: str, minimum: int, maximum: int | None) -> None:
    item = value.get(key)
    if type(item) is not int or item < minimum or (maximum is not None and item > maximum):
        raise ValueError(f"P1 verification report {key} is invalid")


def _datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("P1 verification timestamp must be timezone aware")
    return parsed


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("P1 verification timestamp must be timezone aware")
    return value.isoformat().replace("+00:00", "Z")
