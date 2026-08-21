from __future__ import annotations

import importlib.util


def test_s67_python_runtime_modules_are_not_packaged() -> None:
    for module in (
        "app.cross_market.s67_materializer",
        "app.cross_market.s67_repository",
    ):
        assert importlib.util.find_spec(module) is None
