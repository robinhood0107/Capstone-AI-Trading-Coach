from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.rag import pre_s5_voyage_query_transport
from app.rag.pre_s5_provider_control import PreS5ProviderBinding, PreS5VoyageQueryActivation
from app.rag.pre_s5_voyage_query_transport import (
    PacketGatedPreS5VoyageContext4QueryEmbedder,
    PreS5VoyageContext4QueryEmbedder,
)
from app.rag.pre_s5_voyage_transport import PreS5VoyageHttpRequest, PreS5VoyageHttpResponse
from app.rag.rag_v2_authorized_retrieval import (
    RagV2QueryEmbeddingError,
    RagV2RetrievalFailureCode,
)


_NOW = datetime(2026, 8, 9, 1, tzinfo=UTC)
_QUESTION = "공개 근거를 비교해 보여 주세요."
_SCOPE = "rvs_" + "a" * 32


class _Lease:
    def __init__(self) -> None:
        self.claims = 0
        self.commits: list[tuple[int, int, int]] = []
        self.unknown_billing = 0

    def claim_attempt(self, *, now: datetime) -> None:
        assert now == _NOW
        self.claims += 1

    def commit(
        self,
        *,
        expected_input_tokens: int,
        total_tokens: int,
        actual_cost_microusd: int,
    ) -> None:
        self.commits.append((expected_input_tokens, total_tokens, actual_cost_microusd))

    def mark_unknown_billing(self) -> None:
        self.unknown_billing += 1


class _MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class _LeaseThatExpiresClock(_Lease):
    def __init__(self, *, clock: _MutableClock, expires_at: datetime) -> None:
        super().__init__()
        self._clock = clock
        self._expires_at = expires_at

    def claim_attempt(self, *, now: datetime) -> None:
        super().claim_attempt(now=now)
        self._clock.value = self._expires_at


class _Sender:
    def __init__(self, *, response: PreS5VoyageHttpResponse | None = None) -> None:
        self.requests: list[PreS5VoyageHttpRequest] = []
        self._response = response or _response(_QUESTION)

    def post(self, request: PreS5VoyageHttpRequest) -> PreS5VoyageHttpResponse:
        self.requests.append(request)
        return self._response


class _ReservationRepository:
    def __init__(self, lease: _Lease) -> None:
        self.lease = lease
        self.activations: list[PreS5VoyageQueryActivation] = []

    def reserve(
        self,
        *,
        activation: PreS5VoyageQueryActivation,
        evaluation_component_scope: str | None = None,
    ) -> _Lease:
        del evaluation_component_scope
        self.activations.append(activation)
        return self.lease


def test_voyage_query_transport_sends_one_nested_query_and_returns_one_1024d_receipt() -> None:
    lease = _Lease()
    sender = _Sender()
    embedder = _embedder(lease=lease, sender=sender)

    result = embedder.embed_query_with_receipt(
        question=_QUESTION,
        scope_claim_id=_SCOPE,
        external_query_consent_granted=True,
    )

    assert result.voyage_physical_calls == 1
    assert len(result.vector) == 1024
    assert result.vector[0] == 1.0
    assert lease.claims == 1
    assert lease.commits == [(1, 7, 7)]
    assert lease.unknown_billing == 0
    assert len(sender.requests) == 1
    request = sender.requests[0]
    assert request.url == "https://api.voyageai.com/v1/contextualizedembeddings"
    assert request.headers["Authorization"] == "Bearer test-key"
    assert json.loads(request.body) == {
        "enable_auto_chunking": False,
        "input_type": "query",
        "inputs": [[_QUESTION]],
        "model": "voyage-context-4",
        "output_dimension": 1024,
        "output_dtype": "float",
    }


def test_voyage_query_transport_requires_consent_and_exact_question_scope_binding_before_a_call() -> None:
    lease = _Lease()
    sender = _Sender()
    embedder = _embedder(lease=lease, sender=sender)

    with pytest.raises(RagV2QueryEmbeddingError) as denied:
        embedder.embed_query_with_receipt(
            question=_QUESTION,
            scope_claim_id=_SCOPE,
            external_query_consent_granted=False,
        )
    assert denied.value.failure_code is RagV2RetrievalFailureCode.QUERY_PROFILE_UNAVAILABLE
    assert denied.value.voyage_physical_calls == 0

    with pytest.raises(RagV2QueryEmbeddingError) as mismatched:
        embedder.embed_query_with_receipt(
            question="다른 질문입니다.",
            scope_claim_id=_SCOPE,
            external_query_consent_granted=True,
        )
    assert mismatched.value.failure_code is RagV2RetrievalFailureCode.QUERY_PROFILE_UNAVAILABLE
    assert mismatched.value.voyage_physical_calls == 0
    assert lease.claims == 0
    assert sender.requests == []


def test_voyage_query_transport_marks_unknown_billing_after_one_invalid_provider_response_without_retry() -> None:
    lease = _Lease()
    sender = _Sender(response=PreS5VoyageHttpResponse(status=500, headers={}, body=b""))
    embedder = _embedder(lease=lease, sender=sender)

    with pytest.raises(RagV2QueryEmbeddingError) as error:
        embedder.embed_query_with_receipt(
            question=_QUESTION,
            scope_claim_id=_SCOPE,
            external_query_consent_granted=True,
        )

    assert error.value.failure_code is RagV2RetrievalFailureCode.QUERY_EMBEDDING_INVALID
    assert error.value.voyage_physical_calls == 1
    assert lease.claims == 1
    assert lease.commits == []
    assert lease.unknown_billing == 1
    assert len(sender.requests) == 1


def test_voyage_query_transport_rechecks_expiry_after_claim_before_sender_post() -> None:
    expires_at = _NOW + timedelta(minutes=5)
    clock = _MutableClock(_NOW)
    lease = _LeaseThatExpiresClock(clock=clock, expires_at=expires_at)
    sender = _Sender()
    embedder = PreS5VoyageContext4QueryEmbedder(
        activation=_activation(expires_at=expires_at),
        api_key="test-key",
        lease=lease,
        token_counter=_FixtureTokenCounter(),
        sender=sender,
        clock=clock,
    )

    with pytest.raises(RagV2QueryEmbeddingError) as error:
        embedder.embed_query_with_receipt(
            question=_QUESTION,
            scope_claim_id=_SCOPE,
            external_query_consent_granted=True,
        )

    assert error.value.failure_code is RagV2RetrievalFailureCode.QUERY_EMBEDDING_INVALID
    assert error.value.voyage_physical_calls == 0
    assert lease.claims == 1
    assert lease.unknown_billing == 1
    assert sender.requests == []


def test_voyage_query_transport_rejects_duplicate_provider_json_keys_after_one_attempt() -> None:
    lease = _Lease()
    duplicate_key_response = PreS5VoyageHttpResponse(
        status=200,
        headers={},
        body=b'{"model":"voyage-context-4","model":"forged"}',
    )
    sender = _Sender(response=duplicate_key_response)
    embedder = _embedder(lease=lease, sender=sender)

    with pytest.raises(RagV2QueryEmbeddingError) as error:
        embedder.embed_query_with_receipt(
            question=_QUESTION,
            scope_claim_id=_SCOPE,
            external_query_consent_granted=True,
        )

    assert error.value.failure_code is RagV2RetrievalFailureCode.QUERY_EMBEDDING_INVALID
    assert error.value.voyage_physical_calls == 1
    assert lease.claims == 1
    assert lease.commits == []
    assert lease.unknown_billing == 1
    assert len(sender.requests) == 1


def test_packet_gated_query_embedder_loads_a_fresh_exact_packet_per_request(tmp_path: Path) -> None:
    os.chmod(tmp_path, 0o700)
    control = tmp_path / "control"
    control.mkdir(mode=0o700)
    tokenizer_sha256 = _write_official_tokenizer(tmp_path)
    _write_packet(
        control / "pre-s5-voyage-query-activation.json",
        tokenizer_sha256=tokenizer_sha256,
    )
    lease = _Lease()
    reservations = _ReservationRepository(lease)
    sender = _Sender()
    embedder = PacketGatedPreS5VoyageContext4QueryEmbedder(
        local_root=tmp_path,
        binding=_binding(),
        api_key="test-key",
        usage_repository=reservations,
        sender=sender,
        clock=lambda: _NOW,
    )

    result = embedder.embed_query_with_receipt(
        question=_QUESTION,
        scope_claim_id=_SCOPE,
        external_query_consent_granted=True,
    )

    assert result.voyage_physical_calls == 1
    assert len(reservations.activations) == 1
    assert lease.claims == 1
    assert len(sender.requests) == 1


def test_packet_gated_query_embedder_does_not_reserve_a_one_shot_packet_when_official_count_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    os.chmod(tmp_path, 0o700)
    control = tmp_path / "control"
    control.mkdir(mode=0o700)
    tokenizer_sha256 = _write_official_tokenizer(tmp_path)
    _write_packet(
        control / "pre-s5-voyage-query-activation.json",
        tokenizer_sha256=tokenizer_sha256,
    )
    lease = _Lease()
    reservations = _ReservationRepository(lease)
    monkeypatch.setattr(
        pre_s5_voyage_query_transport.LocalPreS5VoyageContext4Tokenizer,
        "from_local_root",
        classmethod(lambda _cls, **_kwargs: _FailingTokenCounter()),
    )
    embedder = PacketGatedPreS5VoyageContext4QueryEmbedder(
        local_root=tmp_path,
        binding=_binding(),
        api_key="test-key",
        usage_repository=reservations,
        sender=_Sender(),
        clock=lambda: _NOW,
    )

    with pytest.raises(RagV2QueryEmbeddingError) as error:
        embedder.embed_query_with_receipt(
            question=_QUESTION,
            scope_claim_id=_SCOPE,
            external_query_consent_granted=True,
        )

    assert error.value.failure_code is RagV2RetrievalFailureCode.QUERY_PROFILE_UNAVAILABLE
    assert reservations.activations == []
    assert lease.claims == 0


class _FailingTokenCounter:
    model = "voyage-context-4"

    @property
    def tokenizer_sha256(self) -> str:
        return "e" * 64

    def count_texts(self, *, texts: tuple[str, ...], token_cap: int) -> int:
        del texts, token_cap
        raise RuntimeError("fixture tokenizer preflight failed")


def _embedder(*, lease: _Lease, sender: _Sender) -> PreS5VoyageContext4QueryEmbedder:
    return PreS5VoyageContext4QueryEmbedder(
        activation=_activation(),
        api_key="test-key",
        lease=lease,
        token_counter=_FixtureTokenCounter(),
        sender=sender,
        clock=lambda: _NOW,
    )


def _activation(*, expires_at: datetime | None = None) -> PreS5VoyageQueryActivation:
    return PreS5VoyageQueryActivation(
        packet_sha256="a" * 64,
        nonce_sha256="b" * 64,
        query_sha256=hashlib.sha256(_QUESTION.encode()).hexdigest(),
        scope_claim_sha256=hashlib.sha256(_SCOPE.encode()).hexdigest(),
        rate_evidence_sha256="c" * 64,
        tokenizer_sha256="e" * 64,
        provider="VOYAGE",
        operation="CONTEXTUALIZED_QUERY_EMBEDDING",
        origin="https://api.voyageai.com",
        endpoint="/v1/contextualizedembeddings",
        expires_at=expires_at or _NOW + timedelta(minutes=5),
        logical_call_cap=1,
        physical_call_cap=1,
        token_cap=8_192,
        byte_cap=1_048_576,
        cost_cap_microusd=8_192,
        input_microusd_per_token=1,
        retry_count=0,
        raw_artifact_count=0,
    )


def _response(question: str) -> PreS5VoyageHttpResponse:
    vector = [0.0] * 1024
    vector[0] = 1.0
    body = json.dumps(
        {
            "chunker_version": "1.0.0",
            "data": [
                {
                    "data": [
                        {
                            "embedding": vector,
                            "index": 0,
                            "text": question,
                        }
                    ],
                    "index": 0,
                }
            ],
            "model": "voyage-context-4",
            "usage": {"total_tokens": 7},
        },
        separators=(",", ":"),
    ).encode()
    return PreS5VoyageHttpResponse(status=200, headers={}, body=body)


def _binding() -> PreS5ProviderBinding:
    return PreS5ProviderBinding(
        head_commit="a" * 40,
        tree_object="b" * 40,
        ci_digest="c" * 64,
        security_digest="d" * 64,
    )


def _write_packet(path: Path, *, tokenizer_sha256: str) -> None:
    payload = {
        "byteCap": 1_048_576,
        "ciDigest": "c" * 64,
        "costCapMicrousd": 8_192,
        "date": "NONE",
        "endpoint": "/v1/contextualizedembeddings",
        "expiresAt": (_NOW + timedelta(minutes=5)).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "headCommit": "a" * 40,
        "inputMicrousdPerToken": 1,
        "issuedAt": _NOW.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "logicalCallCap": 1,
        "nonce": "ps5_voyage_query_activation_0001",
        "operation": "CONTEXTUALIZED_QUERY_EMBEDDING",
        "operator": "local-operator",
        "organizationTrainingOptOutEvidenceSha256": "f" * 64,
        "origin": "https://api.voyageai.com",
        "paymentMethodPrivacyEvidenceSha256": "0" * 64,
        "physicalCallCap": 1,
        "provider": "VOYAGE",
        "query": "SINGLE_RAG_QUERY_SHA256_BOUND",
        "querySha256": hashlib.sha256(_QUESTION.encode()).hexdigest(),
        "rawArtifactCount": 0,
        "rateEvidenceSha256": "1" * 64,
        "retryCount": 0,
        "schemaVersion": "pre-s5-voyage-query-activation/v1",
        "scopeClaimSha256": hashlib.sha256(_SCOPE.encode()).hexdigest(),
        "securityDigest": "d" * 64,
        "state": "APPROVED",
        "symbol": "NONE",
        "tokenizerSha256": tokenizer_sha256,
        "tokenCap": 8_192,
        "treeObject": "b" * 40,
    }
    path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    os.chmod(path, 0o600)


class _FixtureTokenCounter:
    """Transport test가 official count preflight를 bypass하지 않게 하는 content-free seam이다."""

    model = "voyage-context-4"
    tokenizer_sha256 = "e" * 64

    def count_texts(self, *, texts: tuple[str, ...], token_cap: int) -> int:
        del texts
        assert token_cap == 8_192
        return 1


def _write_official_tokenizer(local_root: Path) -> str:
    """Packet-gated path가 local 0700/0600 hash-pinned artifact를 실제로 요구함을 검증한다."""

    artifact_root = local_root / "artifacts"
    artifact_root.mkdir(mode=0o700)
    model_root = artifact_root / "voyage-context-4"
    model_root.mkdir(mode=0o700)
    raw = json.dumps(
        {
            "added_tokens": [],
            "decoder": None,
            "model": {"type": "WordLevel", "unk_token": "[UNK]", "vocab": {"[UNK]": 0}},
            "normalizer": None,
            "padding": None,
            "post_processor": None,
            "pre_tokenizer": {"type": "Whitespace"},
            "truncation": None,
            "version": "1.0",
        },
        separators=(",", ":"),
    ).encode("utf-8")
    tokenizer_path = model_root / "tokenizer.json"
    tokenizer_path.write_bytes(raw)
    os.chmod(tokenizer_path, 0o600)
    return hashlib.sha256(raw).hexdigest()
