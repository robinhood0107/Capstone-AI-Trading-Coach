from __future__ import annotations

import hashlib
import re
from dataclasses import replace
from datetime import UTC, datetime
from types import MappingProxyType

import numpy as np
import pytest

from app.rag.bge_artifact import BgeVerifiedPacket
from app.rag.bge_poc import (
    BgePocDatabaseReceipt,
    BgePocError,
    BgePocPlan,
    BgeStagedEmbedding,
    execute_bge_poc,
    prepare_bge_poc,
)
from app.rag.source_card import RagSourceCard

APPROVED_SOURCE_IDENTITIES = (
    ("src_project_ecos_pit_availability_001", "card_ecos_pit_availability_001", "ecos"),
    (
        "src_project_gold_futures_etf_132030_001",
        "card_gold_futures_etf_132030_001",
        "samsungfund",
    ),
    ("src_project_kis_adjusted_price_001", "card_kis_adjusted_price_001", "kis"),
    ("src_project_krx_service_coverage_001", "card_krx_service_coverage_001", "krx"),
    (
        "src_project_opendart_status_quota_001",
        "card_opendart_status_quota_001",
        "opendart",
    ),
)


def test_five_card_plan_is_deterministic_one_card_one_chunk_and_db_compatible() -> None:
    cards = _approved_cards()
    artifact = _artifact_receipt()

    first = prepare_bge_poc(cards=cards, tokenizer=_FixtureTokenizer(), artifact=artifact)
    second = prepare_bge_poc(
        cards=tuple(reversed(cards)),
        tokenizer=_FixtureTokenizer(),
        artifact=artifact,
    )

    assert first == second
    assert re.fullmatch(r"rag_gen_[0-9a-f]{32}", first.generation_id)
    assert re.fullmatch(r"rag_mat_[0-9a-f]{32}", first.materialization_run_id)
    assert len(first.items) == 5
    assert all(re.fullmatch(r"src_rev_[0-9a-f]{32}", item.source_revision_id) for item in first.items)
    assert all(re.fullmatch(r"rag_ing_[0-9a-f]{32}", item.ingest_run_id) for item in first.items)
    assert all(re.fullmatch(r"rag_chk_[0-9a-f]{32}", item.chunk.chunk_revision_id) for item in first.items)
    assert all(item.chunk.sequence == 1 for item in first.items)
    assert all(item.embedding_input.text == item.chunk.text for item in first.items)
    assert all(item.embedding_input.context_set_hash is None for item in first.items)
    assert all("---" not in item.chunk.text for item in first.items)
    assert first.artifact_manifest_sha256 == artifact.file_manifest_sha256


def test_poc_execution_stages_normalized_vectors_and_requires_eval_passed_without_pointer_change() -> None:
    plan = prepare_bge_poc(
        cards=_approved_cards(),
        tokenizer=_FixtureTokenizer(),
        artifact=_artifact_receipt(),
    )
    repository = _RecordingRepository()

    receipt = execute_bge_poc(
        plan=plan,
        embedder=_FixtureEmbedder(),
        repository=repository,
    )

    assert receipt.status == "EVAL_PASSED"
    assert receipt.active_pointer_changed is False
    assert receipt.final_row_count == 5
    assert repository.rows is not None
    assert len(repository.rows) == 5
    assert all(row.embedding.dtype == np.float32 for row in repository.rows)
    assert all(row.embedding.shape == (1024,) for row in repository.rows)
    assert all(np.linalg.norm(row.embedding) == pytest.approx(1.0) for row in repository.rows)
    expected_hash = hashlib.sha256(
        "".join(
            row.staging_row_hash
            for row in sorted(repository.rows, key=lambda row: row.chunk_revision_id.encode("utf-8"))
        ).encode("ascii")
    ).hexdigest()
    assert repository.staging_hash == expected_hash


def test_poc_plan_rejects_any_card_membership_drift() -> None:
    cards = list(_approved_cards())
    cards[0] = replace(cards[0], source_id="src_project_unapproved_001")

    with pytest.raises(BgePocError, match="FIVE_CARD_MEMBERSHIP"):
        prepare_bge_poc(
            cards=tuple(cards),
            tokenizer=_FixtureTokenizer(),
            artifact=_artifact_receipt(),
        )


class _FixtureTokenizer:
    def token_spans(self, text: str) -> tuple[tuple[int, int], ...]:
        return tuple((match.start(), match.end()) for match in re.finditer(r"\S+", text))


class _FixtureEmbedder:
    def embed(self, texts: tuple[str, ...]) -> np.ndarray:
        embedding = np.zeros((len(texts), 1024), dtype=np.float32)
        for index in range(len(texts)):
            embedding[index, index] = 1.0
        return embedding


class _RecordingRepository:
    def __init__(self) -> None:
        self.rows: tuple[BgeStagedEmbedding, ...] | None = None
        self.staging_hash: str | None = None

    def materialize(
        self,
        *,
        plan: BgePocPlan,
        rows: tuple[BgeStagedEmbedding, ...],
        staging_hash: str,
    ) -> BgePocDatabaseReceipt:
        self.rows = rows
        self.staging_hash = staging_hash
        return BgePocDatabaseReceipt(
            generation_id=plan.generation_id,
            materialization_run_id=plan.materialization_run_id,
            final_row_count=len(rows),
            status="EVAL_PASSED",
            active_pointer_changed=False,
        )


def _approved_cards() -> tuple[RagSourceCard, ...]:
    result = []
    for index, (source_id, card_id, institution) in enumerate(APPROVED_SOURCE_IDENTITIES, start=1):
        title = f"공식 카드 {index}"
        claim = f"공식 공개 근거에 기반한 독립 claim {index}"
        body = (
            f"# Source Card: {title}\n"
            "## 핵심 claim\n"
            f"{claim}\n"
            "## 적용 범위와 전제\n"
            "공개 fixture 범위에만 적용한다.\n"
            "## 프로젝트 적용\n"
            "설명형 retrieval에만 사용한다.\n"
            "## 한계와 반례\n"
            "실시간 값과 투자 판단을 보장하지 않는다.\n"
            "## 허용 사용\n"
            "offline 평가에 사용한다.\n"
            "## 금지 추론\n"
            "주문이나 수익 보장으로 확대하지 않는다.\n"
            "## 근거 위치\n"
            "공식 locator와 bounded hash를 사용한다.\n"
        )
        result.append(
            RagSourceCard(
                source_id=source_id,
                card_id=card_id,
                title=title,
                institution=institution,
                topic=source_id.removeprefix("src_project_").removesuffix("_001"),
                claim=claim,
                status="VERIFIED",
                verified_at=datetime(2026, 7, 30, tzinfo=UTC),
                canonical_url=f"https://example.com/official/{index}",
                canonical_url_sha256=hashlib.sha256(
                    f"https://example.com/official/{index}".encode()
                ).hexdigest(),
                evidence_content_sha256=f"{index:x}" * 64,
                upstream_source_ids=(f"src_{institution}_reference_{index:03d}",),
                contradicts=(),
                representative_questions=(f"공식 카드 {index}의 경계는 무엇인가요?",),
                relative_path=f"{source_id}.md",
                content_sha256=hashlib.sha256(body.encode()).hexdigest(),
                canonical_body=body,
                license_note="원문을 복제하지 않는 project-authored card다.",
                attribution=f"{institution} official",
                retention_owner="python-rag-corpus-privacy",
                retention_days=365,
                external_processing_allowed=False,
                sections=MappingProxyType({"핵심 claim": claim}),
            )
        )
    return tuple(result)


def _artifact_receipt() -> BgeVerifiedPacket:
    return BgeVerifiedPacket(
        revision="5617a9f61b028005a4858fdac845db406aefb181",
        file_count=10,
        total_bytes=2_289_781_803,
        file_manifest_sha256="a" * 64,
    )
