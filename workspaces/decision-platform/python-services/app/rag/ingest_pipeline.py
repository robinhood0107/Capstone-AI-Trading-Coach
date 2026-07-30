from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Iterable, Literal

from app.rag.contract_catalog import PROFILE_IDS, RagContractCatalogError

BlockKind = Literal["paragraph", "table"]
_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+|[가-힣]+|[^\s]")


class RagIngestError(ValueError):
    """RAG parse/chunk/embedding-input 계약 위반을 내부 파일 경로 없이 보고한다."""


@dataclass(frozen=True)
class RagParsedBlock:
    """heading path가 부착된 parser block.

    table block은 chunker가 절대 row 단위로 쪼개지 않는 단위이며, heading block 자체는 citation
    본문이 아니라 metadata로만 보존한다.
    """

    kind: BlockKind
    heading_path: tuple[str, ...]
    text: str
    ordinal: int

    @property
    def token_count(self) -> int:
        return count_tokens(self.text)


@dataclass(frozen=True)
class RagCanonicalChunk:
    """profile과 무관한 citation/content hash 기준 chunk."""

    source_id: str
    source_revision_id: str
    chunk_revision_id: str
    sequence: int
    heading_path: tuple[str, ...]
    text: str
    content_hash: str
    token_count: int
    contains_table: bool


@dataclass(frozen=True)
class RagEmbeddingInput:
    """embedding adapter에 전달할 profile-specific 입력.

    BGE는 인접 canonical context를 임시로 붙이고, Voyage는 document-level contextSetHash로
    vector space 오염 없이 canonical text 묶음을 provider에 전달한다.
    """

    chunk_revision_id: str
    embedding_profile_id: str
    text: str
    embedding_input_hash: str
    context_set_hash: str | None


def parse_markdown_document(text: str) -> tuple[RagParsedBlock, ...]:
    """Markdown heading과 pipe table을 deterministic block sequence로 파싱한다.

    v1 parser는 HTML/remote include/명령을 해석하지 않고, ingest approved-root에서 읽은
    untrusted text를 순수 문자열 블록으로만 다룬다.
    """

    normalized = _normalize_text(text)
    if not normalized:
        raise RagIngestError("RAG document is empty after normalization.")
    blocks: list[RagParsedBlock] = []
    heading_stack: list[str] = []
    paragraph: list[str] = []
    table: list[str] = []

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            blocks.append(_make_block("paragraph", heading_stack, "\n".join(paragraph), len(blocks)))
            paragraph = []

    def flush_table() -> None:
        nonlocal table
        if table:
            blocks.append(_make_block("table", heading_stack, "\n".join(table), len(blocks)))
            table = []

    for raw_line in normalized.splitlines():
        line = raw_line.rstrip()
        heading = _HEADING_PATTERN.match(line)
        if heading is not None:
            flush_table()
            flush_paragraph()
            level = len(heading.group(1))
            title = heading.group(2).strip()
            heading_stack = heading_stack[: level - 1]
            heading_stack.append(title)
            continue
        if not line.strip():
            flush_table()
            flush_paragraph()
            continue
        if _is_table_line(line):
            flush_paragraph()
            table.append(line)
            continue
        flush_table()
        paragraph.append(line)

    flush_table()
    flush_paragraph()
    if not blocks:
        raise RagIngestError("RAG document has no indexable blocks.")
    return tuple(blocks)


def build_canonical_chunks(
    *,
    source_id: str,
    source_revision_id: str,
    blocks: Iterable[RagParsedBlock],
    min_tokens: int = 400,
    max_tokens: int = 600,
) -> tuple[RagCanonicalChunk, ...]:
    """heading-aware canonical chunks를 만들며 table block은 절대 분할하지 않는다."""

    if min_tokens <= 0 or max_tokens < min_tokens:
        raise RagIngestError("RAG chunk token bounds are invalid.")
    parsed_blocks = tuple(blocks)
    if not parsed_blocks:
        raise RagIngestError("RAG chunker requires at least one parsed block.")

    chunks: list[RagCanonicalChunk] = []
    buffer: list[RagParsedBlock] = []
    buffer_tokens = 0

    def flush() -> None:
        nonlocal buffer, buffer_tokens
        if not buffer:
            return
        chunks.append(_make_chunk(source_id, source_revision_id, len(chunks), tuple(buffer)))
        buffer = []
        buffer_tokens = 0

    for block in _split_large_paragraph_blocks(parsed_blocks, max_tokens=max_tokens):
        block_tokens = block.token_count
        if buffer and buffer_tokens >= min_tokens and buffer_tokens + block_tokens > max_tokens:
            flush()
        buffer.append(block)
        buffer_tokens += block_tokens
        if buffer_tokens >= max_tokens:
            flush()
    flush()
    return tuple(chunks)


def build_embedding_inputs(
    chunks: Iterable[RagCanonicalChunk],
    *,
    embedding_profile_id: str,
    bge_overlap_ratio: float = 0.15,
) -> tuple[RagEmbeddingInput, ...]:
    """profile별 embedding input hash를 생성하되 canonical chunk hash는 바꾸지 않는다."""

    ordered = tuple(chunks)
    if not ordered:
        raise RagIngestError("RAG embedding input requires at least one chunk.")
    if embedding_profile_id not in PROFILE_IDS:
        raise RagContractCatalogError("RAG embedding profile is not in the approved catalog.")
    if not 0 <= bge_overlap_ratio <= 0.5:
        raise RagIngestError("BGE overlap ratio must be between 0 and 0.5.")
    if embedding_profile_id == "voyage_context_4_1024_v1":
        context_hash = compute_context_set_hash(ordered)
        return tuple(
            RagEmbeddingInput(
                chunk_revision_id=chunk.chunk_revision_id,
                embedding_profile_id=embedding_profile_id,
                text=chunk.text,
                embedding_input_hash=_sha256_text(chunk.text),
                context_set_hash=context_hash,
            )
            for chunk in ordered
        )
    return tuple(
        RagEmbeddingInput(
            chunk_revision_id=chunk.chunk_revision_id,
            embedding_profile_id=embedding_profile_id,
            text=_bge_text_with_adjacent_context(ordered, index, overlap_ratio=bge_overlap_ratio),
            embedding_input_hash=_sha256_text(
                _bge_text_with_adjacent_context(ordered, index, overlap_ratio=bge_overlap_ratio)
            ),
            context_set_hash=None,
        )
        for index, chunk in enumerate(ordered)
    )


def compute_context_set_hash(chunks: Iterable[RagCanonicalChunk]) -> str:
    """Voyage contextual embedding invalidation을 위한 document-order hash를 계산한다."""

    ordered = tuple(chunks)
    if not ordered:
        raise RagIngestError("context set hash requires chunks.")
    digest = hashlib.sha256()
    source_revision_ids = {chunk.source_revision_id for chunk in ordered}
    if len(source_revision_ids) != 1:
        raise RagIngestError("context set hash cannot mix source revisions.")
    for chunk in ordered:
        digest.update(chunk.source_revision_id.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(chunk.sequence).encode("ascii"))
        digest.update(b"\0")
        digest.update(chunk.content_hash.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def count_tokens(text: str) -> int:
    """S4.2 offline chunk planning용 deterministic tokenizer count.

    실제 BGE/Voyage tokenizer accounting은 adapter 단계에서 별도 golden fixture로 검증한다.
    """

    return len(_TOKEN_PATTERN.findall(text))


def _normalize_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    normalized = "\n".join(line.rstrip() for line in normalized.splitlines())
    return normalized


def _is_table_line(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2


def _make_block(
    kind: BlockKind,
    heading_stack: list[str],
    text: str,
    ordinal: int,
) -> RagParsedBlock:
    cleaned = text.strip()
    if not cleaned:
        raise RagIngestError("RAG parser produced an empty block.")
    return RagParsedBlock(
        kind=kind,
        heading_path=tuple(heading_stack),
        text=cleaned,
        ordinal=ordinal,
    )


def _split_large_paragraph_blocks(
    blocks: Iterable[RagParsedBlock],
    *,
    max_tokens: int,
) -> tuple[RagParsedBlock, ...]:
    result: list[RagParsedBlock] = []
    for block in blocks:
        if block.kind == "table" or block.token_count <= max_tokens:
            result.append(block)
            continue
        tokens = _TOKEN_PATTERN.findall(block.text)
        for start in range(0, len(tokens), max_tokens):
            result.append(
                RagParsedBlock(
                    kind="paragraph",
                    heading_path=block.heading_path,
                    text=" ".join(tokens[start : start + max_tokens]),
                    ordinal=block.ordinal,
                )
            )
    return tuple(result)


def _make_chunk(
    source_id: str,
    source_revision_id: str,
    sequence: int,
    blocks: tuple[RagParsedBlock, ...],
) -> RagCanonicalChunk:
    text = "\n\n".join(block.text for block in blocks).strip()
    content_hash = _sha256_text(text)
    chunk_revision_id = f"chkrev_{source_revision_id}_{sequence + 1:05d}_{content_hash[:16]}"
    heading_path = blocks[-1].heading_path if blocks else ()
    return RagCanonicalChunk(
        source_id=source_id,
        source_revision_id=source_revision_id,
        chunk_revision_id=chunk_revision_id,
        sequence=sequence + 1,
        heading_path=heading_path,
        text=text,
        content_hash=content_hash,
        token_count=count_tokens(text),
        contains_table=any(block.kind == "table" for block in blocks),
    )


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _bge_text_with_adjacent_context(
    chunks: tuple[RagCanonicalChunk, ...],
    index: int,
    *,
    overlap_ratio: float,
) -> str:
    chunk = chunks[index]
    side_tokens = max(1, int(chunk.token_count * overlap_ratio)) if chunk.token_count else 0
    parts: list[str] = []
    if index > 0 and side_tokens:
        parts.append(" ".join(_TOKEN_PATTERN.findall(chunks[index - 1].text)[-side_tokens:]))
    parts.append(chunk.text)
    if index + 1 < len(chunks) and side_tokens:
        parts.append(" ".join(_TOKEN_PATTERN.findall(chunks[index + 1].text)[:side_tokens]))
    return "\n\n".join(part for part in parts if part)
