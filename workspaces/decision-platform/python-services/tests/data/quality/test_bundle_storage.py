from datetime import UTC, date, datetime
import errno
import hashlib
import json
import os
from pathlib import Path
from threading import Barrier, Lock, Thread

import pytest

from app.data.quality.kis_daily import load_quality_snapshot
from app.data.quality.metrics import analyze_quality
from app.data.quality.report import render_markdown, report_json_bytes
from app.data.quality.storage import QualityBundleStorageError, publish_quality_bundle
from tests.data.quality.helpers import prepare_snapshot


def _report(root: Path, *, revision: str = "7131f695293472ea16ee05322ed9b05f7b69d129"):
    identifiers = prepare_snapshot(root)
    snapshot = load_quality_snapshot(
        root=root,
        universe_identifier=identifiers.universe,
        dataset_identifier=identifiers.dataset,
        collection_identifier=identifiers.collection,
        window_start=date(2026, 7, 21),
        window_end=date(2026, 7, 21),
        evaluated_at=datetime(2026, 7, 21, 7, tzinfo=UTC),
        software_revision=revision,
    )
    return analyze_quality(snapshot.context, snapshot.datasets)


def test_bundle_is_private_complete_hashed_and_latest_points_only_after_completion(
    posix_tmp_path: Path,
) -> None:
    report = _report(posix_tmp_path)
    published = publish_quality_bundle(posix_tmp_path, report)

    assert published.created
    assert published.bundle_identifier.startswith("quality/2026/07/21/")
    assert published.bundle_path.stat().st_mode & 0o777 == 0o700
    expected_names = {"report.json", "report.md", "manifest.json"}
    assert {item.name for item in published.bundle_path.iterdir()} == expected_names
    for name in expected_names:
        assert (published.bundle_path / name).stat().st_mode & 0o777 == 0o600
    manifest_bytes = (published.bundle_path / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    assert (posix_tmp_path / "quality" / "latest-manifest.json").read_bytes() == manifest_bytes
    for entry in manifest["files"]:
        content = (published.bundle_path / entry["name"]).read_bytes()
        assert entry["byteSize"] == len(content)
        assert entry["sha256"] == hashlib.sha256(content).hexdigest()
    assert str(posix_tmp_path).encode() not in b"".join(
        (published.bundle_path / name).read_bytes() for name in expected_names
    )


def test_same_identity_is_verified_noop_and_corrupt_existing_bundle_is_never_overwritten(
    posix_tmp_path: Path,
) -> None:
    report = _report(posix_tmp_path)
    first = publish_quality_bundle(posix_tmp_path, report)
    second = publish_quality_bundle(posix_tmp_path, report)
    assert first.bundle_path == second.bundle_path
    assert not second.created

    latest_before = (posix_tmp_path / "quality" / "latest-manifest.json").read_bytes()
    (first.bundle_path / "report.md").write_text("corrupt", encoding="utf-8")
    with pytest.raises(QualityBundleStorageError, match="existing bundle"):
        publish_quality_bundle(posix_tmp_path, report)
    assert (first.bundle_path / "report.md").read_text(encoding="utf-8") == "corrupt"
    assert (posix_tmp_path / "quality" / "latest-manifest.json").read_bytes() == latest_before


@pytest.mark.parametrize("kind", ["symlink", "file", "fifo"])
def test_preexisting_unsafe_final_target_is_rejected(posix_tmp_path: Path, kind: str) -> None:
    report = _report(posix_tmp_path)
    day = posix_tmp_path / "quality" / "2026" / "07" / "21"
    day.mkdir(parents=True, mode=0o700)
    target = day / str(report.report_id)
    if kind == "symlink":
        target.symlink_to(posix_tmp_path / "daily", target_is_directory=True)
    elif kind == "file":
        target.write_bytes(b"unsafe")
    else:
        os.mkfifo(target)

    with pytest.raises(QualityBundleStorageError, match="existing bundle"):
        publish_quality_bundle(posix_tmp_path, report)


def test_hardlinked_bundle_file_is_rejected_on_idempotent_verification(
    posix_tmp_path: Path,
) -> None:
    report = _report(posix_tmp_path)
    published = publish_quality_bundle(posix_tmp_path, report)
    os.link(published.bundle_path / "report.json", posix_tmp_path / "report-alias.json")

    with pytest.raises(QualityBundleStorageError, match="existing bundle"):
        publish_quality_bundle(posix_tmp_path, report)


def test_partial_write_failure_preserves_last_good_and_does_not_publish_final(
    posix_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _report(posix_tmp_path)
    from app.data.quality import storage

    calls = 0
    original = storage._write_file

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError(errno.ENOSPC, "secret path must not escape")
        return original(*args, **kwargs)

    monkeypatch.setattr(storage, "_write_file", fail_second)
    with pytest.raises(QualityBundleStorageError, match="publish failed"):
        publish_quality_bundle(posix_tmp_path, report)

    final_path = (
        posix_tmp_path
        / "quality"
        / "2026"
        / "07"
        / "21"
        / str(report.report_id)
    )
    assert not final_path.exists()
    assert not (posix_tmp_path / "quality" / "latest-manifest.json").exists()


def test_stale_temp_is_ignored_and_concurrent_same_identity_converges(
    posix_tmp_path: Path,
) -> None:
    report = _report(posix_tmp_path)
    day = posix_tmp_path / "quality" / "2026" / "07" / "21"
    stale = day / ".quality-stale.tmp"
    stale.mkdir(parents=True, mode=0o700)
    (stale / "orphan").write_bytes(b"orphan")
    barrier = Barrier(2)
    guard = Lock()
    results = []
    errors: list[BaseException] = []

    def publish() -> None:
        barrier.wait(timeout=2)
        try:
            result = publish_quality_bundle(posix_tmp_path, report)
            with guard:
                results.append(result)
        except BaseException as error:
            with guard:
                errors.append(error)

    threads = [Thread(target=publish) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)

    assert not errors
    assert len(results) == 2
    assert sum(result.created for result in results) == 1
    assert stale.exists()
    assert (posix_tmp_path / "quality" / "latest-manifest.json").exists()


def test_serialized_bytes_given_to_publisher_match_report_renderers(
    posix_tmp_path: Path,
) -> None:
    report = _report(posix_tmp_path)
    published = publish_quality_bundle(posix_tmp_path, report)

    assert (published.bundle_path / "report.json").read_bytes() == report_json_bytes(report)
    assert (published.bundle_path / "report.md").read_bytes() == render_markdown(report)


def test_deadline_after_complete_rename_preserves_previous_latest(
    posix_tmp_path: Path,
) -> None:
    output = posix_tmp_path / "output"
    first = _report(posix_tmp_path / "first")
    second = _report(
        posix_tmp_path / "second",
        revision="8131f695293472ea16ee05322ed9b05f7b69d129",
    )
    publish_quality_bundle(output, first)
    latest = output / "quality" / "latest-manifest.json"
    last_good = latest.read_bytes()
    second_final = output / "quality" / "2026" / "07" / "21" / str(second.report_id)

    def expire_after_final_rename() -> None:
        if second_final.exists():
            raise ValueError("quality report deadline exceeded")

    with pytest.raises(QualityBundleStorageError, match="publish failed"):
        publish_quality_bundle(output, second, deadline_check=expire_after_final_rename)

    assert second_final.exists()
    assert latest.read_bytes() == last_good
