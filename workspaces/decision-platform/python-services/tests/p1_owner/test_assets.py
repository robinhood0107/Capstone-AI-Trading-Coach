from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

import app.p1_owner.assets as owner_assets
from app.p1_owner.assets import (
    P1OwnerAssetError,
    build_golden_bundle,
    verify_golden_bundle,
)

# 골든 번들은 회귀 기준선이므로 세션 날짜를 고정한다. 오늘 날짜를 쓰면 두 번 실행
# byte determinism 이 날마다 깨진다.
GOLDEN_SESSION_DATE = "2026-08-31"

UNIVERSE_CATALOG = "contracts/catalogs/p1-return-universe.v1.json"


def universe_catalog() -> Path:
    """커밋된 exact-31 유니버스 카탈로그. 봉인 input pack 을 대신하는 골든 번들 입력이다."""

    path = owner_assets._repository_root() / UNIVERSE_CATALOG
    assert path.is_file(), f"유니버스 카탈로그가 없다: {path}"
    return path


def _build_golden(root: Path) -> tuple[Path, str]:
    golden_root = root / "golden"
    result = build_golden_bundle(
        universe_catalog=universe_catalog(),
        session_date=GOLDEN_SESSION_DATE,
        output_root=golden_root,
    )
    return golden_root, result.manifest_sha256


def _tampered_catalog(root: Path, mutate) -> Path:
    payload = json.loads(universe_catalog().read_text(encoding="utf-8"))
    mutate(payload)
    path = root / "p1-return-universe.v1.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def test_golden_bundle_roundtrip_is_provider_free_and_idempotent() -> None:
    with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
        root = Path(temporary)
        golden_root = root / "golden"
        first = build_golden_bundle(
            universe_catalog=universe_catalog(),
            session_date=GOLDEN_SESSION_DATE,
            output_root=golden_root,
        )
        second = build_golden_bundle(
            universe_catalog=universe_catalog(),
            session_date=GOLDEN_SESSION_DATE,
            output_root=golden_root,
        )
        golden_sha = verify_golden_bundle(golden_root)

        assert first.no_op is False
        assert second.no_op is True
        assert first.manifest_sha256 == second.manifest_sha256 == golden_sha
        manifest = json.loads((golden_root / "p1-return-engine-manifest.v3.json").read_text())
        assert manifest["evidenceMode"] == "SYNTHETIC_GOLDEN"
        assert manifest["realTeamB"] is False
        assert manifest["performanceClaimAllowed"] is False
        assert manifest["orderAuthority"] == "NONE"
        # inputPackSha256 은 사전 공유 상수가 아니라 실제로 읽은 카탈로그 바이트의 해시다.
        assert manifest["inputPackSha256"] == owner_assets._digest(universe_catalog().read_bytes())
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


def test_golden_bundle_rejects_universe_that_is_not_exact31() -> None:
    with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
        root = Path(temporary)
        cases = [
            ("종목 하나 제거", lambda payload: payload["symbols"].pop()),
            (
                "금 ETF 중복",
                lambda payload: payload["symbols"][0].update({"symbol": "132030"}),
            ),
            (
                "금 ETF 없음",
                lambda payload: [
                    item.update({"symbol": "000001"})
                    for item in payload["symbols"]
                    if item["symbol"] == "132030"
                ],
            ),
        ]
        for index, (label, mutate) in enumerate(cases):
            case_root = root / f"case-{index}"
            case_root.mkdir()
            tampered = _tampered_catalog(case_root, mutate)
            with pytest.raises(P1OwnerAssetError, match="exact-31"):
                build_golden_bundle(
                    universe_catalog=tampered,
                    session_date=GOLDEN_SESSION_DATE,
                    output_root=case_root / "golden",
                )
            assert not (case_root / "golden").exists(), label


def test_golden_bundle_rejects_non_iso_session_date() -> None:
    with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
        root = Path(temporary)
        for value in ("2026-8-31", "20260831", "2026-13-01", "not-a-date", ""):
            with pytest.raises(P1OwnerAssetError, match="ISO date"):
                build_golden_bundle(
                    universe_catalog=universe_catalog(),
                    session_date=value,
                    output_root=root / "golden",
                )
        assert not (root / "golden").exists()


def test_golden_verifier_rejects_symlinked_artifact() -> None:
    with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
        root = Path(temporary)
        golden_root, _ = _build_golden(root)
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
        golden_root, _ = _build_golden(root)
        with (golden_root / "model_report.md").open("ab") as stream:
            stream.write(b"tampered")
        with pytest.raises(P1OwnerAssetError, match="binding mismatch"):
            verify_golden_bundle(golden_root)


def test_publisher_rejects_symlinked_output_parent() -> None:
    with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
        root = Path(temporary)
        real_parent = root / "real-parent"
        real_parent.mkdir(mode=0o700)
        linked_parent = root / "linked-parent"
        try:
            os.symlink(real_parent, linked_parent)
        except OSError as error:
            pytest.skip(f"symlink is unavailable: {error}")
        with pytest.raises(P1OwnerAssetError, match="opened safely"):
            build_golden_bundle(
                universe_catalog=universe_catalog(),
                session_date=GOLDEN_SESSION_DATE,
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
