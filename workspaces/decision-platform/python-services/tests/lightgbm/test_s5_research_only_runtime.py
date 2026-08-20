from __future__ import annotations

import pytest

from app.lightgbm import (
    bootstrap_execute_cli,
    daily_execute_cli,
    production_stage_cli,
    rollback_batch_cli,
    tick_cli,
)


@pytest.fixture
def forbidden_runtime_access(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("research-only command touched production authority")

    for module in (
        bootstrap_execute_cli,
        daily_execute_cli,
        production_stage_cli,
        rollback_batch_cli,
        tick_cli,
    ):
        monkeypatch.setattr(module.os.environ, "get", forbidden)


def test_production_stage_is_closed_before_authority_or_database_access(
    capsys: pytest.CaptureFixture[str], forbidden_runtime_access: None
) -> None:
    assert production_stage_cli.main() == 2
    assert capsys.readouterr().out.strip() == "S5_PRODUCTION_STAGE=RESEARCH_ONLY"


def test_daily_inference_and_publication_is_closed_before_provider_access(
    capsys: pytest.CaptureFixture[str], forbidden_runtime_access: None
) -> None:
    assert daily_execute_cli.main() == 2
    assert capsys.readouterr().out.strip() == "S5_DAILY_REFRESH=RESEARCH_ONLY"


def test_rollback_batch_is_closed(
    capsys: pytest.CaptureFixture[str], forbidden_runtime_access: None
) -> None:
    assert rollback_batch_cli.main() == 2
    assert capsys.readouterr().out.strip() == "S5_ROLLBACK_BATCH=RESEARCH_ONLY"


def test_bootstrap_is_closed_before_root_quota_or_provider_access(
    capsys: pytest.CaptureFixture[str], forbidden_runtime_access: None
) -> None:
    assert bootstrap_execute_cli.main() == 2
    assert capsys.readouterr().out.strip() == "S5_BOOTSTRAP=RESEARCH_ONLY"


def test_tick_is_closed_as_normal_no_progress_before_authority_access(
    capsys: pytest.CaptureFixture[str], forbidden_runtime_access: None
) -> None:
    assert tick_cli.main() == tick_cli.EXIT_NO_PROGRESS
    assert capsys.readouterr().out.strip() == "S5_TICK=RESEARCH_ONLY"
