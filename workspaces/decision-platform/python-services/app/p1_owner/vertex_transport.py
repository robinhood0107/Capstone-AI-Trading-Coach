"""등록 코퍼스 근거만으로 판정하는 실 Vertex transport.

`vertex_veto.py`는 provider 코드를 담지 않는다는 계약이 있어 credential과 HTTP는 여기 있다.
검증 로직과 ABSTAIN 사유 매트릭스는 그대로 두고 transport만 갈아끼우므로, 기존 fixture 검증은 전부
그대로 유효하다.

grounding은 요청 패킷의 publicEvidence로 한정한다. 모델이 새 출처를 만들어 오면 host가 관측한
grounding_sources에 없으므로 NO_GROUNDING으로 떨어진다. 호출 실패·시간초과·형식오류도 전부 기존
ABSTAIN 사유로 이어져 fail-closed가 유지된다.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import httpx

from app.p1_owner.vertex_veto import (
    MODEL_ID,
    VerifiedGroundingSource,
    VertexBudgetExhausted,
    VertexProviderTimeout,
    VertexTransportResult,
    VertexVetoRequestError,
)

_SCOPE: Final = "https://www.googleapis.com/auth/cloud-platform"
_REQUEST_TIMEOUT_SECONDS: Final = 20.0
_MAX_RESPONSE_BYTES: Final = 256 * 1024
_MAX_EVIDENCE_ITEMS: Final = 8


class VertexTransportNotConfigured(RuntimeError):
    """Vertex 설정이나 credential이 없어 실 transport를 만들 수 없다."""


@dataclass(frozen=True, slots=True)
class VertexTransportSettings:
    """환경에서 읽는 Vertex 좌표. credential 본문은 파일에서만 읽는다."""

    project_id: str
    location: str
    service_account_file: Path

    @classmethod
    def from_environment(cls) -> VertexTransportSettings | None:
        project_id = os.environ.get("VERTEX_PROJECT_ID", "").strip()
        location = os.environ.get("VERTEX_LOCATION", "").strip()
        credential = os.environ.get("VERTEX_SERVICE_ACCOUNT_FILE", "").strip()
        if not project_id or not location or not credential:
            return None
        path = Path(credential)
        if not path.is_file():
            raise VertexTransportNotConfigured("VERTEX_SERVICE_ACCOUNT_FILE is not a regular file")
        if not project_id.replace("-", "").isalnum() or not location.replace("-", "").isalnum():
            raise VertexTransportNotConfigured("Vertex project or location is invalid")
        return cls(project_id=project_id, location=location, service_account_file=path)

    @property
    def generate_url(self) -> str:
        return (
            f"https://{self.location}-aiplatform.googleapis.com/v1/projects/"
            f"{self.project_id}/locations/{self.location}/publishers/google/models/"
            f"{MODEL_ID}:generateContent"
        )


def _grounding_sources_from_request(
    request_bytes: bytes,
) -> tuple[dict[str, VerifiedGroundingSource], int]:
    """요청 패킷의 publicEvidence를 host 관측 사실로 되돌린다.

    이 값은 수집 시점에 host가 기록한 uri·발행일에서 나온 것이므로 모델 주장과 독립이다.
    """

    try:
        request = json.loads(request_bytes)
    except json.JSONDecodeError as error:
        raise VertexVetoRequestError("Vertex veto request is not JSON") from error
    if not isinstance(request, dict):
        raise VertexVetoRequestError("Vertex veto request is not an object")
    items = request.get("publicEvidence")
    if not isinstance(items, list) or len(items) > _MAX_EVIDENCE_ITEMS:
        raise VertexVetoRequestError("Vertex veto request evidence is invalid")
    sources: dict[str, VerifiedGroundingSource] = {}
    for item in items:
        if not isinstance(item, dict):
            raise VertexVetoRequestError("Vertex veto request evidence item is invalid")
        source_id = item.get("sourceId")
        source_type = item.get("sourceType")
        event_date = item.get("sourceEventDate")
        quote = item.get("boundedQuote")
        if (
            not isinstance(source_id, str)
            or not isinstance(source_type, str)
            or not isinstance(event_date, str)
            or not isinstance(quote, str)
            or source_id in sources
        ):
            raise VertexVetoRequestError("Vertex veto request evidence item is invalid")
        sources[source_id] = VerifiedGroundingSource(
            source_type=source_type,
            source_event_date=event_date,
            support_texts=(quote,),
        )
    return sources, len(sources)


@dataclass(slots=True)
class VertexAiVetoTransport:
    """단발 generateContent 호출 하나만 수행한다. tool도 웹검색도 붙이지 않는다."""

    settings: VertexTransportSettings
    session_call_cap: int = 8
    physical_calls: int = 0
    logical_calls: int = 0
    _client: httpx.Client | None = field(default=None, repr=False)

    def invoke(self, *, system_prompt: str, request_bytes: bytes) -> VertexTransportResult:
        self.logical_calls += 1
        if self.physical_calls >= self.session_call_cap:
            raise VertexBudgetExhausted("VERTEX_SESSION_CALL_CAP_EXHAUSTED")
        grounding_sources, grounding_query_count = _grounding_sources_from_request(request_bytes)
        if not grounding_sources:
            # 근거가 없으면 모델을 부를 이유가 없다. 검증기가 NO_GROUNDING으로 처리한다.
            raise VertexBudgetExhausted("VERTEX_NO_REGISTERED_EVIDENCE")
        payload = {
            "contents": [{"role": "user", "parts": [{"text": request_bytes.decode("utf-8")}]}],
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0,
                "candidateCount": 1,
            },
        }
        self.physical_calls += 1
        try:
            response = self._post(payload)
        except httpx.TimeoutException as error:
            raise VertexProviderTimeout("VERTEX_GENERATE_TIMEOUT") from error
        except httpx.HTTPError as error:
            raise VertexBudgetExhausted("VERTEX_GENERATE_UNAVAILABLE") from error
        return VertexTransportResult(
            response_bytes=_model_json_bytes(response),
            grounding_sources=grounding_sources,
            provider_call_count=1,
            grounding_query_count=grounding_query_count,
        )

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        client = self._require_client()
        result = client.post(
            self.settings.generate_url,
            headers={"Authorization": f"Bearer {self._access_token()}"},
            json=payload,
        )
        if result.status_code >= 400:
            raise VertexBudgetExhausted("VERTEX_GENERATE_REJECTED")
        if len(result.content) > _MAX_RESPONSE_BYTES:
            raise VertexVetoRequestError("Vertex response byte bound violated")
        body = result.json()
        if not isinstance(body, dict):
            raise VertexVetoRequestError("Vertex response is not an object")
        return body

    def _require_client(self) -> httpx.Client:
        if self._client is None:
            # 재시도는 두지 않는다. 한 번 실패하면 그대로 fail-closed다.
            self._client = httpx.Client(
                timeout=_REQUEST_TIMEOUT_SECONDS,
                transport=httpx.HTTPTransport(retries=0),
            )
        return self._client

    def _access_token(self) -> str:
        from google.auth.transport.requests import Request  # noqa: PLC0415
        from google.oauth2 import service_account  # noqa: PLC0415

        try:
            # google-auth는 이 생성자에 주석이 없어 mypy가 untyped call로 본다.
            credentials = service_account.Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
                str(self.settings.service_account_file), scopes=[_SCOPE]
            )
        except (OSError, ValueError) as error:
            raise VertexBudgetExhausted("VERTEX_CREDENTIAL_UNREADABLE") from error
        try:
            credentials.refresh(Request())
        except Exception as error:
            # 토큰 교환 실패는 provider 실패로 접어 fail-closed를 유지한다.
            raise VertexBudgetExhausted("VERTEX_TOKEN_REJECTED") from error
        token = credentials.token
        if not isinstance(token, str) or not token:
            raise VertexBudgetExhausted("VERTEX_TOKEN_MISSING")
        return token


def _model_json_bytes(body: dict[str, Any]) -> bytes:
    """candidate 하나의 text part만 모아 모델 packet bytes로 만든다."""

    candidates = body.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != 1:
        raise VertexVetoRequestError("Vertex response candidate count is invalid")
    content = candidates[0].get("content") if isinstance(candidates[0], dict) else None
    parts = content.get("parts") if isinstance(content, dict) else None
    if not isinstance(parts, list) or not parts:
        raise VertexVetoRequestError("Vertex response parts are missing")
    texts = [part.get("text") for part in parts if isinstance(part, dict)]
    if any(not isinstance(text, str) for text in texts) or not texts:
        raise VertexVetoRequestError("Vertex response text is missing")
    return "".join(str(text) for text in texts).encode("utf-8")
