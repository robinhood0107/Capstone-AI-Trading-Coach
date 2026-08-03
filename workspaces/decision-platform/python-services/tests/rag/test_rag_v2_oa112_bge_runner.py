from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from app.rag.oa112_active_registry import Oa112ActiveRegistry, Oa112RegistryEntry
from app.rag.oa_release_manifest import OA_TRACK_IDS
from app.rag.rag_v2_oa112_bge_runner import (
    RagV2Oa112BgeRunnerError,
    materialize_oa112_public_bge_component,
)


class _FixtureTokenizer:
    def token_spans(self, text: str) -> tuple[tuple[int, int], ...]:
        return tuple((match.start(), match.end()) for match in re.finditer(r"\S+", text))

    def take_prefix(self, text: str, maximum_tokens: int) -> str:
        spans = self.token_spans(text)
        return text[: spans[min(len(spans), maximum_tokens) - 1][1]] if spans else ""

    def take_suffix(self, text: str, maximum_tokens: int) -> str:
        spans = self.token_spans(text)
        return text[spans[max(0, len(spans) - maximum_tokens)][0] :] if spans else ""


class _FixtureEmbedder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def embed(self, texts: tuple[str, ...]) -> np.ndarray:
        self.calls.append(texts)
        vectors = np.zeros((len(texts), 1024), dtype=np.float32)
        for index in range(len(texts)):
            vectors[index, index] = 1.0
        return vectors


class _FixtureApprovedParser:
    def __init__(self, entries: tuple[Oa112RegistryEntry, ...]) -> None:
        self._entries = {entry.source_id: entry for entry in entries}
        self.calls: list[dict[str, object]] = []

    def parse_approved_document(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(kwargs)
        source_id = kwargs["source_id"]
        assert isinstance(source_id, str)
        entry = self._entries[source_id]
        text = f"Approved OA evidence {entry.source_id} stays in its immutable source generation."
        blocks = [
            {
                "blockType": "PARAGRAPH",
                "locator": {"section": "document"},
                "ocrConfidence": None,
                "readingOrder": 1,
                "text": text,
            }
        ]
        return {
            "blocks": blocks,
            "contractId": "rag-document-ir-v1",
            "documentIrVersion": 1,
            "extractionMode": "NATIVE",
            "languageTags": ["en"],
            "mimeType": entry.mime_type,
            "normalizedContentSha256": hashlib.sha256(
                json.dumps(blocks, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
            ).hexdigest(),
            "parserEvidence": {
                "ocr": {"backend": "NOT_USED", "backendVersion": None, "modelSha256": None},
                "parserArtifactSha256": "b" * 64,
                "parserBackend": "fixture",
                "parserVersion": "fixture-v1",
            },
            "rawContentSha256": entry.raw_content_sha256,
            "safetyClassification": {
                "externalLlmEligible": True,
                "piiDetected": False,
                "promptInjectionDetected": False,
                "secretDetected": False,
            },
            "sourceId": entry.source_id,
            "sourceRevisionId": entry.source_revision_id,
        }


def test_oa112_runner_materializes_only_the_full_active_registry_with_local_bge(
    tmp_path: Path,
) -> None:
    registry = _registry()
    parser = _FixtureApprovedParser(registry.active_entries)
    embedder = _FixtureEmbedder()

    materialization = materialize_oa112_public_bge_component(
        tokenizer=_FixtureTokenizer(),
        embedder=embedder,
        registry=registry,
        local_cache_root=tmp_path,
        parser=parser,
    )

    assert materialization.context.component_scope == "OA112"
    assert materialization.context.expected_source_count == 112
    assert len(materialization.records) == 112
    assert len(parser.calls) == len(embedder.calls) == 112
    assert all(
        record[0].document.external_processing_eligible is True
        and record[1].source_card is not None
        and record[1].machine_fetch_allowed is True
        and record[1].external_embedding_allowed is True
        and record[1].external_generation_allowed is True
        for record in materialization.records
    )
    receipt = materialization.content_free_receipt()
    assert receipt["sourceCount"] == 112
    assert receipt["chunkCount"] == materialization.context.expected_chunk_count
    serialized = json.dumps(receipt, ensure_ascii=False, sort_keys=True)
    assert "canonicalText" not in serialized
    assert '"embedding":' not in serialized
    assert str(tmp_path) not in serialized


def test_oa112_runner_rejects_any_active_source_without_all_four_permissions(
    tmp_path: Path,
) -> None:
    registry = _registry()
    first = registry.active_entries[0]
    invalid_registry = replace(
        registry,
        active_entries=(
            replace(first, external_generation_allowed=False),
            *registry.active_entries[1:],
        ),
    )

    with pytest.raises(RagV2Oa112BgeRunnerError, match="OA112_RIGHTS_REQUIRED"):
        materialize_oa112_public_bge_component(
            tokenizer=_FixtureTokenizer(),
            embedder=_FixtureEmbedder(),
            registry=invalid_registry,
            local_cache_root=tmp_path,
            parser=_FixtureApprovedParser(invalid_registry.active_entries),
        )


def _registry() -> Oa112ActiveRegistry:
    entries: list[Oa112RegistryEntry] = []
    for track_index, track_id in enumerate(OA_TRACK_IDS):
        for source_index in range(8):
            ordinal = track_index * 8 + source_index
            source_id = f"src_oa_{track_index:02d}_{source_index:02d}"
            source_revision_id = f"srv_oa_{track_index:02d}_{source_index:02d}"
            raw_hash = hashlib.sha256(f"raw-{ordinal}".encode()).hexdigest()
            canonical_url = f"https://example.invalid/oa/{ordinal:03d}.txt"
            entries.append(
                Oa112RegistryEntry(
                    source_id=source_id,
                    source_revision_id=source_revision_id,
                    document_id=f"doc_oa_{hashlib.sha256(source_id.encode()).hexdigest()[:32]}",
                    track_id=track_id,
                    language_tags=("en",),
                    retrieval_topics=("METHODOLOGY",),
                    source_card=_source_card(
                        source_id=source_id,
                        ordinal=ordinal,
                        raw_hash=raw_hash,
                        canonical_url=canonical_url,
                    ),
                    title=f"OA fixture {ordinal}",
                    canonical_url=canonical_url,
                    raw_content_sha256=raw_hash,
                    mime_type="text/plain",
                    license_evidence_sha256=hashlib.sha256(f"license-{ordinal}".encode()).hexdigest(),
                    access_evidence_sha256=hashlib.sha256(f"access-{ordinal}".encode()).hexdigest(),
                    machine_fetch_allowed=True,
                    local_processing_allowed=True,
                    external_embedding_allowed=True,
                    external_generation_allowed=True,
                )
            )
    return Oa112ActiveRegistry(
        registry_id="oa112-runner-fixture-v1",
        registry_digest="a" * 64,
        active_entries=tuple(entries),
        reserve_entries=(),
    )


def _source_card(
    *,
    source_id: str,
    ordinal: int,
    raw_hash: str,
    canonical_url: str,
) -> dict[str, object]:
    return {
        "accessEvidence": {
            "accessCheckedAt": "2026-08-03T00:00:00Z",
            "accessEvidenceDigest": hashlib.sha256(f"access-{ordinal}".encode()).hexdigest(),
            "verificationState": "VERIFIED",
        },
        "activeOa112Eligible": True,
        "authors": [f"Fixture Author {ordinal}"],
        "canonicalUrl": canonical_url,
        "canonicalUrlSha256": hashlib.sha256(canonical_url.encode()).hexdigest(),
        "contractId": "rag-source-card-v4",
        "identifier": {"scheme": "DOI", "value": f"10.0000/fixture-{ordinal}"},
        "licenseEvidenceDigest": hashlib.sha256(f"license-{ordinal}".encode()).hexdigest(),
        "mimeType": "text/plain",
        "permissions": {
            "externalEmbeddingAllowed": True,
            "externalGenerationAllowed": True,
            "localProcessingAllowed": True,
            "machineFetchAllowed": True,
        },
        "rawContentSha256": raw_hash,
        "revision": f"r{ordinal}",
        "revisionDate": "2026-08-03",
        "schemaVersion": 4,
        "sourceId": source_id,
        "sourceKind": "OPEN_ACCESS_DOCUMENT",
        "title": f"OA fixture {ordinal}",
    }
