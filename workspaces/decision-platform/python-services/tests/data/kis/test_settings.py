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
