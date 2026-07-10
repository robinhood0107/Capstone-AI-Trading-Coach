from pathlib import Path

import pytest
from pydantic import ValidationError

from app.data.kis.settings import KISSettings


def test_offline_mode_does_not_require_credentials(tmp_path: Path) -> None:
    settings = KISSettings(kis_offline=True, kis_data_dir=tmp_path, _env_file=None)

    assert settings.offline is True
    assert settings.mode == "mock"
    assert settings.base_url == "https://openapivts.koreainvestment.com:29443"


def test_live_mode_uses_live_read_only_domain_without_credential_fields(tmp_path: Path) -> None:
    settings = KISSettings(kis_mode="live", kis_data_dir=tmp_path, _env_file=None)

    assert settings.mode == "live"
    assert settings.base_url == "https://openapi.koreainvestment.com:9443"
    serialized = settings.model_dump()
    assert all(marker not in key.lower() for key in serialized for marker in ("key", "secret", "account", "password"))


def test_online_non_secret_settings_do_not_read_or_validate_credentials(tmp_path: Path) -> None:
    settings = KISSettings(kis_mode="mock", kis_offline=False, kis_data_dir=tmp_path, _env_file=None)

    assert settings.offline is False
    assert "credential" not in repr(settings).lower()


def test_rate_limit_must_be_positive(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        KISSettings(
            kis_offline=True,
            kis_rate_limit_per_second=0,
            kis_data_dir=tmp_path,
            _env_file=None,
        )


@pytest.mark.parametrize(
    ("mode", "rate"),
    [("mock", 1.0001), ("live", 18.0001)],
)
def test_provider_hard_rate_limit_cannot_be_raised(
    tmp_path: Path,
    mode: str,
    rate: float,
) -> None:
    with pytest.raises(ValidationError, match="official REST limit"):
        KISSettings(
            kis_mode=mode,
            kis_offline=True,
            kis_rate_limit_per_second=rate,
            kis_data_dir=tmp_path,
            _env_file=None,
        )


def test_mode_defaults_enforce_mock_and_live_pacing(tmp_path: Path) -> None:
    mock = KISSettings(kis_mode="mock", kis_offline=True, kis_data_dir=tmp_path, _env_file=None)
    live = KISSettings(kis_mode="live", kis_offline=True, kis_data_dir=tmp_path, _env_file=None)

    assert mock.rate_limit_per_second == 1.0
    assert mock.request_interval_seconds == 1.0
    assert live.rate_limit_per_second == 18.0
    assert live.request_interval_seconds == 0.12


@pytest.mark.parametrize(
    ("mode", "interval_ms"),
    [("mock", 999), ("live", 99)],
)
def test_provider_minimum_request_interval_cannot_be_shortened(
    tmp_path: Path,
    mode: str,
    interval_ms: int,
) -> None:
    with pytest.raises(ValidationError, match="minimum request interval"):
        KISSettings(
            kis_mode=mode,
            kis_offline=True,
            kis_request_interval_milliseconds=interval_ms,
            kis_data_dir=tmp_path,
            _env_file=None,
        )


def test_lower_operator_rate_override_only_slows_requests(tmp_path: Path) -> None:
    settings = KISSettings(
        kis_mode="live",
        kis_offline=True,
        kis_rate_limit_per_second=2,
        kis_request_interval_milliseconds=120,
        kis_data_dir=tmp_path,
        _env_file=None,
    )

    assert settings.request_interval_seconds == 0.5


@pytest.mark.parametrize("wait_seconds", [8.0, 10.001])
def test_rate_limit_wait_must_preserve_io_budget_and_ten_second_ceiling(
    tmp_path: Path,
    wait_seconds: float,
) -> None:
    with pytest.raises(ValidationError):
        KISSettings(
            kis_offline=True,
            kis_rate_limit_max_wait_seconds=wait_seconds,
            kis_data_dir=tmp_path,
            _env_file=None,
        )


def test_provider_http_timeout_cannot_outlive_token_singleflight_lease(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        KISSettings(
            kis_offline=True,
            kis_timeout_seconds=10.001,
            kis_data_dir=tmp_path,
            _env_file=None,
        )
