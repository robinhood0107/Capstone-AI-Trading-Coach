from __future__ import annotations

import hashlib
import json
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.rag import rag_v2_public_voyage_cli
from app.rag.rag_v2_external_exact30_voyage_runner import RagV2PublicVoyageComponentContext
from app.rag.rag_v2_oa112_voyage_runner import RagV2Oa112VoyageComponentContext
from app.rag.rag_v2_public_voyage_staging_repository import PublicVoyageEvaluationEvidence


def test_public_voyage_operator_entrypoint_is_registered() -> None:
    """The packet-gated CLI must be reachable without inventing an ad-hoc module command."""

    project_root = Path(__file__).resolve().parents[2]
    pyproject = tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))

    assert (
        pyproject["project"]["scripts"]["rag-public-voyage"]
        == "app.rag.rag_v2_public_voyage_cli:main"
    )


def test_public_voyage_operator_requires_explicit_distinct_private_source_and_output_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    output = tmp_path / "output"
    source.mkdir(mode=0o700)
    output.mkdir(mode=0o700)
    source.chmod(0o700)
    output.chmod(0o700)
    monkeypatch.setenv("CAPSTONE_RAG_SOURCE_ROOT", str(source))
    monkeypatch.setenv("CAPSTONE_RAG_OUTPUT_ROOT", str(output))
    monkeypatch.setenv("CAPSTONE_RAG_LOCAL_ROOT", str(source))

    assert rag_v2_public_voyage_cli._source_root() == source
    assert rag_v2_public_voyage_cli._output_root() == output

    monkeypatch.delenv("CAPSTONE_RAG_OUTPUT_ROOT")
    with pytest.raises(
        rag_v2_public_voyage_cli.PublicVoyageCliError,
        match="PUBLIC_VOYAGE_OUTPUT_ROOT_REQUIRED",
    ):
        rag_v2_public_voyage_cli._output_root()

    monkeypatch.setenv("CAPSTONE_RAG_OUTPUT_ROOT", str(source))
    with pytest.raises(
        rag_v2_public_voyage_cli.PublicVoyageCliError,
        match="PUBLIC_VOYAGE_ROOTS_MUST_BE_DISTINCT",
    ):
        rag_v2_public_voyage_cli._execution_roots()


def test_public_voyage_operator_rejects_symlink_or_non_private_output_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    output = tmp_path / "output"
    source.mkdir(mode=0o700)
    output.mkdir(mode=0o755)
    source.chmod(0o700)
    output.chmod(0o755)
    monkeypatch.setenv("CAPSTONE_RAG_SOURCE_ROOT", str(source))
    monkeypatch.setenv("CAPSTONE_RAG_OUTPUT_ROOT", str(output))

    with pytest.raises(
        rag_v2_public_voyage_cli.PublicVoyageCliError,
        match="PUBLIC_VOYAGE_OUTPUT_ROOT_BOUNDARY",
    ):
        rag_v2_public_voyage_cli._output_root()

    output.chmod(0o700)
    output.rmdir()
    output.symlink_to(source, target_is_directory=True)
    with pytest.raises(
        rag_v2_public_voyage_cli.PublicVoyageCliError,
        match="PUBLIC_VOYAGE_OUTPUT_ROOT_BOUNDARY",
    ):
        rag_v2_public_voyage_cli._output_root()


@pytest.mark.parametrize(
    "database_dsn",
    [
        "postgresql://decision_rag_writer:secret@127.0.0.1:5432/trading",
        "postgresql://decision_rag_writer:secret@localhost:55432/trading",
        "postgresql://decision_rag_writer:secret@127.0.0.1:55433/trading",
    ],
)
def test_fresh_voyage_operator_rejects_default_or_non_fresh_database_namespace(
    database_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CAPSTONE_PRE_S5_COMPOSE_PROJECT", "capstone-pre-s5-fresh")
    monkeypatch.setenv("POSTGRES_HOST_PORT", "55432")
    monkeypatch.setenv("REDIS_HOST_PORT", "56379")

    with pytest.raises(
        rag_v2_public_voyage_cli.PublicVoyageCliError,
        match="PUBLIC_VOYAGE_FRESH_NAMESPACE_REQUIRED",
    ):
        rag_v2_public_voyage_cli._require_fresh_database_namespace(database_dsn)


def test_fresh_voyage_operator_accepts_only_fixed_project_and_ports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CAPSTONE_PRE_S5_COMPOSE_PROJECT", "capstone-pre-s5-fresh")
    monkeypatch.setenv("POSTGRES_HOST_PORT", "55432")
    monkeypatch.setenv("REDIS_HOST_PORT", "56379")

    rag_v2_public_voyage_cli._require_fresh_database_namespace(
        "postgresql://decision_rag_writer:secret@127.0.0.1:55432/trading"
    )

    monkeypatch.setenv("CAPSTONE_PRE_S5_COMPOSE_PROJECT", "capstone-rag-local")
    with pytest.raises(
        rag_v2_public_voyage_cli.PublicVoyageCliError,
        match="PUBLIC_VOYAGE_FRESH_NAMESPACE_REQUIRED",
    ):
        rag_v2_public_voyage_cli._require_fresh_database_namespace(
            "postgresql://decision_rag_writer:secret@127.0.0.1:55432/trading"
        )


def test_public_voyage_cli_rejects_unknown_and_unconfigured_commands_before_a_provider_call(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invoked = False

    def stage(*, writer_dsn: str) -> object:
        nonlocal invoked
        del writer_dsn
        invoked = True
        raise AssertionError("provider seam must not be reached")

    monkeypatch.setattr(rag_v2_public_voyage_cli, "_stage_public_base", stage)
    assert rag_v2_public_voyage_cli.main(("unknown",)) == 2
    assert json.loads(capsys.readouterr().out) == {
        "code": "PUBLIC_VOYAGE_COMMAND_INVALID",
        "state": "FAILED",
    }

    monkeypatch.delenv("CAPSTONE_RAG_WRITER_DATABASE_DSN", raising=False)
    assert rag_v2_public_voyage_cli.main(("materialize-stage-public-base",)) == 2
    assert json.loads(capsys.readouterr().out) == {
        "code": "PUBLIC_VOYAGE_STAGE_DATABASE_DSN",
        "state": "FAILED",
    }
    assert invoked is False


def test_public_voyage_cli_prepares_content_free_batch_plan_without_provider_configuration(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch = SimpleNamespace(content_free_receipt=lambda: {"batchId": "rvb_" + "1" * 32})
    plan = SimpleNamespace(
        batches=(batch,),
        chunk_count=7_874,
        plan_sha256="a" * 64,
        source_count=142,
        token_count=250_000,
    )
    preparation = SimpleNamespace(
        content_free_receipt=lambda: {
            "checkpointReusedSourceCount": 142,
            "checkpointWrittenSourceCount": 0,
        },
        plan=plan,
    )
    stored: dict[str, object] = {}
    evaluation_inputs: list[object] = []

    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    monkeypatch.setattr(
        rag_v2_public_voyage_cli,
        "_prepare_public_base_batch_plan",
        lambda: preparation,
    )
    monkeypatch.setattr(
        rag_v2_public_voyage_cli,
        "_prepare_public_evaluation_inputs",
        lambda value: evaluation_inputs.append(value),
    )
    monkeypatch.setattr(rag_v2_public_voyage_cli, "_local_root", lambda: Path("/safe/local-root"))
    monkeypatch.setattr(
        rag_v2_public_voyage_cli,
        "write_benchmark_receipt",
        lambda **values: stored.update(values),
    )

    assert rag_v2_public_voyage_cli.main(("prepare-public-base-batches",)) == 0
    assert stored["approved_root"] == Path("/safe/local-root")
    assert evaluation_inputs == [preparation]
    stored_payload = json.loads(stored["payload"])
    assert stored_payload["providerPhysicalCallCount"] == 0
    assert stored_payload["checkpointExpectedSourceCount"] == 142
    assert json.loads(capsys.readouterr().out) == {
        "batchCount": 1,
        "chunkCount": 7_874,
        "code": "PUBLIC_VOYAGE_BATCH_PLAN_PREPARED",
        "planSha256": "a" * 64,
        "providerPhysicalCallCount": 0,
        "sourceCount": 142,
        "state": "PREPARED",
        "tokenCount": 250_000,
    }


def test_public_voyage_execution_binding_uses_fresh_voyage_evidence_not_historical_oa_download(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Voyage packet은 OA112 다운로드 당시 HEAD가 아니라 새 EXECUTION_HEAD에 결속해야 한다."""

    received: dict[str, object] = {}

    def load_binding(**values: object) -> SimpleNamespace:
        received.update(values)
        return SimpleNamespace(
            head_sha="1" * 40,
            tree_sha256="2" * 64,
            ci_digest="3" * 64,
            security_digest="4" * 64,
        )

    monkeypatch.setattr(
        rag_v2_public_voyage_cli,
        "load_oa112_execution_binding",
        load_binding,
    )

    binding = rag_v2_public_voyage_cli._execution_binding(local_root=tmp_path)

    assert received["approved_root"] == tmp_path
    assert received["relative_path"] == "pre-s5-voyage-execution-evidence.v1.json"
    assert binding.head_commit == "1" * 40
    assert binding.tree_object == "2" * 64


def test_public_voyage_cli_stages_one_pair_and_emits_only_content_free_ids(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: dict[str, object] = {}
    pair = SimpleNamespace(
        document_embedding_provider_physical_call_count=1,
        exact30=SimpleNamespace(component_generation_id="rgr_0123456789abcdef0123456789abcdef"),
        oa112=SimpleNamespace(component_generation_id="rgr_abcdef0123456789abcdef0123456789"),
    )

    def stage(*, writer_dsn: str) -> object:
        received["writer_dsn"] = writer_dsn
        return pair

    def write(*, local_root: Path, pair: object) -> None:
        received["local_root"] = local_root
        received["pair"] = pair

    monkeypatch.setenv("CAPSTONE_RAG_WRITER_DATABASE_DSN", "postgresql://rag-writer")
    monkeypatch.setattr(rag_v2_public_voyage_cli, "_stage_public_base", stage)
    monkeypatch.setattr(rag_v2_public_voyage_cli, "_local_root", lambda: Path("/safe/local-root"))
    monkeypatch.setattr(rag_v2_public_voyage_cli, "_write_staged_pair_receipt", write)

    assert rag_v2_public_voyage_cli.main(("materialize-stage-public-base",)) == 0
    assert received == {
        "writer_dsn": "postgresql://rag-writer",
        "local_root": Path("/safe/local-root"),
        "pair": pair,
    }
    assert json.loads(capsys.readouterr().out) == {
        "code": "PUBLIC_VOYAGE_PUBLIC_BASE_STAGED",
        "documentEmbeddingProviderPhysicalCallCount": 1,
        "embeddingProfileId": "voyage_context_4_1024_v1",
        "exact30GenerationId": "rgr_0123456789abcdef0123456789abcdef",
        "oa112GenerationId": "rgr_abcdef0123456789abcdef0123456789",
        "state": "STAGED",
    }


def test_public_voyage_cli_preserves_one_consumed_attempt_when_postcall_staging_fails(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CAPSTONE_RAG_WRITER_DATABASE_DSN", "postgresql://rag-writer")

    def stage(*, writer_dsn: str) -> object:
        del writer_dsn
        raise rag_v2_public_voyage_cli.PublicVoyageCliError(
            "PUBLIC_VOYAGE_POSTCALL_STAGING_REQUIRED",
            attempt_summary={
                "externalPhysicalCalls": 1,
                "logicalCallsConsumed": 1,
                "rawArtifactCount": 0,
                "state": "COMMITTED",
            },
        )

    monkeypatch.setattr(rag_v2_public_voyage_cli, "_stage_public_base", stage)
    assert rag_v2_public_voyage_cli.main(("materialize-stage-public-base",)) == 2
    assert json.loads(capsys.readouterr().out) == {
        "attempt": {
            "externalPhysicalCalls": 1,
            "logicalCallsConsumed": 1,
            "rawArtifactCount": 0,
            "state": "COMMITTED",
        },
        "code": "PUBLIC_VOYAGE_POSTCALL_STAGING_REQUIRED",
        "state": "FAILED",
    }


def test_public_voyage_document_batch_failure_summary_keeps_only_safe_provider_class() -> None:
    """실패 batch는 HTTP 숫자나 원문 없이 bounded 상태 분류만 operator에게 돌려줘야 한다."""

    transport = SimpleNamespace(
        content_free_summary=lambda: {
            "externalPhysicalCalls": 1,
            "logicalCallsConsumed": 1,
            "providerStatusClass": "HTTP_4XX",
            "rawArtifactCount": 0,
            "state": "UNKNOWN_BILLING",
        }
    )
    preparation = SimpleNamespace(plan=SimpleNamespace(batches=tuple(range(31))))
    accumulator = SimpleNamespace(completed_batch_ids=())
    batch = SimpleNamespace(batch_id="ps5_voyage_doc_0001_0123456789abcdef")

    summary = rag_v2_public_voyage_cli._document_batch_failure_summary(
        transport=transport,
        preparation=preparation,
        accumulator=accumulator,
        batch=batch,
    )

    assert summary == {
        "batchCount": 31,
        "completedBatchCount": 0,
        "externalPhysicalCalls": 1,
        "failedBatchId": "ps5_voyage_doc_0001_0123456789abcdef",
        "logicalCallsConsumed": 1,
        "providerStatusClass": "HTTP_4XX",
        "rawArtifactCount": 0,
        "state": "UNKNOWN_BILLING",
    }
    assert "4XX" in json.dumps(summary, sort_keys=True)
    assert "429" not in json.dumps(summary, sort_keys=True)


def test_public_voyage_cli_runs_the_packet_gated_10_plus_112_evaluation_only_after_stage(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pair = SimpleNamespace(
        document_embedding_provider_physical_call_count=1,
        exact30=SimpleNamespace(component_generation_id="rgr_0123456789abcdef0123456789abcdef"),
        oa112=SimpleNamespace(component_generation_id="rgr_abcdef0123456789abcdef0123456789"),
    )
    evaluation = SimpleNamespace(
        exact30=SimpleNamespace(provider_physical_call_count=1),
        oa112=SimpleNamespace(provider_physical_call_count=1),
    )
    received: dict[str, object] = {}

    def stage_and_evaluate(*, writer_dsn: str) -> tuple[object, object]:
        received["writer_dsn"] = writer_dsn
        return pair, evaluation

    monkeypatch.setenv("CAPSTONE_RAG_WRITER_DATABASE_DSN", "postgresql://rag-writer")
    monkeypatch.setattr(
        rag_v2_public_voyage_cli, "_stage_and_evaluate_public_base", stage_and_evaluate
    )

    assert rag_v2_public_voyage_cli.main(("materialize-stage-evaluate-public-base",)) == 0
    assert received == {"writer_dsn": "postgresql://rag-writer"}
    assert json.loads(capsys.readouterr().out) == {
        "code": "PUBLIC_VOYAGE_PUBLIC_BASE_EVALUATED",
        "documentEmbeddingProviderPhysicalCallCount": 1,
        "embeddingProfileId": "voyage_context_4_1024_v1",
        "exact30GenerationId": "rgr_0123456789abcdef0123456789abcdef",
        "exact30QueryPhysicalCallCount": 1,
        "oa112GenerationId": "rgr_abcdef0123456789abcdef0123456789",
        "oa112QueryPhysicalCallCount": 1,
        "state": "EVALUATED",
    }


def test_public_voyage_cli_evaluates_and_activates_only_the_staged_pair(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exact_context = SimpleNamespace(component_generation_id="rgr_0123456789abcdef0123456789abcdef")
    oa_context = SimpleNamespace(component_generation_id="rgr_abcdef0123456789abcdef0123456789")
    pair = SimpleNamespace(
        bundle_manifest_sha256="a" * 64,
        exact30=exact_context,
        oa112=oa_context,
    )
    evidence = SimpleNamespace(
        exact30=SimpleNamespace(provider_physical_call_count=1),
        oa112=SimpleNamespace(provider_physical_call_count=1),
    )
    calls: list[tuple[object, object]] = []

    class _WriterRepository:
        def __init__(self, *, database_dsn: str) -> None:
            assert database_dsn == "postgresql://rag-writer"

        def evaluate(self, *, context: object, evidence: object) -> object:
            calls.append((context, evidence))
            return object()

    monkeypatch.setenv("CAPSTONE_RAG_WRITER_DATABASE_DSN", "postgresql://rag-writer")
    monkeypatch.setattr(rag_v2_public_voyage_cli, "_local_root", lambda: Path("/safe/local-root"))
    monkeypatch.setattr(rag_v2_public_voyage_cli, "_load_staged_pair", lambda *, local_root: pair)
    monkeypatch.setattr(
        rag_v2_public_voyage_cli,
        "_load_evaluation_pair",
        lambda *, local_root, pair: evidence,
    )
    monkeypatch.setattr(
        rag_v2_public_voyage_cli, "PsycopgRagV2PublicVoyageStagingRepository", _WriterRepository
    )

    assert rag_v2_public_voyage_cli.main(("evaluate-public-base",)) == 0
    assert calls == [(exact_context, evidence.exact30), (oa_context, evidence.oa112)]
    assert json.loads(capsys.readouterr().out) == {
        "code": "PUBLIC_VOYAGE_PUBLIC_BASE_EVALUATED",
        "embeddingProfileId": "voyage_context_4_1024_v1",
        "exact30GenerationId": "rgr_0123456789abcdef0123456789abcdef",
        "exact30QueryPhysicalCallCount": 1,
        "oa112GenerationId": "rgr_abcdef0123456789abcdef0123456789",
        "oa112QueryPhysicalCallCount": 1,
        "state": "EVALUATED",
    }

    # A new process may activate only the same staged receipt; it cannot pass arbitrary component IDs.
    received: dict[str, object] = {}

    class _ActivationRepository:
        def __init__(self, *, database_dsn: str) -> None:
            received["database_dsn"] = database_dsn

        def activate(self, *, request: object) -> SimpleNamespace:
            received["request"] = request
            return SimpleNamespace(
                embedding_profile_id="voyage_context_4_1024_v1",
                exact30_generation_id="rgr_0123456789abcdef0123456789abcdef",
                oa112_generation_id="rgr_abcdef0123456789abcdef0123456789",
                previous_pointer_version=1,
                new_pointer_version=2,
                state="ACTIVE",
            )

    monkeypatch.setenv("CAPSTONE_RAG_ADMIN_DATABASE_DSN", "postgresql://rag-admin")
    monkeypatch.setattr(
        rag_v2_public_voyage_cli, "PublicVoyageActivationRequest", lambda **values: values
    )
    monkeypatch.setattr(
        rag_v2_public_voyage_cli,
        "PsycopgRagV2PublicVoyageActivationRepository",
        _ActivationRepository,
    )
    assert rag_v2_public_voyage_cli.main(("activate-public-base",)) == 0
    assert received == {
        "database_dsn": "postgresql://rag-admin",
        "request": {"exact30": exact_context, "oa112": oa_context},
    }
    assert json.loads(capsys.readouterr().out) == {
        "code": "PUBLIC_VOYAGE_BASE_ACTIVE",
        "embeddingProfileId": "voyage_context_4_1024_v1",
        "exact30GenerationId": "rgr_0123456789abcdef0123456789abcdef",
        "newPointerVersion": 2,
        "oa112GenerationId": "rgr_abcdef0123456789abcdef0123456789",
        "previousPointerVersion": 1,
        "state": "ACTIVE",
    }


def test_public_voyage_pair_receipts_bind_context_member_hashes_and_exact_query_counts(
    tmp_path: Path,
) -> None:
    local_root = tmp_path / "private"
    local_root.mkdir(mode=0o700)
    pair = _pair()

    rag_v2_public_voyage_cli._write_staged_pair_receipt(local_root=local_root, pair=pair)
    loaded = rag_v2_public_voyage_cli._load_staged_pair(local_root=local_root)
    assert loaded.bundle_manifest_sha256 == pair.bundle_manifest_sha256
    assert loaded.exact30.member_digests == pair.exact30.member_digests
    assert loaded.oa112.member_digests == pair.oa112.member_digests

    with pytest.raises(
        rag_v2_public_voyage_cli.PublicVoyageCliError, match="PUBLIC_VOYAGE_EVALUATION_RECEIPT"
    ):
        rag_v2_public_voyage_cli.write_public_voyage_pair_evaluation_receipt(
            local_root=local_root,
            pair=pair,
            exact30=_evidence(0),
            oa112=_evidence(1),
        )

    rag_v2_public_voyage_cli.write_public_voyage_pair_evaluation_receipt(
        local_root=local_root,
        pair=pair,
        exact30=_evidence(1),
        oa112=_evidence(1),
    )
    evaluation = rag_v2_public_voyage_cli._load_evaluation_pair(local_root=local_root, pair=pair)
    assert evaluation.exact30.provider_physical_call_count == 1
    assert evaluation.oa112.provider_physical_call_count == 1


def _pair() -> rag_v2_public_voyage_cli.PublicVoyageStagedPair:
    exact_generation = "rgr_" + "1" * 32
    oa_generation = "rgr_" + "2" * 32
    exact_manifest = "3" * 64
    oa_manifest = "4" * 64
    return rag_v2_public_voyage_cli.PublicVoyageStagedPair(
        bundle_manifest_sha256="5" * 64,
        exact30=RagV2PublicVoyageComponentContext(
            component_scope="EXACT30",
            component_generation_id=exact_generation,
            materialization_run_id="rgr_run_" + "6" * 32,
            generation_hash="7" * 64,
            manifest_hash=exact_manifest,
            expected_source_count=30,
            expected_chunk_count=30,
            embedding_profile_id="voyage_context_4_1024_v1",
            member_digests=tuple(_digest(f"exact-{index}") for index in range(30)),
            source_card_corpus_manifest_sha256="8" * 64,
        ),
        oa112=RagV2Oa112VoyageComponentContext(
            component_scope="OA112",
            component_generation_id=oa_generation,
            materialization_run_id="rgr_run_" + "9" * 32,
            generation_hash="a" * 64,
            manifest_hash=oa_manifest,
            expected_source_count=112,
            expected_chunk_count=112,
            embedding_profile_id="voyage_context_4_1024_v1",
            member_digests=tuple(_digest(f"oa-{index}") for index in range(112)),
            registry_id="oa112-fixture-v1",
            registry_digest="b" * 64,
        ),
        document_embedding_provider_physical_call_count=1,
    )


def _evidence(provider_physical_call_count: int) -> PublicVoyageEvaluationEvidence:
    return PublicVoyageEvaluationEvidence(
        evaluation_digest=_digest(f"evaluation-{provider_physical_call_count}"),
        evaluation_scope_claim_sha256=_digest("public-voyage-evaluation-scope"),
        exact_top5_hit_rate=1.0,
        track_recall_at5=0.8,
        citation_coverage=0.8,
        direct_advice_block_rate=1.0,
        cross_owner_leak_count=0,
        mixed_profile_row_count=0,
        owner_delete_residual_row_count=0,
        warm_p95_millis=123.0,
        provider_physical_call_count=provider_physical_call_count,
    )


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
