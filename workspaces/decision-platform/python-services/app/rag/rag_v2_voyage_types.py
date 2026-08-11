"""Voyage public preparation/staging이 공유하는 cycle-free value types다."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PublicVoyageSourceMetadata:
    """external-safe public citation/right projection만 checkpoint와 DB staging에 전달한다."""

    citation_title: str
    retrieval_topics: tuple[str, ...]
    canonical_https_url: str
    source_card_sha256: str | None
    machine_fetch_allowed: bool
    local_processing_allowed: bool
    external_embedding_allowed: bool
    external_generation_allowed: bool
    oa_track_id: str | None = None
    oa_source_card: dict[str, object] | None = None
    license_evidence_sha256: str | None = None
    access_evidence_sha256: str | None = None
