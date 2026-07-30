from __future__ import annotations

from textwrap import dedent

import pytest

from app.rag.contract_catalog import RagContractCatalogError
from app.rag.ingest_pipeline import (
    build_canonical_chunks,
    build_embedding_inputs,
    compute_context_set_hash,
    count_tokens,
    parse_markdown_document,
)


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
        min_tokens=6,
        max_tokens=12,
    )

    table_chunks = [chunk for chunk in chunks if chunk.contains_table]
    assert len(table_chunks) == 1
    assert "| row1 | value1 |" in table_chunks[0].text
    assert "| row3 | value3 |" in table_chunks[0].text
    assert table_chunks[0].content_hash
    assert table_chunks[0].chunk_revision_id.startswith("chkrev_src_rev_demo_")


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
        min_tokens=5,
        max_tokens=8,
    )

    bge = build_embedding_inputs(chunks, embedding_profile_id="bge_m3_local_1024_v1")
    voyage = build_embedding_inputs(chunks, embedding_profile_id="voyage_context_4_1024_v1")

    assert len(chunks) >= 3
    assert bge[1].context_set_hash is None
    assert chunks[1].text in bge[1].text
    assert bge[1].text != chunks[1].text
    assert voyage[1].text == chunks[1].text
    assert voyage[1].context_set_hash == compute_context_set_hash(chunks)
    assert bge[1].embedding_input_hash != voyage[1].embedding_input_hash
    assert chunks[1].content_hash == chunks[1].content_hash


def test_context_set_hash_changes_when_any_document_chunk_changes() -> None:
    original = build_canonical_chunks(
        source_id="src_kis_openapi_overview_001",
        source_revision_id="src_rev_demo",
        blocks=parse_markdown_document("# A\n\none two three four five.\n\nsix seven eight nine ten."),
        min_tokens=4,
        max_tokens=5,
    )
    changed = build_canonical_chunks(
        source_id="src_kis_openapi_overview_001",
        source_revision_id="src_rev_demo",
        blocks=parse_markdown_document("# A\n\none two three four five.\n\nsix seven eight nine changed."),
        min_tokens=4,
        max_tokens=5,
    )

    assert compute_context_set_hash(original) != compute_context_set_hash(changed)


def test_forbidden_or_unknown_embedding_profile_is_rejected() -> None:
    chunks = build_canonical_chunks(
        source_id="src_kis_openapi_overview_001",
        source_revision_id="src_rev_demo",
        blocks=parse_markdown_document("# A\n\none two three four five."),
        min_tokens=4,
        max_tokens=8,
    )

    with pytest.raises(RagContractCatalogError):
        build_embedding_inputs(chunks, embedding_profile_id="voyage_context_3_1024_v1")


def test_count_tokens_is_deterministic_for_mixed_korean_identifier_text() -> None:
    text = "삼성전자 005930 TR_ID FHKST01010100"

    assert count_tokens(text) == count_tokens(text)
    assert count_tokens(text) >= 4
