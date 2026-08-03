from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast

from app.rag.ingest_pipeline import (
    RagCanonicalChunk,
    RagIngestError,
    RagParsedBlock,
    RagTokenizer,
    build_canonical_chunks,
)


DocumentSourceScope = Literal["EXACT30", "OA112", "OWNER_PRIVATE"]

_DOCUMENT_ID = re.compile(r"^doc_[a-z0-9][a-z0-9_-]{10,95}$")
_SOURCE_ID = re.compile(r"^src_[a-z0-9][a-z0-9_-]{2,95}$")
_SOURCE_REVISION_ID = re.compile(r"^srv_[a-z0-9][a-z0-9_-]{2,95}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_LOCATOR_KEYS = frozenset(("page", "slide", "sheet", "section"))
_LOCATOR_URI = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_MAX_DOCUMENT_IR_BLOCKS = 50_000
_MAX_DOCUMENT_IR_TEXT_CHARACTERS = 10_000_000
_MAX_LIST_ITEMS = 1_000
_MAX_TABLE_CELLS = 50_000
_MAX_RENDERED_TABLE_CELLS = 50_000


class DocumentIrMaterializationError(ValueError):
    """Document IR가 local-only canonical materialization 경계를 벗어났음을 나타낸다."""


@dataclass(frozen=True, slots=True)
class RagV2DocumentMaterializationRequest:
    """경로·원본 없이 이미 검사된 문서를 canonical generation 입력으로 식별한다.

    `external_*_allowed`는 provider 호출 권한이 아니다. 이 단계는 호출을 만들지 않고 이후
    full-bundle policy가 참고할 수 있는 local eligibility만 계산한다.
    """

    document_id: str
    source_scope: DocumentSourceScope
    source_id: str
    source_revision_id: str
    local_processing_allowed: bool
    external_embedding_allowed: bool
    external_generation_allowed: bool


@dataclass(frozen=True, slots=True)
class RagV2CanonicalDocumentChunk:
    """단일 locator에 묶인 canonical text chunk다.

    `canonical_text`는 local DB generation writer로만 전달하는 transient value이며, content-free
    receipt/API/history에는 절대 직렬화하지 않는다.
    """

    chunk_id: str
    document_id: str
    sequence: int
    heading_path: tuple[str, ...]
    locator: dict[str, object]
    canonical_text: str
    canonical_text_sha256: str
    token_count: int
    contains_table: bool


@dataclass(frozen=True, slots=True)
class RagV2DocumentMaterialization:
    """Document IR에서 만든 local-only canonicalization 결과다."""

    document_id: str
    source_scope: DocumentSourceScope
    source_id: str
    source_revision_id: str
    raw_content_sha256: str
    normalized_content_sha256: str
    external_processing_eligible: bool
    chunks: tuple[RagV2CanonicalDocumentChunk, ...]

    def content_free_receipt(self) -> dict[str, object]:
        """원문·canonical text·filesystem 위치 없이 reusable hash receipt만 투영한다."""

        return {
            "canonicalChunkCount": len(self.chunks),
            "chunkHashes": [chunk.canonical_text_sha256 for chunk in self.chunks],
            "documentId": self.document_id,
            "externalProcessingEligible": self.external_processing_eligible,
            "normalizedContentSha256": self.normalized_content_sha256,
            "ownerRawCopies": 0,
            "rawContentSha256": self.raw_content_sha256,
            "sourceId": self.source_id,
            "sourceRevisionId": self.source_revision_id,
            "sourceScope": self.source_scope,
        }


@dataclass(frozen=True, slots=True)
class _RenderedBlock:
    locator: dict[str, object]
    parsed_block: RagParsedBlock


def materialize_document_ir(
    *,
    document_ir: Mapping[str, object],
    request: RagV2DocumentMaterializationRequest,
    tokenizer: RagTokenizer,
    min_tokens: int = 400,
    max_tokens: int = 600,
) -> RagV2DocumentMaterialization:
    """안전 parser의 Document IR를 local canonical text/chunk로 바꾼다.

    이 함수는 source file을 다시 열거나 cache에 복사하지 않고, Document IR가 보장한 source
    identity와 safety classification만 사용한다. locator가 다른 block은 절대 같은 chunk에
    합치지 않아 citation이 파일 경로나 넓은 문서 범위를 노출하지 않게 한다.
    """

    _validate_request(request)
    if not request.local_processing_allowed:
        raise DocumentIrMaterializationError("LOCAL_PROCESSING_NOT_ALLOWED")
    validated = _validate_document_ir(document_ir, request)
    safety = validated["safety"]
    if safety["secretDetected"]:
        raise DocumentIrMaterializationError("DOCUMENT_SECRET_QUARANTINED")

    rendered = _render_document_blocks(validated["blocks"])
    if not rendered:
        raise DocumentIrMaterializationError("DOCUMENT_IR_NO_INDEXABLE_CONTENT")

    chunks: list[RagV2CanonicalDocumentChunk] = []
    for locator, blocks in _group_same_locator(rendered):
        try:
            canonical = build_canonical_chunks(
                source_id=request.source_id,
                source_revision_id=request.source_revision_id,
                blocks=blocks,
                tokenizer=tokenizer,
                min_tokens=min_tokens,
                max_tokens=max_tokens,
            )
        except RagIngestError as error:
            raise DocumentIrMaterializationError(str(error)) from error
        for item in canonical:
            chunks.append(_to_document_chunk(item, request, len(chunks) + 1, locator))

    return RagV2DocumentMaterialization(
        document_id=request.document_id,
        source_scope=request.source_scope,
        source_id=request.source_id,
        source_revision_id=request.source_revision_id,
        raw_content_sha256=validated["raw_content_sha256"],
        normalized_content_sha256=validated["normalized_content_sha256"],
        external_processing_eligible=(
            request.external_embedding_allowed
            and request.external_generation_allowed
            and safety["externalLlmEligible"]
            and not safety["piiDetected"]
            and not safety["promptInjectionDetected"]
        ),
        chunks=tuple(chunks),
    )


def _validate_request(request: RagV2DocumentMaterializationRequest) -> None:
    if (
        not isinstance(request.document_id, str)
        or not _DOCUMENT_ID.fullmatch(request.document_id)
        or not isinstance(request.source_scope, str)
        or request.source_scope not in {"EXACT30", "OA112", "OWNER_PRIVATE"}
        or not isinstance(request.source_id, str)
        or not _SOURCE_ID.fullmatch(request.source_id)
        or not isinstance(request.source_revision_id, str)
        or not _SOURCE_REVISION_ID.fullmatch(request.source_revision_id)
        or any(
            not isinstance(value, bool)
            for value in (
                request.local_processing_allowed,
                request.external_embedding_allowed,
                request.external_generation_allowed,
            )
        )
    ):
        raise DocumentIrMaterializationError("DOCUMENT_MATERIALIZATION_REQUEST_INVALID")


def _validate_document_ir(
    document_ir: Mapping[str, object],
    request: RagV2DocumentMaterializationRequest,
) -> dict[str, Any]:
    if not isinstance(document_ir, Mapping):
        raise DocumentIrMaterializationError("DOCUMENT_IR_INVALID")
    if (
        document_ir.get("contractId") != "rag-document-ir-v1"
        or document_ir.get("documentIrVersion") != 1
        or document_ir.get("sourceId") != request.source_id
        or document_ir.get("sourceRevisionId") != request.source_revision_id
    ):
        raise DocumentIrMaterializationError("DOCUMENT_IR_IDENTITY_MISMATCH")

    raw_content_sha256 = document_ir.get("rawContentSha256")
    normalized_content_sha256 = document_ir.get("normalizedContentSha256")
    if not (
        isinstance(raw_content_sha256, str)
        and _SHA256.fullmatch(raw_content_sha256)
        and isinstance(normalized_content_sha256, str)
        and _SHA256.fullmatch(normalized_content_sha256)
    ):
        raise DocumentIrMaterializationError("DOCUMENT_IR_HASH_INVALID")

    safety = document_ir.get("safetyClassification")
    if not isinstance(safety, Mapping) or set(safety) != {
        "externalLlmEligible",
        "piiDetected",
        "promptInjectionDetected",
        "secretDetected",
    } or any(not isinstance(value, bool) for value in safety.values()):
        raise DocumentIrMaterializationError("DOCUMENT_IR_SAFETY_INVALID")

    blocks = document_ir.get("blocks")
    if (
        isinstance(blocks, (str, bytes))
        or not isinstance(blocks, Sequence)
        or not blocks
        or len(blocks) > _MAX_DOCUMENT_IR_BLOCKS
    ):
        raise DocumentIrMaterializationError("DOCUMENT_IR_BLOCKS_INVALID")
    copied_blocks: list[dict[str, object]] = []
    text_characters = 0
    for block in blocks:
        if not isinstance(block, Mapping):
            raise DocumentIrMaterializationError("DOCUMENT_IR_BLOCK_INVALID")
        copied_block = _validate_block(cast(Mapping[str, object], block))
        text_characters += _block_text_characters(copied_block)
        if text_characters > _MAX_DOCUMENT_IR_TEXT_CHARACTERS:
            raise DocumentIrMaterializationError("DOCUMENT_IR_TEXT_BOUND_EXCEEDED")
        copied_blocks.append(copied_block)
    return {
        "blocks": tuple(copied_blocks),
        "normalized_content_sha256": normalized_content_sha256,
        "raw_content_sha256": raw_content_sha256,
        "safety": {key: cast(bool, value) for key, value in safety.items()},
    }


def _validate_block(block: Mapping[str, object]) -> dict[str, object]:
    block_type = block.get("blockType")
    locator = block.get("locator")
    reading_order = block.get("readingOrder")
    ocr_confidence = block.get("ocrConfidence")
    if (
        not isinstance(block_type, str)
        or block_type not in {"HEADING", "PARAGRAPH", "LIST", "TABLE", "FORMULA", "CAPTION"}
        or not isinstance(locator, Mapping)
        or set(locator) - _LOCATOR_KEYS
        or len(locator) != 1
        or not isinstance(reading_order, int)
        or isinstance(reading_order, bool)
        or reading_order < 0
        or "ocrConfidence" not in block
        or (
            ocr_confidence is not None
            and (
                not isinstance(ocr_confidence, (int, float))
                or isinstance(ocr_confidence, bool)
                or not 0 <= ocr_confidence <= 1
            )
        )
    ):
        raise DocumentIrMaterializationError("DOCUMENT_IR_BLOCK_INVALID")
    copied_locator = _copy_locator(locator)
    copied: dict[str, object] = {"blockType": block_type, "locator": copied_locator, "readingOrder": reading_order}
    if block_type == "HEADING":
        level = block.get("level")
        text = _required_text(block, "text")
        if not isinstance(level, int) or isinstance(level, bool) or level not in range(1, 7):
            raise DocumentIrMaterializationError("DOCUMENT_IR_BLOCK_INVALID")
        copied.update({"level": level, "text": text})
    elif block_type in {"PARAGRAPH", "CAPTION"}:
        copied["text"] = _required_text(block, "text")
        if block_type == "CAPTION":
            target_reading_order = block.get("targetReadingOrder")
            if (
                not isinstance(target_reading_order, int)
                or isinstance(target_reading_order, bool)
                or target_reading_order < 0
            ):
                raise DocumentIrMaterializationError("DOCUMENT_IR_BLOCK_INVALID")
            copied["targetReadingOrder"] = target_reading_order
    elif block_type == "LIST":
        items = block.get("items")
        ordered = block.get("ordered")
        if (
            isinstance(items, (str, bytes))
            or not isinstance(items, Sequence)
            or not items
            or len(items) > _MAX_LIST_ITEMS
            or not isinstance(ordered, bool)
            or any(not isinstance(item, str) or not item.strip() for item in items)
        ):
            raise DocumentIrMaterializationError("DOCUMENT_IR_BLOCK_INVALID")
        copied.update({"items": tuple(cast(str, item).strip() for item in items), "ordered": ordered})
    elif block_type == "TABLE":
        cells = block.get("cells")
        row_count = block.get("rowCount")
        column_count = block.get("columnCount")
        if (
            isinstance(cells, (str, bytes))
            or not isinstance(cells, Sequence)
            or not cells
            or len(cells) > _MAX_TABLE_CELLS
            or not isinstance(row_count, int)
            or not isinstance(column_count, int)
            or isinstance(row_count, bool)
            or isinstance(column_count, bool)
            or row_count not in range(1, 50_001)
            or column_count not in range(1, 257)
        ):
            raise DocumentIrMaterializationError("DOCUMENT_IR_BLOCK_INVALID")
        if row_count * column_count > _MAX_RENDERED_TABLE_CELLS:
            raise DocumentIrMaterializationError("DOCUMENT_IR_TABLE_AREA_EXCEEDED")
        copied_cells = tuple(
            _validate_table_cell(cell, row_count, column_count)
            for cell in cells
        )
        copied.update({"cells": copied_cells, "rowCount": row_count, "columnCount": column_count})
    else:
        copied.update(
            {
                "normalizedFormula": _required_text(block, "normalizedFormula"),
                "sourceText": _required_text(block, "sourceText"),
            }
        )
    return copied


def _copy_locator(locator: Mapping[str, object]) -> dict[str, object]:
    key, value = next(iter(locator.items()))
    if key in {"page", "slide"}:
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise DocumentIrMaterializationError("DOCUMENT_IR_BLOCK_INVALID")
    elif key in {"sheet", "section"}:
        if (
            not isinstance(value, str)
            or not value
            or len(value) > (128 if key == "sheet" else 300)
            or "/" in value
            or "\\" in value
            or _LOCATOR_URI.match(value) is not None
            or any(ord(character) < 32 for character in value)
        ):
            raise DocumentIrMaterializationError("DOCUMENT_IR_BLOCK_INVALID")
    else:  # pragma: no cover - set validation above keeps this closed.
        raise DocumentIrMaterializationError("DOCUMENT_IR_BLOCK_INVALID")
    return {key: value}


def _required_text(block: Mapping[str, object], key: str) -> str:
    value = block.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > 65_536:
        raise DocumentIrMaterializationError("DOCUMENT_IR_BLOCK_INVALID")
    return value.strip()


def _validate_table_cell(
    cell: object,
    row_count: int,
    column_count: int,
) -> tuple[int, int, int, int, str]:
    if not isinstance(cell, Mapping):
        raise DocumentIrMaterializationError("DOCUMENT_IR_BLOCK_INVALID")
    row = cell.get("row")
    column = cell.get("column")
    row_span = cell.get("rowSpan")
    column_span = cell.get("columnSpan")
    text = cell.get("text")
    if (
        not isinstance(row, int)
        or not isinstance(column, int)
        or not isinstance(row_span, int)
        or not isinstance(column_span, int)
        or any(isinstance(value, bool) for value in (row, column, row_span, column_span))
        or not isinstance(text, str)
        or not text.strip()
        or row < 0
        or column < 0
        or row_span < 1
        or column_span < 1
        or row + row_span > row_count
        or column + column_span > column_count
        or len(text) > 65_536
    ):
        raise DocumentIrMaterializationError("DOCUMENT_IR_BLOCK_INVALID")
    return row, column, row_span, column_span, text.strip()


def _block_text_characters(block: Mapping[str, object]) -> int:
    """safe parser의 text cap과 같은 단위로 local materialization 입력을 제한한다."""

    block_type = cast(str, block["blockType"])
    if block_type in {"HEADING", "PARAGRAPH", "CAPTION"}:
        return len(cast(str, block["text"]))
    if block_type == "LIST":
        return sum(len(item) for item in cast(tuple[str, ...], block["items"]))
    if block_type == "TABLE":
        return sum(len(cell[4]) for cell in cast(tuple[tuple[int, int, int, int, str], ...], block["cells"]))
    return len(cast(str, block["sourceText"])) + len(cast(str, block["normalizedFormula"]))


def _render_document_blocks(blocks: Sequence[Mapping[str, object]]) -> tuple[_RenderedBlock, ...]:
    heading_stack: list[str] = []
    rendered: list[_RenderedBlock] = []
    last_order = -1
    for block in sorted(blocks, key=lambda item: cast(int, item["readingOrder"])):
        reading_order = cast(int, block["readingOrder"])
        if reading_order <= last_order:
            raise DocumentIrMaterializationError("DOCUMENT_IR_READING_ORDER_INVALID")
        last_order = reading_order
        block_type = cast(str, block["blockType"])
        if block_type == "HEADING":
            level = cast(int, block["level"])
            heading_stack = heading_stack[: level - 1]
            heading_stack.append(cast(str, block["text"]))
            continue
        text, kind = _render_block_text(block)
        rendered.append(
            _RenderedBlock(
                locator=dict(cast(dict[str, object], block["locator"])),
                parsed_block=RagParsedBlock(
                    kind=kind,
                    # heading이 없는 Office/scan 문서도 기존 chunker의 non-empty heading contract를
                    # 만족시켜야 한다. 사용자 입력 제목을 만들지 않고 고정 root만 사용한다.
                    heading_path=tuple(heading_stack) or ("Document",),
                    text=text,
                    ordinal=len(rendered),
                ),
            )
        )
    return tuple(rendered)


def _render_block_text(block: Mapping[str, object]) -> tuple[str, Literal["paragraph", "table"]]:
    block_type = cast(str, block["blockType"])
    if block_type in {"PARAGRAPH", "CAPTION"}:
        return cast(str, block["text"]), "paragraph"
    if block_type == "LIST":
        ordered = cast(bool, block["ordered"])
        items = cast(tuple[str, ...], block["items"])
        return (
            "\n".join(
                f"{index}. {item}" if ordered else f"- {item}"
                for index, item in enumerate(items, start=1)
            ),
            "paragraph",
        )
    if block_type == "TABLE":
        return _render_table(block), "table"
    if block_type == "FORMULA":
        return f"{block['sourceText']}\n{block['normalizedFormula']}", "paragraph"
    raise DocumentIrMaterializationError("DOCUMENT_IR_BLOCK_INVALID")


def _render_table(block: Mapping[str, object]) -> str:
    row_count = cast(int, block["rowCount"])
    column_count = cast(int, block["columnCount"])
    matrix = [["" for _ in range(column_count)] for _ in range(row_count)]
    occupied: set[tuple[int, int]] = set()
    for row, column, row_span, column_span, text in cast(
        tuple[tuple[int, int, int, int, str], ...], block["cells"]
    ):
        for current_row in range(row, row + row_span):
            for current_column in range(column, column + column_span):
                coordinate = (current_row, current_column)
                if coordinate in occupied:
                    raise DocumentIrMaterializationError("DOCUMENT_IR_TABLE_OVERLAP")
                occupied.add(coordinate)
                matrix[current_row][current_column] = text if coordinate == (row, column) else ""
    return "\n".join(
        "| " + " | ".join(_escape_table_cell(value) for value in row) + " |" for row in matrix
    )


def _escape_table_cell(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def _group_same_locator(
    blocks: Sequence[_RenderedBlock],
) -> tuple[tuple[dict[str, object], tuple[RagParsedBlock, ...]], ...]:
    groups: list[tuple[dict[str, object], list[RagParsedBlock]]] = []
    for block in blocks:
        if not groups or groups[-1][0] != block.locator:
            groups.append((dict(block.locator), []))
        groups[-1][1].append(block.parsed_block)
    return tuple((locator, tuple(group_blocks)) for locator, group_blocks in groups)


def _to_document_chunk(
    chunk: RagCanonicalChunk,
    request: RagV2DocumentMaterializationRequest,
    sequence: int,
    locator: dict[str, object],
) -> RagV2CanonicalDocumentChunk:
    identity = hashlib.sha256(
        (
            f"{request.document_id}\0{request.source_revision_id}\0{sequence}\0"
            f"{chunk.content_hash}"
        ).encode("utf-8")
    ).hexdigest()
    return RagV2CanonicalDocumentChunk(
        chunk_id=f"rag_v2_chk_{identity[:32]}",
        document_id=request.document_id,
        sequence=sequence,
        heading_path=chunk.heading_path,
        locator=dict(locator),
        canonical_text=chunk.text,
        canonical_text_sha256=chunk.content_hash,
        token_count=chunk.token_count,
        contains_table=chunk.contains_table,
    )
