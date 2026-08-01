from __future__ import annotations

from pathlib import Path

from app.rag.external_processing_corpus import (
    S4_7C_CORPUS_MANIFEST_PATH,
    S4_7C_PROFILE_ID,
    S4_7C_SOURCE_CARD_ROOT,
    ExternalProcessingCorpusError,
    load_external_processing_corpus,
)
from app.rag.source_card_corpus import (
    S4_7B_CORPUS_MANIFEST_PATH,
    S4_7B_SOURCE_CARD_ROOT,
    FrozenSourceCardCorpus,
    load_frozen_source_card_corpus,
)

S4_7B_PROFILE_ID = "s4_7b_internal_v1"
SERVER_ACTIVE_CORPUS_PROFILE = S4_7C_PROFILE_ID


def load_source_card_corpus(
    *,
    profile_id: str,
    card_root: Path | None = None,
    manifest_path: Path | None = None,
) -> FrozenSourceCardCorpus:
    """server-selected explicit profile만 exact root/manifest pair로 연다.

    public request payload에서 profile을 받지 않으며 old/new root 또는 manifest를 섞으면
    activation 전에 fail-closed한다.
    """

    if profile_id == S4_7B_PROFILE_ID:
        return load_frozen_source_card_corpus(
            card_root=card_root or S4_7B_SOURCE_CARD_ROOT,
            manifest_path=manifest_path or S4_7B_CORPUS_MANIFEST_PATH,
        )
    if profile_id == S4_7C_PROFILE_ID:
        return load_external_processing_corpus(
            card_root=card_root or S4_7C_SOURCE_CARD_ROOT,
            manifest_path=manifest_path or S4_7C_CORPUS_MANIFEST_PATH,
        )
    raise ExternalProcessingCorpusError("CORPUS_PROFILE_UNKNOWN: profile is not server allowlisted")
