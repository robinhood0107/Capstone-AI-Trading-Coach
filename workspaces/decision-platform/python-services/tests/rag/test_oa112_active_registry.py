from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from app.rag.oa112_active_registry import (
    Oa112ActiveRegistryError,
    canonical_oa112_active_registry_digest,
    load_oa112_active_registry,
)
from app.rag.oa_release_manifest import OA_TRACK_IDS


def test_active_registry_requires_exact_14_by_8_verified_rights_and_keeps_reserves_non_active(
    tmp_path: Path,
) -> None:
    _secure_directory(tmp_path)
    payload = _registry()
    path = tmp_path / "oa112-active-registry.v1.json"
    _write_registry(path, payload)

    registry = load_oa112_active_registry(
        approved_root=tmp_path,
        relative_path=path.name,
    )

    assert registry.active_source_count == 112
    assert registry.reserve_source_count == 1
    assert registry.active_source_ids[0] == "src_oa_00_00"
    assert registry.active_source_ids[-1] == "src_oa_13_07"
    assert registry.track_counts == {track_id: 8 for track_id in OA_TRACK_IDS}
    assert registry.reserve_source_ids == ("src_reserve_000",)
    assert registry.active_entries[0].document_id.startswith("doc_oa_")
    assert registry.active_entries[0].external_embedding_allowed is True
    assert registry.active_entries[0].external_generation_allowed is True


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["activeSources"][0]["sourceCard"]["permissions"].update(
            {"externalGenerationAllowed": False}
        ),
        lambda value: value["activeSources"][7].update({"trackId": OA_TRACK_IDS[1]}),
        lambda value: value["activeSources"][0]["sourceCard"].update(
            {"activeOa112Eligible": False}
        ),
        lambda value: value["activeSources"][0].update({"retrievalTopics": ["UNKNOWN"]}),
        lambda value: value.update({"automaticReservePromotion": True}),
        lambda value: value.update({"registryDigest": "0" * 64}),
    ],
)
def test_active_registry_rejects_rights_track_topic_promotion_and_digest_drift(
    tmp_path: Path,
    mutate: object,
) -> None:
    _secure_directory(tmp_path)
    payload = _registry()
    assert callable(mutate)
    mutate(payload)
    if payload["registryDigest"] != "0" * 64:
        payload["registryDigest"] = canonical_oa112_active_registry_digest(payload)
    path = tmp_path / "oa112-active-registry.v1.json"
    _write_registry(path, payload)

    with pytest.raises(Oa112ActiveRegistryError):
        load_oa112_active_registry(approved_root=tmp_path, relative_path=path.name)


def test_active_registry_requires_a_private_nontracked_secure_input_root(tmp_path: Path) -> None:
    payload = _registry()
    path = tmp_path / "oa112-active-registry.v1.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    os.chmod(path, 0o600)
    os.chmod(tmp_path, 0o755)

    with pytest.raises(Oa112ActiveRegistryError, match="OA112_REGISTRY_UNSAFE"):
        load_oa112_active_registry(approved_root=tmp_path, relative_path=path.name)


def _registry() -> dict[str, object]:
    active_sources: list[dict[str, object]] = []
    for track_index, track_id in enumerate(OA_TRACK_IDS):
        for source_index in range(8):
            source_id = f"src_oa_{track_index:02d}_{source_index:02d}"
            active_sources.append(
                _entry(
                    source_id=source_id,
                    source_revision_id=f"srv_oa_{track_index:02d}_{source_index:02d}",
                    track_id=track_id,
                    ordinal=track_index * 8 + source_index,
                )
            )
    reserve = _entry(
        source_id="src_reserve_000",
        source_revision_id="srv_reserve_000",
        track_id=OA_TRACK_IDS[0],
        ordinal=999,
        active=False,
    )
    payload: dict[str, object] = {
        "activeSourceCount": 112,
        "activeSources": active_sources,
        "automaticReservePromotion": False,
        "contractId": "rag-v2-oa112-local-activation-registry-v1",
        "registryDigest": None,
        "registryId": "oa112-active-fixture-v1",
        "reserveSourceCount": 1,
        "reserveSources": [reserve],
        "schemaVersion": 1,
    }
    payload["registryDigest"] = canonical_oa112_active_registry_digest(payload)
    return payload


def _entry(
    *,
    source_id: str,
    source_revision_id: str,
    track_id: str,
    ordinal: int,
    active: bool = True,
) -> dict[str, object]:
    canonical_url = f"https://example.invalid/oa/{ordinal:03d}.pdf"
    source_card = {
        "accessEvidence": {
            "accessCheckedAt": "2026-08-03T00:00:00Z",
            "accessEvidenceDigest": hashlib.sha256(f"access-{ordinal}".encode()).hexdigest(),
            "verificationState": "VERIFIED",
        },
        "activeOa112Eligible": active,
        "authors": [f"Fixture Author {ordinal}"],
        "canonicalUrl": canonical_url,
        "canonicalUrlSha256": hashlib.sha256(canonical_url.encode()).hexdigest(),
        "contractId": "rag-source-card-v4",
        "identifier": {"scheme": "DOI", "value": f"10.0000/fixture-{ordinal}"},
        "licenseEvidenceDigest": hashlib.sha256(f"license-{ordinal}".encode()).hexdigest(),
        "mimeType": "application/pdf",
        "permissions": {
            "externalEmbeddingAllowed": active,
            "externalGenerationAllowed": active,
            "localProcessingAllowed": active,
            "machineFetchAllowed": active,
        },
        "rawContentSha256": hashlib.sha256(f"raw-{ordinal}".encode()).hexdigest(),
        "revision": f"r{ordinal}",
        "revisionDate": "2026-08-03",
        "schemaVersion": 4,
        "sourceId": source_id,
        "sourceKind": "OPEN_ACCESS_DOCUMENT",
        "title": f"Fixture OA source {ordinal}",
    }
    return {
        "languageTags": ["en"],
        "retrievalTopics": ["METHODOLOGY"],
        "sourceCard": source_card,
        "sourceRevisionId": source_revision_id,
        "trackId": track_id,
    }


def _secure_directory(path: Path) -> None:
    os.chmod(path, 0o700)


def _write_registry(path: Path, payload: dict[str, object]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.chmod(path, 0o600)
