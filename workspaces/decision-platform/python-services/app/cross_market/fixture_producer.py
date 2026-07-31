from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Protocol, Sequence


class PayloadConflictError(RuntimeError):
    """같은 logical identity의 다른 canonical payload를 append하려는 충돌이다."""


class AppendDisposition(StrEnum):
    INSERTED = "INSERTED"
    REPLAY = "REPLAY"


@dataclass(frozen=True, slots=True)
class AppendSummary:
    inserted: int
    replayed: int


@dataclass(slots=True)
class CrossMarketFixtureBatch:
    entitlements: list[dict[str, object]]
    exposures: list[dict[str, object]]
    observations: list[dict[str, object]]
    analyst_evidence: list[dict[str, object]]
    cause_evidence: list[dict[str, object]]
    provider_physical_calls: int = 0
    external_llm_calls: int = 0

    @property
    def record_count(self) -> int:
        return sum(
            len(values)
            for values in (
                self.entitlements,
                self.exposures,
                self.observations,
                self.analyst_evidence,
                self.cause_evidence,
            )
        )

    def canonical_bytes(self) -> bytes:
        return _canonical(
            {
                "analystEvidence": self.analyst_evidence,
                "causeEvidence": self.cause_evidence,
                "entitlements": self.entitlements,
                "exposures": self.exposures,
                "externalLlmCalls": self.external_llm_calls,
                "observations": self.observations,
                "providerPhysicalCalls": self.provider_physical_calls,
            }
        )

    def record_groups(self) -> tuple[tuple[str, list[dict[str, object]]], ...]:
        return (
            ("ENTITLEMENT", self.entitlements),
            ("EXPOSURE", self.exposures),
            ("OBSERVATION", self.observations),
            ("ANALYST", self.analyst_evidence),
            ("CAUSE", self.cause_evidence),
        )


class AppendOnlyCrossMarketRepository(Protocol):
    """fixture batch 전체를 한 transaction으로 append하는 저장 port다."""

    def append_batch(self, batch: CrossMarketFixtureBatch) -> AppendSummary: ...


class InMemoryAppendOnlyCrossMarketRepository:
    """PostgreSQL의 replay/conflict/atomic semantics를 재현하는 offline test adapter다."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], bytes] = {}

    @property
    def record_count(self) -> int:
        return len(self._records)

    def append_batch(self, batch: CrossMarketFixtureBatch) -> AppendSummary:
        candidate = dict(self._records)
        inserted = 0
        replayed = 0
        for kind, records in batch.record_groups():
            for record in records:
                identity = _required_hash(record, "logicalIdentityHash")
                key = (kind, identity)
                payload = _canonical(record)
                existing = candidate.get(key)
                if existing is None:
                    candidate[key] = payload
                    inserted += 1
                elif existing == payload:
                    replayed += 1
                else:
                    raise PayloadConflictError("cross-market logical identity conflict")
        self._records = candidate
        return AppendSummary(inserted=inserted, replayed=replayed)


class CrossMarketFixtureProducer:
    """network client 없이 synthetic/manual EOD batch만 append하는 producer다."""

    def __init__(self, repository: AppendOnlyCrossMarketRepository) -> None:
        self._repository = repository

    def materialize(self, batch: CrossMarketFixtureBatch) -> AppendSummary:
        if batch.provider_physical_calls != 0:
            raise ValueError("cross-market provider physical calls must remain zero")
        if batch.external_llm_calls != 0:
            raise ValueError("cross-market external LLM calls must remain zero")
        if any(
            item.get("activationStatus") != "CANDIDATE_DISABLED"
            or item.get("providerCallsAllowed") is not False
            for item in batch.entitlements
        ):
            raise ValueError("cross-market fixture entitlement must remain disabled")
        _reject_forbidden_fields(batch)
        return self._repository.append_batch(batch)


class SyntheticEodFixtureFactory:
    """S4.8A 19-source disabled registry에 결속된 재현 가능한 253-session fixture를 만든다."""

    def build(self, evaluation_date: date) -> CrossMarketFixtureBatch:
        if evaluation_date < date(2026, 1, 1):
            raise ValueError("cross-market fixture evaluation date is outside the bounded epoch")
        entitlements = _load_entitlements()
        by_source = {str(item["sourceId"]): item for item in entitlements}
        sessions = _completed_weekdays(evaluation_date, 253)
        observations = _observations(sessions, by_source)
        return CrossMarketFixtureBatch(
            entitlements=entitlements,
            exposures=_exposures(evaluation_date, by_source),
            observations=observations,
            analyst_evidence=_analyst_evidence(evaluation_date),
            cause_evidence=_cause_evidence(evaluation_date, by_source),
        )


def _observations(
    sessions: Sequence[date],
    entitlements: dict[str, dict[str, object]],
) -> list[dict[str, object]]:
    series = (
        ("NVDA", "XNAS", "SESSION_RETURN", "KIS_DISABLED_04"),
        ("MU", "XNAS", "SESSION_RETURN", "KIS_DISABLED_05"),
        ("AMD", "XNAS", "SESSION_RETURN", "KIS_DISABLED_06"),
        ("ASML", "XNAS", "SESSION_RETURN", "KIS_DISABLED_07"),
        ("QQQ", "XNAS", "SESSION_RETURN", "KIS_DISABLED_04"),
        ("SPY", "XNYS", "SESSION_RETURN", "KIS_DISABLED_05"),
        ("USDKRW", "FX", "PRICE", "KIS_DISABLED_06"),
        ("MARGIN_CREDIT", "XKRX", "STRESS_LEVEL", "KIS_DISABLED_08"),
        ("SHORT_SELLING", "XKRX", "STRESS_LEVEL", "KIS_DISABLED_09"),
        ("STOCK_LOAN", "XKRX", "STRESS_LEVEL", "KIS_DISABLED_10"),
        ("PROGRAM_NET", "XKRX", "STRESS_LEVEL", "KIS_DISABLED_11"),
        ("FOREIGN_FLOW", "XKRX", "STRESS_LEVEL", "KIS_DISABLED_12"),
        ("INSTITUTION_FLOW", "XKRX", "STRESS_LEVEL", "KIS_DISABLED_13"),
        ("CREDIT_BALANCE", "XKRX", "STRESS_LEVEL", "KIS_DISABLED_14"),
        ("LOAN_BALANCE", "XKRX", "STRESS_LEVEL", "KIS_DISABLED_15"),
        ("SHORT_BALANCE", "XKRX", "STRESS_LEVEL", "KIS_DISABLED_16"),
        ("PROGRAM_BUY", "XKRX", "STRESS_LEVEL", "KIS_DISABLED_17"),
        ("PROGRAM_SELL", "XKRX", "STRESS_LEVEL", "KIS_DISABLED_18"),
    )
    records: list[dict[str, object]] = []
    for series_index, (instrument, market, value_type, source_id) in enumerate(series):
        source_ref = str(entitlements[source_id]["logicalIdentityHash"])
        for session_index, session in enumerate(sessions):
            observed_at = datetime.combine(session, time(21, 0), tzinfo=UTC)
            available_at = datetime.combine(session + timedelta(days=1), time(0, 0), tzinfo=UTC)
            value = _fixture_value(value_type, series_index, session_index)
            core: dict[str, object] = {
                "availableAt": _instant(available_at),
                "completeness": "COMPLETE",
                "contractId": "cross_market_observation.v1",
                "decisionAuthority": "NONE",
                "evaluatedAt": _instant(available_at + timedelta(seconds=1)),
                "instrument": instrument,
                "market": market,
                "observedAt": _instant(observed_at),
                "receivedAt": _instant(available_at - timedelta(seconds=1)),
                "schemaVersion": "1",
                "sessionDate": session.isoformat(),
                "sourceRef": source_ref,
                "status": "AVAILABLE",
                "timeframe": "EOD",
                "value": value,
                "valueType": value_type,
            }
            records.append(_with_hashes(core, f"observation|{instrument}|{session}"))
    return records


def _fixture_value(value_type: str, series_index: int, session_index: int) -> float:
    if value_type == "SESSION_RETURN":
        return round((((series_index + 3) * 17 + session_index * 13) % 401 - 200) / 10_000, 6)
    if value_type == "PRICE":
        return round(1_150 + ((session_index * 7 + series_index) % 260) * 0.75, 4)
    return round(((series_index + 5) * 19 + session_index * 11) % 10_001 / 100, 2)


def _exposures(
    evaluation_date: date,
    entitlements: dict[str, dict[str, object]],
) -> list[dict[str, object]]:
    available = datetime.combine(evaluation_date, time(0, 0, 1), tzinfo=UTC)
    mappings = (
        ("005930", "SEMICONDUCTOR"),
        ("000660", "SEMICONDUCTOR"),
        ("069500", "BROAD_MARKET"),
        ("261240", "FX"),
    )
    lineage = [str(entitlements["KIS_DISABLED_04"]["logicalIdentityHash"])]
    return [
        _with_hashes(
            {
                "availableAt": _instant(available),
                "classification": classification,
                "configVersion": "cross-market-exposure.v1",
                "contractId": "cross_market_exposure_catalog.v1",
                "effectiveAt": _instant(available - timedelta(seconds=1)),
                "inScope": True,
                "schemaVersion": "1",
                "sourceLineage": lineage,
                "symbol": symbol,
                "validationState": "AVAILABLE",
            },
            f"exposure|{symbol}|cross-market-exposure.v1",
        )
        for symbol, classification in mappings
    ]


def _analyst_evidence(evaluation_date: date) -> list[dict[str, object]]:
    published = datetime.combine(evaluation_date - timedelta(days=1), time(0, 0), tzinfo=UTC)
    records: list[dict[str, object]] = []
    for index in range(1, 4):
        evidence_id = f"analyst_revision_fixture_{index:02d}"
        previous_target = 100_000 + index * 1_000
        current_target = previous_target - index * 500
        core: dict[str, object] = {
            "availableAt": _instant(published + timedelta(minutes=3)),
            "brokerId": f"broker_{index:016x}",
            "buyOpinionWeight": 0,
            "contractId": "analyst_revision_evidence.v1",
            "contributorCount": 3,
            "current": {
                "eps": 8_000 - index * 100,
                "rating": "BUY",
                "revenue": 300_000 - index * 1_000,
                "targetPrice": current_target,
            },
            "decisionAuthority": "NONE",
            "dedupeKeyHash": _sha256(f"analyst-dedupe|{index}".encode()),
            "dispersion": round(0.1 + index * 0.01, 4),
            "estimatePeriod": "2026Q3",
            "originalEvidenceId": evidence_id,
            "previous": {
                "eps": 8_000,
                "rating": "BUY",
                "revenue": 300_000,
                "targetPrice": previous_target,
            },
            "publishedAt": _instant(published),
            "rawTextStored": False,
            "receivedAt": _instant(published + timedelta(minutes=2)),
            "retracted": False,
            "revision": {
                "epsDelta": -index * 100,
                "ratingChanged": False,
                "revenueDelta": -index * 1_000,
                "targetPriceDelta": -index * 500,
            },
            "schemaVersion": "1",
            "sourceLicense": "STRUCTURED_FIXTURE",
            "supersedesEvidenceId": None,
            "symbol": "005930",
            "userConfirmedTags": ["CHANGE", "RISK"],
        }
        records.append(_with_hashes(core, f"analyst|{evidence_id}"))
    return records


def _cause_evidence(
    evaluation_date: date,
    entitlements: dict[str, dict[str, object]],
) -> list[dict[str, object]]:
    occurred = datetime.combine(evaluation_date - timedelta(days=1), time(0, 0), tzinfo=UTC)
    lineage = str(entitlements["GDELT_AGGREGATE"]["logicalIdentityHash"])
    definitions = (
        (
            "cause_gdelt_fixture_primary",
            False,
            "MARKET_INTERPRETATION",
            "CO_MOVES_WITH",
            ["cause_gdelt_fixture_counter"],
            "합성 aggregate의 관심도와 평균 tone이 같은 기간에 함께 변했다.",
        ),
        (
            "cause_gdelt_fixture_counter",
            True,
            "HYPOTHESIS",
            "CONTRADICTS",
            ["cause_gdelt_fixture_primary"],
            "같은 변화는 인과가 아닌 동시 관측일 수 있어 단독 원인으로 확정할 수 없다.",
        ),
    )
    records: list[dict[str, object]] = []
    for evidence_id, counterargument, classification, relation, contradictions, summary in definitions:
        core: dict[str, object] = {
            "availableAt": _instant(occurred + timedelta(minutes=3)),
            "classification": classification,
            "contractId": "market_cause_evidence.v1",
            "contradictionEvidenceIds": contradictions,
            "counterargument": counterargument,
            "decisionAuthority": "NONE",
            "dedupeKeyHash": _sha256(f"cause-dedupe|{evidence_id}".encode()),
            "occurredAt": _instant(occurred),
            "publishedAt": _instant(occurred + timedelta(minutes=1)),
            "receivedAt": _instant(occurred + timedelta(minutes=2)),
            "relatedEvidenceIds": [],
            "relation": relation,
            "retracted": False,
            "sanitizedSummary": summary,
            "schemaVersion": "1",
            "sourceFamily": "GDELT_AGGREGATE",
            "sourceLineageHash": lineage,
            "supersedesEvidenceId": None,
        }
        records.append(_with_hashes(core, f"cause|{evidence_id}"))
    return records


def _with_hashes(core: dict[str, object], identity: str) -> dict[str, object]:
    payload_hash = _sha256(_canonical(core))
    record = dict(core)
    record["logicalIdentityHash"] = _sha256(identity.encode("utf-8"))
    record["payloadHash"] = payload_hash
    record["artifactHash"] = _sha256(_canonical(record))
    return record


def _completed_weekdays(before: date, count: int) -> tuple[date, ...]:
    sessions: list[date] = []
    candidate = before - timedelta(days=1)
    while len(sessions) < count:
        if candidate.weekday() < 5:
            sessions.append(candidate)
        candidate -= timedelta(days=1)
    return tuple(reversed(sessions))


def _load_entitlements() -> list[dict[str, object]]:
    path = _repository_root() / "contracts/examples/market_source_entitlement.v1.valid.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    entitlements = payload.get("entitlements")
    if not isinstance(entitlements, list) or len(entitlements) != 19:
        raise ValueError("S4.8A entitlement fixture must contain exact 19 entries")
    return deepcopy(entitlements)


def _repository_root() -> Path:
    current = Path(__file__).resolve()
    while current.parent != current:
        if (current / "AGENTS.md").is_file():
            return current
        current = current.parent
    raise RuntimeError("repository root was not found")


def _reject_forbidden_fields(batch: CrossMarketFixtureBatch) -> None:
    forbidden = {
        "accountnumber",
        "accesstoken",
        "apikey",
        "articlebody",
        "articlemetadata",
        "credential",
        "pdfcontent",
        "providerraw",
        "rawbody",
    }

    def visit(value: object) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if str(key).replace("_", "").casefold() in forbidden:
                    raise ValueError("cross-market fixture contains a forbidden field")
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(json.loads(batch.canonical_bytes()))


def _required_hash(record: dict[str, object], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise ValueError(f"cross-market {field} is invalid")
    return value


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _instant(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")
