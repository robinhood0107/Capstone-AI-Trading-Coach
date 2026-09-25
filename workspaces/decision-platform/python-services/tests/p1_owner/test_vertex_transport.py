"""실 Vertex transport는 요청에 담긴 등록 근거만 host 사실로 인정하고, 실패는 전부 fail-closed다."""

from __future__ import annotations

import base64
import json
import os
from typing import Any
from unittest.mock import Mock

import httpx
import pytest

from app.data._shared.canonical_json import canonical_json_bytes
from app.p1_owner.vertex_transport import (
    VertexAiVetoTransport,
    VertexTransportNotConfigured,
    VertexTransportSettings,
    _grounding_sources_from_request,
    _model_json_bytes,
)
from app.p1_owner.vertex_veto import (
    VertexBudgetExhausted,
    VertexVetoRequestError,
)

_EVIDENCE = [
    {
        "boundedQuote": "금융감독원이 제재 절차에 착수했다고 밝혔다.",
        "sourceEventDate": "2026-08-27",
        "sourceId": "src_official_dart",
        "sourceType": "OFFICIAL_PRIMARY",
    }
]


def _request(evidence: list[dict[str, Any]] | None = None) -> bytes:
    return canonical_json_bytes(
        {
            "candidate": {"symbol": "005930"},
            "publicEvidence": _EVIDENCE if evidence is None else evidence,
        }
    )


def _credential_info(project: str = "capstone-demo") -> dict[str, str]:
    return {
        "type": "service_account",
        "project_id": project,
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_email": "vertex-test@capstone-demo.iam.gserviceaccount.com",
        "private_key_id": "a" * 40,
        "private_key": "test-private-key",
    }


def _settings(tmp_path: Any) -> VertexTransportSettings:
    info = _credential_info()
    return VertexTransportSettings(service_account_info=info, project_id=info["project_id"])


def test_vertex_settings_read_root_env_base64(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    encoded = base64.b64encode(json.dumps(_credential_info()).encode()).decode()
    monkeypatch.setenv("MARS_VERTEX_SERVICE_ACCOUNT_JSON_B64", encoded)

    settings = VertexTransportSettings.from_environment()

    assert settings is not None
    assert settings.project_id == "capstone-demo"
    assert settings.service_account_info["type"] == "service_account"
    assert os.environ["MARS_VERTEX_SERVICE_ACCOUNT_JSON_B64"] == encoded


def test_vertex_settings_reject_invalid_root_env_base64(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARS_VERTEX_SERVICE_ACCOUNT_JSON_B64", "not-base64")

    with pytest.raises(VertexTransportNotConfigured, match="VERTEX_CREDENTIAL_ENV_INVALID"):
        VertexTransportSettings.from_environment()


def test_grounding_sources_come_from_the_request_not_the_model() -> None:
    sources, count = _grounding_sources_from_request(_request())

    assert count == 1
    assert sources["src_official_dart"].source_event_date == "2026-08-27"
    assert sources["src_official_dart"].source_type == "OFFICIAL_PRIMARY"
    assert sources["src_official_dart"].support_texts == (_EVIDENCE[0]["boundedQuote"],)


@pytest.mark.parametrize(
    "evidence",
    [
        # 같은 출처가 두 번
        _EVIDENCE + _EVIDENCE,
        # 필드 누락
        [{"sourceId": "src_official_dart"}],
        # 항목이 객체가 아님
        ["not-an-object"],
    ],
)
def test_malformed_request_evidence_is_rejected(evidence: list[Any]) -> None:
    with pytest.raises(VertexVetoRequestError):
        _grounding_sources_from_request(_request(evidence))


def test_no_registered_evidence_never_calls_the_provider(tmp_path: Any) -> None:
    transport = VertexAiVetoTransport(settings=_settings(tmp_path))

    with pytest.raises(VertexBudgetExhausted):
        transport.invoke(system_prompt="prompt", request_bytes=_request([]))

    assert transport.physical_calls == 0


def test_session_call_cap_stops_further_provider_calls(tmp_path: Any) -> None:
    transport = VertexAiVetoTransport(settings=_settings(tmp_path), session_call_cap=0)

    with pytest.raises(VertexBudgetExhausted):
        transport.invoke(system_prompt="prompt", request_bytes=_request())

    assert transport.physical_calls == 0


def test_public_full_without_usage_meter_still_calls_vertex(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MARS_PUBLIC_SURFACE_MODE", "FULL")
    transport = VertexAiVetoTransport(settings=_settings(tmp_path))
    send = Mock()
    monkeypatch.setattr(
        VertexAiVetoTransport,
        "_post",
        lambda self, payload: (
            send(payload)
            and {"candidates": [{"content": {"parts": [{"text": '{"status":"ABSTAIN"}'}]}}]}
        ),
    )

    result = transport.invoke(system_prompt="prompt", request_bytes=_request())

    assert transport.physical_calls == 1
    send.assert_called_once()
    assert result.provider_call_count == 1


def test_public_full_meter_failure_does_not_block_vertex_call(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MARS_PUBLIC_SURFACE_MODE", "FULL")
    meter = Mock()
    meter.record.side_effect = RuntimeError("measurement unavailable")
    transport = VertexAiVetoTransport(
        settings=_settings(tmp_path),
        owner_user_id="usr_alice",
        run_id="run_alice",
        usage_meter=meter,
    )
    send = Mock()
    monkeypatch.setattr(
        VertexAiVetoTransport,
        "_post",
        lambda self, payload: (
            send(payload)
            and {"candidates": [{"content": {"parts": [{"text": '{"status":"ABSTAIN"}'}]}}]}
        ),
    )

    result = transport.invoke(system_prompt="prompt", request_bytes=_request())

    meter.record.assert_called_once()
    assert transport.physical_calls == 1
    send.assert_called_once()
    assert result.provider_call_count == 1


def test_demo_cannot_run_trade_ai_even_with_a_usage_meter(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MARS_PUBLIC_SURFACE_MODE", "DEMO")
    meter = Mock()
    transport = VertexAiVetoTransport(settings=_settings(tmp_path), usage_meter=meter)
    send = Mock()
    monkeypatch.setattr(VertexAiVetoTransport, "_post", lambda self, payload: send(payload))

    with pytest.raises(VertexBudgetExhausted):
        transport.invoke(system_prompt="prompt", request_bytes=_request())

    meter.record.assert_not_called()
    assert transport.physical_calls == 0
    send.assert_not_called()


def test_generate_url_pins_the_global_endpoint_the_contract_allows(tmp_path: Any) -> None:
    # 승인 계약과 Spring executor의 경로 정규식이 locations/global만 통과시킨다.
    url = _settings(tmp_path).generate_url

    assert url.startswith("https://aiplatform.googleapis.com/v1/projects/capstone-demo/")
    assert "/locations/global/publishers/google/models/" in url
    assert url.endswith(":generateContent")


def test_settings_come_from_the_project_root_env_value(monkeypatch: Any) -> None:
    monkeypatch.delenv("MARS_VERTEX_SERVICE_ACCOUNT_JSON_B64", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    assert VertexTransportSettings.from_environment() is None

    info = _credential_info()
    monkeypatch.setenv("MARS_VERTEX_SERVICE_ACCOUNT_JSON_B64", base64.b64encode(json.dumps(info).encode()).decode())
    settings = VertexTransportSettings.from_environment()

    assert settings is not None
    # project는 env가 아니라 credential JSON이 정한다. 둘이 어긋날 여지를 두지 않는다.
    assert settings.project_id == "capstone-demo"


def test_api_key_fallback_is_refused_outright(monkeypatch: Any) -> None:
    info = _credential_info()
    monkeypatch.setenv("MARS_VERTEX_SERVICE_ACCOUNT_JSON_B64", base64.b64encode(json.dumps(info).encode()).decode())
    monkeypatch.setenv("GOOGLE_API_KEY", "should-never-be-used")

    with pytest.raises(VertexTransportNotConfigured):
        VertexTransportSettings.from_environment()


def test_invalid_service_account_env_is_refused(monkeypatch: Any) -> None:
    monkeypatch.setenv("MARS_VERTEX_SERVICE_ACCOUNT_JSON_B64", "not-base64")

    with pytest.raises(VertexTransportNotConfigured, match="VERTEX_CREDENTIAL_ENV_INVALID"):
        VertexTransportSettings.from_environment()


def test_model_packet_is_taken_only_from_a_single_candidate_text() -> None:
    body = {"candidates": [{"content": {"parts": [{"text": '{"status":"ABSTAIN"}'}]}}]}

    assert json.loads(_model_json_bytes(body))["status"] == "ABSTAIN"


@pytest.mark.parametrize(
    "body",
    [
        {"candidates": []},
        {"candidates": [{}, {}]},
        {"candidates": [{"content": {"parts": []}}]},
        {"candidates": [{"content": {"parts": [{"inlineData": {}}]}}]},
    ],
)
def test_unusable_provider_bodies_are_rejected(body: dict[str, Any]) -> None:
    with pytest.raises(VertexVetoRequestError):
        _model_json_bytes(body)


def test_provider_timeout_and_http_failure_stay_fail_closed(
    tmp_path: Any, monkeypatch: Any
) -> None:
    transport = VertexAiVetoTransport(settings=_settings(tmp_path))
    monkeypatch.setattr(
        VertexAiVetoTransport,
        "_post",
        lambda self, payload: (_ for _ in ()).throw(httpx.ConnectError("down")),
    )

    with pytest.raises(VertexBudgetExhausted):
        transport.invoke(system_prompt="prompt", request_bytes=_request())
