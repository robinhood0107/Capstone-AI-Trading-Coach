from __future__ import annotations

import json
import os
import stat
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.data.gdelt.cli import main, parse_args
from app.data.gdelt.collector import GdeltCollector
from app.data.gdelt.errors import GdeltAggregateError
from app.data.gdelt.policy import QueryDefinition
from app.data.gdelt.storage import publish_observation
from app.data.gdelt.transport import FixtureResponse, FixtureTransport


FIXTURE_ROOT = Path(__file__).with_name("fixtures")


@pytest.fixture
def posix_tmp_path() -> Iterator[Path]:
    """WSL의 Windows temp mount가 mode bits를 보존하지 않아 native tmp에서 검증한다."""

    with tempfile.TemporaryDirectory(prefix="gdelt-test-", dir="/tmp") as directory:
        yield Path(directory)


def _observation() -> dict[str, object]:
    transport = FixtureTransport(
        {
            "TIMELINE_TONE": FixtureResponse(
                (FIXTURE_ROOT / "timeline_tone.valid.json").read_bytes(),
                "application/json",
                False,
            ),
            "TIMELINE_VOL_RAW": FixtureResponse(
                (FIXTURE_ROOT / "timeline_vol_raw.valid.json").read_bytes(),
                "application/json",
                False,
            ),
        }
    )
    return GdeltCollector(transport=transport).collect(
        query=QueryDefinition(
            query_registry_id="global_semiconductor_stress_v1",
            aliases=("semiconductor",),
            entity_mapping_version="issuer_alias_v1",
            symbol="005930",
        ),
        window_start=datetime(2026, 7, 30, tzinfo=UTC),
        window_end=datetime(2026, 7, 31, tzinfo=UTC),
        observed_at=datetime(2026, 7, 31, tzinfo=UTC),
        received_at=datetime(2026, 7, 31, 0, 0, 1, tzinfo=UTC),
        available_at=datetime(2026, 7, 31, 0, 0, 2, tzinfo=UTC),
    )


def test_publish_observation_is_append_only_canonical_and_private(
    posix_tmp_path: Path,
) -> None:
    observation = _observation()

    published = publish_observation(root=posix_tmp_path / "gdelt", observation=observation)
    payload = json.loads(published.path.read_bytes())

    assert payload == observation
    assert stat.S_IMODE(os.lstat(published.path).st_mode) == 0o600
    assert published.path.name == f"{observation['artifactHash']}.json"
    assert published.path.parent.name == "31"
    with pytest.raises(GdeltAggregateError, match="ARTIFACT_CONFLICT"):
        publish_observation(root=posix_tmp_path / "gdelt", observation=observation)


def test_publish_observation_rejects_symlink_root(posix_tmp_path: Path) -> None:
    target = posix_tmp_path / "target"
    target.mkdir()
    root = posix_tmp_path / "gdelt"
    root.symlink_to(target, target_is_directory=True)

    with pytest.raises(GdeltAggregateError, match="STORAGE_UNSAFE"):
        publish_observation(root=root, observation=_observation())


def test_offline_is_cli_default_and_online_requires_exact_packet_fields() -> None:
    assert parse_args([]).mode == "offline"

    with pytest.raises(SystemExit):
        parse_args(["--mode", "online"])


def test_offline_cli_collects_fixture_and_can_publish(
    posix_tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output_root = posix_tmp_path / "published"

    assert main(["--output-root", str(output_root)]) == 0
    output = capsys.readouterr().out

    assert "status=AVAILABLE" in output
    assert "physicalProviderCalls=0" in output
    assert len(list(output_root.rglob("*.json"))) == 1


def test_source_contains_no_article_metadata_or_network_enabled_fixture_path() -> None:
    package = Path(__file__).resolve().parents[3] / "app/data/gdelt"
    source = "\n".join(
        (package / name).read_text(encoding="utf-8")
        for name in ("collector.py", "parser.py", "scoring.py", "transport.py")
    )

    forbidden = ("articleTitle", "articleUrl", "publisherUrl", "httpx.")
    assert all(token not in source for token in forbidden)
