from __future__ import annotations

import hashlib
import json
import os
import struct
import tempfile
from pathlib import Path

import pytest

from app.data._shared.canonical_json import canonical_json_bytes
from app.p1_owner.assets import build_golden_bundle
from app.p1_owner.importer import P1ArtifactImportError, validate_artifact_bundle
from tests.p1_owner.test_assets import _build_input


def _golden(root: Path) -> tuple[Path, str]:
    input_root = _build_input(root)
    golden_root = root / "golden"
    result = build_golden_bundle(
        input_pack_manifest=input_root / "manifest.json",
        output_root=golden_root,
    )
    return golden_root, result.manifest_sha256


def _rewrite_manifest(golden_root: Path, artifact_name: str) -> str:
    manifest_path = golden_root / "p1-return-engine-manifest.v2.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    content = (golden_root / artifact_name).read_bytes()
    for item in manifest["artifacts"]:
        if item["path"] == artifact_name:
            item["sha256"] = hashlib.sha256(content).hexdigest()
            item["sizeBytes"] = len(content)
    if artifact_name == "golden_output.json":
        manifest["producer"]["goldenOutputSha256"] = hashlib.sha256(content).hexdigest()
    manifest_bytes = canonical_json_bytes(manifest)
    manifest_path.write_bytes(manifest_bytes)
    return hashlib.sha256(manifest_bytes).hexdigest()


def test_importer_validates_exact_ten_bundle_and_builds_bounded_projection_packet() -> None:
    with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
        root = Path(temporary)
        golden_root, manifest_sha = _golden(root)

        result = validate_artifact_bundle(
            bundle_root=golden_root,
            expected_manifest_sha256=manifest_sha,
        )

        assert result.bundle_sha256 == manifest_sha
        assert result.artifact_id == f"artifact_p1_{manifest_sha[:24]}"
        assert result.evidence_mode == "SYNTHETIC_GOLDEN"
        assert result.real_team_b is False
        assert result.import_packet["fixtureClass"] == "SYNTHETIC_FAKE_E2E"
        assert len(result.import_packet["signals"]) == 62
        assert {row["producer"] for row in result.import_packet["signals"]} == {
            "LSTM",
            "RULE_BASELINE",
        }
        model_projection = json.loads(result.import_packet["modelProjectionText"])
        backtest_projection = json.loads(result.import_packet["backtestProjectionText"])
        assert model_projection["data"]["performanceClaimAllowed"] is False
        assert backtest_projection["data"]["performanceClaimAllowed"] is False
        assert backtest_projection["data"]["view"]["fixtureClass"] == ("SYNTHETIC_FAKE_E2E")


def test_importer_rejects_extra_file_and_hard_link_before_content_parsing() -> None:
    with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
        root = Path(temporary)
        golden_root, manifest_sha = _golden(root)
        (golden_root / "unexpected.json").write_text("{}", encoding="utf-8")
        with pytest.raises(P1ArtifactImportError, match="exact ten"):
            validate_artifact_bundle(
                bundle_root=golden_root,
                expected_manifest_sha256=manifest_sha,
            )

    with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
        root = Path(temporary)
        golden_root, manifest_sha = _golden(root)
        report = golden_root / "model_report.md"
        linked = root / "linked-report.md"
        os.link(report, linked)
        with pytest.raises(P1ArtifactImportError, match="metadata is unsafe"):
            validate_artifact_bundle(
                bundle_root=golden_root,
                expected_manifest_sha256=manifest_sha,
            )


def test_importer_rejects_non_finite_safetensors_even_with_matching_manifest_hash() -> None:
    with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
        root = Path(temporary)
        golden_root, _manifest_sha = _golden(root)
        model_path = golden_root / "model.safetensors"
        content = bytearray(model_path.read_bytes())
        header_length = struct.unpack("<Q", content[:8])[0]
        data_start = 8 + header_length
        content[data_start : data_start + 4] = struct.pack("<f", float("nan"))
        model_path.write_bytes(content)
        manifest_sha = _rewrite_manifest(golden_root, "model.safetensors")

        with pytest.raises(P1ArtifactImportError, match="non-finite"):
            validate_artifact_bundle(
                bundle_root=golden_root,
                expected_manifest_sha256=manifest_sha,
            )


def test_importer_rejects_backtest_metrics_not_supported_by_equity_and_trade_logs() -> None:
    with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
        root = Path(temporary)
        golden_root, _manifest_sha = _golden(root)
        backtest_path = golden_root / "backtest_result.json"
        backtest = json.loads(backtest_path.read_text(encoding="utf-8"))
        backtest["scenarios"][1]["netReturn"] = 0.25
        backtest_path.write_bytes(canonical_json_bytes(backtest))
        manifest_sha = _rewrite_manifest(golden_root, "backtest_result.json")

        with pytest.raises(P1ArtifactImportError, match="independent recomputation mismatch"):
            validate_artifact_bundle(
                bundle_root=golden_root,
                expected_manifest_sha256=manifest_sha,
            )


def test_importer_module_has_no_provider_account_or_order_transport() -> None:
    source = Path(__file__).parents[2] / "app" / "p1_owner" / "importer.py"
    text = source.read_text(encoding="utf-8")
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
        assert forbidden not in text


def test_capstone_artifact_command_is_outside_certification_heredoc_and_uses_one_shot_profile() -> (
    None
):
    repository = Path(__file__).parents[5]
    control = (repository / "deploy/p1/full-appctl").read_text(encoding="utf-8")
    certification = control.index("mock_certify()")
    certification_heredoc_end = control.index("\nPY\n", certification)
    artifact_function = control.index("\nartifact_import()")
    assert artifact_function > certification_heredoc_end
    assert "artifact <validate|import> <bundle-directory> --manifest-sha256 <sha256>" in control
    assert "compose --profile owner run --rm --no-deps artifact-importer" in control
    validator_function = control.index("\nartifact_validate()")
    assert validator_function > artifact_function
    validator = control[validator_function : control.index("\n}\n", validator_function)]
    assert "--pull never" in validator
    assert "--network none" in validator
    assert "--read-only" in validator
    assert "--validate-only" in validator
    assert "PROVIDER_LIVE_CALLS_ENABLED=false" in validator
    assert "KIS_OFFLINE=1" in validator
    assert "PROVIDER_CALLS=0" in validator

    compose = (repository / "deploy/p1/compose.yml").read_text(encoding="utf-8")
    assert "artifact-importer:" in compose
    assert 'entrypoint: ["/usr/local/bin/p1-secret-entrypoint", "artifact-import"]' in compose
    assert 'P1_OPERATOR_UID: "${P1_OPERATOR_UID}"' in compose
    assert "/owner/${P1_ARTIFACT_BUNDLE_NAME:-disabled}" in compose
    assert "${P1_ARTIFACT_BUNDLE_PARENT:-/nonexistent}:/owner:ro" in compose
    assert "${P1_ARTIFACT_ARCHIVE_DIR:-/nonexistent}:/archive" in compose

    dockerfile = (repository / "deploy/p1/docker/decision-platform.Dockerfile").read_text(
        encoding="utf-8"
    )
    assert "COPY --chown=65532:65532 contracts /app/contracts" in dockerfile

    entrypoint = (repository / "deploy/p1/docker/secret-entrypoint.sh").read_text(encoding="utf-8")
    assert 'profile" = certification ] || [ "$profile" = artifact-import' in entrypoint
