"""실 Vertex transport는 요청에 담긴 등록 근거만 host 사실로 인정하고, 실패는 전부 fail-closed다."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.data._shared.canonical_json import canonical_json_bytes
from app.p1_owner.vertex_transport import (
    VertexAiVetoTransport,
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


def _settings(tmp_path: Any) -> VertexTransportSettings:
    key = tmp_path / "service-account.json"
    key.write_text("{}", encoding="utf-8")
    return VertexTransportSettings(
        project_id="capstone-demo", location="asia-northeast3", service_account_file=key
    )


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


def test_generate_url_pins_project_location_and_model(tmp_path: Any) -> None:
    url = _settings(tmp_path).generate_url

    assert url.startswith("https://asia-northeast3-aiplatform.googleapis.com/v1/projects/")
    assert "/publishers/google/models/" in url
    assert url.endswith(":generateContent")


def test_settings_are_absent_until_every_coordinate_is_configured(monkeypatch: Any) -> None:
    for name in ("VERTEX_PROJECT_ID", "VERTEX_LOCATION", "VERTEX_SERVICE_ACCOUNT_FILE"):
        monkeypatch.delenv(name, raising=False)

    assert VertexTransportSettings.from_environment() is None

    monkeypatch.setenv("VERTEX_PROJECT_ID", "capstone-demo")
    monkeypatch.setenv("VERTEX_LOCATION", "asia-northeast3")

    assert VertexTransportSettings.from_environment() is None


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
