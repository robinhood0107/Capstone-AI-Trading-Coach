from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.data.ecos.settings import ECOSSettings


def test_defaults_are_lower_than_hard_safety_caps() -> None:
    settings = ECOSSettings(_env_file=None)

    assert settings.response_max_bytes == 512 * 1024
    assert settings.response_max_bytes <= 1024 * 1024
    assert settings.json_max_depth == 8
    assert settings.max_calls_per_run == 8
    assert settings.max_attempts_per_request == 2
    assert settings.connect_timeout_seconds == 2.0
    assert settings.read_timeout_seconds == 5.0


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ECOS_RESPONSE_MAX_BYTES", 1024 * 1024 + 1),
        ("ECOS_JSON_MAX_DEPTH", 13),
        ("ECOS_MAX_CALLS_PER_RUN", 9),
        ("ECOS_MAX_ATTEMPTS_PER_REQUEST", 3),
        ("ECOS_CONNECT_TIMEOUT_SECONDS", 3.1),
        ("ECOS_READ_TIMEOUT_SECONDS", 8.1),
    ],
)
def test_hard_safety_caps_cannot_be_raised(name: str, value: int | float) -> None:
    with pytest.raises(ValidationError):
        ECOSSettings(_env_file=None, **{name: value})


@pytest.mark.parametrize("attempts", [1, 2])
def test_attempt_limit_accepts_only_the_approved_lower_only_range(attempts: int) -> None:
    settings = ECOSSettings(_env_file=None, ECOS_MAX_ATTEMPTS_PER_REQUEST=attempts)

    assert settings.max_attempts_per_request == attempts


@pytest.mark.parametrize("attempts", [0, 3, True])
def test_attempt_limit_rejects_values_outside_one_to_two(attempts: int | bool) -> None:
    with pytest.raises(ValidationError):
        ECOSSettings(_env_file=None, ECOS_MAX_ATTEMPTS_PER_REQUEST=attempts)


def test_public_settings_never_contain_provider_credentials() -> None:
    settings = ECOSSettings(_env_file=None)

    dumped = settings.model_dump()
    rendered = repr(settings).lower()
    assert "api_key" not in dumped
    assert "credential" not in rendered
    assert "secret" not in rendered
