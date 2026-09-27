from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from typing import NoReturn

import grpc
import pytest

from app.generated import strong_llm_agent_pb2
from app.strong_llm.grpc_server import StrongLlmAgentServicer
from app.strong_llm.models import RunRequest
from app.strong_llm.vertex_provider import VertexProviderSettings
from tests.strong_llm.test_grpc_server import _HostFrames
from tests.strong_llm.test_runtime import FakeProvider

_FAKE_SA = {
    "type": "service_account",
    "project_id": "mars-test-dummy",
    "private_key_id": "0123456789abcdef",
    "private_key": "-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----\n",
    "client_email": "dummy@mars-test-dummy.iam.gserviceaccount.com",
    "token_uri": "https://oauth2.googleapis.com/token",
}
_OWNER_B64 = base64.b64encode(json.dumps(_FAKE_SA).encode()).decode("ascii")


class _Context:
    def invocation_metadata(self) -> tuple[tuple[str, str], ...]:
        return (("x-decision-strong-llm-grpc-auth", "s" * 64),)

    def abort(self, code: grpc.StatusCode, details: str) -> NoReturn:
        raise RuntimeError(f"{code.name}:{details}")


def _start(owner_b64: str) -> _HostFrames:
    frames = _HostFrames()
    frames.put(
        strong_llm_agent_pb2.HostEvent(
            run_id="s49_run_" + "2" * 32,
            sequence=1,
            call_id="start",
            start_run=strong_llm_agent_pb2.StartRun(
                model_id="gemini-3.5-flash",
                question="후보를 평가하세요.",
                answer_mode="CONCISE",
                max_tool_rounds=0,
                current_time="2026-09-27T00:00:00Z",
                timezone="Asia/Seoul",
                owner_vertex_service_account_json_b64=owner_b64,
            ),
        )
    )
    return frames


def test_owner_credential_run_uses_the_owner_factory_and_never_the_operator() -> None:
    provider = FakeProvider()
    seen: list[str] = []

    def operator(_request: RunRequest) -> tuple[FakeProvider, None]:
        pytest.fail("a run carrying the owner's key must not fall back to the operator key")

    def owner(_request: RunRequest, credential: str) -> tuple[FakeProvider, None]:
        seen.append(credential)
        return provider, None

    events: Iterator[strong_llm_agent_pb2.AgentEvent] = StrongLlmAgentServicer(
        "s" * 64,
        operator,
        owner,  # type: ignore[arg-type]
    ).Generate(iter(_start(_OWNER_B64)), _Context())  # type: ignore[arg-type]
    first = next(events)
    assert first.WhichOneof("payload") == "provider_call_planned"
    assert seen == [_OWNER_B64]


def test_owner_credential_without_an_owner_factory_fails_instead_of_using_the_operator() -> None:
    def operator(_request: RunRequest) -> tuple[FakeProvider, None]:
        pytest.fail("operator key must not be substituted")

    events = StrongLlmAgentServicer("s" * 64, operator).Generate(  # type: ignore[arg-type]
        iter(_start(_OWNER_B64)),
        _Context(),  # type: ignore[arg-type]
    )
    failed = next(events)
    assert failed.WhichOneof("payload") == "failed"
    assert failed.failed.failure_leaf == "STRONG_LLM_OWNER_CREDENTIAL_UNSUPPORTED"
    # 실패 leaf 에 비밀이 실리지 않는다.
    assert _OWNER_B64 not in str(failed)


def test_owner_service_account_decodes_with_the_same_canonical_rule_as_the_operator_secret() -> (
    None
):
    settings = VertexProviderSettings.from_b64(
        _OWNER_B64, timeout_seconds=50.0, thinking_level="low", max_output_tokens=4096
    )
    assert settings.service_account_info["project_id"] == "mars-test-dummy"
    for bad in [
        "AIzaSyFAKEFAKEFAKEFAKE",
        _OWNER_B64 + "\n",
        base64.urlsafe_b64encode(b"{}").decode(),
        "",
    ]:
        with pytest.raises(ValueError) as error:
            VertexProviderSettings.from_b64(
                bad, timeout_seconds=50.0, thinking_level="low", max_output_tokens=4096
            )
        assert "AIza" not in str(error.value)
