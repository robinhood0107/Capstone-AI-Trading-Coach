from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal, Protocol

from app.rag.contract_catalog import PROFILE_IDS, RagContractCatalogError

BlockKind = Literal["paragraph", "table"]
_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_BGE_CONTEXT_RATIO_PER_SIDE = 0.075


class RagIngestError(ValueError):
    """RAG parse/chunk/embedding-input 계약 위반을 내부 파일 경로 없이 보고한다."""


class RagTokenizer(Protocol):
    """canonical chunk와 transient context가 공유하는 static tokenizer port."""

    def token_spans(self, text: str) -> tuple[tuple[int, int], ...]:
        """special token을 제외한 token별 원문 character span을 순서대로 반환한다."""


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
            blocks.append(
                _make_block("paragraph", heading_stack, "\n".join(paragraph), len(blocks))
            )
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
    tokenizer: RagTokenizer,
    min_tokens: int = 400,
    max_tokens: int = 600,
    atomic_document: bool = False,
) -> tuple[RagCanonicalChunk, ...]:
    """heading-aware canonical chunks를 만들며 원문 span과 atomic block을 보존한다.

    일반 문서는 heading 경계를 넘겨 합치지 않는다. source card처럼 호출자가 명시한
    `atomic_document`는 문서 전체를 정확히 한 chunk로 만들되 600-token 상한을 넘으면
    분할하지 않고 즉시 거부한다.
    """

    if min_tokens <= 0 or max_tokens < min_tokens:
        raise RagIngestError("RAG chunk token bounds are invalid.")
    parsed_blocks = tuple(blocks)
    if not parsed_blocks:
        raise RagIngestError("RAG chunker requires at least one parsed block.")

    units = _build_atomic_units(
        parsed_blocks,
        tokenizer=tokenizer,
        max_tokens=max_tokens,
        atomic_document=atomic_document,
    )
    chunks: list[RagCanonicalChunk] = []
    buffer: list[RagParsedBlock] = []
    buffer_tokens = 0

    def flush() -> None:
        nonlocal buffer, buffer_tokens
        if not buffer:
            return
        chunks.append(
            _make_chunk(
                source_id,
                source_revision_id,
                len(chunks),
                tuple(buffer),
                tokenizer=tokenizer,
            )
        )
        buffer = []
        buffer_tokens = 0

    for unit in units:
        unit_text = _join_blocks(unit)
        unit_tokens = count_tokens(unit_text, tokenizer=tokenizer)
        if unit_tokens > max_tokens:
            raise RagIngestError("OVERSIZED_ATOMIC_BLOCK")
        if buffer and not atomic_document and buffer[-1].heading_path != unit[0].heading_path:
            flush()
        if buffer and buffer_tokens + unit_tokens > max_tokens:
            flush()
        buffer.extend(unit)
        buffer_tokens += unit_tokens
        if buffer_tokens >= max_tokens:
            flush()
    flush()
    return tuple(chunks)


def build_embedding_inputs(
    chunks: Iterable[RagCanonicalChunk],
    *,
    embedding_profile_id: str,
    tokenizer: RagTokenizer | None = None,
) -> tuple[RagEmbeddingInput, ...]:
    """profile별 embedding input hash를 생성하되 canonical chunk hash는 바꾸지 않는다."""

    ordered = tuple(chunks)
    if not ordered:
        raise RagIngestError("RAG embedding input requires at least one chunk.")
    if embedding_profile_id not in PROFILE_IDS:
        raise RagContractCatalogError("RAG embedding profile is not in the approved catalog.")
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
    if tokenizer is None:
        raise RagIngestError("BGE embedding input requires the pinned static tokenizer.")
    return tuple(
        RagEmbeddingInput(
            chunk_revision_id=chunk.chunk_revision_id,
            embedding_profile_id=embedding_profile_id,
            text=_bge_text_with_adjacent_context(ordered, index, tokenizer=tokenizer),
            embedding_input_hash=_sha256_text(
                _bge_text_with_adjacent_context(ordered, index, tokenizer=tokenizer)
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


def count_tokens(text: str, *, tokenizer: RagTokenizer) -> int:
    """주입된 pinned/static tokenizer의 원문 span으로 token 수를 계산한다."""

    return len(_validated_token_spans(text, tokenizer=tokenizer))


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


def _build_atomic_units(
    blocks: tuple[RagParsedBlock, ...],
    *,
    tokenizer: RagTokenizer,
    max_tokens: int,
    atomic_document: bool,
) -> tuple[tuple[RagParsedBlock, ...], ...]:
    if atomic_document:
        if count_tokens(_join_blocks(blocks), tokenizer=tokenizer) > max_tokens:
            raise RagIngestError("OVERSIZED_ATOMIC_BLOCK")
        return (blocks,)

    result: list[tuple[RagParsedBlock, ...]] = []
    index = 0
    while index < len(blocks):
        block = blocks[index]
        if (
            block.kind == "paragraph"
            and index + 1 < len(blocks)
            and blocks[index + 1].kind == "table"
            and blocks[index + 1].heading_path == block.heading_path
        ):
            table_unit = (block, blocks[index + 1])
            if count_tokens(_join_blocks(table_unit), tokenizer=tokenizer) > max_tokens:
                raise RagIngestError("OVERSIZED_ATOMIC_BLOCK")
            result.append(table_unit)
            index += 2
            continue
        if block.kind == "table":
            if count_tokens(block.text, tokenizer=tokenizer) > max_tokens:
                raise RagIngestError("OVERSIZED_ATOMIC_BLOCK")
            result.append((block,))
            index += 1
            continue
        result.extend(
            (split_block,)
            for split_block in _split_large_paragraph_block(
                block,
                tokenizer=tokenizer,
                max_tokens=max_tokens,
            )
        )
        index += 1
    return tuple(result)


def _split_large_paragraph_block(
    block: RagParsedBlock,
    *,
    tokenizer: RagTokenizer,
    max_tokens: int,
) -> tuple[RagParsedBlock, ...]:
    spans = _validated_token_spans(block.text, tokenizer=tokenizer)
    if len(spans) <= max_tokens:
        return (block,)
    result: list[RagParsedBlock] = []
    character_cursor = 0
    while character_cursor < len(block.text):
        remaining = block.text[character_cursor:]
        remaining_spans = _validated_token_spans(remaining, tokenizer=tokenizer)
        if not remaining_spans:
            if remaining.strip():
                raise RagIngestError(
                    "RAG tokenizer paragraph spans did not preserve the full text."
                )
            character_cursor = len(block.text)
            break
        # SentencePiece/BPE는 같은 문자열도 문서 중간과 chunk 선두에서 token 수가 달라질 수 있다.
        # 새 chunk의 실제 좌측 문맥으로 다시 tokenize한 뒤 우측 경계도 상한 안으로 수렴시킨다.
        segment_start = character_cursor + remaining_spans[0][0]
        if block.text[character_cursor:segment_start].strip():
            raise RagIngestError("RAG tokenizer paragraph spans did not preserve the full text.")
        candidate = block.text[segment_start:]
        segment = _bounded_paragraph_prefix(
            candidate,
            tokenizer=tokenizer,
            max_tokens=max_tokens,
        )
        result.append(
            RagParsedBlock(
                kind="paragraph",
                heading_path=block.heading_path,
                text=segment,
                ordinal=block.ordinal,
            )
        )
        next_cursor = segment_start + len(segment)
        if next_cursor <= character_cursor:
            raise RagIngestError("RAG tokenizer produced an empty paragraph span.")
        character_cursor = next_cursor
    if character_cursor != len(block.text):
        raise RagIngestError("RAG tokenizer paragraph spans did not preserve the full text.")
    return tuple(result)


def _bounded_paragraph_prefix(
    text: str,
    *,
    tokenizer: RagTokenizer,
    max_tokens: int,
) -> str:
    """현재 chunk 문맥에서 재검증된 max-token 이하의 가장 긴 안전 prefix를 반환한다."""

    spans = _validated_token_spans(text, tokenizer=tokenizer)
    if not spans:
        raise RagIngestError("RAG tokenizer produced an empty paragraph span.")
    if len(spans) <= max_tokens:
        return text
    character_end = spans[max_tokens - 1][1]
    while character_end > 0:
        segment = text[:character_end]
        segment_spans = _validated_token_spans(segment, tokenizer=tokenizer)
        if segment and len(segment_spans) <= max_tokens:
            return segment
        if len(segment_spans) <= max_tokens:
            break
        next_end = segment_spans[max_tokens - 1][1]
        if next_end >= character_end:
            earlier_ends = tuple(end for _start, end in segment_spans if end < character_end)
            if not earlier_ends:
                break
            next_end = max(earlier_ends)
        character_end = next_end
    raise RagIngestError("RAG tokenizer paragraph split exceeded the token bound.")


def _make_chunk(
    source_id: str,
    source_revision_id: str,
    sequence: int,
    blocks: tuple[RagParsedBlock, ...],
    *,
    tokenizer: RagTokenizer,
) -> RagCanonicalChunk:
    text = _join_blocks(blocks)
    content_hash = _sha256_text(text)
    chunk_identity = _sha256_text(f"{source_revision_id}\0{sequence + 1}\0{content_hash}")
    chunk_revision_id = f"rag_chk_{chunk_identity[:32]}"
    heading_path = _common_heading_prefix(blocks)
    return RagCanonicalChunk(
        source_id=source_id,
        source_revision_id=source_revision_id,
        chunk_revision_id=chunk_revision_id,
        sequence=sequence + 1,
        heading_path=heading_path,
        text=text,
        content_hash=content_hash,
        token_count=count_tokens(text, tokenizer=tokenizer),
        contains_table=any(block.kind == "table" for block in blocks),
    )


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _bge_text_with_adjacent_context(
    chunks: tuple[RagCanonicalChunk, ...],
    index: int,
    *,
    tokenizer: RagTokenizer,
) -> str:
    chunk = chunks[index]
    side_tokens = int(chunk.token_count * _BGE_CONTEXT_RATIO_PER_SIDE)
    parts: list[str] = []
    if index > 0 and side_tokens:
        parts.append(
            _take_token_suffix(
                chunks[index - 1].text,
                side_tokens,
                tokenizer=tokenizer,
            )
        )
    parts.append(chunk.text)
    if index + 1 < len(chunks) and side_tokens:
        parts.append(
            _take_token_prefix(
                chunks[index + 1].text,
                side_tokens,
                tokenizer=tokenizer,
            )
        )
    return "\n\n".join(part for part in parts if part)


def _take_token_prefix(
    text: str,
    maximum_tokens: int,
    *,
    tokenizer: RagTokenizer,
) -> str:
    spans = _validated_token_spans(text, tokenizer=tokenizer)
    if maximum_tokens <= 0 or not spans:
        return ""
    return text[: spans[min(maximum_tokens, len(spans)) - 1][1]].rstrip()


def _take_token_suffix(
    text: str,
    maximum_tokens: int,
    *,
    tokenizer: RagTokenizer,
) -> str:
    spans = _validated_token_spans(text, tokenizer=tokenizer)
    if maximum_tokens <= 0 or not spans:
        return ""
    return text[spans[max(0, len(spans) - maximum_tokens)][0] :].lstrip()


def _validated_token_spans(
    text: str,
    *,
    tokenizer: RagTokenizer,
) -> tuple[tuple[int, int], ...]:
    spans = tuple(tokenizer.token_spans(text))
    previous_start = 0
    previous_end = 0
    for start, end in spans:
        if (
            not isinstance(start, int)
            or not isinstance(end, int)
            or start < previous_start
            or end < previous_end
            or end <= start
            or end > len(text)
        ):
            raise RagIngestError("RAG tokenizer produced an invalid non-monotonic token span.")
        previous_start = start
        previous_end = end
    return spans


def _join_blocks(blocks: Iterable[RagParsedBlock]) -> str:
    return "\n\n".join(block.text for block in blocks)


def _common_heading_prefix(blocks: tuple[RagParsedBlock, ...]) -> tuple[str, ...]:
    if not blocks:
        raise RagIngestError("RAG chunk requires at least one block.")
    prefix = list(blocks[0].heading_path)
    for block in blocks[1:]:
        common_length = 0
        for left, right in zip(prefix, block.heading_path, strict=False):
            if left != right:
                break
            common_length += 1
        prefix = prefix[:common_length]
    if not prefix:
        raise RagIngestError("RAG chunk cannot cross unrelated heading roots.")
    return tuple(prefix)
