from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.rag.pre_s5_final_gate import (
    FinalGateError,
    WindowBManifest,
    author_kis_quote_manifest,
    author_window_b_manifest,
    derive_kis_mock_limit_price,
    load_kis_quote_manifest,
    load_kis_quote_receipt,
    load_window_b_manifest,
    require_window_b_child,
    write_kis_quote_receipt,
)


@pytest.mark.parametrize(
    ("current_price", "previous_close", "expected"),
    (
        (73_500, 72_700, 51_000),
        (18_000, 17_950, 12_580),
        (1_500, 1_490, 1_044),
    ),
)
def test_kis_mock_limit_price_uses_previous_close_seventy_percent_plus_one_tick(
    current_price: int,
    previous_close: int,
    expected: int,
) -> None:
    assert derive_kis_mock_limit_price(current_price, previous_close) == expected


def test_kis_mock_limit_price_rejects_price_not_below_current() -> None:
    with pytest.raises(FinalGateError, match="PRE_S5_KIS_MOCK_LIMIT_PRICE_INVALID"):
        derive_kis_mock_limit_price(1, 1)


def test_kis_quote_manifest_and_receipt_are_exact_and_content_free(tmp_path: Path) -> None:
    manifest_path = tmp_path / "kis-mock-quote-manifest.v1.json"
    manifest_sha256 = author_kis_quote_manifest(
        output_path=manifest_path,
        head_commit="1" * 40,
        tree_object="2" * 40,
        ci_digest="3" * 64,
        security_digest="4" * 64,
    )
    os.environ["PRE_S5_KIS_MOCK_QUOTE_MANIFEST_SHA256"] = manifest_sha256
    try:
        manifest = load_kis_quote_manifest(manifest_path)
    finally:
        os.environ.pop("PRE_S5_KIS_MOCK_QUOTE_MANIFEST_SHA256", None)
    receipt_path = tmp_path / "kis-mock-quote-receipt.v1.json"
    receipt_sha256 = write_kis_quote_receipt(
        output_path=receipt_path,
        manifest_sha256=manifest.manifest_sha256,
        quote_projection_sha256="5" * 64,
        current_price=73_500,
        previous_diff=800,
        token_physical_calls=1,
        brokerage_physical_calls=1,
    )
    receipt = load_kis_quote_receipt(receipt_path)

    assert len(receipt_sha256) == 64
    assert receipt.limit_price == 51_000
    assert receipt.previous_close == 72_700
    assert receipt.token_physical_calls == 1
    assert receipt.brokerage_physical_calls == 1
    raw = receipt_path.read_text(encoding="utf-8")
    assert "appkey" not in raw.lower()
    assert "authorization" not in raw.lower()
    assert receipt_path.stat().st_mode & 0o777 == 0o600


def test_window_b_manifest_binds_all_child_packets_and_no_retry(tmp_path: Path) -> None:
    output = tmp_path / "window-b-final-manifest.v1.json"
    manifest_sha256 = author_window_b_manifest(
        output_path=output,
        head_commit="1" * 40,
        tree_object="2" * 40,
        ci_digest="3" * 64,
        security_digest="4" * 64,
        voyage_query_packet_sha256="5" * 64,
        vertex_packet_sha256="6" * 64,
        kis_mock_packet_sha256="7" * 64,
        kis_quote_receipt_sha256="8" * 64,
    )
    os.environ["PRE_S5_WINDOW_B_FINAL_MANIFEST_SHA256"] = manifest_sha256
    try:
        manifest = load_window_b_manifest(output)
    finally:
        os.environ.pop("PRE_S5_WINDOW_B_FINAL_MANIFEST_SHA256", None)

    assert manifest.voyage_query_packet_sha256 == "5" * 64
    assert manifest.vertex_packet_sha256 == "6" * 64
    assert manifest.kis_mock_packet_sha256 == "7" * 64
    value = json.loads(output.read_text(encoding="utf-8"))
    assert value["physicalCaps"] == {
        "kisMockBrokerage": 7,
        "kisMockTokenP": 1,
        "vertexGenerateContent": 1,
        "vertexToken": 1,
        "voyageQuery": 1,
    }
    assert value["retryCount"] == 0
    assert value["rawArtifactCount"] == 0
    assert isinstance(value["expiresAt"], str)
    assert output.stat().st_mode & 0o777 == 0o600


def test_window_b_manifest_rejects_wrong_exact_approval(tmp_path: Path) -> None:
    output = tmp_path / "window-b-final-manifest.v1.json"
    author_window_b_manifest(
        output_path=output,
        head_commit="1" * 40,
        tree_object="2" * 40,
        ci_digest="3" * 64,
        security_digest="4" * 64,
        voyage_query_packet_sha256="5" * 64,
        vertex_packet_sha256="6" * 64,
        kis_mock_packet_sha256="7" * 64,
        kis_quote_receipt_sha256="8" * 64,
    )
    os.environ["PRE_S5_WINDOW_B_FINAL_MANIFEST_SHA256"] = "f" * 64
    try:
        with pytest.raises(FinalGateError, match="PRE_S5_WINDOW_B_MANIFEST_APPROVAL"):
            load_window_b_manifest(output)
    finally:
        os.environ.pop("PRE_S5_WINDOW_B_FINAL_MANIFEST_SHA256", None)


def test_window_b_manifest_rejects_mismatched_child_before_runtime() -> None:
    manifest = WindowBManifest(
        manifest_sha256="4" * 64,
        voyage_query_packet_sha256="5" * 64,
        vertex_packet_sha256="6" * 64,
        kis_mock_packet_sha256="7" * 64,
        kis_quote_receipt_sha256="8" * 64,
        head_commit="1" * 40,
        tree_object="2" * 40,
        ci_digest="3" * 64,
        security_digest="9" * 64,
    )

    with pytest.raises(FinalGateError, match="PRE_S5_WINDOW_B_CHILD_BINDING"):
        require_window_b_child(
            manifest,
            runtime="VERTEX",
            packet_sha256="f" * 64,
        )


def test_final_gate_writer_rejects_symlinked_control_directory(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    os.symlink(outside, tmp_path / "control")

    with pytest.raises(FinalGateError, match="PRE_S5_FINAL_GATE_BOUNDARY"):
        author_kis_quote_manifest(
            output_path=tmp_path / "control" / "kis-mock-quote-manifest.v1.json",
            head_commit="1" * 40,
            tree_object="2" * 40,
            ci_digest="3" * 64,
            security_digest="4" * 64,
        )
    assert not (outside / "kis-mock-quote-manifest.v1.json").exists()
