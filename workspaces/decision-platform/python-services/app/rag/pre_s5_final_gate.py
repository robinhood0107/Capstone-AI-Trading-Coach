"""Pre-S5 KIS quote, Window B, release receipt의 content-free local gate다."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

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
    """ignored release ledger가 exact final marker set을 가진 경우에만 OPEN을 산출한다."""

    ledger_path = local_root / "evidence/pre-s5-release-ledger.v1.json"
    raw = _read_private_json(ledger_path, max_bytes=128 * 1024)
    value = _decode_json(raw, code="PRE_S5_RELEASE_LEDGER_INVALID")
    if not isinstance(value, dict) or value.get("binding") != {
        "ciDigest": binding[2],
        "headCommit": binding[0],
        "securityDigest": binding[3],
        "treeObject": binding[1],
    }:
        raise FinalGateError("PRE_S5_RELEASE_LEDGER_BINDING")
    markers = value.get("markers")
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
    if not isinstance(markers, dict) or any(markers.get(key) != expected for key, expected in required.items()):
        raise FinalGateError("PRE_S5_RELEASE_LEDGER_INCOMPLETE")
    _emit({"markers": required, "state": "OPEN"})
    return 0


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
