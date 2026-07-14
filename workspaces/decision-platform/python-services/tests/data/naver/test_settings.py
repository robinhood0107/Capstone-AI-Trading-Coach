from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.data.naver.settings import NaverSettings


def test_legacy_defaults_are_bounded_and_secret_free() -> None:
    settings = NaverSettings(_env_file=None)

    assert settings.search_profile == "legacy"
    assert settings.batch_size == 4
    assert settings.display == 10
    assert settings.max_calls_per_run == 8
    assert settings.response_max_bytes == 512 * 1024
    assert "secret" not in repr(settings).lower()
    assert "client_id" not in settings.model_dump()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("NAVER_BATCH_SIZE", 5),
        ("NAVER_DISPLAY", 21),
        ("NAVER_MAX_CALLS_PER_RUN", 9),
        ("NAVER_RESPONSE_MAX_BYTES", 1024 * 1024 + 1),
        ("NAVER_JSON_MAX_DEPTH", 9),
    ],
)
def test_hard_caps_cannot_be_raised(name: str, value: int) -> None:
    with pytest.raises(ValidationError):
        NaverSettings(_env_file=None, **{name: value})
