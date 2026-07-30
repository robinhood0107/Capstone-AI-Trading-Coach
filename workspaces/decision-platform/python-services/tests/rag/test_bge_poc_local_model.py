from __future__ import annotations

import os

import psycopg
import pytest

from app.rag.bge_acquisition import (
    DEFAULT_MODEL_MANIFEST,
    DEFAULT_MODEL_ROOT,
    verify_bge_completion_manifest,
)
from app.rag.bge_poc import (
    PsycopgBgePocRepository,
    execute_bge_poc,
    prepare_bge_poc,
)
from app.rag.bge_runtime import BgeStaticTokenizer, load_bge_onnx_embedder
from app.rag.source_card import OFFICIAL_SOURCE_CARD_ROOT, load_rag_source_cards

pytestmark = pytest.mark.skipif(
    os.environ.get("BGE_LOCAL_MODEL_TESTS") != "1",
    reason="exact ignored local BGE packet을 명시한 smoke에서만 실행한다.",
)

_CARD_PATHS = (
    "src_project_ecos_pit_availability_001.md",
    "src_project_gold_futures_etf_132030_001.md",
    "src_project_kis_adjusted_price_001.md",
    "src_project_krx_service_coverage_001.md",
    "src_project_opendart_status_quota_001.md",
)


def test_exact_local_model_materializes_official_five_card_poc(
    postgres_cluster: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실물 model/card는 provider 없이 CPU ONNX→COPY→EVAL_PASSED까지 검증한다."""

    artifact = verify_bge_completion_manifest(
        DEFAULT_MODEL_ROOT,
        manifest_path=DEFAULT_MODEL_MANIFEST,
    )
    cards = load_rag_source_cards(
        approved_root=OFFICIAL_SOURCE_CARD_ROOT,
        relative_paths=_CARD_PATHS,
    )
    tokenizer = BgeStaticTokenizer.from_file(
        DEFAULT_MODEL_ROOT / "onnx/tokenizer.json"
    )
    plan = prepare_bge_poc(cards=cards, tokenizer=tokenizer, artifact=artifact)
    assert [item.chunk.token_count for item in plan.items] == [250, 264, 239, 197, 207]

    with psycopg.connect(postgres_cluster["admin_dsn"]) as admin:
        pointer_before = admin.execute(
            """
            SELECT policy_id, effective_profile_id, active_generation_id, version
            FROM rag_embedding_policy_state
            WHERE state_id = 'default'
            """
        ).fetchone()

    monkeypatch.setenv("RAG_SOURCE_REGISTER_TARGET", "testcontainers")
    receipt = execute_bge_poc(
        plan=plan,
        embedder=load_bge_onnx_embedder(DEFAULT_MODEL_ROOT),
        repository=PsycopgBgePocRepository(
            database_dsn=postgres_cluster["rag_writer_dsn"],
        ),
    )

    assert receipt.status == "EVAL_PASSED"
    assert receipt.final_row_count == 5
    assert receipt.active_pointer_changed is False
    with psycopg.connect(postgres_cluster["admin_dsn"]) as admin:
        assert admin.execute(
            """
            SELECT count(*), min(vector_dims(embedding)), max(vector_dims(embedding)),
                   bool_and(abs(vector_norm(embedding)::double precision - 1.0) <= 0.00001)
            FROM rag_chunk_embeddings
            WHERE corpus_generation_id = %s
            """,
            (plan.generation_id,),
        ).fetchone() == (5, 1024, 1024, True)
        assert admin.execute(
            """
            SELECT status, actual_chunk_count, evaluation_status,
                   evaluated_at IS NOT NULL, activated_at IS NULL
            FROM rag_corpus_generations
            WHERE corpus_generation_id = %s
            """,
            (plan.generation_id,),
        ).fetchone() == ("EVAL_PASSED", 5, "PASSED", True, True)
        pointer_after = admin.execute(
            """
            SELECT policy_id, effective_profile_id, active_generation_id, version
            FROM rag_embedding_policy_state
            WHERE state_id = 'default'
            """
        ).fetchone()
    assert pointer_after == pointer_before
