from __future__ import annotations

import json
import socket
import tempfile
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import grpc
import pytest
from grpc_health.v1 import health_pb2, health_pb2_grpc

from app.data._shared.canonical_json import canonical_json_bytes
from app.p1_owner.assets import FEATURE_ORDER, build_golden_bundle
from app.p1_owner.inference import ReturnInferenceError, ReturnInferenceModel
from app.p1_owner.importer import validate_artifact_bundle
from app.p1_owner.inference_grpc_server import (
    METHOD_PATH,
    ReturnInferenceSettings,
    create_server,
)
from tests.p1_owner.test_assets import GOLDEN_SESSION_DATE, universe_catalog


@pytest.fixture(scope="module")
def model_and_request() -> Iterator[tuple[ReturnInferenceModel, bytes, Path]]:
    with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
        root = Path(temporary)
        golden_root = root / "golden"
        result = build_golden_bundle(
            universe_catalog=universe_catalog(),
            session_date=GOLDEN_SESSION_DATE,
            output_root=golden_root,
        )
        model = ReturnInferenceModel.load(
            bundle_root=golden_root,
            manifest_sha256=result.manifest_sha256,
            allow_synthetic=True,
        )
        rows = []
        for index, symbol in enumerate(model.symbols):
            current_close = 10_000.0 + index
            features = [[0.0 for _ in FEATURE_ORDER] for _ in range(20)]
            for feature_row in features:
                feature_row[FEATURE_ORDER.index("raw_close")] = current_close
            rows.append(
                {
                    "currentClose": current_close,
                    "features": features,
                    "sessionDate": "2026-01-05",
                    "symbol": symbol,
                }
            )
        request = canonical_json_bytes(
            {
                "artifactId": model.artifact_id,
                "bundleSha256": model.bundle_sha256,
                "contractId": "p1-return-inference-request.v1",
                "rows": rows,
                "sessionDate": "2026-01-05",
            }
        )
        yield model, request, golden_root


def test_fixed_lstm_kernel_is_deterministic_exact31_and_provider_free(
    model_and_request: tuple[ReturnInferenceModel, bytes, Path],
) -> None:
    model, request, _golden_root = model_and_request
    first = model.infer_bytes(request)
    second = model.infer_bytes(request)
    assert first == second
    response = json.loads(first)
    assert response["contractId"] == "p1-return-inference-response.v1"
    assert response["artifactId"] == model.artifact_id
    assert response["orderAuthority"] == "NONE"
    assert response["providerCalls"] == 0
    assert len(response["predictions"]) == 31
    assert {row["symbol"] for row in response["predictions"]} == set(model.symbols)
    assert {row["signal"] for row in response["predictions"]} == {"SELL"}
    assert {row["forecastClose"] for row in response["predictions"]} == {0.0}
    assert {row["expectedReturn"] for row in response["predictions"]} == {-1.0}


def test_inference_rejects_noncanonical_wrong_shape_and_synthetic_production_load(
    model_and_request: tuple[ReturnInferenceModel, bytes, Path],
) -> None:
    model, request, golden_root = model_and_request
    with pytest.raises(ReturnInferenceError, match="canonical"):
        model.infer_bytes(json.dumps(json.loads(request), indent=2).encode())
    payload = json.loads(request)
    payload["rows"] = payload["rows"][:-1]
    with pytest.raises(ReturnInferenceError, match="exact-31"):
        model.infer_bytes(canonical_json_bytes(payload))
    with pytest.raises(ReturnInferenceError, match="synthetic bundle is disabled"):
        ReturnInferenceModel.load(
            bundle_root=golden_root,
            manifest_sha256=model.bundle_sha256,
            allow_synthetic=False,
        )


def test_below_baseline_real_bundle_is_runtime_eligible_when_integrity_passes(
    model_and_request: tuple[ReturnInferenceModel, bytes, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model, _request, golden_root = model_and_request
    validated = validate_artifact_bundle(
        bundle_root=golden_root,
        expected_manifest_sha256=model.bundle_sha256,
    )
    monkeypatch.setattr(
        "app.p1_owner.inference.validate_artifact_bundle",
        lambda **_kwargs: replace(
            validated,
            evidence_mode="REAL_TEAM_B",
            real_team_b=True,
            model_quality="BELOW_BASELINE",
            mock_runtime_eligible=True,
        ),
    )
    loaded = ReturnInferenceModel.load(
        bundle_root=golden_root,
        manifest_sha256=model.bundle_sha256,
        allow_synthetic=False,
    )
    assert loaded.bundle_sha256 == model.bundle_sha256


def test_loopback_grpc_requires_auth_deadline_and_returns_bounded_bytes(
    model_and_request: tuple[ReturnInferenceModel, bytes, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model, request_bytes, _golden_root = model_and_request
    port = _free_port()
    secret = "return-inference-test-secret-0001"
    monkeypatch.setattr(
        ReturnInferenceModel,
        "load",
        classmethod(lambda cls, **_kwargs: model),
    )
    settings = ReturnInferenceSettings(
        bind_address=f"127.0.0.1:{port}",
        shared_secret=secret,
        bundle_root=Path("/tmp/fixture"),
        manifest_sha256=model.bundle_sha256,
        allow_synthetic=True,
    )
    server = create_server(settings)
    server.start()
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    infer = channel.unary_unary(
        METHOD_PATH,
        request_serializer=lambda value: value,
        response_deserializer=lambda value: value,
    )
    try:
        response = infer(
            request_bytes,
            timeout=2,
            metadata=(("x-return-inference-auth", secret),),
        )
        assert len(response) < 64 * 1024
        assert json.loads(response)["providerCalls"] == 0
        with pytest.raises(grpc.RpcError) as unauthenticated:
            infer(request_bytes, timeout=2)
        assert unauthenticated.value.code() == grpc.StatusCode.UNAUTHENTICATED
        with pytest.raises(grpc.RpcError) as unbounded:
            infer(
                request_bytes,
                timeout=10,
                metadata=(("x-return-inference-auth", secret),),
            )
        assert unbounded.value.code() == grpc.StatusCode.DEADLINE_EXCEEDED
    finally:
        channel.close()
        server.stop(grace=0).wait(timeout=2)


def test_server_settings_reject_non_loopback_and_partial_model_binding() -> None:
    with pytest.raises(ValueError, match="loopback"):
        ReturnInferenceSettings("0.0.0.0:50057", "x" * 32, None, None, False).validate()
    with pytest.raises(ValueError, match="paired"):
        ReturnInferenceSettings(
            "127.0.0.1:50057", "x" * 32, Path("/tmp/model"), None, False
        ).validate()


def test_no_pointer_server_stays_healthy_and_inference_fails_closed() -> None:
    port = _free_port()
    secret = "return-inference-test-secret-0002"
    server = create_server(ReturnInferenceSettings(f"127.0.0.1:{port}", secret, None, None, False))
    server.start()
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    infer = channel.unary_unary(
        METHOD_PATH,
        request_serializer=lambda value: value,
        response_deserializer=lambda value: value,
    )
    try:
        health = health_pb2_grpc.HealthStub(channel).Check(
            health_pb2.HealthCheckRequest(
                service="capstone.return_inference.v1.ReturnInferenceService"
            ),
            timeout=2,
        )
        assert health.status == health_pb2.HealthCheckResponse.SERVING
        with pytest.raises(grpc.RpcError) as unavailable:
            infer(
                b"{}",
                timeout=2,
                metadata=(("x-return-inference-auth", secret),),
            )
        assert unavailable.value.code() == grpc.StatusCode.FAILED_PRECONDITION
    finally:
        channel.close()
        server.stop(grace=0).wait(timeout=2)


def test_inference_modules_have_no_provider_database_or_order_transport() -> None:
    root = Path(__file__).parents[2] / "app" / "p1_owner"
    source = (root / "inference.py").read_text() + (root / "inference_grpc_server.py").read_text()
    for forbidden in (
        "app.data.kis",
        "app.data.krx",
        "app.data.ecos",
        "app.brokerage",
        "psycopg",
        "httpx",
        "requests",
        "urllib",
    ):
        assert forbidden not in source


def test_compose_supervisor_health_and_secret_boundary_include_return_inference() -> None:
    repository = Path(__file__).parents[5]
    supervisor = (repository / "deploy/p1/docker/decision-platform-supervisor.py").read_text()
    health = (repository / "deploy/p1/docker/decision-platform-health.py").read_text()
    compose = (repository / "deploy/p1/compose.yml").read_text()
    p1ctl = (repository / "deploy/p1/p1ctl").read_text()
    full_app = (repository / "deploy/p1/full-appctl").read_text()
    entrypoint = (repository / "deploy/p1/docker/secret-entrypoint.sh").read_text()
    assert '"app.p1_owner.inference_grpc_server"' in supervisor
    assert "worker, inference, spring" in supervisor
    assert "RETURN_INFERENCE_SERVICE" in health
    assert "127.0.0.1:50057" in health
    assert "RETURN_INFERENCE_GRPC_BIND_ADDRESS: 127.0.0.1:50057" in compose
    assert 'RETURN_INFERENCE_ALLOW_SYNTHETIC: "false"' in compose
    assert "RETURN_INFERENCE_GRPC_SHARED_SECRET" in p1ctl
    assert "RETURN_INFERENCE_GRPC_SHARED_SECRET" in full_app
    assert "decision-platform:RETURN_INFERENCE_GRPC_SHARED_SECRET" in entrypoint
    assert "return_inference_env" in compose
    assert "return-inference.env" in p1ctl


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])
