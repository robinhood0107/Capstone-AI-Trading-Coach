from __future__ import annotations

import importlib.util
import tomllib
from pathlib import Path


def test_s66_runtime_modules_and_cli_are_not_packaged() -> None:
    for module in (
        "app.financial_engineering.event_study",
        "app.financial_engineering.lightgbm_replay",
        "app.financial_engineering.replay_cli",
    ):
        assert importlib.util.find_spec(module) is None

    project_root = Path(__file__).resolve().parents[2]
    configuration = tomllib.loads((project_root / "pyproject.toml").read_text())
    assert "s6-research-replay" not in configuration["project"]["scripts"]
