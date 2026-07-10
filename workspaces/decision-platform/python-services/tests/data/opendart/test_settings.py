from pathlib import Path

import pytest
from pydantic import ValidationError

from app.data.opendart.settings import OpenDARTSettings


def test_runtime_settings_never_expose_authentication_material(tmp_path: Path) -> None:
    settings = OpenDARTSettings(opendart_offline=True, opendart_data_dir=tmp_path, _env_file=None)

    assert settings.offline is True
    assert settings.base_url == "https://opendart.fss.or.kr"
    assert not hasattr(settings, "api_key")
    assert not hasattr(settings, "opendart_api_key")
    assert "api_key" not in repr(settings).lower()
    assert "api_key" not in settings.model_dump()


def test_online_runtime_settings_do_not_read_authentication_material(tmp_path: Path) -> None:
    settings = OpenDARTSettings(opendart_offline=False, opendart_data_dir=tmp_path, _env_file=None)

    assert settings.offline is False
    assert set(settings.model_dump()) == {
        "opendart_offline",
        "opendart_rate_limit_per_second",
        "opendart_timeout_seconds",
        "opendart_retry_attempts",
        "opendart_data_dir",
    }


def test_official_origin_cannot_be_overridden_by_runtime_input(tmp_path: Path) -> None:
    settings = OpenDARTSettings(
        opendart_offline=True,
        opendart_base_url="https://attacker.invalid",
        opendart_data_dir=tmp_path,
        _env_file=None,
    )

    assert settings.base_url == "https://opendart.fss.or.kr"


def test_rate_limit_must_be_positive(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        OpenDARTSettings(
            opendart_offline=True,
            opendart_rate_limit_per_second=0,
            opendart_data_dir=tmp_path,
            _env_file=None,
        )
