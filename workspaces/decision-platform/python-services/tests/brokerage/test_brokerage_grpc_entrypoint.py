from __future__ import annotations

import importlib


def test_brokerage_grpc_console_entrypoint_exports_serve() -> None:
    module = importlib.import_module("app.brokerage.brokerage_grpc_server")

    assert callable(module.serve)
    assert module.main is module.serve
