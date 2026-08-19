"""S5 bootstrap calendar drift를 provider 재호출 전에 계산·봉인한다."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
import hashlib
from io import BytesIO
import os
from pathlib import Path
import stat
from typing import Mapping, cast

import pyarrow as pa
import pyarrow.parquet as pq

from app.data._shared.bounded_json import (
    BoundedJsonError,
    BoundedJsonLimits,
    parse_bounded_json_bytes,
)
from app.data._shared.canonical_json import canonical_json_bytes
from app.data.krx.production_parsers import S5_PRODUCTION_PROJECTION_FIELDS
from app.lightgbm.bootstrap_executor import provider_query_sha256
from app.lightgbm.bootstrap_journal import (
    JOURNAL_FILENAME,
    MAX_JOURNAL_BYTES,
    BootstrapJournal,
    JournalAttempt,
    SUPERSEDED_CONSUMED,
    build_recovery_journal_bytes,
)
from app.lightgbm.bootstrap_packet import (
    _author_bootstrap_packet,
    BootstrapPacket,
    author_bootstrap_packet,
    author_recovery_bootstrap_packet,
    validate_bootstrap_packet,
)
from app.lightgbm.errors import LightGbmContractError
from app.lightgbm.pit_calendar import (
    S5_ADHOC_CLOSED_SESSIONS,
    S5_CALENDAR_CORRECTION_SET_SHA256,
    S5_CALENDAR_POLICY_VERSION,
    S5_SUPERSEDED_CORRECTION_SETS,
    calendar_for_corrections,
    corrections_for_sha256,
)
from app.lightgbm.production_policy import MAX_KRX_SUPERSEDED_ALLOWANCE
from app.lightgbm.private_root import (
    acquire_run_lock,
    release_run_lock,
    require_private_root,
)
from app.lightgbm.temporal import next_session_evidence_clock
from app.rag.safe_io import (
    RagSafeIoError,
    read_approved_regular_file,
    write_approved_new_file,
)


RECOVERY_VERSION = "s5-bootstrap-calendar-recovery-v1"
RECOVERY_BINDING_VERSION = "s5-bootstrap-calendar-recovery-binding-v1"
RECOVERY_LINEAGE_VERSION = "s5-bootstrap-calendar-recovery-lineage-v1"
_DAILY_KRX_SERVICES = (
    "stk_bydd_trd",
    "ksq_bydd_trd",
    "kospi_dd_trd",
    "kosdaq_dd_trd",
)
_MONTHLY_KRX_SERVICES = (
    "stk_isu_base_info",
    "ksq_isu_base_info",
    "etf_bydd_trd",
)
_MAX_PACKET_BYTES = 1 * 1024 * 1024
_MAX_KRX_CHUNK_BYTES = 4 * 1024 * 1024
_RECOVERY_FIELDS = frozenset(
    {
        "recoveryVersion",
        "recoveryBindingSha256",
        "status",
        "priorPacketSha256",
        "priorProgressSha256",
        "correctedPacketSha256",
        "calendarPolicyVersion",
        "calendarCorrectionSetSha256",
        "correctionSessions",
        "failedQuerySha256",
        "failedOperationId",
        "failedSessionDate",
        "supersededQueries",
        "consumedKrxPhysicalCalls",
        "reusableSuccessfulChunks",
        "supersededConsumedCalls",
        "temporalReceiptsToRebind",
        "missingRequiredKrxQueries",
        "projectedKrxPhysicalCalls",
        "approvedKrxMaxGet",
        "krxSupersededAllowance",
        "krxShortfall",
        "providerCallsDuringRecovery",
    }
)
_LINEAGE_FIELDS = frozenset(
    {
        "lineageVersion",
        "correctedPacketSha256",
        "recoveryBindingSha256",
        "recoveryReceiptSha256",
        "priorPacketSha256",
        "priorProgressSha256",
        "adoptedProgressSha256",
        "adoptedSuccessfulChunks",
        "supersededConsumedCalls",
        "consumedKrxPhysicalCalls",
        "providerCallsDuringAdoption",
    }
)


@dataclass(frozen=True, slots=True)
class KrxQuery:
    """Provider URL 없이 service/date/hash만 보존하는 KRX logical query다."""

    service: str
    session_date: date
    sha256: str


@dataclass(frozen=True, slots=True)
class BootstrapCalendarRecovery:
    """기존 원장과 corrected packet의 재사용 가능성·cap 결과를 결속한다."""

    content: bytes
    sha256: str
    corrected_packet: BootstrapPacket
    status: str
    reusable_chunks: int
    missing_krx_queries: int
    projected_krx_physical_calls: int
    krx_shortfall: int
    temporal_rebindings: int
    recovery_binding_sha256: str
    prior_packet_sha256: str
    prior_progress_sha256: str
    reusable_attempts: tuple[JournalAttempt, ...]
    superseded_attempts: tuple[JournalAttempt, ...]
    prior_corrections: tuple[date, ...]


def assess_bootstrap_calendar_recovery(
    *, approved_root: Path, prior_packet_sha256: str
) -> BootstrapCalendarRecovery:
    """고정 root의 v1 packet/run만 읽어 corrected v2와 누적 physical cap을 검증한다.

    이 함수는 socket/client를 만들지 않으며 모든 성공 chunk의 inode·size·SHA를 다시 확인한다.
    """

    require_private_root(approved_root)
    prior_file = read_approved_regular_file(
        approved_root=approved_root,
        relative_path=f"bootstrap-{_sha(prior_packet_sha256)}.json",
        max_bytes=_MAX_PACKET_BYTES,
    )
    prior = validate_bootstrap_packet(
        prior_file.content,
        expected_sha256=prior_packet_sha256,
        allow_historical_v1=True,
        allow_superseded_corrections=True,
    )
    # prior는 수정 전 historical v1이거나 이미 소비된 recovery packet이다. supersede 사유는 달력
    # correction 또는 승인 상한 변경이며, 두 경우 모두 corrected packet이 prior와 달라야 한다.
    prior_corrections = (
        ()
        if prior.packet_version == "s5-production-bootstrap-packet-v1"
        else corrections_for_sha256(str(prior.calendar_correction_set_sha256))
    )
    if (
        prior.packet_version == "s5-production-bootstrap-packet-v2"
        and prior.lineage_mode != "CALENDAR_RECOVERY"
    ):
        raise LightGbmContractError("calendar recovery prior lineage is invalid")
    generation_changed = tuple(prior_corrections) != tuple(S5_ADHOC_CLOSED_SESSIONS)

    run_root = approved_root / f"run-{prior_packet_sha256}"
    source_root = run_root / "source"
    require_private_root(run_root)
    require_private_root(source_root)
    run_lock = acquire_run_lock(run_root)
    try:
        progress = read_approved_regular_file(
            approved_root=source_root,
            relative_path=JOURNAL_FILENAME,
            max_bytes=MAX_JOURNAL_BYTES,
        )
        journal = BootstrapJournal(source_root, policy_corrections=prior_corrections)
        attempts = journal.attempts
        if not attempts or any(attempt.provider != "KRX" for attempt in attempts):
            raise LightGbmContractError(
                "calendar recovery requires a KRX-only failed prefix"
            )

        corrected_base = author_bootstrap_packet(cutoff=prior.window.cutoff)
        old_queries = _expected_krx_queries(prior)
        corrected_queries = _expected_krx_queries(corrected_base)
        old_by_hash = _unique_by_hash(old_queries)
        corrected_by_hash = _unique_by_hash(corrected_queries)

        failed = tuple(attempt for attempt in attempts if attempt.state == "FAILED")
        if len({attempt.query_sha256 for attempt in failed}) > 1:
            raise LightGbmContractError("calendar recovery failed query is not unique")
        if generation_changed and not failed:
            raise LightGbmContractError("calendar recovery failed query is not unique")
        failed_query = old_by_hash.get(failed[0].query_sha256) if failed else None
        if failed and (
            failed_query is None
            or failed_query.service not in _DAILY_KRX_SERVICES
            or failed_query.session_date not in S5_ADHOC_CLOSED_SESSIONS
            or failed_query.sha256 in corrected_by_hash
        ):
            raise LightGbmContractError(
                "failed KRX query is not an approved calendar correction"
            )

        succeeded = {
            attempt.query_sha256: attempt
            for attempt in attempts
            if attempt.state == "SUCCEEDED"
        }
        if len(succeeded) != sum(
            attempt.state == "SUCCEEDED" for attempt in attempts
        ):
            raise LightGbmContractError("successful KRX query identity is duplicated")
        reusable_attempts = tuple(
            succeeded[query.sha256]
            for query in corrected_queries
            if query.sha256 in succeeded
        )
        reusable_hashes = {attempt.query_sha256 for attempt in reusable_attempts}
        temporal_rebindings = 0
        for attempt in reusable_attempts:
            query = corrected_by_hash[attempt.query_sha256]
            _validate_reusable_chunk(
                source_root=source_root,
                attempt=attempt,
                query=query,
            )
            assert attempt.chunk is not None
            if attempt.chunk.temporal.policy_effective_at != next_session_evidence_clock(
                attempt.chunk.temporal.observation_date
            ):
                temporal_rebindings += 1

        superseded_attempts = tuple(
            attempt for attempt in attempts if attempt.query_sha256 not in reusable_hashes
        )
        missing = len(set(corrected_by_hash).difference(reusable_hashes))
        consumed_krx = len(attempts)
        projected = consumed_krx + missing
        # 교정 논리 집합은 fresh 유도값과 정확히 같아야 한다. 어긋나면 allowance를 주지 않고 멈춘다.
        if len(reusable_attempts) + missing != corrected_base.budget.krx_get:
            raise LightGbmContractError(
                "calendar recovery logical query identity does not match approved dimensions"
            )
        # Allowance는 증명된 superseded consumed call 수와 정확히 같다. 논리 query는 늘어나지 않는다.
        allowance = (
            len(superseded_attempts)
            if len(superseded_attempts) <= MAX_KRX_SUPERSEDED_ALLOWANCE
            else 0
        )
        approved_krx_max_get = corrected_base.budget.krx_get + allowance
        superseded_queries = _superseded_query_set(
            superseded_attempts,
            _generation_query_identities(prior.window.cutoff, old_by_hash),
        )
        shortfall = max(0, projected - approved_krx_max_get)
        status = "CAPACITY_EXHAUSTED" if shortfall else "READY_TO_SUPERSEDE"
        binding_payload = _recovery_binding_payload(
            prior_packet_sha256=prior_packet_sha256,
            prior_progress_sha256=progress.content_sha256,
            consumed_krx_physical_calls=consumed_krx,
            reusable_successful_chunks=len(reusable_attempts),
            superseded_consumed_calls=len(superseded_attempts),
            missing_required_krx_queries=missing,
            projected_krx_physical_calls=projected,
            approved_krx_max_get=approved_krx_max_get,
            krx_superseded_allowance=allowance,
            superseded_queries=superseded_queries,
        )
        recovery_binding_sha256 = hashlib.sha256(
            canonical_json_bytes(binding_payload)
        ).hexdigest()
        corrected = author_recovery_bootstrap_packet(
            cutoff=prior.window.cutoff,
            recovery_binding_sha256=recovery_binding_sha256,
            superseded_allowance=allowance,
        )
        payload = {
            "recoveryVersion": RECOVERY_VERSION,
            "recoveryBindingSha256": recovery_binding_sha256,
            "status": status,
            "priorPacketSha256": prior_packet_sha256,
            "priorProgressSha256": progress.content_sha256,
            "correctedPacketSha256": corrected.sha256,
            "calendarPolicyVersion": S5_CALENDAR_POLICY_VERSION,
            "calendarCorrectionSetSha256": S5_CALENDAR_CORRECTION_SET_SHA256,
            "correctionSessions": [
                day.isoformat() for day in S5_ADHOC_CLOSED_SESSIONS
            ],
            "failedQuerySha256": failed_query.sha256 if failed_query else "",
            "failedOperationId": failed_query.service if failed_query else "",
            "failedSessionDate": (
                failed_query.session_date.isoformat() if failed_query else ""
            ),
            "supersededQueries": [dict(item) for item in superseded_queries],
            "consumedKrxPhysicalCalls": consumed_krx,
            "reusableSuccessfulChunks": len(reusable_attempts),
            "supersededConsumedCalls": len(superseded_attempts),
            "temporalReceiptsToRebind": temporal_rebindings,
            "missingRequiredKrxQueries": missing,
            "projectedKrxPhysicalCalls": projected,
            "approvedKrxMaxGet": corrected.budget.krx_get,
            "krxSupersededAllowance": corrected.budget.krx_superseded_allowance,
            "krxShortfall": shortfall,
            "providerCallsDuringRecovery": 0,
        }
        content = canonical_json_bytes(payload)
        return BootstrapCalendarRecovery(
            content=content,
            sha256=hashlib.sha256(content).hexdigest(),
            corrected_packet=corrected,
            status=status,
            reusable_chunks=len(reusable_attempts),
            missing_krx_queries=missing,
            projected_krx_physical_calls=projected,
            krx_shortfall=shortfall,
            temporal_rebindings=temporal_rebindings,
            recovery_binding_sha256=recovery_binding_sha256,
            prior_packet_sha256=prior_packet_sha256,
            prior_progress_sha256=progress.content_sha256,
            reusable_attempts=reusable_attempts,
            superseded_attempts=superseded_attempts,
            prior_corrections=tuple(prior_corrections),
        )
    finally:
        release_run_lock(run_lock)


def validate_recovery_receipt(
    content: bytes,
    *,
    corrected_packet: BootstrapPacket,
) -> Mapping[str, object]:
    """Packet에서 독립 파생한 query set과 canonical preimage로 receipt를 검증한다."""

    validated_packet = validate_bootstrap_packet(
        corrected_packet.content,
        expected_sha256=corrected_packet.sha256,
    )
    if validated_packet != corrected_packet:
        raise LightGbmContractError("calendar recovery corrected packet is invalid")

    try:
        value = parse_bounded_json_bytes(
            content,
            limits=BoundedJsonLimits(
                max_bytes=64 * 1024,
                max_depth=4,
                max_list_items=32,
                max_object_keys=32,
                max_text_codepoints=1_024,
                max_text_bytes=4_096,
                max_number_characters=32,
            ),
        )
    except BoundedJsonError as error:
        raise LightGbmContractError("calendar recovery block JSON is invalid") from error
    if (
        not isinstance(value, dict)
        or set(value) != _RECOVERY_FIELDS
        or canonical_json_bytes(value) != content
    ):
        raise LightGbmContractError("calendar recovery block is not closed canonical JSON")
    payload = value
    numeric_fields = (
        "consumedKrxPhysicalCalls",
        "reusableSuccessfulChunks",
        "supersededConsumedCalls",
        "temporalReceiptsToRebind",
        "missingRequiredKrxQueries",
        "projectedKrxPhysicalCalls",
        "approvedKrxMaxGet",
        "krxSupersededAllowance",
        "krxShortfall",
        "providerCallsDuringRecovery",
    )
    if any(
        isinstance(payload[field], bool)
        or not isinstance(payload[field], int)
        or payload[field] < 0
        for field in numeric_fields
    ):
        raise LightGbmContractError("calendar recovery block numeric field is invalid")
    corrections = payload["correctionSessions"]
    expected_queries = _unique_by_hash(_expected_krx_queries(corrected_packet))
    expected_query_count = len(expected_queries)
    if (
        corrected_packet.packet_version != "s5-production-bootstrap-packet-v2"
        or corrected_packet.lineage_mode != "CALENDAR_RECOVERY"
        or corrected_packet.recovery_binding_sha256 is None
        or corrected_packet.calendar_policy_version != S5_CALENDAR_POLICY_VERSION
        or corrected_packet.calendar_correction_set_sha256
        != S5_CALENDAR_CORRECTION_SET_SHA256
        or corrected_packet.budget.krx_get
        != expected_query_count + corrected_packet.budget.krx_superseded_allowance
    ):
        raise LightGbmContractError("calendar recovery corrected packet is invalid")
    if (
        payload["recoveryVersion"] != RECOVERY_VERSION
        or payload["status"] not in {"CAPACITY_EXHAUSTED", "READY_TO_SUPERSEDE"}
        or payload["correctedPacketSha256"] != corrected_packet.sha256
        or payload["recoveryBindingSha256"]
        != corrected_packet.recovery_binding_sha256
        or payload["calendarPolicyVersion"] != S5_CALENDAR_POLICY_VERSION
        or payload["calendarCorrectionSetSha256"]
        != S5_CALENDAR_CORRECTION_SET_SHA256
        or corrections != [day.isoformat() for day in S5_ADHOC_CLOSED_SESSIONS]
        or payload["approvedKrxMaxGet"] != corrected_packet.budget.krx_get
        or payload["reusableSuccessfulChunks"]
        + payload["supersededConsumedCalls"]
        != payload["consumedKrxPhysicalCalls"]
        or payload["reusableSuccessfulChunks"]
        + payload["missingRequiredKrxQueries"]
        != expected_query_count
        or payload["projectedKrxPhysicalCalls"]
        != payload["consumedKrxPhysicalCalls"]
        + payload["missingRequiredKrxQueries"]
        or payload["temporalReceiptsToRebind"]
        > payload["reusableSuccessfulChunks"]
        or payload["providerCallsDuringRecovery"] != 0
        or payload["krxSupersededAllowance"]
        != corrected_packet.budget.krx_superseded_allowance
        or payload["krxSupersededAllowance"]
        not in (0, payload["supersededConsumedCalls"])
    ):
        raise LightGbmContractError("calendar recovery block authority is invalid")
    superseded_queries = payload["supersededQueries"]
    if not isinstance(superseded_queries, list) or not superseded_queries:
        raise LightGbmContractError("calendar recovery superseded query set is invalid")
    seen: set[str] = set()
    for item in superseded_queries:
        if not isinstance(item, dict) or set(item) != {
            "querySha256",
            "operationId",
            "sessionDate",
        }:
            raise LightGbmContractError("calendar recovery superseded query is not closed")
        digest = _sha(str(item["querySha256"]))
        if digest in seen:
            raise LightGbmContractError("calendar recovery superseded query is duplicated")
        seen.add(digest)
        try:
            date.fromisoformat(str(item["sessionDate"]))
        except ValueError:
            raise LightGbmContractError(
                "calendar recovery superseded session is invalid"
            ) from None
    if superseded_queries != sorted(
        superseded_queries, key=lambda item: str(item["querySha256"])
    ):
        raise LightGbmContractError("calendar recovery superseded query order is invalid")
    failed_digest = str(payload["failedQuerySha256"])
    failed_operation = str(payload["failedOperationId"])
    failed_date = str(payload["failedSessionDate"])
    declared_failure = bool(failed_digest or failed_operation or failed_date)
    if declared_failure and not (failed_digest and failed_operation and failed_date):
        raise LightGbmContractError("calendar recovery failed query is partially declared")
    if declared_failure and failed_digest not in seen:
        raise LightGbmContractError("calendar recovery failed query is not superseded")
    if len(seen) > int(payload["supersededConsumedCalls"]):
        raise LightGbmContractError("calendar recovery superseded query count is invalid")
    expected_shortfall = max(
        0,
        payload["projectedKrxPhysicalCalls"] - payload["approvedKrxMaxGet"],
    )
    if payload["krxShortfall"] != expected_shortfall or (
        (payload["status"] == "CAPACITY_EXHAUSTED") != (expected_shortfall > 0)
    ):
        raise LightGbmContractError("calendar recovery status is inconsistent")
    for field in ("priorPacketSha256", "priorProgressSha256"):
        _sha(str(payload[field]))
    if declared_failure:
        _sha(failed_digest)
        try:
            failed_session = date.fromisoformat(failed_date)
        except ValueError:
            raise LightGbmContractError(
                "calendar recovery failed session is invalid"
            ) from None
        if (
            failed_session.isoformat() != failed_date
            or failed_session not in S5_ADHOC_CLOSED_SESSIONS
            or failed_operation not in _DAILY_KRX_SERVICES
            or _krx_query(failed_operation, failed_session).sha256 != failed_digest
            or failed_digest in expected_queries
        ):
            raise LightGbmContractError("calendar recovery failed query is invalid")
    binding_payload = _recovery_binding_payload(
        prior_packet_sha256=str(payload["priorPacketSha256"]),
        prior_progress_sha256=str(payload["priorProgressSha256"]),
        consumed_krx_physical_calls=int(payload["consumedKrxPhysicalCalls"]),
        reusable_successful_chunks=int(payload["reusableSuccessfulChunks"]),
        superseded_consumed_calls=int(payload["supersededConsumedCalls"]),
        missing_required_krx_queries=int(payload["missingRequiredKrxQueries"]),
        projected_krx_physical_calls=int(payload["projectedKrxPhysicalCalls"]),
        approved_krx_max_get=int(payload["approvedKrxMaxGet"]),
        krx_superseded_allowance=int(payload["krxSupersededAllowance"]),
        superseded_queries=tuple(
            cast(Mapping[str, str], item) for item in superseded_queries
        ),
    )
    recomputed_binding = hashlib.sha256(
        canonical_json_bytes(binding_payload)
    ).hexdigest()
    if recomputed_binding != corrected_packet.recovery_binding_sha256:
        raise LightGbmContractError("calendar recovery binding preimage is invalid")
    return payload


def materialize_recovery_adoption(
    *, approved_root: Path, recovery: BootstrapCalendarRecovery
) -> Mapping[str, object]:
    """성공 projection을 새 packet run에 복사하고 calendar clock만 결정적으로 재결속한다.

    Provider 호출은 없으며 prior progress가 assessment 이후 바뀌면 아무 journal도 게시하지 않는다.
    """

    require_private_root(approved_root)
    prior_run_root = approved_root / f"run-{recovery.prior_packet_sha256}"
    prior_source_root = prior_run_root / "source"
    prior_lock = acquire_run_lock(prior_run_root)
    try:
        progress = read_approved_regular_file(
            approved_root=prior_source_root,
            relative_path=JOURNAL_FILENAME,
            max_bytes=MAX_JOURNAL_BYTES,
        )
        if progress.content_sha256 != recovery.prior_progress_sha256:
            raise LightGbmContractError("prior bootstrap progress changed after assessment")
        journal = BootstrapJournal(
            prior_source_root, policy_corrections=recovery.prior_corrections
        )
        if journal.attempts != (
            *recovery.reusable_attempts,
            *recovery.superseded_attempts,
        ):
            # Assessment keeps reusable queries in corrected order, so compare identities as sets below.
            assessed = {
                (item.ordinal, item.query_sha256, item.state) for item in journal.attempts
            }
            expected = {
                (item.ordinal, item.query_sha256, item.state)
                for item in (
                    *recovery.reusable_attempts,
                    *recovery.superseded_attempts,
                )
            }
            if assessed != expected:
                raise LightGbmContractError(
                    "prior bootstrap attempts changed after assessment"
                )

        run_root = approved_root / f"run-{recovery.corrected_packet.sha256}"
        source_root = run_root / "source"
        _ensure_private_directory(run_root)
        _ensure_private_directory(source_root)
        _ensure_private_directory(source_root / "chunks")
        run_lock = acquire_run_lock(run_root)
        try:
            adopted: list[JournalAttempt] = []
            expected_chunk_files: set[str] = set()
            corrected_by_hash = _unique_by_hash(
                _expected_krx_queries(recovery.corrected_packet)
            )
            for ordinal, old_attempt in enumerate(
                recovery.reusable_attempts,
                start=1,
            ):
                query = corrected_by_hash.get(old_attempt.query_sha256)
                if query is None:
                    raise LightGbmContractError(
                        "reusable KRX query is outside corrected packet"
                    )
                _validate_reusable_chunk(
                    source_root=prior_source_root,
                    attempt=old_attempt,
                    query=query,
                )
                old_chunk = old_attempt.chunk
                if old_chunk is None:
                    raise LightGbmContractError("reusable KRX chunk receipt is missing")
                source_file = read_approved_regular_file(
                    approved_root=prior_source_root,
                    relative_path=old_chunk.relative_path,
                    max_bytes=old_chunk.byte_count,
                )
                if source_file.content_sha256 != old_chunk.content_sha256:
                    raise LightGbmContractError("reusable KRX chunk changed during adoption")
                new_clock = next_session_evidence_clock(
                    old_chunk.temporal.observation_date
                )
                new_chunk = replace(
                    old_chunk,
                    temporal=replace(
                        old_chunk.temporal,
                        policy_effective_at=new_clock,
                    ),
                )
                _publish_private_exact(
                    root=source_root,
                    relative_path=new_chunk.relative_path,
                    content=source_file.content,
                    max_bytes=_MAX_KRX_CHUNK_BYTES,
                )
                expected_chunk_files.add(Path(new_chunk.relative_path).name)
                adopted.append(
                    JournalAttempt(
                        ordinal=ordinal,
                        provider=old_attempt.provider,
                        operation_id=old_attempt.operation_id,
                        query_sha256=old_attempt.query_sha256,
                        state="SUCCEEDED",
                        chunk=new_chunk,
                    )
                )
            actual_chunk_files = set(os.listdir(source_root / "chunks"))
            if actual_chunk_files != expected_chunk_files:
                raise LightGbmContractError("recovery chunk directory is not exact")

            journal_content = build_recovery_journal_bytes(
                adopted=adopted,
                superseded=recovery.superseded_attempts,
            )
            _publish_private_exact(
                root=source_root,
                relative_path=JOURNAL_FILENAME,
                content=journal_content,
                max_bytes=MAX_JOURNAL_BYTES,
            )
            receipt_sha256 = hashlib.sha256(recovery.content).hexdigest()
            lineage_payload = {
                "lineageVersion": RECOVERY_LINEAGE_VERSION,
                "correctedPacketSha256": recovery.corrected_packet.sha256,
                "recoveryBindingSha256": recovery.recovery_binding_sha256,
                "recoveryReceiptSha256": receipt_sha256,
                "priorPacketSha256": recovery.prior_packet_sha256,
                "priorProgressSha256": recovery.prior_progress_sha256,
                "adoptedProgressSha256": hashlib.sha256(journal_content).hexdigest(),
                "adoptedSuccessfulChunks": len(adopted),
                "supersededConsumedCalls": len(recovery.superseded_attempts),
                "consumedKrxPhysicalCalls": len(adopted)
                + len(recovery.superseded_attempts),
                "providerCallsDuringAdoption": 0,
            }
            lineage_content = canonical_json_bytes(lineage_payload)
            _publish_private_exact(
                root=source_root,
                relative_path="recovery-lineage.json",
                content=lineage_content,
                max_bytes=64 * 1024,
            )
            return validate_recovery_lineage(
                lineage_content,
                packet=recovery.corrected_packet,
                recovery_receipt_sha256=receipt_sha256,
            )
        finally:
            release_run_lock(run_lock)
    finally:
        release_run_lock(prior_lock)


def validate_recovery_execution_authority(
    *, approved_root: Path, packet: BootstrapPacket
) -> str:
    """Recovery packet이면 receipt·adoption journal이 모두 결속돼야 client 생성을 허용한다."""

    if packet.lineage_mode == "FRESH":
        return "FRESH"
    if (
        packet.lineage_mode != "CALENDAR_RECOVERY"
        or packet.recovery_binding_sha256 is None
    ):
        raise LightGbmContractError("bootstrap recovery lineage is unavailable")
    receipt_file = read_approved_regular_file(
        approved_root=approved_root,
        relative_path=(
            f"calendar-recovery-binding-{packet.recovery_binding_sha256}.json"
        ),
        max_bytes=64 * 1024,
    )
    source_root = approved_root / f"run-{packet.sha256}" / "source"
    require_private_root(source_root)
    lineage_file = read_approved_regular_file(
        approved_root=source_root,
        relative_path="recovery-lineage.json",
        max_bytes=64 * 1024,
    )
    lineage = validate_recovery_lineage(
        lineage_file.content,
        packet=packet,
        recovery_receipt_sha256=receipt_file.content_sha256,
    )
    progress = read_approved_regular_file(
        approved_root=source_root,
        relative_path=JOURNAL_FILENAME,
        max_bytes=MAX_JOURNAL_BYTES,
    )
    if progress.content_sha256 != lineage["adoptedProgressSha256"]:
        raise LightGbmContractError("adopted bootstrap progress digest mismatches lineage")
    journal = BootstrapJournal(source_root)
    adopted = tuple(item for item in journal.attempts if item.state == "SUCCEEDED")
    superseded = tuple(
        item for item in journal.attempts if item.state == SUPERSEDED_CONSUMED
    )
    expected_by_hash = _unique_by_hash(_expected_krx_queries(packet))
    expected_hashes = set(expected_by_hash)
    adopted_hashes = {item.query_sha256 for item in adopted}
    if len(adopted_hashes) != len(adopted):
        raise LightGbmContractError("adopted bootstrap query identity is duplicated")
    receipt = validate_recovery_receipt(
        receipt_file.content,
        corrected_packet=packet,
    )
    superseded_identities = {
        (str(item["querySha256"]), str(item["operationId"]))
        for item in cast(list[Mapping[str, str]], receipt["supersededQueries"])
    }
    if (
        any(item.provider != "KRX" for item in journal.attempts)
        or len(adopted) != receipt["reusableSuccessfulChunks"]
        or len(superseded) != receipt["supersededConsumedCalls"]
        or len(journal.attempts) != receipt["consumedKrxPhysicalCalls"]
        or len(adopted) != lineage["adoptedSuccessfulChunks"]
        or len(superseded) != lineage["supersededConsumedCalls"]
        or adopted_hashes.difference(expected_hashes)
        or len(expected_hashes.difference(adopted_hashes))
        != receipt["missingRequiredKrxQueries"]
        or any(
            (item.query_sha256, item.operation_id) not in superseded_identities
            for item in superseded
        )
        or packet.budget.krx_superseded_allowance != receipt["krxSupersededAllowance"]
        or (
            packet.budget.krx_superseded_allowance
            and packet.budget.krx_superseded_allowance != len(superseded)
        )
    ):
        raise LightGbmContractError("adopted bootstrap journal accounting is invalid")
    for item in adopted:
        _validate_reusable_chunk(
            source_root=source_root,
            attempt=item,
            query=expected_by_hash[item.query_sha256],
        )
    return str(receipt["status"])


def validate_recovery_lineage(
    content: bytes,
    *,
    packet: BootstrapPacket,
    recovery_receipt_sha256: str,
) -> Mapping[str, object]:
    """새 source root의 adoption ledger를 packet과 recovery receipt에 결속한다."""

    try:
        value = parse_bounded_json_bytes(
            content,
            limits=BoundedJsonLimits(
                max_bytes=64 * 1024,
                max_depth=3,
                max_list_items=4,
                max_object_keys=16,
                max_text_codepoints=512,
                max_text_bytes=2_048,
                max_number_characters=32,
            ),
        )
    except BoundedJsonError as error:
        raise LightGbmContractError("calendar recovery lineage JSON is invalid") from error
    if (
        not isinstance(value, dict)
        or set(value) != _LINEAGE_FIELDS
        or canonical_json_bytes(value) != content
    ):
        raise LightGbmContractError("calendar recovery lineage is not closed canonical JSON")
    payload = value
    for field in (
        "correctedPacketSha256",
        "recoveryBindingSha256",
        "recoveryReceiptSha256",
        "priorPacketSha256",
        "priorProgressSha256",
        "adoptedProgressSha256",
    ):
        _sha(str(payload[field]))
    for field in (
        "adoptedSuccessfulChunks",
        "supersededConsumedCalls",
        "consumedKrxPhysicalCalls",
        "providerCallsDuringAdoption",
    ):
        if (
            isinstance(payload[field], bool)
            or not isinstance(payload[field], int)
            or payload[field] < 0
        ):
            raise LightGbmContractError("calendar recovery lineage count is invalid")
    if (
        payload["lineageVersion"] != RECOVERY_LINEAGE_VERSION
        or payload["correctedPacketSha256"] != packet.sha256
        or payload["recoveryBindingSha256"] != packet.recovery_binding_sha256
        or payload["recoveryReceiptSha256"] != _sha(recovery_receipt_sha256)
        or payload["consumedKrxPhysicalCalls"]
        != payload["adoptedSuccessfulChunks"] + payload["supersededConsumedCalls"]
        or payload["providerCallsDuringAdoption"] != 0
    ):
        raise LightGbmContractError("calendar recovery lineage binding is invalid")
    return payload


def _expected_krx_queries(packet: BootstrapPacket) -> tuple[KrxQuery, ...]:
    values: list[KrxQuery] = []
    for session in packet.window.raw_sessions:
        for service in _DAILY_KRX_SERVICES:
            values.append(_krx_query(service, session))
    for schedule in packet.schedules:
        for service in _MONTHLY_KRX_SERVICES:
            values.append(_krx_query(service, schedule.selection_session))
    return tuple(values)


def _krx_query(service: str, session: date) -> KrxQuery:
    digest = provider_query_sha256(
        {"service": service, "basDd": session.strftime("%Y%m%d")}
    )
    return KrxQuery(service, session, digest)


def _unique_by_hash(values: tuple[KrxQuery, ...]) -> Mapping[str, KrxQuery]:
    output: dict[str, KrxQuery] = {}
    for value in values:
        if value.sha256 in output:
            raise LightGbmContractError("KRX logical query digest is duplicated")
        output[value.sha256] = value
    return output


def _validate_reusable_chunk(
    *, source_root: Path, attempt: JournalAttempt, query: KrxQuery
) -> None:
    chunk = attempt.chunk
    expected_key = f"{query.service}:{query.session_date.isoformat()}"
    if (
        attempt.provider != "KRX"
        or attempt.operation_id != query.service
        or attempt.query_sha256 != query.sha256
        or attempt.state != "SUCCEEDED"
        or chunk is None
        or chunk.source_id != "KRX"
        or chunk.operation_id != query.service
        or chunk.query_key != expected_key
        or chunk.temporal.source_id != "KRX"
        or chunk.temporal.operation_id != query.service
        or chunk.temporal.observation_date != query.session_date
        or chunk.temporal.request_sha256 != query.sha256
        or chunk.temporal.snapshot_sha256 != chunk.content_sha256
        or chunk.row_count <= 0
        or chunk.byte_count <= 0
        or chunk.byte_count > _MAX_KRX_CHUNK_BYTES
    ):
        raise LightGbmContractError("reusable KRX chunk receipt is invalid")
    try:
        payload = read_approved_regular_file(
            approved_root=source_root,
            relative_path=chunk.relative_path,
            max_bytes=chunk.byte_count,
        )
    except RagSafeIoError as error:
        raise LightGbmContractError("reusable KRX chunk file is invalid") from error
    if (
        len(payload.content) != chunk.byte_count
        or payload.content_sha256 != chunk.content_sha256
    ):
        raise LightGbmContractError("reusable KRX chunk digest is invalid")
    try:
        parquet = pq.ParquetFile(  # type: ignore[no-untyped-call]
            BytesIO(payload.content),
            thrift_string_size_limit=1 * 1024 * 1024,
            thrift_container_size_limit=10_000_000,
            page_checksum_verification=True,
        )
        metadata = parquet.metadata
        expected_fields = tuple(sorted(S5_PRODUCTION_PROJECTION_FIELDS[query.service]))
        if (
            metadata.num_rows != chunk.row_count
            or metadata.num_columns != len(expected_fields)
            or tuple(parquet.schema_arrow.names) != expected_fields
            or parquet.schema_arrow.metadata
        ):
            raise LightGbmContractError(
                "reusable KRX Parquet footer or schema is invalid"
            )
        if any(
            field.type != pa.string() or field.nullable
            for field in parquet.schema_arrow
        ):
            raise LightGbmContractError(
                "reusable KRX Parquet fields must be non-null strings"
            )
        rows = 0
        expected_provider_date = query.session_date.strftime("%Y%m%d")
        for batch in parquet.iter_batches(batch_size=8_192, use_threads=False):  # type: ignore[no-untyped-call]
            rows += batch.num_rows
            if "BAS_DD" in expected_fields:
                date_values = batch.column(expected_fields.index("BAS_DD")).to_pylist()
                if any(value != expected_provider_date for value in date_values):
                    raise LightGbmContractError(
                        "reusable KRX Parquet row date mismatches query"
                    )
        if rows != chunk.row_count:
            raise LightGbmContractError(
                "reusable KRX Parquet actual row count is invalid"
            )
    except LightGbmContractError:
        raise
    except Exception as error:
        raise LightGbmContractError(
            "reusable KRX chunk is not valid bounded Parquet"
        ) from error


def _generation_query_identities(
    cutoff: datetime, prior_by_hash: Mapping[str, KrxQuery]
) -> Mapping[str, KrxQuery]:
    """승인된 correction 세대 전체의 KRX logical query 신원을 합친다.

    긴 체인에서는 superseded 항목이 직전 세대보다 앞선 세대의 query일 수 있다. 승인된 세대만
    사용하므로 계약 밖 날짜는 신원을 얻지 못한다.
    """

    identities: dict[str, KrxQuery] = dict(prior_by_hash)
    for corrections in (S5_ADHOC_CLOSED_SESSIONS, *S5_SUPERSEDED_CORRECTION_SETS):
        generation = _author_bootstrap_packet(
            cutoff=cutoff,
            calendar=calendar_for_corrections(tuple(corrections)),
            packet_version="s5-production-bootstrap-packet-v2",
            lineage_mode="FRESH",
            corrections=tuple(corrections),
        )
        for query in _expected_krx_queries(generation):
            identities.setdefault(query.sha256, query)
    return identities


def _superseded_query_set(
    attempts: tuple[JournalAttempt, ...], by_hash: Mapping[str, KrxQuery]
) -> tuple[Mapping[str, str], ...]:
    """superseded 물리 시도를 논리 query 신원 집합으로 접는다. 같은 query의 재시도는 하나로 본다."""

    identities: dict[str, Mapping[str, str]] = {}
    for attempt in attempts:
        query = by_hash.get(attempt.query_sha256)
        if query is None:
            raise LightGbmContractError("superseded attempt has no prior query identity")
        identities[query.sha256] = {
            "operationId": query.service,
            "querySha256": query.sha256,
            "sessionDate": query.session_date.isoformat(),
        }
    return tuple(identities[key] for key in sorted(identities))


def _recovery_binding_payload(
    *,
    prior_packet_sha256: str,
    prior_progress_sha256: str,
    consumed_krx_physical_calls: int,
    reusable_successful_chunks: int,
    superseded_consumed_calls: int,
    missing_required_krx_queries: int,
    projected_krx_physical_calls: int,
    approved_krx_max_get: int,
    krx_superseded_allowance: int,
    superseded_queries: tuple[Mapping[str, str], ...],
) -> dict[str, object]:
    """Authoring과 검증이 공유하는 exact canonical binding preimage다."""

    return {
        "bindingVersion": RECOVERY_BINDING_VERSION,
        "priorPacketSha256": prior_packet_sha256,
        "priorProgressSha256": prior_progress_sha256,
        "calendarPolicyVersion": S5_CALENDAR_POLICY_VERSION,
        "calendarCorrectionSetSha256": S5_CALENDAR_CORRECTION_SET_SHA256,
        "consumedKrxPhysicalCalls": consumed_krx_physical_calls,
        "reusableSuccessfulChunks": reusable_successful_chunks,
        "supersededConsumedCalls": superseded_consumed_calls,
        "missingRequiredKrxQueries": missing_required_krx_queries,
        "projectedKrxPhysicalCalls": projected_krx_physical_calls,
        "approvedKrxMaxGet": approved_krx_max_get,
        "krxSupersededAllowance": krx_superseded_allowance,
        "supersededQueries": [dict(item) for item in superseded_queries],
    }


def _ensure_private_directory(path: Path) -> None:
    if os.path.lexists(path):
        metadata = path.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise LightGbmContractError("calendar recovery directory is not owner-private")
        return
    parent = path.parent
    require_private_root(parent)
    path.mkdir(mode=0o700)
    require_private_root(path)


def _publish_private_exact(
    *, root: Path, relative_path: str, content: bytes, max_bytes: int
) -> None:
    try:
        result = write_approved_new_file(
            approved_root=root,
            relative_path=relative_path,
            content=content,
            max_bytes=max_bytes,
        )
        os.chmod(result.absolute_path, 0o600, follow_symlinks=False)
    except RagSafeIoError as error:
        existing = read_approved_regular_file(
            approved_root=root,
            relative_path=relative_path,
            max_bytes=max_bytes,
        )
        if existing.content != content:
            raise LightGbmContractError("calendar recovery artifact conflicts") from error


def _sha(value: str) -> str:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise LightGbmContractError("calendar recovery SHA-256 is invalid")
    return value
