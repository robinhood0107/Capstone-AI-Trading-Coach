from __future__ import annotations

import hashlib
import json
import os
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import grpc
import pytest

import app.rag.rag_v2_grpc_server as grpc_server_module
from app.generated import rag_v2_pb2, rag_v2_pb2_grpc
from app.rag.pre_s5_provider_control import (
    load_pre_s5_voyage_query_runtime_configuration,
)
from app.rag.pre_s5_voyage_transport import PreS5VoyageHttpRequest, PreS5VoyageHttpResponse
from app.rag.rag_v2_authorized_retrieval import (
    RagV2BundleScope,
    RagV2ChannelResult,
    RagV2RetrievalCandidate,
)
from app.rag.rag_v2_grpc_server import RagV2GrpcServerSettings, build_rag_v2_engine
from app.rag.rag_v2_rpc import RagV2RpcStatus, create_rag_v2_server


_SECRET = "rag-v2-grpc-shared-secret-for-s4-7d-settings-0001"


def test_v2_grpc_operator_entrypoint_is_registered() -> None:
    """운영자는 ad-hoc module invocation 없이 purpose-separated v2 server만 실행할 수 있다."""

    project_root = Path(__file__).resolve().parents[2]
    pyproject = tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))

    assert (
        pyproject["project"]["scripts"]["rag-v2-grpc"]
        == "app.rag.rag_v2_grpc_server:serve"
    )


def test_v2_server_settings_require_dedicated_loopback_query_dsn_and_absolute_bge_packet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAG_V2_GRPC_SHARED_SECRET", _SECRET)
    monkeypatch.setenv("RAG_V2_QUERY_DATABASE_DSN", "postgresql://decision_rag_query@localhost/rag")
    monkeypatch.setenv("CAPSTONE_RAG_BGE_PACKET_ROOT", "/var/lib/capstone/bge-packet")
    monkeypatch.delenv("RAG_V2_GRPC_ENABLE_REFLECTION", raising=False)

    settings = RagV2GrpcServerSettings.from_env()

    assert settings.bind_address == "127.0.0.1:50054"
    assert settings.shared_secret == _SECRET
    assert settings.query_database_dsn == "postgresql://decision_rag_query@localhost/rag"
    assert settings.bge_packet_root == Path("/var/lib/capstone/bge-packet")


@pytest.mark.parametrize(
    ("name", "value", "expected"),
    [
        ("RAG_V2_GRPC_BIND_ADDRESS", "0.0.0.0:50054", "numeric loopback"),
        ("RAG_V2_QUERY_DATABASE_DSN", "", "QUERY_DATABASE_DSN"),
        ("CAPSTONE_RAG_BGE_PACKET_ROOT", "relative/packet", "BGE_PACKET_ROOT"),
    ],
)
def test_v2_server_settings_fail_closed_for_invalid_runtime_surfaces(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
    expected: str,
) -> None:
    monkeypatch.setenv("RAG_V2_GRPC_SHARED_SECRET", _SECRET)
    monkeypatch.setenv("RAG_V2_QUERY_DATABASE_DSN", "postgresql://decision_rag_query@localhost/rag")
    monkeypatch.setenv("CAPSTONE_RAG_BGE_PACKET_ROOT", "/var/lib/capstone/bge-packet")
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=expected):
        RagV2GrpcServerSettings.from_env()


def test_v2_server_settings_reject_reflection_and_reused_privileged_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAG_V2_GRPC_SHARED_SECRET", _SECRET)
    monkeypatch.setenv("RAG_V2_QUERY_DATABASE_DSN", "postgresql://decision_rag_query@localhost/rag")
    monkeypatch.setenv("CAPSTONE_RAG_BGE_PACKET_ROOT", "/var/lib/capstone/bge-packet")
    monkeypatch.setenv("RAG_V2_GRPC_ENABLE_REFLECTION", "true")

    with pytest.raises(ValueError, match="reflection"):
        RagV2GrpcServerSettings.from_env()

    monkeypatch.setenv("RAG_V2_GRPC_ENABLE_REFLECTION", "false")
    monkeypatch.setenv("DECISION_GRPC_SHARED_SECRET", _SECRET)
    with pytest.raises(ValueError, match="purpose-separated"):
        RagV2GrpcServerSettings.from_env()


def test_voyage_only_local_runtime_configuration_creates_no_bge_fallback_and_requires_query_packet(
    tmp_path: Path,
) -> None:
    os.chmod(tmp_path, 0o700)
    control = tmp_path / "control"
    control.mkdir(mode=0o700)
    runtime_path = control / "pre-s5-voyage-query-runtime.json"
    runtime_path.write_text(
        json.dumps(
            {
                "bgeEnabled": False,
                "ciDigest": "c" * 64,
                "headCommit": "a" * 40,
                "schemaVersion": "pre-s5-voyage-query-runtime/v1",
                "securityDigest": "d" * 64,
                "treeObject": "b" * 40,
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    os.chmod(runtime_path, 0o600)
    secrets = tmp_path / "secrets"
    secrets.mkdir(mode=0o700)
    writer_dsn_path = secrets / "rag-v2-voyage-query-writer-dsn"
    writer_dsn_path.write_text("postgresql://decision_rag_writer@localhost/rag", encoding="utf-8")
    os.chmod(writer_dsn_path, 0o600)
    runtime = load_pre_s5_voyage_query_runtime_configuration(local_root=tmp_path)
    settings = RagV2GrpcServerSettings(
        bind_address="127.0.0.1:50054",
        shared_secret=_SECRET,
        query_database_dsn="postgresql://decision_rag_query@localhost/rag",
        bge_packet_root=None,
        bge_enabled=False,
        voyage_query_runtime=runtime,
    )
    scope = RagV2BundleScope(
        claim_id="rvs_" + "a" * 32,
        owner_user_id="usr_demo_user",
        session_id="req_voyage_runtime_0001",
        exact30_generation_id="rgr_" + "1" * 32,
        oa112_generation_id="rgr_" + "2" * 32,
        owner_private_generation_id=None,
        embedding_profile_id="voyage_context_4_1024_v1",
        policy_version=1,
        allowed_topics=("RISK",),
    )
    engine = build_rag_v2_engine(
        settings=settings,
        scope_reader=_ScopeReader(scope),
        retrieval_adapter=object(),
        environment={"VOYAGE_API_KEY": "test-key"},
    )

    result = engine.ask(
        rag_v2_pb2.RagAskRequest(
            request_id=scope.session_id,
            owner_scope_claim=scope.claim_id,
            question="공개 근거를 비교해 보여 주세요.",
            answer_mode="CONCISE",
            topics=["RISK"],
            consent_context=rag_v2_pb2.RagConsentContext(
                granted=True,
                policy_version="EXTERNAL_AI_RAG_V2",
            ),
        )
    )

    assert result.status is RagV2RpcStatus.RETRIEVAL_FAILURE
    assert result.failure_code == "RAG_QUERY_PROFILE_UNAVAILABLE"
    assert result.provider_physical_total == 0
    assert result.voyage_physical_calls == 0


def test_voyage_profile_composes_packet_lease_rrf_and_loopback_grpc_without_bge_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The enabled profile path permits one fake provider send only after the exact packet/lease chain."""

    question = "공개 근거를 비교해 보여 주세요."
    scope = _voyage_scope()
    _write_voyage_query_runtime(tmp_path)
    tokenizer_sha256 = _write_voyage_official_tokenizer(tmp_path)
    _write_voyage_query_packet(
        tmp_path,
        question=question,
        scope_claim_id=scope.claim_id,
        tokenizer_sha256=tokenizer_sha256,
    )
    writer_secret = tmp_path / "secrets"
    writer_secret.mkdir(mode=0o700)
    writer_dsn_path = writer_secret / "rag-v2-voyage-query-writer-dsn"
    writer_dsn_path.write_text("postgresql://decision_rag_writer@localhost/rag", encoding="utf-8")
    os.chmod(writer_dsn_path, 0o600)
    runtime = load_pre_s5_voyage_query_runtime_configuration(local_root=tmp_path)
    settings = RagV2GrpcServerSettings(
        bind_address="127.0.0.1:50054",
        shared_secret=_SECRET,
        query_database_dsn="postgresql://decision_rag_query@localhost/rag",
        bge_packet_root=None,
        bge_enabled=False,
        voyage_query_runtime=runtime,
    )
    lease = _Lease()
    reservations = _Reservations(lease)
    sender = _Sender(question)
    adapter = _RetrievalAdapter(scope)
    monkeypatch.setattr(
        grpc_server_module,
        "PsycopgPreS5VoyageQueryUsageRepository",
        lambda *, database_dsn: reservations,
    )
    monkeypatch.setattr(
        grpc_server_module,
        "UrllibPreS5VoyageHttpSender",
        lambda: sender,
    )

    engine = build_rag_v2_engine(
        settings=settings,
        scope_reader=_ScopeReader(scope),
        retrieval_adapter=adapter,
        environment={"VOYAGE_API_KEY": "test-key"},
    )
    resources = create_rag_v2_server(_LoopbackSettings(), engine)
    resources.server.start()
    channel = grpc.insecure_channel(f"127.0.0.1:{resources.bound_port}")
    try:
        response = rag_v2_pb2_grpc.RagServiceStub(channel).Ask(  # type: ignore[no-untyped-call]
            rag_v2_pb2.RagAskRequest(
                request_id=scope.session_id,
                owner_scope_claim=scope.claim_id,
                question=question,
                answer_mode="CONCISE",
                topics=["RISK"],
                consent_context=rag_v2_pb2.RagConsentContext(
                    granted=True,
                    policy_version="EXTERNAL_AI_RAG_V2",
                ),
            ),
            metadata=(("x-decision-rag-v2-grpc-auth", _SECRET),),
            timeout=2,
        )
    finally:
        channel.close()
        resources.server.stop(grace=0).wait(timeout=2)

    assert response.status == rag_v2_pb2.RAG_RESPONSE_STATUS_RETRIEVAL_ONLY
    assert response.embedding_profile_id == "voyage_context_4_1024_v1"
    assert response.provider_physical_counts.total == 1
    assert response.provider_physical_counts.voyage == 1
    assert response.provider_physical_counts.gemini == response.provider_physical_counts.openai == 0
    assert len(response.citations) == 2
    assert len(reservations.activations) == 1
    assert lease.claims == 1
    assert len(lease.commits) == 1
    assert lease.commits[0][0] > 0
    assert lease.commits[0][1:] == (7, 7)
    assert lease.unknown_billing == 0
    assert len(sender.requests) == 1
    assert json.loads(sender.requests[0].body) == {
        "enable_auto_chunking": False,
        "input_type": "query",
        "inputs": [[question]],
        "model": "voyage-context-4",
        "output_dimension": 1024,
        "output_dtype": "float",
    }
    assert adapter.dense_vectors == [(1.0,) + (0.0,) * 1023]


@dataclass(frozen=True)
class _LoopbackSettings:
    bind_address: str = "127.0.0.1:0"
    shared_secret: str = _SECRET


class _Lease:
    def __init__(self) -> None:
        self.claims = 0
        self.commits: list[tuple[int, int, int]] = []
        self.unknown_billing = 0

    def claim_attempt(self, *, now: datetime) -> None:
        assert now.tzinfo is not None
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


class _Reservations:
    def __init__(self, lease: _Lease) -> None:
        self._lease = lease
        self.activations: list[object] = []

    def reserve(self, *, activation: object, evaluation_component_scope: str | None = None) -> _Lease:
        del evaluation_component_scope
        self.activations.append(activation)
        return self._lease


class _Sender:
    def __init__(self, question: str) -> None:
        self.requests: list[PreS5VoyageHttpRequest] = []
        vector = [0.0] * 1024
        vector[0] = 1.0
        self._response = PreS5VoyageHttpResponse(
            status=200,
            headers={},
            body=json.dumps(
                {
                    "chunker_version": "1.0.0",
                    "data": [{"data": [{"embedding": vector, "index": 0, "text": question}], "index": 0}],
                    "model": "voyage-context-4",
                    "usage": {"total_tokens": 7},
                },
                separators=(",", ":"),
            ).encode(),
        )

    def post(self, request: PreS5VoyageHttpRequest) -> PreS5VoyageHttpResponse:
        self.requests.append(request)
        return self._response


class _RetrievalAdapter:
    def __init__(self, scope: RagV2BundleScope) -> None:
        self._exact = _candidate(1, scope, source_scope="EXACT30")
        self._oa = _candidate(2, scope, source_scope="OA112")
        self.dense_vectors: list[tuple[float, ...]] = []

    def retrieve_exact(self, **_: object) -> RagV2ChannelResult:
        return RagV2ChannelResult(channel="exact", items=(self._exact,), complete=True)

    def retrieve_lexical(self, **_: object) -> RagV2ChannelResult:
        return RagV2ChannelResult(channel="lexical", items=(self._oa,), complete=True)

    def retrieve_dense(self, *, query_vector: tuple[float, ...], **_: object) -> RagV2ChannelResult:
        self.dense_vectors.append(query_vector)
        return RagV2ChannelResult(channel="dense", items=(self._exact, self._oa), complete=True)


def _voyage_scope() -> RagV2BundleScope:
    return RagV2BundleScope(
        claim_id="rvs_" + "a" * 32,
        owner_user_id="usr_demo_user",
        session_id="req_voyage_runtime_0001",
        exact30_generation_id="rgr_" + "1" * 32,
        oa112_generation_id="rgr_" + "2" * 32,
        owner_private_generation_id=None,
        embedding_profile_id="voyage_context_4_1024_v1",
        policy_version=1,
        allowed_topics=("RISK",),
    )


def _candidate(index: int, scope: RagV2BundleScope, *, source_scope: str) -> RagV2RetrievalCandidate:
    content = f"Canonical public content {index}."
    digest = hashlib.sha256(content.encode()).hexdigest()
    generation_id = scope.exact30_generation_id if source_scope == "EXACT30" else scope.oa112_generation_id
    return RagV2RetrievalCandidate(
        canonical_content=content,
        canonical_content_sha256=digest,
        canonical_https_url=f"https://evidence.example.com/{source_scope.lower()}/{index}",
        chunk_id="rag_v2_chk_" + digest[:32],
        document_id=None,
        embedding_profile_id=scope.embedding_profile_id,
        external_processing_eligible=True,
        generation_id=generation_id,
        heading_path=("Evidence",),
        locator={"section": f"Section {index}"},
        owner_user_id=None,
        policy_version=scope.policy_version,
        sanitized_display_name=None,
        scope_claim_id=scope.claim_id,
        session_id=scope.session_id,
        source_id=f"src_voyage_fixture_{index:03d}",
        source_revision_id=f"srv_voyage_fixture_{index:03d}",
        source_scope=source_scope,
        title=f"Public source {index}",
        topics=("RISK",),
    )


def _write_voyage_query_runtime(local_root: Path) -> None:
    os.chmod(local_root, 0o700)
    control = local_root / "control"
    control.mkdir(mode=0o700)
    runtime_path = control / "pre-s5-voyage-query-runtime.json"
    runtime_path.write_text(
        json.dumps(
            {
                "bgeEnabled": False,
                "ciDigest": "c" * 64,
                "headCommit": "a" * 40,
                "schemaVersion": "pre-s5-voyage-query-runtime/v1",
                "securityDigest": "d" * 64,
                "treeObject": "b" * 40,
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    os.chmod(runtime_path, 0o600)


def _write_voyage_query_packet(
    local_root: Path,
    *,
    question: str,
    scope_claim_id: str,
    tokenizer_sha256: str,
) -> None:
    issued_at = datetime.now(UTC).replace(microsecond=0)
    payload = {
        "byteCap": 1_048_576,
        "ciDigest": "c" * 64,
        "costCapMicrousd": 8_192,
        "date": "NONE",
        "endpoint": "/v1/contextualizedembeddings",
        "expiresAt": (issued_at + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        "headCommit": "a" * 40,
        "inputMicrousdPerToken": 1,
        "issuedAt": issued_at.isoformat().replace("+00:00", "Z"),
        "logicalCallCap": 1,
        "nonce": "ps5_voyage_query_composition_0001",
        "operation": "CONTEXTUALIZED_QUERY_EMBEDDING",
        "operator": "local-operator",
        "organizationTrainingOptOutEvidenceSha256": "f" * 64,
        "origin": "https://api.voyageai.com",
        "paymentMethodPrivacyEvidenceSha256": "0" * 64,
        "physicalCallCap": 1,
        "provider": "VOYAGE",
        "query": "SINGLE_RAG_QUERY_SHA256_BOUND",
        "querySha256": hashlib.sha256(question.encode()).hexdigest(),
        "rawArtifactCount": 0,
        "rateEvidenceSha256": "1" * 64,
        "retryCount": 0,
        "schemaVersion": "pre-s5-voyage-query-activation/v1",
        "scopeClaimSha256": hashlib.sha256(scope_claim_id.encode()).hexdigest(),
        "securityDigest": "d" * 64,
        "state": "APPROVED",
        "symbol": "NONE",
        "tokenizerSha256": tokenizer_sha256,
        "tokenCap": 8_192,
        "treeObject": "b" * 40,
    }
    path = local_root / "control" / "pre-s5-voyage-query-activation.json"
    path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    os.chmod(path, 0o600)


def _write_voyage_official_tokenizer(local_root: Path) -> str:
    """Use a real local `tokenizer.json` so the full gRPC composition cannot skip the official-count gate."""

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


class _ScopeReader:
    def __init__(self, scope: RagV2BundleScope) -> None:
        self._scope = scope

    def read_scope_by_claim(self, *, claim_id: str, session_id: str) -> RagV2BundleScope:
        assert claim_id == self._scope.claim_id
        assert session_id == self._scope.session_id
        return self._scope
