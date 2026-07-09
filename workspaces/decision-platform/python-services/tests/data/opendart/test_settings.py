from pathlib import Path

import pytest
from pydantic import ValidationError

from app.data.opendart.settings import OpenDARTSettings


def test_offline_mode_does_not_require_api_key(tmp_path: Path) -> None:
    settings = OpenDARTSettings(opendart_offline=True, opendart_data_dir=tmp_path, _env_file=None)

    assert settings.offline is True
    assert settings.api_key is None
    assert settings.base_url == "https://opendart.fss.or.kr"


def test_online_mode_requires_api_key(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        OpenDARTSettings(opendart_offline=False, opendart_data_dir=tmp_path, _env_file=None)


def test_rate_limit_must_be_positive(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        OpenDARTSettings(
            opendart_offline=True,
            opendart_rate_limit_per_second=0,
            opendart_data_dir=tmp_path,
            _env_file=None,
        )
