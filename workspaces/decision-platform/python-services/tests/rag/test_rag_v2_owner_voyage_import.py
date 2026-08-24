from __future__ import annotations

import hashlib
import importlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import psycopg
import pytest

from app.rag.local_document_parser import LocalDocumentParser
from app.rag.pre_s5_voyage_transport import (
    PreS5VoyageDocumentBatchResult,
    PreS5VoyageTransportError,
)
from app.rag.rag_v2_bge_materializer import (
    RagV2OwnerDocumentRequest,
    materialize_owner_bge_document,
    prepare_owner_document_for_embedding,
)
from app.rag.rag_v2_external_exact30_voyage_runner import (
    VoyagePreChunkedChunk,
    VoyagePreChunkedDocumentGroup,
)
from app.rag.rag_v2_owner_bge_deletion import PsycopgRagV2OwnerBgeDeletionRepository
from app.rag.rag_v2_owner_bge_staging import (
    OwnerBgeStagingError,
    OwnerBgeStagingMetadata,
    PsycopgRagV2OwnerBgeStagingRepository,
)
from app.rag.rag_v2_owner_overlay import PsycopgRagV2OwnerOverlayRepository


@dataclass
class _TokenCounter:
    counts: dict[str, int]
    model: str = "voyage-context-4"
    tokenizer_sha256: str = "a" * 64

    def count_texts(self, *, texts: tuple[str, ...], token_cap: int) -> int:
        total = sum(self.counts[text] for text in texts)
        if total > token_cap:
            raise ValueError("token cap")
        return total


class _WhitespaceTokenizer:
    def token_spans(self, text: str) -> tuple[tuple[int, int], ...]:
        import re

        return tuple((match.start(), match.end()) for match in re.finditer(r"\S+", text))

    def take_prefix(self, text: str, maximum_tokens: int) -> str:
        spans = self.token_spans(text)
        return text[: spans[min(len(spans), maximum_tokens) - 1][1]] if spans else ""

    def take_suffix(self, text: str, maximum_tokens: int) -> str:
        spans = self.token_spans(text)
        return text[spans[max(0, len(spans) - maximum_tokens)][0] :] if spans else ""


class _UnitEmbedder:
    def embed(self, texts: tuple[str, ...]) -> np.ndarray:
        vectors = np.zeros((len(texts), 1024), dtype=np.float32)
        vectors[:, 0] = 1.0
        return vectors


class _Transport:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, object]] = []

    def embed_document_batch(
        self,
        *,
        batch_plan_sha256: str,
        batch: object,
    ) -> PreS5VoyageDocumentBatchResult:
        self.calls.append((batch_plan_sha256, batch))
        if self.fail:
            raise PreS5VoyageTransportError("PRE_S5_VOYAGE_RESPONSE_INVALID")
        chunk_count = batch.chunk_count
        token_count = batch.token_count
        vectors = np.zeros((chunk_count, 1024), dtype=np.float32)
        vectors[:, 0] = 1.0
        return PreS5VoyageDocumentBatchResult(
            vectors=vectors,
            expected_input_tokens=token_count,
            provider_total_tokens=token_count,
            actual_cost_microusd=token_count,
        )

    def content_free_summary(self) -> dict[str, object]:
        return {"responseValidationLeaf": "VECTOR_NORM"}


class _Repository:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def complete(self, **values: object) -> dict[str, object]:
        self.calls.append(values)
        return {
            "componentGenerationId": "rgr_" + "9" * 32,
            "documentCount": 9,
            "chunkCount": 9,
            "state": "STAGED",
        }


class _AttemptRepository(_Repository):
    def __init__(self) -> None:
        super().__init__()
        self.claims: list[dict[str, object]] = []
        self.unknown: list[dict[str, object]] = []

    def claim_attempt(self, **values: object) -> None:
        self.claims.append(values)

    def mark_unknown_billing(self, **values: object) -> None:
        self.unknown.append(values)


class _FailingCompletionRepository(_AttemptRepository):
    def complete(self, **values: object) -> dict[str, object]:
        self.calls.append(values)
        raise RuntimeError("synthetic atomic completion failure")


def test_owner_voyage_plan_binds_nine_tickets_to_one_content_free_batch_and_one_atomic_completion() -> (
    None
):
    module = importlib.import_module("app.rag.rag_v2_owner_voyage_import")
    texts = tuple(f"safe owner document {index}" for index in range(1, 10))
    counter = _TokenCounter(dict.fromkeys(texts, 10))
    items = tuple(
        _item(module, index=index, text=text, token_count=10) for index, text in enumerate(texts, 1)
    )

    plan = module.build_owner_voyage_import_plan(
        owner_user_id="usr_demo_user",
        items=items,
        token_counter=counter,
    )
    transport = _Transport()
    repository = _Repository()
    receipt = module.RagV2OwnerVoyageImportExecutor(repository=repository).execute(
        plan=plan,
        transport=transport,
    )

    assert len(transport.calls) == 1
    assert len(repository.calls) == 1
    assert receipt.document_count == 9
    assert receipt.chunk_count == 9
    assert receipt.state == "STAGED"
    summary = json.dumps(plan.content_free_receipt(), sort_keys=True)
    assert "safe owner document" not in summary
    assert "rti_" not in summary
    assert "usr_demo_user" not in summary
    assert plan.batch.token_count == 90
    assert plan.batch.group_count == 9
    assert plan.batch.batch_count == 1


def test_owner_voyage_item_uses_one_safe_parse_and_keeps_path_out_of_staging_payload(
    tmp_path: Path,
) -> None:
    module = importlib.import_module("app.rag.rag_v2_owner_voyage_import")
    source = tmp_path / "owner-note.txt"
    source.write_text("Safe portfolio evidence without personal data.", encoding="utf-8")
    prepared = prepare_owner_document_for_embedding(
        parser=LocalDocumentParser(),
        tokenizer=_WhitespaceTokenizer(),
        request=RagV2OwnerDocumentRequest(
            approved_root=tmp_path,
            relative_path="owner-note.txt",
            document_id="doc_owner_voyage_safe_001",
            source_id="src_owner_voyage_safe_001",
            source_revision_id="srv_owner_voyage_safe_001",
            language_tags=("en",),
            embedding_profile_id="voyage_context_4_1024_v1",
        ),
        external_processing_authorized=True,
    )

    item = module.build_owner_voyage_import_item(
        import_ticket_id="rti_11111111111111111111111111111111",
        prepared=prepared,
        metadata=OwnerBgeStagingMetadata(
            sanitized_display_name="Synthetic owner note",
            retrieval_topics=("FINANCIAL_ENGINEERING",),
        ),
        tokenizer_version="voyage-context-4-official-tokenizer-v1",
    )

    encoded = json.dumps(item.staging_payload, ensure_ascii=False, sort_keys=True)
    assert item.group.context_set_hash == prepared.embedding_inputs[0].context_set_hash
    assert item.staging_payload["schemaVersion"] == 3
    assert item.staging_payload["embeddings"] == []
    assert str(tmp_path) not in encoded
    assert "owner-note.txt" not in encoded


@pytest.mark.parametrize(
    ("document_token_count", "expected_code"),
    [
        (32_001, "OWNER_VOYAGE_IMPORT_TOO_LARGE"),
        (55_001, "OWNER_VOYAGE_IMPORT_TOO_LARGE"),
    ],
)
def test_owner_voyage_plan_rejects_context_or_request_cap_before_transport(
    document_token_count: int,
    expected_code: str,
) -> None:
    module = importlib.import_module("app.rag.rag_v2_owner_voyage_import")
    text = "oversized owner document"
    counter = _TokenCounter({text: document_token_count})

    with pytest.raises(module.OwnerVoyageImportError, match=expected_code):
        module.build_owner_voyage_import_plan(
            owner_user_id="usr_demo_user",
            items=(_item(module, index=1, text=text, token_count=min(document_token_count, 600)),),
            token_counter=counter,
        )


def test_owner_voyage_failure_stops_after_one_call_without_completion_or_bge_fallback() -> None:
    module = importlib.import_module("app.rag.rag_v2_owner_voyage_import")
    text = "one safe owner document"
    plan = module.build_owner_voyage_import_plan(
        owner_user_id="usr_demo_user",
        items=(_item(module, index=1, text=text, token_count=10),),
        token_counter=_TokenCounter({text: 10}),
    )
    transport = _Transport(fail=True)
    repository = _Repository()

    with pytest.raises(PreS5VoyageTransportError, match="PRE_S5_VOYAGE_RESPONSE_INVALID"):
        module.RagV2OwnerVoyageImportExecutor(repository=repository).execute(
            plan=plan,
            transport=transport,
        )

    assert len(transport.calls) == 1
    assert repository.calls == []


def test_owner_voyage_failure_preserves_only_content_free_validation_leaf() -> None:
    module = importlib.import_module("app.rag.rag_v2_owner_voyage_import")
    text = "one safe owner document"
    plan = module.build_owner_voyage_import_plan(
        owner_user_id="usr_demo_user",
        items=(_item(module, index=1, text=text, token_count=10),),
        token_counter=_TokenCounter({text: 10}),
    )
    transport = _Transport(fail=True)
    repository = _AttemptRepository()

    with pytest.raises(PreS5VoyageTransportError, match="PRE_S5_VOYAGE_RESPONSE_INVALID"):
        module.RagV2OwnerVoyageImportExecutor(repository=repository).execute(
            plan=plan,
            transport=transport,
        )

    assert repository.unknown == [{"response_validation_leaf": "VECTOR_NORM"}]


def test_owner_voyage_completion_failure_terminalizes_unknown_billing_without_retry() -> None:
    module = importlib.import_module("app.rag.rag_v2_owner_voyage_import")
    text = "one safe owner document"
    plan = module.build_owner_voyage_import_plan(
        owner_user_id="usr_demo_user",
        items=(_item(module, index=1, text=text, token_count=10),),
        token_counter=_TokenCounter({text: 10}),
    )
    transport = _Transport()
    repository = _FailingCompletionRepository()

    with pytest.raises(RuntimeError, match="atomic completion failure"):
        module.RagV2OwnerVoyageImportExecutor(repository=repository).execute(
            plan=plan,
            transport=transport,
        )

    assert len(transport.calls) == 1
    assert len(repository.calls) == 1
    assert repository.unknown == [{"response_validation_leaf": None}]


def test_owner_voyage_attempt_lease_binds_manifest_packet_nonce_and_exact_ticket_set() -> None:
    module = importlib.import_module("app.rag.rag_v2_owner_voyage_import")
    text = "one safe owner document"
    plan = module.build_owner_voyage_import_plan(
        owner_user_id="usr_demo_user",
        items=(_item(module, index=1, text=text, token_count=10),),
        token_counter=_TokenCounter({text: 10}),
    )
    repository = _AttemptRepository()
    lease = module.OwnerVoyageAttemptLease(
        repository=repository,
        plan=plan,
        packet_sha256="b" * 64,
        approval_manifest_sha256="c" * 64,
        nonce_sha256="d" * 64,
    )

    lease.claim_attempt(now=datetime(2026, 8, 13, tzinfo=UTC))
    lease.mark_unknown_billing()

    assert repository.claims == [
        {
            "approval_manifest_sha256": "c" * 64,
            "chunk_count": 1,
            "document_count": 1,
            "expected_input_tokens": 10,
            "nonce_sha256": "d" * 64,
            "owner_user_id": "usr_demo_user",
            "packet_sha256": "b" * 64,
            "plan_sha256": plan.plan_sha256,
            "ticket_ids": ("rti_00000000000000000000000000000001",),
            "ticket_set_sha256": hashlib.sha256(
                b"rti_00000000000000000000000000000001\n"
            ).hexdigest(),
        }
    ]
    assert repository.unknown == [
        {
            "owner_user_id": "usr_demo_user",
            "packet_sha256": "b" * 64,
            "plan_sha256": plan.plan_sha256,
            "response_validation_leaf": None,
        }
    ]


def test_psycopg_owner_voyage_repository_completes_usage_and_staging_in_one_function_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("app.rag.rag_v2_owner_voyage_import")
    calls: list[tuple[str, tuple[object, ...]]] = []

    class _Cursor:
        def fetchone(self) -> tuple[object, ...]:
            return ("rgr_" + "9" * 32, 9, 9, "STAGED")

    class _Connection:
        def __enter__(self) -> _Connection:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def transaction(self) -> _Connection:
            return self

        def execute(self, sql: str, params: tuple[object, ...] = ()) -> _Cursor:
            calls.append((" ".join(sql.split()), params))
            return _Cursor()

    monkeypatch.setattr(module.psycopg, "connect", lambda *_args, **_kwargs: _Connection())
    monkeypatch.setattr(module, "_attest_writer_connection", lambda _connection: None)
    repository = module.PsycopgOwnerVoyageRepository(database_dsn="postgresql://private")

    row = repository.complete(
        owner_user_id="usr_demo_user",
        plan_sha256="a" * 64,
        packet_sha256="b" * 64,
        expected_input_tokens=90,
        provider_total_tokens=91,
        actual_cost_microusd=91,
        items=tuple({"importTicketId": f"rti_{index:032x}"} for index in range(1, 10)),
    )

    completion_calls = [
        sql for sql, _params in calls if "complete_rag_v2_owner_voyage_import" in sql
    ]
    assert len(completion_calls) == 1
    assert row == {
        "componentGenerationId": "rgr_" + "9" * 32,
        "documentCount": 9,
        "chunkCount": 9,
        "state": "STAGED",
    }


def test_owner_voyage_postgres_attempt_commits_usage_ticket_and_vector_atomically(
    isolated_postgres_cluster: dict[str, str],
    tmp_path: Path,
) -> None:
    module = importlib.import_module("app.rag.rag_v2_owner_voyage_import")
    ticket_id = "rti_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    source = tmp_path / "owner-voyage-note.txt"
    source.write_text(
        "Safe portfolio allocation evidence for a deterministic owner retrieval test.",
        encoding="utf-8",
    )
    prepared = prepare_owner_document_for_embedding(
        parser=LocalDocumentParser(),
        tokenizer=_WhitespaceTokenizer(),
        request=RagV2OwnerDocumentRequest(
            approved_root=tmp_path,
            relative_path="owner-voyage-note.txt",
            document_id="doc_owner_voyage_atomic_001",
            source_id="src_owner_voyage_atomic_001",
            source_revision_id="srv_owner_voyage_atomic_001",
            language_tags=("en",),
            embedding_profile_id="voyage_context_4_1024_v1",
        ),
        external_processing_authorized=True,
    )
    item = module.build_owner_voyage_import_item(
        import_ticket_id=ticket_id,
        prepared=prepared,
        metadata=OwnerBgeStagingMetadata(
            sanitized_display_name="Synthetic owner Voyage note",
            retrieval_topics=("FINANCIAL_ENGINEERING",),
        ),
        tokenizer_version="voyage-context-4-official-tokenizer-v1",
    )
    counts = {chunk.canonical_text: chunk.token_count for chunk in item.group.chunks}
    plan = module.build_owner_voyage_import_plan(
        owner_user_id="usr_demo_user",
        items=(item,),
        token_counter=_TokenCounter(counts),
    )
    _grant_external_consent_and_issue_voyage_ticket(
        isolated_postgres_cluster["app_dsn"],
        owner_user_id="usr_demo_user",
        event_character="a",
        ticket_ids=(ticket_id, "rti_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"),
    )
    bge_race_ticket = "rti_cccccccccccccccccccccccccccccccc"
    _issue_profile_ticket(
        isolated_postgres_cluster["app_dsn"],
        owner_user_id="usr_demo_user",
        ticket_id=bge_race_ticket,
        embedding_profile_id="bge_m3_local_1024_v1",
    )
    with psycopg.connect(isolated_postgres_cluster["admin_dsn"]) as connection:
        assert connection.execute(
            """
            SELECT action, public_consent_event_id, policy_digest, processor_set_digest
            FROM rag_v2_immutable_consent_events
            WHERE owner_user_id = 'usr_demo_user'
            ORDER BY created_at DESC, consent_event_id DESC
            LIMIT 1
            """
        ).fetchone() == (
            "GRANT",
            "rce_owner_voyage_aaaaaaaaaaaa",
            "2" * 64,
            "3" * 64,
        )
    repository = module.PsycopgOwnerVoyageRepository(
        database_dsn=isolated_postgres_cluster["rag_writer_dsn"],
    )
    missing_text = "safe document without an issued ticket"
    missing_plan = module.build_owner_voyage_import_plan(
        owner_user_id="usr_demo_user",
        items=(_item(module, index=3, text=missing_text, token_count=10),),
        token_counter=_TokenCounter({missing_text: 10}),
    )
    missing_ticket_lease = module.OwnerVoyageAttemptLease(
        repository=repository,
        plan=missing_plan,
        packet_sha256="e" * 64,
        approval_manifest_sha256="f" * 64,
        nonce_sha256="1" * 64,
    )
    with pytest.raises(module.OwnerVoyageImportError, match="OWNER_VOYAGE_ATTEMPT_REJECTED"):
        missing_ticket_lease.claim_attempt(now=datetime(2026, 8, 13, tzinfo=UTC))
    with psycopg.connect(isolated_postgres_cluster["admin_dsn"]) as connection:
        assert connection.execute(
            "SELECT count(*) FROM rag_v2_owner_voyage_import_attempts"
        ).fetchone() == (0,)
    lease = module.OwnerVoyageAttemptLease(
        repository=repository,
        plan=plan,
        packet_sha256="b" * 64,
        approval_manifest_sha256="c" * 64,
        nonce_sha256="d" * 64,
    )
    lease.claim_attempt(now=datetime(2026, 8, 13, tzinfo=UTC))

    with pytest.raises(OwnerBgeStagingError, match="OWNER_BGE_STAGE_REJECTED"):
        PsycopgRagV2OwnerBgeStagingRepository(
            database_dsn=isolated_postgres_cluster["rag_writer_dsn"],
        ).stage(
            owner_user_id="usr_demo_user",
            import_ticket_id=bge_race_ticket,
            materialized=_materialized_bge_race(tmp_path, identity="user"),
            metadata=OwnerBgeStagingMetadata(
                sanitized_display_name="Synthetic owner BGE race",
                retrieval_topics=("FINANCIAL_ENGINEERING",),
            ),
        )

    poisoned_payload = dict(item.staging_payload)
    poisoned_payload["embeddings"] = [
        {
            "chunkId": chunk.chunk_id,
            "contextSetHash": item.group.context_set_hash,
            "embeddingInputHash": chunk.embedding_input_hash,
            "embedding": [1.0] + [0.0] * 1023,
        }
        for chunk in item.group.chunks
    ]
    with pytest.raises(module.OwnerVoyageImportError, match="OWNER_VOYAGE_COMPLETION_REJECTED"):
        repository.complete(
            owner_user_id="usr_demo_user",
            plan_sha256=plan.plan_sha256,
            packet_sha256="b" * 64,
            expected_input_tokens=plan.batch.token_count,
            provider_total_tokens=plan.batch.token_count,
            actual_cost_microusd=plan.batch.token_count,
            items=(
                {
                    "importTicketId": "rti_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                    "stagingPayload": poisoned_payload,
                },
            ),
        )

    receipt = module.RagV2OwnerVoyageImportExecutor(repository=lease).execute(
        plan=plan,
        transport=_Transport(),
    )

    assert receipt.document_count == 1
    assert receipt.chunk_count == plan.batch.chunk_count
    assert receipt.embedding_profile_id == "voyage_context_4_1024_v1"
    with psycopg.connect(isolated_postgres_cluster["admin_dsn"]) as connection:
        assert connection.execute(
            """
            SELECT state, document_count, chunk_count, expected_input_tokens,
                   provider_total_tokens, actual_cost_microusd, response_validation_leaf
            FROM rag_v2_owner_voyage_import_attempts
            WHERE plan_sha256 = %s
            """,
            (plan.plan_sha256,),
        ).fetchone() == (
            "COMMITTED",
            1,
            plan.batch.chunk_count,
            plan.batch.token_count,
            plan.batch.token_count,
            plan.batch.token_count,
            None,
        )
        assert connection.execute(
            """
            SELECT ticket.state, ticket.policy_version, ticket.embedding_profile_id,
                   generation.embedding_profile_id, generation.actual_chunk_count,
                   count(embedding.chunk_id),
                   bool_and(source.external_processing_eligible),
                   bool_and(source.external_embedding_allowed),
                   bool_and(source.external_generation_allowed)
            FROM rag_v2_immutable_import_tickets AS ticket
            JOIN rag_v2_immutable_materialization_runs AS run
              ON run.materialization_run_id = ticket.consumer_run_id
            JOIN rag_v2_immutable_component_generations AS generation
              ON generation.component_generation_id = run.component_generation_id
            JOIN rag_v2_immutable_generation_memberships AS membership
              ON membership.component_generation_id = generation.component_generation_id
            JOIN rag_v2_immutable_generation_embeddings AS embedding
              ON embedding.component_generation_id = membership.component_generation_id
             AND embedding.chunk_id = membership.chunk_id
            JOIN rag_v2_immutable_source_revisions AS source
              ON source.source_revision_id = membership.source_revision_id
            WHERE ticket.ticket_hash = encode(digest(%s, 'sha256'), 'hex')
            GROUP BY ticket.state, ticket.policy_version, ticket.embedding_profile_id,
                     generation.embedding_profile_id, generation.actual_chunk_count
            """,
            (ticket_id,),
        ).fetchone() == (
            "CONSUMED",
            "RAG_V2_OWNER_DOCUMENT_V2",
            "voyage_context_4_1024_v1",
            "voyage_context_4_1024_v1",
            plan.batch.chunk_count,
            plan.batch.chunk_count,
            True,
            True,
            True,
        )


def test_staged_bge_library_rejects_voyage_reservation_before_provider(
    isolated_postgres_cluster: dict[str, str],
    tmp_path: Path,
) -> None:
    module = importlib.import_module("app.rag.rag_v2_owner_voyage_import")
    bge_ticket = "rti_dddddddddddddddddddddddddddddddd"
    _issue_profile_ticket(
        isolated_postgres_cluster["app_dsn"],
        owner_user_id="usr_demo_admin",
        ticket_id=bge_ticket,
        embedding_profile_id="bge_m3_local_1024_v1",
    )
    PsycopgRagV2OwnerBgeStagingRepository(
        database_dsn=isolated_postgres_cluster["rag_writer_dsn"],
    ).stage(
        owner_user_id="usr_demo_admin",
        import_ticket_id=bge_ticket,
        materialized=_materialized_bge_race(tmp_path, identity="admin"),
        metadata=OwnerBgeStagingMetadata(
            sanitized_display_name="Synthetic admin BGE note",
            retrieval_topics=("FINANCIAL_ENGINEERING",),
        ),
    )
    voyage_ticket = "rti_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
    _grant_external_consent_and_issue_voyage_ticket(
        isolated_postgres_cluster["app_dsn"],
        owner_user_id="usr_demo_admin",
        event_character="b",
        ticket_ids=(voyage_ticket,),
    )
    text = "safe admin Voyage document"
    plan = module.build_owner_voyage_import_plan(
        owner_user_id="usr_demo_admin",
        items=(_item(module, index=14, text=text, token_count=10, ticket_id=voyage_ticket),),
        token_counter=_TokenCounter({text: 10}),
    )
    lease = module.OwnerVoyageAttemptLease(
        repository=module.PsycopgOwnerVoyageRepository(
            database_dsn=isolated_postgres_cluster["rag_writer_dsn"],
        ),
        plan=plan,
        packet_sha256="4" * 64,
        approval_manifest_sha256="5" * 64,
        nonce_sha256="6" * 64,
    )

    with pytest.raises(module.OwnerVoyageImportError, match="OWNER_VOYAGE_ATTEMPT_REJECTED"):
        lease.claim_attempt(now=datetime(2026, 8, 13, tzinfo=UTC))
    with psycopg.connect(isolated_postgres_cluster["admin_dsn"]) as connection:
        assert connection.execute(
            """
            SELECT count(*)
            FROM rag_v2_owner_voyage_import_attempts
            WHERE owner_user_id = 'usr_demo_admin'
            """
        ).fetchone() == (0,)


def test_active_owner_overlay_delete_rebuild_counts_only_per_document_staging_generations(
    isolated_postgres_cluster: dict[str, str],
    tmp_path: Path,
) -> None:
    _seed_minimal_voyage_public_pointer(isolated_postgres_cluster["admin_dsn"])
    staging = PsycopgRagV2OwnerBgeStagingRepository(
        database_dsn=isolated_postgres_cluster["rag_writer_dsn"],
    )
    documents: list[str] = []
    for ordinal, identity in enumerate(("survivor_a", "survivor_b"), 1):
        ticket_id = f"rti_{ordinal + 20:032x}"
        _issue_profile_ticket(
            isolated_postgres_cluster["app_dsn"],
            owner_user_id="usr_demo_user",
            ticket_id=ticket_id,
            embedding_profile_id="bge_m3_local_1024_v1",
        )
        materialized = _materialized_bge_race(tmp_path, identity=identity)
        staging.stage(
            owner_user_id="usr_demo_user",
            import_ticket_id=ticket_id,
            materialized=materialized,
            metadata=OwnerBgeStagingMetadata(
                sanitized_display_name=f"Synthetic {identity}",
                retrieval_topics=("FINANCIAL_ENGINEERING",),
            ),
        )
        documents.append(materialized.document.document_id)

    overlay = PsycopgRagV2OwnerOverlayRepository(
        database_dsn=isolated_postgres_cluster["rag_admin_dsn"],
    ).prepare_and_activate(owner_user_id="usr_demo_user")
    assert overlay.source_count == 2
    assert overlay.chunk_count == 2

    first_delete_ticket = "rtd_21212121212121212121212121212121"
    _issue_delete_ticket(
        isolated_postgres_cluster["app_dsn"],
        owner_user_id="usr_demo_user",
        document_id=documents[0],
        ticket_id=first_delete_ticket,
    )
    deletion = PsycopgRagV2OwnerBgeDeletionRepository(
        database_dsn=isolated_postgres_cluster["rag_admin_dsn"],
    )
    assert (
        deletion.delete(
            owner_user_id="usr_demo_user",
            document_id=documents[0],
            delete_ticket_id=first_delete_ticket,
        ).state
        == "DELETED"
    )

    with psycopg.connect(isolated_postgres_cluster["admin_dsn"]) as connection:
        assert connection.execute(
            """
            SELECT generation.actual_source_count, generation.actual_chunk_count,
                   bundle.owner_embedding_profile_id,
                   (SELECT count(*) FROM rag_v2_immutable_source_revisions
                    WHERE owner_user_id = 'usr_demo_user' AND source_scope = 'OWNER_PRIVATE')
            FROM rag_v2_immutable_owner_bundle_pointers AS pointer
            JOIN rag_v2_immutable_bundles AS bundle
              ON bundle.bundle_id = pointer.active_bundle_id
            JOIN rag_v2_immutable_component_generations AS generation
              ON generation.component_generation_id = bundle.owner_private_generation_id
            WHERE pointer.owner_user_id = 'usr_demo_user'
            """
        ).fetchone() == (1, 1, "bge_m3_local_1024_v1", 1)

    second_delete_ticket = "rtd_22222222222222222222222222222222"
    _issue_delete_ticket(
        isolated_postgres_cluster["app_dsn"],
        owner_user_id="usr_demo_user",
        document_id=documents[1],
        ticket_id=second_delete_ticket,
    )
    assert (
        deletion.delete(
            owner_user_id="usr_demo_user",
            document_id=documents[1],
            delete_ticket_id=second_delete_ticket,
        ).state
        == "DELETED"
    )

    with psycopg.connect(isolated_postgres_cluster["admin_dsn"]) as connection:
        assert connection.execute(
            """
            SELECT bundle.owner_embedding_profile_id,
                   (SELECT count(*) FROM rag_v2_immutable_source_revisions
                    WHERE owner_user_id = 'usr_demo_user' AND source_scope = 'OWNER_PRIVATE'),
                   (SELECT count(*) FROM rag_v2_immutable_chunks
                    WHERE owner_user_id = 'usr_demo_user' AND source_scope = 'OWNER_PRIVATE'),
                   (SELECT count(*) FROM rag_v2_immutable_generation_embeddings
                    WHERE owner_user_id = 'usr_demo_user' AND component_scope = 'OWNER_PRIVATE')
            FROM rag_v2_immutable_owner_bundle_pointers AS pointer
            JOIN rag_v2_immutable_bundles AS bundle
              ON bundle.bundle_id = pointer.active_bundle_id
            WHERE pointer.owner_user_id = 'usr_demo_user'
            """
        ).fetchone() == (None, 0, 0, 0)


def _seed_minimal_voyage_public_pointer(database_dsn: str) -> None:
    """Owner overlay 회귀는 public raw/vector 없이 active Voyage pointer만 고정한다."""

    with psycopg.connect(database_dsn, autocommit=False) as connection:
        with connection.transaction():
            connection.execute(
                """
                INSERT INTO rag_v2_immutable_component_generations (
                  component_generation_id, owner_user_id, component_scope, embedding_profile_id,
                  state, evaluation_status, expected_source_count, expected_chunk_count,
                  actual_source_count, actual_chunk_count, generation_hash, manifest_hash,
                  evaluated_at, activated_at
                ) VALUES
                  ('rgr_f1111111111111111111111111111111', NULL, 'EXACT30',
                   'voyage_context_4_1024_v1', 'ACTIVE', 'PASSED', 30, 30, 30, 30,
                   repeat('1', 64), repeat('2', 64), clock_timestamp(), clock_timestamp()),
                  ('rgr_f2222222222222222222222222222222', NULL, 'OA112',
                   'voyage_context_4_1024_v1', 'ACTIVE', 'PASSED', 112, 7841, 112, 7841,
                   repeat('3', 64), repeat('4', 64), clock_timestamp(), clock_timestamp())
                """
            )
            connection.execute(
                """
                UPDATE rag_v2_immutable_public_bundle_pointers
                SET state = 'ACTIVE',
                    exact30_generation_id = 'rgr_f1111111111111111111111111111111',
                    oa112_generation_id = 'rgr_f2222222222222222222222222222222',
                    embedding_profile_id = 'voyage_context_4_1024_v1',
                    pointer_version = 2
                WHERE state_id = 'default'
                """
            )


def _issue_delete_ticket(
    database_dsn: str,
    *,
    owner_user_id: str,
    document_id: str,
    ticket_id: str,
) -> None:
    with psycopg.connect(database_dsn, autocommit=False) as connection:
        with connection.transaction():
            connection.execute(
                "SELECT set_config('app.actor_user_id', %s, true)",
                (owner_user_id,),
            )
            connection.execute(
                "SELECT issue_rag_v2_immutable_owner_delete_ticket(%s, %s, %s)",
                (owner_user_id, document_id, ticket_id),
            ).fetchone()


def _grant_external_consent_and_issue_voyage_ticket(
    database_dsn: str,
    *,
    owner_user_id: str,
    event_character: str,
    ticket_ids: tuple[str, ...],
) -> None:
    with psycopg.connect(database_dsn, autocommit=False) as connection:
        with connection.transaction():
            connection.execute(
                "SELECT set_config('app.actor_user_id', %s, true)",
                (owner_user_id,),
            )
            connection.execute(
                """
                SELECT record_rag_v2_immutable_consent_v2(
                  %s, %s, %s,
                  'GRANT',
                  %s, %s, %s
                )
                """,
                (
                    owner_user_id,
                    f"cns_v2_{event_character * 32}",
                    f"rce_owner_voyage_{event_character * 12}",
                    "1" * 64,
                    "2" * 64,
                    "3" * 64,
                ),
            ).fetchone()
            for ticket_id in ticket_ids:
                connection.execute(
                    """
                    SELECT issue_rag_v2_immutable_import_ticket_v2(
                      %s, %s, 'OWNER_IMPORT',
                      'RAG_V2_OWNER_DOCUMENT_V2', 'voyage_context_4_1024_v1'
                    )
                    """,
                    (owner_user_id, ticket_id),
                ).fetchone()


def _issue_profile_ticket(
    database_dsn: str,
    *,
    owner_user_id: str,
    ticket_id: str,
    embedding_profile_id: str,
) -> None:
    with psycopg.connect(database_dsn, autocommit=False) as connection:
        with connection.transaction():
            connection.execute(
                "SELECT set_config('app.actor_user_id', %s, true)",
                (owner_user_id,),
            )
            connection.execute(
                """
                SELECT issue_rag_v2_immutable_import_ticket_v2(
                  %s, %s, 'OWNER_IMPORT', 'RAG_V2_OWNER_DOCUMENT_V2', %s
                )
                """,
                (owner_user_id, ticket_id, embedding_profile_id),
            ).fetchone()


def _materialized_bge_race(root: Path, *, identity: str):
    relative_path = f"owner-bge-race-{identity}.txt"
    (root / relative_path).write_text(
        "Safe local owner BGE document for profile race validation.",
        encoding="utf-8",
    )
    return materialize_owner_bge_document(
        parser=LocalDocumentParser(),
        tokenizer=_WhitespaceTokenizer(),
        embedder=_UnitEmbedder(),
        request=RagV2OwnerDocumentRequest(
            approved_root=root,
            relative_path=relative_path,
            document_id=f"doc_owner_bge_race_{identity}_001",
            source_id=f"src_owner_bge_race_{identity}_001",
            source_revision_id=f"srv_owner_bge_race_{identity}_001",
            language_tags=("en",),
            embedding_profile_id="bge_m3_local_1024_v1",
        ),
    )


def _item(
    module: Any,
    *,
    index: int,
    text: str,
    token_count: int,
    ticket_id: str | None = None,
) -> object:
    text_sha = hashlib.sha256(text.encode()).hexdigest()
    chunk_id = f"rag_v2_chk_{index:032x}"
    group = VoyagePreChunkedDocumentGroup(
        source_id=f"src_owner_voyage_{index:03d}",
        source_revision_id=f"srv_owner_voyage_{index:03d}",
        context_set_hash=hashlib.sha256(f"context-{index}".encode()).hexdigest(),
        chunks=(
            VoyagePreChunkedChunk(
                chunk_id=chunk_id,
                canonical_text=text,
                canonical_text_sha256=text_sha,
                embedding_input_hash=hashlib.sha256(f"input-{index}".encode()).hexdigest(),
                token_count=token_count,
            ),
        ),
    )
    return module.OwnerVoyageImportItem(
        import_ticket_id=ticket_id or f"rti_{index:032x}",
        group=group,
        staging_payload={
            "documentId": f"doc_owner_voyage_{index:04d}",
            "embeddingProfileId": "voyage_context_4_1024_v1",
            "schemaVersion": 3,
        },
    )
