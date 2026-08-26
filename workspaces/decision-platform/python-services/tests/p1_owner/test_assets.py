from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest import mock

import pyarrow as pa
import pytest

import app.p1_owner.assets as owner_assets
from app.data.market_data.archive import MarketDataArchive
from app.p1_owner.assets import (
    P1OwnerAssetError,
    build_golden_bundle,
    build_input_pack,
    verify_golden_bundle,
    verify_input_pack,
)


_ARCHIVE_SHA = "e3f26485c93d5e8bd9cdbd7f9ea7cc46cf3f446cf42e9d65b28f1f5b89bd9a5c"


def _symbols() -> list[str]:
    return [f"{index:06d}" for index in range(1, 31)] + ["132030"]


def _sessions() -> list[date]:
    first = date(2023, 1, 2)
    return [first + timedelta(days=index) for index in range(1_100)]


def _tables() -> dict[str, pa.Table]:
    symbols = _symbols()
    sessions = _sessions()
    bars = pa.Table.from_pylist(
        [
            {
                "close": 10_000 + symbol_index,
                "currency": "KRW",
                "high": 10_100 + symbol_index,
                "low": 9_900 + symbol_index,
                "open": 10_000 + symbol_index,
                "sessionDate": session,
                "sourceReceiptSha256": "a" * 64,
                "symbol": symbol,
                "temporalQuality": "RECONSTRUCTED_FIXED_LAG",
                "volume": 1_000 + symbol_index,
            }
            for symbol_index, symbol in enumerate(symbols)
            for session in sessions
        ]
    )
    indices = pa.Table.from_pylist(
        [
            {
                "close": 1_000.0,
                "indexId": index_id,
                "sessionDate": session,
                "sourceReceiptSha256": "b" * 64,
                "temporalQuality": "PROVIDER_AS_OF_NO_VINTAGE",
            }
            for session in sessions
            for index_id in ("KOSPI", "KOSDAQ")
        ]
    )
    macro = pa.Table.from_pylist(
        [
            {
                "availableAt": datetime.combine(session, datetime.min.time(), tzinfo=UTC),
                "observationDate": session,
                "seriesId": series,
                "sourceReceiptSha256": "c" * 64,
                "temporalQuality": "RECONSTRUCTED_FIXED_LAG",
                "value": "1.0",
            }
            for session in sessions
            for series in ("722Y001/0101000/D", "731Y001/0000001/D")
        ]
    )
    universes = pa.Table.from_pylist(
        [
            {
                "effectiveFromSession": sessions[0],
                "instrumentId": f"SYNTHETIC-{symbol}",
                "isFixedMember": symbol == "132030",
                "market": "ETF" if symbol == "132030" else "KOSPI",
                "membershipMonth": "2026-08",
                "rank": index,
                "selectionSession": sessions[0],
                "sourceReceiptSha256": "d" * 64,
                "symbol": symbol,
                "temporalQuality": "PROVIDER_AS_OF_NO_VINTAGE",
            }
            for index, symbol in enumerate(symbols, start=1)
        ]
    )
    return {"BARS": bars, "INDICES": indices, "MACRO": macro, "UNIVERSES": universes}


def _archive(root: Path) -> MarketDataArchive:
    return MarketDataArchive(
        root=root,
        manifest_sha256=_ARCHIVE_SHA,
        archive_sha256="f" * 64,
        source_manifest_sha256="e" * 64,
        created_at=datetime(2026, 8, 26, tzinfo=UTC),
        artifacts=(),
    )


def _build_input(root: Path) -> Path:
    tables = _tables()
    archive_root = root / "archive"
    archive_root.mkdir(mode=0o700)
    output = root / "input-pack"
    with (
        mock.patch(
            "app.p1_owner.assets.read_market_data_archive", return_value=_archive(archive_root)
        ),
        mock.patch(
            "app.p1_owner.assets.read_artifact_table",
            side_effect=lambda _archive_value, kind: tables[kind],
        ),
    ):
        result = build_input_pack(
            archive_root=archive_root,
            expected_archive_manifest_sha256=_ARCHIVE_SHA,
            output_root=output,
        )
    assert result.no_op is False
    assert result.file_count == 6
    return output


def test_input_pack_and_golden_bundle_roundtrip_is_provider_free_and_idempotent() -> None:
    with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
        root = Path(temporary)
        input_root = _build_input(root)
        input_sha = verify_input_pack(input_root)
        golden_root = root / "golden"
        first = build_golden_bundle(
            input_pack_manifest=input_root / "manifest.json",
            output_root=golden_root,
        )
        second = build_golden_bundle(
            input_pack_manifest=input_root / "manifest.json",
            output_root=golden_root,
        )
        golden_sha = verify_golden_bundle(golden_root)

        assert first.no_op is False
        assert second.no_op is True
        assert first.manifest_sha256 == second.manifest_sha256 == golden_sha
        assert len(input_sha) == 64
        manifest = json.loads((golden_root / "p1-return-engine-manifest.v2.json").read_text())
        assert manifest["evidenceMode"] == "SYNTHETIC_GOLDEN"
        assert manifest["realTeamB"] is False
        assert manifest["performanceClaimAllowed"] is False
        assert manifest["orderAuthority"] == "NONE"
        assert [item["path"] for item in manifest["artifacts"]] == [
            "model.safetensors",
            "scaler.json",
            "config.json",
            "lstm_signals.parquet",
            "rule_baseline_signals.parquet",
            "backtest_result.json",
            "trade_log.parquet",
            "equity_log.parquet",
            "golden_output.json",
            "model_report.md",
        ]


def test_input_pack_rejects_wrong_archive_binding_before_publish() -> None:
    with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
        root = Path(temporary)
        archive_root = root / "archive"
        archive_root.mkdir(mode=0o700)
        output = root / "input-pack"
        with mock.patch(
            "app.p1_owner.assets.read_market_data_archive", return_value=_archive(archive_root)
        ):
            with pytest.raises(P1OwnerAssetError, match="does not match"):
                build_input_pack(
                    archive_root=archive_root,
                    expected_archive_manifest_sha256="1" * 64,
                    output_root=output,
                )
        assert not output.exists()


def test_golden_verifier_rejects_symlinked_artifact() -> None:
    with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
        root = Path(temporary)
        input_root = _build_input(root)
        golden_root = root / "golden"
        build_golden_bundle(
            input_pack_manifest=input_root / "manifest.json",
            output_root=golden_root,
        )
        target = root / "outside-config.json"
        target.write_text("{}", encoding="utf-8")
        (golden_root / "config.json").unlink()
        try:
            os.symlink(target, golden_root / "config.json")
        except OSError as error:
            pytest.skip(f"symlink is unavailable: {error}")
        with pytest.raises(P1OwnerAssetError, match="unsafe or missing"):
            verify_golden_bundle(golden_root)


def test_golden_verifier_rejects_tampered_artifact_hash() -> None:
    with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
        root = Path(temporary)
        input_root = _build_input(root)
        golden_root = root / "golden"
        build_golden_bundle(
            input_pack_manifest=input_root / "manifest.json",
            output_root=golden_root,
        )
        with (golden_root / "model_report.md").open("ab") as stream:
            stream.write(b"tampered")
        with pytest.raises(P1OwnerAssetError, match="binding mismatch"):
            verify_golden_bundle(golden_root)


def test_publisher_rejects_symlinked_output_parent() -> None:
    with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
        root = Path(temporary)
        input_root = _build_input(root)
        real_parent = root / "real-parent"
        real_parent.mkdir(mode=0o700)
        linked_parent = root / "linked-parent"
        try:
            os.symlink(real_parent, linked_parent)
        except OSError as error:
            pytest.skip(f"symlink is unavailable: {error}")
        with pytest.raises(P1OwnerAssetError, match="opened safely"):
            build_golden_bundle(
                input_pack_manifest=input_root / "manifest.json",
                output_root=linked_parent / "golden",
            )
        assert not (real_parent / "golden").exists()


def test_owner_asset_module_has_no_provider_account_or_order_transport() -> None:
    source = Path(owner_assets.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "app.data.kis",
        "app.data.krx",
        "app.data.ecos",
        "app.brokerage",
        "httpx",
        "requests",
        "urllib",
        "socket",
    ):
        assert forbidden not in source
