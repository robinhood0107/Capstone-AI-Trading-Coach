from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.data.krx.settings import KrxOpenApiSettings


_OFFICIAL_ORIGIN = "https://data-dbg.krx.co.kr"


def test_defaults_are_two_call_bounded_and_secret_free() -> None:
    settings = KrxOpenApiSettings(_env_file=None)

    assert settings.origin == _OFFICIAL_ORIGIN
    assert settings.max_calls_per_run == 2
    assert settings.max_attempts_per_request == 1
    assert settings.response_max_bytes == 4 * 1024 * 1024
    assert settings.json_max_depth == 4
    assert settings.json_max_rows == 5_000
    assert settings.connect_timeout_seconds == 2.0
    assert settings.read_timeout_seconds == 120.0
    assert settings.write_timeout_seconds == 2.0
    assert settings.pool_timeout_seconds == 1.0
    assert settings.logical_deadline_seconds == 260.0

    dumped_keys = {key.lower() for key in settings.model_dump()}
    rendered = repr(settings).lower()
    for forbidden in ("auth", "credential", "secret", "api_key"):
        assert all(forbidden not in key for key in dumped_keys)
        assert forbidden not in rendered


def test_official_origin_is_source_controlled_and_cannot_be_overridden() -> None:
    settings = KrxOpenApiSettings(
        _env_file=None,
        KRX_OPENAPI_ORIGIN="https://attacker.invalid",
    )

    assert settings.origin == _OFFICIAL_ORIGIN
    assert "attacker" not in repr(settings).lower()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("KRX_OPENAPI_MAX_CALLS_PER_RUN", 3),
        ("KRX_OPENAPI_MAX_ATTEMPTS_PER_REQUEST", 2),
        ("KRX_OPENAPI_RESPONSE_MAX_BYTES", 4 * 1024 * 1024 + 1),
        ("KRX_OPENAPI_JSON_MAX_DEPTH", 5),
        ("KRX_OPENAPI_JSON_MAX_ROWS", 5_001),
        ("KRX_OPENAPI_CONNECT_TIMEOUT_SECONDS", 2.1),
        ("KRX_OPENAPI_READ_TIMEOUT_SECONDS", 120.1),
        ("KRX_OPENAPI_READ_TIMEOUT_SECONDS", "120.1"),
        ("KRX_OPENAPI_WRITE_TIMEOUT_SECONDS", 2.1),
        ("KRX_OPENAPI_POOL_TIMEOUT_SECONDS", 1.1),
        ("KRX_OPENAPI_LOGICAL_DEADLINE_SECONDS", 260.1),
        ("KRX_OPENAPI_LOGICAL_DEADLINE_SECONDS", "260.1"),
    ],
)
def test_hard_safety_caps_cannot_be_raised(name: str, value: object) -> None:
    with pytest.raises(ValidationError):
        KrxOpenApiSettings(_env_file=None, **{name: value})


@pytest.mark.parametrize(
    "name",
    [
        "KRX_OPENAPI_CONNECT_TIMEOUT_SECONDS",
        "KRX_OPENAPI_READ_TIMEOUT_SECONDS",
        "KRX_OPENAPI_WRITE_TIMEOUT_SECONDS",
        "KRX_OPENAPI_POOL_TIMEOUT_SECONDS",
        "KRX_OPENAPI_LOGICAL_DEADLINE_SECONDS",
    ],
)
def test_timeout_limits_reject_boolean_values(name: str) -> None:
    with pytest.raises(ValidationError):
        KrxOpenApiSettings(_env_file=None, **{name: True})


@pytest.mark.parametrize("run_cap", [1, 2])
def test_run_cap_accepts_only_the_approved_lower_only_range(run_cap: int) -> None:
    settings = KrxOpenApiSettings(
        _env_file=None,
        KRX_OPENAPI_MAX_CALLS_PER_RUN=run_cap,
    )

    assert settings.max_calls_per_run == run_cap


@pytest.mark.parametrize("run_cap", [0, 3, True])
def test_run_cap_rejects_values_outside_one_to_two(run_cap: int | bool) -> None:
    with pytest.raises(ValidationError):
        KrxOpenApiSettings(
            _env_file=None,
            KRX_OPENAPI_MAX_CALLS_PER_RUN=run_cap,
        )


@pytest.mark.parametrize("attempts", [0, 2, True])
def test_attempt_limit_is_exactly_one(attempts: int | bool) -> None:
    with pytest.raises(ValidationError):
        KrxOpenApiSettings(
            _env_file=None,
            KRX_OPENAPI_MAX_ATTEMPTS_PER_REQUEST=attempts,
        )


def test_response_shape_and_timeout_limits_can_only_be_lowered() -> None:
    settings = KrxOpenApiSettings(
        _env_file=None,
        KRX_OPENAPI_RESPONSE_MAX_BYTES=1024,
        KRX_OPENAPI_JSON_MAX_DEPTH=3,
        KRX_OPENAPI_JSON_MAX_ROWS=30,
        KRX_OPENAPI_CONNECT_TIMEOUT_SECONDS=1.0,
        KRX_OPENAPI_READ_TIMEOUT_SECONDS=2.0,
        KRX_OPENAPI_WRITE_TIMEOUT_SECONDS=1.0,
        KRX_OPENAPI_POOL_TIMEOUT_SECONDS=0.5,
        KRX_OPENAPI_LOGICAL_DEADLINE_SECONDS=5.0,
    )

    assert settings.response_max_bytes == 1024
    assert settings.json_max_depth == 3
    assert settings.json_max_rows == 30
    assert settings.connect_timeout_seconds == 1.0
    assert settings.read_timeout_seconds == 2.0
    assert settings.write_timeout_seconds == 1.0
    assert settings.pool_timeout_seconds == 0.5
    assert settings.logical_deadline_seconds == 5.0


@pytest.mark.parametrize(
    ("read_timeout", "logical_deadline"),
    [(120.0, 260.0), (90.0, 200.0), (30.0, 70.0)],
)
def test_staged_probe_timeout_overrides_accept_new_cap_and_lower_values(
    read_timeout: float,
    logical_deadline: float,
) -> None:
    settings = KrxOpenApiSettings(
        _env_file=None,
        KRX_OPENAPI_READ_TIMEOUT_SECONDS=read_timeout,
        KRX_OPENAPI_LOGICAL_DEADLINE_SECONDS=logical_deadline,
    )

    assert settings.read_timeout_seconds == read_timeout
    assert settings.logical_deadline_seconds == logical_deadline
