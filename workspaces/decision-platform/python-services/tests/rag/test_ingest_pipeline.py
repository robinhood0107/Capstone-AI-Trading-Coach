from __future__ import annotations

import hashlib
import re
from textwrap import dedent

import pytest

from app.rag.contract_catalog import RagContractCatalogError
from app.rag.ingest_pipeline import (
    RagCanonicalChunk,
    RagIngestError,
    build_canonical_chunks,
    build_embedding_inputs,
    compute_context_set_hash,
    count_tokens,
    parse_markdown_document,
)


class _FixtureTokenizer:
    """모델 payload 없이 parser/chunker 계약을 검증하는 offset 기반 tokenizer port다."""

    def token_spans(self, text: str) -> tuple[tuple[int, int], ...]:
        return tuple((match.start(), match.end()) for match in re.finditer(r"\S+", text))


TOKENIZER = _FixtureTokenizer()


def test_parse_markdown_document_preserves_heading_metadata_and_table_block() -> None:
    blocks = parse_markdown_document(
        dedent(
            """
        # KIS

        API 개요 문단입니다.

        ## TR ID
        | tr_id | 설명 |
        | --- | --- |
        | FHKST01010100 | 현재가 |

        다음 문단입니다.
        """
        )
    )

    assert [block.kind for block in blocks] == ["paragraph", "table", "paragraph"]
    assert blocks[0].heading_path == ("KIS",)
    assert blocks[1].heading_path == ("KIS", "TR ID")
    assert "FHKST01010100" in blocks[1].text


def test_build_canonical_chunks_never_splits_table_blocks() -> None:
    blocks = parse_markdown_document(
        dedent(
            """
        # 공식자료

        짧은 설명 alpha beta gamma.

        | col1 | col2 |
        | --- | --- |
        | row1 | value1 |
        | row2 | value2 |
        | row3 | value3 |

        tail one two three four five six.
        """
        )
    )

    chunks = build_canonical_chunks(
        source_id="src_kis_openapi_overview_001",
        source_revision_id="src_rev_demo",
        blocks=blocks,
        tokenizer=TOKENIZER,
        min_tokens=6,
        max_tokens=30,
    )

    table_chunks = [chunk for chunk in chunks if chunk.contains_table]
    assert len(table_chunks) == 1
    assert "| row1 | value1 |" in table_chunks[0].text
    assert "| row3 | value3 |" in table_chunks[0].text
    assert table_chunks[0].content_hash
    assert re.fullmatch(r"rag_chk_[0-9a-f]{32}", table_chunks[0].chunk_revision_id)


def test_embedding_inputs_keep_canonical_hashes_profile_specific() -> None:
    blocks = parse_markdown_document(
        dedent(
            """
        # A
        one two three four five six.

        ## B
        seven eight nine ten eleven twelve.

        ## C
        thirteen fourteen fifteen sixteen seventeen eighteen.
        """
        )
    )
    chunks = build_canonical_chunks(
        source_id="src_kis_openapi_overview_001",
        source_revision_id="src_rev_demo",
        blocks=blocks,
        tokenizer=TOKENIZER,
        min_tokens=5,
        max_tokens=8,
    )

    bge = build_embedding_inputs(
        chunks,
        embedding_profile_id="bge_m3_local_1024_v1",
        tokenizer=TOKENIZER,
    )
    voyage = build_embedding_inputs(chunks, embedding_profile_id="voyage_context_4_1024_v1")

    assert len(chunks) >= 3
    assert bge[1].context_set_hash is None
    assert chunks[1].text in bge[1].text
    # 짧은 청크의 floor(6 * 7.5%)는 0이며 missing budget을 최소 1로 올리지 않는다.
    assert bge[1].text == chunks[1].text
    assert voyage[1].text == chunks[1].text
    assert voyage[1].context_set_hash == compute_context_set_hash(chunks)
    assert bge[1].embedding_input_hash == voyage[1].embedding_input_hash
    assert bge[1].context_set_hash != voyage[1].context_set_hash
    assert chunks[1].content_hash == chunks[1].content_hash


def test_context_set_hash_changes_when_any_document_chunk_changes() -> None:
    original = build_canonical_chunks(
        source_id="src_kis_openapi_overview_001",
        source_revision_id="src_rev_demo",
        blocks=parse_markdown_document(
            "# A\n\none two three four five.\n\nsix seven eight nine ten."
        ),
        tokenizer=TOKENIZER,
        min_tokens=4,
        max_tokens=5,
    )
    changed = build_canonical_chunks(
        source_id="src_kis_openapi_overview_001",
        source_revision_id="src_rev_demo",
        blocks=parse_markdown_document(
            "# A\n\none two three four five.\n\nsix seven eight nine changed."
        ),
        tokenizer=TOKENIZER,
        min_tokens=4,
        max_tokens=5,
    )

    assert compute_context_set_hash(original) != compute_context_set_hash(changed)


def test_forbidden_or_unknown_embedding_profile_is_rejected() -> None:
    chunks = build_canonical_chunks(
        source_id="src_kis_openapi_overview_001",
        source_revision_id="src_rev_demo",
        blocks=parse_markdown_document("# A\n\none two three four five."),
        tokenizer=TOKENIZER,
        min_tokens=4,
        max_tokens=8,
    )

    with pytest.raises(RagContractCatalogError):
        build_embedding_inputs(
            chunks,
            embedding_profile_id="voyage_context_3_1024_v1",
            tokenizer=TOKENIZER,
        )


def test_count_tokens_uses_the_injected_static_tokenizer_port() -> None:
    text = "삼성전자 005930 TR_ID FHKST01010100"

    assert count_tokens(text, tokenizer=TOKENIZER) == 4


def test_monotonic_overlapping_subword_offsets_count_without_rebuilding_text() -> None:
    class _OverlappingSubwordTokenizer:
        def token_spans(self, text: str) -> tuple[tuple[int, int], ...]:
            assert text == "KIS X"
            return ((0, 1), (0, 3), (4, 5))

    assert count_tokens("KIS X", tokenizer=_OverlappingSubwordTokenizer()) == 3


def test_canonical_chunks_never_merge_across_heading_boundaries() -> None:
    chunks = build_canonical_chunks(
        source_id="src_project_heading_boundary_001",
        source_revision_id="src_rev_heading_boundary",
        blocks=parse_markdown_document("# 첫 절\n\none two three\n\n# 둘째 절\n\nfour five six"),
        tokenizer=TOKENIZER,
        min_tokens=5,
        max_tokens=10,
    )

    assert [chunk.heading_path for chunk in chunks] == [("첫 절",), ("둘째 절",)]
    assert [chunk.text for chunk in chunks] == ["one two three", "four five six"]


def test_oversized_atomic_table_and_source_card_fail_closed() -> None:
    oversized = " ".join(f"token{index}" for index in range(601))
    table_blocks = parse_markdown_document(
        f"# 표\n\n표 설명 문단\n\n| 항목 |\n| --- |\n| {oversized} |"
    )

    with pytest.raises(RagIngestError, match="OVERSIZED_ATOMIC_BLOCK"):
        build_canonical_chunks(
            source_id="src_project_oversized_table_001",
            source_revision_id="src_rev_oversized_table",
            blocks=table_blocks,
            tokenizer=TOKENIZER,
            min_tokens=400,
            max_tokens=600,
        )

    with pytest.raises(RagIngestError, match="OVERSIZED_ATOMIC_BLOCK"):
        build_canonical_chunks(
            source_id="src_project_oversized_card_001",
            source_revision_id="src_rev_oversized_card",
            blocks=parse_markdown_document(f"# 카드\n\n{oversized}"),
            tokenizer=TOKENIZER,
            min_tokens=400,
            max_tokens=600,
            atomic_document=True,
        )


def test_large_paragraph_splitting_preserves_original_spacing_and_punctuation() -> None:
    original = "alpha  beta,\tgamma!  delta?"
    chunks = build_canonical_chunks(
        source_id="src_project_whitespace_001",
        source_revision_id="src_rev_whitespace",
        blocks=parse_markdown_document(f"# 문단\n\n{original}"),
        tokenizer=TOKENIZER,
        min_tokens=1,
        max_tokens=2,
    )

    assert len(chunks) == 2
    assert all(chunk.text in original for chunk in chunks)
    assert chunks[0].text == "alpha  beta,"
    assert chunks[1].text == "gamma!  delta?"


def test_large_paragraph_retokenizes_each_context_sensitive_chunk_boundary() -> None:
    class _BoundarySensitiveTokenizer:
        """문서 중간 token이 chunk 선두가 되면 subword 수가 달라지는 tokenizer fixture다."""

        def token_spans(self, text: str) -> tuple[tuple[int, int], ...]:
            spans: list[tuple[int, int]] = []
            for index, match in enumerate(re.finditer(r"\S+", text)):
                if index == 0 and not text.startswith("root") and len(match.group()) > 1:
                    midpoint = match.start() + 1
                    spans.extend(((match.start(), midpoint), (midpoint, match.end())))
                else:
                    spans.append((match.start(), match.end()))
            return tuple(spans)

    original = "root one boundary two three"
    tokenizer = _BoundarySensitiveTokenizer()

    chunks = build_canonical_chunks(
        source_id="src_project_boundary_context_001",
        source_revision_id="src_rev_boundary_context",
        blocks=parse_markdown_document(f"# 문단\n\n{original}"),
        tokenizer=tokenizer,
        min_tokens=1,
        max_tokens=2,
    )

    assert " ".join(chunk.text for chunk in chunks) == original
    assert all(chunk.token_count <= 2 for chunk in chunks)


def test_bge_context_uses_independent_seven_point_five_percent_side_budgets() -> None:
    chunks = tuple(_chunk(sequence, prefix) for sequence, prefix in ((1, "p"), (2, "c"), (3, "n")))

    inputs = build_embedding_inputs(
        chunks,
        embedding_profile_id="bge_m3_local_1024_v1",
        tokenizer=TOKENIZER,
    )

    assert inputs[1].text.startswith("p38 p39 p40\n\nc01")
    assert inputs[1].text.endswith("c40\n\nn01 n02 n03")
    assert "p37" not in inputs[1].text
    assert "n04" not in inputs[1].text
    assert inputs[0].text.startswith("p01")
    assert inputs[0].text.endswith("p40\n\nc01 c02 c03")
    assert "c04" not in inputs[0].text


def test_neighbor_change_keeps_canonical_hash_but_changes_embedding_input_hash() -> None:
    original = tuple(
        _chunk(sequence, prefix) for sequence, prefix in ((1, "p"), (2, "c"), (3, "n"))
    )
    changed_neighbor = (
        _chunk(1, "x"),
        original[1],
        original[2],
    )

    original_input = build_embedding_inputs(
        original,
        embedding_profile_id="bge_m3_local_1024_v1",
        tokenizer=TOKENIZER,
    )[1]
    changed_input = build_embedding_inputs(
        changed_neighbor,
        embedding_profile_id="bge_m3_local_1024_v1",
        tokenizer=TOKENIZER,
    )[1]

    assert original[1].content_hash == changed_neighbor[1].content_hash
    assert original_input.embedding_input_hash != changed_input.embedding_input_hash


def _chunk(sequence: int, prefix: str) -> RagCanonicalChunk:
    text = " ".join(f"{prefix}{index:02d}" for index in range(1, 41))
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return RagCanonicalChunk(
        source_id="src_project_context_budget_001",
        source_revision_id="src_rev_context_budget",
        chunk_revision_id=f"rag_chk_{sequence:032x}",
        sequence=sequence,
        heading_path=(f"section-{sequence}",),
        text=text,
        content_hash=digest,
        token_count=40,
        contains_table=False,
    )
