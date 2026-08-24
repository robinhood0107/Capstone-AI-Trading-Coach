from __future__ import annotations

import importlib.util
import math
from pathlib import Path
from types import ModuleType

from app.rag.rag_v2_grpc_server import RagV2GrpcServerSettings
from app.rag.rag_v2_rpc import BgeRagV2RetrievalOnlyEngine

_FIXTURE_SERVER_PATH = (
    Path(__file__).resolve().parents[1] / "support" / "rag_v2_fixture_grpc_server.py"
)
_SECRET = "rag-v2-grpc-shared-secret-for-fixture-server-tests-0001"


def test_fixture_embedder_is_deterministic_1024d_unit_and_keeps_the_local_bge_profile() -> None:
    module = _load_fixture_module()
    embedder = module.DeterministicRagV2FixtureQueryEmbedder()

    first = tuple(embedder.embed_query("fixture query text"))
    second = tuple(embedder.embed_query("fixture query text"))

    assert embedder.embedding_profile_id == "bge_m3_local_1024_v1"
    assert first == second
    assert len(first) == 1024
    assert set(first) <= {0.0, 1.0}
    assert math.fsum(value * value for value in first) == 1.0


def test_fixture_server_reuses_production_settings_and_query_path_without_a_bge_loader(
    monkeypatch,
) -> None:
    module = _load_fixture_module()
    captured: dict[str, object] = {}

    class _Adapter:
        def __init__(self, *, database_dsn: str) -> None:
            captured["database_dsn"] = database_dsn

    def _create_server(settings: object, engine: object) -> object:
        captured["settings"] = settings
        captured["engine"] = engine
        return object()

    monkeypatch.setitem(module.__dict__, "PsycopgRagV2AuthorizedRetrievalAdapter", _Adapter)
    monkeypatch.setitem(module.__dict__, "create_rag_v2_server", _create_server)
    settings = RagV2GrpcServerSettings(
        bind_address="127.0.0.1:50054",
        shared_secret=_SECRET,
        query_database_dsn="postgresql://decision_rag_query@localhost/rag",
        # The fixture validates but never opens the production-shaped packet-root setting.
        bge_packet_root=Path("/unused-fixture-bge-packet"),
    )

    result = module.create_fixture_rag_v2_server(settings)

    assert result is not None
    assert captured["settings"] is settings
    assert captured["database_dsn"] == settings.query_database_dsn
    assert isinstance(captured["engine"], BgeRagV2RetrievalOnlyEngine)
    assert "load_bge_onnx_embedder" not in _FIXTURE_SERVER_PATH.read_text(encoding="utf-8")
    assert "LocalBgeQueryEmbedder" not in _FIXTURE_SERVER_PATH.read_text(encoding="utf-8")


def _load_fixture_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "rag_v2_fixture_grpc_server_test", _FIXTURE_SERVER_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
