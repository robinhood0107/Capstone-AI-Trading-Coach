"""Pre-S5 KIS quote, Window B, release receipt의 content-free local gate다."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import stat
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urlsplit
from uuid import uuid4

import psycopg

from app.data.kis.accounting import (
    CollectionRunRecorder,
    CollectionRunStatus,
    LogicalOperation,
    PhysicalChannel,
)
from app.data.kis.http_client import KISHttpClient
from app.data.kis.market_client import KISMarketClient
from app.data.kis.settings import KISSettings

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_OBJECT = re.compile(r"^[0-9a-f]{40,64}$")
_WINDOW_B_FIELDS = {
    "binding",
    "expiresAt",
    "issuedAt",
    "kisMockPacketSha256",
    "kisQuoteReceiptSha256",
    "operation",
    "physicalCaps",
    "rawArtifactCount",
    "retryCount",
    "schemaVersion",
    "vertexPacketSha256",
    "voyageQueryPacketSha256",
}
_KIS_QUOTE_FIELDS = {
    "binding",
    "expiresAt",
    "issuedAt",
    "operation",
    "physicalCaps",
    "rawArtifactCount",
    "retryCount",
    "schemaVersion",
    "symbol",
}
_KIS_QUOTE_RECEIPT_FIELDS = {
    "brokeragePhysicalCalls",
    "limitPrice",
    "manifestSha256",
    "observedAt",
    "previousClose",
    "quoteProjectionSha256",
    "rawArtifactCount",
    "retryCount",
    "schemaVersion",
    "symbol",
    "tokenPhysicalCalls",
}
_RELEASE_RECEIPT_NAMES = (
    "ownerBgeLocal",
    "kisMockV3",
    "requiredCi",
    "securityScan",
    "trackedAudit",
)
_RELEASE_S48_STATES = (
    ("S48_CORE6_ECOS", "ABSTAIN"),
    ("S48_CORE6_KIS", "AVAILABLE"),
    ("S48_CORE6_KOFIA", "BLOCKED"),
    ("S48_CORE6_KRX", "ABSTAIN"),
    ("S48_CORE6_OPENDART", "ABSTAIN"),
    ("S48_CORE6_SEC_EDGAR", "ABSTAIN"),
    ("S48_OPTIONAL3_FINNHUB", "BLOCKED"),
    ("S48_OPTIONAL3_MASSIVE", "BLOCKED"),
    ("S48_OPTIONAL3_TWELVE_DATA", "BLOCKED"),
)


class FinalGateError(ValueError):
    """final provider manifest 또는 release receipt가 fail-closed 했음을 나타낸다."""


@dataclass(frozen=True, slots=True)
class WindowBManifest:
    """세 runtime이 socket 전에 공유해야 하는 exact child packet projection이다."""

    manifest_sha256: str
    voyage_query_packet_sha256: str
    vertex_packet_sha256: str
    kis_mock_packet_sha256: str
    kis_quote_receipt_sha256: str
    head_commit: str
    tree_object: str
    ci_digest: str
    security_digest: str


@dataclass(frozen=True, slots=True)
class KisQuoteManifest:
    """005930 current-price 한 번만 허용하는 exact quote packet이다."""

    manifest_sha256: str
    head_commit: str
    tree_object: str
    ci_digest: str
    security_digest: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class KisQuoteReceipt:
    """raw KIS body 없이 Window B 지정가 계산에 필요한 최소 scalar receipt다."""

    receipt_sha256: str
    manifest_sha256: str
    quote_projection_sha256: str
    previous_close: int
    limit_price: int
    token_physical_calls: int
    brokerage_physical_calls: int


@dataclass(frozen=True, slots=True)
class ReleaseDatabaseSnapshot:
    """release validator가 raw row 없이 비교하는 fresh DB의 content-free aggregate다."""

    latest_migration: int
    public_state: str
    public_embedding_profile_id: str
    public_source_count: int
    public_chunk_count: int
    committed_document_batch_count: int
    public_evaluation_count: int
    public_evaluation_minimum: float
    public_evaluation_leak_count: int
    owner_source_count: int
    owner_chunk_count: int
    owner_embedding_count: int
    owner_profile_lock_count: int
    owner_voyage_committed_document_count: int
    owner_voyage_committed_chunk_count: int
    s48_states: tuple[tuple[str, str], ...]
    voyage_query_committed_packet_sha256: str | None
    vertex_committed_packet_sha256: str | None


def require_window_b_child(
    manifest: WindowBManifest,
    *,
    runtime: str,
    packet_sha256: str,
) -> None:
    """각 provider runtime 직전에 parent manifest의 exact child digest를 재검증한다."""

    expected = {
        "VOYAGE_QUERY": manifest.voyage_query_packet_sha256,
        "VERTEX": manifest.vertex_packet_sha256,
        "KIS_MOCK": manifest.kis_mock_packet_sha256,
    }
    if runtime not in expected or _SHA256.fullmatch(packet_sha256) is None:
        raise FinalGateError("PRE_S5_WINDOW_B_CHILD_BINDING")
    if expected[runtime] != packet_sha256:
        raise FinalGateError("PRE_S5_WINDOW_B_CHILD_BINDING")


def derive_kis_mock_limit_price(current_price: int, previous_close: int) -> int:
    """전일 종가 70%를 KRX tick에 올림하고 한 tick을 더해 비체결 지정가를 만든다."""

    if type(current_price) is not int or type(previous_close) is not int:
        raise FinalGateError("PRE_S5_KIS_MOCK_LIMIT_PRICE_INVALID")
    if current_price <= 0 or previous_close <= 0:
        raise FinalGateError("PRE_S5_KIS_MOCK_LIMIT_PRICE_INVALID")
    numerator = previous_close * 7
    raw_floor = numerator // 10
    tick = _krx_tick(raw_floor)
    rounded = ((numerator + 10 * tick - 1) // (10 * tick)) * tick
    # 경계값을 넘으면 새 가격대 tick으로 다시 올림한다.
    final_tick = _krx_tick(rounded)
    rounded = ((numerator + 10 * final_tick - 1) // (10 * final_tick)) * final_tick
    limit_price = rounded + _krx_tick(rounded)
    if limit_price <= 0 or limit_price >= current_price:
        raise FinalGateError("PRE_S5_KIS_MOCK_LIMIT_PRICE_INVALID")
    return limit_price


def author_kis_quote_manifest(
    *,
    output_path: Path,
    head_commit: str,
    tree_object: str,
    ci_digest: str,
    security_digest: str,
) -> str:
    """KIS_MOCK tokenP 최대 1회와 005930 current-price 1회만 승인한다."""

    if (
        not isinstance(output_path, Path)
        or not output_path.is_absolute()
        or _GIT_OBJECT.fullmatch(head_commit) is None
        or _GIT_OBJECT.fullmatch(tree_object) is None
        or _SHA256.fullmatch(ci_digest) is None
        or _SHA256.fullmatch(security_digest) is None
    ):
        raise FinalGateError("PRE_S5_KIS_QUOTE_MANIFEST_INVALID")
    issued_at = datetime.now(UTC)
    payload: dict[str, object] = {
        "binding": {
            "ciDigest": ci_digest,
            "headCommit": head_commit,
            "securityDigest": security_digest,
            "treeObject": tree_object,
        },
        "expiresAt": _format_instant(issued_at + timedelta(minutes=5)),
        "issuedAt": _format_instant(issued_at),
        "operation": "KIS_MOCK_CURRENT_PRICE",
        "physicalCaps": {"brokerage": 1, "tokenP": 1},
        "rawArtifactCount": 0,
        "retryCount": 0,
        "schemaVersion": "pre-s5-kis-mock-quote-manifest/v1",
        "symbol": "005930",
    }
    content = _canonical_json(payload)
    _write_private_json(output_path, content)
    return hashlib.sha256(content).hexdigest()


def load_kis_quote_manifest(path: Path, *, now: datetime | None = None) -> KisQuoteManifest:
    """exact user approval과 5-minute TTL을 current execution binding보다 먼저 검증한다."""

    raw = _read_private_json(path, max_bytes=64 * 1024)
    digest = hashlib.sha256(raw).hexdigest()
    if os.environ.get("PRE_S5_KIS_MOCK_QUOTE_MANIFEST_SHA256", "").strip() != digest:
        raise FinalGateError("PRE_S5_KIS_QUOTE_MANIFEST_APPROVAL")
    value = _decode_json(raw, code="PRE_S5_KIS_QUOTE_MANIFEST_INVALID")
    if not isinstance(value, dict) or set(value) != _KIS_QUOTE_FIELDS:
        raise FinalGateError("PRE_S5_KIS_QUOTE_MANIFEST_INVALID")
    binding = value.get("binding")
    try:
        issued_at = _parse_instant(value.get("issuedAt"))
        expires_at = _parse_instant(value.get("expiresAt"))
    except ValueError as error:
        raise FinalGateError("PRE_S5_KIS_QUOTE_MANIFEST_INVALID") from error
    current = (now or datetime.now(UTC)).astimezone(UTC)
    if (
        value.get("schemaVersion") != "pre-s5-kis-mock-quote-manifest/v1"
        or value.get("operation") != "KIS_MOCK_CURRENT_PRICE"
        or value.get("symbol") != "005930"
        or value.get("physicalCaps") != {"brokerage": 1, "tokenP": 1}
        or value.get("retryCount") != 0
        or value.get("rawArtifactCount") != 0
        or expires_at <= issued_at
        or expires_at - issued_at > timedelta(minutes=5)
        or current < issued_at
        or current >= expires_at
        or not isinstance(binding, dict)
        or set(binding) != {"ciDigest", "headCommit", "securityDigest", "treeObject"}
    ):
        raise FinalGateError("PRE_S5_KIS_QUOTE_MANIFEST_INVALID")
    head = binding.get("headCommit")
    tree = binding.get("treeObject")
    ci = binding.get("ciDigest")
    security = binding.get("securityDigest")
    if (
        not isinstance(head, str)
        or _GIT_OBJECT.fullmatch(head) is None
        or not isinstance(tree, str)
        or _GIT_OBJECT.fullmatch(tree) is None
        or not isinstance(ci, str)
        or _SHA256.fullmatch(ci) is None
        or not isinstance(security, str)
        or _SHA256.fullmatch(security) is None
    ):
        raise FinalGateError("PRE_S5_KIS_QUOTE_MANIFEST_INVALID")
    return KisQuoteManifest(digest, head, tree, ci, security, expires_at)


def write_kis_quote_receipt(
    *,
    output_path: Path,
    manifest_sha256: str,
    quote_projection_sha256: str,
    current_price: int,
    previous_diff: int,
    token_physical_calls: int,
    brokerage_physical_calls: int,
) -> str:
    """normalized KIS scalar에서 previous close와 bound limit만 durable local receipt에 남긴다."""

    if (
        _SHA256.fullmatch(manifest_sha256) is None
        or _SHA256.fullmatch(quote_projection_sha256) is None
        or type(previous_diff) is not int
        or token_physical_calls not in {0, 1}
        or brokerage_physical_calls != 1
    ):
        raise FinalGateError("PRE_S5_KIS_QUOTE_RECEIPT_INVALID")
    previous_close = current_price - previous_diff
    limit_price = derive_kis_mock_limit_price(current_price, previous_close)
    payload: dict[str, object] = {
        "brokeragePhysicalCalls": brokerage_physical_calls,
        "limitPrice": limit_price,
        "manifestSha256": manifest_sha256,
        "observedAt": _format_instant(datetime.now(UTC)),
        "previousClose": previous_close,
        "quoteProjectionSha256": quote_projection_sha256,
        "rawArtifactCount": 0,
        "retryCount": 0,
        "schemaVersion": "pre-s5-kis-mock-quote-receipt/v1",
        "symbol": "005930",
        "tokenPhysicalCalls": token_physical_calls,
    }
    content = _canonical_json(payload)
    _write_private_json(output_path, content)
    return hashlib.sha256(content).hexdigest()


def load_kis_quote_receipt(path: Path) -> KisQuoteReceipt:
    """Window B author가 exact derived price와 physical caps만 읽도록 receipt를 재검증한다."""

    raw = _read_private_json(path, max_bytes=64 * 1024)
    value = _decode_json(raw, code="PRE_S5_KIS_QUOTE_RECEIPT_INVALID")
    if not isinstance(value, dict) or set(value) != _KIS_QUOTE_RECEIPT_FIELDS:
        raise FinalGateError("PRE_S5_KIS_QUOTE_RECEIPT_INVALID")
    manifest_sha = value.get("manifestSha256")
    projection_sha = value.get("quoteProjectionSha256")
    previous_close = value.get("previousClose")
    limit_price = value.get("limitPrice")
    token_calls = value.get("tokenPhysicalCalls")
    brokerage_calls = value.get("brokeragePhysicalCalls")
    if (
        value.get("schemaVersion") != "pre-s5-kis-mock-quote-receipt/v1"
        or value.get("symbol") != "005930"
        or value.get("retryCount") != 0
        or value.get("rawArtifactCount") != 0
        or not isinstance(manifest_sha, str)
        or _SHA256.fullmatch(manifest_sha) is None
        or not isinstance(projection_sha, str)
        or _SHA256.fullmatch(projection_sha) is None
        or type(previous_close) is not int
        or previous_close <= 0
        or type(limit_price) is not int
        or limit_price <= 0
        or token_calls not in {0, 1}
        or brokerage_calls != 1
    ):
        raise FinalGateError("PRE_S5_KIS_QUOTE_RECEIPT_INVALID")
    return KisQuoteReceipt(
        hashlib.sha256(raw).hexdigest(),
        manifest_sha,
        projection_sha,
        previous_close,
        limit_price,
        token_calls,
        brokerage_calls,
    )


def author_window_b_manifest(
    *,
    output_path: Path,
    head_commit: str,
    tree_object: str,
    ci_digest: str,
    security_digest: str,
    voyage_query_packet_sha256: str,
    vertex_packet_sha256: str,
    kis_mock_packet_sha256: str,
    kis_quote_receipt_sha256: str,
) -> str:
    """Voyage query, Vertex JSON OAuth, KIS V3를 한 exact approval에 묶는다."""

    if (
        not isinstance(output_path, Path)
        or not output_path.is_absolute()
        or _GIT_OBJECT.fullmatch(head_commit) is None
        or _GIT_OBJECT.fullmatch(tree_object) is None
        or any(
            _SHA256.fullmatch(value) is None
            for value in (
                ci_digest,
                security_digest,
                voyage_query_packet_sha256,
                vertex_packet_sha256,
                kis_mock_packet_sha256,
                kis_quote_receipt_sha256,
            )
        )
    ):
        raise FinalGateError("PRE_S5_WINDOW_B_MANIFEST_INVALID")
    issued_at = datetime.now(UTC)
    payload: dict[str, object] = {
        "binding": {
            "ciDigest": ci_digest,
            "headCommit": head_commit,
            "securityDigest": security_digest,
            "treeObject": tree_object,
        },
        "expiresAt": _format_instant(issued_at + timedelta(hours=2)),
        "issuedAt": _format_instant(issued_at),
        "kisMockPacketSha256": kis_mock_packet_sha256,
        "kisQuoteReceiptSha256": kis_quote_receipt_sha256,
        "operation": "PRE_S5_WINDOW_B_FINAL",
        "physicalCaps": {
            "kisMockBrokerage": 7,
            "kisMockTokenP": 1,
            "vertexGenerateContent": 1,
            "vertexToken": 1,
            "voyageQuery": 1,
        },
        "rawArtifactCount": 0,
        "retryCount": 0,
        "schemaVersion": "pre-s5-window-b-final-manifest/v1",
        "vertexPacketSha256": vertex_packet_sha256,
        "voyageQueryPacketSha256": voyage_query_packet_sha256,
    }
    content = _canonical_json(payload)
    _write_private_json(output_path, content)
    return hashlib.sha256(content).hexdigest()


def load_window_b_manifest(path: Path) -> WindowBManifest:
    """fixed exact approval hash와 0600 regular-file boundary를 provider socket 전에 검증한다."""

    raw = _read_private_json(path, max_bytes=64 * 1024)
    digest = hashlib.sha256(raw).hexdigest()
    if os.environ.get("PRE_S5_WINDOW_B_FINAL_MANIFEST_SHA256", "").strip() != digest:
        raise FinalGateError("PRE_S5_WINDOW_B_MANIFEST_APPROVAL")
    try:
        value = json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
        raise FinalGateError("PRE_S5_WINDOW_B_MANIFEST_INVALID") from error
    if not isinstance(value, dict) or set(value) != _WINDOW_B_FIELDS:
        raise FinalGateError("PRE_S5_WINDOW_B_MANIFEST_INVALID")
    binding = value.get("binding")
    physical_caps = value.get("physicalCaps")
    try:
        issued_at = _parse_instant(value.get("issuedAt"))
        expires_at = _parse_instant(value.get("expiresAt"))
    except (TypeError, ValueError) as error:
        raise FinalGateError("PRE_S5_WINDOW_B_MANIFEST_INVALID") from error
    if (
        value.get("schemaVersion") != "pre-s5-window-b-final-manifest/v1"
        or value.get("operation") != "PRE_S5_WINDOW_B_FINAL"
        or value.get("retryCount") != 0
        or value.get("rawArtifactCount") != 0
        or physical_caps
        != {
            "kisMockBrokerage": 7,
            "kisMockTokenP": 1,
            "vertexGenerateContent": 1,
            "vertexToken": 1,
            "voyageQuery": 1,
        }
        or not isinstance(binding, dict)
        or set(binding) != {"ciDigest", "headCommit", "securityDigest", "treeObject"}
        or expires_at <= issued_at
        or expires_at - issued_at > timedelta(hours=2)
        or datetime.now(UTC) >= expires_at
    ):
        raise FinalGateError("PRE_S5_WINDOW_B_MANIFEST_INVALID")
    values = (
        value.get("voyageQueryPacketSha256"),
        value.get("vertexPacketSha256"),
        value.get("kisMockPacketSha256"),
        value.get("kisQuoteReceiptSha256"),
        binding.get("ciDigest"),
        binding.get("securityDigest"),
    )
    if any(not isinstance(item, str) or _SHA256.fullmatch(item) is None for item in values):
        raise FinalGateError("PRE_S5_WINDOW_B_MANIFEST_INVALID")
    head_commit = binding.get("headCommit")
    tree_object = binding.get("treeObject")
    if (
        not isinstance(head_commit, str)
        or _GIT_OBJECT.fullmatch(head_commit) is None
        or not isinstance(tree_object, str)
        or _GIT_OBJECT.fullmatch(tree_object) is None
    ):
        raise FinalGateError("PRE_S5_WINDOW_B_MANIFEST_INVALID")
    return WindowBManifest(
        manifest_sha256=digest,
        voyage_query_packet_sha256=str(value["voyageQueryPacketSha256"]),
        vertex_packet_sha256=str(value["vertexPacketSha256"]),
        kis_mock_packet_sha256=str(value["kisMockPacketSha256"]),
        kis_quote_receipt_sha256=str(value["kisQuoteReceiptSha256"]),
        head_commit=head_commit,
        tree_object=tree_object,
        ci_digest=str(binding["ciDigest"]),
        security_digest=str(binding["securityDigest"]),
    )


def main(argv: Sequence[str] | None = None) -> int:
    """final gate는 명시된 다섯 local operator command 외의 입력을 받지 않는다."""

    parser = argparse.ArgumentParser(prog="pre-s5-final-gate")
    parser.add_argument(
        "command",
        choices=(
            "author-kis-quote",
            "execute-kis-quote",
            "author-window-b",
            "verify-release",
        ),
    )
    arguments = parser.parse_args(tuple(sys.argv[1:] if argv is None else argv))
    try:
        local_root = _environment_root()
        binding = _environment_binding()
        if arguments.command == "author-kis-quote":
            digest = author_kis_quote_manifest(
                output_path=local_root / "control/kis-mock-quote-manifest.v1.json",
                head_commit=binding[0],
                tree_object=binding[1],
                ci_digest=binding[2],
                security_digest=binding[3],
            )
            _emit({"manifestSha256": digest, "providerCalls": 0, "state": "AUTHORED"})
            return 0
        if arguments.command == "execute-kis-quote":
            return _execute_kis_quote(local_root=local_root, binding=binding)
        if arguments.command == "author-window-b":
            quote_receipt = load_kis_quote_receipt(
                local_root / "control/kis-mock-quote-receipt.v1.json"
            )
            digest = author_window_b_manifest(
                output_path=local_root / "control/window-b-final-manifest.v1.json",
                head_commit=binding[0],
                tree_object=binding[1],
                ci_digest=binding[2],
                security_digest=binding[3],
                voyage_query_packet_sha256=_required_hash_environment(
                    "PRE_S5_VOYAGE_QUERY_PACKET_SHA256"
                ),
                vertex_packet_sha256=_required_hash_environment("PRE_S5_VERTEX_PACKET_SHA256"),
                kis_mock_packet_sha256=_required_hash_environment("PRE_S5_KIS_MOCK_PACKET_SHA256"),
                kis_quote_receipt_sha256=quote_receipt.receipt_sha256,
            )
            _emit({"manifestSha256": digest, "providerCalls": 0, "state": "AUTHORED"})
            return 0
        return _verify_release(local_root=local_root, binding=binding)
    except Exception as error:
        code = str(error)
        if not code or len(code) > 160 or re.fullmatch(r"[A-Z0-9_]+", code) is None:
            code = "PRE_S5_FINAL_GATE_FAILED"
        _emit({"code": code, "providerCalls": 0, "state": "FAILED"})
        return 2


def _execute_kis_quote(
    *,
    local_root: Path,
    binding: tuple[str, str, str, str],
) -> int:
    """approved quote packet에서 tokenP 최대 1회와 read-only current-price 1회만 실행한다."""

    manifest = load_kis_quote_manifest(local_root / "control/kis-mock-quote-manifest.v1.json")
    if (manifest.head_commit, manifest.tree_object, manifest.ci_digest, manifest.security_digest) != binding:
        raise FinalGateError("PRE_S5_KIS_QUOTE_MANIFEST_BINDING")
    settings = KISSettings(kis_retry_attempts=1)
    if settings.mode != "mock" or settings.offline:
        raise FinalGateError("PRE_S5_KIS_MOCK_QUOTE_CONFIGURATION")
    recorder = CollectionRunRecorder(
        run_id=uuid4(),
        started_at=datetime.now(UTC),
        logical_caps={
            LogicalOperation.CURRENT_PRICE: 1,
            LogicalOperation.DAILY_BARS: 0,
            LogicalOperation.HOLIDAY: 0,
        },
        physical_caps={PhysicalChannel.MARKET_DATA: 1, PhysicalChannel.TOKEN_P: 1},
    )
    client = KISHttpClient(settings, accounting=recorder)
    try:
        current = KISMarketClient(settings, client, accounting=recorder).current_price("005930")
    finally:
        client.close()
    summary = recorder.snapshot(completed_at=datetime.now(UTC), status=CollectionRunStatus.SUCCESS)
    counts = {item.channel: item.attempts for item in summary.physical_attempts}
    token_calls = counts.get(PhysicalChannel.TOKEN_P, 0)
    market_calls = counts.get(PhysicalChannel.MARKET_DATA, 0)
    projection = {
        "high": current.high,
        "low": current.low,
        "open": current.open,
        "previousDiff": current.previous_diff,
        "previousRate": str(current.previous_rate),
        "price": current.price,
        "symbol": current.symbol,
        "turnover": current.turnover,
        "volume": current.volume,
    }
    receipt_sha = write_kis_quote_receipt(
        output_path=local_root / "control/kis-mock-quote-receipt.v1.json",
        manifest_sha256=manifest.manifest_sha256,
        quote_projection_sha256=hashlib.sha256(_canonical_json(projection)).hexdigest(),
        current_price=current.price,
        previous_diff=current.previous_diff,
        token_physical_calls=token_calls,
        brokerage_physical_calls=market_calls,
    )
    receipt = load_kis_quote_receipt(local_root / "control/kis-mock-quote-receipt.v1.json")
    _emit(
        {
            "brokeragePhysicalCalls": receipt.brokerage_physical_calls,
            "limitPrice": receipt.limit_price,
            "receiptSha256": receipt_sha,
            "state": "COMMITTED",
            "tokenPhysicalCalls": receipt.token_physical_calls,
        }
    )
    return 0


def _verify_release(
    *,
    local_root: Path,
    binding: tuple[str, str, str, str],
) -> int:
    """ignored ledger, physical receipts, fresh DB가 모두 일치할 때만 OPEN을 산출한다."""

    ledger_path = local_root / "evidence/pre-s5-release-ledger.v1.json"
    raw = _read_private_json(ledger_path, max_bytes=128 * 1024)
    value = _decode_json(raw, code="PRE_S5_RELEASE_LEDGER_INVALID")
    if not isinstance(value, dict):
        raise FinalGateError("PRE_S5_RELEASE_LEDGER_INVALID")
    window_b = value.get("windowB")
    if not isinstance(window_b, dict):
        raise FinalGateError("PRE_S5_RELEASE_LEDGER_INVALID")
    voyage_packet = window_b.get("voyageQueryPacketSha256")
    vertex_packet = window_b.get("vertexPacketSha256")
    if not isinstance(voyage_packet, str) or not isinstance(vertex_packet, str):
        raise FinalGateError("PRE_S5_RELEASE_LEDGER_INVALID")
    snapshot = _load_release_database_snapshot(
        database_dsn=_release_database_dsn(),
        voyage_query_packet_sha256=voyage_packet,
        vertex_packet_sha256=vertex_packet,
    )
    markers = verify_release_ledger(
        local_root=local_root,
        binding=binding,
        ledger=value,
        database_snapshot=snapshot,
    )
    _emit({"markers": markers, "state": "OPEN"})
    return 0


def verify_release_ledger(
    *,
    local_root: Path,
    binding: tuple[str, str, str, str],
    ledger: Mapping[str, object],
    database_snapshot: ReleaseDatabaseSnapshot,
) -> dict[str, object]:
    """self-asserted marker를 실제 receipt hash와 DB aggregate에 결박한다.

    입력은 ignored 0600 JSON과 content-free DB aggregate뿐이며 provider payload, 문서 text,
    vector, credential은 읽거나 반환하지 않는다.
    """

    expected_binding = {
        "ciDigest": binding[2],
        "headCommit": binding[0],
        "securityDigest": binding[3],
        "treeObject": binding[1],
    }
    if set(ledger) != {"binding", "markers", "receipts", "schemaVersion", "windowB"}:
        raise FinalGateError("PRE_S5_RELEASE_LEDGER_INVALID")
    if ledger.get("schemaVersion") != "pre-s5-release-ledger/v2":
        raise FinalGateError("PRE_S5_RELEASE_LEDGER_INVALID")
    if ledger.get("binding") != expected_binding:
        raise FinalGateError("PRE_S5_RELEASE_LEDGER_BINDING")
    window_b = ledger.get("windowB")
    if not isinstance(window_b, dict) or set(window_b) != {
        "kisMockPacketSha256",
        "manifestSha256",
        "vertexPacketSha256",
        "voyageQueryPacketSha256",
    }:
        raise FinalGateError("PRE_S5_RELEASE_LEDGER_INVALID")
    if any(not isinstance(value, str) or _SHA256.fullmatch(value) is None for value in window_b.values()):
        raise FinalGateError("PRE_S5_RELEASE_LEDGER_INVALID")
    receipts = ledger.get("receipts")
    if not isinstance(receipts, dict) or set(receipts) != set(_RELEASE_RECEIPT_NAMES):
        raise FinalGateError("PRE_S5_RELEASE_LEDGER_INVALID")
    for name in _RELEASE_RECEIPT_NAMES:
        _verify_release_receipt(
            local_root=local_root,
            name=name,
            reference=receipts[name],
            expected_binding=expected_binding,
        )
    _verify_release_database_snapshot(
        database_snapshot,
        voyage_query_packet_sha256=str(window_b["voyageQueryPacketSha256"]),
        vertex_packet_sha256=str(window_b["vertexPacketSha256"]),
    )
    markers = ledger.get("markers")
    required = {
        "BGE_PUBLIC_EMBEDDING_INFERENCE_CALLS": 0,
        "BGE_OWNER_EMBEDDING_INFERENCE": "USER_SELECTED_ONLY",
        "FINAL_SECURITY_COVERAGE_COMPLETE_FINDINGS": 0,
        "FOREIGN_NEWS_MODEL_SELECTION": "ABSTAIN",
        "FOREIGN_NEWS_PROVIDER_CALLS": 0,
        "KIS_MOCK_FULL_RECONCILIATION_VERIFIED": True,
        "OWNER_PRIVATE_BGE_LOCAL_VERIFIED": True,
        "OWNER_PRIVATE_IMPORT_DELETE_RLS_VERIFIED": True,
        "OWNER_PRIVATE_PROFILE_SELECTION": "USER_EXPLICIT_LIBRARY_LEVEL",
        "OWNER_PRIVATE_VOYAGE_SYNTHETIC_ONE_SHOT_VERIFIED": True,
        "PRE_S5_FRESH_EXECUTION_NAMESPACE_VERIFIED": True,
        "RAG_NEWS_ANALYST_DECISION_SIGNAL_ORDER_AUTHORITY": 0,
        "RAG_V2_ACTIVE_EMBEDDING_PROFILE": "voyage_context_4_1024_v1",
        "RAG_V2_CORPUS_STATE": "FULL_READY",
        "S48_ACCESSIBLE_LANES_TERMINALLY_CLASSIFIED": True,
        "TRACKED_RAW_EXTRACTED_EMBEDDINGS": 0,
        "VERTEX_SERVICE_ACCOUNT_OAUTH_GEMINI_3_5_FLASH_ONE_SHOT_VERIFIED": True,
        "VOYAGE_QUERY_USAGE": "COMMITTED",
    }
    if not isinstance(markers, dict) or not _json_exact_equal(markers, required):
        raise FinalGateError("PRE_S5_RELEASE_LEDGER_INCOMPLETE")
    return required


def write_release_evidence_receipt(
    *,
    output_path: Path,
    binding: tuple[str, str, str, str],
    kind: str,
    facts: Mapping[str, object],
) -> str:
    """완료된 local/provider gate의 allowlisted scalar만 0600 receipt로 봉인한다."""

    if kind not in _RELEASE_RECEIPT_NAMES or not _release_receipt_facts_are_valid(kind, facts):
        raise FinalGateError("PRE_S5_RELEASE_RECEIPT_INVALID")
    expected_binding = {
        "ciDigest": binding[2],
        "headCommit": binding[0],
        "securityDigest": binding[3],
        "treeObject": binding[1],
    }
    if (
        _GIT_OBJECT.fullmatch(binding[0]) is None
        or _GIT_OBJECT.fullmatch(binding[1]) is None
        or _SHA256.fullmatch(binding[2]) is None
        or _SHA256.fullmatch(binding[3]) is None
    ):
        raise FinalGateError("PRE_S5_RELEASE_RECEIPT_INVALID")
    payload: dict[str, object] = {
        "binding": expected_binding,
        "facts": dict(facts),
        "kind": kind,
        "schemaVersion": "pre-s5-release-evidence-receipt/v1",
        "state": "VERIFIED",
    }
    content = _canonical_json(payload)
    _write_private_json(output_path, content)
    return hashlib.sha256(content).hexdigest()


def _verify_release_receipt(
    *,
    local_root: Path,
    name: str,
    reference: object,
    expected_binding: Mapping[str, str],
) -> None:
    """fixed relative path와 digest를 먼저 검증해 ledger가 임의 파일을 신뢰하지 않게 한다."""

    if not isinstance(reference, dict) or set(reference) != {"path", "sha256"}:
        raise FinalGateError("PRE_S5_RELEASE_RECEIPT_INVALID")
    expected_path = f"evidence/{name}.json"
    if reference.get("path") != expected_path:
        raise FinalGateError("PRE_S5_RELEASE_RECEIPT_INVALID")
    expected_sha = reference.get("sha256")
    if not isinstance(expected_sha, str) or _SHA256.fullmatch(expected_sha) is None:
        raise FinalGateError("PRE_S5_RELEASE_RECEIPT_INVALID")
    raw = _read_private_json(local_root / expected_path, max_bytes=64 * 1024)
    if not _constant_time_equal(hashlib.sha256(raw).hexdigest(), expected_sha):
        raise FinalGateError("PRE_S5_RELEASE_RECEIPT_INVALID")
    receipt = _decode_json(raw, code="PRE_S5_RELEASE_RECEIPT_INVALID")
    if (
        not isinstance(receipt, dict)
        or set(receipt) != {"binding", "facts", "kind", "schemaVersion", "state"}
        or receipt.get("binding") != expected_binding
        or receipt.get("kind") != name
        or receipt.get("schemaVersion") != "pre-s5-release-evidence-receipt/v1"
        or receipt.get("state") != "VERIFIED"
    ):
        raise FinalGateError("PRE_S5_RELEASE_RECEIPT_INVALID")
    facts = receipt.get("facts")
    if not isinstance(facts, dict) or not _release_receipt_facts_are_valid(name, facts):
        raise FinalGateError("PRE_S5_RELEASE_RECEIPT_INVALID")


def _release_receipt_facts_are_valid(name: str, facts: Mapping[str, object]) -> bool:
    if name == "ownerBgeLocal":
        return _json_exact_equal(
            facts,
            {"documentCount": 1, "providerPhysicalCalls": 0, "residualRows": 0},
        )
    if name == "kisMockV3":
        return (
            set(facts)
            == {
                "brokeragePhysicalCalls",
                "completedSteps",
                "liveOrderCalls",
                "openOrderCount",
                "retryCount",
                "tokenPhysicalCalls",
            }
            and type(facts.get("brokeragePhysicalCalls")) is int
            and facts.get("brokeragePhysicalCalls") == 7
            and _json_exact_equal(
                facts.get("completedSteps"),
                [
                "preBalance",
                "buyable",
                "submitLimitBuy",
                "cancelFull",
                "executionRead",
                "postBalance",
                "openOrderReconciliation",
                ],
            )
            and type(facts.get("liveOrderCalls")) is int
            and facts.get("liveOrderCalls") == 0
            and type(facts.get("openOrderCount")) is int
            and facts.get("openOrderCount") == 0
            and type(facts.get("retryCount")) is int
            and facts.get("retryCount") == 0
            and type(facts.get("tokenPhysicalCalls")) is int
            and facts.get("tokenPhysicalCalls") in {0, 1}
        )
    if name == "requiredCi":
        return _json_exact_equal(
            facts,
            {
                "checks": {
                    "Contracts CI": "SUCCESS",
                    "Kotlin Build": "SUCCESS",
                    "Python CI": "SUCCESS",
                    "Repo Hygiene": "SUCCESS",
                }
            },
        )
    if name == "securityScan":
        return _json_exact_equal(facts, {"coverage": "complete", "validatedFindings": 0})
    if name == "trackedAudit":
        return _json_exact_equal(
            facts,
            {
                "aiAttributionCount": 0,
                "credentialCount": 0,
                "placeholderWorkspaceDiffCount": 0,
                "rawTextVectorCount": 0,
            },
        )
    return False


def _json_exact_equal(actual: object, expected: object) -> bool:
    """JSON bool을 integer evidence로 취급하는 Python equality를 명시적으로 차단한다."""

    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False
        return set(actual) == set(expected) and all(
            _json_exact_equal(actual[key], expected[key]) for key in expected
        )
    if isinstance(expected, list):
        if not isinstance(actual, list):
            return False
        return len(actual) == len(expected) and all(
            _json_exact_equal(left, right) for left, right in zip(actual, expected, strict=True)
        )
    return actual == expected


def _verify_release_database_snapshot(
    snapshot: ReleaseDatabaseSnapshot,
    *,
    voyage_query_packet_sha256: str,
    vertex_packet_sha256: str,
) -> None:
    if (
        snapshot.latest_migration != 61
        or snapshot.public_state != "ACTIVE"
        or snapshot.public_embedding_profile_id != "voyage_context_4_1024_v1"
        or snapshot.public_source_count != 142
        or snapshot.public_chunk_count != 7_871
        or snapshot.committed_document_batch_count != 63
        or snapshot.public_evaluation_count != 2
        or snapshot.public_evaluation_minimum != 1.0
        or snapshot.public_evaluation_leak_count != 0
        or snapshot.owner_source_count != 0
        or snapshot.owner_chunk_count != 0
        or snapshot.owner_embedding_count != 0
        or snapshot.owner_profile_lock_count != 0
        or snapshot.owner_voyage_committed_document_count != 9
        or snapshot.owner_voyage_committed_chunk_count != 9
        or snapshot.s48_states != _RELEASE_S48_STATES
        or snapshot.voyage_query_committed_packet_sha256 != voyage_query_packet_sha256
        or snapshot.vertex_committed_packet_sha256 != vertex_packet_sha256
    ):
        raise FinalGateError("PRE_S5_RELEASE_DATABASE_DRIFT")


def _release_database_dsn() -> str:
    """fresh localhost DB의 operator-only admin DSN을 argv와 receipt 밖에서 조립한다."""

    explicit = os.environ.get("PRE_S5_RELEASE_DATABASE_DSN", "").strip()
    if explicit:
        dsn = explicit
    else:
        values = {
            key: os.environ.get(key, "").strip()
            for key in (
                "POSTGRES_ADMIN_PASSWORD",
                "POSTGRES_ADMIN_USER",
                "POSTGRES_DB",
                "POSTGRES_HOST",
                "POSTGRES_HOST_PORT",
            )
        }
        if not all(values.values()):
            raise FinalGateError("PRE_S5_RELEASE_DATABASE_DSN")
        dsn = (
            f"postgresql://{quote(values['POSTGRES_ADMIN_USER'], safe='')}:"
            f"{quote(values['POSTGRES_ADMIN_PASSWORD'], safe='')}@{values['POSTGRES_HOST']}:"
            f"{values['POSTGRES_HOST_PORT']}/{quote(values['POSTGRES_DB'], safe='')}"
        )
    parsed = urlsplit(dsn)
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or parsed.hostname != "127.0.0.1"
        or parsed.port != 55_432
        or not parsed.username
        or not parsed.password
        or not parsed.path.strip("/")
        or parsed.query
        or parsed.fragment
    ):
        raise FinalGateError("PRE_S5_RELEASE_DATABASE_DSN")
    return dsn


def _load_release_database_snapshot(
    *,
    database_dsn: str,
    voyage_query_packet_sha256: str,
    vertex_packet_sha256: str,
) -> ReleaseDatabaseSnapshot:
    """fresh DB의 allowlisted aggregate만 읽고 row text, vector, credential은 반환하지 않는다."""

    if (
        _SHA256.fullmatch(voyage_query_packet_sha256) is None
        or _SHA256.fullmatch(vertex_packet_sha256) is None
    ):
        raise FinalGateError("PRE_S5_RELEASE_DATABASE_DRIFT")
    try:
        with psycopg.connect(database_dsn, autocommit=False, connect_timeout=2) as connection:
            with connection.transaction():
                connection.execute("SET LOCAL statement_timeout = '5000ms'")
                connection.execute("SET LOCAL lock_timeout = '1000ms'")
                core = connection.execute(
                    """
                    SELECT
                      (SELECT max(version::integer) FROM public.flyway_schema_history
                       WHERE success AND version ~ '^[0-9]+$'),
                      pointer.state,
                      pointer.embedding_profile_id,
                      (SELECT count(*) FROM public.rag_v2_immutable_source_revisions
                       WHERE source_scope IN ('EXACT30', 'OA112')),
                      (SELECT count(*) FROM public.rag_v2_immutable_chunks
                       WHERE source_scope IN ('EXACT30', 'OA112')),
                      (SELECT count(*) FROM public.rag_v2_immutable_voyage_document_batches
                       WHERE state = 'COMMITTED'),
                      (SELECT count(*) FROM public.rag_v2_immutable_source_revisions
                       WHERE source_scope = 'OWNER_PRIVATE'),
                      (SELECT count(*) FROM public.rag_v2_immutable_chunks
                       WHERE source_scope = 'OWNER_PRIVATE'),
                      (SELECT count(*) FROM public.rag_v2_immutable_generation_embeddings
                       WHERE component_scope = 'OWNER_PRIVATE'),
                      (SELECT count(*)
                       FROM public.rag_v2_immutable_owner_bundle_pointers owner_pointer
                       JOIN public.rag_v2_immutable_bundles owner_bundle
                         ON owner_bundle.bundle_id = owner_pointer.active_bundle_id
                       WHERE owner_bundle.owner_embedding_profile_id IS NOT NULL),
                      (SELECT coalesce(sum(document_count), 0)
                       FROM public.rag_v2_owner_voyage_import_attempts WHERE state = 'COMMITTED'),
                      (SELECT coalesce(sum(chunk_count), 0)
                       FROM public.rag_v2_owner_voyage_import_attempts WHERE state = 'COMMITTED')
                    FROM public.rag_v2_immutable_public_bundle_pointers pointer
                    WHERE pointer.state_id = 'default'
                    """
                ).fetchone()
                evaluation = connection.execute(
                    """
                    SELECT count(*),
                           min(least(exact_top5_hit_rate, track_recall_at5, citation_coverage,
                                     direct_advice_block_rate)),
                           coalesce(sum(cross_owner_leak_count + mixed_profile_row_count
                                        + owner_delete_residual_row_count), 0)
                    FROM public.rag_v2_immutable_public_voyage_component_evaluations evaluation
                    JOIN public.rag_v2_immutable_public_bundle_pointers pointer
                      ON evaluation.component_generation_id IN (
                           pointer.exact30_generation_id, pointer.oa112_generation_id
                         )
                    WHERE pointer.state_id = 'default'
                    """
                ).fetchone()
                s48_rows = connection.execute(
                    """
                    SELECT source_id, status
                    FROM (
                      SELECT DISTINCT ON (source_id) source_id, status
                      FROM public.s48_runtime_sanitized_projections
                      ORDER BY source_id, evaluated_at DESC, logical_identity_hash DESC
                    ) latest
                    ORDER BY source_id
                    """
                ).fetchall()
                voyage_row = connection.execute(
                    """
                    SELECT outcome.packet_sha256
                    FROM public.rag_v2_immutable_voyage_query_usage_outcomes outcome
                    JOIN public.rag_v2_immutable_voyage_query_usage_reservations reservation
                      USING (usage_event_id)
                    WHERE outcome.state = 'COMMITTED'
                      AND reservation.evaluation_component_scope = 'RUNTIME'
                      AND outcome.packet_sha256 = %s
                    """,
                    (voyage_query_packet_sha256,),
                ).fetchone()
                vertex_row = connection.execute(
                    """
                    SELECT packet_sha256
                    FROM public.rag_v2_immutable_vertex_usage_outcomes
                    WHERE state = 'COMMITTED' AND packet_sha256 = %s
                    """,
                    (vertex_packet_sha256,),
                ).fetchone()
    except (psycopg.Error, ValueError, TypeError):
        raise FinalGateError("PRE_S5_RELEASE_DATABASE_UNAVAILABLE") from None
    if core is None or len(core) != 12 or evaluation is None or len(evaluation) != 3:
        raise FinalGateError("PRE_S5_RELEASE_DATABASE_DRIFT")
    try:
        return ReleaseDatabaseSnapshot(
            latest_migration=int(core[0]),
            public_state=str(core[1]),
            public_embedding_profile_id=str(core[2]),
            public_source_count=int(core[3]),
            public_chunk_count=int(core[4]),
            committed_document_batch_count=int(core[5]),
            public_evaluation_count=int(evaluation[0]),
            public_evaluation_minimum=float(evaluation[1]),
            public_evaluation_leak_count=int(evaluation[2]),
            owner_source_count=int(core[6]),
            owner_chunk_count=int(core[7]),
            owner_embedding_count=int(core[8]),
            owner_profile_lock_count=int(core[9]),
            owner_voyage_committed_document_count=int(core[10]),
            owner_voyage_committed_chunk_count=int(core[11]),
            s48_states=tuple((str(row[0]), str(row[1])) for row in s48_rows),
            voyage_query_committed_packet_sha256=(str(voyage_row[0]) if voyage_row else None),
            vertex_committed_packet_sha256=(str(vertex_row[0]) if vertex_row else None),
        )
    except (IndexError, TypeError, ValueError):
        raise FinalGateError("PRE_S5_RELEASE_DATABASE_DRIFT") from None


def _constant_time_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("ascii"), right.encode("ascii"))


def _krx_tick(price: int) -> int:
    if price < 2_000:
        return 1
    if price < 5_000:
        return 5
    if price < 20_000:
        return 10
    if price < 50_000:
        return 50
    if price < 200_000:
        return 100
    if price < 500_000:
        return 500
    return 1_000


def _write_private_json(path: Path, content: bytes) -> None:
    if not path.is_absolute() or ".." in path.parts or not content or len(content) > 64 * 1024:
        raise FinalGateError("PRE_S5_FINAL_GATE_BOUNDARY")
    _create_or_require_private_directory(path.parent)
    temporary_name = f".{path.name}.{os.getpid()}.tmp"
    parent_descriptor = -1
    descriptor = -1
    try:
        parent_descriptor = os.open(
            path.parent,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
        _validate_private_directory_metadata(os.fstat(parent_descriptor))
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=parent_descriptor,
        )
        os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(content):
            written = os.write(descriptor, content[offset:])
            if written <= 0:
                raise FinalGateError("PRE_S5_FINAL_GATE_BOUNDARY")
            offset += written
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or metadata.st_size != len(content):
            raise FinalGateError("PRE_S5_FINAL_GATE_BOUNDARY")
        os.close(descriptor)
        descriptor = -1
        try:
            existing = os.stat(path.name, dir_fd=parent_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None:
            if (
                not stat.S_ISREG(existing.st_mode)
                or existing.st_uid != os.geteuid()
                or existing.st_nlink != 1
                or stat.S_IMODE(existing.st_mode) != 0o600
            ):
                raise FinalGateError("PRE_S5_FINAL_GATE_BOUNDARY")
        os.replace(
            temporary_name,
            path.name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
        )
        os.chmod(path.name, 0o600, dir_fd=parent_descriptor, follow_symlinks=False)
    except FinalGateError:
        raise
    except OSError as error:
        raise FinalGateError("PRE_S5_FINAL_GATE_BOUNDARY") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if parent_descriptor >= 0:
            try:
                os.unlink(temporary_name, dir_fd=parent_descriptor)
            except FileNotFoundError:
                pass
            os.close(parent_descriptor)


def _create_or_require_private_directory(path: Path) -> None:
    """trusted 0700 parent 아래 한 단계만 만들고 link traversal을 막는다."""

    try:
        metadata = path.lstat()
    except FileNotFoundError:
        _require_private_directory(path.parent)
        try:
            path.mkdir(mode=0o700, parents=False, exist_ok=False)
        except OSError as error:
            raise FinalGateError("PRE_S5_FINAL_GATE_BOUNDARY") from error
        metadata = path.lstat()
    except OSError as error:
        raise FinalGateError("PRE_S5_FINAL_GATE_BOUNDARY") from error
    _validate_private_directory_metadata(metadata)


def _require_private_directory(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise FinalGateError("PRE_S5_FINAL_GATE_BOUNDARY") from error
    _validate_private_directory_metadata(metadata)


def _validate_private_directory_metadata(metadata: os.stat_result) -> None:
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise FinalGateError("PRE_S5_FINAL_GATE_BOUNDARY")


def _read_private_json(path: Path, *, max_bytes: int) -> bytes:
    if not path.is_absolute() or ".." in path.parts:
        raise FinalGateError("PRE_S5_FINAL_GATE_BOUNDARY")
    try:
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or not 1 <= metadata.st_size <= max_bytes
        ):
            raise FinalGateError("PRE_S5_FINAL_GATE_BOUNDARY")
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            raw = os.read(descriptor, max_bytes + 1)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
    except FinalGateError:
        raise
    except OSError as error:
        raise FinalGateError("PRE_S5_FINAL_GATE_BOUNDARY") from error
    if len(raw) != metadata.st_size or len(raw) > max_bytes or (after.st_dev, after.st_ino) != (metadata.st_dev, metadata.st_ino):
        raise FinalGateError("PRE_S5_FINAL_GATE_BOUNDARY")
    return raw


def _reject_duplicate_keys(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _decode_json(raw: bytes, *, code: str) -> object:
    try:
        return json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
        raise FinalGateError(code) from error


def _parse_instant(value: object) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("instant")
    parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    if parsed.tzinfo is None:
        raise ValueError("instant")
    return parsed.astimezone(UTC)


def _environment_root() -> Path:
    value = os.environ.get("CAPSTONE_RAG_LOCAL_ROOT", "").strip()
    root = Path(value)
    if not value or not root.is_absolute() or ".." in root.parts:
        raise FinalGateError("PRE_S5_FINAL_GATE_ENVIRONMENT")
    try:
        _require_private_directory(root)
    except FinalGateError as error:
        raise FinalGateError("PRE_S5_FINAL_GATE_ENVIRONMENT") from error
    return root


def _environment_binding() -> tuple[str, str, str, str]:
    head = os.environ.get("PRE_S5_FINAL_HEAD_COMMIT", "").strip()
    tree = os.environ.get("PRE_S5_FINAL_TREE_OBJECT", "").strip()
    ci = os.environ.get("PRE_S5_FINAL_CI_DIGEST", "").strip()
    security = os.environ.get("PRE_S5_FINAL_SECURITY_DIGEST", "").strip()
    if (
        _GIT_OBJECT.fullmatch(head) is None
        or _GIT_OBJECT.fullmatch(tree) is None
        or _SHA256.fullmatch(ci) is None
        or _SHA256.fullmatch(security) is None
    ):
        raise FinalGateError("PRE_S5_FINAL_GATE_ENVIRONMENT")
    return head, tree, ci, security


def _required_hash_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if _SHA256.fullmatch(value) is None:
        raise FinalGateError("PRE_S5_FINAL_GATE_ENVIRONMENT")
    return value


def _canonical_json(value: Mapping[str, object]) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _format_instant(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _emit(value: Mapping[str, object]) -> None:
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
