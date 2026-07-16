from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.data.naver.settings import NaverSettings


_PYTHON_SERVICE_ROOT = Path(__file__).resolve().parents[3]


def test_legacy_defaults_are_bounded_and_secret_free() -> None:
    settings = NaverSettings(_env_file=None)

    assert settings.search_profile == "legacy"
    assert settings.batch_size == 4
    assert settings.display == 10
    assert settings.max_calls_per_run == 8
    assert settings.max_attempts_per_query == 2
    assert settings.response_max_bytes == 512 * 1024
    assert settings.connect_timeout_seconds == 2.0
    assert settings.read_timeout_seconds == 5.0
    assert settings.write_timeout_seconds == 2.0
    assert settings.pool_timeout_seconds == 1.0
    assert settings.logical_deadline_seconds == 12.0
    assert settings.snapshot_root == _PYTHON_SERVICE_ROOT / "data" / "source_snapshots"
    assert settings.snapshot_root.is_absolute()
    assert "secret" not in repr(settings).lower()
    assert "client_id" not in settings.model_dump()


def test_source_snapshot_root_uses_the_shared_secret_free_setting(tmp_path: Path) -> None:
    override = tmp_path / "operator-source-snapshots"

    settings = NaverSettings(_env_file=None, SOURCE_SNAPSHOT_ROOT=override)

    assert settings.snapshot_root == override
    assert "credential" not in repr(settings).lower()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("NAVER_BATCH_SIZE", 5),
        ("NAVER_DISPLAY", 21),
        ("NAVER_MAX_CALLS_PER_RUN", 9),
        ("NAVER_MAX_ATTEMPTS_PER_QUERY", 3),
        ("NAVER_RESPONSE_MAX_BYTES", 1024 * 1024 + 1),
        ("NAVER_JSON_MAX_DEPTH", 9),
        ("NAVER_CONNECT_TIMEOUT_SECONDS", 3.1),
        ("NAVER_READ_TIMEOUT_SECONDS", 8.1),
        ("NAVER_WRITE_TIMEOUT_SECONDS", 3.1),
        ("NAVER_POOL_TIMEOUT_SECONDS", 2.1),
        ("NAVER_LOGICAL_DEADLINE_SECONDS", 20.1),
    ],
)
def test_hard_caps_cannot_be_raised(name: str, value: int | float) -> None:
    with pytest.raises(ValidationError):
        NaverSettings(_env_file=None, **{name: value})


@pytest.mark.parametrize("batch_size", [1, 4])
def test_batch_size_accepts_smoke_and_operational_values(batch_size: int) -> None:
    settings = NaverSettings(_env_file=None, NAVER_BATCH_SIZE=batch_size)

    assert settings.batch_size == batch_size


@pytest.mark.parametrize("batch_size", [0, 5, True])
def test_batch_size_rejects_values_outside_one_to_four(batch_size: int | bool) -> None:
    with pytest.raises(ValidationError):
        NaverSettings(_env_file=None, NAVER_BATCH_SIZE=batch_size)


@pytest.mark.parametrize("attempts", [1, 2])
def test_attempt_limit_accepts_only_the_approved_lower_only_range(attempts: int) -> None:
    settings = NaverSettings(_env_file=None, NAVER_MAX_ATTEMPTS_PER_QUERY=attempts)

    assert settings.max_attempts_per_query == attempts


@pytest.mark.parametrize("attempts", [0, 3, True])
def test_attempt_limit_rejects_values_outside_one_to_two(attempts: int | bool) -> None:
    with pytest.raises(ValidationError):
        NaverSettings(_env_file=None, NAVER_MAX_ATTEMPTS_PER_QUERY=attempts)
