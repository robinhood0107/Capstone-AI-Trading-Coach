from __future__ import annotations

import importlib
import importlib.util
import json
from pathlib import Path

import pytest


def _writer_module():
    spec = importlib.util.find_spec("app.brokerage.fill_observation_writer")
    assert spec is not None, "S3.3 offline fill observation writer must exist"
    return importlib.import_module("app.brokerage.fill_observation_writer")


def _fixture(path: Path, **overrides: object) -> Path:
    item = {
        "orderId": "ord_mock_" + "1" * 32,
        "providerExecRefHash": "a" * 64,
        "execType": "FILL",
        "fillQuantity": 2,
        "fillPriceKrw": 70000,
        "cumulativeQuantity": 2,
        "leavesQuantity": 0,
        "averageFillPriceKrw": 70000,
        "observedAt": "2026-07-27T01:00:00Z",
        "receivedAt": "2026-07-27T01:00:01Z",
        "completeness": "COMPLETE",
        "sourceRef": "fixture-s33-fill-001",
    }
    item.update(overrides)
    path.write_text(
        json.dumps(
            {
                "schemaVersion": "1",
                "sourceVersion": "s3.3-fill-observation-v1",
                "observations": [item],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_fill_fixture_is_sanitized_and_deterministic(tmp_path: Path) -> None:
    writer = _writer_module()

    observations = writer.load_fill_observation_fixture(_fixture(tmp_path / "fills.json"))

    assert len(observations) == 1
    assert observations[0].observation_id.startswith("ofo_")
    assert len(observations[0].observation_id) == 36
    assert observations[0].provider_exec_ref_hash == "a" * 64


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("providerExecRefHash", "raw-provider-exec-123"),
        ("execType", "UNKNOWN"),
        ("fillQuantity", -1),
        ("fillPriceKrw", None),
        ("cumulativeQuantity", 1),
        ("receivedAt", "2026-07-27T00:59:59Z"),
    ],
)
def test_fill_fixture_rejects_unbounded_or_inconsistent_values(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    writer = _writer_module()

    with pytest.raises(ValueError, match="fill observation"):
        writer.load_fill_observation_fixture(
            _fixture(tmp_path / f"{field}.json", **{field: value}),
        )


def test_fill_writer_has_no_provider_transport_import() -> None:
    writer = _writer_module()

    source = Path(writer.__file__).read_text(encoding="utf-8")
    assert "requests" not in source
    assert "httpx" not in source
    assert "KISMarketClient" not in source
    assert "VTTC0081R" not in source
    assert "VTSC9215R" not in source
